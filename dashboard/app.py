"""
dashboard/app.py
----------------
Streamlit dashboard. Run with: streamlit run dashboard/app.py
Reads from SQLite trade log — works locally and on Streamlit Community Cloud.
"""

import os
import sqlite3
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = os.getenv("DB_PATH", "logs/trades.db")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", 10000))
REFRESH_SECS = 60

st.set_page_config(
    page_title="Crypto Agent Dashboard",
    page_icon="📈",
    layout="wide",
)

st.markdown("""
<style>
.metric-card { background:#1e1e2e; border-radius:10px; padding:16px; text-align:center; }
.green  { color: #50fa7b; }
.red    { color: #ff5555; }
.yellow { color: #f1fa8c; }
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------
# Data loading
# ------------------------------------------------------------------

@st.cache_data(ttl=REFRESH_SECS)
def load_trades() -> pd.DataFrame:
    if not Path(DB_PATH).exists():
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT * FROM trades ORDER BY id DESC", conn)
    return df


def compute_stats(df: pd.DataFrame) -> dict:
    closed = df[df["status"] == "CLOSED"]
    if closed.empty:
        return {}

    pnls    = closed["pnl_usd"].tolist()
    wins    = [p for p in pnls if p > 0]
    losses  = [p for p in pnls if p <= 0]
    wr      = len(wins) / len(pnls) * 100 if pnls else 0
    avg_w   = sum(wins) / len(wins) if wins else 0
    avg_l   = sum(losses) / len(losses) if losses else 0
    pf      = abs(avg_w / avg_l) if avg_l != 0 else float("inf")

    returns = closed["pnl_pct"].tolist()
    avg_r   = sum(returns) / len(returns) if returns else 0
    std_r   = math.sqrt(sum((r - avg_r) ** 2 for r in returns) / len(returns)) if len(returns) > 1 else 0
    sharpe  = (avg_r / std_r * (252 ** 0.5)) if std_r > 0 else 0

    total_pnl   = sum(pnls)
    total_ret   = total_pnl / INITIAL_CAPITAL * 100
    capital     = INITIAL_CAPITAL + total_pnl

    return {
        "capital":       capital,
        "total_pnl":     total_pnl,
        "total_ret_pct": total_ret,
        "win_rate":      wr,
        "total_trades":  len(pnls),
        "profit_factor": pf,
        "sharpe":        sharpe,
        "avg_win":       avg_w,
        "avg_loss":      avg_l,
        "best":          max(pnls) if pnls else 0,
        "worst":         min(pnls) if pnls else 0,
    }


# ------------------------------------------------------------------
# Layout
# ------------------------------------------------------------------

st.title("📈 Crypto Trading Agent — Paper Dashboard")
st.caption(f"Refreshes every {REFRESH_SECS}s  •  Data: {DB_PATH}")

df = load_trades()

if df.empty:
    st.info("No trades yet. The agent hasn't executed any trades, or the DB path is incorrect.")
    st.code(f"Expected DB at: {Path(DB_PATH).resolve()}")
    st.stop()

stats = compute_stats(df)
open_df   = df[df["status"] == "OPEN"]
closed_df = df[df["status"] == "CLOSED"].copy()

# ------------------------------------------------------------------
# KPI Row
# ------------------------------------------------------------------

col1, col2, col3, col4, col5, col6 = st.columns(6)

def kpi(col, label, value, delta=None, fmt=None):
    with col:
        if fmt == "currency":
            v = f"${value:,.2f}"
        elif fmt == "pct":
            v = f"{value:+.2f}%"
        elif fmt == "ratio":
            v = f"{value:.2f}"
        else:
            v = str(value)
        st.metric(label, v, delta=delta)

kpi(col1, "💰 Capital",       stats.get("capital", INITIAL_CAPITAL),        fmt="currency")
kpi(col2, "📊 Total Return",  stats.get("total_ret_pct", 0),                 fmt="pct")
kpi(col3, "🎯 Win Rate",      f"{stats.get('win_rate', 0):.1f}%")
kpi(col4, "📈 Profit Factor", stats.get("profit_factor", 0),                 fmt="ratio")
kpi(col5, "⚡ Sharpe",        stats.get("sharpe", 0),                        fmt="ratio")
kpi(col6, "🔄 Trades",        stats.get("total_trades", 0))

st.divider()

# ------------------------------------------------------------------
# PnL Curve
# ------------------------------------------------------------------

if not closed_df.empty:
    st.subheader("Cumulative PnL")
    closed_df_sorted = closed_df.sort_values("close_time")
    closed_df_sorted["cumulative_pnl"] = closed_df_sorted["pnl_usd"].cumsum()
    closed_df_sorted["equity"] = INITIAL_CAPITAL + closed_df_sorted["cumulative_pnl"]
    st.line_chart(closed_df_sorted.set_index("close_time")["equity"])

# ------------------------------------------------------------------
# Open Positions
# ------------------------------------------------------------------

st.subheader(f"Open Positions ({len(open_df)})")
if open_df.empty:
    st.info("No open positions.")
else:
    display_open = open_df[["symbol","direction","entry_price","stop_loss",
                             "take_profit","trailing_stop","size_usd","confidence",
                             "llm_verdict","open_time"]].copy()
    st.dataframe(display_open, use_container_width=True)

# ------------------------------------------------------------------
# Recent Trades
# ------------------------------------------------------------------

st.subheader("Recent Closed Trades")
if closed_df.empty:
    st.info("No closed trades yet.")
else:
    display_closed = closed_df[[
        "id","symbol","direction","entry_price","exit_price",
        "exit_reason","pnl_usd","pnl_pct","confidence","llm_verdict",
        "open_time","close_time"
    ]].copy()
    display_closed["pnl_usd"] = display_closed["pnl_usd"].map(lambda x: f"${x:+.2f}")
    display_closed["pnl_pct"] = display_closed["pnl_pct"].map(lambda x: f"{x:+.2f}%")
    st.dataframe(display_closed.head(50), use_container_width=True)

# ------------------------------------------------------------------
# Signal breakdown
# ------------------------------------------------------------------

col_a, col_b = st.columns(2)
with col_a:
    st.subheader("Trades by Symbol")
    if not closed_df.empty:
        by_sym = closed_df.groupby("symbol")["pnl_usd"].sum().reset_index()
        st.bar_chart(by_sym.set_index("symbol"))

with col_b:
    st.subheader("Exit Reasons")
    if not closed_df.empty:
        exit_counts = closed_df["exit_reason"].value_counts().reset_index()
        exit_counts.columns = ["Reason", "Count"]
        st.dataframe(exit_counts, use_container_width=True)

# ------------------------------------------------------------------
# Auto-refresh
# ------------------------------------------------------------------

st.caption(f"Last loaded: {datetime.now().strftime('%H:%M:%S')}")
st.button("🔄 Refresh now", on_click=st.cache_data.clear)
