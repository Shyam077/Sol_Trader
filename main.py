"""
main.py — Shyam's Crypto Scalping Agent
Runs BMV (15m) + Scalping (1m/3m) every 120 seconds.
"""
import logging, os, signal as sys_signal
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
from signals.scalper import ScalpingEngine
from risk.manager import RiskManager
from executor.paper import PaperExecutor
from agent import build_graph, AgentState

POLL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", 120))
cycle_counter = {"n": 0}


def run_cycle(graph, risk_mgr, executor):
    cycle_counter["n"] += 1
    n = cycle_counter["n"]
    logger.info(f"{'='*55}")
    logger.info(f"⚡ CYCLE {n} | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info(f"{'='*55}")
    try:
        final = graph.invoke({
            "cycle": n, "timestamp": datetime.now(timezone.utc).isoformat(),
            "snapshot": {}, "snapshot_1m": {}, "snapshot_3m": {},
            "signals": [], "orders": [], "closed_trades": [],
            "risk_status": {}, "errors": [],
        })
        status = risk_mgr.get_status()
        stats  = executor.get_stats()
        open_t = executor.get_open_trades()
        logger.info(f"💰 Capital: ${status['capital']:>10,.2f} ({status['total_return_pct']:+.2f}%)")
        logger.info(f"📊 DD: {status['daily_drawdown']:.2f}% | Kill: {status['kill_switch']}")
        logger.info(f"📈 Open: {len(open_t)} | Trades: {stats.get('total_trades',0)} | WR: {stats.get('win_rate',0):.1f}%")
        for t in final.get("closed_trades", []):
            emoji = "✅" if t["pnl_usd"] > 0 else "❌"
            logger.info(f"{emoji} CLOSED {t['symbol']} PnL: ${t['pnl_usd']:+.2f} [{t['exit_reason']}]")
        for e in final.get("errors", []): logger.error(f"  {e}")
    except Exception as e:
        logger.exception(f"Cycle {n} crashed: {e}")


def main():
    logger.info("🚀 Shyam's Crypto Trading Agent — SCALPING MODE")
    logger.info(f"📊 Mode: PAPER | Poll: {POLL_SECONDS}s | Pairs: 8")
    logger.info("⚡ Strategies: BMV(15m) + EMA_CROSS + RSI_REVERSAL + VWAP_BOUNCE + MICRO_BREAKOUT")

    pipeline = DataPipeline()
    bmv      = BMVDetector()
    scalper  = ScalpingEngine()
    risk_mgr = RiskManager()
    executor = PaperExecutor()
    graph    = build_graph(pipeline, bmv, scalper, risk_mgr, executor)

    def _shutdown(sig, frame):
        logger.info("Shutdown signal. Stopping...")
        scheduler.shutdown(wait=False)

    sys_signal.signal(sys_signal.SIGINT,  _shutdown)
    sys_signal.signal(sys_signal.SIGTERM, _shutdown)

    run_cycle(graph, risk_mgr, executor)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_cycle, "interval", seconds=POLL_SECONDS,
                      args=[graph, risk_mgr, executor], id="trading_cycle")
    logger.info(f"Scheduler started. Cycling every {POLL_SECONDS}s.")
    scheduler.start()


if __name__ == "__main__":
    main()
