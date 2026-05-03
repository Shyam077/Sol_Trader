"""
executor/paper.py
-----------------
Paper trading executor. Simulates order fills at market price,
manages open positions (trailing stop + time stop + TP),
and logs every trade to SQLite.
"""

import os
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    id:             int
    symbol:         str
    direction:      str
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    trailing_stop:  float       # tracks the trailing stop price
    size_usd:       float
    size_units:     float
    confidence:     float
    risk_reward:    float
    open_time:      str         # ISO format
    close_time:     str
    exit_price:     float
    exit_reason:    str         # "TP" | "SL" | "TRAILING" | "TIME" | "OPEN"
    pnl_usd:        float
    pnl_pct:        float
    status:         str         # "OPEN" | "CLOSED"
    signal_reason:  str
    llm_verdict:    str


class PaperExecutor:
    """
    Manages paper trades lifecycle:
    open → monitor (trailing stop, TP, time stop) → close
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("DB_PATH", "logs/trades.db")
        self.trailing_stop_pct = float(os.getenv("BMV_TRAILING_STOP_PCT", 0.015))
        self.max_hold_hours    = float(os.getenv("BMV_HOLD_MAX_HOURS", 4))
        self._init_db()
        logger.info(f"PaperExecutor ready | DB: {self.db_path}")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def open_trade(self, order, signal) -> Trade | None:
        """Opens a new paper trade. Returns Trade or None if already open."""
        open_trades = self.get_open_trades()
        if any(t.symbol == order.symbol for t in open_trades):
            logger.warning(f"Already have open trade for {order.symbol}, skipping")
            return None

        now = datetime.now(timezone.utc).isoformat()
        trade = Trade(
            id=0,
            symbol=order.symbol, direction=order.direction,
            entry_price=order.entry_price,
            stop_loss=order.stop_loss, take_profit=order.take_profit,
            trailing_stop=order.stop_loss,   # trailing stop starts at initial SL
            size_usd=order.size_usd, size_units=order.size_units,
            confidence=order.confidence, risk_reward=order.risk_reward,
            open_time=now, close_time="", exit_price=0.0,
            exit_reason="OPEN", pnl_usd=0.0, pnl_pct=0.0, status="OPEN",
            signal_reason=signal.reason, llm_verdict=signal.llm_verdict,
        )
        trade.id = self._insert_trade(trade)
        logger.info(
            f"📈 TRADE OPENED #{trade.id} | {order.symbol} {order.direction} | "
            f"Entry: {order.entry_price} | Size: ${order.size_usd:.2f} | "
            f"SL: {order.stop_loss} | TP: {order.take_profit}"
        )
        return trade

    def monitor_trades(self, current_prices: dict[str, float]) -> list[Trade]:
        """
        Check all open trades against current prices.
        Closes trades that hit TP, SL, trailing stop, or time limit.
        Returns list of trades that were closed this cycle.
        """
        closed = []
        open_trades = self.get_open_trades()

        for trade in open_trades:
            price = current_prices.get(trade.symbol)
            if price is None:
                continue

            exit_reason = self._check_exit(trade, price)
            if exit_reason:
                closed.append(self._close_trade(trade, price, exit_reason))
            else:
                # Update trailing stop
                self._update_trailing_stop(trade, price)

        return closed

    def get_open_trades(self) -> list[Trade]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'OPEN'"
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_all_trades(self, limit: int = 200) -> list[Trade]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_stats(self) -> dict:
        """Returns aggregate performance statistics."""
        trades = [t for t in self.get_all_trades() if t.status == "CLOSED"]
        if not trades:
            return {"total_trades": 0}

        pnls    = [t.pnl_usd for t in trades]
        wins    = [p for p in pnls if p > 0]
        losses  = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(trades) if trades else 0

        avg_win  = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        pf       = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

        returns = [t.pnl_pct for t in trades]
        import math
        avg_r = sum(returns) / len(returns)
        std_r = math.sqrt(sum((r - avg_r) ** 2 for r in returns) / len(returns)) if len(returns) > 1 else 0
        sharpe = (avg_r / std_r * (252 ** 0.5)) if std_r > 0 else 0

        return {
            "total_trades":  len(trades),
            "win_rate":      round(win_rate * 100, 1),
            "total_pnl":     round(sum(pnls), 2),
            "avg_win":       round(avg_win, 2),
            "avg_loss":      round(avg_loss, 2),
            "profit_factor": round(pf, 2),
            "sharpe":        round(sharpe, 2),
            "best_trade":    round(max(pnls), 2),
            "worst_trade":   round(min(pnls), 2),
            "exit_reasons":  self._count_exit_reasons(trades),
        }

    # ------------------------------------------------------------------
    # Private — trade lifecycle
    # ------------------------------------------------------------------

    def _check_exit(self, trade: Trade, price: float) -> str | None:
        """Returns exit reason string or None if trade stays open."""
        if trade.direction == "LONG":
            if price >= trade.take_profit:   return "TP"
            if price <= trade.trailing_stop: return "TRAILING" if trade.trailing_stop > trade.stop_loss else "SL"
        else:  # SHORT
            if price <= trade.take_profit:   return "TP"
            if price >= trade.trailing_stop: return "TRAILING" if trade.trailing_stop < trade.stop_loss else "SL"

        # Time stop
        open_dt = datetime.fromisoformat(trade.open_time)
        if open_dt.tzinfo is None:
            open_dt = open_dt.replace(tzinfo=timezone.utc)
        held_hours = (datetime.now(timezone.utc) - open_dt).total_seconds() / 3600
        if held_hours >= self.max_hold_hours:
            return "TIME"

        return None

    def _update_trailing_stop(self, trade: Trade, price: float):
        """Ratchets the trailing stop up (LONG) or down (SHORT)."""
        if trade.direction == "LONG":
            new_trail = price * (1 - self.trailing_stop_pct)
            if new_trail > trade.trailing_stop:
                self._update_trailing_in_db(trade.id, new_trail)
        else:
            new_trail = price * (1 + self.trailing_stop_pct)
            if new_trail < trade.trailing_stop:
                self._update_trailing_in_db(trade.id, new_trail)

    def _close_trade(self, trade: Trade, exit_price: float, reason: str) -> Trade:
        if trade.direction == "LONG":
            pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
        else:
            pnl_pct = (trade.entry_price - exit_price) / trade.entry_price

        pnl_usd = pnl_pct * trade.size_usd
        now     = datetime.now(timezone.utc).isoformat()

        with self._conn() as conn:
            conn.execute(
                """UPDATE trades SET status='CLOSED', close_time=?, exit_price=?,
                   exit_reason=?, pnl_usd=?, pnl_pct=? WHERE id=?""",
                (now, exit_price, reason, round(pnl_usd, 4), round(pnl_pct * 100, 4), trade.id)
            )

        emoji = "✅" if pnl_usd > 0 else "❌"
        logger.info(
            f"{emoji} TRADE CLOSED #{trade.id} | {trade.symbol} | "
            f"Exit: {exit_price} ({reason}) | PnL: ${pnl_usd:+.2f} ({pnl_pct*100:+.2f}%)"
        )
        trade.status = "CLOSED"
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.pnl_usd = round(pnl_usd, 4)
        trade.pnl_pct = round(pnl_pct * 100, 4)
        return trade

    # ------------------------------------------------------------------
    # Private — DB
    # ------------------------------------------------------------------

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol        TEXT, direction   TEXT,
                    entry_price   REAL, stop_loss   REAL,
                    take_profit   REAL, trailing_stop REAL,
                    size_usd      REAL, size_units  REAL,
                    confidence    REAL, risk_reward REAL,
                    open_time     TEXT, close_time  TEXT,
                    exit_price    REAL, exit_reason TEXT,
                    pnl_usd       REAL, pnl_pct     REAL,
                    status        TEXT, signal_reason TEXT,
                    llm_verdict   TEXT
                )
            """)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _insert_trade(self, t: Trade) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades
                   (symbol,direction,entry_price,stop_loss,take_profit,trailing_stop,
                    size_usd,size_units,confidence,risk_reward,open_time,close_time,
                    exit_price,exit_reason,pnl_usd,pnl_pct,status,signal_reason,llm_verdict)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (t.symbol,t.direction,t.entry_price,t.stop_loss,t.take_profit,t.trailing_stop,
                 t.size_usd,t.size_units,t.confidence,t.risk_reward,t.open_time,t.close_time,
                 t.exit_price,t.exit_reason,t.pnl_usd,t.pnl_pct,t.status,t.signal_reason,t.llm_verdict)
            )
            return cur.lastrowid

    def _update_trailing_in_db(self, trade_id: int, new_stop: float):
        with self._conn() as conn:
            conn.execute("UPDATE trades SET trailing_stop=? WHERE id=?", (new_stop, trade_id))

    def _row_to_trade(self, row) -> Trade:
        cols = ["id","symbol","direction","entry_price","stop_loss","take_profit",
                "trailing_stop","size_usd","size_units","confidence","risk_reward",
                "open_time","close_time","exit_price","exit_reason","pnl_usd","pnl_pct",
                "status","signal_reason","llm_verdict"]
        return Trade(**dict(zip(cols, row)))

    def _count_exit_reasons(self, trades: list[Trade]) -> dict:
        counts = {}
        for t in trades:
            counts[t.exit_reason] = counts.get(t.exit_reason, 0) + 1
        return counts
