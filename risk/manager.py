"""
risk/manager.py
---------------
Position sizing (Kelly fraction), drawdown kill switch,
per-trade risk validation. All decisions are logged.
"""

import os
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, date

logger = logging.getLogger(__name__)


@dataclass
class PositionOrder:
    symbol:         str
    direction:      str       # "LONG" | "SHORT"
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    size_usd:       float     # dollar amount to risk
    size_units:     float     # units of the asset
    confidence:     float
    risk_pct:       float     # % of portfolio being risked
    risk_reward:    float
    approved:       bool
    reject_reason:  str


class RiskManager:
    """
    Validates signals and sizes positions.

    Rules:
    - Max 10% of capital per trade
    - Max 2% capital at risk per trade (stop distance)
    - Max 3 concurrent open positions
    - Daily drawdown kill switch at 8%
    - Min risk/reward of 1.5
    """

    def __init__(self, initial_capital: float | None = None):
        self.initial_capital    = initial_capital or float(os.getenv("INITIAL_CAPITAL", 10000))
        self.capital            = self.initial_capital
        self.max_position_pct   = float(os.getenv("MAX_POSITION_PCT", 0.10))
        self.max_risk_pct       = 0.02          # max 2% capital at risk per trade
        self.max_positions      = 3
        self.daily_dd_limit     = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", 0.08))
        self.min_rr             = 1.5

        self.open_positions: dict[str, PositionOrder] = {}
        self.daily_start_equity = self.capital
        self.today              = date.today()
        self.killed             = False         # kill switch state

        logger.info(f"RiskManager ready | Capital: ${self.capital:,.2f}")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def evaluate(self, signal) -> PositionOrder:
        """
        Takes a BMVSignal, returns a PositionOrder.
        If approved=False, the executor must not trade.
        """
        self._reset_daily_if_needed()

        # Kill switch
        if self.killed:
            return self._reject(signal, "Kill switch active — daily drawdown limit hit")

        # Direction check
        if signal.direction == "NONE":
            return self._reject(signal, "No signal")

        # Confidence floor
        if signal.confidence < 0.55:
            return self._reject(signal, f"Low confidence {signal.confidence:.2f} < 0.55")

        # LLM veto
        if signal.llm_verdict == "FADE":
            return self._reject(signal, f"LLM vetoed: {signal.llm_reasoning}")

        # Already have a position in this symbol
        if signal.symbol in self.open_positions:
            return self._reject(signal, "Already holding position in this symbol")

        # Max concurrent positions
        if len(self.open_positions) >= self.max_positions:
            return self._reject(signal, f"Max {self.max_positions} concurrent positions reached")

        # Risk/reward check
        rr = self._calc_rr(signal)
        if rr < self.min_rr:
            return self._reject(signal, f"R/R {rr:.2f} < {self.min_rr}")

        # Daily drawdown check
        dd = self._daily_drawdown()
        if dd >= self.daily_dd_limit:
            self.killed = True
            return self._reject(signal, f"Daily drawdown {dd*100:.1f}% hit limit — kill switch activated")

        # Size the position
        order = self._size_position(signal, rr)

        if order.approved:
            logger.info(
                f"✅ APPROVED {signal.symbol} {signal.direction} | "
                f"Size: ${order.size_usd:.2f} | R/R: {rr:.2f} | "
                f"Confidence: {signal.confidence:.2f}"
            )
        return order

    def record_open(self, order: PositionOrder):
        """Call after executor confirms the paper trade is open."""
        self.open_positions[order.symbol] = order
        logger.info(f"Position opened: {order.symbol} {order.direction} ${order.size_usd:.2f}")

    def record_close(self, symbol: str, exit_price: float):
        """Call when a position is closed. Updates capital."""
        if symbol not in self.open_positions:
            return
        order = self.open_positions.pop(symbol)
        if order.direction == "LONG":
            pnl = (exit_price - order.entry_price) / order.entry_price * order.size_usd
        else:
            pnl = (order.entry_price - exit_price) / order.entry_price * order.size_usd

        self.capital += pnl
        logger.info(
            f"Position closed: {symbol} | Exit: {exit_price} | "
            f"PnL: ${pnl:+.2f} | Capital: ${self.capital:,.2f}"
        )

    def get_status(self) -> dict:
        return {
            "capital":          round(self.capital, 2),
            "initial_capital":  self.initial_capital,
            "total_return_pct": round((self.capital / self.initial_capital - 1) * 100, 2),
            "daily_drawdown":   round(self._daily_drawdown() * 100, 2),
            "open_positions":   len(self.open_positions),
            "kill_switch":      self.killed,
        }

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _size_position(self, signal, rr: float) -> PositionOrder:
        """Kelly-inspired position sizing capped at max_position_pct."""
        # Dollar risk = distance to stop as % of entry
        stop_dist_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price

        # Max dollars we're willing to lose on this trade = 2% of capital
        max_risk_usd = self.capital * self.max_risk_pct

        # Position size based on stop distance
        size_from_risk = max_risk_usd / stop_dist_pct if stop_dist_pct > 0 else 0

        # Cap at max_position_pct of capital
        size_cap = self.capital * self.max_position_pct

        # Scale by confidence (0.55–1.0 maps to 70%–100% of allowed size)
        confidence_scale = 0.7 + (signal.confidence - 0.55) * (0.3 / 0.45)
        size_usd = min(size_from_risk, size_cap) * confidence_scale

        size_units = size_usd / signal.entry_price if signal.entry_price > 0 else 0
        risk_pct   = (size_usd * stop_dist_pct) / self.capital

        return PositionOrder(
            symbol=signal.symbol, direction=signal.direction,
            entry_price=signal.entry_price, stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            size_usd=round(size_usd, 2), size_units=round(size_units, 6),
            confidence=signal.confidence, risk_pct=round(risk_pct, 4),
            risk_reward=round(rr, 2), approved=True, reject_reason="",
        )

    def _calc_rr(self, signal) -> float:
        reward = abs(signal.take_profit - signal.entry_price)
        risk   = abs(signal.entry_price - signal.stop_loss)
        return reward / risk if risk > 0 else 0.0

    def _daily_drawdown(self) -> float:
        return max(0.0, (self.daily_start_equity - self.capital) / self.daily_start_equity)

    def _reset_daily_if_needed(self):
        today = date.today()
        if today != self.today:
            self.today              = today
            self.daily_start_equity = self.capital
            self.killed             = False
            logger.info(f"Daily reset | Start equity: ${self.capital:,.2f}")

    def _reject(self, signal, reason: str) -> PositionOrder:
        logger.debug(f"❌ REJECTED {getattr(signal, 'symbol', '?')} — {reason}")
        price = getattr(signal, "entry_price", 0.0)
        return PositionOrder(
            symbol=getattr(signal, "symbol", "?"),
            direction=getattr(signal, "direction", "NONE"),
            entry_price=price, stop_loss=0.0, take_profit=0.0,
            size_usd=0.0, size_units=0.0, confidence=0.0,
            risk_pct=0.0, risk_reward=0.0,
            approved=False, reject_reason=reason,
        )
