#!/usr/bin/env python3
"""
NSE EOD CPR Scanner

Separate from the Shah CPR console (`cpr_engine.py` / `app.py`) and the
intraday breakout screener (`cpr_breakout_engine.py` / `breakout_app.py`).

- Downloads NSE bhavcopy CSVs (cash + F&O)
- Caches ~252 prior cash sessions (configurable via --lookback): ~60 for daily
  own-history rank, the rest gives weekly / monthly CPR real depth
- Computes CPR, Width %, classification, and Bullish/Bearish flags
- Tags F&O vs Cash-only symbols
- Exports ranked tables and shortlists

This uses the completed session's H/L/C (EOD bhavcopy). Those levels are
the CPR that applies to the *next* session.

Usage:
    python nse_cpr_scanner.py 20260813
"""

from __future__ import annotations

import io
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from cpr_contract import (
    CPR_NARROW_MAX_PCT,
    CPR_WIDE_MIN_PCT,
    calculate_cpr_frame,
)
from cpr_scoring import SCORE_FIELDS, attach_confirmation_score
from wide_cpr_strategy import WIDE_FIELDS, attach_wide_strategy, wide_table
from signal_contract import setup_score
from cpr_parquet import save_session_parquet

OUTPUT_DIR = Path("cpr_output")
IST = ZoneInfo("Asia/Kolkata")


def session_dir(date: str, output_dir: Optional[Path] = None) -> Path:
    """Return cpr_output/YYYY/MM for a YYYYMMDD session date."""
    root = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    if len(date) != 8 or not date.isdigit():
        raise ValueError(f"Session date must be YYYYMMDD, got {date!r}")
    return root / date[:4] / date[4:6]


def scan_csv_path(kind: str, date: str, output_dir: Optional[Path] = None) -> Path:
    """Canonical path for a scan CSV: cpr_output/YYYY/MM/cpr_{kind}_{date}.csv."""
    return session_dir(date, output_dir) / f"cpr_{kind}_{date}.csv"


def resolve_scan_csv(kind: str, date: str, output_dir: Optional[Path] = None) -> Path:
    """Prefer nested YYYY/MM path; fall back to legacy flat cpr_output/ layout."""
    nested = scan_csv_path(kind, date, output_dir)
    if nested.exists():
        return nested
    root = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    flat = root / f"cpr_{kind}_{date}.csv"
    if flat.exists():
        return flat
    return nested

CASH_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"
FO_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{date}_F_0000.csv.zip"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
}

# UDI bhavcopy (Jul 2024+) → legacy names used by the rest of this scanner.
COLUMN_ALIASES = {
    "TckrSymb": "SYMBOL",
    "SctySrs": "SERIES",
    "OpnPric": "OPEN",
    "HghPric": "HIGH",
    "LwPric": "LOW",
    "ClsPric": "CLOSE",
    "LastPric": "LAST",
    "PrvsClsgPric": "PREVCLOSE",
    "TtlTradgVol": "VOLUME",
    "TtlTrfVal": "VALUE",
    "FinInstrmNm": "NAME",
    "ISIN": "ISIN",
}

CASH_SERIES = ("EQ",)
UNCLASSIFIED_INDUSTRY = "Unclassified"
INDUSTRY_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
INDUSTRY_CACHE = Path(__file__).resolve().parent / "universes" / "nifty500_industry.csv"
# eod2-curated fine-grained sector map (fallback for non-Nifty-500 symbols)
SECTOR_CACHE = Path(__file__).resolve().parent / "universes" / "eod2_sectors.csv"

# Keyword heuristics — used when symbol is not in SECTOR_CACHE either
_KEYWORD_SECTORS: list[tuple[tuple[str, ...], str]] = [
    (("BANK", "FINANCE", "FINANCIAL", "FINSERV", "NBFC", "HOUSING", "CREDIT", "SECURITIES"), "Financial Services"),
    (("PHARMA", "DRUG", "HEALTH", "MEDIC", "BIO", "LAB", "HOSPITAL", "CLINIC", "DIAGN"), "Healthcare & Pharma"),
    (("TECH", "SOFT", "INFO", "DIGITAL", "SOLUTION", "SYSTEM", "DATA", "CYBER"), "Information Technology"),
    (("AUTO", "MOTOR", "TYRE", "WHEEL", "GEAR", "CLUTCH", "BRAKE", "ENGINE", "FORG"), "Automobile & Ancillary"),
    (("STEEL", "IRON", "METAL", "ALUM", "COPPER", "ZINC", "MINING", "MINERAL"), "Metals & Mining"),
    (("POWER", "SOLAR", "WIND", "RENEW"), "Power & Utilities"),
    (("OIL", "GAS", "PETRO", "REFIN", "FUEL"), "Energy & Oil/Gas"),
    (("CHEM", "ORGANIC", "SPECIALTY", "FERT", "AGRO", "PEST", "PIGMENT"), "Chemicals & Fertilizers"),
    (("REALTY", "INFRA", "BUILD", "CONSTRUCT", "ESTATE", "DEVELOP", "PROP"), "Real Estate & Infra"),
    (("TEXTILE", "FABRIC", "COTTON", "YARN", "SPIN", "GARMENT", "APPAREL", "WEAR"), "Textiles & Apparel"),
    (("FOOD", "SUGAR", "TEA", "COFFEE", "BEVERAGE", "BREW", "DISTILL", "DAIRY"), "FMCG & Food Products"),
    (("PAPER", "PACK", "PRINT", "CONTAINER"), "Paper & Packaging"),
    (("CEMENT", "CERAMIC", "PIPE", "GLASS", "TILES"), "Building Materials"),
    (("LOGISTIC", "TRANSPORT", "SHIPPING", "PORT", "FREIGHT", "EXPRESS", "CARGO"), "Logistics & Ports"),
    (("MEDIA", "ENTERTAIN", "FILM", "BROADCAST", "CABLE"), "Media & Entertainment"),
    (("RETAIL", "MART", "STORE", "JEWEL", "WATCH", "FASHION"), "Consumer & Retail"),
    (("HOTEL", "RESORT", "TRAVEL", "TOUR", "RESTAUR", "HOSPITALITY"), "Hotels & Tourism"),
    (("TELECOM", "COMMUNICATION", "TOWER", "ANTENNA"), "Telecommunications"),
    (("DEFENCE", "DEFENSE", "ARMOUR", "WEAPON", "ORDNANCE"), "Aerospace & Defense"),
    (("INSURANCE", "INSUR", "ASSURANCE"), "Insurance"),
]


def _classify_by_keyword(symbol: str) -> str:
    """Classify a symbol using keyword heuristics. Returns a sector string."""
    s = symbol.upper()
    for keywords, sector in _KEYWORD_SECTORS:
        if any(kw in s for kw in keywords):
            return sector
    return "Diversified"
HISTORY_LOOKBACK = 60
HISTORY_LOOKBACK_HTF = 252
OWN_NARROW_QUANTILE = 0.25
MIN_HISTORY_DAYS = 20
MIN_HISTORY_WEEKS = 12
MIN_HISTORY_MONTHS = 6
MIN_VALUE_TOP20 = 20_000_000
BEARISH_WIDTH_PCT = CPR_NARROW_MAX_PCT
SETUP_CUSHION_PCT = 0.20
ATR_PERIOD = 14
SMA_FAST = 50
SMA_SLOW = 100
MARKET_SYMBOL = "NIFTY"
WATCHLIST_SETUPS = ("Long", "Short", "Watch Long", "Watch Short", "Watch")
BHAVCOPY_SLIM_COLS = ["SYMBOL", "SERIES", "NAME", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "VALUE"]

# ETFs, AMC schemes, index funds, gilt/liquid products listed as EQ.
NON_EQUITY_NAME = (
    r"AMC|\bETF\b|BEES|IETF|MUTUAL\s*FUND|INDEX FUND|\bFOF\b|"
    r"LIQUID FUND|LIQUID ETF|GOLD ETF|SILVER ETF"
)
NON_EQUITY_SYMBOL = r"ETF|BEES|IETF|LIQUID|GILT|GSEC|INVIT"


@dataclass
class ScanResult:
    date: str
    cash_rows: int
    fo_available: bool
    full: pd.DataFrame
    narrow: pd.DataFrame
    bullish: pd.DataFrame
    bearish: pd.DataFrame
    top20: pd.DataFrame
    output_dir: Path = field(default_factory=lambda: OUTPUT_DIR)
    weekly: pd.DataFrame = field(default_factory=pd.DataFrame)
    monthly: pd.DataFrame = field(default_factory=pd.DataFrame)
    weekly_applies: str = ""
    monthly_applies: str = ""
    best: pd.DataFrame = field(default_factory=pd.DataFrame)
    watchlist: pd.DataFrame = field(default_factory=pd.DataFrame)
    follow_through: pd.DataFrame = field(default_factory=pd.DataFrame)
    wide: pd.DataFrame = field(default_factory=pd.DataFrame)
    bullish_bias: pd.DataFrame = field(default_factory=pd.DataFrame)


def _nse_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get("https://www.nseindia.com", timeout=20)
    except requests.RequestException:
        pass
    return session


def download_bhavcopy(url: str, date: str, session: Optional[requests.Session] = None) -> Optional[pd.DataFrame]:
    """Download and unzip an NSE bhavcopy CSV."""
    formatted_url = url.format(date=date)
    print(f"Downloading: {formatted_url}")
    own_session = session is None
    session = session or _nse_session()
    try:
        response = session.get(formatted_url, timeout=45)
        response.raise_for_status()
        if formatted_url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                csv_filename = next(
                    (name for name in z.namelist() if name.lower().endswith(".csv")),
                    z.namelist()[0],
                )
                with z.open(csv_filename) as f:
                    df = pd.read_csv(f)
        else:
            df = pd.read_csv(io.BytesIO(response.content))
        return df
    except Exception as exc:
        print(f"Error downloading {formatted_url}: {exc}")
        return None
    finally:
        if own_session:
            session.close()


def normalize_bhavcopy(df: pd.DataFrame, cash_only: bool = False) -> pd.DataFrame:
    """Map UDI or legacy columns onto SYMBOL / OPEN / HIGH / LOW / CLOSE."""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    rename = {src: dst for src, dst in COLUMN_ALIASES.items() if src in out.columns and dst not in out.columns}
    if rename:
        out = out.rename(columns=rename)

    required = ["SYMBOL", "OPEN", "HIGH", "LOW", "CLOSE"]
    missing = [col for col in required if col not in out.columns]
    if missing:
        raise ValueError(f"Bhavcopy missing columns {missing}. Got: {list(out.columns)}")

    out["SYMBOL"] = out["SYMBOL"].astype(str).str.strip().str.upper()
    for col in ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "VALUE"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "SERIES" in out.columns:
        out["SERIES"] = out["SERIES"].astype(str).str.strip().str.upper()
        if cash_only:
            out = out[out["SERIES"].isin(CASH_SERIES)]

    out = out.dropna(subset=["SYMBOL", "HIGH", "LOW", "CLOSE"])
    out = out[out["CLOSE"] > 0]
    out = out.drop_duplicates(subset=["SYMBOL"], keep="first")
    return out.reset_index(drop=True)


def non_equity_mask(df: pd.DataFrame) -> pd.Series:
    """True for ETFs, AMC products, liquid/gilt funds, not operating companies."""
    symbol = df["SYMBOL"].astype(str)
    mask = symbol.str.contains(NON_EQUITY_SYMBOL, case=False, regex=True, na=False)
    if "NAME" in df.columns:
        name = df["NAME"].astype(str)
        mask = mask | name.str.contains(NON_EQUITY_NAME, case=False, regex=True, na=False)
    return mask


def keep_listed_equity(df: pd.DataFrame, quiet: bool = False) -> pd.DataFrame:
    """EQ operating companies only — drop ETFs, AMCs, mutual funds, gilt/liquid products."""
    if df.empty:
        return df
    dropped = non_equity_mask(df)
    kept = df.loc[~dropped].copy()
    if not quiet:
        print(f"Equity filter: {int(dropped.sum())} ETF/AMC/fund rows dropped → {len(kept)} stocks")
    return kept.reset_index(drop=True)


def load_sector_map() -> dict:
    """Symbol → fine-grained sector from universes/eod2_sectors.csv (eod2 curated data)."""
    if not SECTOR_CACHE.exists():
        return {}
    try:
        df = pd.read_csv(SECTOR_CACHE)
        if "Symbol" in df.columns and "Sector" in df.columns:
            return {
                str(sym).strip().upper(): str(sec).strip()
                for sym, sec in zip(df["Symbol"], df["Sector"])
                if pd.notna(sym) and pd.notna(sec)
            }
    except Exception as exc:
        print(f"Could not load eod2 sector map: {exc}")
    return {}


def load_industry_map(session: Optional[requests.Session] = None, fetch: bool = True) -> dict:
    """Symbol → NSE Indices industry (Nifty 500 list). Cache under universes/.

    Returns the Nifty-500 official industry mapping. Non-Nifty-500 symbols
    will still be "Unclassified" here; use ``attach_industry()`` which applies
    the eod2 sector fallback for those.
    """
    if INDUSTRY_CACHE.exists():
        cached = pd.read_csv(INDUSTRY_CACHE)
        if "Symbol" in cached.columns and "Industry" in cached.columns:
            mapping = {
                str(sym).strip().upper(): str(ind).strip()
                for sym, ind in zip(cached["Symbol"], cached["Industry"])
                if pd.notna(sym) and pd.notna(ind)
            }
            if mapping:
                return mapping
    if not fetch:
        return {}
    own = session is None
    session = session or _nse_session()
    try:
        response = session.get(INDUSTRY_URL, timeout=30)
        response.raise_for_status()
        table = pd.read_csv(io.BytesIO(response.content))
        table.columns = [str(c).strip() for c in table.columns]
        if "Symbol" not in table.columns or "Industry" not in table.columns:
            print(f"Industry file missing columns: {list(table.columns)}")
            return {}
        INDUSTRY_CACHE.parent.mkdir(exist_ok=True)
        table.to_csv(INDUSTRY_CACHE, index=False)
        print(f"Industry map: {len(table)} Nifty 500 names → {INDUSTRY_CACHE}")
        return {
            str(sym).strip().upper(): str(ind).strip()
            for sym, ind in zip(table["Symbol"], table["Industry"])
            if pd.notna(sym) and pd.notna(ind)
        }
    except Exception as exc:
        print(f"Industry map unavailable: {exc}")
        return {}
    finally:
        if own:
            session.close()


def attach_industry(df: pd.DataFrame, mapping: Optional[dict] = None, fetch: bool = True) -> pd.DataFrame:
    """Join Nifty 500 industry.

    For symbols inside Nifty 500 the official NSE industry label is used.
    For symbols outside Nifty 500 (currently "Unclassified") the eod2
    curated sector map is tried first, then keyword heuristics, so no stock
    is left as "Unclassified" unnecessarily.
    """
    out = df.copy()
    # Nifty-500 official industry
    mapping = mapping if mapping is not None else load_industry_map(fetch=fetch)
    out["Industry"] = out["SYMBOL"].map(mapping)
    # Ensure object dtype so string fills work correctly
    out["Industry"] = out["Industry"].astype(object)
    out["Nifty500"] = out["Industry"].notna()

    # For non-Nifty-500 symbols apply eod2 sector as fallback
    unclassified_mask = out["Industry"].isna()
    if unclassified_mask.any():
        sector_map = load_sector_map()
        # Priority 1: eod2 curated sectors
        eod2_fill = out.loc[unclassified_mask, "SYMBOL"].map(sector_map).astype(object)
        # Priority 2: keyword heuristics for remaining unknowns
        still_missing = eod2_fill.isna()
        if still_missing.any():
            keyword_fill = out.loc[unclassified_mask & still_missing, "SYMBOL"].apply(_classify_by_keyword)
            eod2_fill = eod2_fill.copy()
            eod2_fill[still_missing] = keyword_fill.values
        out.loc[unclassified_mask, "Industry"] = eod2_fill.values

    # Final safety net: anything still NaN → "Diversified"
    out["Industry"] = out["Industry"].fillna("Diversified")
    return out




def cpr_overlay(today_top, today_bot, prior_top, prior_bot) -> str:
    """Shah overlay: today's CPR vs the previous session's CPR."""
    if pd.isna(prior_top) or pd.isna(prior_bot) or pd.isna(today_top) or pd.isna(today_bot):
        return "Unknown"
    if today_bot > prior_top:
        return "Higher"
    if today_top < prior_bot:
        return "Lower"
    if today_top <= prior_top and today_bot >= prior_bot:
        return "Inside"
    if today_top >= prior_top and today_bot <= prior_bot:
        return "Outside"
    return "Overlapping"


def bhavcopy_cache_dir(output_dir: Optional[Path] = None) -> Path:
    root = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    return root / "bhavcopy"


def session_date_window(end_date: str, sessions: int = HISTORY_LOOKBACK_HTF, calendar_pad: int = 130) -> List[str]:
    """Newest-first weekday dates, padded so holidays can be skipped.

    `calendar_pad` covers weekends + market holidays so the window yields at least
    `sessions` trading dates (~250 sessions ≈ 12 months)."""
    end = datetime.strptime(end_date, "%Y%m%d").date()
    dates: List[str] = []
    cur = end
    for _ in range(sessions + calendar_pad):
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y%m%d"))
        cur -= timedelta(days=1)
    return dates


def _slim_bhavcopy(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in BHAVCOPY_SLIM_COLS if c in df.columns]
    return df.loc[:, cols].copy()


def ensure_bhavcopy_history(
    end_date: str,
    lookback: int = HISTORY_LOOKBACK_HTF,
    output_dir: Optional[Path] = None,
    session: Optional[requests.Session] = None,
) -> List[str]:
    """Download and cache up to `lookback` cash EQ bhavcopies ending at end_date."""
    cache = bhavcopy_cache_dir(output_dir)
    cache.mkdir(parents=True, exist_ok=True)
    own = session is None
    session = session or _nse_session()
    got: List[str] = []
    try:
        for date in session_date_window(end_date, lookback):
            if len(got) >= lookback:
                break
            path = cache / f"cm_{date}.csv"
            if path.exists() and path.stat().st_size > 0:
                got.append(date)
                continue
            raw = download_bhavcopy(CASH_URL, date, session=session)
            if raw is None:
                print(f"  no cash bhavcopy for {date}")
                time.sleep(0.15)
                continue
            try:
                df = keep_listed_equity(normalize_bhavcopy(raw, cash_only=True), quiet=True)
            except Exception as exc:
                print(f"  skip {date}: {exc}")
                continue
            _slim_bhavcopy(df).to_csv(path, index=False)
            print(f"  cached {date}: {len(df)} stocks → {path.name}")
            got.append(date)
            time.sleep(0.15)
    finally:
        if own:
            session.close()
    print(f"Bhavcopy history: {len(got)} sessions ending {end_date}")
    return got


def seed_bhavcopy_cache(df: pd.DataFrame, date: str, output_dir: Optional[Path] = None) -> Path:
    """Write today's slim cash bhavcopy so history does not re-download it."""
    cache = bhavcopy_cache_dir(output_dir)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"cm_{date}.csv"
    _slim_bhavcopy(df).to_csv(path, index=False)
    return path


def cached_history_dates(end_date: str, output_dir: Optional[Path] = None, lookback: int = HISTORY_LOOKBACK_HTF) -> List[str]:
    cache = bhavcopy_cache_dir(output_dir)
    got: List[str] = []
    for date in session_date_window(end_date, lookback):
        path = cache / f"cm_{date}.csv"
        if path.exists() and path.stat().st_size > 0:
            got.append(date)
        if len(got) >= lookback:
            break
    return got


def load_history_panel(dates: List[str], output_dir: Optional[Path] = None) -> pd.DataFrame:
    cache = bhavcopy_cache_dir(output_dir)
    frames = []
    for date in dates:
        path = cache / f"cm_{date}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if df.empty:
            continue
        df["session"] = date
        frames.append(compute_cpr(df))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def scan_from_cached_bhavcopy(
    date: str,
    output_dir: Optional[Path] = None,
    lookback: int = HISTORY_LOOKBACK_HTF,
    write_csv: bool = True,
) -> ScanResult:
    """Build a session scan from a cached cash bhavcopy (no NSE download)."""
    path = bhavcopy_cache_dir(output_dir) / f"cm_{date}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing cached bhavcopy {path}")
    cash_df = pd.read_csv(path)
    cash_df = keep_listed_equity(cash_df, quiet=True)
    if cash_df.empty:
        raise RuntimeError(f"Cached bhavcopy {date} has no listed equity rows")
    cash_df = attach_industry(cash_df, fetch=False)
    if "Segment" not in cash_df.columns:
        cash_df["Segment"] = "Cash Only"
    cash_df = compute_cpr(cash_df)
    cash_df = apply_bullish_cpr_filters(cash_df)
    hist_dates = cached_history_dates(date, output_dir, lookback)
    if hist_dates:
        cash_df = attach_history_features(
            cash_df, load_history_panel(hist_dates, output_dir), own_window=HISTORY_LOOKBACK
        )
    if write_csv:
        return export_results(cash_df, date, output_dir=output_dir, verbose=False)
    full_table, narrow, bullish, bearish, top20 = split_shortlists(cash_df)
    bullish_bias = cash_df[cash_df["Bias"] == "Bullish"].copy() if "Bias" in cash_df.columns else pd.DataFrame()
    return ScanResult(
        date=date,
        cash_rows=len(cash_df),
        fo_available=False,
        full=full_table,
        narrow=narrow,
        bullish=bullish,
        bearish=bearish,
        top20=top20,
        output_dir=Path(output_dir) if output_dir is not None else OUTPUT_DIR,
        bullish_bias=bullish_bias,
    )


def backfill_cached_scans(
    end_date: str,
    output_dir: Optional[Path] = None,
    lookback: int = HISTORY_LOOKBACK_HTF,
    skip_existing: bool = True,
) -> List[str]:
    """Write cpr_full_*.csv for each cached cash session so the site archive has those dates."""
    output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    dates = cached_history_dates(end_date, output_dir, lookback)
    if not dates:
        print("Archive: no cached bhavcopies")
        return []
    print(f"Archive: loading {len(dates)} cached sessions…")
    panel = load_history_panel(dates, output_dir)
    industry = load_industry_map(fetch=False)
    written: List[str] = []
    for date in dates:
        full_path = resolve_scan_csv("full", date, output_dir)
        if full_path.exists():
            header = set(pd.read_csv(full_path, nrows=0).columns)
            if skip_existing and "Setup" in header and "Overlay" in header:
                continue
            print(f"  enrich {date} with overlay / own-narrow")
            result = load_scan_result(date, output_dir)
            export_results(result.full, date, output_dir=output_dir, verbose=False)
            written.append(date)
            continue
        day = panel[panel["session"] == date].copy()
        if day.empty:
            continue
        print(f"  archive scan {date}")
        if "Segment" not in day.columns:
            day["Segment"] = "Cash Only"
        day = attach_industry(day, mapping=industry, fetch=False)
        day = apply_bullish_cpr_filters(day)
        hist = panel.loc[panel["session"] <= date]
        day = attach_history_features(day, hist, own_window=HISTORY_LOOKBACK)
        export_results(day, date, output_dir=output_dir, verbose=False)
        written.append(date)
    print(f"Archive: {len(dates)} cached sessions, {len(written)} scans written")
    return dates


def backfill_htf_scans(
    end_date: str,
    output_dir: Optional[Path] = None,
    lookback: int = HISTORY_LOOKBACK_HTF,
) -> List[str]:
    """Write cpr_weekly_* / cpr_monthly_*.csv for every archived daily session.

    Aggregates the daily cache to week/month bars once, then per archived session
    replays the completed-period logic so every archive page gets HTF tabs.
    """
    output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    dates = cached_history_dates(end_date, output_dir, lookback)
    if len(dates) < 5:
        return []
    panel = load_history_panel(dates, output_dir)
    if panel.empty:
        return []
    weekly_bars = aggregate_htf_bars(panel, "W-FRI")
    monthly_bars = aggregate_htf_bars(panel, "M")
    written: List[str] = []
    for date in dates:
        full_path = resolve_scan_csv("full", date, output_dir)
        if not full_path.exists():
            continue
        for freq, bars, min_hist, suffix in (
            ("W-FRI", weekly_bars, MIN_HISTORY_WEEKS, "weekly"),
            ("M", monthly_bars, MIN_HISTORY_MONTHS, "monthly"),
        ):
            out_path = resolve_scan_csv(suffix, date, output_dir)
            if out_path.exists() and out_path.stat().st_size:
                continue
            if bars.empty:
                continue
            complete_end = last_complete_period_end(date, freq)
            hist = bars[bars["session"] <= complete_end]
            if hist.empty or hist["session"].nunique() < min_hist:
                continue
            hist = apply_bullish_cpr_filters(hist)
            f = attach_history_features(hist, hist, min_history=min_hist)
            latest = f["session"].max()
            frame = f[f["session"] == latest].copy()
            if frame.empty:
                continue
            frame["Applies"] = htf_applies_label(str(latest), freq)
            frame["Timeframe"] = "Weekly" if freq.startswith("W") else "Monthly"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(out_path, index=False)
            written.append(f"{date}_{suffix}")
    if written:
        print(f"HTF archive: wrote {len(written)} week/month CSVs")
    return written


def market_regime(history_df: pd.DataFrame, symbol: str = MARKET_SYMBOL) -> str:
    """Market regime from the cached panel.

    Prefers a named index (NIFTY): Risk On when the index closed above its prior
    session's CPR top, Risk Off below the prior bottom. When the index row is not
    present in the cash bhavcopy (indices are not equities), falls back to breadth:
    if more than half the panel closed above their prior CPR top → Risk On; more
    than half below their prior bottom → Risk Off; otherwise Neutral.
    """
    if history_df is None or history_df.empty:
        return "Unknown"
    if "CLOSE" in history_df.columns and "CPR_Top" not in history_df.columns:
        return "Unknown"
    mkt = history_df[history_df["SYMBOL"] == symbol].sort_values("session")
    if len(mkt) >= 2 and "CPR_Top" in mkt.columns:
        last = mkt.iloc[-1]
        prev = mkt.iloc[-2]
        if not (pd.isna(last["CLOSE"]) or pd.isna(prev["CPR_Top"]) or pd.isna(prev["CPR_Bottom"])):
            if float(last["CLOSE"]) > float(prev["CPR_Top"]):
                return "Risk On"
            if float(last["CLOSE"]) < float(prev["CPR_Bottom"]):
                return "Risk Off"
            return "Neutral"

    h = history_df.dropna(subset=["SYMBOL", "session", "CLOSE", "CPR_Top", "CPR_Bottom"])
    if h.empty:
        return "Unknown"
    h = h.sort_values(["SYMBOL", "session"])
    h["prior_top"] = h.groupby("SYMBOL")["CPR_Top"].shift(1)
    h["prior_bot"] = h.groupby("SYMBOL")["CPR_Bottom"].shift(1)
    latest = h["session"].max()
    day = h[h["session"] == latest].copy()
    day = day.dropna(subset=["prior_top", "prior_bot"])
    if day.empty:
        return "Unknown"
    up = (day["CLOSE"] > day["prior_top"]).mean()
    dn = (day["CLOSE"] < day["prior_bot"]).mean()
    if up >= 0.5 and up > dn:
        return "Risk On"
    if dn >= 0.5 and dn > up:
        return "Risk Off"
    return "Neutral"


def attached_history_features(
    hist: pd.DataFrame,
    own_window: Optional[int],
    min_history: int,
) -> pd.DataFrame:
    """Per-symbol rolling history features from a bhavcopy panel.

    Technical series (ATR14 / SMA50 / SMA100) use every completed bar in the panel;
    the width rank and 60-day median turnover use `own_window` (the last N sessions)
    when provided. Returns the latest session's features keyed by SYMBOL.
    """
    h = hist.copy()
    h["SYMBOL"] = h["SYMBOL"].astype(str).str.strip().str.upper()
    h = h.sort_values(["SYMBOL", "session"])
    full = h
    if own_window:
        h = h.groupby("SYMBOL", sort=False).tail(own_window).copy()
    h["prior_top"] = h.groupby("SYMBOL")["CPR_Top"].shift(1)
    h["prior_bot"] = h.groupby("SYMBOL")["CPR_Bottom"].shift(1)
    h["Width_Rank_Pct"] = h.groupby("SYMBOL")["CPR_Width_Pct"].rank(method="average", pct=True)
    h["History_Days"] = h.groupby("SYMBOL")["session"].transform("count")
    
    # Multi-day compression: NR4 and NR7
    h["min_width_4"] = h.groupby("SYMBOL")["CPR_Width_Pct"].transform(lambda s: s.rolling(4, min_periods=4).min())
    h["min_width_7"] = h.groupby("SYMBOL")["CPR_Width_Pct"].transform(lambda s: s.rolling(7, min_periods=7).min())
    h["NR4"] = (h["CPR_Width_Pct"] <= h["min_width_4"]).fillna(False)
    h["NR7"] = (h["CPR_Width_Pct"] <= h["min_width_7"]).fillna(False)

    if "VALUE" in h.columns:
        h["VALUE"] = pd.to_numeric(h["VALUE"], errors="coerce")
        h["Value_60d"] = h.groupby("SYMBOL")["VALUE"].transform("median")
    else:
        h["Value_60d"] = np.nan

    tech = full.dropna(subset=["HIGH", "LOW", "CLOSE"]).copy()
    tech["prev_close"] = tech.groupby("SYMBOL")["CLOSE"].shift(1)
    tech["TR"] = np.maximum(
        tech["HIGH"] - tech["LOW"],
        np.maximum(
            (tech["HIGH"] - tech["prev_close"]).abs(),
            (tech["LOW"] - tech["prev_close"]).abs(),
        ),
    )
    tech["ATR14"] = tech.groupby("SYMBOL")["TR"].transform(
        lambda s: s.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    )
    tech["SMA50"] = tech.groupby("SYMBOL")["CLOSE"].transform(
        lambda s: s.rolling(SMA_FAST, min_periods=SMA_FAST).mean()
    )
    tech["SMA100"] = tech.groupby("SYMBOL")["CLOSE"].transform(
        lambda s: s.rolling(SMA_SLOW, min_periods=SMA_SLOW).mean()
    )
    tech_latest = tech[tech["session"] == tech["session"].max()]
    tech_latest = tech_latest.set_index("SYMBOL")[["ATR14", "SMA50", "SMA100"]]

    latest = h["session"].max()
    today = h[h["session"] == latest][
        ["SYMBOL", "prior_top", "prior_bot", "Width_Rank_Pct", "History_Days", "Value_60d", "NR4", "NR7"]
    ]
    today = today.set_index("SYMBOL")
    if tech_latest.empty:
        return today, pd.DataFrame()
    return today, tech_latest


def attach_history_features(
    scan_df: pd.DataFrame,
    history_df: pd.DataFrame,
    own_narrow_q: float = OWN_NARROW_QUANTILE,
    min_history: int = MIN_HISTORY_DAYS,
    own_window: Optional[int] = None,
) -> pd.DataFrame:
    """Width percentile vs own history, prior-session overlay, median turnover, Setup.

    `own_window` limits the width percentile / History_Days / turnover to the most
    recent N sessions per symbol. Daily scans pass HISTORY_LOOKBACK (60) even when
    the cache holds ~252 sessions; weekly / monthly HTF bars leave it None so the
    rank and history counts use every completed bar in the cache.

    Technical context (ATR14 / Width_ATR / Value_Ratio / Above_SMA50/100) always
    uses the full cached panel so 50/100-session moving averages are meaningful.
    """
    out = scan_df.copy()
    if history_df is None or history_df.empty or "SYMBOL" not in history_df.columns:
        out["Overlay"] = "Unknown"
        out["Width_Rank_Pct"] = np.nan
        out["Own_Narrow"] = False
        out["History_Days"] = 0
        out["History_OK"] = False
        out["Value_60d"] = np.nan
        out["ATR14"] = np.nan
        out["Width_ATR"] = np.nan
        out["Value_Ratio"] = np.nan
        out["Above_SMA50"] = False
        out["Above_SMA100"] = False
        out["Regime"] = "Unknown"
        out["Setup"] = "No setup"
        out["NR4"] = False
        out["NR7"] = False
        out["Virgin_CPR"] = "None"
        out["Triple_Confluence"] = "None"
        return out

    hist = history_df.copy()
    hist["SYMBOL"] = hist["SYMBOL"].astype(str).str.strip().str.upper()
    hist = hist.sort_values(["SYMBOL", "session"])
    today, tech = attached_history_features(hist, own_window, min_history)

    latest = hist["session"].max()
    regime = market_regime(hist)

    out["SYMBOL"] = out["SYMBOL"].astype(str).str.strip().str.upper()
    drop_cols = [
        "Overlay", "Width_Rank_Pct", "Own_Narrow", "History_Days", "History_OK",
        "Value_60d", "ATR14", "Width_ATR", "Value_Ratio", "Above_SMA50",
        "Above_SMA100", "Regime", "Setup", "NR4", "NR7", "Virgin_CPR", "Triple_Confluence",
    ]
    out = out.drop(columns=drop_cols, errors="ignore")
    out = out.merge(today.reset_index(), on="SYMBOL", how="left")
    out["Overlay"] = [
        cpr_overlay(t, b, pt, pb)
        for t, b, pt, pb in zip(out["CPR_Top"], out["CPR_Bottom"], out["prior_top"], out["prior_bot"])
    ]
    out["Width_Rank_Pct"] = pd.to_numeric(out["Width_Rank_Pct"], errors="coerce")
    out["History_Days"] = pd.to_numeric(out["History_Days"], errors="coerce").fillna(0).astype(int)
    out["History_OK"] = out["History_Days"] >= min_history
    out["Regime"] = regime
    out["Own_Narrow"] = (
        (out["Width_Rank_Pct"] <= own_narrow_q)
        & (out["History_OK"])
        & (pd.to_numeric(out["CPR_Width_Pct"], errors="coerce") > 0)
    ).fillna(False).astype(bool)

    if "NR4" not in out.columns:
        out["NR4"] = False
    if "NR7" not in out.columns:
        out["NR7"] = False
    out["NR4"] = out["NR4"].fillna(False).astype(bool)
    out["NR7"] = out["NR7"].fillna(False).astype(bool)

    # Virgin CPR calculation
    low_val = pd.to_numeric(out.get("LOW", np.nan), errors="coerce")
    high_val = pd.to_numeric(out.get("HIGH", np.nan), errors="coerce")
    pt = pd.to_numeric(out.get("prior_top", np.nan), errors="coerce")
    pb = pd.to_numeric(out.get("prior_bot", np.nan), errors="coerce")
    out["Virgin_CPR"] = np.where(
        (low_val > pt) & pt.notna(),
        "Bullish Virgin",
        np.where(
            (high_val < pb) & pb.notna(),
            "Bearish Virgin",
            "None",
        ),
    )

    if not tech.empty:
        out = out.merge(tech.reset_index(), on="SYMBOL", how="left")
    for col in ("ATR14", "SMA50", "SMA100"):
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["Width_ATR"] = out["CPR_Width"] / out["ATR14"]
    if "VALUE" in out.columns:
        value_today = pd.to_numeric(out["VALUE"], errors="coerce")
    else:
        value_today = np.nan
    close = pd.to_numeric(out["CLOSE"], errors="coerce")
    out["Value_Ratio"] = value_today / out["Value_60d"]
    out["Above_SMA50"] = close > out["SMA50"]
    out["Above_SMA100"] = close > out["SMA100"]

    above = out["Price_Position"] == "Above CPR"
    below = out["Price_Position"] == "Below CPR"
    inside = out["Price_Position"] == "Inside CPR"
    bull = out["Bias"] == "Bullish"
    bear = out["Bias"] == "Bearish"
    neutral = out["Bias"] == "Neutral"
    long_ok = (
        above
        & bull
        & (out["Overlay"] == "Higher")
        & ((close - out["CPR_Top"]) / close * 100 >= SETUP_CUSHION_PCT)
    )
    short_ok = (
        below
        & bear
        & (out["Overlay"] == "Lower")
        & ((out["CPR_Bottom"] - close) / close * 100 >= SETUP_CUSHION_PCT)
    )
    long_ok &= out["Regime"] != "Risk Off"
    short_ok &= out["Regime"] != "Risk On"
    own = out["Own_Narrow"].astype(bool)
    out["Setup"] = np.where(
        own & long_ok,
        "Long",
        np.where(
            own & short_ok,
            "Short",
            np.where(
                own & inside & bull,
                "Watch Long",
                np.where(own & inside & bear, "Watch Short", np.where(own & inside & neutral, "Watch", "No setup")),
            ),
        ),
    )

    # Triple Confluence Flag
    conf = pd.to_numeric(out["Confluence_Score"], errors="coerce").fillna(0) if "Confluence_Score" in out.columns else pd.Series(0, index=out.index)
    vr = pd.to_numeric(out["Value_Ratio"], errors="coerce").fillna(1.0) if "Value_Ratio" in out.columns else pd.Series(1.0, index=out.index)
    out["Triple_Confluence"] = np.where(
        (conf >= 4) & (out["Above_SMA50"] == True) & (out["Above_SMA100"] == True) & (vr >= 1.2),
        "Bullish",
        np.where(
            (conf <= -4) & (out["Above_SMA50"] == False) & (out["Above_SMA100"] == False) & (vr >= 1.2),
            "Bearish",
            "None",
        ),
    )
    return out.drop(columns=["prior_top", "prior_bot"], errors="ignore")


def last_complete_period_end(scan_date: str, freq: str) -> str:
    """Last finished week (W-FRI) or month as of scan_date. Incomplete bars are excluded."""
    d = datetime.strptime(scan_date, "%Y%m%d").date()
    ts = pd.Timestamp(d)
    per = ts.to_period(freq)
    end = per.end_time.date()
    if freq.startswith("W"):
        if d >= end or d.weekday() == 4:
            return end.strftime("%Y%m%d")
        return (per - 1).end_time.date().strftime("%Y%m%d")
    last_weekday = end
    while last_weekday.weekday() >= 5:
        last_weekday -= timedelta(days=1)
    if d >= last_weekday:
        return end.strftime("%Y%m%d")
    return (per - 1).end_time.date().strftime("%Y%m%d")


def htf_applies_label(period_end: str, freq: str) -> str:
    """The calendar window the completed bar’s CPR is for (next week / next month)."""
    end = datetime.strptime(period_end, "%Y%m%d").date()
    if freq.startswith("W"):
        start = end + timedelta(days=3)
        while start.weekday() != 0:
            start += timedelta(days=1)
        finish = start + timedelta(days=4)
        return f"Week {start.strftime('%d %b')} – {finish.strftime('%d %b %Y')}"
    nxt = pd.Timestamp(end).to_period("M") + 1
    return nxt.strftime("%b %Y")


def aggregate_htf_bars(history_df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Roll daily OHLC into week or month bars. session = period end YYYYMMDD."""
    if history_df is None or history_df.empty:
        return pd.DataFrame()
    df = history_df.copy()
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip().str.upper()
    df["dt"] = pd.to_datetime(df["session"].astype(str), format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["dt", "SYMBOL", "HIGH", "LOW", "CLOSE"])
    if df.empty:
        return pd.DataFrame()
    df["period_end"] = df["dt"].dt.to_period(freq).dt.end_time.dt.strftime("%Y%m%d")
    agg = {"OPEN": "first", "HIGH": "max", "LOW": "min", "CLOSE": "last"}
    if "VALUE" in df.columns:
        df["VALUE"] = pd.to_numeric(df["VALUE"], errors="coerce")
        agg["VALUE"] = "sum"
    for col in ("NAME", "Industry", "Segment"):
        if col in df.columns:
            agg[col] = "last"
    grouped = df.sort_values(["SYMBOL", "dt"]).groupby(["SYMBOL", "period_end"], sort=True)
    out = grouped.agg(agg).reset_index()
    out["session"] = out["period_end"]
    out = compute_cpr(out)
    if "Industry" not in out.columns:
        out = attach_industry(out, fetch=False)
    return out


def build_htf_frame(
    history_df: pd.DataFrame,
    scan_date: str,
    freq: str,
    min_history: int,
) -> tuple[pd.DataFrame, str]:
    """Completed HTF bars up to scan_date, with overlay / own-narrow / Setup."""
    bars = aggregate_htf_bars(history_df, freq)
    if bars.empty:
        return pd.DataFrame(), ""
    complete_end = last_complete_period_end(scan_date, freq)
    bars = bars[bars["session"] <= complete_end]
    if bars.empty:
        return pd.DataFrame(), ""
    bars = apply_bullish_cpr_filters(bars)
    bars = attach_history_features(bars, bars, min_history=min_history)
    latest = bars["session"].max()
    frame = bars[bars["session"] == latest].copy()
    label = htf_applies_label(str(latest), freq)
    frame["Applies"] = label
    frame["Timeframe"] = "Weekly" if freq.startswith("W") else "Monthly"
    if "Setup" in frame.columns:
        frame["Daily_Signal"] = 0
        frame["Weekly_Signal"] = frame["Setup"].map(setup_signal).fillna(0).astype(int)
        frame["Monthly_Signal"] = 0
        frame["Confluence_Score"] = frame["Weekly_Signal" if freq.startswith("W") else "Monthly_Signal"]
    return frame.reset_index(drop=True), label


def _signal_map(frame: pd.DataFrame) -> dict:
    """SYMBOL → signed direction from a Daily/Weekly/Monthly setup list."""
    if frame.empty or "Setup" not in frame.columns:
        return {}
    return {
        sym: setup_score(s)
        for sym, s in zip(frame["SYMBOL"].astype(str).str.upper(), frame["Setup"])
    }


def setup_signal(setup: str) -> int:
    return setup_score(setup)


def attach_confluence(
    daily: pd.DataFrame,
    weekly: pd.DataFrame,
    monthly: pd.DataFrame,
) -> pd.DataFrame:
    """Add Daily/Weekly/Monthly_signal and signed Confluence_Score to the daily frame.

    Signals are directional: Long +2, Watch Long +1, Short −2, Watch Short −1,
    Watch / No setup 0. Confluence_Score sums the three timeframes (−6 … +6);
    the sign is the net direction and the magnitude the breadth of agreement.
    """
    out = daily.copy()
    out["SYMBOL"] = out["SYMBOL"].astype(str).str.strip().str.upper()
    if "Setup" not in out.columns:
        out["Setup"] = "No setup"
    out["Daily_Signal"] = out["Setup"].map(setup_signal).fillna(0).astype(int)
    w_map = _signal_map(weekly)
    m_map = _signal_map(monthly)
    out["Weekly_Signal"] = out["SYMBOL"].map(w_map).fillna(0).astype(int)
    out["Monthly_Signal"] = out["SYMBOL"].map(m_map).fillna(0).astype(int)
    out["Confluence_Score"] = (
        out["Daily_Signal"] + out["Weekly_Signal"] + out["Monthly_Signal"]
    )
    return out


def attach_htf_to_result(result: ScanResult, output_dir: Optional[Path] = None, write_csv: bool = True) -> ScanResult:
    """Add weekly / monthly CPR from cached daily bhavcopies."""
    output_dir = Path(output_dir) if output_dir is not None else result.output_dir
    hist_dates = cached_history_dates(result.date, output_dir)
    if len(hist_dates) < 5:
        return result
    panel = load_history_panel(hist_dates, output_dir)
    if panel.empty:
        return result
    weekly, w_label = build_htf_frame(panel, result.date, "W-FRI", MIN_HISTORY_WEEKS)
    monthly, m_label = build_htf_frame(panel, result.date, "M", MIN_HISTORY_MONTHS)
    result.weekly = weekly
    result.monthly = monthly
    result.weekly_applies = w_label
    result.monthly_applies = m_label
    result.full = attach_confluence(result.full, weekly, monthly)
    if write_csv and "Confluence_Score" in result.full.columns:
        result = export_results(result.full, result.date, output_dir=output_dir, verbose=False)
        result.weekly = weekly
        result.monthly = monthly
        result.weekly_applies = w_label
        result.monthly_applies = m_label
    if write_csv:
        day_dir = session_dir(result.date, output_dir)
        day_dir.mkdir(parents=True, exist_ok=True)
        if not weekly.empty:
            weekly.to_csv(scan_csv_path("weekly", result.date, output_dir), index=False)
            print(f"✓ Weekly CPR ({w_label}): {len(weekly)} names")
        if not monthly.empty:
            monthly.to_csv(scan_csv_path("monthly", result.date, output_dir), index=False)
            print(f"✓ Monthly CPR ({m_label}): {len(monthly)} names")
    w_setups = int(weekly["Setup"].isin(["Long", "Short", "Watch"]).sum()) if not weekly.empty and "Setup" in weekly.columns else 0
    m_setups = int(monthly["Setup"].isin(["Long", "Short", "Watch"]).sum()) if not monthly.empty and "Setup" in monthly.columns else 0
    print(f"HTF: weekly {w_label or 'n/a'} setups {w_setups} | monthly {m_label or 'n/a'} setups {m_setups}")
    return result


def compute_cpr(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute CPR columns for a bhavcopy DataFrame.
    Required columns: SYMBOL, OPEN, HIGH, LOW, CLOSE
    """
    out = df.copy()
    for col in ["OPEN", "HIGH", "LOW", "CLOSE"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    canonical = calculate_cpr_frame(
        out,
        high_col="HIGH",
        low_col="LOW",
        close_col="CLOSE",
        narrow_max_pct=CPR_NARROW_MAX_PCT,
        wide_min_pct=CPR_WIDE_MIN_PCT,
    )
    out["Pivot"] = canonical["pivot"]
    out["BC"] = canonical["bc"]
    out["TC"] = canonical["tc"]
    out["CPR_Top"] = canonical["top"]
    out["CPR_Bottom"] = canonical["bottom"]
    out["CPR_Width"] = canonical["width"]
    out["CPR_Width_Pct"] = canonical["width_pct"]
    out["CPR_Class"] = canonical["width_class"]
    out["Bias"] = canonical["bias"]
    out["Price_Position"] = canonical["price_position"]
    return out


def tag_fo_symbols(cash_df: pd.DataFrame, fo_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Tag symbols as F&O or Cash-only."""
    out = cash_df.copy()
    if fo_df is not None and not fo_df.empty and "SYMBOL" in fo_df.columns:
        fo_symbols = set(fo_df["SYMBOL"].astype(str).str.strip().str.upper().unique())
        out["Segment"] = np.where(out["SYMBOL"].isin(fo_symbols), "F&O + Cash", "Cash Only")
    else:
        out["Segment"] = "Cash Only"
    return out


def apply_bullish_cpr_filters(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply Bullish CPR conditions:
    - Close above CPR
    - Bullish bias (Pivot > BC)
    - Narrow CPR (for breakout potential)
    """
    out = df.copy()
    out["Bullish_CPR"] = (
        (out["CLOSE"] > out["CPR_Top"])
        & (out["Pivot"] > out["BC"])
        & (out["CPR_Width_Pct"] <= CPR_NARROW_MAX_PCT)
    )
    out["Bearish_CPR"] = (
        (out["CLOSE"] < out["CPR_Bottom"])
        & (out["Pivot"] < out["BC"])
        & (out["CPR_Width_Pct"] <= BEARISH_WIDTH_PCT)
    )
    return out


DISPLAY_COLS = [
    "SYMBOL",
    "NAME",
    "Industry",
    "SERIES",
    "CLOSE",
    "Pivot",
    "BC",
    "TC",
    "CPR_Bottom",
    "CPR_Top",
    "CPR_Width",
    "CPR_Width_Pct",
    "Width_Rank_Pct",
    "CPR_Class",
    "Own_Narrow",
    "Overlay",
    "Setup",
    "Bias",
    "Price_Position",
    "Segment",
    "Nifty500",
    "History_Days",
    "History_OK",
    "Value_60d",
    "ATR14",
    "Width_ATR",
    "Value_Ratio",
    "Above_SMA50",
    "Above_SMA100",
    "Regime",
    "Applies",
    "Timeframe",
    "Bullish_CPR",
    "Bearish_CPR",
    "Daily_Signal",
    "Weekly_Signal",
    "Monthly_Signal",
    "Confluence_Score",
    "Signal_Direction",
    "Signal_Score",
    "Signal_Grade",
    "Signal_Explanation",
    "Strategy_Type",
    "Strategy_Setup",
    "Strategy_Confirmation",
    "Strategy_Explanation",
    "NR4",
    "NR7",
    "Virgin_CPR",
    "Triple_Confluence",
]

WEB_EXPORT_COLS = [
    "SYMBOL",
    "SERIES",
    "NAME",
    "Industry",
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "VOLUME",
    "VALUE",
    "Pivot",
    "BC",
    "TC",
    "CPR_Bottom",
    "CPR_Top",
    "CPR_Width",
    "CPR_Width_Pct",
    "Width_Rank_Pct",
    "CPR_Class",
    "Own_Narrow",
    "NR4",
    "NR7",
    "Virgin_CPR",
    "Triple_Confluence",
    "Overlay",
    "Setup",
    "Bias",
    "Price_Position",
    "Segment",
    "Nifty500",
    "History_Days",
    "History_OK",
    "Value_60d",
    "ATR14",
    "Width_ATR",
    "Value_Ratio",
    "Above_SMA50",
    "Above_SMA100",
    "Regime",
    "Applies",
    "Timeframe",
    "Bullish_CPR",
    "Bearish_CPR",
    "Daily_Signal",
    "Weekly_Signal",
    "Monthly_Signal",
    "Confluence_Score",
    "Signal_Direction",
    "Signal_Score",
    "Signal_Grade",
    "Signal_Explanation",
    "Strategy_Type",
    "Strategy_Setup",
    "Strategy_Confirmation",
    "Strategy_Explanation",
    "Next_Close",
    "Follow_Through",
]


def _present_cols(df: pd.DataFrame, cols: Optional[List[str]] = None) -> list:
    wanted = cols or DISPLAY_COLS
    return [c for c in wanted if c in df.columns]


def web_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Slim CPR columns for public download (not the raw bhavcopy dump)."""
    cols = _present_cols(df, WEB_EXPORT_COLS)
    return df.loc[:, cols].copy() if cols else df.copy()


def last_completed_session(now: Optional[datetime] = None) -> str:
    """Most recent weekday session. Before 16:15 IST, yesterday is still 'today'."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    d = now.date()
    if now.hour < 16 or (now.hour == 16 and now.minute < 15):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def candidate_session_dates(now: Optional[datetime] = None, max_back: int = 7) -> List[str]:
    """Weekday dates to try when the latest session is a holiday."""
    dates: List[str] = []
    first = datetime.strptime(last_completed_session(now), "%Y%m%d").date()
    cur = first
    while len(dates) < max_back:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y%m%d"))
        cur -= timedelta(days=1)
    return dates


def discover_scan_dates(output_dir: Optional[Path] = None) -> List[str]:
    output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    nested = {
        p.stem.split("_")[-1]
        for p in output_dir.rglob("cpr_full_*.csv")
        if p.stem.split("_")[-1].isdigit() and "bhavcopy" not in p.parts
    }
    # Legacy flat layout support during migration.
    flat = {
        p.stem.split("_")[-1]
        for p in output_dir.glob("cpr_full_*.csv")
        if p.stem.split("_")[-1].isdigit()
    }
    return sorted(nested | flat, reverse=True)


def load_scan_result(date: str, output_dir: Optional[Path] = None, previous: Optional[str] = None) -> ScanResult:
    """Rebuild a ScanResult from previously exported CSVs.

    Previous session's date is used for the follow-through tab; pass it explicitly
    (e.g. from discover_scan_dates) so large archives do not re-glob per date.
    """
    output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    full_path = resolve_scan_csv("full", date, output_dir)
    if not full_path.exists():
        raise FileNotFoundError(f"Missing {full_path}")
    full = pd.read_csv(full_path)
    for col in ("Bullish_CPR", "Bearish_CPR", "Own_Narrow", "History_OK", "Above_SMA50", "Above_SMA100", "Nifty500"):
        if col in full.columns:
            full[col] = full[col].astype(str).str.lower().isin(["true", "1", "yes"])
    if "Bullish_CPR" not in full.columns:
        full = apply_bullish_cpr_filters(full)
    full = keep_listed_equity(full, quiet=True)
    full = attach_industry(full)
    if "Setup" in full.columns:
        full["Setup"] = full["Setup"].fillna("No setup").replace({"None": "No setup", "nan": "No setup"})
    else:
        hist_dates = cached_history_dates(date, output_dir)
        if date in hist_dates:
            full = attach_history_features(
                full, load_history_panel(hist_dates, output_dir), own_window=HISTORY_LOOKBACK
            )
    if not set(SCORE_FIELDS).issubset(full.columns):
        full = attach_confirmation_score(full)
    if not set(WIDE_FIELDS).issubset(full.columns):
        full = attach_wide_strategy(full)
    _, narrow, bullish, bearish, top20 = split_shortlists(full)
    bullish_bias = full[full["Bias"] == "Bullish"].copy() if "Bias" in full.columns else pd.DataFrame()
    bullish_bias_path = resolve_scan_csv("bullish_bias", date, output_dir)
    if bullish_bias_path.exists():
        bullish_bias = pd.read_csv(bullish_bias_path)
    fo_available = "Segment" in full.columns and bool((full["Segment"] == "F&O + Cash").any())
    best = pd.DataFrame()
    watchlist = pd.DataFrame()
    wide = pd.DataFrame()
    best_path = resolve_scan_csv("best", date, output_dir)
    watch_path = resolve_scan_csv("watchlist", date, output_dir)
    if best_path.exists():
        best = pd.read_csv(best_path)
    if best.empty or not set(SCORE_FIELDS).issubset(best.columns):
        best = compute_best(full)
    if watch_path.exists():
        watchlist = pd.read_csv(watch_path)
    if watchlist.empty or not set(SCORE_FIELDS).issubset(watchlist.columns):
        watchlist = compute_watchlist(full)
    wide_path = resolve_scan_csv("wide", date, output_dir)
    if wide_path.exists():
        wide = pd.read_csv(wide_path)
    if wide.empty or not set(WIDE_FIELDS).issubset(wide.columns):
        wide = wide_table(full)
    result = ScanResult(
        date=date,
        cash_rows=len(full),
        fo_available=fo_available,
        full=full,
        narrow=narrow,
        bullish=bullish,
        bearish=bearish,
        top20=top20,
        output_dir=output_dir,
        best=best,
        watchlist=watchlist,
        wide=wide,
        bullish_bias=bullish_bias,
    )
    weekly_path = resolve_scan_csv("weekly", date, output_dir)
    monthly_path = resolve_scan_csv("monthly", date, output_dir)
    if weekly_path.exists():
        result.weekly = pd.read_csv(weekly_path)
        if "Applies" in result.weekly.columns and not result.weekly.empty:
            result.weekly_applies = str(result.weekly["Applies"].iloc[0])
    if monthly_path.exists():
        result.monthly = pd.read_csv(monthly_path)
        if "Applies" in result.monthly.columns and not result.monthly.empty:
            result.monthly_applies = str(result.monthly["Applies"].iloc[0])
    previous = previous or [d for d in discover_scan_dates(output_dir) if d < date]
    if previous and isinstance(previous, list):
        previous = previous[0] if previous else None
    if previous:
        prev_path = resolve_scan_csv("full", previous, output_dir)
        if prev_path.exists() and "Setup" in result.full.columns:
            prev_full = pd.read_csv(prev_path)
            try:
                result.follow_through = follow_through(prev_full, result.full)
            except Exception:
                result.follow_through = pd.DataFrame()
    return result


def _liquid_enough(df: pd.DataFrame) -> pd.Series:
    """Median turnover over the last ~60 sessions, not the single session's VALUE spike."""
    if "Value_60d" in df.columns:
        value = pd.to_numeric(df["Value_60d"], errors="coerce")
        if value.notna().any():
            return value >= MIN_VALUE_TOP20
    return pd.Series(True, index=df.index)


def compute_best(df: pd.DataFrame, n: int = 25) -> pd.DataFrame:
    """'Best today' — active Long/Short daily setups, ranked by |confluence| (multi-TF
    agreement) preferring F&O names, liquid by median turnover."""
    cols = [
        c
        for c in [
            "SYMBOL",
            "Industry",
            "CLOSE",
            "CPR_Top",
            "CPR_Bottom",
            "CPR_Width_Pct",
            "Width_Rank_Pct",
            "Overlay",
            "Setup",
            "Bias",
            "Segment",
            "Confluence_Score",
            "Signal_Direction",
            "Signal_Score",
            "Signal_Grade",
            "Signal_Explanation",
            "Strategy_Type",
            "Strategy_Setup",
            "Strategy_Confirmation",
            "Strategy_Explanation",
            "Regime",
        ]
        if c in df.columns
    ]
    if "Setup" not in df.columns:
        return pd.DataFrame(columns=cols)
    setup_rows = df[df["Setup"].isin(["Long", "Short"])].copy()
    if setup_rows.empty:
        setup_rows = df[df["Setup"].isin(["Watch Long", "Watch Short", "Watch"])].copy()
    if setup_rows.empty:
        return pd.DataFrame(columns=cols)
    liquid = _liquid_enough(setup_rows).reindex(setup_rows.index).fillna(False)
    if liquid.any():
        setup_rows = setup_rows.loc[liquid]
    sort_by = "Signal_Score" if "Signal_Score" in setup_rows.columns else ("Confluence_Score" if "Confluence_Score" in setup_rows.columns else "Width_Rank_Pct")
    extra = []
    if sort_by in setup_rows.columns:
        setup_rows["_key"] = setup_rows[sort_by].abs()
        setup_rows["_fo"] = setup_rows.get("Segment", pd.Series(False, index=setup_rows.index)) == "F&O + Cash"
        setup_rows = setup_rows.sort_values(["_key", "_fo"], ascending=[False, False], na_position="last")
        extra = ["_key", "_fo"]
    best = setup_rows.head(n)[cols + extra]
    return best.drop(columns=extra, errors="ignore").reset_index(drop=True)


def compute_watchlist(df: pd.DataFrame) -> pd.DataFrame:
    """Weekend watchlist — every setup name with the levels to trade next session."""
    cols = [
        c
        for c in [
            "SYMBOL",
            "Industry",
            "CLOSE",
            "Pivot",
            "BC",
            "TC",
            "CPR_Top",
            "CPR_Bottom",
            "CPR_Width_Pct",
            "Width_Rank_Pct",
            "Overlay",
            "Setup",
            "Bias",
            "Price_Position",
            "Segment",
            "Confluence_Score",
            "Signal_Direction",
            "Signal_Score",
            "Signal_Grade",
            "Signal_Explanation",
            "Strategy_Type",
            "Strategy_Setup",
            "Strategy_Confirmation",
            "Strategy_Explanation",
            "Regime",
        ]
        if c in df.columns
    ]
    if "Setup" not in df.columns:
        return pd.DataFrame(columns=cols)
    rows = df[df["Setup"].isin(WATCHLIST_SETUPS)].copy()
    if rows.empty:
        return pd.DataFrame(columns=cols)
    sort_cols = [c for c in ("Signal_Score", "Confluence_Score") if c in rows.columns]
    if sort_cols:
        rows = rows.sort_values(sort_cols, ascending=[False] * len(sort_cols), na_position="last")
    return rows[cols].reset_index(drop=True)


def follow_through(prev_full: pd.DataFrame, cur_full: pd.DataFrame) -> pd.DataFrame:
    """Did yesterday's setups work today? For each setup stock, next close vs its
    CPR band from the day the setup fired. Followed / Flat / Failed by direction."""
    cols = [
        c
        for c in [
            "SYMBOL",
            "Industry",
            "Setup",
            "CLOSE",
            "CPR_Top",
            "CPR_Bottom",
            "CPR_Width_Pct",
            "Width_Rank_Pct",
            "Segment",
        ]
        if c in prev_full.columns
    ]
    prev = prev_full[prev_full["Setup"].isin(WATCHLIST_SETUPS)].copy()
    if prev.empty:
        return pd.DataFrame()
    prev = prev[cols].copy()
    cur = cur_full[["SYMBOL", "CLOSE"]].copy()
    cur = cur.rename(columns={"CLOSE": "Next_Close"})
    prev["SYMBOL"] = prev["SYMBOL"].astype(str).str.strip().str.upper()
    merged = prev.merge(cur, on="SYMBOL", how="left")
    up = merged["Setup"].isin(["Long", "Watch Long"])
    down = merged["Setup"].isin(["Short", "Watch Short"])
    merged["Follow_Through"] = "No data"
    merged.loc[up, "Follow_Through"] = np.select(
        [
            merged.loc[up, "Next_Close"] > merged.loc[up, "CPR_Top"],
            merged.loc[up, "Next_Close"] < merged.loc[up, "CPR_Bottom"],
        ],
        ["Followed", "Failed"],
        default="Flat",
    )
    merged.loc[down, "Follow_Through"] = np.select(
        [
            merged.loc[down, "Next_Close"] < merged.loc[down, "CPR_Bottom"],
            merged.loc[down, "Next_Close"] > merged.loc[down, "CPR_Top"],
        ],
        ["Followed", "Failed"],
        default="Flat",
    )
    merged["Next_Close"] = pd.to_numeric(merged["Next_Close"], errors="coerce")
    return merged.reset_index(drop=True)


def generate_monthly_commentary(row: Any) -> str:
    """Generate professional, actionable CPR trade commentary."""
    cpr_class = str(row.get("CPR_Class", "Moderate"))
    width_pct = float(row.get("CPR_Width_Pct", 1.0) or 1.0)
    pivot = float(row.get("Pivot", 0.0) or 0.0)
    cpr_top = float(row.get("CPR_Top", 0.0) or 0.0)
    cpr_bot = float(row.get("CPR_Bottom", 0.0) or 0.0)
    pos = str(row.get("Current_Position_LTP", row.get("Price_Position", "Above CPR")))
    vol_ratio = float(row.get("Value_Ratio", 1.0) or 1.0)
    chg = float(row.get("DAY_CHG_PCT", 0.0) or 0.0)
    cat = str(row.get("CATEGORY", "Narrow CPR - Breakout"))

    if width_pct < 0.3:
        cpr_desc = f"Ultra-tight {cpr_class} CPR ({width_pct:.2f}%)"
    elif width_pct < 0.75:
        cpr_desc = f"Narrow/Moderate CPR ({width_pct:.2f}%)"
    elif width_pct < 2.0:
        cpr_desc = f"Moderate CPR ({width_pct:.2f}%)"
    else:
        cpr_desc = f"Wide CPR ({width_pct:.2f}%)"

    if vol_ratio >= 10.0:
        vol_desc = f"massive {vol_ratio:.1f}x volume explosion"
    elif vol_ratio >= 3.0:
        vol_desc = f"strong {vol_ratio:.1f}x volume surge"
    elif vol_ratio >= 1.5:
        vol_desc = f"above-average {vol_ratio:.1f}x volume"
    else:
        vol_desc = f"{vol_ratio:.1f}x average volume"

    if pos == "Above CPR":
        if chg > 0:
            action = f"Trading strong above monthly CPR Top (₹{cpr_top:,.2f}) with +{chg:.2f}% gain on Day 1. Look for bullish continuation above ₹{cpr_top:,.2f} with target expansion."
        else:
            action = f"Consolidating above monthly CPR Top (₹{cpr_top:,.2f}). Hold/buy on pullbacks holding above Pivot (₹{pivot:,.2f})."
    elif pos == "Below CPR":
        if width_pct < 0.5:
            action = f"Currently testing below CPR Bottom (₹{cpr_bot:,.2f}). Due to tight compression, watch for a sharp reclaim above ₹{cpr_top:,.2f} for a false breakdown reversal or breakdown continuation below ₹{cpr_bot:,.2f}."
        else:
            action = f"Trading below monthly CPR Bottom (₹{cpr_bot:,.2f}) ({chg:+.2f}%). Needs a clear close above Pivot (₹{pivot:,.2f}) to reverse bearish pressure."
    else:
        action = f"Oscillating inside the monthly CPR band (₹{cpr_bot:,.2f} - ₹{cpr_top:,.2f}). Wait for a decisive breakout above ₹{cpr_top:,.2f} or breakdown below ₹{cpr_bot:,.2f}."

    if "Narrow" in cat:
        return f"{cpr_desc} with {vol_desc}. {action}"
    else:
        return f"{vol_desc} backed by monthly bullish structure. {action}"


def compute_monthly_top_watchlist(
    monthly_df: pd.DataFrame,
    n: int = 20,
    daily_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Compute Top N (e.g. 20 or 100) Monthly CPR Watchlist with dual-track scoring & commentary."""
    if monthly_df is None or monthly_df.empty:
        return pd.DataFrame()
    
    df = monthly_df.copy()
    if "History_OK" in df.columns:
        df = df[df["History_OK"].astype(bool)]
    if "VALUE" in df.columns:
        df = df[df["VALUE"] > 1e7]
    if "CLOSE" in df.columns:
        df = df[df["CLOSE"] > 5]
    if df.empty:
        return pd.DataFrame()

    def _pct_rank(s: pd.Series, ascending: bool = True) -> pd.Series:
        return s.rank(pct=True, ascending=ascending) * 100

    # Track A: Narrow CPR
    is_narrow = (df.get("CPR_Class") == "Narrow") | (df.get("Own_Narrow") == True)
    narrow = df[is_narrow].copy()
    if not narrow.empty:
        w_rank = narrow["Width_Rank_Pct"] if "Width_Rank_Pct" in narrow.columns else narrow["CPR_Width_Pct"]
        narrow["_score"] = (100 - _pct_rank(w_rank)) * 0.40
        if "VALUE" in narrow.columns:
            narrow["_score"] += _pct_rank(narrow["VALUE"]) * 0.30
        if "Value_Ratio" in narrow.columns:
            narrow["_score"] += _pct_rank(narrow["Value_Ratio"].clip(upper=5)) * 0.20
        if "Nifty500" in narrow.columns:
            narrow["_score"] += narrow["Nifty500"].map({True: 10, False: 0}).fillna(0)
        narrow["CATEGORY"] = "Narrow CPR - Breakout"
        min_s, max_s = narrow["_score"].min(), narrow["_score"].max()
        narrow["UNIFIED_SCORE"] = 100.0 if max_s == min_s else ((narrow["_score"] - min_s) / (max_s - min_s) * 100.0)

    # Track B: Above CPR + Volume
    is_above = (df.get("Price_Position") == "Above CPR") & (df.get("Bias") == "Bullish")
    if "Value_Ratio" in df.columns:
        is_above = is_above & (df["Value_Ratio"] > 1.0)
    above = df[is_above].copy()
    if not above.empty:
        above["_score"] = 0.0
        if "Value_Ratio" in above.columns:
            above["_score"] += _pct_rank(above["Value_Ratio"].clip(upper=10)) * 0.35
        if "VALUE" in above.columns:
            above["_score"] += _pct_rank(above["VALUE"]) * 0.30
        if "Overlay" in above.columns:
            above["_score"] += above["Overlay"].map({"Higher": 20, "Inside": 10, "Lower": 0, "Below": 0}).fillna(0)
        if "Nifty500" in above.columns:
            above["_score"] += above["Nifty500"].map({True: 10, False: 0}).fillna(0)
        above["CATEGORY"] = "Above CPR - Volume Strength"
        min_s, max_s = above["_score"].min(), above["_score"].max()
        above["UNIFIED_SCORE"] = 100.0 if max_s == min_s else ((above["_score"] - min_s) / (max_s - min_s) * 100.0)

    combined = pd.concat([narrow, above], ignore_index=True) if not (narrow.empty and above.empty) else df.copy()
    if "UNIFIED_SCORE" in combined.columns:
        combined = combined.sort_values("UNIFIED_SCORE", ascending=False)
    combined = combined.drop_duplicates(subset=["SYMBOL"], keep="first")
    top_n = combined.head(n).reset_index(drop=True)
    top_n.insert(0, "Rank", top_n.index + 1)

    # Attach LTP and session stats if daily_df is provided
    if daily_df is not None and not daily_df.empty:
        daily_cols = ["SYMBOL", "CLOSE", "LAST", "OPEN", "HIGH", "LOW", "VOLUME", "VALUE"]
        available_cols = [c for c in daily_cols if c in daily_df.columns]
        d_sub = daily_df[available_cols].copy()
        if "LAST" in d_sub.columns:
            d_sub["LTP"] = d_sub["LAST"].fillna(d_sub.get("CLOSE", np.nan))
        elif "CLOSE" in d_sub.columns:
            d_sub["LTP"] = d_sub["CLOSE"]
        
        top_n = top_n.rename(columns={"CLOSE": "AUG_CLOSE"})
        top_n = top_n.merge(d_sub[["SYMBOL", "LTP"]], on="SYMBOL", how="left")
        top_n["LTP"] = top_n["LTP"].fillna(top_n.get("AUG_CLOSE", np.nan))
    else:
        top_n["AUG_CLOSE"] = top_n["CLOSE"] if "CLOSE" in top_n.columns else np.nan
        top_n["LTP"] = top_n["AUG_CLOSE"]

    if "AUG_CLOSE" in top_n.columns and "LTP" in top_n.columns:
        top_n["DAY_CHG_PCT"] = ((top_n["LTP"] - top_n["AUG_CLOSE"]) / top_n["AUG_CLOSE"] * 100).round(2)

    def _get_cur_pos(r):
        ltp = r.get("LTP")
        tc = r.get("CPR_Top")
        bc = r.get("CPR_Bottom")
        if pd.isna(ltp) or pd.isna(tc) or pd.isna(bc):
            return r.get("Price_Position", "Above CPR")
        if ltp > tc:
            return "Above CPR"
        if ltp < bc:
            return "Below CPR"
        return "Inside CPR"

    top_n["Current_Position_LTP"] = top_n.apply(_get_cur_pos, axis=1)
    top_n["Commentary"] = top_n.apply(generate_monthly_commentary, axis=1)
    return top_n


def bullish_bias_view(df: pd.DataFrame) -> pd.DataFrame:
    """Return every row with bullish CPR geometry, independent of width/close breakout filters.

    This is intentionally separate from ``cpr_bullish_*.csv`` / ``Bullish_CPR``:
    the latter is the strict narrow, above-band breakout shortlist.
    """
    if "Bias" not in df.columns:
        return df.iloc[0:0].copy()
    return df[df["Bias"].astype(str).str.strip().eq("Bullish")].copy().reset_index(drop=True)


def split_shortlists(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    full_table = df.sort_values(["Confluence_Score", "CPR_Width_Pct"], ascending=[False, True], na_position="last").reset_index(drop=True) if "Confluence_Score" in df.columns else df.sort_values("CPR_Width_Pct").reset_index(drop=True)
    narrow = df[df["CPR_Class"] == "Narrow"].sort_values("CPR_Width_Pct").reset_index(drop=True)
    bullish = df[df["Bullish_CPR"]].sort_values("CPR_Width_Pct").reset_index(drop=True)
    bearish = df[df["Bearish_CPR"]].sort_values("CPR_Width_Pct", ascending=False).reset_index(drop=True)
    top_cols = [
        c
        for c in [
            "SYMBOL",
            "Industry",
            "CLOSE",
            "Pivot",
            "BC",
            "TC",
            "CPR_Width_Pct",
            "Width_Rank_Pct",
            "Overlay",
            "Setup",
            "Own_Narrow",
            "Bias",
            "Price_Position",
            "Segment",
            "Confluence_Score",
            "Signal_Direction",
            "Signal_Score",
            "Signal_Grade",
            "Signal_Explanation",
            "Strategy_Type",
            "Strategy_Setup",
            "Strategy_Confirmation",
            "Strategy_Explanation",
        ]
        if c in df.columns
    ]
    ranked = pd.DataFrame(columns=df.columns)
    if "Setup" in df.columns:
        ranked = df[df["Setup"].isin(["Long", "Short", "Watch Long", "Watch Short", "Watch"])]
        if not ranked.empty:
            liquid_mask = _liquid_enough(ranked).reindex(ranked.index).fillna(False)
            liquid = ranked.loc[liquid_mask]
            if not liquid.empty:
                ranked = liquid
    if ranked.empty and "Own_Narrow" in df.columns:
        ranked = df[df["Own_Narrow"].astype(bool)]
    if ranked.empty:
        ranked = narrow[narrow["CPR_Width_Pct"] > 0] if "CPR_Width_Pct" in narrow.columns else narrow
    sort_col = "Confluence_Score" if "Confluence_Score" in ranked.columns else ("Width_Rank_Pct" if "Width_Rank_Pct" in ranked.columns else "CPR_Width_Pct")
    if not ranked.empty and sort_col in ranked.columns:
        ranked = ranked.sort_values(sort_col, ascending=False, na_position="last")
    top20 = ranked.head(20)[top_cols].reset_index(drop=True) if not ranked.empty else pd.DataFrame(columns=top_cols)
    return full_table, narrow, bullish, bearish, top20


def export_results(
    df: pd.DataFrame,
    date: str,
    output_dir: Optional[Path] = None,
    verbose: bool = True,
) -> ScanResult:
    """Export ranked tables and shortlists."""
    output_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    day_dir = session_dir(date, output_dir)
    day_dir.mkdir(parents=True, exist_ok=True)
    # Compute after the current-session and higher-timeframe fields available to
    # this export path are attached. Existing setup/filter membership is unchanged.
    df = attach_confirmation_score(df)
    df = attach_wide_strategy(df)
    full_table, narrow, bullish, bearish, top20 = split_shortlists(df)
    bullish_bias = bullish_bias_view(df)

    paths = {
        "full": scan_csv_path("full", date, output_dir),
        "narrow": scan_csv_path("narrow", date, output_dir),
        "bullish": scan_csv_path("bullish", date, output_dir),
        "bullish_bias": scan_csv_path("bullish_bias", date, output_dir),
        "bearish": scan_csv_path("bearish", date, output_dir),
        "top20_narrow": scan_csv_path("top20_narrow", date, output_dir),
        "best": scan_csv_path("best", date, output_dir),
    }
    full_table.to_csv(paths["full"], index=False)
    narrow.to_csv(paths["narrow"], index=False)
    bullish.to_csv(paths["bullish"], index=False)
    bullish_bias.to_csv(paths["bullish_bias"], index=False)
    bearish.to_csv(paths["bearish"], index=False)
    top20.to_csv(paths["top20_narrow"], index=False)
    best = compute_best(df)
    best.to_csv(paths["best"], index=False)
    watchlist = compute_watchlist(df)
    watch_path = scan_csv_path("watchlist", date, output_dir)
    if not watchlist.empty:
        watchlist.to_csv(watch_path, index=False)
    wide = wide_table(df)
    wide_path = scan_csv_path("wide", date, output_dir)
    if not wide.empty:
        wide.to_csv(wide_path, index=False)
    try:
        save_session_parquet(full_table, date, output_dir)
    except Exception:
        pass
    if verbose:
        print(f"✓ Full table: {paths['full']}")
        print(f"✓ Narrow CPR: {len(narrow)} symbols → {paths['narrow']}")
        print(f"✓ Bullish CPR: {len(bullish)} symbols → {paths['bullish']}")
        print(f"✓ Bullish Bias: {len(bullish_bias)} symbols → {paths['bullish_bias']}")
        print(f"✓ Bearish CPR: {len(bearish)} symbols → {paths['bearish']}")
        print(f"✓ Top 20 Narrow: {paths['top20_narrow']}")
        print(f"✓ Best today: {len(best)} symbols → {paths['best']}")
        if not watchlist.empty:
            print(f"✓ Watchlist: {len(watchlist)} symbols → {watch_path}")
        print(f"✓ Wide CPR: {len(wide)} symbols → {wide_path}")

    return ScanResult(
        date=date,
        cash_rows=len(df),
        fo_available="Segment" in df.columns and (df["Segment"] == "F&O + Cash").any(),
        full=full_table,
        narrow=narrow,
        bullish=bullish,
        bearish=bearish,
        top20=top20,
        output_dir=output_dir,
        best=best,
        watchlist=watchlist,
        wide=wide,
        bullish_bias=bullish_bias,
    )


def scan_eod_cpr(
    date: str,
    output_dir: Optional[Path] = None,
    write_csv: bool = True,
    lookback: int = HISTORY_LOOKBACK_HTF,
) -> ScanResult:
    """Download bhavcopies, compute CPR, attach history features, optionally write CSVs."""
    session = _nse_session()
    try:
        cash_raw = download_bhavcopy(CASH_URL, date, session=session)
        if cash_raw is None:
            raise RuntimeError("Failed to download cash bhavcopy.")
        cash_df = normalize_bhavcopy(cash_raw, cash_only=True)
        print(f"Cash bhavcopy: {len(cash_raw)} rows → {len(cash_df)} EQ symbols")
        cash_df = keep_listed_equity(cash_df)

        fo_raw = download_bhavcopy(FO_URL, date, session=session)
        fo_df = None
        if fo_raw is not None:
            fo_df = normalize_bhavcopy(fo_raw, cash_only=False)
            print(f"F&O bhavcopy: {len(fo_raw)} rows → {fo_df['SYMBOL'].nunique()} unique symbols")
        else:
            print("F&O bhavcopy not available (weekend/holiday?)")

        cash_df = tag_fo_symbols(cash_df, fo_df)
        cash_df = attach_industry(cash_df, fetch=True)
        cash_df = compute_cpr(cash_df)
        cash_df = apply_bullish_cpr_filters(cash_df)

        seed_bhavcopy_cache(cash_df, date, output_dir=output_dir)
        if lookback and lookback > 0:
            hist_dates = ensure_bhavcopy_history(
                date, lookback=lookback, output_dir=output_dir, session=session
            )
            cash_df = attach_history_features(
                cash_df, load_history_panel(hist_dates, output_dir), own_window=HISTORY_LOOKBACK
            )
            setups = int((cash_df["Setup"].isin(["Long", "Short", "Watch"])).sum()) if "Setup" in cash_df.columns else 0
            own_n = int(cash_df["Own_Narrow"].sum()) if "Own_Narrow" in cash_df.columns else 0
            print(f"History features: Own_Narrow {own_n} | Setups {setups}")

        if write_csv:
            result = export_results(cash_df, date, output_dir=output_dir)
            result = attach_htf_to_result(result, output_dir=output_dir, write_csv=True)
            if lookback and lookback > 0:
                backfill_cached_scans(date, output_dir=output_dir, lookback=lookback, skip_existing=True)
                backfill_htf_scans(date, output_dir=output_dir, lookback=lookback)
            return result

        full_table, narrow, bullish, bearish, top20 = split_shortlists(cash_df)
        result = ScanResult(
            date=date,
            cash_rows=len(cash_df),
            fo_available=fo_df is not None,
            full=full_table,
            narrow=narrow,
            bullish=bullish,
            bearish=bearish,
            top20=top20,
            output_dir=Path(output_dir) if output_dir is not None else OUTPUT_DIR,
        )
        return attach_htf_to_result(result, output_dir=output_dir, write_csv=False)
    finally:
        session.close()


def main(argv: Optional[List[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("Usage: python nse_cpr_scanner.py YYYYMMDD [--lookback 252]")
        print("       python nse_cpr_scanner.py --backfill YYYYMMDD")
        print("Example: python nse_cpr_scanner.py 20260813")
        sys.exit(0 if argv and argv[0] in ("-h", "--help") else 1)

    lookback = HISTORY_LOOKBACK_HTF
    if "--lookback" in argv:
        idx = argv.index("--lookback")
        try:
            lookback = int(argv[idx + 1])
        except (IndexError, ValueError):
            print("--lookback needs an integer, e.g. --lookback 252")
            sys.exit(1)

    if argv[0] == "--backfill":
        date = argv[1] if len(argv) > 1 and argv[1] != "--lookback" else last_completed_session()
        try:
            datetime.strptime(date, "%Y%m%d")
        except ValueError:
            print("Date must be YYYYMMDD, e.g. 20260813")
            sys.exit(1)
        print(f"=== Backfill archive scans through {date} ===\n")
        dates = backfill_cached_scans(date, lookback=lookback, skip_existing=True)
        print(f"Sessions available: {len(dates)}")
        return

    date = argv[0]
    print(f"=== NSE EOD CPR Scanner for {date} ===\n")
    try:
        datetime.strptime(date, "%Y%m%d")
    except ValueError:
        print("Date must be YYYYMMDD, e.g. 20260813")
        sys.exit(1)

    try:
        result = scan_eod_cpr(date, lookback=lookback)
    except Exception as exc:
        print(f"Scan failed: {exc}")
        sys.exit(1)

    print("\n=== Scan Complete ===")
    print(f"EQ symbols: {result.cash_rows}")
    print(f"Narrow: {len(result.narrow)} | Bullish: {len(result.bullish)} | Bearish: {len(result.bearish)}")
    print(f"Output directory: {result.output_dir.resolve()}")


if __name__ == "__main__":
    main()
