#!/usr/bin/env python3
"""
export_snapshot.py
------------------
Exports lightweight trade summary (CSV + JSON) from SQLite.
Run by cron every 15 minutes. Push THESE files to GitHub, not the DB.

Usage: python export_snapshot.py
"""

import os
import json
import math
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone

DB_PATH      = os.getenv("DB_PATH", "logs/trades.db")
EXPORT_DIR   = Path("logs/exports")
CSV_PATH     = EXPORT_DIR / "trades_summary.csv"
JSON_PATH    = EXPORT_DIR / "stats_snapshot.json"
INITIAL_CAP  = float(os.getenv("INITIAL_CAPITAL", 10000))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger(__name__)


def export():
    if not Path(DB_PATH).exists():
        logger.warning(f"DB not found at {DB_PATH}")
        return

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        # ── 1. Trade summary CSV (no sensitive fields) ──────────────
        rows = conn.execute("""
            SELECT
                id, symbol, direction, status,
                entry_price, exit_price, exit_reason,
                pnl_usd, pnl_pct,
                confidence, llm_verdict,
                open_time, close_time
            FROM trades
            ORDER BY id DESC
            LIMIT 200
        """).fetchall()

        import csv
        with open(CSV_PATH, "w", newline="") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows([dict(r) for r in rows])

        logger.info(f"CSV exported: {len(rows)} trades → {CSV_PATH}")

        # ── 2. Stats JSON snapshot ───────────────────────────────────
        closed = [r for r in rows if r["status"] == "CLOSED"]
        pnls   = [r["pnl_usd"] for r in closed]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        open_t = [r for r in rows if r["status"] == "OPEN"]

        avg_w  = sum(wins)/len(wins) if wins else 0
        avg_l  = sum(losses)/len(losses) if losses else 0
        pf     = abs(avg_w/avg_l) if avg_l else 0

        returns = [r["pnl_pct"] for r in closed]
        avg_r   = sum(returns)/len(returns) if returns else 0
        std_r   = math.sqrt(sum((r-avg_r)**2 for r in returns)/len(returns)) if len(returns) > 1 else 0
        sharpe  = (avg_r/std_r*(252**0.5)) if std_r else 0

        total_pnl = sum(pnls)
        capital   = INITIAL_CAP + total_pnl

        # Per symbol breakdown
        sym_pnl = {}
        for r in closed:
            sym_pnl[r["symbol"]] = round(sym_pnl.get(r["symbol"], 0) + r["pnl_usd"], 2)

        # Exit reason counts
        exit_counts = {}
        for r in closed:
            exit_counts[r["exit_reason"]] = exit_counts.get(r["exit_reason"], 0) + 1

        # Equity curve (last 50 points)
        equity_curve = []
        eq = INITIAL_CAP
        for r in sorted(closed, key=lambda x: x["close_time"] or ""):
            eq += r["pnl_usd"]
            equity_curve.append({
                "time": r["close_time"],
                "equity": round(eq, 2),
                "trade_id": r["id"],
            })
        equity_curve = equity_curve[-50:]  # last 50 points only

        snapshot = {
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "capital":        round(capital, 2),
            "initial_capital": INITIAL_CAP,
            "total_pnl":      round(total_pnl, 2),
            "return_pct":     round(total_pnl / INITIAL_CAP * 100, 2),
            "total_trades":   len(closed),
            "open_positions": len(open_t),
            "win_rate":       round(len(wins)/len(pnls)*100, 1) if pnls else 0,
            "wins":           len(wins),
            "losses":         len(losses),
            "profit_factor":  round(pf, 2),
            "sharpe":         round(sharpe, 2),
            "avg_win":        round(avg_w, 2),
            "avg_loss":       round(avg_l, 2),
            "best_trade":     round(max(pnls), 2) if pnls else 0,
            "worst_trade":    round(min(pnls), 2) if pnls else 0,
            "symbol_pnl":     sym_pnl,
            "exit_reasons":   exit_counts,
            "equity_curve":   equity_curve,
            "open_trades":    [
                {
                    "symbol":       r["symbol"],
                    "direction":    r["direction"],
                    "entry_price":  r["entry_price"],
                    "confidence":   r["confidence"],
                    "llm_verdict":  r["llm_verdict"],
                    "open_time":    r["open_time"],
                }
                for r in open_t
            ],
        }

        with open(JSON_PATH, "w") as f:
            json.dump(snapshot, f, indent=2)

        logger.info(f"JSON snapshot exported → {JSON_PATH}")
        logger.info(f"Capital: ${capital:,.2f} | Return: {snapshot['return_pct']:+.2f}% | "
                    f"Trades: {len(closed)} | Win rate: {snapshot['win_rate']}%")


if __name__ == "__main__":
    export()
