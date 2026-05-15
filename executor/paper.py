"""
executor/paper.py
-----------------
Smart paper trading executor with professional exit management
and realistic brokerage cost simulation.
"""

import os
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from executor.brokerage import BrokerageSimulator

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    id:             int
    symbol:         str
    direction:      str
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    trailing_stop:  float
    peak_price:     float
    size_usd:       float
    size_units:     float
    confidence:     float
    risk_reward:    float
    open_time:      str
    close_time:     str
    exit_price:     float
    exit_reason:    str
    gross_pnl:      float
    brokerage_cost: float
    pnl_usd:        float      # NET after brokerage
    pnl_pct:        float      # NET %
    status:         str
    signal_reason:  str
    llm_verdict:    str
    strategy:       str


class SmartExitEngine:
    """
    5 exit modes working together like a real scalper:
    TRAILING | PROTECT | BREAKEVEN | TIME DECAY | SL/TP
    """

    def __init__(self):
        self.trailing_pct    = float(os.getenv("SCALP_TRAILING_PCT",  0.008))
        self.profit_lock_r   = float(os.getenv("PROFIT_LOCK_R",       1.5))
        self.profit_lock_pct = float(os.getenv("PROFIT_LOCK_PCT",     0.5))
        self.time_decay_min  = float(os.getenv("TIME_DECAY_MIN",      20))
        self.breakeven_r     = float(os.getenv("BREAKEVEN_R",         0.8))

    def evaluate(self, trade: Trade, price: float) -> tuple[bool, str]:
        if trade.direction == "LONG":
            return self._long(trade, price)
        return self._short(trade, price)

    def updated_trailing(self, trade: Trade, price: float) -> float:
        if trade.direction == "LONG":
            return max(price * (1 - self.trailing_pct), trade.trailing_stop)
        return min(price * (1 + self.trailing_pct), trade.trailing_stop)

    def profit_protect_stop(self, trade: Trade, price: float):
        sl_dist = abs(trade.entry_price - trade.stop_loss)
        if not sl_dist:
            return None
        r = (price - trade.entry_price) / sl_dist if trade.direction == "LONG" \
            else (trade.entry_price - price) / sl_dist
        if r >= self.profit_lock_r:
            locked = (abs(price - trade.entry_price)) * self.profit_lock_pct
            if trade.direction == "LONG":
                return max(trade.entry_price + locked, trade.trailing_stop)
            else:
                return min(trade.entry_price - locked, trade.trailing_stop)
        return None

    def _long(self, t, p):
        if p <= t.stop_loss:                                   return True, "SL"
        if p >= t.take_profit:                                 return True, "TP"
        if p <= t.trailing_stop and t.trailing_stop > t.stop_loss: return True, "TRAILING"
        ps = self.profit_protect_stop(t, p)
        if ps and p <= ps:                                     return True, "PROTECT"
        if self._decayed(t, p):                                return True, "TIME"
        return False, ""

    def _short(self, t, p):
        if p >= t.stop_loss:                                   return True, "SL"
        if p <= t.take_profit:                                 return True, "TP"
        if p >= t.trailing_stop and t.trailing_stop < t.stop_loss: return True, "TRAILING"
        ps = self.profit_protect_stop(t, p)
        if ps and p >= ps:                                     return True, "PROTECT"
        if self._decayed(t, p):                                return True, "TIME"
        return False, ""

    def _decayed(self, t, p):
        try:
            dt = datetime.fromisoformat(t.open_time)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
            if mins < self.time_decay_min: return False
            sl_dist = abs(t.entry_price - t.stop_loss)
            if not sl_dist: return False
            r = (p - t.entry_price) / sl_dist if t.direction == "LONG" \
                else (t.entry_price - p) / sl_dist
            return r < 0.3
        except: return False


class PaperExecutor:
    """Paper trading executor with smart exits and brokerage costs."""

    def __init__(self, db_path=None):
        self.db_path    = db_path or os.getenv("DB_PATH", "logs/trades.db")
        self.exit_engine = SmartExitEngine()
        self.brokerage   = BrokerageSimulator()
        self._init_db()
        logger.info(f"PaperExecutor ready | DB: {self.db_path}")

    def open_trade(self, order, signal):
        if any(t.symbol == order.symbol for t in self.get_open_trades()):
            return None

        # Apply entry brokerage cost to effective entry price
        entry_cost = self.brokerage.calculate_entry_cost(order.symbol, order.size_usd)
        slip_adj   = entry_cost["slippage_usd"] / order.size_usd if order.size_usd else 0

        # Adjust effective entry price for slippage
        eff_entry = order.entry_price * (1 + slip_adj) if order.direction == "LONG" \
                    else order.entry_price * (1 - slip_adj)

        now = datetime.now(timezone.utc).isoformat()
        t = Trade(
            id=0, symbol=order.symbol, direction=order.direction,
            entry_price=round(eff_entry, 6),
            stop_loss=order.stop_loss, take_profit=order.take_profit,
            trailing_stop=order.stop_loss, peak_price=eff_entry,
            size_usd=order.size_usd, size_units=order.size_units,
            confidence=order.confidence, risk_reward=order.risk_reward,
            open_time=now, close_time="", exit_price=0.0,
            exit_reason="OPEN", gross_pnl=0.0, brokerage_cost=0.0,
            pnl_usd=0.0, pnl_pct=0.0, status="OPEN",
            signal_reason=signal.reason, llm_verdict=signal.llm_verdict,
            strategy=getattr(signal, "strategy", "BMV") or "BMV",
        )
        t.id = self._insert(t)
        logger.info(
            f"📈 #{t.id} {order.symbol} {order.direction} | "
            f"Entry: {eff_entry:.4f} (slip adj) | "
            f"Size: ${order.size_usd:.0f} | "
            f"Entry cost: ${entry_cost['total_usd']:.3f}"
        )
        return t

    def monitor_trades(self, current_prices: dict) -> list:
        closed = []
        for t in self.get_open_trades():
            price = current_prices.get(t.symbol)
            if not price: continue

            # Update peak
            if (t.direction == "LONG" and price > t.peak_price) or \
               (t.direction == "SHORT" and price < t.peak_price):
                self._update_field(t.id, "peak_price", price)
                t.peak_price = price

            # Update trailing stop
            new_trail = self.exit_engine.updated_trailing(t, price)
            if new_trail != t.trailing_stop:
                self._update_field(t.id, "trailing_stop", new_trail)
                t.trailing_stop = new_trail

            # Update profit protection
            protect = self.exit_engine.profit_protect_stop(t, price)
            if protect:
                self._update_field(t.id, "trailing_stop", protect)
                t.trailing_stop = protect

            # Evaluate exit
            should_exit, reason = self.exit_engine.evaluate(t, price)
            if should_exit:
                closed.append(self._close(t, price, reason))
        return closed

    def get_open_trades(self):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        return [self._row(r) for r in rows]

    def get_all_trades(self, limit=500):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def get_stats(self) -> dict:
        trades = [t for t in self.get_all_trades() if t.status == "CLOSED"]
        if not trades:
            return {"total_trades": 0, "win_rate": 0, "total_pnl": 0,
                    "sharpe": 0, "profit_factor": 0, "avg_win": 0,
                    "avg_loss": 0, "total_brokerage": 0}
        import math
        pnls       = [t.pnl_usd for t in trades]
        gross_pnls = [t.gross_pnl for t in trades]
        wins       = [p for p in pnls if p > 0]
        losses     = [p for p in pnls if p <= 0]
        avg_w      = sum(wins)/len(wins) if wins else 0
        avg_l      = sum(losses)/len(losses) if losses else 0
        pf         = abs(avg_w/avg_l) if avg_l else 0
        returns    = [t.pnl_pct for t in trades]
        avg_r      = sum(returns)/len(returns) if returns else 0
        std_r      = math.sqrt(sum((r-avg_r)**2 for r in returns)/len(returns)) if len(returns)>1 else 0
        sharpe     = (avg_r/std_r*(252**0.5)) if std_r else 0
        total_brok = sum(t.brokerage_cost for t in trades)

        sym_pnl = {}
        for t in trades:
            sym_pnl[t.symbol] = round(sym_pnl.get(t.symbol,0) + t.pnl_usd, 2)

        exit_counts = {}
        for t in trades:
            exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1

        strat_pnl = {}
        for t in trades:
            s = t.strategy or "BMV"
            if s not in strat_pnl: strat_pnl[s] = {"pnl":0,"trades":0,"wins":0}
            strat_pnl[s]["pnl"] += t.pnl_usd
            strat_pnl[s]["trades"] += 1
            if t.pnl_usd > 0: strat_pnl[s]["wins"] += 1

        return {
            "total_trades":    len(trades),
            "win_rate":        round(len(wins)/len(pnls)*100, 1) if pnls else 0,
            "wins":            len(wins),
            "losses":          len(losses),
            "total_pnl":       round(sum(pnls), 2),
            "total_gross_pnl": round(sum(gross_pnls), 2),
            "total_brokerage": round(total_brok, 2),
            "avg_win":         round(avg_w, 2),
            "avg_loss":        round(avg_l, 2),
            "profit_factor":   round(pf, 2),
            "sharpe":          round(sharpe, 2),
            "best_trade":      round(max(pnls), 2) if pnls else 0,
            "worst_trade":     round(min(pnls), 2) if pnls else 0,
            "symbol_pnl":      sym_pnl,
            "exit_reasons":    exit_counts,
            "strategy_pnl":    strat_pnl,
        }

    # ------------------------------------------------------------------
    def _close(self, t, exit_price, reason):
        # Gross PnL
        if t.direction == "LONG":
            gross_pnl_pct = (exit_price - t.entry_price) / t.entry_price
        else:
            gross_pnl_pct = (t.entry_price - exit_price) / t.entry_price
        gross_pnl = gross_pnl_pct * t.size_usd

        # Apply full round-trip brokerage
        costs = self.brokerage.apply_round_trip_cost(
            t.symbol, t.size_usd, gross_pnl, reason
        )
        net_pnl     = costs["net_pnl"]
        brok_cost   = costs["total_cost"]
        net_pnl_pct = (net_pnl / t.size_usd) * 100 if t.size_usd else 0
        now         = datetime.now(timezone.utc).isoformat()

        with self._conn() as c:
            c.execute(
                """UPDATE trades SET status='CLOSED', close_time=?, exit_price=?,
                   exit_reason=?, gross_pnl=?, brokerage_cost=?,
                   pnl_usd=?, pnl_pct=? WHERE id=?""",
                (now, exit_price, reason,
                 round(gross_pnl,4), round(brok_cost,4),
                 round(net_pnl,4), round(net_pnl_pct,4), t.id)
            )

        emoji = "✅" if net_pnl > 0 else "❌"
        logger.info(
            f"{emoji} #{t.id} {t.symbol} [{reason}] | "
            f"Gross:${gross_pnl:+.2f} Brok:${brok_cost:.3f} Net:${net_pnl:+.2f} ({net_pnl_pct:+.2f}%)"
        )
        t.status = "CLOSED"; t.exit_price = exit_price; t.exit_reason = reason
        t.gross_pnl = round(gross_pnl,4); t.brokerage_cost = round(brok_cost,4)
        t.pnl_usd = round(net_pnl,4); t.pnl_pct = round(net_pnl_pct,4)
        return t

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT, direction TEXT,
                    entry_price REAL, stop_loss REAL, take_profit REAL,
                    trailing_stop REAL, peak_price REAL,
                    size_usd REAL, size_units REAL,
                    confidence REAL, risk_reward REAL,
                    open_time TEXT, close_time TEXT,
                    exit_price REAL, exit_reason TEXT,
                    gross_pnl REAL, brokerage_cost REAL,
                    pnl_usd REAL, pnl_pct REAL,
                    status TEXT, signal_reason TEXT,
                    llm_verdict TEXT, strategy TEXT
                )
            """)
            for col, typ in [
                ("peak_price","REAL"), ("strategy","TEXT"),
                ("gross_pnl","REAL"), ("brokerage_cost","REAL")
            ]:
                try: c.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
                except: pass

    def _conn(self): return sqlite3.connect(self.db_path)

    def _insert(self, t):
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO trades
                   (symbol,direction,entry_price,stop_loss,take_profit,
                    trailing_stop,peak_price,size_usd,size_units,confidence,
                    risk_reward,open_time,close_time,exit_price,exit_reason,
                    gross_pnl,brokerage_cost,pnl_usd,pnl_pct,status,
                    signal_reason,llm_verdict,strategy)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (t.symbol,t.direction,t.entry_price,t.stop_loss,t.take_profit,
                 t.trailing_stop,t.peak_price,t.size_usd,t.size_units,
                 t.confidence,t.risk_reward,t.open_time,t.close_time,
                 t.exit_price,t.exit_reason,t.gross_pnl,t.brokerage_cost,
                 t.pnl_usd,t.pnl_pct,t.status,t.signal_reason,
                 t.llm_verdict,t.strategy)
            )
            return cur.lastrowid

    def _update_field(self, tid, field, val):
        with self._conn() as c:
            c.execute(f"UPDATE trades SET {field}=? WHERE id=?", (val, tid))

    def _row(self, r):
        cols = ["id","symbol","direction","entry_price","stop_loss","take_profit",
                "trailing_stop","peak_price","size_usd","size_units","confidence",
                "risk_reward","open_time","close_time","exit_price","exit_reason",
                "gross_pnl","brokerage_cost","pnl_usd","pnl_pct","status",
                "signal_reason","llm_verdict","strategy"]
        d = dict(zip(cols, r))
        for k,v in [("peak_price",d.get("entry_price",0)),
                    ("strategy","BMV"),("gross_pnl",0.0),("brokerage_cost",0.0)]:
            if d.get(k) is None: d[k] = v
        return Trade(**d)
