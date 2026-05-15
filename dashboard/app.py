"""
dashboard/app.py — Shyam's Trading Terminal
Professional dark terminal with brokerage cost tracking
"""

import os, sqlite3, math
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import streamlit as st

DB_PATH     = os.getenv("DB_PATH", "logs/trades.db")
INITIAL_CAP = float(os.getenv("INITIAL_CAPITAL", 10000))

st.set_page_config(
    page_title="Shyam's Trading Terminal",
    page_icon="⚡", layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body,.stApp{background:#050508!important;color:#e2e8f0!important;font-family:'IBM Plex Sans',sans-serif!important}
#MainMenu,footer,header{visibility:hidden}
.block-container{padding:0!important;max-width:100%!important}
section[data-testid="stSidebar"]{display:none}
.stApp::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(0,255,136,.02) 1px,transparent 1px),linear-gradient(90deg,rgba(0,255,136,.02) 1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0}
.hdr{display:flex;align-items:center;justify-content:space-between;padding:14px 28px;border-bottom:1px solid rgba(0,255,136,.12);background:rgba(5,5,8,.97);position:sticky;top:0;z-index:100;backdrop-filter:blur(12px)}
.logo{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;color:#00ff88;letter-spacing:3px;text-transform:uppercase}
.logo span{color:#fff;opacity:.3}
.live{display:flex;align-items:center;gap:8px;font-family:'IBM Plex Mono',monospace;font-size:10px;color:#00ff88;letter-spacing:2px}
.dot{width:7px;height:7px;background:#00ff88;border-radius:50%;animation:p 2s infinite;box-shadow:0 0 8px #00ff88}
@keyframes p{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.85)}}
.kpi-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;background:rgba(0,255,136,.06);border-bottom:1px solid rgba(0,255,136,.08)}
.kpi{background:#050508;padding:18px 20px;position:relative;overflow:hidden;transition:background .2s}
.kpi:hover{background:rgba(0,255,136,.03)}
.kpi::after{content:'';position:absolute;bottom:0;left:0;right:0;height:2px;background:var(--a,#00ff88);transform:scaleX(0);transform-origin:left;transition:transform .3s}
.kpi:hover::after{transform:scaleX(1)}
.kl{font-family:'IBM Plex Mono',monospace;font-size:9px;color:rgba(226,232,240,.25);text-transform:uppercase;letter-spacing:2px;margin-bottom:8px}
.kv{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:var(--a,#e2e8f0);line-height:1}
.ks{font-size:10px;color:rgba(226,232,240,.3);margin-top:5px;font-family:'IBM Plex Mono',monospace}
.pt{font-family:'IBM Plex Mono',monospace;font-size:9px;color:rgba(226,232,240,.22);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.pt::before{content:'';width:3px;height:10px;background:#00ff88;border-radius:2px}
.badge{display:inline-block;padding:2px 7px;border-radius:2px;font-size:9px;font-weight:600;letter-spacing:1px;font-family:'IBM Plex Mono',monospace}
.bl{background:rgba(0,255,136,.1);color:#00ff88}
.bs{background:rgba(255,68,102,.1);color:#ff4466}
.btp{background:rgba(0,255,136,.08);color:#00cc66}
.bsl{background:rgba(255,68,102,.08);color:#ff4466}
.btr{background:rgba(68,136,255,.1);color:#4488ff}
.bpr{background:rgba(255,170,0,.1);color:#ffaa00}
.btm{background:rgba(160,100,255,.1);color:#a064ff}
.tt{width:100%;border-collapse:collapse;font-size:11px}
.tt th{font-family:'IBM Plex Mono',monospace;font-size:8px;color:rgba(226,232,240,.2);text-transform:uppercase;letter-spacing:1.5px;padding:7px 10px;text-align:left;border-bottom:1px solid rgba(255,255,255,.04)}
.tt td{padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.03);font-family:'IBM Plex Mono',monospace;font-size:11px;color:rgba(226,232,240,.65)}
.tt tr:hover td{background:rgba(0,255,136,.03);color:#e2e8f0}
.pos-card{background:rgba(0,255,136,.03);border:1px solid rgba(0,255,136,.1);border-radius:4px;padding:12px;margin-bottom:8px}
.pr{display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:10px;color:rgba(226,232,240,.35);padding:3px 0}
.pr span:last-child{color:rgba(226,232,240,.75)}
.brok-card{background:rgba(255,68,102,.03);border:1px solid rgba(255,68,102,.1);border-radius:4px;padding:14px;margin-bottom:10px}
::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-thumb{background:rgba(0,255,136,.2);border-radius:2px}
div[data-testid="stButton"] button{background:rgba(0,255,136,.08)!important;border:1px solid rgba(0,255,136,.2)!important;color:#00ff88!important;font-family:'IBM Plex Mono',monospace!important;font-size:10px!important;letter-spacing:1.5px!important;text-transform:uppercase!important;border-radius:2px!important}
div[data-testid="stButton"] button:hover{background:rgba(0,255,136,.15)!important}
div[data-testid="stSelectbox"]>div{background:rgba(255,255,255,.03)!important;border-color:rgba(255,255,255,.08)!important}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=30)
def load_data():
    if not Path(DB_PATH).exists():
        return pd.DataFrame(), {}
    try:
        with sqlite3.connect(DB_PATH) as conn:
            df = pd.read_sql("SELECT * FROM trades ORDER BY id DESC", conn)
        if df.empty: return df, {}
        closed = df[df["status"]=="CLOSED"]
        if closed.empty: return df, {}

        pnls   = closed["pnl_usd"].tolist()
        gross  = closed["gross_pnl"].tolist() if "gross_pnl" in closed.columns else pnls
        brok   = closed["brokerage_cost"].tolist() if "brokerage_cost" in closed.columns else [0]*len(pnls)
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        avg_w  = sum(wins)/len(wins) if wins else 0
        avg_l  = sum(losses)/len(losses) if losses else 0
        pf     = abs(avg_w/avg_l) if avg_l else 0
        returns = closed["pnl_pct"].tolist()
        avg_r  = sum(returns)/len(returns) if returns else 0
        std_r  = math.sqrt(sum((r-avg_r)**2 for r in returns)/len(returns)) if len(returns)>1 else 0
        sharpe = (avg_r/std_r*(252**0.5)) if std_r else 0

        return df, {
            "capital":         INITIAL_CAP + sum(pnls),
            "total_pnl":       round(sum(pnls),2),
            "total_gross":     round(sum(gross),2),
            "total_brokerage": round(sum(brok),2),
            "ret_pct":         sum(pnls)/INITIAL_CAP*100,
            "win_rate":        len(wins)/len(pnls)*100 if pnls else 0,
            "wins":            len(wins), "losses": len(losses),
            "profit_factor":   pf, "sharpe": sharpe,
            "avg_win":         avg_w, "avg_loss": avg_l,
            "best":            max(pnls) if pnls else 0,
            "worst":           min(pnls) if pnls else 0,
            "total":           len(pnls),
        }
    except Exception as e:
        return pd.DataFrame(), {}


df, stats = load_data()
open_df   = df[df["status"]=="OPEN"]   if not df.empty else pd.DataFrame()
closed_df = df[df["status"]=="CLOSED"] if not df.empty else pd.DataFrame()
now_utc   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
capital   = stats.get("capital", INITIAL_CAP)
ret_pct   = stats.get("ret_pct", 0)
total_pnl = stats.get("total_pnl", 0)
total_brok= stats.get("total_brokerage", 0)


# ── Header ───────────────────────────────────────────────────────────
st.markdown(f"""
<div class="hdr">
  <div class="logo">⚡ SHYAM <span>/</span> TRADING TERMINAL</div>
  <div style="display:flex;gap:28px;align-items:center">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:rgba(226,232,240,.25);letter-spacing:1px">{now_utc}</div>
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:rgba(226,232,240,.25)">PAPER · OKX · 5 PAIRS · BMV+SCALP</div>
    <div class="live"><div class="dot"></div>LIVE</div>
  </div>
</div>
""", unsafe_allow_html=True)


# ── KPI Row (7 cards including brokerage) ────────────────────────────
rc  = "#00ff88" if ret_pct >= 0 else "#ff4466"
wrc = "#00ff88" if stats.get("win_rate",0) >= 50 else "#ffaa00" if stats.get("win_rate",0) >= 40 else "#ff4466"
pfc = "#00ff88" if stats.get("profit_factor",0) >= 1.5 else "#ffaa00" if stats.get("profit_factor",0) >= 1 else "#ff4466"
shc = "#00ff88" if stats.get("sharpe",0) >= 1 else "#ffaa00" if stats.get("sharpe",0) >= 0 else "#ff4466"

st.markdown(f"""
<div class="kpi-grid">
  <div class="kpi" style="--a:{rc}">
    <div class="kl">Capital (Net)</div>
    <div class="kv" style="color:{rc}">${capital:,.2f}</div>
    <div class="ks">{ret_pct:+.2f}% return</div>
  </div>
  <div class="kpi" style="--a:{rc}">
    <div class="kl">Net PnL</div>
    <div class="kv" style="color:{rc}">${total_pnl:+.2f}</div>
    <div class="ks">after brokerage</div>
  </div>
  <div class="kpi" style="--a:#ff6644">
    <div class="kl">Brokerage Paid</div>
    <div class="kv" style="color:#ff6644">${total_brok:.2f}</div>
    <div class="ks">fees + slippage + spread</div>
  </div>
  <div class="kpi" style="--a:{wrc}">
    <div class="kl">Win Rate</div>
    <div class="kv" style="color:{wrc}">{stats.get('win_rate',0):.1f}%</div>
    <div class="ks">{stats.get('wins',0)}W / {stats.get('losses',0)}L</div>
  </div>
  <div class="kpi" style="--a:{pfc}">
    <div class="kl">Profit Factor</div>
    <div class="kv" style="color:{pfc}">{stats.get('profit_factor',0):.2f}</div>
    <div class="ks">need > 1.5</div>
  </div>
  <div class="kpi" style="--a:{shc}">
    <div class="kl">Sharpe</div>
    <div class="kv" style="color:{shc}">{stats.get('sharpe',0):.2f}</div>
    <div class="ks">annualised</div>
  </div>
  <div class="kpi">
    <div class="kl">Trades</div>
    <div class="kv">{stats.get('total',0)}</div>
    <div class="ks">{len(open_df)} open now</div>
  </div>
</div>
""", unsafe_allow_html=True)


# ── Equity + Open Positions ──────────────────────────────────────────
col_eq, col_pos = st.columns([2.5, 1])

with col_eq:
    st.markdown('<div class="pt">Equity Curve (Net After Brokerage)</div>', unsafe_allow_html=True)
    if not closed_df.empty:
        import plotly.graph_objects as go
        cdf = closed_df.sort_values("close_time").copy()
        cdf["equity"]       = INITIAL_CAP + cdf["pnl_usd"].cumsum()
        cdf["equity_gross"] = INITIAL_CAP + cdf["gross_pnl"].cumsum() if "gross_pnl" in cdf.columns else cdf["equity"]
        cdf["n"] = range(1, len(cdf)+1)
        color = "#00ff88" if cdf["equity"].iloc[-1] >= INITIAL_CAP else "#ff4466"
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=cdf["n"], y=cdf["equity_gross"], name="Gross",
            line=dict(color="rgba(226,232,240,0.15)", width=1, dash="dot"),
            hovertemplate="Trade #%{x}<br>Gross: $%{y:,.2f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=cdf["n"], y=cdf["equity"], name="Net",
            mode="lines", line=dict(color=color, width=2),
            fill="tozeroy", fillcolor=f"rgba({'0,255,136' if color=='#00ff88' else '255,68,102'},0.05)",
            hovertemplate="Trade #%{x}<br>Net: $%{y:,.2f}<extra></extra>",
        ))
        fig.add_hline(y=INITIAL_CAP, line_dash="dot", line_color="rgba(255,255,255,0.08)", line_width=1)
        fig.update_layout(
            height=240, margin=dict(l=0,r=0,t=4,b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(family="IBM Plex Mono",size=9,color="rgba(226,232,240,0.2)")),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.04)", zeroline=False, tickfont=dict(family="IBM Plex Mono",size=9,color="rgba(226,232,240,0.2)"), tickprefix="$"),
            legend=dict(font=dict(family="IBM Plex Mono",size=9,color="rgba(226,232,240,0.4)"), bgcolor="rgba(0,0,0,0)", x=0.01, y=0.99),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})
    else:
        st.markdown('<div style="height:240px;display:flex;align-items:center;justify-content:center;color:rgba(226,232,240,0.12);font-family:IBM Plex Mono,monospace;font-size:11px">Awaiting first closed trade...</div>', unsafe_allow_html=True)

with col_pos:
    st.markdown(f'<div class="pt">Open Positions ({len(open_df)})</div>', unsafe_allow_html=True)
    if open_df.empty:
        st.markdown('<div style="color:rgba(226,232,240,0.15);font-family:IBM Plex Mono,monospace;font-size:11px;padding:24px 0;text-align:center">— no open positions —</div>', unsafe_allow_html=True)
    else:
        for _, r in open_df.iterrows():
            dc = "#00ff88" if r["direction"]=="LONG" else "#ff4466"
            sl = float(r.get("stop_loss",0)); tp = float(r.get("take_profit",0))
            entry = float(r.get("entry_price",0)); trail = float(r.get("trailing_stop",sl))
            rng = abs(tp-sl) if abs(tp-sl)>0 else 1
            trail_pct = min(abs(trail-sl)/rng*100, 100)
            st.markdown(f"""
            <div class="pos-card">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <span style="font-family:'IBM Plex Mono',monospace;font-weight:600;font-size:13px">{r['symbol']}</span>
                <span class="badge b{'l' if r['direction']=='LONG' else 's'}">{r['direction']}</span>
              </div>
              <div class="pr"><span>Entry</span><span>{entry:,.4f}</span></div>
              <div class="pr"><span>Stop</span><span style="color:#ff4466">{sl:,.4f}</span></div>
              <div class="pr"><span>Target</span><span style="color:#00ff88">{tp:,.4f}</span></div>
              <div class="pr"><span>Trailing</span><span style="color:#4488ff">{trail:,.4f}</span></div>
              <div class="pr"><span>Size</span><span>${float(r['size_usd']):,.0f}</span></div>
              <div style="margin-top:9px">
                <div style="font-family:'IBM Plex Mono',monospace;font-size:8px;color:rgba(226,232,240,0.2);margin-bottom:4px;letter-spacing:1.5px;text-transform:uppercase">Trail Progress</div>
                <div style="height:3px;background:rgba(255,255,255,0.05);border-radius:2px;overflow:hidden">
                  <div style="width:{trail_pct:.0f}%;height:100%;background:#4488ff;border-radius:2px;transition:width .5s"></div>
                </div>
              </div>
            </div>""", unsafe_allow_html=True)


# ── Brokerage breakdown ──────────────────────────────────────────────
if not closed_df.empty and "brokerage_cost" in closed_df.columns:
    with st.expander("💸 Brokerage Cost Breakdown", expanded=False):
        bc1, bc2, bc3, bc4 = st.columns(4)
        avg_brok = closed_df["brokerage_cost"].mean() if len(closed_df) else 0
        brok_pct = (total_brok / (INITIAL_CAP + stats.get("total_gross",total_pnl))) * 100 if (INITIAL_CAP + stats.get("total_gross",0)) else 0
        bc1.metric("Total Fees Paid",      f"${total_brok:.2f}")
        bc2.metric("Avg Cost / Trade",     f"${avg_brok:.3f}")
        bc3.metric("Cost as % of Turnover",f"{brok_pct:.3f}%")
        bc4.metric("Gross vs Net PnL",     f"${stats.get('total_gross',total_pnl):.2f} → ${total_pnl:.2f}")

        st.markdown("**OKX Spot Fee Structure Applied:**")
        col_f1, col_f2 = st.columns(2)
        col_f1.markdown("""
        | Component | Rate |
        |---|---|
        | Taker fee (entry/SL/TIME) | 0.100% |
        | Maker fee (TP exits) | 0.080% |
        | Slippage — BTC/ETH | 0.010% |
        | Slippage — SOL/LINK | 0.020% |
        | Slippage — others | 0.030% |
        """)
        col_f2.markdown("""
        | Component | Rate |
        |---|---|
        | Spread — BTC/ETH | 0.010% |
        | Spread — SOL/LINK | 0.020% |
        | Spread — others | 0.030% |
        | Round-trip min cost | ~0.22% |
        | Round-trip typical | ~0.28% |
        """)
        st.caption("TP exits use maker fee (limit order simulation). All other exits use taker fee.")


# ── Charts ───────────────────────────────────────────────────────────
import plotly.graph_objects as go
c1, c2, c3 = st.columns(3)

with c1:
    st.markdown('<div class="pt">PnL by Symbol</div>', unsafe_allow_html=True)
    if not closed_df.empty:
        sym = closed_df.groupby("symbol")["pnl_usd"].sum().sort_values()
        colors = ["#00ff88" if v>=0 else "#ff4466" for v in sym.values]
        fig = go.Figure(go.Bar(x=sym.values, y=sym.index, orientation="h",
            marker_color=colors, marker_line_width=0,
            hovertemplate="%{y}: $%{x:,.2f}<extra></extra>"))
        fig.update_layout(height=200, margin=dict(l=0,r=0,t=0,b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,.04)", zeroline=True, zerolinecolor="rgba(255,255,255,.1)", tickfont=dict(family="IBM Plex Mono",size=9,color="rgba(226,232,240,.22)")),
            yaxis=dict(showgrid=False, tickfont=dict(family="IBM Plex Mono",size=9,color="rgba(226,232,240,.5)")),
            showlegend=False)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

with c2:
    st.markdown('<div class="pt">Exit Reason Breakdown</div>', unsafe_allow_html=True)
    if not closed_df.empty:
        ec = closed_df["exit_reason"].value_counts()
        ecolors = {"TP":"#00ff88","SL":"#ff4466","TRAILING":"#4488ff","PROTECT":"#ffaa00","TIME":"#a064ff"}
        fig = go.Figure(go.Pie(labels=ec.index, values=ec.values,
            marker_colors=[ecolors.get(k,"#7d8590") for k in ec.index],
            hole=0.6, textfont=dict(family="IBM Plex Mono",size=9),
            hovertemplate="%{label}: %{value} (%{percent})<extra></extra>"))
        fig.update_layout(height=200, margin=dict(l=0,r=20,t=0,b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            legend=dict(font=dict(family="IBM Plex Mono",size=9,color="rgba(226,232,240,.35)"), bgcolor="rgba(0,0,0,0)"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

with c3:
    st.markdown('<div class="pt">Gross vs Net PnL per Trade</div>', unsafe_allow_html=True)
    if not closed_df.empty and "gross_pnl" in closed_df.columns:
        cdf2 = closed_df.sort_values("close_time").head(40)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=list(range(len(cdf2))), y=cdf2["gross_pnl"],
            name="Gross", marker_color="rgba(100,180,255,0.4)", marker_line_width=0,
            hovertemplate="Trade %{x}: Gross $%{y:,.2f}<extra></extra>"))
        fig.add_trace(go.Bar(x=list(range(len(cdf2))), y=cdf2["pnl_usd"],
            name="Net", marker_color=["rgba(0,255,136,0.7)" if v>=0 else "rgba(255,68,102,0.7)" for v in cdf2["pnl_usd"]],
            marker_line_width=0,
            hovertemplate="Trade %{x}: Net $%{y:,.2f}<extra></extra>"))
        fig.update_layout(height=200, margin=dict(l=0,r=0,t=0,b=0), barmode="overlay",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False, tickfont=dict(family="IBM Plex Mono",size=9,color="rgba(226,232,240,.2)")),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,.04)", tickfont=dict(family="IBM Plex Mono",size=9,color="rgba(226,232,240,.2)"), tickprefix="$"),
            legend=dict(font=dict(family="IBM Plex Mono",size=9,color="rgba(226,232,240,.4)"), bgcolor="rgba(0,0,0,0)"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})


# ── Stat row ─────────────────────────────────────────────────────────
cols = st.columns(6)
def sc(col, label, val, color="#e2e8f0"):
    col.markdown(f"""
    <div style="padding:14px 4px;border-top:1px solid rgba(255,255,255,.04)">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:8px;color:rgba(226,232,240,.2);text-transform:uppercase;letter-spacing:2px;margin-bottom:6px">{label}</div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:600;color:{color}">{val}</div>
    </div>""", unsafe_allow_html=True)
sc(cols[0],"Best Trade",  f"${stats.get('best',0):+.2f}","#00ff88")
sc(cols[1],"Worst Trade", f"${stats.get('worst',0):+.2f}","#ff4466")
sc(cols[2],"Avg Win",     f"${stats.get('avg_win',0):.2f}","#00ff88")
sc(cols[3],"Avg Loss",    f"${stats.get('avg_loss',0):.2f}","#ff4466")
sc(cols[4],"Brokerage",   f"${total_brok:.2f}","#ff6644")
sc(cols[5],"Open Pos",    str(len(open_df)),"#4488ff")


# ── Trade History ─────────────────────────────────────────────────────
st.markdown("---")
hc1, hc2, hc3, hc4 = st.columns([3,1,1,1])
with hc1: st.markdown('<div class="pt">Trade History</div>', unsafe_allow_html=True)
with hc2:
    sym_opts = ["All"] + sorted(df["symbol"].unique().tolist()) if not df.empty else ["All"]
    sym_filter = st.selectbox("Symbol", sym_opts, label_visibility="collapsed")
with hc3:
    dir_filter = st.selectbox("Direction", ["All","LONG","SHORT"], label_visibility="collapsed")
with hc4:
    if st.button("⟳  Refresh"):
        st.cache_data.clear(); st.rerun()

if not closed_df.empty:
    disp = closed_df.copy()
    if sym_filter != "All": disp = disp[disp["symbol"]==sym_filter]
    if dir_filter != "All": disp = disp[disp["direction"]==dir_filter]
    disp = disp.head(60)

    bmap = {"TP":"tp","SL":"sl","TRAILING":"tr","PROTECT":"pr","TIME":"tm"}
    rows = ""
    for _, r in disp.iterrows():
        pnl = float(r.get("pnl_usd",0)); gross = float(r.get("gross_pnl",pnl))
        brok = float(r.get("brokerage_cost",0))
        pc   = "style='color:#00ff88'" if pnl>=0 else "style='color:#ff4466'"
        er   = r.get("exit_reason","")
        bc   = bmap.get(er,"op")
        dc   = "l" if r.get("direction")=="LONG" else "s"
        strat= (r.get("strategy","BMV") or "BMV")[:8]
        conf = int(float(r.get("confidence",0))*100)
        ot   = str(r.get("open_time",""))[:16].replace("T"," ")
        rows += f"""<tr>
          <td style="color:rgba(226,232,240,.3)">#{int(r['id'])}</td>
          <td style="color:#e2e8f0;font-weight:500">{r['symbol']}</td>
          <td><span class="badge b{dc}">{r['direction']}</span></td>
          <td>{float(r.get('entry_price',0)):,.3f}</td>
          <td>{float(r.get('exit_price',0)):,.3f}</td>
          <td><span class="badge b{bc}">{er}</span></td>
          <td {pc}>{gross:+.2f}</td>
          <td style="color:#ff6644">-{brok:.3f}</td>
          <td {pc}>{pnl:+.2f}</td>
          <td {pc}>{float(r.get('pnl_pct',0)):+.2f}%</td>
          <td style="color:rgba(226,232,240,.4)">{strat}</td>
          <td style="color:rgba(226,232,240,.3)">{conf}%</td>
          <td style="color:rgba(226,232,240,.25);font-size:10px">{ot}</td>
        </tr>"""

    st.markdown(f"""
    <div style="overflow-x:auto;margin-top:6px">
    <table class="tt">
      <thead><tr>
        <th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th>
        <th>Reason</th><th>Gross $</th><th>Brok $</th><th>Net $</th><th>Net %</th>
        <th>Strategy</th><th>Conf</th><th>Opened</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
    """, unsafe_allow_html=True)

st.markdown(f"""
<div style="text-align:center;padding:18px;border-top:1px solid rgba(255,255,255,.04);margin-top:16px">
  <span style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:rgba(226,232,240,.12);letter-spacing:2px">
    SHYAM TRADING TERMINAL · PAPER MODE · OKX · AUTO-REFRESH 30s · BROKERAGE SIMULATED
  </span>
</div>
""", unsafe_allow_html=True)
