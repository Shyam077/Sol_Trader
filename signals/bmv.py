"""
signals/bmv.py
--------------
Breakout + Momentum + Volume (BMV) signal detector.

Rules (adapted from Indian options BMV for crypto):
  1. Resistance/support = rolling N-bar high/low
  2. CLOSE must cross the level (no wick-only breakouts)
  3. Volume spike >= 2.5x 20-period average
  4. RSI > 58 for LONG, < 42 for SHORT
  5. Price above EMA21 for LONG, below for SHORT
  6. Order book imbalance sanity check
  7. Avoid 12am–6am IST (low-volume session)
  8. LLM reasoning step to confirm or veto the signal
"""

import os
import json
import logging
import requests
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from data.features import compute_features

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

AVOID_HOUR_START = 0
AVOID_HOUR_END   = 6


@dataclass
class BMVSignal:
    symbol:         str
    direction:      str       # "LONG" | "SHORT" | "NONE"
    confidence:     float     # 0.0 – 1.0
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    breakout_level: float
    vol_ratio:      float
    rsi:            float
    trend_score:    float
    llm_verdict:    str       # "CONFIRM" | "FADE" | "WAIT" | "N/A"
    llm_reasoning:  str
    reason:         str
    timestamp:      datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "symbol":         self.symbol,
            "direction":      self.direction,
            "confidence":     round(self.confidence, 3),
            "entry_price":    self.entry_price,
            "stop_loss":      self.stop_loss,
            "take_profit":    self.take_profit,
            "breakout_level": self.breakout_level,
            "vol_ratio":      round(self.vol_ratio, 2),
            "rsi":            round(self.rsi, 1),
            "trend_score":    round(self.trend_score, 2),
            "llm_verdict":    self.llm_verdict,
            "llm_reasoning":  self.llm_reasoning,
            "reason":         self.reason,
            "timestamp":      self.timestamp.isoformat(),
        }

    @property
    def is_actionable(self) -> bool:
        """True if signal should be sent to executor."""
        return (
            self.direction != "NONE"
            and self.confidence >= 0.55
            and self.llm_verdict in ("CONFIRM", "N/A")
        )


class BMVDetector:
    """Detects BMV breakout setups on a single symbol."""

    def __init__(self):
        self.vol_multiplier = float(os.getenv("BMV_VOLUME_MULTIPLIER", 2.5))
        self.rsi_long_min   = float(os.getenv("BMV_RSI_MIN", 58))
        self.rsi_short_max  = float(os.getenv("BMV_RSI_MAX", 42))
        self.lookback       = int(os.getenv("BMV_LOOKBACK_CANDLES", 20))
        self.atr_tp_mult    = 2.0
        self.atr_sl_mult    = 1.0
        self.use_llm        = os.getenv("USE_LLM_FILTER", "true").lower() == "true"

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def detect(self, symbol: str, df: pd.DataFrame, order_book: dict | None = None, fear_greed: dict | None = None) -> BMVSignal:
        if df.empty or len(df) < self.lookback + 10:
            return self._none(symbol, df, "Insufficient data")

        if self._is_low_volume_session():
            return self._none(symbol, df, "Low-volume session (12am–6am IST)")

        enriched = compute_features(df)
        if enriched.empty:
            return self._none(symbol, df, "Feature computation failed")

        last = enriched.iloc[-1]
        prev = enriched.iloc[-2]

        close       = float(last["close"])
        vol_ratio   = float(last.get("vol_ratio", 0) or 0)
        rsi         = float(last.get("rsi", 50) or 50)
        ema21       = float(last.get("ema21", close) or close)
        atr         = float(last.get("atr", close * 0.01) or close * 0.01)
        trend_score = float(last.get("trend_score", 0) or 0)

        resistance = float(enriched["high"].iloc[-(self.lookback + 1):-1].max())
        support    = float(enriched["low"].iloc[-(self.lookback + 1):-1].min())

        # Try LONG
        sig = self._check_long(symbol, close, float(prev["close"]), resistance,
                               vol_ratio, rsi, ema21, atr, trend_score, order_book)
        if sig:
            return self._apply_llm(sig, fear_greed)

        # Try SHORT
        sig = self._check_short(symbol, close, float(prev["close"]), support,
                                vol_ratio, rsi, ema21, atr, trend_score, order_book)
        if sig:
            return self._apply_llm(sig, fear_greed)

        return self._none(symbol, df, "No breakout setup")

    # ------------------------------------------------------------------
    # Long / Short checks
    # ------------------------------------------------------------------

    def _check_long(self, symbol, close, prev_close, resistance,
                    vol_ratio, rsi, ema21, atr, trend_score, order_book) -> "BMVSignal | None":
        # 1. Close above resistance (not wick)
        if not (close > resistance and prev_close <= resistance):
            return None
        # 2. Volume spike
        if vol_ratio < self.vol_multiplier:
            logger.debug(f"{symbol} LONG: vol {vol_ratio:.2f}x < {self.vol_multiplier}x")
            return None
        # 3. RSI
        if rsi < self.rsi_long_min:
            logger.debug(f"{symbol} LONG: RSI {rsi:.1f} too low")
            return None
        # 4. Trend alignment
        if close < ema21:
            logger.debug(f"{symbol} LONG: price below EMA21")
            return None
        # 5. Order book
        if order_book and order_book.get("imbalance", 0.5) < 0.45:
            logger.debug(f"{symbol} LONG: bearish order book")
            return None

        confidence = self._score_long(vol_ratio, rsi, trend_score, order_book)
        return BMVSignal(
            symbol=symbol, direction="LONG", confidence=confidence,
            entry_price=close,
            stop_loss=round(close - self.atr_sl_mult * atr, 4),
            take_profit=round(close + self.atr_tp_mult * atr, 4),
            breakout_level=resistance,
            vol_ratio=vol_ratio, rsi=rsi, trend_score=trend_score,
            llm_verdict="N/A", llm_reasoning="",
            reason=f"Breakout above {resistance:.2f} | Vol {vol_ratio:.1f}x | RSI {rsi:.1f}",
        )

    def _check_short(self, symbol, close, prev_close, support,
                     vol_ratio, rsi, ema21, atr, trend_score, order_book) -> "BMVSignal | None":
        # 1. Close below support (not wick)
        if not (close < support and prev_close >= support):
            return None
        # 2. Volume spike
        if vol_ratio < self.vol_multiplier:
            return None
        # 3. RSI
        if rsi > self.rsi_short_max:
            return None
        # 4. Trend alignment
        if close > ema21:
            return None
        # 5. Order book
        if order_book and order_book.get("imbalance", 0.5) > 0.55:
            return None

        confidence = self._score_short(vol_ratio, rsi, trend_score, order_book)
        return BMVSignal(
            symbol=symbol, direction="SHORT", confidence=confidence,
            entry_price=close,
            stop_loss=round(close + self.atr_sl_mult * atr, 4),
            take_profit=round(close - self.atr_tp_mult * atr, 4),
            breakout_level=support,
            vol_ratio=vol_ratio, rsi=rsi, trend_score=trend_score,
            llm_verdict="N/A", llm_reasoning="",
            reason=f"Breakdown below {support:.2f} | Vol {vol_ratio:.1f}x | RSI {rsi:.1f}",
        )

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    def _score_long(self, vol_ratio, rsi, trend_score, ob) -> float:
        score = 0.0
        score += min(vol_ratio / 5.0, 0.35)           # up to 0.35 for volume
        score += min((rsi - 58) / 42.0, 0.25)         # up to 0.25 for RSI
        score += min((trend_score + 1) / 2 * 0.25, 0.25)  # up to 0.25 for trend
        if ob:
            score += min((ob.get("imbalance", 0.5) - 0.5) * 0.3, 0.15)
        return round(min(max(score, 0.0), 1.0), 3)

    def _score_short(self, vol_ratio, rsi, trend_score, ob) -> float:
        score = 0.0
        score += min(vol_ratio / 5.0, 0.35)
        score += min((42 - rsi) / 42.0, 0.25)
        score += min((-trend_score + 1) / 2 * 0.25, 0.25)
        if ob:
            score += min((0.5 - ob.get("imbalance", 0.5)) * 0.3, 0.15)
        return round(min(max(score, 0.0), 1.0), 3)

    # ------------------------------------------------------------------
    # LLM reasoning step (Anthropic API)
    # ------------------------------------------------------------------

    def _apply_llm(self, signal: BMVSignal, fear_greed: dict | None) -> BMVSignal:
        if not self.use_llm:
            return signal

        try:
            fg_value = fear_greed.get("value", 50) if fear_greed else 50
            fg_class = fear_greed.get("classification", "Neutral") if fear_greed else "Neutral"
            fg_delta = fear_greed.get("delta_24h", 0) if fear_greed else 0

            prompt = f"""You are a crypto trading risk analyst. A BMV (Breakout-Momentum-Volume) signal has fired. Evaluate it.

Signal Details:
- Asset: {signal.symbol}
- Direction: {signal.direction}
- Entry: {signal.entry_price}
- Breakout level: {signal.breakout_level}
- Volume ratio: {signal.vol_ratio:.1f}x (threshold: 2.5x)
- RSI: {signal.rsi:.1f}
- Trend score: {signal.trend_score:+.2f} (-1 bearish to +1 bullish)
- Stop loss: {signal.stop_loss}
- Take profit: {signal.take_profit}
- Fear & Greed: {fg_value} ({fg_class}), Δ24h: {fg_delta:+d}

Rules to check:
1. Is the risk/reward ratio acceptable? (TP-Entry)/(Entry-SL) should be >= 1.5
2. Does Fear & Greed sentiment align with the direction?
3. Is RSI dangerously overbought (>80) for LONG or oversold (<20) for SHORT?
4. Any reason this could be a fake breakout?

Respond ONLY with a JSON object, no markdown:
{{"verdict": "CONFIRM" or "FADE" or "WAIT", "reasoning": "one sentence explanation", "risk_reward": <float>}}"""

            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=10,
            )
            resp.raise_for_status()
            raw = resp.json()["content"][0]["text"].strip()
            parsed = json.loads(raw)
            signal.llm_verdict   = parsed.get("verdict", "WAIT")
            signal.llm_reasoning = parsed.get("reasoning", "")
            logger.info(f"LLM verdict for {signal.symbol} {signal.direction}: {signal.llm_verdict} — {signal.llm_reasoning}")

        except Exception as e:
            logger.warning(f"LLM filter failed (using signal as-is): {e}")
            signal.llm_verdict   = "N/A"
            signal.llm_reasoning = "LLM unavailable"

        return signal

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _is_low_volume_session(self) -> bool:
        now_ist = datetime.now(IST)
        return AVOID_HOUR_START <= now_ist.hour < AVOID_HOUR_END

    def _none(self, symbol: str, df: pd.DataFrame, reason: str) -> BMVSignal:
        price = float(df["close"].iloc[-1]) if not df.empty else 0.0
        return BMVSignal(
            symbol=symbol, direction="NONE", confidence=0.0,
            entry_price=price, stop_loss=0.0, take_profit=0.0,
            breakout_level=0.0, vol_ratio=0.0, rsi=0.0, trend_score=0.0,
            llm_verdict="N/A", llm_reasoning="", reason=reason,
        )
