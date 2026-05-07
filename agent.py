"""
agent.py
--------
LangGraph state machine that orchestrates the full trading loop.

Graph nodes:
  fetch_data → run_signals → evaluate_risk → execute_trades → monitor_exits → [back to fetch]

State flows through all nodes as a typed dict.
"""

import logging
import os
from datetime import datetime, timezone
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, END

from data.fetcher import DataPipeline
from data.features import compute_features
from signals.bmv import BMVDetector, BMVSignal
from risk.manager import RiskManager, PositionOrder
from executor.paper import PaperExecutor

logger = logging.getLogger(__name__)

PAIRS = [p.replace("-", "/").replace("/USD", "/USDT")
         for p in os.getenv("TRADE_PAIRS", "BTC-USD,ETH-USD,SOL-USD").split(",")]


# ------------------------------------------------------------------
# State schema
# ------------------------------------------------------------------

class AgentState(TypedDict):
    cycle:          int
    timestamp:      str
    snapshot:       dict                        # raw market data
    signals:        Annotated[list, operator.add]  # BMVSignal dicts
    orders:         Annotated[list, operator.add]  # PositionOrder dicts
    closed_trades:  Annotated[list, operator.add]  # Trade dicts
    risk_status:    dict
    errors:         Annotated[list, operator.add]


# ------------------------------------------------------------------
# Node functions
# ------------------------------------------------------------------

def fetch_data(state: AgentState, pipeline: DataPipeline) -> AgentState:
    """Node 1: Pull market snapshot from Coinbase + sentiment APIs."""
    logger.info(f"[Cycle {state['cycle']}] Fetching market data...")
    try:
        snapshot = pipeline.snapshot(timeframe="15m")
        return {**state, "snapshot": snapshot, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.error(f"fetch_data error: {e}")
        return {**state, "errors": state["errors"] + [f"fetch_data: {e}"]}


def run_signals(state: AgentState, detector: BMVDetector) -> AgentState:
    """Node 2: Run BMV detector on each symbol."""
    if not state.get("snapshot"):
        return state

    snap       = state["snapshot"]
    fear_greed = snap.get("fear_greed", {})
    new_signals = []

    for symbol in PAIRS:
        df = snap["ohlcv"].get(symbol)
        ob = snap["order_books"].get(symbol, {})

        if df is None or df.empty:
            logger.warning(f"No OHLCV data for {symbol}")
            continue

        try:
            sig = detector.detect(symbol=symbol, df=df, order_book=ob, fear_greed=fear_greed)
            new_signals.append(sig.to_dict())

            if sig.direction != "NONE":
                logger.info(
                    f"🔔 SIGNAL: {symbol} {sig.direction} | "
                    f"Confidence: {sig.confidence:.2f} | "
                    f"LLM: {sig.llm_verdict} | {sig.reason}"
                )
        except Exception as e:
            logger.error(f"run_signals error for {symbol}: {e}")
            state["errors"].append(f"run_signals {symbol}: {e}")

    return {**state, "signals": new_signals}


def evaluate_risk(state: AgentState, risk_mgr: RiskManager) -> AgentState:
    """Node 3: Run each actionable signal through risk manager."""
    from signals.bmv import BMVSignal
    new_orders = []

    for sig_dict in state.get("signals", []):
        # Reconstruct minimal signal object for risk manager
        sig = _dict_to_signal(sig_dict)
        if not sig.is_actionable:
            continue

        order = risk_mgr.evaluate(sig)
        new_orders.append({
            "approved":     order.approved,
            "symbol":       order.symbol,
            "direction":    order.direction,
            "entry_price":  order.entry_price,
            "stop_loss":    order.stop_loss,
            "take_profit":  order.take_profit,
            "size_usd":     order.size_usd,
            "size_units":   order.size_units,
            "confidence":   order.confidence,
            "risk_reward":  order.risk_reward,
            "reject_reason": order.reject_reason,
        })

    risk_status = risk_mgr.get_status()
    return {**state, "orders": new_orders, "risk_status": risk_status}


def execute_trades(state: AgentState, executor: PaperExecutor, risk_mgr: RiskManager) -> AgentState:
    """Node 4: Open paper trades for approved orders."""
    for order_dict in state.get("orders", []):
        if not order_dict.get("approved"):
            continue

        # Find the matching signal
        sig_dict = next(
            (s for s in state["signals"] if s["symbol"] == order_dict["symbol"]),
            {}
        )
        sig    = _dict_to_signal(sig_dict)
        order  = _dict_to_order(order_dict)
        trade  = executor.open_trade(order, sig)
        if trade:
            risk_mgr.record_open(order)

    return state


def monitor_exits(state: AgentState, executor: PaperExecutor, risk_mgr: RiskManager) -> AgentState:
    """Node 5: Check open trades for TP / SL / trailing / time exits."""
    snap = state.get("snapshot", {})
    tickers = snap.get("tickers", {})

    current_prices = {
        symbol: t.get("price")
        for symbol, t in tickers.items()
        if t.get("price")
    }

    closed = executor.monitor_trades(current_prices)
    for trade in closed:
        risk_mgr.record_close(trade.symbol, trade.exit_price)

    closed_dicts = [
        {"id": t.id, "symbol": t.symbol, "direction": t.direction,
         "pnl_usd": t.pnl_usd, "pnl_pct": t.pnl_pct, "exit_reason": t.exit_reason}
        for t in closed
    ]
    return {**state, "closed_trades": closed_dicts}


# ------------------------------------------------------------------
# Graph builder
# ------------------------------------------------------------------

def build_graph(pipeline, detector, risk_mgr, executor):
    """Builds and compiles the LangGraph trading agent."""

    def _fetch(s):    return fetch_data(s, pipeline)
    def _signals(s):  return run_signals(s, detector)
    def _risk(s):     return evaluate_risk(s, risk_mgr)
    def _execute(s):  return execute_trades(s, executor, risk_mgr)
    def _monitor(s):  return monitor_exits(s, executor, risk_mgr)

    g = StateGraph(AgentState)
    g.add_node("fetch_data",     _fetch)
    g.add_node("run_signals",    _signals)
    g.add_node("evaluate_risk",  _risk)
    g.add_node("execute_trades", _execute)
    g.add_node("monitor_exits",  _monitor)

    g.set_entry_point("fetch_data")
    g.add_edge("fetch_data",     "run_signals")
    g.add_edge("run_signals",    "evaluate_risk")
    g.add_edge("evaluate_risk",  "execute_trades")
    g.add_edge("execute_trades", "monitor_exits")
    g.add_edge("monitor_exits",  END)

    return g.compile()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _dict_to_signal(d: dict):
    """Reconstructs a minimal BMVSignal from a dict."""
    from signals.bmv import BMVSignal
    from datetime import datetime, timezone
    return BMVSignal(
        symbol=d.get("symbol", ""),
        direction=d.get("direction", "NONE"),
        confidence=d.get("confidence", 0.0),
        entry_price=d.get("entry_price", 0.0),
        stop_loss=d.get("stop_loss", 0.0),
        take_profit=d.get("take_profit", 0.0),
        breakout_level=d.get("breakout_level", 0.0),
        vol_ratio=d.get("vol_ratio", 0.0),
        rsi=d.get("rsi", 50.0),
        trend_score=d.get("trend_score", 0.0),
        llm_verdict=d.get("llm_verdict", "N/A"),
        llm_reasoning=d.get("llm_reasoning", ""),
        reason=d.get("reason", ""),
    )


def _dict_to_order(d: dict):
    from risk.manager import PositionOrder
    return PositionOrder(
        symbol=d["symbol"], direction=d["direction"],
        entry_price=d["entry_price"], stop_loss=d["stop_loss"],
        take_profit=d["take_profit"], size_usd=d["size_usd"],
        size_units=d["size_units"], confidence=d["confidence"],
        risk_pct=0.0, risk_reward=d["risk_reward"],
        approved=d["approved"], reject_reason=d.get("reject_reason", ""),
    )
