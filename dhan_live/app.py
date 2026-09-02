"""
Dhan live CPR tracker — standalone Streamlit sub-project.

Separate from:
  - Shah CPR console  (app.py, port 8501)
  - Breakout screener (breakout_app.py, port 8502)
  - EOD bhavcopy site (eod_app.py, port 8503)

Run with:
    streamlit run dhan_live/app.py --server.port 8505

Needs DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in dhan_live/dhan_credentials.env
Market data only. No orders.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_LIVE_DIR = Path(__file__).resolve().parent
_load_env(ROOT / ".env")
_load_env(_LIVE_DIR / ".env")
_load_env(_LIVE_DIR / "dhan_credentials.env")

from dhan_live.dhan_feed import DhanError, DhanFeed
from dhan_live.live_cpr import build_live_rows, ltp_map, requested_symbols
from universe import INDEX_UNIVERSES, load_universe, universe_counts

IST = pytz.timezone("Asia/Kolkata")
HERE = Path(__file__).resolve().parent

st.set_page_config(
    page_title="Dhan Live CPR Tracker",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "dhan_prev" not in st.session_state:
    st.session_state.dhan_prev = None
if "dhan_rows" not in st.session_state:
    st.session_state.dhan_rows = None
if "dhan_ltps" not in st.session_state:
    st.session_state.dhan_ltps = {}
if "dhan_error" not in st.session_state:
    st.session_state.dhan_error = None
if "dhan_universe" not in st.session_state:
    st.session_state.dhan_universe = None


def credentials():
    client_id = st.session_state.get("dhan_client_id") or os.getenv("DHAN_CLIENT_ID", "")
    token = st.session_state.get("dhan_access_token") or os.getenv("DHAN_ACCESS_TOKEN", "")
    return str(client_id).strip(), str(token).strip()


def color_pos(val: str) -> str:
    if val in ("Above CPR", "Left above", "Crossed TC"):
        return "color: #2e7d32; font-weight: 600"
    if val in ("Below CPR", "Left below", "Crossed BC"):
        return "color: #c62828; font-weight: 600"
    if val in ("Near CPR", "Entered CPR", "Developing"):
        return "color: #f57c00"
    return ""


def color_bias(val: str) -> str:
    if val == "Bullish":
        return "color: #2e7d32; font-weight: 600"
    if val == "Bearish":
        return "color: #c62828; font-weight: 600"
    return ""


def parse_pasted(text: str):
    symbols = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        symbols.append(line.split(",")[0].strip())
    return symbols


with st.sidebar:
    st.header("📡 Dhan live")
    st.caption("Independent of ports 8501–8503. Uses DhanHQ market data only.")

    env_id, env_token = os.getenv("DHAN_CLIENT_ID", ""), os.getenv("DHAN_ACCESS_TOKEN", "")
    if env_id and env_token:
        st.success("Credentials loaded from dhan_credentials.env")
    else:
        st.session_state.dhan_client_id = st.text_input("Dhan client ID", type="default")
        st.session_state.dhan_access_token = st.text_input("Dhan access token", type="password")
        st.caption("Or save them in `dhan_live/dhan_credentials.env`. Token is not written to disk from this box.")

    method = st.radio("Universe", ["Built-in", "Paste symbols"], index=0)
    if method == "Built-in":
        uni_name = st.selectbox("Index", INDEX_UNIVERSES, index=INDEX_UNIVERSES.index("Nifty 50") if "Nifty 50" in INDEX_UNIVERSES else 0)
        symbols = requested_symbols(load_universe(uni_name))
        n, cash_n, fo_n = universe_counts(symbols)
        st.caption(f"{n} names · {fo_n} F&O · {cash_n} cash")
    else:
        pasted = st.text_area("One NSE symbol per line", height=140, placeholder="RELIANCE\nTCS\nINFY")
        symbols = requested_symbols(parse_pasted(pasted))
        uni_name = "custom"

    segment_filter = st.selectbox("Segment", ["Any", "F&O", "Cash"])
    position_filter = st.selectbox("Position", ["Any", "Above CPR", "Below CPR", "Inside CPR", "Near CPR"])
    events_only = st.checkbox("Only TC/BC crosses this refresh", value=False)
    refresh_sec = st.slider("Auto-refresh (seconds)", 0, 60, 0, help="0 = manual only. Quotes are rate-limited to 1 batch/sec.")

    load_prev = st.button("① Load previous session", use_container_width=True)
    poll = st.button("② Refresh live quotes", type="primary", use_container_width=True)


st.title("📡 Dhan Live CPR Tracker")
st.markdown(
    """
**Separate sub-project.** Previous-day H/L/C from Dhan daily candles → today's CPR.
Live LTP / day OHLC from Dhan quote snapshots. *Research only. No orders. Not investment advice.*
"""
)

client_id, access_token = credentials()
if not client_id or not access_token:
    st.warning("Add Dhan credentials in the sidebar or in `dhan_live/dhan_credentials.env`.")
    st.stop()


def get_feed() -> DhanFeed:
    return DhanFeed(client_id, access_token, cache_dir=HERE / ".cache")


if load_prev:
    if not symbols:
        st.warning("No symbols selected.")
    else:
        feed = get_feed()
        progress = st.progress(0.0, text="Loading Dhan instrument master…")
        try:
            feed.load_master()

            def on_prog(i, total, name):
                if total:
                    progress.progress(min(i / total, 1.0), text=f"Previous session {i}/{total} · {name}")

            prev = feed.load_prev_sessions(symbols, progress=on_prog)
            st.session_state.dhan_prev = prev
            st.session_state.dhan_universe = uni_name
            st.session_state.dhan_error = None
            missing = [s for s in symbols if s not in prev]
            progress.empty()
            st.success(f"Cached previous session for {len(prev)} / {len(symbols)} symbols.")
            if missing:
                with st.expander(f"Unresolved or no history ({len(missing)})"):
                    st.write(", ".join(missing[:80]))
        except Exception as exc:
            progress.empty()
            st.session_state.dhan_error = str(exc)
            st.error(str(exc))


def poll_quotes():
    prev = st.session_state.dhan_prev
    if not prev:
        st.info("Click **Load previous session** once per day, then refresh quotes.")
        return
    feed = get_feed()
    ids = [row["security_id"] for row in prev.values() if row.get("security_id")]
    quotes = feed.live_ohlc(ids)
    rows = build_live_rows(prev, quotes, previous_ltps=st.session_state.dhan_ltps)
    st.session_state.dhan_ltps = ltp_map(rows)
    st.session_state.dhan_rows = rows
    st.session_state.dhan_error = None


if poll:
    try:
        poll_quotes()
    except (DhanError, Exception) as exc:
        st.session_state.dhan_error = str(exc)
        st.error(str(exc))

if refresh_sec and st.session_state.dhan_prev is not None:
    try:
        from datetime import timedelta as _td

        @st.fragment(run_every=_td(seconds=int(refresh_sec)))
        def _auto():
            try:
                poll_quotes()
            except Exception as exc:
                st.session_state.dhan_error = str(exc)

        _auto()
    except Exception:
        pass

if st.session_state.dhan_error:
    st.error(st.session_state.dhan_error)

df = st.session_state.dhan_rows
if df is None:
    st.info("Load the previous session, then refresh live quotes. Previous-day cache is reused until tomorrow.")
    st.stop()

view = df.copy()
if segment_filter != "Any" and "Segment" in view.columns:
    view = view[view["Segment"] == segment_filter]
if position_filter != "Any" and "Position" in view.columns:
    view = view[view["Position"] == position_filter]
if events_only and "Event" in view.columns:
    view = view[view["Event"].astype(str).str.len() > 0]

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Tracked", 0 if df.empty else len(df))
m2.metric("Above", int((df["Position"] == "Above CPR").sum()) if "Position" in df.columns else 0)
m3.metric("Inside", int((df["Position"] == "Inside CPR").sum()) if "Position" in df.columns else 0)
m4.metric("Below", int((df["Position"] == "Below CPR").sum()) if "Position" in df.columns else 0)
m5.metric("Events", int((df["Event"].astype(str).str.len() > 0).sum()) if "Event" in df.columns else 0)
st.caption(datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S %Z") + " · Dhan OHLC snapshot · CPR from last completed session")

if view.empty:
    st.info("No rows match the filters.")
else:
    show = view.drop(columns=["security_id"], errors="ignore").copy()
    for col in ["LTP", "Day Open", "Day High", "Day Low", "Prev Close", "CPR Bottom", "Pivot", "CPR Top", "TC", "BC"]:
        if col in show.columns:
            show[col] = show[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "—")
    for col in ["Width %", "Dist from Pivot %"]:
        if col in show.columns:
            show[col] = show[col].apply(lambda x: f"{x:.3f}" if pd.notna(x) else "—")
    styled = show.style
    if "Position" in show.columns:
        styled = styled.map(color_pos, subset=["Position"])
    if "Bias" in show.columns:
        styled = styled.map(color_bias, subset=["Bias"])
    if "Event" in show.columns:
        styled = styled.map(color_pos, subset=["Event"])
    st.dataframe(styled, use_container_width=True, height=520)

    csv = view.drop(columns=["security_id"], errors="ignore").to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Download live snapshot (CSV)",
        data=csv,
        file_name=f"dhan_live_cpr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
        use_container_width=True,
    )

st.divider()
st.markdown(
    '<div style="text-align:center;color:gray;font-size:0.9em;">'
    "Dhan Live CPR · port 8505 · separate from 8501 / 8502 / 8503 · market data only"
    "</div>",
    unsafe_allow_html=True,
)
