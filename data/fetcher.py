"""
data/fetcher.py - Uses Bybit public API, zero API key needed
"""
import logging, requests, pandas as pd, ccxt, os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class CoinbaseFetcher:
    def __init__(self):
        self.exchange = ccxt.bybit({
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        logger.info("MarketFetcher ready | Bybit public API (no key needed)")

    def fetch_ohlcv(self, symbol, timeframe="15m", limit=200):
        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df.sort_values("timestamp").reset_index(drop=True)
        except Exception as e:
            logger.error(f"fetch_ohlcv failed for {symbol}: {e}")
            return pd.DataFrame()

    def fetch_order_book(self, symbol, depth=20):
        try:
            ob = self.exchange.fetch_order_book(symbol, depth)
            bids, asks = ob["bids"][:depth], ob["asks"][:depth]
            bid_depth = sum(p*s for p,s in bids)
            ask_depth = sum(p*s for p,s in asks)
            total = bid_depth + ask_depth
            return {"bids": bids, "asks": asks,
                    "bid_ask_spread": asks[0][0]-bids[0][0] if bids and asks else None,
                    "bid_depth_usd": bid_depth, "ask_depth_usd": ask_depth,
                    "imbalance": bid_depth/total if total > 0 else 0.5}
        except Exception as e:
            logger.error(f"fetch_order_book failed for {symbol}: {e}")
            return {}

    def fetch_ticker(self, symbol):
        try:
            t = self.exchange.fetch_ticker(symbol)
            return {"symbol": symbol, "price": t["last"], "change_pct": t["percentage"],
                    "volume_24h": t["quoteVolume"], "high_24h": t["high"], "low_24h": t["low"],
                    "timestamp": datetime.now(timezone.utc)}
        except Exception as e:
            logger.error(f"fetch_ticker failed for {symbol}: {e}")
            return {}

    def fetch_multi_ohlcv(self, symbols, timeframe="15m", limit=200):
        return {s: self.fetch_ohlcv(s, timeframe, limit) for s in symbols}

class SentimentFetcher:
    URL = "https://api.alternative.me/fng/?limit=2&format=json"
    def fetch_fear_greed(self):
        try:
            data = requests.get(self.URL, timeout=10).json()["data"]
            v_today, v_yesterday = int(data[0]["value"]), int(data[1]["value"] if len(data)>1 else data[0]["value"])
            return {"value": v_today, "classification": data[0]["value_classification"],
                    "delta_24h": v_today-v_yesterday, "timestamp": datetime.now(timezone.utc)}
        except Exception as e:
            logger.error(f"fetch_fear_greed failed: {e}")
            return {"value": 50, "classification": "Neutral", "delta_24h": 0, "timestamp": datetime.now(timezone.utc)}

class DataPipeline:
    DEFAULT_PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    def __init__(self, pairs=None):
        env_pairs = os.getenv("TRADE_PAIRS", "")
        if pairs:
            self.pairs = pairs
        elif env_pairs:
            self.pairs = [p.strip().replace("-","/").replace("/USD","/USDT") for p in env_pairs.split(",")]
        else:
            self.pairs = self.DEFAULT_PAIRS
        self.coinbase = CoinbaseFetcher()
        self.sentiment = SentimentFetcher()
        logger.info(f"DataPipeline ready for pairs: {self.pairs}")

    def snapshot(self, timeframe="15m"):
        logger.info(f"Taking market snapshot [{timeframe}] for {self.pairs}")
        return {
            "ohlcv":       self.coinbase.fetch_multi_ohlcv(self.pairs, timeframe, limit=200),
            "order_books": {s: self.coinbase.fetch_order_book(s) for s in self.pairs},
            "tickers":     {s: self.coinbase.fetch_ticker(s) for s in self.pairs},
            "fear_greed":  self.sentiment.fetch_fear_greed(),
            "timestamp":   datetime.now(timezone.utc),
        }
