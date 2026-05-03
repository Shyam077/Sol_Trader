"""
data/features.py
----------------
Computes all technical indicators used by the signal modules.
Input:  raw OHLCV DataFrame from fetcher.py
Output: enriched DataFrame with indicator columns appended
"""

import logging
import numpy as np
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds the following columns to the OHLCV DataFrame:

    Trend
        ema9, ema21, ema50         — exponential moving averages
        ema_cross                  — 1 when ema9 > ema21 (bullish), -1 when below

    Momentum
        rsi                        — RSI(14)
        macd, macd_signal, macd_hist

    Volatility / mean reversion
        bb_upper, bb_mid, bb_lower — Bollinger Bands(20, 2)
        bb_width                   — (upper - lower) / mid  (squeeze detector)
        bb_pct                     — %B: where price sits in the band (0–1)
        atr                        — ATR(14), used for stop-loss sizing

    Volume
        vol_sma20                  — 20-period simple MA of volume
        vol_ratio                  — current volume / vol_sma20
        obv                        — On Balance Volume
        vwap                       — intraday VWAP (resets each day)

    Composite
        trend_score                — -1 to +1 weighted trend signal
        squeeze                    — True when BB width < 20th percentile (last 50 bars)
    """
    if df.empty or len(df) < 50:
        logger.warning("DataFrame too short for feature engineering (need ≥50 bars)")
        return df

    df = df.copy()

    # --- Trend ---
    df["ema9"]  = ta.ema(df["close"], length=9)
    df["ema21"] = ta.ema(df["close"], length=21)
    df["ema50"] = ta.ema(df["close"], length=50)
    df["ema_cross"] = np.where(df["ema9"] > df["ema21"], 1, -1)

    # --- Momentum ---
    df["rsi"] = ta.rsi(df["close"], length=14)

    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        df["macd"]        = macd_df.iloc[:, 0]
        df["macd_signal"] = macd_df.iloc[:, 2]
        df["macd_hist"]   = macd_df.iloc[:, 1]

    # --- Volatility / Mean Reversion ---
    bb_df = ta.bbands(df["close"], length=20, std=2)
    if bb_df is not None and not bb_df.empty:
        df["bb_lower"] = bb_df.iloc[:, 0]
        df["bb_mid"]   = bb_df.iloc[:, 1]
        df["bb_upper"] = bb_df.iloc[:, 2]
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
        df["bb_pct"]   = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])

    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # --- Volume ---
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_sma20"].replace(0, np.nan)
    df["obv"]       = ta.obv(df["close"], df["volume"])

    # VWAP — resets daily
    df["date"] = df["timestamp"].dt.date
    df["vwap"] = (
        df.groupby("date", group_keys=False)
        .apply(_rolling_vwap)
    )
    df.drop(columns=["date"], inplace=True)

    # --- Composite signals ---
    # trend_score: weighted sum of EMA cross, price vs EMA50, MACD direction
    ema50_signal = np.where(df["close"] > df["ema50"], 1, -1)
    macd_signal  = np.where(df.get("macd_hist", pd.Series(0, index=df.index)) > 0, 1, -1)
    df["trend_score"] = (
        0.5 * df["ema_cross"] +
        0.3 * ema50_signal +
        0.2 * macd_signal
    )

    # Squeeze: BB width below its 20th percentile over last 50 bars
    rolling_pct20 = df["bb_width"].rolling(50).quantile(0.20)
    df["squeeze"] = df["bb_width"] < rolling_pct20

    logger.debug(f"Features computed: {len(df)} rows, {len(df.columns)} columns")
    return df


def _rolling_vwap(group: pd.DataFrame) -> pd.Series:
    """Cumulative VWAP within a single trading day group."""
    tp = (group["high"] + group["low"] + group["close"]) / 3
    cum_vol = group["volume"].cumsum()
    cum_tp_vol = (tp * group["volume"]).cumsum()
    return (cum_tp_vol / cum_vol).rename("vwap")


def get_latest_features(df: pd.DataFrame) -> dict:
    """
    Returns a flat dict of the most recent bar's feature values.
    Useful for feeding into the signal engine without passing the full DataFrame.
    """
    if df.empty:
        return {}

    enriched = compute_features(df)
    last = enriched.iloc[-1]

    keys = [
        "close", "volume", "vol_ratio",
        "rsi", "macd", "macd_hist",
        "ema9", "ema21", "ema50", "ema_cross", "trend_score",
        "bb_upper", "bb_mid", "bb_lower", "bb_width", "bb_pct",
        "atr", "obv", "vwap", "squeeze",
    ]
    return {k: last.get(k) for k in keys if k in last.index}


if __name__ == "__main__":
    # Smoke test with synthetic data
    import pandas as pd
    import numpy as np

    np.random.seed(42)
    n = 100
    close = 40000 + np.cumsum(np.random.randn(n) * 200)
    df_test = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"),
        "open":  close - np.abs(np.random.randn(n) * 50),
        "high":  close + np.abs(np.random.randn(n) * 150),
        "low":   close - np.abs(np.random.randn(n) * 150),
        "close": close,
        "volume": np.random.uniform(5, 50, n),
    })

    result = compute_features(df_test)
    print(result[["timestamp", "close", "rsi", "vol_ratio", "bb_width", "squeeze", "trend_score"]].tail(5).to_string())
