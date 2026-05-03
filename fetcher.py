"""
data/fetcher.py
---------------
Fetches OHLCV candles, order book depth, and Fear & Greed sentiment.
All data is normalised into pandas DataFrames before passing downstream.
"""

import os
import logging
import requests
import pandas as pd
import ccxt
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class CoinbaseFetcher:
    """Coinbase Advanced Trade data fetcher via CCXT."""

    TIMEFRAMES = {
        "1m":  "1m",
        "5m":  "5m",
        "15m": "15m",
        "1h":  "1h",
        "4h":  "4h",
        "1d":  "1d",
    }

    def __init__(self):
        sandbox = os.getenv("COINBASE_SANDBOX", "true").lower() == "true"

        self.exchange = ccxt.coinbaseadvanced({
            "apiKey":    os.getenv("COINBASE_API_KEY", ""),
            "secret":    os.getenv("COINBASE_API_SECRET", ""),
            "sandbox":   sandbox,
            "enableRateLimit": True,
            "options":   {"defaultType": "spot"},
        })

        mode = "SANDBOX" if sandbox else "LIVE"
        logger.info(f"CoinbaseFetcher initialised in {mode} mode")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 200,
    ) -> pd.DataFrame:
        """
        Returns DataFrame with columns:
            timestamp, open, high, low, close, volume
        Sorted ascending by timestamp.
        """
        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(
                raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.sort_values("timestamp").reset_index(drop=True)
            logger.debug(f"OHLCV {symbol} {timeframe}: {len(df)} candles")
            return df
        except Exception as e:
            logger.error(f"fetch_ohlcv failed for {symbol}: {e}")
            return pd.DataFrame()

    def fetch_order_book(self, symbol: str, depth: int = 20) -> dict:
        """
        Returns dict with keys:
            bids: list of [price, size]
            asks: list of [price, size]
            bid_ask_spread: float
            bid_depth_usd: total USD value of top-N bids
            ask_depth_usd: total USD value of top-N asks
            imbalance: bid_depth / (bid_depth + ask_depth)  -- >0.5 = buy pressure
        """
        try:
            ob = self.exchange.fetch_order_book(symbol, depth)
            bids = ob["bids"][:depth]
            asks = ob["asks"][:depth]

            bid_depth = sum(p * s for p, s in bids)
            ask_depth = sum(p * s for p, s in asks)
            total = bid_depth + ask_depth

            return {
                "bids": bids,
                "asks": asks,
                "bid_ask_spread": asks[0][0] - bids[0][0] if bids and asks else None,
                "bid_depth_usd": bid_depth,
                "ask_depth_usd": ask_depth,
                "imbalance": bid_depth / total if total > 0 else 0.5,
            }
        except Exception as e:
            logger.error(f"fetch_order_book failed for {symbol}: {e}")
            return {}

    def fetch_ticker(self, symbol: str) -> dict:
        """Returns latest ticker: last price, 24h change %, 24h volume."""
        try:
            t = self.exchange.fetch_ticker(symbol)
            return {
                "symbol":       symbol,
                "price":        t["last"],
                "change_pct":   t["percentage"],
                "volume_24h":   t["quoteVolume"],
                "high_24h":     t["high"],
                "low_24h":      t["low"],
                "timestamp":    datetime.now(timezone.utc),
            }
        except Exception as e:
            logger.error(f"fetch_ticker failed for {symbol}: {e}")
            return {}

    def fetch_multi_ohlcv(
        self,
        symbols: list[str],
        timeframe: str = "15m",
        limit: int = 200,
    ) -> dict[str, pd.DataFrame]:
        """Fetches OHLCV for multiple symbols. Returns {symbol: df}."""
        return {s: self.fetch_ohlcv(s, timeframe, limit) for s in symbols}


class SentimentFetcher:
    """Fetches Fear & Greed index from alternative.me (free, no key needed)."""

    URL = "https://api.alternative.me/fng/?limit=2&format=json"

    def fetch_fear_greed(self) -> dict:
        """
        Returns dict with:
            value: int 0-100 (0=extreme fear, 100=extreme greed)
            classification: str
            delta_24h: change vs yesterday (positive = rising greed)
            timestamp: datetime
        """
        try:
            resp = requests.get(self.URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()["data"]

            today = data[0]
            yesterday = data[1] if len(data) > 1 else today

            value_today = int(today["value"])
            value_yesterday = int(yesterday["value"])

            return {
                "value":          value_today,
                "classification": today["value_classification"],
                "delta_24h":      value_today - value_yesterday,
                "timestamp":      datetime.now(timezone.utc),
            }
        except Exception as e:
            logger.error(f"fetch_fear_greed failed: {e}")
            return {"value": 50, "classification": "Neutral", "delta_24h": 0, "timestamp": datetime.now(timezone.utc)}


class DataPipeline:
    """
    Top-level pipeline that aggregates all data sources
    into a single snapshot dict for the signal engine.
    """

    DEFAULT_PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    def __init__(self, pairs: list[str] | None = None):
        env_pairs = os.getenv("TRADE_PAIRS", "")
        if pairs:
            self.pairs = pairs
        elif env_pairs:
            # Convert BTC-USD → BTC/USDT format for CCXT
            self.pairs = [p.replace("-", "/").replace("USD", "USDT") for p in env_pairs.split(",")]
        else:
            self.pairs = self.DEFAULT_PAIRS

        self.coinbase = CoinbaseFetcher()
        self.sentiment = SentimentFetcher()
        logger.info(f"DataPipeline ready for pairs: {self.pairs}")

    def snapshot(self, timeframe: str = "15m") -> dict:
        """
        Returns full market snapshot:
        {
            "ohlcv":       {symbol: DataFrame},
            "order_books": {symbol: dict},
            "tickers":     {symbol: dict},
            "fear_greed":  dict,
            "timestamp":   datetime,
        }
        """
        logger.info(f"Taking market snapshot [{timeframe}] for {self.pairs}")

        ohlcv       = self.coinbase.fetch_multi_ohlcv(self.pairs, timeframe, limit=200)
        order_books = {s: self.coinbase.fetch_order_book(s) for s in self.pairs}
        tickers     = {s: self.coinbase.fetch_ticker(s) for s in self.pairs}
        fear_greed  = self.sentiment.fetch_fear_greed()

        return {
            "ohlcv":       ohlcv,
            "order_books": order_books,
            "tickers":     tickers,
            "fear_greed":  fear_greed,
            "timestamp":   datetime.now(timezone.utc),
        }


if __name__ == "__main__":
    # Quick smoke test — run: python -m data.fetcher
    logging.basicConfig(level=logging.INFO)
    pipeline = DataPipeline(pairs=["BTC/USDT"])
    snap = pipeline.snapshot("15m")

    print("\n--- OHLCV (last 3 candles) ---")
    print(snap["ohlcv"]["BTC/USDT"].tail(3).to_string())

    print("\n--- Order Book Summary ---")
    ob = snap["order_books"]["BTC/USDT"]
    print(f"  Spread:     ${ob.get('bid_ask_spread', 'N/A'):.2f}")
    print(f"  Imbalance:  {ob.get('imbalance', 0):.3f} (>0.5 = buy pressure)")

    print("\n--- Fear & Greed ---")
    fg = snap["fear_greed"]
    print(f"  Value: {fg['value']} ({fg['classification']}) | Δ24h: {fg['delta_24h']:+d}")
