"""
CPR Screening Console - Streamlit Dashboard

A clean, practical Central Pivot Range (CPR) screening console for equities.
Designed for research and chart preparation, NOT personalized investment advice.

Run with: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from typing import List, Dict, Optional, Tuple
import io
import os

# Import our modules
from data_provider import get_data_provider, OHLCVData
from cpr_engine import CPREngine, CPRResult, CPRBias, PricePosition, VirginCPRStatus, validate_cpr_formulas
from universe import INDEX_UNIVERSES, load_universe, universe_counts, classify_symbol


# ============================================================================
# Page Configuration
# ============================================================================
st.set_page_config(
    page_title="CPR Screening Console",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for restrained colors
st.markdown("""
<style>
    .metric-bullish { color: #2e7d32; }
    .metric-bearish { color: #c62828; }
    .metric-neutral { color: #757575; }
    .metric-developing { color: #f57c00; }
    .data-unavailable { color: #9e9e9e; }
    
    .stDataFrame [data-colindex="10"] { color: #2e7d32; }
    .stDataFrame [data-colindex="11"] { color: #c62828; }
    
    div[data-testid="stMetricValue"] { font-size: 1.5rem; }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# Session State Initialization
# ============================================================================
if 'data_cache' not in st.session_state:
    st.session_state.data_cache = {}
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = None
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = None
if 'all_symbols_data' not in st.session_state:
    st.session_state.all_symbols_data = None
if 'prev_match_symbols' not in st.session_state:
    st.session_state.prev_match_symbols = []
if 'entered_symbols' not in st.session_state:
    st.session_state.entered_symbols = []
if 'exited_symbols' not in st.session_state:
    st.session_state.exited_symbols = []
if 'force_fetch' not in st.session_state:
    st.session_state.force_fetch = False


# ============================================================================
# Helper Functions
# ============================================================================

def load_sample_symbols() -> List[str]:
    """Load default NSE symbols from sample_symbols.csv when present."""
    csv_path = os.path.join(os.path.dirname(__file__), "sample_symbols.csv")
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8") as handle:
            parsed = parse_symbols_csv(handle.read())
            if parsed:
                return parsed
    return [
        "RELIANCE.NS",
        "TCS.NS",
        "INFY.NS",
        "HDFCBANK.NS",
        "ICICIBANK.NS",
        "SBIN.NS",
        "BHARTIARTL.NS",
        "ITC.NS",
        "KOTAKBANK.NS",
        "LT.NS",
        "AXISBANK.NS",
        "ASIANPAINT.NS",
        "MARUTI.NS",
        "TITAN.NS",
        "BAJFINANCE.NS"
    ]


def parse_symbols_csv(csv_content: str) -> List[str]:
    """Parse symbols from CSV content (one symbol per line or first column)"""
    symbols = []
    lines = csv_content.strip().split('\n')
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#'):  # Skip comments
            # Handle CSV with multiple columns
            if ',' in line:
                symbol = line.split(',')[0].strip()
            else:
                symbol = line
            if symbol:
                symbols.append(symbol)
    return symbols


def rank_live_watchlist(df: pd.DataFrame) -> pd.DataFrame:
    """Reorder symbols by live CPR action so the list moves as quotes update."""
    if df is None or df.empty:
        return df
    ranked = df.copy()
    virgin_map = {
        "Bullish Virgin": 3,
        "Bearish Virgin": 3,
        "Developing": 2,
        "None": 0,
    }
    position_map = {
        "Near CPR": 3,
        "Above CPR": 2,
        "Below CPR": 2,
        "Inside CPR": 1,
        "Unknown": 0,
    }
    if "Virgin CPR" in ranked.columns:
        ranked["_virgin"] = ranked["Virgin CPR"].map(virgin_map).fillna(0)
    else:
        ranked["_virgin"] = 0
    if "Position" in ranked.columns:
        ranked["_position"] = ranked["Position"].map(position_map).fillna(0)
    else:
        ranked["_position"] = 0
    if "Dist from Pivot %" in ranked.columns:
        ranked["_dist"] = pd.to_numeric(ranked["Dist from Pivot %"], errors="coerce").abs().fillna(999)
    else:
        ranked["_dist"] = 999
    ranked = ranked.sort_values(
        ["_virgin", "_position", "_dist"],
        ascending=[False, False, True],
    ).drop(columns=["_virgin", "_position", "_dist"])
    return ranked.reset_index(drop=True)


def annotate_live_changes(df: pd.DataFrame, previous_symbols: List[str]) -> Tuple[pd.DataFrame, List[str], List[str]]:
    """Mark symbols that entered or left the live match list since the last refresh."""
    if df is None or df.empty:
        current = []
    else:
        current = [s for s in df["Symbol"].tolist() if s]
    prev = list(previous_symbols or [])
    entered = sorted(set(current) - set(prev)) if prev else []
    exited = sorted(set(prev) - set(current)) if prev else []
    if df is None or df.empty:
        return df, entered, exited
    marked = df.copy()
    if prev:
        marked.insert(0, "Live Change", marked["Symbol"].map(lambda s: "NEW" if s in entered else "—"))
    else:
        marked.insert(0, "Live Change", "—")
    return marked, entered, exited


def format_currency(value: Optional[float], currency: str = "₹") -> str:
    """Format value as currency"""
    if value is None:
        return "N/A"
    return f"{currency}{value:,.2f}"


def format_percentage(value: Optional[float]) -> str:
    """Format value as percentage"""
    if value is None:
        return "N/A"
    return f"{value:.3f}%"


def get_position_color(position: str) -> str:
    """Get color for price position"""
    if position == "Above CPR":
        return "green"
    elif position == "Below CPR":
        return "red"
    elif position == "Inside CPR":
        return "orange"
    elif position == "Near CPR":
        return "amber"
    return "gray"


def get_bias_color(bias: str) -> str:
    """Get color for bias"""
    if bias == "Bullish":
        return "green"
    elif bias == "Bearish":
        return "red"
    return "gray"


def get_virgin_color(virgin: str) -> str:
    """Get color for Virgin CPR status"""
    if virgin in ["Bullish Virgin", "Bearish Virgin"]:
        return "green"
    elif virgin == "Developing":
        return "orange"
    return "gray"


def apply_color_to_text(text: str, color: str) -> str:
    """Apply color to text for Streamlit"""
    color_map = {
        "green": "success",
        "red": "error",
        "orange": "warning",
        "amber": "warning",
        "gray": "secondary"
    }
    streamlit_color = color_map.get(color, "secondary")
    return f":{streamlit_color}[**{text}**]"


# ============================================================================
# Sidebar - Configuration
# ============================================================================

with st.sidebar:
    st.header("⚙️ Configuration")
    
    # Exchange & Universe
    st.subheader("Exchange & Universe")
    exchange = st.selectbox(
        "Exchange",
        ["NSE", "BSE", "NYSE", "NASDAQ", "Custom"],
        index=0,
        help="Select the exchange. NSE is default for Indian equities."
    )
    
    # Symbol input
    st.subheader("Symbols")
    symbol_input_method = st.radio(
        "Input Method",
        ["Built-in universe", "Paste CSV", "Upload CSV"],
        index=0,
        help="Nifty 50 is mostly F&O. Use Cash stocks or Nifty 500 to include cash-market names."
    )
    
    symbols = []
    
    if symbol_input_method == "Built-in universe":
        universe_name = st.selectbox(
            "Universe",
            INDEX_UNIVERSES,
            index=0,
            help="Cash stocks = Nifty 500 names that are not in the F&O lot list."
        )
        symbols = load_universe(universe_name)
        total_n, cash_n, fo_n = universe_counts(symbols)
        st.caption(f"{total_n} names · {cash_n} cash · {fo_n} F&O")
        st.text_area(
            "Preview",
            value="\n".join(symbols[:20]) + (f"\n… {total_n} total" if total_n > 20 else ""),
            height=140,
            disabled=True,
        )
    
    elif symbol_input_method == "Paste CSV":
        csv_paste = st.text_area(
            "Paste CSV content",
            height=200,
            placeholder="RELIANCE.NS\nTCS.NS\nINFY.NS\n...",
            help="Paste symbols (one per line or first column of CSV)"
        )
        if csv_paste:
            symbols = parse_symbols_csv(csv_paste)
    
    elif symbol_input_method == "Upload CSV":
        uploaded_file = st.file_uploader(
            "Upload CSV",
            type=["csv", "txt"],
            help="CSV with symbols in first column or one symbol per line"
        )
        if uploaded_file:
            content = uploaded_file.read().decode('utf-8')
            symbols = parse_symbols_csv(content)
    
    st.markdown(f"**Symbols loaded:** {len(symbols)}")
    if len(symbols) > 80:
        st.caption("Large universe: scans take longer. Stay on Manual refresh.")
    
    # Session Configuration
    st.subheader("Session Settings")
    session_timezone = st.selectbox(
        "Timezone",
        ["Asia/Kolkata", "America/New_York", "Europe/London"],
        index=0,
        help="Timezone for session calculations"
    )
    
    use_mock_data = st.checkbox(
        "Use Mock Data",
        value=False,
        help="Off = live Yahoo Finance quotes (NSE .NS tickers). On = synthetic test data."
    )
    
    # Data Provider
    data_provider = get_data_provider(use_mock=use_mock_data, session_timezone=session_timezone)
    st.caption(f"Source: {data_provider.data_source}")
    
    st.divider()
    
    # Refresh
    st.subheader("Data Refresh")
    refresh_mode = st.radio(
        "Refresh mode",
        ["Manual", "Auto"],
        index=0,
        help="Manual (default): scan only when you click Refresh. Auto re-fetches on a timer."
    )
    refresh_seconds = None
    if refresh_mode == "Auto":
        auto_refresh_label = st.selectbox(
            "Auto interval",
            ["15 seconds", "30 seconds", "1 minute", "2 minutes"],
            index=2,
            help="How often to re-fetch quotes. Use 1–2 minutes for Nifty 500."
        )
        refresh_seconds = {
            "15 seconds": 15,
            "30 seconds": 30,
            "1 minute": 60,
            "2 minutes": 120,
        }[auto_refresh_label]

    if st.button("🔄 Refresh Data", use_container_width=True, type="primary"):
        st.session_state.force_fetch = True
        st.rerun()
    
    if st.session_state.last_refresh:
        st.caption(f"Last refresh: {st.session_state.last_refresh.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        st.caption("No scan yet — click Refresh Data")
    if getattr(data_provider, "is_session_open", lambda: False)():
        st.caption("Market session: open")
    else:
        st.caption("Market session: closed (showing last available quotes)")


# ============================================================================
# Main Panel - Filters
# ============================================================================

st.title("📊 CPR Screening Console")
st.markdown("""
**Central Pivot Range screener** following [Prashant Shah's CPR thread](https://x.com/prashantshah267/status/1457382268033396742):  
bands from **previous day's H/L/C**, then screen today's live open / high / low / last against that range.  
*Research only. Quotes may be delayed. Not investment advice.*
""")

# Filter Controls
with st.expander("🔍 Screening Filters", expanded=True):
    shah_preset = st.selectbox(
        "Shah CPR preset",
        [
            "Manual filters",
            "Narrow CPR — possible trend day",
            "Too narrow CPR",
            "Wide CPR — prior day strong close",
            "Virgin CPR — never touched the band",
            "Higher CPR — bullish overlay",
            "Lower CPR — bearish overlay",
            "Away above CPR — strong uptrend",
            "Away below CPR — strong downtrend",
            "Opened above + away (trend day)",
        ],
        index=0,
        help="Presets from Shah: narrow/wide bands, virgin CPR, and current band vs prior band."
    )

    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        cpr_width_filter = st.selectbox(
            "CPR Width",
            ["Any", "Narrow", "Wide", "Custom"],
            index=0,
            help="Narrow: close was near mid-range. Wide: close was far from mid-range."
        )
        
        narrow_max = st.number_input(
            "Narrow Max %",
            min_value=0.0,
            max_value=5.0,
            value=0.25,
            step=0.05,
            help="Maximum Width % for 'Narrow' CPR"
        )
        
        wide_min = st.number_input(
            "Wide Min %",
            min_value=0.0,
            max_value=5.0,
            value=0.75,
            step=0.05,
            help="Minimum Width % for 'Wide' CPR"
        )
    
    with col2:
        position_filter = st.selectbox(
            "Price Position",
            ["Any", "Above CPR", "Below CPR", "Inside CPR", "Near CPR"],
            index=0
        )
        
        near_distance = st.number_input(
            "Near Distance %",
            min_value=0.0,
            max_value=5.0,
            value=0.5,
            step=0.1,
            help="Distance % from CPR for 'Near' classification"
        )
    
    with col3:
        bias_filter = st.selectbox(
            "Previous Bias",
            ["Any", "Bullish", "Bearish", "Neutral"],
            index=0
        )
        
        virgin_cpr_filter = st.selectbox(
            "Virgin CPR",
            ["Any", "Bullish Virgin", "Bearish Virgin", "Any Virgin"],
            index=0,
            help="Shah: strong trend day when price does not even touch CPR bands."
        )

        overlay_filter = st.selectbox(
            "CPR overlay",
            ["Any", "Higher CPR", "Lower CPR", "Inside prior CPR", "Overlapping"],
            index=0,
            help="Today's band vs previous day's band. Higher = bullish overlay."
        )

        segment_filter = st.selectbox(
            "Segment",
            ["Any", "Cash", "F&O"],
            index=0,
            help="Cash = not in NSE F&O lot list. F&O = derivatives underlyings."
        )

        # Industry filter — populated from nifty500_industry + eod2_sectors
        _industry_opts = ["Any"]
        try:
            import pandas as _pd
            from pathlib import Path as _Path
            _ind_df = _pd.read_csv(_Path(__file__).resolve().parent / "universes" / "nifty500_industry.csv")
            _eod2_df = _pd.read_csv(_Path(__file__).resolve().parent / "universes" / "eod2_sectors.csv")
            _all_inds = sorted(
                set(_ind_df["Industry"].dropna().astype(str).tolist())
                | set(_eod2_df["Sector"].dropna().astype(str).tolist())
            )
            _industry_opts += _all_inds
        except Exception:
            pass
        industry_filter = st.selectbox(
            "Industry / Sector",
            _industry_opts,
            index=0,
            help="Filter by NSE official industry (Nifty 500) or eod2 fine-grained sector.",
        )
        hide_diversified = st.checkbox(
            "Hide Diversified / unknown",
            value=False,
            help="Hide stocks whose industry could not be determined.",
        )

    with col4:
        st.markdown("**Liquidity Filters**")

        min_price = st.number_input(
            "Min Price",
            min_value=0.0,
            value=0.0,
            step=10.0,
            help="Minimum current price"
        )
        
        min_volume = st.number_input(
            "Min Volume",
            min_value=0,
            value=0,
            step=100000,
            help="Minimum average volume"
        )
        
        show_all = st.checkbox(
            "Show All Symbols",
            value=False,
            help="Off = live watchlist of current matches only (symbols enter/leave as data changes)."
        )

width_class_filter = "Any"
open_vs_cpr_filter = "Any"
if shah_preset != "Manual filters":
    show_all = False
    cpr_width_filter = "Any"
    position_filter = "Any"
    bias_filter = "Any"
    virgin_cpr_filter = "Any"
    overlay_filter = "Any"
    if shah_preset == "Narrow CPR — possible trend day":
        cpr_width_filter = "Narrow"
    elif shah_preset == "Too narrow CPR":
        width_class_filter = "Too Narrow"
    elif shah_preset == "Wide CPR — prior day strong close":
        cpr_width_filter = "Wide"
    elif shah_preset == "Virgin CPR — never touched the band":
        virgin_cpr_filter = "Any Virgin"
    elif shah_preset == "Higher CPR — bullish overlay":
        overlay_filter = "Higher CPR"
    elif shah_preset == "Lower CPR — bearish overlay":
        overlay_filter = "Lower CPR"
    elif shah_preset == "Away above CPR — strong uptrend":
        position_filter = "Above CPR"
    elif shah_preset == "Away below CPR — strong downtrend":
        position_filter = "Below CPR"
    elif shah_preset == "Opened above + away (trend day)":
        position_filter = "Above CPR"
        open_vs_cpr_filter = "Opened above"


# ============================================================================
# Data Fetching & Processing
# ============================================================================

def fetch_and_calculate_cpr(
    symbols: List[str],
    data_provider,
    cpr_engine: CPREngine,
    session_timezone: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fetch data and calculate CPR for all symbols.
    
    Returns:
        (all_results_df, filtered_results_df)
    """
    tz = pytz.timezone(session_timezone)
    fetch_timestamp = datetime.now(tz)
    
    # Fetch OHLCV data for all symbols
    ohlcv_data = data_provider.fetch_multiple_symbols(
        symbols,
        lookback_days=20 if len(symbols) > 80 else 60,
    )
    
    all_results = []
    
    for symbol, ohlcv in ohlcv_data.items():
        prev_session = ohlcv.get_previous_session()
        prior_session = ohlcv.get_session_before_previous()
        quote = ohlcv.get_current_quote()
        current_price = ohlcv.get_current_price()
        data_status = ohlcv.get_data_status()
        session_open = ohlcv.is_session_open()
        
        if prev_session is None:
            result = CPRResult(
                symbol=symbol,
                company_name=None,
                current_price=current_price,
                previous_close=None,
                previous_high=None,
                previous_low=None,
                pivot=None,
                bc=None,
                tc=None,
                cpr_bottom=None,
                cpr_top=None,
                cpr_width=None,
                cpr_width_pct=None,
                bias=None,
                position=None,
                virgin_cpr=None,
                data_timestamp=fetch_timestamp.strftime("%Y-%m-%d %H:%M:%S %Z"),
                data_status=data_status if data_status != "OK" else "Data unavailable",
                current_high=quote.get("day_high"),
                current_low=quote.get("day_low")
            )
        else:
            result = cpr_engine.screen_symbol(
                symbol=symbol,
                company_name=None,
                prev_high=prev_session['high'],
                prev_low=prev_session['low'],
                prev_close=prev_session['close'],
                current_price=current_price,
                current_high=quote.get("day_high"),
                current_low=quote.get("day_low"),
                current_volume=quote.get("volume") or prev_session.get('volume'),
                data_timestamp=fetch_timestamp.strftime("%Y-%m-%d %H:%M:%S %Z"),
                data_status=data_status,
                is_session_open=session_open,
                current_open=quote.get("open"),
                prior_high=prior_session['high'] if prior_session else None,
                prior_low=prior_session['low'] if prior_session else None,
                prior_close=prior_session['close'] if prior_session else None,
            )
        
        if result is not None:
            row = result.to_dict()
            row["Segment"] = classify_symbol(symbol)
            all_results.append(row)

    # Create DataFrame
    df = pd.DataFrame(all_results)

    # Attach Industry / Sector column from eod2 sector map
    if not df.empty:
        try:
            _sec_map: Dict[str, str] = {}
            _ind_path = os.path.join(os.path.dirname(__file__), "universes", "nifty500_industry.csv")
            _eod2_path = os.path.join(os.path.dirname(__file__), "universes", "eod2_sectors.csv")
            if os.path.exists(_ind_path):
                _idf = pd.read_csv(_ind_path)
                _sec_map.update({
                    str(s).strip().upper(): str(i).strip()
                    for s, i in zip(_idf["Symbol"], _idf["Industry"])
                    if pd.notna(s) and pd.notna(i)
                })
            if os.path.exists(_eod2_path):
                _sdf = pd.read_csv(_eod2_path)
                for s, sec in zip(_sdf["Symbol"], _sdf["Sector"]):
                    key = str(s).strip().upper()
                    if pd.notna(s) and pd.notna(sec) and key not in _sec_map:
                        _sec_map[key] = str(sec).strip()
            if _sec_map:
                _bare = df["Symbol"].str.upper().str.replace(r"\.NS$|\.BO$", "", regex=True)
                df["Industry"] = _bare.map(_sec_map).fillna("Diversified")
        except Exception:
            pass

    # Apply CPR filters
    filtered_df = cpr_engine.screen_dataframe(
        df,
        cpr_width_filter=cpr_width_filter,
        position_filter=position_filter,
        bias_filter=bias_filter,
        virgin_cpr_filter=virgin_cpr_filter,
        overlay_filter=overlay_filter,
        open_vs_cpr_filter=open_vs_cpr_filter,
        width_class_filter=width_class_filter,
    )
    if segment_filter != "Any" and filtered_df is not None and not filtered_df.empty and "Segment" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Segment"] == segment_filter].reset_index(drop=True)
    if industry_filter != "Any" and filtered_df is not None and not filtered_df.empty and "Industry" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Industry"] == industry_filter].reset_index(drop=True)
    if hide_diversified and filtered_df is not None and not filtered_df.empty and "Industry" in filtered_df.columns:
        filtered_df = filtered_df[~filtered_df["Industry"].isin(["Diversified", "Unclassified"])].reset_index(drop=True)

    return df, filtered_df



def apply_watchlist_filters(all_df: pd.DataFrame, cpr_engine: CPREngine) -> pd.DataFrame:
    """Re-apply CPR filters to a cached scan without downloading quotes again."""
    if all_df is None or all_df.empty:
        return all_df
    filtered_df = cpr_engine.screen_dataframe(
        all_df,
        cpr_width_filter=cpr_width_filter,
        position_filter=position_filter,
        bias_filter=bias_filter,
        virgin_cpr_filter=virgin_cpr_filter,
        overlay_filter=overlay_filter,
        open_vs_cpr_filter=open_vs_cpr_filter,
        width_class_filter=width_class_filter,
    )
    if segment_filter != "Any" and filtered_df is not None and not filtered_df.empty and "Segment" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Segment"] == segment_filter].reset_index(drop=True)
    if industry_filter != "Any" and filtered_df is not None and not filtered_df.empty and "Industry" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Industry"] == industry_filter].reset_index(drop=True)
    if hide_diversified and filtered_df is not None and not filtered_df.empty and "Industry" in filtered_df.columns:
        filtered_df = filtered_df[~filtered_df["Industry"].isin(["Diversified", "Unclassified"])].reset_index(drop=True)
    return filtered_df



# ============================================================================
# Main Execution
# ============================================================================

if len(symbols) == 0:
    st.warning("⚠️ No symbols loaded. Please add symbols via the sidebar.")
else:
    cpr_engine = CPREngine(
        narrow_max_pct=narrow_max,
        wide_min_pct=wide_min,
        near_cpr_distance_pct=near_distance,
        min_price=min_price if min_price > 0 else None,
        min_volume=min_volume if min_volume > 0 else None
    )

    top_l, top_r = st.columns([3, 1])
    with top_r:
        if st.button("🔄 Refresh Data", use_container_width=True, type="primary", key="main_refresh"):
            st.session_state.force_fetch = True
            st.rerun()
    with top_l:
        if refresh_mode == "Manual":
            st.caption("Manual refresh — quotes are fetched only when you click Refresh Data. Changing filters does not re-download.")
        else:
            st.caption(f"Auto refresh every {refresh_seconds}s.")

    def build_watchlist(all_df: pd.DataFrame) -> pd.DataFrame:
        filtered_df = apply_watchlist_filters(all_df, cpr_engine)
        watchlist = all_df if show_all else filtered_df
        watchlist = rank_live_watchlist(watchlist)
        watchlist, entered, exited = annotate_live_changes(
            watchlist, st.session_state.prev_match_symbols
        )
        st.session_state.entered_symbols = entered
        st.session_state.exited_symbols = exited
        st.session_state.prev_match_symbols = (
            watchlist["Symbol"].tolist() if watchlist is not None and not watchlist.empty else []
        )
        st.session_state.scan_results = watchlist
        return watchlist

    def run_fetch():
        with st.spinner("📡 Fetching quotes and calculating CPR..."):
            all_df, _ = fetch_and_calculate_cpr(
                symbols,
                data_provider,
                cpr_engine,
                session_timezone
            )
        st.session_state.all_symbols_data = all_df
        st.session_state.last_refresh = datetime.now()
        st.session_state.force_fetch = False
        build_watchlist(all_df)

    do_fetch = bool(st.session_state.force_fetch)
    if refresh_mode == "Auto" and st.session_state.all_symbols_data is None:
        do_fetch = True

    @st.fragment(run_every=refresh_seconds)
    def render_screening():
        if st.session_state.force_fetch or (refresh_mode == "Auto"):
            last = st.session_state.last_refresh
            due = (
                st.session_state.force_fetch
                or last is None
                or (
                    refresh_seconds
                    and (datetime.now() - last).total_seconds() >= refresh_seconds
                )
            )
            if due:
                try:
                    run_fetch()
                except Exception as e:
                    st.error(f"❌ Error during scan: {str(e)}")
                    return
            elif st.session_state.all_symbols_data is not None:
                build_watchlist(st.session_state.all_symbols_data)
        elif st.session_state.all_symbols_data is not None:
            build_watchlist(st.session_state.all_symbols_data)
        else:
            st.info("Manual refresh is on. Click **Refresh Data** to run the scan.")
            return

        all_df = st.session_state.all_symbols_data
        filtered_df = st.session_state.scan_results
        if all_df is None:
            return

        total_scanned = len(all_df)
        matches = len(filtered_df) if filtered_df is not None else 0
        width = pd.to_numeric(all_df['Width %'], errors='coerce') if 'Width %' in all_df.columns else pd.Series(dtype=float)
        narrow_count = int((width <= narrow_max).sum())
        wide_count = int((width >= wide_min).sum())
        bullish_virgin_count = len(all_df[all_df['Virgin CPR'].isin(['Bullish Virgin', 'Developing'])]) if 'Virgin CPR' in all_df.columns else 0
        bearish_virgin_count = len(all_df[all_df['Virgin CPR'].isin(['Bearish Virgin', 'Developing'])]) if 'Virgin CPR' in all_df.columns else 0

        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            st.metric("Symbols Scanned", total_scanned)
        with col2:
            st.metric("Matches", matches)
        with col3:
            st.metric("Narrow CPR", narrow_count, help=f"Width ≤ {narrow_max}%")
        with col4:
            st.metric("Wide CPR", wide_count, help=f"Width ≥ {wide_min}%")
        with col5:
            st.metric("Bullish Virgin", bullish_virgin_count)
        with col6:
            st.metric("Bearish Virgin", bearish_virgin_count)

        st.caption(
            f"{data_provider.data_source} · "
            f"{datetime.now(pytz.timezone(session_timezone)).strftime('%Y-%m-%d %H:%M:%S %Z')}"
            + (f" · auto every {refresh_seconds}s" if refresh_seconds else " · manual refresh")
        )

        entered = st.session_state.get("entered_symbols") or []
        exited = st.session_state.get("exited_symbols") or []
        if entered or exited:
            ch1, ch2 = st.columns(2)
            with ch1:
                st.success("Entered: " + (", ".join(entered) if entered else "—"))
            with ch2:
                st.warning("Left screen: " + (", ".join(exited) if exited else "—"))

        st.divider()

        tab_watch, tab_movers = st.tabs(["📋 Live CPR Watchlist", "🚀 Top 25 Movers & CPR Breakdown"])
        
        with tab_watch:
            st.subheader("📋 Live CPR watchlist")
            st.caption("Cached scan — change filters instantly. Click Refresh Data to download new quotes.")
            if filtered_df is not None and not filtered_df.empty:
                display_df = filtered_df.copy()
                numeric_cols = ['Current Price', 'Day Open', 'Previous Close', 'CPR Bottom', 'Pivot', 'CPR Top', 'CPR Width', 'Width %', 'Dist from Pivot %']
                for col in numeric_cols:
                    if col in display_df.columns:
                        display_df[col] = display_df[col].apply(lambda x: f"{x:.3f}" if pd.notna(x) else "N/A")

                def color_bias(val):
                    if val == "Bullish":
                        return "color: green"
                    elif val == "Bearish":
                        return "color: red"
                    return ""

                def color_position(val):
                    if val == "Above CPR":
                        return "color: green"
                    elif val == "Below CPR":
                        return "color: red"
                    elif val == "Inside CPR":
                        return "color: orange"
                    return ""

                def color_virgin(val):
                    if val in ["Bullish Virgin", "Bearish Virgin"]:
                        return "color: green"
                    elif val == "Developing":
                        return "color: orange"
                    return ""

                def color_overlay(val):
                    if val == "Higher CPR":
                        return "color: green"
                    elif val == "Lower CPR":
                        return "color: red"
                    return ""

                def color_segment(val):
                    if val == "Cash":
                        return "color: #1565c0"
                    elif val == "F&O":
                        return "color: #6a1b9a"
                    return ""

                styled_df = display_df.style
                if 'Bias' in display_df.columns:
                    styled_df = styled_df.map(color_bias, subset=['Bias'])
                if 'Position' in display_df.columns:
                    styled_df = styled_df.map(color_position, subset=['Position'])
                if 'Virgin CPR' in display_df.columns:
                    styled_df = styled_df.map(color_virgin, subset=['Virgin CPR'])
                if 'Overlay' in display_df.columns:
                    styled_df = styled_df.map(color_overlay, subset=['Overlay'])
                if 'Segment' in display_df.columns:
                    styled_df = styled_df.map(color_segment, subset=['Segment'])

                st.dataframe(styled_df, use_container_width=True, height=400)

                csv_buffer = io.StringIO()
                display_df.to_csv(csv_buffer, index=False)
                st.download_button(
                    label="📥 Download Results (CSV)",
                    data=csv_buffer.getvalue().encode('utf-8'),
                    file_name=f"cpr_screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            else:
                st.info("ℹ️ No symbols match the current filters. Try relaxing the filter criteria.")

        with tab_movers:
            st.subheader("🚀 Top 25 Movers vs CPR Analysis")
            st.caption("Analyze how today's biggest market movers correlate with CPR width, overlays, and price positions.")
            if all_results_df is not None and not all_results_df.empty:
                df_m = all_results_df.copy()
                if 'Current Price' in df_m.columns and 'Previous Close' in df_m.columns:
                    cp = pd.to_numeric(df_m['Current Price'], errors='coerce')
                    pc = pd.to_numeric(df_m['Previous Close'], errors='coerce')
                    df_m['Day Change %'] = ((cp - pc) / pc * 100).round(2)
                    
                    gainers_25 = df_m.sort_values('Day Change %', ascending=False).head(25)
                    losers_25 = df_m.sort_values('Day Change %', ascending=True).head(25)
                    
                    show_cols = [c for c in ['Symbol', 'Current Price', 'Day Change %', 'CPR Width', 'Width %', 'Bias', 'Position', 'Overlay', 'Segment'] if c in df_m.columns]
                    
                    col_g, col_l = st.columns(2)
                    with col_g:
                        st.markdown("### 🟢 Top 25 Outperformers (Gainers)")
                        st.dataframe(gainers_25[show_cols], use_container_width=True)
                    with col_l:
                        st.markdown("### 🔴 Top 25 Underperformers (Losers)")
                        st.dataframe(losers_25[show_cols], use_container_width=True)
            else:
                st.info("ℹ️ Scan data not yet loaded. Please click Refresh Data to populate movers.")

        st.divider()
        with st.expander("📖 Methodology & Formulas"):
            st.markdown("""
            ### Prashant Shah CPR rules

            Source: [CPR thread, 9 Nov 2021](https://x.com/prashantshah267/status/1457382268033396742)

            Three lines from the **previous day's** bar: **CPR (pivot)**, **BC** (mid-range), **TC** (mirror of BC around pivot).

            - **Pivot** = (H + L + C) / 3
            - **BC** = (H + L) / 2
            - **TC** = (Pivot − BC) + Pivot
            - Pivot above BC = **bullish pivot**; pivot below BC = **bearish pivot**
            - **Narrow CPR**: close near mid-range → possible trend day next
            - **Wide CPR**: close far from mid-range → prior day trended into the close
            - **Higher CPR**: today's band entirely above prior day's band (bullish overlay)
            - **Lower CPR**: today's band entirely below prior day's band (bearish overlay)
            - **Virgin CPR**: today's high/low never touches the band (strong trend day)
            - Price **away above / below** the band = strong directional session
            - CPR as support/resistance in sideways markets, and as a **pullback zone** in trends

            Live screening uses yesterday's completed OHLC for the bands, and today's open / high / low / last from 1-minute bars.
            """)
            st.code(f"""
Data Source: {data_provider.data_source}
Session Timezone: {session_timezone}
Fetch Timestamp: {datetime.now(pytz.timezone(session_timezone)).strftime('%Y-%m-%d %H:%M:%S %Z')}
Symbols Scanned: {total_scanned}
            """)
            st.warning("""
            **DISCLAIMER**: This tool is for research and educational purposes only.
            - Yahoo Finance data may be delayed or incomplete
            - Not suitable for live trading decisions
            - Not investment advice or recommendation
            - Verify with licensed broker data before trading
            """)

    render_screening()


# ============================================================================
# Footer
# ============================================================================

st.divider()

st.markdown("""
<div style="text-align: center; color: gray; font-size: 0.9em;">
CPR Screening Console v1.0 | For research use only | Not investment advice
</div>
""", unsafe_allow_html=True)