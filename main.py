"""
main.py
-------
Entry point. Runs the trading agent on a 5-minute schedule.
Start with: python main.py
"""

import logging
import os
import time
import signal as sys_signal
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

from apscheduler.schedulers.blocking import BlockingScheduler
from data.fetcher import DataPipeline
from signals.bmv import BMVDetector
from risk.manager import RiskManager
from executor.paper import PaperExecutor
from agent import build_graph, AgentState

POLL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", 300))
cycle_counter = {"n": 0}


def run_cycle(graph, risk_mgr: RiskManager, executor: PaperExecutor):
    """One full agent cycle: fetch → signal → risk → execute → monitor."""
    cycle_counter["n"] += 1
    n = cycle_counter["n"]

    logger.info(f"{'='*50}")
    logger.info(f"CYCLE {n} | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info(f"{'='*50}")

    initial_state: AgentState = {
        "cycle":         n,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "snapshot":      {},
        "signals":       [],
        "orders":        [],
        "closed_trades": [],
        "risk_status":   {},
        "errors":        [],
    }

    try:
        final_state = graph.invoke(initial_state)

        # Log cycle summary
        status = risk_mgr.get_status()
        stats  = executor.get_stats()
        open_t = executor.get_open_trades()

        logger.info(f"Capital:     ${status['capital']:>10,.2f}  ({status['total_return_pct']:+.2f}%)")
        logger.info(f"Daily DD:    {status['daily_drawdown']:.2f}%  |  Kill switch: {status['kill_switch']}")
        logger.info(f"Open pos:    {len(open_t)}  |  Total trades: {stats.get('total_trades', 0)}")
        logger.info(f"Win rate:    {stats.get('win_rate', 0):.1f}%  |  Sharpe: {stats.get('sharpe', 0):.2f}")

        if final_state.get("errors"):
            for err in final_state["errors"]:
                logger.error(f"  Error: {err}")

    except Exception as e:
        logger.exception(f"Cycle {n} crashed: {e}")


def main():
    logger.info("🚀 Crypto Trading Agent starting...")
    logger.info(f"Mode: {'PAPER' if os.getenv('PAPER_TRADING','true')=='true' else '⚠️  LIVE'}")
    logger.info(f"Poll interval: {POLL_SECONDS}s")

    pipeline = DataPipeline()
    detector = BMVDetector()
    risk_mgr = RiskManager()
    executor = PaperExecutor()
    graph    = build_graph(pipeline, detector, risk_mgr, executor)

    # Graceful shutdown
    def _shutdown(sig, frame):
        logger.info("Shutdown signal received. Stopping scheduler...")
        scheduler.shutdown(wait=False)
    sys_signal.signal(sys_signal.SIGINT, _shutdown)
    sys_signal.signal(sys_signal.SIGTERM, _shutdown)

    # Run once immediately on startup
    run_cycle(graph, risk_mgr, executor)

    # Then every POLL_SECONDS
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_cycle,
        "interval",
        seconds=POLL_SECONDS,
        args=[graph, risk_mgr, executor],
        id="trading_cycle",
    )

    logger.info(f"Scheduler started. Next cycle in {POLL_SECONDS}s. Press Ctrl+C to stop.")
    scheduler.start()


if __name__ == "__main__":
    main()
