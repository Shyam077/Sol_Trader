"""
agent.py — Combined BMV + Scalping agent
"""
import logging, os
from datetime import datetime, timezone
from typing import TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, END
from data.fetcher import DataPipeline
from signals.bmv import BMVDetector, BMVSignal
from signals.scalper import ScalpingEngine
from risk.manager import RiskManager, PositionOrder
from executor.paper import PaperExecutor

logger = logging.getLogger(__name__)

PAIRS = [p.strip().replace("-","/").replace("/USD","/USDT")
         for p in os.getenv("TRADE_PAIRS",
         "BTC-USD,ETH-USD,SOL-USD,AVAX-USD,LINK-USD,DOT-USD,ADA-USD,MATIC-USD").split(",")]


class AgentState(TypedDict):
    cycle:         int
    timestamp:     str
    snapshot:      dict
    snapshot_1m:   dict
    snapshot_3m:   dict
    signals:       Annotated[list, operator.add]
    orders:        Annotated[list, operator.add]
    closed_trades: Annotated[list, operator.add]
    risk_status:   dict
    errors:        Annotated[list, operator.add]


def fetch_data(state, pipeline):
    logger.info(f"[Cycle {state['cycle']}] Fetching data...")
    try:
        snap_15m = pipeline.snapshot(timeframe="15m")
        snap_3m  = pipeline.snapshot_ohlcv(timeframe="3m")
        snap_1m  = pipeline.snapshot_ohlcv(timeframe="1m")
        return {**state, "snapshot": snap_15m, "snapshot_3m": snap_3m,
                "snapshot_1m": snap_1m, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.error(f"fetch_data: {e}")
        return {**state, "errors": state["errors"] + [str(e)]}


def run_signals(state, bmv, scalper):
    new_signals = []
    snap = state.get("snapshot", {})
    fg   = snap.get("fear_greed", {})
    for symbol in PAIRS:
        ob     = snap.get("order_books", {}).get(symbol, {})
        df_15m = snap.get("ohlcv", {}).get(symbol)
        df_1m  = state.get("snapshot_1m", {}).get(symbol)
        df_3m  = state.get("snapshot_3m", {}).get(symbol)
        # BMV 15m
        if df_15m is not None and not df_15m.empty:
            try:
                sig = bmv.detect(symbol=symbol, df=df_15m, order_book=ob, fear_greed=fg)
                new_signals.append(sig.to_dict())
                if sig.direction != "NONE":
                    logger.info(f"🔔 BMV {symbol} {sig.direction} | Conf:{sig.confidence:.2f} | {sig.reason}")
            except Exception as e:
                logger.error(f"BMV {symbol}: {e}")
        # Scalping 1m+3m
        try:
            for ss in scalper.scan(symbol, df_1m, df_3m, ob):
                new_signals.append(ss.to_dict())
                logger.info(f"⚡ SCALP {symbol} {ss.direction} [{ss.strategy}] Conf:{ss.confidence:.2f}")
        except Exception as e:
            logger.error(f"Scalp {symbol}: {e}")
    return {**state, "signals": new_signals}


def evaluate_risk(state, risk_mgr):
    new_orders = []
    for d in state.get("signals", []):
        sig = _to_sig(d)
        if not sig.is_actionable: continue
        o = risk_mgr.evaluate(sig)
        new_orders.append({**vars(o), "strategy": d.get("strategy","BMV")})
    return {**state, "orders": new_orders, "risk_status": risk_mgr.get_status()}


def execute_trades(state, executor, risk_mgr):
    for od in state.get("orders", []):
        if not od.get("approved"): continue
        sd  = next((s for s in state["signals"] if s["symbol"]==od["symbol"]), {})
        sig = _to_sig(sd)
        o   = _to_order(od)
        t   = executor.open_trade(o, sig)
        if t: risk_mgr.record_open(o)
    return state


def monitor_exits(state, executor, risk_mgr):
    prices = {}
    for sym in PAIRS:
        df = state.get("snapshot_1m", {}).get(sym)
        if df is not None and not df.empty:
            prices[sym] = float(df["close"].iloc[-1])
        elif state.get("snapshot", {}).get("tickers", {}).get(sym, {}).get("price"):
            prices[sym] = state["snapshot"]["tickers"][sym]["price"]
    closed = executor.monitor_trades(prices)
    for t in closed: risk_mgr.record_close(t.symbol, t.exit_price)
    return {**state, "closed_trades": [
        {"id":t.id,"symbol":t.symbol,"pnl_usd":t.pnl_usd,"exit_reason":t.exit_reason}
        for t in closed]}


def build_graph(pipeline, bmv, scalper, risk_mgr, executor):
    g = StateGraph(AgentState)
    g.add_node("fetch",   lambda s: fetch_data(s, pipeline))
    g.add_node("signals", lambda s: run_signals(s, bmv, scalper))
    g.add_node("risk",    lambda s: evaluate_risk(s, risk_mgr))
    g.add_node("execute", lambda s: execute_trades(s, executor, risk_mgr))
    g.add_node("monitor", lambda s: monitor_exits(s, executor, risk_mgr))
    g.set_entry_point("fetch")
    g.add_edge("fetch","signals"); g.add_edge("signals","risk")
    g.add_edge("risk","execute");  g.add_edge("execute","monitor")
    g.add_edge("monitor", END)
    return g.compile()


def _to_sig(d):
    return BMVSignal(
        symbol=d.get("symbol",""), direction=d.get("direction","NONE"),
        confidence=d.get("confidence",0.0), entry_price=d.get("entry_price",0.0),
        stop_loss=d.get("stop_loss",0.0), take_profit=d.get("take_profit",0.0),
        breakout_level=d.get("breakout_level",0.0), vol_ratio=d.get("vol_ratio",0.0),
        rsi=d.get("rsi",50.0), trend_score=d.get("trend_score",0.0),
        llm_verdict=d.get("llm_verdict","N/A"), llm_reasoning=d.get("llm_reasoning",""),
        reason=d.get("reason",d.get("strategy","")),
    )

def _to_order(d):
    return PositionOrder(
        symbol=d["symbol"], direction=d["direction"],
        entry_price=d["entry_price"], stop_loss=d["stop_loss"],
        take_profit=d["take_profit"], size_usd=d["size_usd"],
        size_units=d["size_units"], confidence=d["confidence"],
        risk_pct=0.0, risk_reward=d["risk_reward"],
        approved=d["approved"], reject_reason=d.get("reject_reason",""),
    )
