"""
dashboard/app.py
----------------
Reads from SQLite DB directly (when running on GCP VM)
OR from exported JSON snapshot (when running on Streamlit Cloud).

Priority: DB → JSON snapshot → empty state
"""

import os, sqlite3, json, math
from pathlib import Path
from datetime import datetime
import pandas as pd
import streamlit as st

DB_PATH      = os.getenv("DB_PATH", "logs/trades.db")
SNAPSHOT     = "logs/exports/stats_snapshot.json"
CSV_PATH     = "logs/exports/trades_summary.csv"
INITIAL_CAP  = float(os.getenv("INITIAL_CAPITAL", 10000))

st.set_page_config(page_title="Crypto Agent", page_icon="📈", layout="wide")

# ── Data loading — DB preferred, JSON fallback ──────────────────────

@st.cache_data(ttl=60)
def load_from_db():
    if not Path(DB_PATH).exists():
        return None, None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            df = pd.read_sql("SELECT * FROM trades ORDER BY id DESC", conn)
        return df, None
    except Exception as e:
        return None, str(e)


@st.cache_data(ttl=60)
def load_from_snapshot():
    if not Path(SNAPSHOT).exists():
        return None, None
    try:
        with open(SNAPSHOT) as f:
            snap = json.load(f)
        df = pd.read_csv(CSV_PATH) if Path(CSV_PATH).exists() else pd.DataFrame()
        return df, snap
    except Exception as e:
        return None, str(e)


df, snap = load_from_db()
source = "🟢 Live DB"

if df is None:
    df, snap = load_from_snapshot()
    source = "🟡 Snapshot"

if df is None or (isinstance(df, pd.DataFrame) and df.empty and snap is None):
    st.warning("No data yet. Agent hasn't made any trades, or dashboard can't find the DB/snapshot.")
    st.code(f"Looking for DB at: {Path(DB_PATH).resolve()}\nOr snapshot at: {Path(SNAPSHOT).resolve()}")
    st.stop()

# ── Compute stats from DB or use snapshot stats ─────────────────────

def compute_stats(df):
    closed = df[df["status"] == "CLOSED"] if "status" in df.columns else df
    if closed.empty: return {}
    pnls   = closed["pnl_usd"].tolist()
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_w  = sum(wins)/len(wins) if wins else 0
    avg_l  = sum(losses)/len(losses) if losses else 0
    pf     = abs(avg_w/avg_l) if avg_l else 0
    returns = closed["pnl_pct"].tolist()
    avg_r  = sum(returns)/len(returns) if returns else 0
    std_r  = math.sqrt(sum((r-avg_r)**2 for r in returns)/len(returns)) if len(returns)>1 else 0
    sharpe = (avg_r/std_r*(252**0.5)) if std_r else 0
    return {
        "capital": INITIAL_CAP + sum(pnls),
        "total_pnl": sum(pnls),
        "ret_pct": sum(pnls)/INITIAL_CAP*100,
        "win_rate": len(wins)/len(pnls)*100 if pnls else 0,
        "wins": len(wins), "losses": len(losses),
        "profit_factor": pf, "sharpe": sharpe,
        "avg_win": avg_w, "avg_loss": avg_l,
        "best": max(pnls) if pnls else 0,
        "worst": min(pnls) if pnls else 0,
        "total": len(pnls),
    }

s = snap if snap and isinstance(snap, dict) else compute_stats(df)
if not s: s = {}

closed_df = df[df["status"] == "CLOSED"].copy() if "status" in df.columns else df.copy()
open_df   = df[df["status"] == "OPEN"].copy()   if "status" in df.columns else pd.DataFrame()

# ── Header ──────────────────────────────────────────────────────────

st.markdown("## 📈 Crypto Trading Agent")
col_h1, col_h2 = st.columns([3,1])
with col_h1:
    st.caption(f"Paper trading • {source} • Auto-refresh 60s")
with col_h2:
    if snap and "generated_at" in snap:
        st.caption(f"Snapshot: {snap['generated_at'][:16].replace('T',' ')} UTC")

# ── KPIs ─────────────────────────────────────────────────────────────

c1,c2,c3,c4,c5,c6 = st.columns(6)
c1.metric("💰 Capital",       f"${s.get('capital', INITIAL_CAP):,.2f}")
c2.metric("📊 Return",        f"{s.get('ret_pct', s.get('return_pct', 0)):+.2f}%",
                               f"${s.get('total_pnl',0):+.2f}")
c3.metric("🎯 Win Rate",      f"{s.get('win_rate',0):.1f}%",
                               f"{s.get('wins',0)}W / {s.get('losses',0)}L")
c4.metric("⚡ Profit Factor", f"{s.get('profit_factor',0):.2f}")
c5.metric("📐 Sharpe",        f"{s.get('sharpe',0):.2f}")
c6.metric("🔄 Trades",        str(s.get('total_trades', s.get('total', 0))))

st.divider()

# ── Equity curve ─────────────────────────────────────────────────────

st.subheader("Equity Curve")
if snap and "equity_curve" in snap and snap["equity_curve"]:
    eq_df = pd.DataFrame(snap["equity_curve"])
    st.line_chart(eq_df.set_index("time")["equity"])
elif not closed_df.empty:
    cdf = closed_df.sort_values("close_time").copy()
    cdf["equity"] = INITIAL_CAP + cdf["pnl_usd"].cumsum()
    st.line_chart(cdf.set_index("close_time")["equity"])
else:
    st.info("No closed trades yet — equity curve will appear after first trade closes.")

# ── Open positions + Stats ────────────────────────────────────────────

col1, col2 = st.columns([1, 2])

with col1:
    open_count = s.get("open_positions", len(open_df))
    st.subheader(f"Open Positions ({open_count})")

    open_list = snap.get("open_trades", []) if snap else []
    if not open_list and not open_df.empty:
        open_list = open_df.to_dict("records")

    if not open_list:
        st.info("No open positions")
    else:
        for row in open_list:
            with st.container(border=True):
                st.markdown(f"**{row.get('symbol','?')}** `{row.get('direction','?')}`")
                if row.get("entry_price"):
                    st.markdown(f"Entry: `{row['entry_price']}`")
                st.markdown(f"Confidence: `{float(row.get('confidence',0))*100:.0f}%` | LLM: `{row.get('llm_verdict','N/A')}`")
                st.caption(f"Opened: {str(row.get('open_time',''))[:16]}")

with col2:
    st.subheader("Statistics")
    sc1,sc2,sc3,sc4 = st.columns(4)
    sc1.metric("Best Trade",  f"${s.get('best_trade', s.get('best', 0)):+.2f}")
    sc2.metric("Worst Trade", f"${s.get('worst_trade', s.get('worst', 0)):+.2f}")
    sc3.metric("Avg Win",     f"${s.get('avg_win',0):.2f}")
    sc4.metric("Avg Loss",    f"${s.get('avg_loss',0):.2f}")

    # Exit reasons
    exit_reasons = s.get("exit_reasons", {})
    if not exit_reasons and not closed_df.empty:
        exit_reasons = closed_df["exit_reason"].value_counts().to_dict()
    if exit_reasons:
        st.subheader("Exit Reasons")
        er_df = pd.DataFrame(list(exit_reasons.items()), columns=["Reason","Count"])
        st.bar_chart(er_df.set_index("Reason"))

# ── Symbol PnL ───────────────────────────────────────────────────────

sym_pnl = s.get("symbol_pnl", {})
if not sym_pnl and not closed_df.empty:
    sym_pnl = closed_df.groupby("symbol")["pnl_usd"].sum().to_dict()
if sym_pnl:
    st.subheader("PnL by Symbol")
    sp_df = pd.DataFrame(list(sym_pnl.items()), columns=["Symbol","PnL"])
    st.bar_chart(sp_df.set_index("Symbol"))

# ── Trades table ─────────────────────────────────────────────────────

st.subheader("Trade History")
if closed_df.empty:
    st.info("No closed trades yet.")
else:
    cols = [c for c in ["id","symbol","direction","entry_price","exit_price",
            "exit_reason","pnl_usd","pnl_pct","size_usd","confidence",
            "llm_verdict","open_time","close_time"] if c in closed_df.columns]
    st.dataframe(
        closed_df[cols].head(50),
        use_container_width=True,
        column_config={
            "pnl_usd": st.column_config.NumberColumn("PnL $", format="$%.2f"),
            "pnl_pct": st.column_config.NumberColumn("PnL %", format="%.2f%%"),
            "confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1),
        }
    )

# ── Footer ────────────────────────────────────────────────────────────

st.caption(f"Last rendered: {datetime.now().strftime('%H:%M:%S')}")
st.button("🔄 Refresh now", on_click=st.cache_data.clear)
