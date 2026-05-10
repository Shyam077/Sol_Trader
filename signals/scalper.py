"""
signals/scalper.py
------------------
High-frequency scalping module. Runs on 1m and 3m timeframes.
4 strategies: EMA cross, RSI reversal, VWAP bounce, micro breakout.
Designed for 20-50 trades/day with tight stops and fast exits.
"""

import os
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from data.features import compute_features

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class ScalpSignal:
    symbol:       str
    direction:    str        # LONG | SHORT | NONE
    strategy:     str        # EMA_CROSS | RSI_REVERSAL | VWAP_BOUNCE | MICRO_BREAKOUT
    confidence:   float
    entry_price:  float
    stop_loss:    float
    take_profit:  float
    rr_ratio:     float
    reason:       str
    timeframe:    str
    timestamp:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "symbol":      self.symbol,
            "direction":   self.direction,
            "strategy":    self.strategy,
            "confidence":  round(self.confidence, 3),
            "entry_price": self.entry_price,
            "stop_loss":   self.stop_loss,
            "take_profit": self.take_profit,
            "rr_ratio":    round(self.rr_ratio, 2),
            "reason":      self.reason,
            "timeframe":   self.timeframe,
            "timestamp":   self.timestamp.isoformat(),
            "llm_verdict": "N/A",
            "llm_reasoning": "",
        }

    @property
    def is_actionable(self) -> bool:
        return self.direction != "NONE" and self.confidence >= 0.50 and self.rr_ratio >= 1.2


class ScalpingEngine:
    """
    Runs 4 scalping strategies on 1m and 3m data.
    Returns best signal per symbol per cycle.
    """

    def __init__(self):
        # Tight stops for scalping — ATR multipliers
        self.sl_atr  = float(os.getenv("SCALP_SL_ATR", 0.8))
        self.tp_atr  = float(os.getenv("SCALP_TP_ATR", 1.6))
        self.min_vol = float(os.getenv("SCALP_MIN_VOL", 1.8))  # lower than BMV
        logger.info("ScalpingEngine ready | Strategies: EMA_CROSS, RSI_REVERSAL, VWAP_BOUNCE, MICRO_BREAKOUT")

    def scan(self, symbol: str, df_1m: pd.DataFrame, df_3m: pd.DataFrame,
             order_book: dict | None = None) -> list[ScalpSignal]:
        """Returns list of actionable signals across all strategies."""
        signals = []

        for df, tf in [(df_1m, "1m"), (df_3m, "3m")]:
            if df is None or df.empty or len(df) < 30:
                continue
            enriched = compute_features(df)
            if enriched.empty:
                continue

            last = enriched.iloc[-1]
            prev = enriched.iloc[-2]

            atr   = float(last.get("atr", last["close"] * 0.002) or last["close"] * 0.002)
            close = float(last["close"])

            # Run each strategy
            for strategy_fn in [
                self._ema_cross,
                self._rsi_reversal,
                self._vwap_bounce,
                self._micro_breakout,
            ]:
                sig = strategy_fn(symbol, enriched, last, prev, atr, close, tf, order_book)
                if sig and sig.is_actionable:
                    signals.append(sig)

        # Return highest confidence signal per direction
        return signals

    # ------------------------------------------------------------------
    # Strategy 1: EMA Cross (9/21 on 1m)
    # ------------------------------------------------------------------
    def _ema_cross(self, symbol, df, last, prev, atr, close, tf, ob) -> ScalpSignal | None:
        ema9  = last.get("ema9")
        ema21 = last.get("ema21")
        prev_ema9  = prev.get("ema9")
        prev_ema21 = prev.get("ema21")
        vol_ratio  = float(last.get("vol_ratio", 0) or 0)
        rsi        = float(last.get("rsi", 50) or 50)

        if any(v is None for v in [ema9, ema21, prev_ema9, prev_ema21]):
            return None

        # Bullish cross: ema9 crosses above ema21
        if prev_ema9 <= prev_ema21 and ema9 > ema21:
            if vol_ratio < self.min_vol or rsi > 75:
                return None
            conf = min(0.5 + (vol_ratio - 1.8) * 0.1 + (rsi - 50) * 0.003, 0.90)
            sl   = round(close - self.sl_atr * atr, 4)
            tp   = round(close + self.tp_atr * atr, 4)
            return ScalpSignal(
                symbol=symbol, direction="LONG", strategy="EMA_CROSS",
                confidence=conf, entry_price=close, stop_loss=sl, take_profit=tp,
                rr_ratio=self.tp_atr/self.sl_atr, timeframe=tf,
                reason=f"EMA9 crossed above EMA21 | Vol {vol_ratio:.1f}x | RSI {rsi:.0f}",
            )

        # Bearish cross
        if prev_ema9 >= prev_ema21 and ema9 < ema21:
            if vol_ratio < self.min_vol or rsi < 25:
                return None
            conf = min(0.5 + (vol_ratio - 1.8) * 0.1 + (50 - rsi) * 0.003, 0.90)
            sl   = round(close + self.sl_atr * atr, 4)
            tp   = round(close - self.tp_atr * atr, 4)
            return ScalpSignal(
                symbol=symbol, direction="SHORT", strategy="EMA_CROSS",
                confidence=conf, entry_price=close, stop_loss=sl, take_profit=tp,
                rr_ratio=self.tp_atr/self.sl_atr, timeframe=tf,
                reason=f"EMA9 crossed below EMA21 | Vol {vol_ratio:.1f}x | RSI {rsi:.0f}",
            )
        return None

    # ------------------------------------------------------------------
    # Strategy 2: RSI Reversal (mean reversion)
    # ------------------------------------------------------------------
    def _rsi_reversal(self, symbol, df, last, prev, atr, close, tf, ob) -> ScalpSignal | None:
        rsi      = float(last.get("rsi", 50) or 50)
        prev_rsi = float(prev.get("rsi", 50) or 50)
        bb_pct   = float(last.get("bb_pct", 0.5) or 0.5)
        vol_ratio = float(last.get("vol_ratio", 0) or 0)

        # Oversold bounce: RSI was below 25, now turning up
        if prev_rsi < 25 and rsi > prev_rsi and bb_pct < 0.2:
            if vol_ratio < 1.5:
                return None
            conf = min(0.55 + (25 - prev_rsi) * 0.01 + vol_ratio * 0.05, 0.88)
            sl   = round(close - self.sl_atr * atr, 4)
            tp   = round(close + self.tp_atr * atr, 4)
            return ScalpSignal(
                symbol=symbol, direction="LONG", strategy="RSI_REVERSAL",
                confidence=conf, entry_price=close, stop_loss=sl, take_profit=tp,
                rr_ratio=self.tp_atr/self.sl_atr, timeframe=tf,
                reason=f"RSI reversal from oversold {prev_rsi:.0f}→{rsi:.0f} | BB% {bb_pct:.2f}",
            )

        # Overbought reversal: RSI was above 75, now turning down
        if prev_rsi > 75 and rsi < prev_rsi and bb_pct > 0.8:
            if vol_ratio < 1.5:
                return None
            conf = min(0.55 + (prev_rsi - 75) * 0.01 + vol_ratio * 0.05, 0.88)
            sl   = round(close + self.sl_atr * atr, 4)
            tp   = round(close - self.tp_atr * atr, 4)
            return ScalpSignal(
                symbol=symbol, direction="SHORT", strategy="RSI_REVERSAL",
                confidence=conf, entry_price=close, stop_loss=sl, take_profit=tp,
                rr_ratio=self.tp_atr/self.sl_atr, timeframe=tf,
                reason=f"RSI reversal from overbought {prev_rsi:.0f}→{rsi:.0f} | BB% {bb_pct:.2f}",
            )
        return None

    # ------------------------------------------------------------------
    # Strategy 3: VWAP Bounce
    # ------------------------------------------------------------------
    def _vwap_bounce(self, symbol, df, last, prev, atr, close, tf, ob) -> ScalpSignal | None:
        vwap      = float(last.get("vwap", 0) or 0)
        prev_close = float(prev["close"])
        vol_ratio  = float(last.get("vol_ratio", 0) or 0)
        rsi        = float(last.get("rsi", 50) or 50)

        if vwap == 0:
            return None

        vwap_dist_pct = (close - vwap) / vwap * 100

        # Price reclaims VWAP from below (bullish)
        if prev_close < vwap and close > vwap and vol_ratio >= 1.8 and rsi > 45:
            conf = min(0.55 + vol_ratio * 0.05 + (rsi - 45) * 0.003, 0.87)
            sl   = round(vwap - atr * self.sl_atr, 4)
            tp   = round(close + atr * self.tp_atr, 4)
            return ScalpSignal(
                symbol=symbol, direction="LONG", strategy="VWAP_BOUNCE",
                confidence=conf, entry_price=close, stop_loss=sl, take_profit=tp,
                rr_ratio=abs(tp - close) / abs(close - sl) if abs(close - sl) > 0 else 0,
                timeframe=tf,
                reason=f"Price reclaimed VWAP {vwap:.2f} | Vol {vol_ratio:.1f}x | RSI {rsi:.0f}",
            )

        # Price loses VWAP from above (bearish)
        if prev_close > vwap and close < vwap and vol_ratio >= 1.8 and rsi < 55:
            conf = min(0.55 + vol_ratio * 0.05 + (55 - rsi) * 0.003, 0.87)
            sl   = round(vwap + atr * self.sl_atr, 4)
            tp   = round(close - atr * self.tp_atr, 4)
            return ScalpSignal(
                symbol=symbol, direction="SHORT", strategy="VWAP_BOUNCE",
                confidence=conf, entry_price=close, stop_loss=sl, take_profit=tp,
                rr_ratio=abs(close - tp) / abs(sl - close) if abs(sl - close) > 0 else 0,
                timeframe=tf,
                reason=f"Price lost VWAP {vwap:.2f} | Vol {vol_ratio:.1f}x | RSI {rsi:.0f}",
            )
        return None

    # ------------------------------------------------------------------
    # Strategy 4: Micro Breakout (BMV adapted for 1m/3m)
    # ------------------------------------------------------------------
    def _micro_breakout(self, symbol, df, last, prev, atr, close, tf, ob) -> ScalpSignal | None:
        lookback = 10  # shorter lookback for scalping
        if len(df) < lookback + 2:
            return None

        resistance = float(df["high"].iloc[-(lookback + 1):-1].max())
        support    = float(df["low"].iloc[-(lookback + 1):-1].min())
        vol_ratio  = float(last.get("vol_ratio", 0) or 0)
        rsi        = float(last.get("rsi", 50) or 50)
        prev_close = float(prev["close"])

        # Micro breakout long
        if close > resistance and prev_close <= resistance:
            if vol_ratio < self.min_vol or rsi < 52:
                return None
            conf = min(0.52 + (vol_ratio - 1.8) * 0.08 + (rsi - 52) * 0.004, 0.88)
            sl   = round(resistance - atr * self.sl_atr, 4)
            tp   = round(close + atr * self.tp_atr, 4)
            return ScalpSignal(
                symbol=symbol, direction="LONG", strategy="MICRO_BREAKOUT",
                confidence=conf, entry_price=close, stop_loss=sl, take_profit=tp,
                rr_ratio=self.tp_atr/self.sl_atr, timeframe=tf,
                reason=f"Micro breakout above {resistance:.2f} | Vol {vol_ratio:.1f}x | RSI {rsi:.0f}",
            )

        # Micro breakdown short
        if close < support and prev_close >= support:
            if vol_ratio < self.min_vol or rsi > 48:
                return None
            conf = min(0.52 + (vol_ratio - 1.8) * 0.08 + (48 - rsi) * 0.004, 0.88)
            sl   = round(support + atr * self.sl_atr, 4)
            tp   = round(close - atr * self.tp_atr, 4)
            return ScalpSignal(
                symbol=symbol, direction="SHORT", strategy="MICRO_BREAKOUT",
                confidence=conf, entry_price=close, stop_loss=sl, take_profit=tp,
                rr_ratio=self.tp_atr/self.sl_atr, timeframe=tf,
                reason=f"Micro breakdown below {support:.2f} | Vol {vol_ratio:.1f}x | RSI {rsi:.0f}",
            )
        return None
