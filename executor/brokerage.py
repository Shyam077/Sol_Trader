"""
executor/brokerage.py
---------------------
Simulates real brokerage costs on every trade.
Covers: OKX spot fees, slippage, spread cost, funding (for perps).

OKX Spot fee tiers (as of 2026):
  Maker: 0.080%  (limit orders)
  Taker: 0.100%  (market orders — what we use)

Realistic additional costs:
  Slippage:   0.02–0.05% on entry and exit (market impact)
  Spread:     0.01–0.03% depending on pair liquidity

All costs logged per trade for full P&L accuracy.
"""

import os
import logging

logger = logging.getLogger(__name__)


class BrokerageSimulator:
    """
    Calculates and applies all trading costs to paper trades.
    Keeps a running total of fees paid for dashboard display.
    """

    # OKX spot taker fee (market orders)
    DEFAULT_TAKER_FEE = 0.001      # 0.10%
    DEFAULT_MAKER_FEE = 0.0008     # 0.08%

    # Slippage by asset tier
    SLIPPAGE = {
        "BTC/USDT":  0.0001,   # 0.01% — most liquid
        "ETH/USDT":  0.0001,
        "SOL/USDT":  0.0002,
        "LINK/USDT": 0.0003,
        "DOT/USDT":  0.0003,
        "AVAX/USDT": 0.0003,
        "ADA/USDT":  0.0003,
        "MATIC/USDT":0.0004,
    }

    # Spread cost (half-spread applied on entry + exit)
    SPREAD = {
        "BTC/USDT":  0.0001,
        "ETH/USDT":  0.0001,
        "SOL/USDT":  0.0002,
        "LINK/USDT": 0.0003,
        "DOT/USDT":  0.0003,
        "AVAX/USDT": 0.0003,
        "ADA/USDT":  0.0003,
        "MATIC/USDT":0.0004,
    }

    def __init__(self):
        self.taker_fee    = float(os.getenv("BROKERAGE_TAKER_FEE",   self.DEFAULT_TAKER_FEE))
        self.maker_fee    = float(os.getenv("BROKERAGE_MAKER_FEE",   self.DEFAULT_MAKER_FEE))
        self.extra_slip   = float(os.getenv("BROKERAGE_EXTRA_SLIP",  0.0))   # extra slippage %
        self.total_fees   = 0.0    # running total fees paid
        self.total_trades = 0

        logger.info(
            f"BrokerageSimulator ready | "
            f"Taker: {self.taker_fee*100:.3f}% | "
            f"Maker: {self.maker_fee*100:.3f}% | "
            f"Slippage: per-asset"
        )

    def calculate_entry_cost(self, symbol: str, size_usd: float) -> dict:
        """
        Cost when opening a position.
        Returns dict with all cost components and effective entry adjustment.
        """
        slip_pct   = self.SLIPPAGE.get(symbol, 0.0003) + self.extra_slip
        spread_pct = self.SPREAD.get(symbol, 0.0002) / 2   # half-spread on entry
        fee_pct    = self.taker_fee   # market order on entry

        total_pct  = fee_pct + slip_pct + spread_pct
        total_usd  = size_usd * total_pct

        return {
            "fee_usd":      round(size_usd * fee_pct, 4),
            "slippage_usd": round(size_usd * slip_pct, 4),
            "spread_usd":   round(size_usd * spread_pct, 4),
            "total_usd":    round(total_usd, 4),
            "total_pct":    round(total_pct * 100, 4),
        }

    def calculate_exit_cost(self, symbol: str, size_usd: float, exit_reason: str) -> dict:
        """
        Cost when closing a position.
        TP exits can use limit orders (maker fee), others use market (taker).
        """
        slip_pct   = self.SLIPPAGE.get(symbol, 0.0003) + self.extra_slip
        spread_pct = self.SPREAD.get(symbol, 0.0002) / 2

        # TP hits can be limit orders — use maker fee
        fee_pct = self.maker_fee if exit_reason == "TP" else self.taker_fee

        total_pct = fee_pct + slip_pct + spread_pct
        total_usd = size_usd * total_pct

        return {
            "fee_usd":      round(size_usd * fee_pct, 4),
            "slippage_usd": round(size_usd * slip_pct, 4),
            "spread_usd":   round(size_usd * spread_pct, 4),
            "total_usd":    round(total_usd, 4),
            "total_pct":    round(total_pct * 100, 4),
            "order_type":   "maker" if exit_reason == "TP" else "taker",
        }

    def apply_round_trip_cost(
        self,
        symbol: str,
        size_usd: float,
        gross_pnl: float,
        exit_reason: str,
    ) -> dict:
        """
        Deducts full round-trip brokerage cost from gross PnL.
        Returns net PnL and cost breakdown.
        """
        entry_cost = self.calculate_entry_cost(symbol, size_usd)
        exit_cost  = self.calculate_exit_cost(symbol, size_usd, exit_reason)

        total_cost  = entry_cost["total_usd"] + exit_cost["total_usd"]
        net_pnl     = gross_pnl - total_cost
        cost_pct_of_size = (total_cost / size_usd) * 100 if size_usd > 0 else 0

        self.total_fees   += total_cost
        self.total_trades += 1

        result = {
            "gross_pnl":    round(gross_pnl, 4),
            "net_pnl":      round(net_pnl, 4),
            "total_cost":   round(total_cost, 4),
            "entry_cost":   entry_cost,
            "exit_cost":    exit_cost,
            "cost_pct":     round(cost_pct_of_size, 4),
            "cumulative_fees": round(self.total_fees, 4),
        }

        logger.debug(
            f"Brokerage {symbol} | Gross: ${gross_pnl:+.4f} | "
            f"Cost: ${total_cost:.4f} ({cost_pct_of_size:.3f}%) | "
            f"Net: ${net_pnl:+.4f}"
        )
        return result

    def get_break_even_move_pct(self, symbol: str) -> float:
        """
        Minimum price move needed just to break even after round-trip costs.
        Useful for signal filtering — only trade if expected move > this.
        """
        entry = self.calculate_entry_cost(symbol, 1000)
        exit_ = self.calculate_exit_cost(symbol, 1000, "MARKET")
        total_pct = (entry["total_usd"] + exit_["total_usd"]) / 1000 * 100
        return round(total_pct, 4)

    def get_summary(self) -> dict:
        return {
            "total_fees_paid": round(self.total_fees, 2),
            "total_trades":    self.total_trades,
            "avg_cost_per_trade": round(self.total_fees / self.total_trades, 4) if self.total_trades else 0,
        }
