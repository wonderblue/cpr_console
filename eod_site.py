"""
Static website for daily NSE EOD CPR scans.

Builds HTML + CSV/ZIP downloads from `cpr_output/`. Does not change the
Shah CPR console or the breakout screener.

Usage:
    python eod_site.py                  # rebuild from existing CSVs
    python eod_site.py --serve 8504     # rebuild and preview locally
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import zipfile
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

from nse_cpr_scanner import (
    IST,
    ScanResult,
    WEB_EXPORT_COLS,
    attach_htf_to_result,
    discover_scan_dates,
    load_scan_result,
    web_frame,
)
from publication_contract import read_manifest

SITE_DIR = Path("site")
DEFAULT_PUBLISHED_SESSIONS = 20
ROUND_2 = {
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "Pivot",
    "BC",
    "TC",
    "CPR_Bottom",
    "CPR_Top",
    "CPR_Width",
    "ATR14",
    "SMA50",
    "SMA100",
    "Next_Close",
    "DAY_CHG_PCT",
}
TABLE_COLS = [
    "SYMBOL",
    "DAY_CHG_PCT",
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
    "Overlay",
    "Setup",
    "Bias",
    "Price_Position",
    "Segment",
    "Nifty500",
    "History_Days",
    "Value_60d",
    "ATR14",
    "Width_ATR",
    "Value_Ratio",
    "Above_SMA50",
    "Above_SMA100",
    "Regime",
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
    "Applies",
    "Follow_Through",
    "Next_Close",
]


def _date_label(date: str) -> str:
    return datetime.strptime(date, "%Y%m%d").strftime("%d %b %Y")


def _records(df: pd.DataFrame) -> list:
    frame = web_frame(df)
    cols = [c for c in TABLE_COLS if c in frame.columns]
    out = []
    for rec_in in frame.loc[:, cols].to_dict(orient="records"):
        rec = {}
        for col in cols:
            val = rec_in.get(col)
            if pd.isna(val):
                rec[col] = None
            elif col in ROUND_2:
                rec[col] = round(float(val), 2)
            elif col == "CPR_Width_Pct":
                rec[col] = round(float(val), 4)
            elif col == "Width_Rank_Pct":
                rec[col] = round(float(val), 3)
            elif col == "Value_Ratio":
                rec[col] = round(float(val), 2)
            elif col in ("Bullish_CPR", "Bearish_CPR", "Own_Narrow", "History_OK", "Above_SMA50", "Above_SMA100", "Nifty500"):
                rec[col] = bool(val)
            else:
                rec[col] = val
        out.append(rec)
    return out


def _write_downloads(result: ScanResult, dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    mapping = {
        "full": ("cpr_full.csv", result.full),
        "narrow": ("cpr_narrow.csv", result.narrow),
        "bullish": ("cpr_bullish.csv", result.bullish),
        "bullish_bias": ("cpr_bullish_bias.csv", result.bullish_bias),
        "bearish": ("cpr_bearish.csv", result.bearish),
        "top20": ("cpr_top20_narrow.csv", result.top20),
        "best": ("cpr_best.csv", result.best),
        "watchlist": ("cpr_watchlist.csv", result.watchlist),
        "weekly": ("cpr_weekly.csv", result.weekly),
        "monthly": ("cpr_monthly.csv", result.monthly),
    }
    if not result.wide.empty:
        mapping["wide"] = ("cpr_wide.csv", result.wide)
    files = []
    for key, (name, frame) in mapping.items():
        path = dest / name
        web_frame(frame).to_csv(path, index=False)
        files.append(path)

    zip_name = f"cpr_{result.date}.zip"
    zip_path = dest / zip_name
    readme = dest / "README.txt"
    readme.write_text(
        "\n".join(
            [
                f"NSE EOD CPR scan — session {result.date} ({_date_label(result.date)})",
                "CPR from that session's H/L/C applies to the next session.",
                "Weekly CPR applies to the next week after the last completed Friday week.",
                "Monthly CPR applies to the next month after the last completed calendar month.",
                "Research / educational use. Not investment advice.",
                "OHLC sourced from NSE UDI bhavcopy. Not an NSE product.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(readme, "README.txt")
        for path in files:
            zf.write(path, path.name)
    readme.unlink()
    return {
        "full": "downloads/cpr_full.csv",
        "narrow": "downloads/cpr_narrow.csv",
        "bullish": "downloads/cpr_bullish.csv",
        "bullish_bias": "downloads/cpr_bullish_bias.csv",
        "bearish": "downloads/cpr_bearish.csv",
        "top20": "downloads/cpr_top20_narrow.csv",
        "best": "downloads/cpr_best.csv",
        "watchlist": "downloads/cpr_watchlist.csv",
        "weekly": "downloads/cpr_weekly.csv",
        "monthly": "downloads/cpr_monthly.csv",
        "wide": "downloads/cpr_wide.csv" if not result.wide.empty else None,
        "zip": f"downloads/{zip_name}",
    }


def _payload(result: ScanResult, downloads: dict, dates: Iterable[str], home_href: str, publication: Optional[dict] = None) -> dict:
    fo_n = int((result.full["Segment"] == "F&O + Cash").sum()) if "Segment" in result.full.columns else 0
    industries = []
    if "Industry" in result.full.columns:
        industries = sorted(result.full["Industry"].dropna().astype(str).unique().tolist())
    own_n = int(result.full["Own_Narrow"].sum()) if "Own_Narrow" in result.full.columns else 0
    setups = int(result.full["Setup"].isin(["Long", "Short", "Watch Long", "Watch Short", "Watch"]).sum()) if "Setup" in result.full.columns else 0
    w_setups = int(result.weekly["Setup"].isin(["Long", "Short", "Watch Long", "Watch Short", "Watch"]).sum()) if not result.weekly.empty and "Setup" in result.weekly.columns else 0
    m_setups = int(result.monthly["Setup"].isin(["Long", "Short", "Watch Long", "Watch Short", "Watch"]).sum()) if not result.monthly.empty and "Setup" in result.monthly.columns else 0
    regime = ""
    if "Regime" in result.full.columns:
        regimes = result.full["Regime"].dropna().astype(str)
        if not regimes.empty:
            regime = regimes.value_counts().idxmax()
    # Top 25 Gainers & Top 25 Losers
    df_movers = result.full.copy()
    if "PREVCLOSE" in df_movers.columns and "CLOSE" in df_movers.columns:
        prev_c = pd.to_numeric(df_movers["PREVCLOSE"], errors="coerce")
        close_c = pd.to_numeric(df_movers["CLOSE"], errors="coerce")
        df_movers["DAY_CHG_PCT"] = ((close_c - prev_c) / prev_c * 100).round(2)
    elif "OPEN" in df_movers.columns and "CLOSE" in df_movers.columns:
        op = pd.to_numeric(df_movers["OPEN"], errors="coerce")
        close_c = pd.to_numeric(df_movers["CLOSE"], errors="coerce")
        df_movers["DAY_CHG_PCT"] = ((close_c - op) / op * 100).round(2)
    else:
        df_movers["DAY_CHG_PCT"] = 0.0

    gainers_df = df_movers.sort_values("DAY_CHG_PCT", ascending=False).head(25)
    losers_df = df_movers.sort_values("DAY_CHG_PCT", ascending=True).head(25)

    return {
        "date": result.date,
        "label": _date_label(result.date),
        "home": home_href,
        "dates": [{"id": d, "label": _date_label(d)} for d in dates],
        "industries": industries,
        "htf": {
            "weekly_applies": result.weekly_applies or "",
            "monthly_applies": result.monthly_applies or "",
            "weekly_setups": w_setups,
            "monthly_setups": m_setups,
        },
        "metrics": {
            "symbols": int(result.cash_rows),
            "narrow": int(len(result.narrow)),
            "own_narrow": own_n,
            "setups": setups,
            "bullish": int(len(result.bullish)),
            "fo": fo_n,
            "regime": regime,
        },
        "downloads": downloads,
        "publication": publication or {},
        "tables": {
            "full": _records(result.full),
            "gainers": _records(gainers_df),
            "losers": _records(losers_df),
            "narrow": _records(result.narrow),
            "bullish": _records(result.bullish),
            "bullish_bias": _records(result.bullish_bias),
            "bearish": _records(result.bearish),
            "top20": _records(result.top20),
            "best": _records(result.best) if not result.best.empty else [],
            "watchlist": _records(result.watchlist) if not result.watchlist.empty else [],
            "wide": _records(result.wide) if not result.wide.empty else [],
            "follow": _records(result.follow_through) if not result.follow_through.empty else [],
            "weekly": _records(result.weekly) if not result.weekly.empty else [],
            "monthly": _records(result.monthly) if not result.monthly.empty else [],
        },
    }


def _industry_options(payload: dict) -> str:
    parts = ['<option value="Any">Any industry</option>']
    for name in payload.get("industries") or []:
        safe = html.escape(str(name), quote=True)
        parts.append(f'<option value="{safe}">{safe}</option>')
    return "\n      ".join(parts)


def _page_html(payload: dict, asset_prefix: str) -> str:
    industry_opts = _industry_options(payload)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="data-session" content="{html.escape(payload["date"], quote=True)}"/>
  <title>EOD CPR · {html.escape(payload["label"])}</title>
  <base href="{asset_prefix}">
  <link rel="stylesheet" href="assets/style.css?v=8"/>
  <script src="assets/lightweight-charts.standalone.production.js"></script>
</head>
<body>
  <!-- Toast Notification -->
  <div id="toast" class="toast" role="status" aria-live="polite" hidden></div>

  <!-- Sticky Main App Header -->
  <header class="app-header">
    <div class="header-left">
      <div class="brand">
        <span class="brand-icon">📊</span>
        <div>
          <div class="brand-title">NSE EOD CPR</div>
          <div class="brand-sub">Next-Session Levels · Confluence Screener</div>
        </div>
      </div>
      <label class="date-nav" title="Switch Session">
        <select id="dateSelect" aria-label="Select scan date"></select>
      </label>
    </div>

    <div class="header-actions">
      <button id="copyTvBtn" class="btn btn-primary" type="button" title="Copy current symbols formatted for TradingView">
        <span>📋 Copy TV Watchlist</span>
        <span id="tvCountBadge" class="badge-count">0</span>
      </button>
      <button id="sectorHeatmapBtn" class="btn btn-secondary" type="button" title="View industry CPR distribution">
        <span>📊 Sector Breadth</span>
      </button>
      <button id="workspaceToggleBtn" class="btn btn-secondary" type="button" title="Personal watchlists, views & alerts">
        <span>💾 Workspace</span>
        <span id="alertCountBadge" class="badge-count" style="display:none">0</span>
      </button>
      <button id="downloadsToggleBtn" class="btn btn-secondary" type="button" title="Download CSV & ZIP bundles">
        <span>📥 Downloads</span>
      </button>
      <button id="rulesModalBtn" class="btn btn-ghost" type="button" title="Methodology & Formulas">
        <span>ℹ️ Rules</span>
      </button>
    </div>
  </header>

  <!-- Interactive KPI Metric Ribbon (Clickable Filters) -->
  <section class="metrics-ribbon" id="metrics" aria-label="Session summary metrics"></section>

  <!-- Quick Strategy Pill Bar -->
  <section class="strategy-bar" aria-label="Quick strategy filters">
    <span class="strategy-label">Presets:</span>
    <div class="strategy-pills" id="strategyPills">
      <button type="button" class="pill active" data-preset="all">🔥 All Setups</button>
      <button type="button" class="pill" data-preset="triple_conf">🚀 Triple Confluence (≥+4)</button>
      <button type="button" class="pill" data-preset="nr7">⚡ NR7 Squeeze</button>
      <button type="button" class="pill" data-preset="virgin">✨ Virgin CPR</button>
      <button type="button" class="pill" data-preset="narrow">⚡ Narrow Breakout</button>
      <button type="button" class="pill" data-preset="high_confluence">⭐ High Confluence (≥+3)</button>
      <button type="button" class="pill" data-preset="fo_movers">🛡️ F&amp;O Setups</button>
      <button type="button" class="pill" data-preset="bullish_geom">🟢 Bullish Geometry</button>
      <button type="button" class="pill" data-preset="bearish_geom">🔴 Bearish Geometry</button>
      <button type="button" class="pill" data-preset="top20">🏆 Top 20 Narrow</button>
    </div>
  </section>

  <!-- Primary Filter Toolbar -->
  <section class="toolbar" aria-label="Table filter controls">
    <div class="toolbar-primary">
      <div class="search-wrap">
        <input id="search" type="search" placeholder="🔍 Search symbol, sector, or setup…" autocomplete="off" aria-label="Search symbol"/>
      </div>
      <label class="filter-label">
        <select id="segment" aria-label="Filter segment">
          <option value="Any">All Segments</option>
          <option>F&amp;O + Cash</option>
          <option>Cash Only</option>
        </select>
      </label>
      <label class="filter-label">
        <select id="industry" aria-label="Filter industry">{industry_opts}</select>
      </label>
      <label class="filter-label">
        <select id="columnMode" aria-label="Column view mode">
          <option value="compact">Trader View (with Gauge)</option>
          <option value="research">Full Research View</option>
        </select>
      </label>
      <button id="toggleAdvFiltersBtn" class="btn btn-sm btn-ghost" type="button">⚙️ More Filters</button>
    </div>

    <!-- Collapsible Advanced Filters Drawer -->
    <div id="advFiltersPanel" class="toolbar-advanced" hidden>
      <label class="filter-label">CPR Class
        <select id="klass"><option value="Any">Any class</option><option>Narrow</option><option>Moderate</option><option>Wide</option></select>
      </label>
      <label class="filter-label">Bias
        <select id="bias"><option value="Any">Any bias</option><option>Bullish</option><option>Bearish</option><option>Neutral</option></select>
      </label>
      <label class="filter-label">Overlay
        <select id="overlay"><option value="Any">Any overlay</option><option>Higher</option><option>Lower</option><option>Inside</option><option>Outside</option><option>Overlapping</option></select>
      </label>
      <label class="filter-label">Setup
        <select id="setup"><option value="Any">Any setup</option><option>Long</option><option>Short</option><option>Watch Long</option><option>Watch Short</option><option>Watch</option><option>No setup</option></select>
      </label>
      <label class="filter-label">Own-history
        <select id="ownNarrow"><option value="Any">Own-narrow: any</option><option value="Yes">Own-narrow only</option><option value="No">Not own-narrow</option></select>
      </label>
      <div class="filter-checkboxes">
        <label class="filter-check"><input id="niftyOnly" type="checkbox"/> Nifty 500 Only</label>
        <label class="filter-check"><input id="hideUnclassified" type="checkbox"/> Hide Unclassified / Diversified</label>
      </div>
      <button id="resetFiltersBtn" class="btn btn-sm btn-secondary" type="button">Reset Filters</button>
    </div>
  </section>

  <!-- Workspace Panel (Saved Views, Alert Rules, Personal Watchlist) -->
  <section id="localPanel" class="local-panel" hidden aria-live="polite">
    <div class="local-tools" aria-label="Browser-local tools">
      <div>
        <p class="kicker">Personal Workspace</p>
        <p class="local-tool-copy">Saved views, alert rules, and personal watchlists are stored locally in your browser.</p>
      </div>
      <div class="local-actions">
        <button id="saveViewButton" type="button">Save current view</button>
        <button id="savedViewsButton" type="button">Saved views</button>
        <button id="manageAlertsButton" type="button">Alert rules</button>
        <button id="alertCenterButton" type="button">Alerts <span id="alertCount" class="tool-count">0</span></button>
      </div>
    </div>
    <div id="localPanelContent" class="local-panel-body"></div>
  </section>

  <!-- Downloads Popover Panel -->
  <section id="downloadsPanel" class="downloads-panel" hidden>
    <div class="downloads-panel-head">
      <h3>📥 Download Scan Bundles</h3>
      <button id="closeDownloadsBtn" class="btn btn-sm btn-ghost" type="button">✕</button>
    </div>
    <div class="downloads" id="downloads"></div>
  </section>

  <!-- Navigation View Tabs -->
  <nav class="tabs" id="tabs" aria-label="Data views">
    <div class="tab-group" role="tablist" aria-label="Movers views">
      <button role="tab" aria-selected="false" data-tab="gainers">🚀 Top 25 Gainers</button>
      <button role="tab" aria-selected="false" data-tab="losers">📉 Top 25 Losers</button>
    </div>
    <div class="tab-group" role="tablist" aria-label="Daily views">
      <button role="tab" aria-selected="true" data-tab="best" class="on">🎯 Best Today</button>
      <button role="tab" aria-selected="false" data-tab="watchlist">📋 Watchlist</button>
      <button role="tab" aria-selected="false" data-tab="bullish">🟢 Bullish CPR</button>
      <button role="tab" aria-selected="false" data-tab="bearish">🔴 Bearish</button>
      <button role="tab" aria-selected="false" data-tab="narrow">⚡ Narrow</button>
      <button role="tab" aria-selected="false" data-tab="wide">↔️ Wide CPR</button>
      <button role="tab" aria-selected="false" data-tab="full">📊 All Scanned</button>
    </div>
    <div class="tab-group" role="tablist" aria-label="Higher timeframe views">
      <button role="tab" aria-selected="false" data-tab="weekly">📅 Weekly CPR</button>
      <button role="tab" aria-selected="false" data-tab="monthly">🗓️ Monthly CPR</button>
    </div>
    <div class="tab-group" role="tablist" aria-label="Review views">
      <button role="tab" aria-selected="false" data-tab="follow">🔄 Follow-through</button>
      <button role="tab" aria-selected="false" data-tab="top20">🏆 Top 20</button>
      <button role="tab" aria-selected="false" data-tab="bullish_bias">📈 Bullish Bias</button>
    </div>
    <div class="tab-group" role="tablist" aria-label="Personal views">
      <button role="tab" aria-selected="false" data-tab="mylist">★ My list <span id="myListCount" class="tab-count">0</span></button>
    </div>
  </nav>

  <!-- Table View Status Header -->
  <div class="table-header-meta">
    <p class="count" id="count">Loading rows…</p>
    <div class="data-status" id="dataStatus">Loading session data…</div>
  </div>

  <div class="empty-guide" id="emptyGuide" hidden></div>

  <!-- Main Data Table Wrap -->
  <div class="table-wrap">
    <table id="mainTable">
      <thead id="head"></thead>
      <tbody id="body"></tbody>
    </table>
  </div>

  <!-- Side Detail Drawer -->
  <div id="drawerBackdrop" class="drawer-backdrop" hidden></div>
  <aside id="symbolDrawer" class="symbol-drawer" hidden aria-labelledby="drawerTitle" aria-modal="true" role="dialog">
    <div class="drawer-head">
      <div>
        <p class="kicker" id="drawerKicker">Symbol context</p>
        <h2 id="drawerTitle">Symbol</h2>
        <p id="drawerSubtitle" class="drawer-subtitle"></p>
      </div>
      <button id="drawerClose" class="drawer-close" type="button" aria-label="Close symbol context">✕ Close</button>
    </div>
    <div id="drawerBody"></div>
  </aside>

  <!-- Sector Breadth Heatmap Modal -->
  <div id="heatmapBackdrop" class="drawer-backdrop" hidden></div>
  <div id="heatmapModal" class="modal-dialog" hidden role="dialog" aria-labelledby="heatmapTitle" aria-modal="true">
    <div class="modal-head">
      <h2 id="heatmapTitle">📊 Sector &amp; Industry CPR Breadth</h2>
      <button id="closeHeatmapBtn" class="btn btn-sm btn-ghost" type="button">✕</button>
    </div>
    <div class="modal-body" id="heatmapBody">
      <p class="modal-sub">Industry distribution of CPR geometry for next session. Click any sector to filter.</p>
      <div id="heatmapGrid" class="heatmap-grid"></div>
    </div>
  </div>

  <!-- Methodology & Rules Modal -->
  <div id="rulesBackdrop" class="drawer-backdrop" hidden></div>
  <div id="rulesModal" class="modal-dialog" hidden role="dialog" aria-labelledby="rulesTitle" aria-modal="true">
    <div class="modal-head">
      <h2 id="rulesTitle">📖 Methodology &amp; Trading Rules</h2>
      <button id="closeRulesBtn" class="btn btn-sm btn-ghost" type="button">✕</button>
    </div>
    <div class="modal-body">
      <section class="rules-section">
        <h3>Prashant Shah CPR Calculation</h3>
        <p>Formulas calculated from the previous session's completed OHLC bar:</p>
        <ul>
          <li><strong>Pivot</strong> = (High + Low + Close) / 3</li>
          <li><strong>BC (Bottom Central)</strong> = (High + Low) / 2</li>
          <li><strong>TC (Top Central)</strong> = 2 × Pivot − BC</li>
          <li><strong>CPR Width %</strong> = |TC − BC| / Close × 100</li>
        </ul>
      </section>
      <section class="rules-section">
        <h3>Classifications &amp; Signals</h3>
        <ul>
          <li><strong>Narrow CPR (≤ 0.25%)</strong>: Close near mid-range → higher probability of directional trend day.</li>
          <li><strong>Own-Narrow</strong>: CPR Width in the bottom 25% of that specific symbol's own 60-day history.</li>
          <li><strong>Higher / Lower Overlay</strong>: Today's CPR band sits entirely above (bullish) or below (bearish) prior day's band.</li>
          <li><strong>Confluence Score (−6 to +6)</strong>: Sum of Daily, Weekly, and Monthly CPR signal alignment.</li>
          <li><strong>Bullish CPR</strong>: The strict narrow, above-band breakout shortlist; <strong>Bullish Bias</strong> includes all rows with bullish CPR geometry.</li>
          <li><strong>Wide CPR</strong>: Adds separate consolidation and range-breakout states; it does not replace the existing Narrow CPR setup labels.</li>
        </ul>
      </section>
      <section class="rules-section disclaimer-box">
        <strong>⚠️ DISCLAIMER:</strong> For research and educational purposes only. Next-session levels are based on NSE Bhavcopy data. Not investment advice or trading recommendation.
      </section>
    </div>
  </div>

  <footer class="app-footer">
    <div>NSE EOD CPR Console v2.0 · Next-Session Central Pivot Range Analysis · Research Only</div>
    <div style="font-size:10px;margin-top:4px;color:var(--muted)">
      Equity stocks only (ETFs, AMCs, mutual funds excluded). Industry from NSE Nifty 500 official list; non-Nifty-500 stocks use eod2 curated sector data or keyword heuristics.
      Bullish CPR is the strict narrow, above-band breakout shortlist; Bullish Bias includes all rows with bullish CPR geometry.
      Wide CPR adds separate consolidation and range-breakout states; it does not replace the existing Narrow CPR setup labels.
    </div>
  </footer>

  <script>window.CPR_PAYLOAD_URL = "payload.json";</script>
  <script src="assets/app.js?v=9"></script>
</body>
</html>
"""


CSS = """
:root {
  --bg: #0c1015;
  --card: #141a22;
  --card-hover: #1b232e;
  --card-alt: #18202a;
  --line: #26313f;
  --line-subtle: #1c2530;
  --text: #f0f4f8;
  --muted: #8b99a8;
  --bull: #3ecf8e;
  --bull-bg: rgba(62, 207, 142, 0.12);
  --bear: #f85149;
  --bear-bg: rgba(248, 81, 73, 0.12);
  --accent: #58a6ff;
  --accent-bg: rgba(88, 166, 255, 0.12);
  --narrow: #bc8cff;
  --narrow-bg: rgba(188, 140, 255, 0.14);
  --amber: #f5a623;
  --amber-bg: rgba(245, 166, 35, 0.14);
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
}

* { box-sizing: border-box; }
[hidden] { display: none !important; }

html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "IBM Plex Sans", sans-serif;
  -webkit-font-smoothing: antialiased;
}

body.drawer-open { overflow: hidden; }

/* Header */
.app-header {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px 20px;
  background: rgba(20, 26, 34, 0.95);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--line);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 18px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.brand-icon { font-size: 24px; }
.brand-title { font-size: 16px; font-weight: 700; letter-spacing: -0.01em; color: #fff; }
.brand-sub { font-size: 11px; color: var(--muted); }

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  border: 1px solid var(--line);
  background: var(--card);
  color: var(--text);
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}
.btn:hover { background: var(--card-hover); border-color: var(--accent); }
.btn-primary { background: #1f6feb; border-color: #388bfd; color: #fff; }
.btn-primary:hover { background: #388bfd; }
.btn-secondary { background: var(--card-alt); }
.btn-ghost { background: transparent; border-color: transparent; color: var(--muted); }
.btn-ghost:hover { color: var(--text); background: var(--card); }
.btn-sm { padding: 4px 8px; font-size: 11px; }

.badge-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
}

/* Date Nav */
.date-nav select, select, input[type="search"], input[type="text"] {
  background: var(--card);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 12px;
  outline: none;
}
select:focus, input:focus { border-color: var(--accent); }

/* Toast */
.toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 100;
  background: #1f6feb;
  color: #fff;
  padding: 10px 18px;
  border-radius: var(--radius-md);
  font-weight: 600;
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  animation: slideUp 0.2s ease;
}
@keyframes slideUp { from { transform: translateY(10px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }

/* Metrics Ribbon */
.metrics-ribbon {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 20px;
  background: #0f141a;
  border-bottom: 1px solid var(--line-subtle);
}
.metric-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 11px;
  cursor: pointer;
  transition: border-color 0.15s;
}
.metric-pill:hover { border-color: var(--accent); }
.metric-pill span { color: var(--muted); }
.metric-pill b { color: var(--text); font-weight: 700; }
.metric-pill.bull b { color: var(--bull); }
.metric-pill.bear b { color: var(--bear); }
.metric-pill.narrow b { color: var(--narrow); }

/* Strategy Bar */
.strategy-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 20px;
  background: var(--card-alt);
  border-bottom: 1px solid var(--line-subtle);
  overflow-x: auto;
}
.strategy-label { font-size: 11px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; shrink: 0; }
.strategy-pills { display: flex; gap: 6px; }
.pill {
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--card);
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}
.pill:hover { color: var(--text); border-color: var(--accent); }
.pill.active { background: var(--accent); border-color: var(--accent); color: #040d1a; font-weight: 700; }

/* Toolbar */
.toolbar {
  padding: 8px 20px;
  background: var(--bg);
  border-bottom: 1px solid var(--line);
}
.toolbar-primary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.search-wrap { flex: 1; min-width: 220px; }
.search-wrap input { width: 100%; }
.toolbar-advanced {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  padding: 10px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}
.filter-label { font-size: 11px; color: var(--muted); display: flex; flex-direction: column; gap: 3px; }
.filter-check { font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 5px; cursor: pointer; }
.filter-checkboxes { display: flex; gap: 12px; align-items: center; }

/* Tabs */
.tabs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 20px;
  background: var(--card);
  border-bottom: 1px solid var(--line);
}
.tab-group { display: flex; gap: 4px; align-items: center; }
.tabs button {
  background: transparent;
  color: var(--muted);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 5px 10px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.tabs button:hover { color: var(--text); background: rgba(255,255,255,0.05); }
.tabs button.on { color: #fff; background: #1f6feb; border-color: #388bfd; }

.tab-count, .tool-count {
  display: inline-flex;
  min-width: 16px;
  padding: 1px 4px;
  border-radius: 999px;
  background: rgba(255,255,255,0.2);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
}

/* Meta status */
.table-header-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 20px;
  font-size: 12px;
  color: var(--muted);
  background: var(--bg);
}
.count { margin: 0; }
.data-status { font-size: 11px; }

/* Table */
.table-wrap {
  padding: 0 20px 40px;
  overflow: auto;
  max-height: calc(100vh - 280px);
}
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 12px; }
th {
  position: sticky;
  top: 0;
  z-index: 5;
  background: #161d26;
  text-align: left;
  font-size: 10px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--muted);
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  white-space: nowrap;
}
th:hover { color: var(--text); }
th.sorted { color: var(--accent); }
td { padding: 7px 10px; border-bottom: 1px solid var(--line-subtle); white-space: nowrap; }
tr[data-symbol] { cursor: pointer; transition: background 0.1s; }
tr[data-symbol]:hover td { background: var(--card-hover); }

/* Badges */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 7px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}
.badge.confirmed, .bull { color: var(--bull); font-weight: 600; }
.badge.confirmed { background: var(--bull-bg); border: 1px solid rgba(62, 207, 142, 0.3); }
.badge.unavailable, .bear { color: var(--bear); font-weight: 600; }
.badge.unavailable { background: var(--bear-bg); border: 1px solid rgba(248, 81, 73, 0.3); }
.badge.watch, .narrow { color: var(--narrow); }
.badge.watch { background: var(--narrow-bg); border: 1px solid rgba(188, 140, 255, 0.3); }
.badge.neutral { color: var(--muted); background: rgba(255,255,255,0.06); }

/* CPR Range Visual Gauge */
.cpr-gauge-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: monospace;
}
.gauge-bar {
  display: inline-flex;
  align-items: center;
  height: 14px;
  background: #11171f;
  border: 1px solid var(--line);
  border-radius: 3px;
  padding: 0 4px;
  font-size: 9px;
  font-weight: 700;
}
.gauge-bar.above { border-color: var(--bull); color: var(--bull); background: var(--bull-bg); }
.gauge-bar.inside { border-color: var(--amber); color: var(--amber); background: var(--amber-bg); }
.gauge-bar.below { border-color: var(--bear); color: var(--bear); background: var(--bear-bg); }

/* Drawer */
.drawer-backdrop { position: fixed; inset: 0; z-index: 40; background: rgba(0, 0, 0, 0.7); backdrop-filter: blur(2px); }
.symbol-drawer {
  position: fixed;
  z-index: 41;
  top: 0;
  right: 0;
  width: min(560px, 100vw);
  height: 100vh;
  overflow-y: auto;
  padding: 20px 24px;
  background: var(--card);
  border-left: 1px solid var(--line);
  box-shadow: -12px 0 36px rgba(0,0,0,0.5);
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.drawer-head { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid var(--line); padding-bottom: 12px; }
.drawer-head h2 { margin: 2px 0 0; font-size: 24px; color: #fff; }
.drawer-subtitle { margin: 2px 0 0; color: var(--muted); font-size: 12px; }
.drawer-close { background: var(--card-alt); border: 1px solid var(--line); color: var(--text); border-radius: var(--radius-sm); padding: 5px 10px; cursor: pointer; }
.drawer-close:hover { border-color: var(--accent); color: var(--accent); }

.drawer-section { border-bottom: 1px solid var(--line-subtle); padding-bottom: 14px; }
.drawer-section h3 { margin: 0 0 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
.drawer-summary { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.detail-item { background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px; }
.detail-item span { display: block; font-size: 10px; text-transform: uppercase; color: var(--muted); }
.detail-item strong { display: block; margin-top: 2px; font-size: 14px; color: var(--text); }
.detail-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }

/* SVG Mini-chart fallback */
.cpr-chart { width: 100%; min-height: 180px; margin: 4px 0 0; padding: 8px 0; background: #11171c; border: 1px solid var(--line); border-radius: 10px; }
.cpr-chart svg { display: block; width: 100%; height: auto; }
.cpr-chart .band { fill: rgba(142, 192, 245, .24); stroke: var(--accent); stroke-width: 1; }
.cpr-chart .price-line { stroke: var(--bull); stroke-width: 2; }
.cpr-chart .price-dot { fill: var(--bull); }
.cpr-chart .level-line { stroke: #667482; stroke-width: 1; stroke-dasharray: 4 4; }
.cpr-chart text { fill: var(--muted); font: 11px "IBM Plex Sans", "Segoe UI", sans-serif; }

/* TradingView Chart Container inside Drawer */
#tv-chart-container {
  width: 100%;
  height: 240px;
  background: #0d1117;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  position: relative;
}

/* Modals */
.modal-dialog {
  position: fixed;
  z-index: 50;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(800px, 95vw);
  max-height: 85vh;
  overflow-y: auto;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  box-shadow: 0 16px 48px rgba(0,0,0,0.6);
  padding: 20px 24px;
}
.modal-head { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--line); padding-bottom: 12px; margin-bottom: 14px; }
.modal-head h2 { margin: 0; font-size: 18px; color: #fff; }
.modal-sub { color: var(--muted); font-size: 12px; margin: 0 0 14px; }

/* Sector Breadth Heatmap Grid */
.heatmap-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
.sector-card {
  background: var(--card-alt);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.sector-card:hover { border-color: var(--accent); transform: translateY(-1px); }
.sector-name { font-weight: 700; font-size: 12px; color: #fff; margin-bottom: 4px; display: flex; justify-content: space-between; }
.sector-meta { font-size: 10px; color: var(--muted); display: flex; gap: 8px; }
.sector-bar-outer { height: 6px; border-radius: 3px; background: rgba(255,255,255,0.1); margin-top: 6px; overflow: hidden; display: flex; }
.sector-bar-bull { background: var(--bull); height: 100%; }
.sector-bar-bear { background: var(--bear); height: 100%; }

/* Local Workspace Panel */
.local-panel { padding: 12px 20px; background: var(--card-alt); border-bottom: 1px solid var(--line); }
.local-tools { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 10px; }
.local-tool-copy { margin: 2px 0 0; color: var(--muted); font-size: 11px; }
.local-actions { display: flex; gap: 6px; }
.local-actions button, .local-panel button, .drawer-action { background: var(--card); color: var(--text); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 5px 8px; cursor: pointer; font-size: 11px; }
.local-actions button:hover, .drawer-action:hover { border-color: var(--accent); color: var(--accent); }

/* Downloads Panel */
.downloads-panel {
  position: absolute;
  top: 55px;
  right: 20px;
  z-index: 30;
  width: 320px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  box-shadow: 0 12px 32px rgba(0,0,0,0.5);
  padding: 14px;
}
.downloads-panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.downloads-panel-head h3 { margin: 0; font-size: 13px; color: #fff; }
.downloads { display: flex; flex-direction: column; gap: 6px; }
.downloads a {
  display: block;
  text-decoration: none;
  background: var(--card-alt);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 11px;
  font-weight: 600;
  transition: all 0.15s;
}
.downloads a:hover { border-color: var(--accent); color: var(--accent); }
.downloads a.tv-btn { background: #1f6feb; color: #fff; border-color: #388bfd; }
.downloads a.zip { background: rgba(62, 207, 142, 0.15); border-color: var(--bull); color: var(--bull); }

.rules-section { margin-bottom: 14px; }
.rules-section h3 { margin: 0 0 6px; font-size: 12px; text-transform: uppercase; color: var(--accent); }
.rules-section ul { margin: 0; padding-left: 18px; color: var(--muted); font-size: 12px; }
.disclaimer-box { background: rgba(245, 166, 35, 0.1); border: 1px solid rgba(245, 166, 35, 0.3); border-radius: var(--radius-sm); padding: 10px 12px; color: #f5a623; font-size: 11px; }

.empty-guide { margin: 16px 20px; padding: 16px; border: 1px solid var(--line); border-radius: var(--radius-md); color: var(--muted); text-align: center; }
.empty-guide button { margin-left: 8px; background: var(--accent); color: #000; border: 0; border-radius: var(--radius-sm); padding: 4px 10px; cursor: pointer; font-weight: 700; }

.watch-star { color: var(--amber); font-weight: 700; margin-right: 4px; }
.app-footer { padding: 12px 20px; color: var(--muted); font-size: 11px; text-align: center; border-top: 1px solid var(--line); }

@media (max-width: 800px) {
  .app-header { flex-direction: column; align-items: stretch; gap: 10px; }
  .header-actions { flex-wrap: wrap; }
  .metrics-ribbon { overflow-x: auto; flex-wrap: nowrap; }
  .table-wrap { padding: 0 10px 30px; }
  .symbol-drawer { width: 100vw; padding: 16px; }
}
"""

JS = r"""
let DATA = null;
const PAYLOAD_URL = window.CPR_PAYLOAD_URL;
const COLS = ["SYMBOL","DAY_CHG_PCT","NAME","Industry","CLOSE","Pivot","BC","TC","CPR_Width_Pct","Width_Rank_Pct","CPR_Class","Own_Narrow","Overlay","Setup","Bias","Price_Position","Segment","History_Days","Value_60d","ATR14","Width_ATR","Value_Ratio","Above_SMA50","Above_SMA100","Regime","Confluence_Score","Signal_Direction","Signal_Score","Signal_Grade","Signal_Explanation","Strategy_Type","Strategy_Setup","Strategy_Confirmation","Strategy_Explanation","Applies"];
const COMPACT_COLS = ["SYMBOL","DAY_CHG_PCT","Setup","Price_Position","CPR_Gauge","CLOSE","CPR_Bottom","CPR_Top","CPR_Width_Pct","Overlay","Confluence_Score","Value_Ratio","Industry"];
const FOLLOW_COLS = ["SYMBOL","DAY_CHG_PCT","Industry","Setup","CPR_Width_Pct","Width_Rank_Pct","Segment","Next_Close","Follow_Through"];
let tab = "best";
let currentPreset = "all";
let sort = {col: null, asc: true};
let drawerTrigger = null;
let activeTvChart = null;

function $(id) { return document.getElementById(id); }

function esc(value) {
  return String(value ?? "—").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
}

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function showToast(msg) {
  const t = $("toast");
  if (!t) return;
  t.textContent = msg;
  t.hidden = false;
  setTimeout(() => { t.hidden = true; }, 2600);
}

function badgeClass(value) {
  if (value === "Confirmed") return "confirmed";
  if (value === "Watch") return "watch";
  if (value === "Unavailable") return "unavailable";
  return "neutral";
}

function badgeHtml(value) {
  const label = value === null || value === undefined || value === "" ? "—" : value;
  return `<span class="badge ${badgeClass(label)}">${esc(label)}</span>`;
}

const LOCAL_KEYS = { watchlist: "cprConsole.watchlist.v1", views: "cprConsole.views.v1", alerts: "cprConsole.alertRules.v1", dismissed: "cprConsole.dismissedAlerts.v1" };
let localPanelMode = null;

function readLocal(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (e) { return fallback; }
}

function writeLocal(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); return true; }
  catch (e) { loadingStatus("Browser-local storage is unavailable; workspace changes were not saved.", true); return false; }
}

function watchedSymbols() {
  const value = readLocal(LOCAL_KEYS.watchlist, []);
  return new Set(Array.isArray(value) ? value.map(v => String(v).toUpperCase()) : []);
}

function isWatched(symbol) { return watchedSymbols().has(String(symbol || "").toUpperCase()); }

function toggleWatchlist(symbol) {
  const normalized = String(symbol || "").trim().toUpperCase();
  if (!normalized) return;
  const set = watchedSymbols();
  if (set.has(normalized)) set.delete(normalized); else set.add(normalized);
  writeLocal(LOCAL_KEYS.watchlist, Array.from(set).sort());
  renderLocalPanel();
  render();
  updateDrawerAction(normalized);
  showToast(set.has(normalized) ? `★ Added ${normalized} to My list` : `Removed ${normalized} from My list`);
}

function currentFilters() {
  return { tab, q: $("search").value, seg: $("segment").value, ind: $("industry").value, cls: $("klass").value, bias: $("bias").value, ovl: $("overlay").value, setup: $("setup").value, nn: $("ownNarrow").value, cols: $("columnMode").value, nifty: $("niftyOnly").checked, hide: $("hideUnclassified").checked };
}

function applyView(view) {
  if (!view) return;
  const map = [["search", view.q], ["segment", view.seg], ["industry", view.ind], ["klass", view.cls], ["bias", view.bias], ["overlay", view.ovl], ["setup", view.setup], ["ownNarrow", view.nn], ["columnMode", view.cols]];
  map.forEach(([id, value]) => { if (value !== undefined && $(id)) $(id).value = value; });
  $("niftyOnly").checked = Boolean(view.nifty);
  $("hideUnclassified").checked = Boolean(view.hide);
  if (view.tab && document.querySelector(`.tabs button[data-tab="${view.tab}"]`)) {
    document.querySelectorAll(".tabs button").forEach(b => { b.classList.remove("on"); b.setAttribute("aria-selected", "false"); });
    const selected = document.querySelector(`.tabs button[data-tab="${view.tab}"]`);
    selected.classList.add("on");
    selected.setAttribute("aria-selected", "true");
    tab = view.tab;
  }
  render();
}

function saveCurrentView(name) {
  if (!name || !name.trim()) return;
  const views = readLocal(LOCAL_KEYS.views, []);
  const next = views.filter(v => v.name !== name.trim());
  next.push({ name: name.trim(), ...currentFilters(), savedAt: new Date().toISOString() });
  writeLocal(LOCAL_KEYS.views, next.slice(-20));
  localPanelMode = "views";
  renderLocalPanel();
  showToast(`Saved view "${name.trim()}"`);
}

function alertRules() {
  const value = readLocal(LOCAL_KEYS.alerts, []);
  return Array.isArray(value) ? value : [];
}

function addAlertRuleFromForm() {
  const type = ($("alertType").value || "").trim().toLowerCase();
  const allowed = new Set(["confirmed", "wide-confirmed", "score", "setup", "cpr-class"]);
  if (!allowed.has(type)) { loadingStatus("Unknown alert type. Use confirmed, wide-confirmed, score, setup, or cpr-class.", true); return; }
  const symbol = ($("alertSymbol").value || "").trim().toUpperCase();
  let value = ($("alertValue").value || "").trim();
  if (type === "score") value = Number(value || "70");
  if (type === "score" && !Number.isFinite(value)) { loadingStatus("Score threshold must be numeric.", true); return; }
  const label = ($("alertName").value || "").trim() || `${type}${symbol ? ` · ${symbol}` : ""}`;
  const rules = alertRules();
  rules.push({ id: `${Date.now()}-${Math.random().toString(16).slice(2)}`, name: label.trim(), type, symbol, value, enabled: true, createdAt: new Date().toISOString() });
  writeLocal(LOCAL_KEYS.alerts, rules.slice(-30));
  localPanelMode = "alerts";
  renderLocalPanel();
  showToast(`Added alert rule "${label.trim()}"`);
}

function alertMatches(rule, row) {
  if (!rule || rule.enabled === false || !row) return false;
  if (rule.symbol && String(row.SYMBOL || "").toUpperCase() !== String(rule.symbol).toUpperCase()) return false;
  if (rule.type === "confirmed") return row.Strategy_Confirmation === "Confirmed";
  if (rule.type === "wide-confirmed") return row.Strategy_Type === "Wide CPR" && row.Strategy_Confirmation === "Confirmed";
  if (rule.type === "score") return Number(row.Signal_Score) >= Number(rule.value);
  if (rule.type === "setup") return row.Setup === rule.value;
  if (rule.type === "cpr-class") return row.CPR_Class === rule.value;
  return false;
}

function dismissedKeys() {
  const value = readLocal(LOCAL_KEYS.dismissed, []);
  return new Set(Array.isArray(value) ? value : []);
}

function currentAlerts() {
  const dismissed = dismissedKeys();
  const rows = DATA && DATA.tables ? (DATA.tables.full || []) : [];
  const out = [];
  alertRules().forEach(rule => rows.forEach(row => {
    if (!alertMatches(rule, row)) return;
    const key = `${DATA.date}:${rule.id}:${row.SYMBOL}`;
    if (!dismissed.has(key)) out.push({ key, rule, row });
  }));
  return out;
}

function dismissAlert(key) {
  const keys = dismissedKeys();
  keys.add(key);
  writeLocal(LOCAL_KEYS.dismissed, Array.from(keys).slice(-500));
  renderLocalPanel();
}

function deleteAlertRule(id) {
  writeLocal(LOCAL_KEYS.alerts, alertRules().filter(rule => rule.id !== id));
  renderLocalPanel();
}

function renderLocalPanel() {
  const watch = watchedSymbols();
  const views = readLocal(LOCAL_KEYS.views, []);
  const rules = alertRules();
  const matches = currentAlerts();
  $("myListCount").textContent = String(watch.size);
  $("alertCount").textContent = String(matches.length);
  const badge = $("alertCountBadge");
  if (badge) {
    badge.textContent = String(matches.length);
    badge.style.display = matches.length > 0 ? "inline-flex" : "none";
  }
  const panel = $("localPanelContent");
  if (!panel) return;
  if (!localPanelMode) {
    $("localPanel").hidden = true;
    return;
  }
  $("localPanel").hidden = false;
  if (localPanelMode === "save") {
    panel.innerHTML = `<h3>Save Current View</h3><p>Stores active tab, filters, and display settings in this browser.</p>
      <div style="display:flex;gap:8px;margin-top:8px;"><input id="viewName" type="text" maxlength="60" placeholder="e.g. F&O Momentum Longs" style="flex:1;"><button class="btn btn-primary" id="confirmSaveView" type="button">Save</button><button class="btn btn-ghost" id="cancelSaveView" type="button">Cancel</button></div>`;
    $("confirmSaveView").addEventListener("click", () => saveCurrentView($("viewName").value));
    $("cancelSaveView").addEventListener("click", () => { localPanelMode = null; renderLocalPanel(); });
    $("viewName").focus();
    return;
  }
  if (localPanelMode === "views") {
    panel.innerHTML = `<h3>Saved Views</h3>${views.length ? views.map((v, i) => `<div class="panel-row" style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);"><span><strong>${esc(v.name)}</strong> · ${esc(v.tab || 'best')}</span><div style="display:flex;gap:6px;"><button class="btn btn-sm" type="button" data-view-index="${i}">Apply</button><button class="btn btn-sm btn-ghost" type="button" data-delete-view="${i}">Delete</button></div></div>`).join("") : "<p style='color:var(--muted);font-size:12px;'>No saved views yet.</p>"}`;
    panel.querySelectorAll("[data-view-index]").forEach(b => b.addEventListener("click", () => applyView(views[Number(b.dataset.viewIndex)])));
    panel.querySelectorAll("[data-delete-view]").forEach(b => b.addEventListener("click", () => { const next = views.slice(); next.splice(Number(b.dataset.deleteView), 1); writeLocal(LOCAL_KEYS.views, next); renderLocalPanel(); }));
    return;
  }
  if (localPanelMode === "alerts") {
    panel.innerHTML = `<h3>Alert Rules</h3>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:6px;margin:8px 0;">
        <input id="alertName" type="text" placeholder="Rule name">
        <select id="alertType"><option value="confirmed">Confirmed Setup</option><option value="wide-confirmed">Wide Confirmed</option><option value="score">Min Score</option><option value="setup">Setup Label</option><option value="cpr-class">CPR Class</option></select>
        <input id="alertSymbol" type="text" placeholder="Symbol (opt)">
        <input id="alertValue" type="text" placeholder="Value (e.g. 70, Long)">
        <button class="btn btn-primary" id="addAlertRule" type="button">+ Add Rule</button>
      </div>
      ${rules.length ? rules.map(r => `<div style="display:flex;justify-content:space-between;padding:4px 0;"><span>${esc(r.name)} (${esc(r.type)})</span><button class="btn btn-sm btn-ghost" type="button" data-delete-rule="${esc(r.id)}">Delete</button></div>`).join("") : "<p style='color:var(--muted);font-size:12px;'>No alert rules defined.</p>"}`;
    $("addAlertRule").addEventListener("click", addAlertRuleFromForm);
    panel.querySelectorAll("[data-delete-rule]").forEach(b => b.addEventListener("click", () => deleteAlertRule(b.dataset.deleteRule)));
    return;
  }
  panel.innerHTML = `<h3>Active Alerts (${matches.length})</h3>${matches.length ? matches.map(m => `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);"><span><strong>${esc(m.row.SYMBOL)}</strong>: ${esc(m.rule.name)}</span><button class="btn btn-sm" type="button" data-dismiss-alert="${esc(m.key)}">Dismiss</button></div>`).join("") : "<p style='color:var(--muted);font-size:12px;'>No active alert matches for this session.</p>"}`;
  panel.querySelectorAll("[data-dismiss-alert]").forEach(b => b.addEventListener("click", () => dismissAlert(b.dataset.dismissAlert)));
}

function updateDrawerAction(symbol) {
  const button = $("drawerWatchButton");
  if (button) button.textContent = isWatched(symbol) ? "★ In My List" : "☆ Add to My List";
}

function fillIndustry() {
  const sel = $("industry");
  if (!sel || sel.options.length > 1) return;
  (DATA.industries || []).forEach(name => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  });
}

function fillDates() {
  const sel = $("dateSelect");
  DATA.dates.forEach(d => {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = d.label;
    if (d.id === DATA.date) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.addEventListener("change", () => {
    const id = sel.value;
    if (id === DATA.date) return;
    const latest = DATA.dates[0] && DATA.dates[0].id;
    if (id === latest) {
      window.location.href = DATA.home;
    } else {
      const prefix = DATA.home === "./" ? `archive/${id}/` : `../${id}/`;
      window.location.href = prefix;
    }
  });
}

function metrics() {
  const m = DATA.metrics;
  $("metrics").innerHTML = [
    ["EQ Symbols", m.symbols, "", "all"],
    ["Narrow CPR", m.narrow, "narrow", "narrow"],
    ["Own-Narrow", m.own_narrow ?? "—", "narrow", "own_narrow"],
    ["Active Setups", m.setups ?? "—", "bull", "setups"],
    ["Bullish CPR", m.bullish, "bull", "bullish"],
    ["F&O Names", m.fo, "", "fo"],
    ["NIFTY Regime", m.regime || "—", m.regime === "Risk On" ? "bull" : m.regime === "Risk Off" ? "bear" : "", "regime"],
  ].map(([k,v,cls,action]) => `<div class="metric-pill ${cls}" data-metric="${action}" title="Click to filter by ${k}"><span>${k}</span><b>${v}</b></div>`).join("");

  $("metrics").querySelectorAll("[data-metric]").forEach(pill => {
    pill.addEventListener("click", () => {
      const action = pill.dataset.metric;
      if (action === "narrow") { $("klass").value = "Narrow"; selectTab("narrow"); }
      else if (action === "bullish") { selectTab("bullish"); }
      else if (action === "setups") { selectTab("watchlist"); }
      else if (action === "fo") { $("segment").value = "F&O + Cash"; render(); }
      else if (action === "all") { $("segment").value = "Any"; selectTab("full"); }
    });
  });
}

function publicationStatus() {
  const p = DATA.publication || {};
  const freshness = p.freshness || {};
  const source = p.source || {};
  const actual = p.actual_data_date || "unknown";
  const el = $("dataStatus");
  if (!el) return;
  el.textContent = `${freshness.display || "Publication freshness unknown"} · data session ${actual} · source ${source.name || "unknown"}`;
  if (freshness.status !== "known") el.style.borderColor = "var(--bear)";
}

function buildCopyList() {
  const data = sortRows(rows());
  return data
    .map(r => r.SYMBOL)
    .filter(Boolean)
    .map(s => `NSE:${String(s).replace(/\.NS$/i, "")}`)
    .join(", ");
}

function copyWatchlist() {
  const list = buildCopyList();
  if (!list) {
    showToast("No symbols in current view to copy.");
    return;
  }
  navigator.clipboard.writeText(list).then(() => {
    showToast(`✓ Copied ${list.split(",").length} TradingView symbols!`);
  }).catch(() => {
    showToast("Failed to copy symbols to clipboard.");
  });
}

function downloads() {
  const d = DATA.downloads;
  $("downloads").innerHTML = [
    ["🚀 TradingView Charts (Interactive)", "cpr_tradingview_dashboard.html"],
    ["Full Universe CSV", d.full],
    ["Best Today Setups CSV", d.best],
    ["Complete Watchlist CSV", d.watchlist],
    ["Wide CPR CSV", d.wide],
    ["Narrow CPR CSV", d.narrow],
    ["Bullish CPR", d.bullish],
    ["Bullish Bias", d.bullish_bias],
    ["Bearish Setups", d.bearish],
    ["Top 20 Narrow", d.top20],
    ["Weekly CPR", d.weekly],
    ["Monthly CPR", d.monthly],
    ["All ZIP Bundle", d.zip],
  ].map(([label, href]) => href ? `<a class="${label.includes('TradingView') ? 'tv-btn' : label.includes('ZIP') ? 'zip' : ''}" href="${href}" ${label.includes('TradingView') ? 'target=\"_blank\"' : 'download'}>${label}</a>` : "").join("");
}

function fmt(col, val) {
  if (val === null || val === undefined) return "—";
  if (col === "DAY_CHG_PCT") return (Number(val) > 0 ? "+" : "") + Number(val).toFixed(2) + "%";
  if (col === "CPR_Width_Pct") return Number(val).toFixed(4) + "%";
  if (col === "Width_Rank_Pct" || col === "Confluence_Score" || col === "Signal_Score") return Number(val).toFixed(2);
  if (col === "Value_Ratio") return Number(val).toFixed(2);
  if (["CLOSE","Pivot","BC","TC","Value_60d","ATR14","Width_ATR","Next_Close"].includes(col)) return Number(val).toFixed(2);
  if (["Own_Narrow","Nifty500","Above_SMA50","Above_SMA100","History_OK"].includes(col)) return val ? "Yes" : "No";
  return val;
}

function cprRangeGauge(row) {
  const pos = row.Price_Position || "";
  const close = finite(row.CLOSE);
  const bottom = finite(row.CPR_Bottom) || finite(row.BC);
  const top = finite(row.CPR_Top) || finite(row.TC);
  if (pos === "Above CPR") {
    return `<div class="cpr-gauge-cell"><span class="gauge-bar above">[ CPR ] ▲</span><span>Above TC</span></div>`;
  }
  if (pos === "Below CPR") {
    return `<div class="cpr-gauge-cell"><span class="gauge-bar below">▼ [ CPR ]</span><span>Below BC</span></div>`;
  }
  if (pos === "Inside CPR") {
    return `<div class="cpr-gauge-cell"><span class="gauge-bar inside">[ =P= ]</span><span>Inside Band</span></div>`;
  }
  return `<span class="gauge-bar">${esc(pos || "—")}</span>`;
}

function cellHtml(col, val, row) {
  if (col === "CPR_Gauge") return cprRangeGauge(row);
  const rendered = fmt(col, val);
  if (col === "SYMBOL") return `${isWatched(val) ? '<span class="watch-star" title="In My list">★</span>' : ""}${esc(rendered)}`;
  return col === "Strategy_Confirmation" ? badgeHtml(rendered) : esc(rendered);
}

function klass(col, val) {
  if (col === "DAY_CHG_PCT" && Number(val) > 0) return "bull";
  if (col === "DAY_CHG_PCT" && Number(val) < 0) return "bear";
  if (col === "Bias" && val === "Bullish") return "bull";
  if (col === "Bias" && val === "Bearish") return "bear";
  if (col === "Price_Position" && val === "Above CPR") return "bull";
  if (col === "Price_Position" && val === "Below CPR") return "bear";
  if (col === "CPR_Class" && val === "Narrow") return "narrow";
  if (col === "Overlay" && val === "Higher") return "bull";
  if (col === "Overlay" && val === "Lower") return "bear";
  if (col === "Setup" && (val === "Long" || val === "Watch Long")) return "bull";
  if (col === "Setup" && (val === "Short" || val === "Watch Short")) return "bear";
  if (col === "Setup" && val === "Watch") return "narrow";
  if (col === "Signal_Score" && Number(val) >= 65) return "bull";
  if (col === "Signal_Score" && Number(val) < 50) return "bear";
  if (col === "Strategy_Confirmation" && val === "Confirmed") return "bull";
  if (col === "Strategy_Confirmation" && val === "Watch") return "narrow";
  if (col === "Strategy_Confirmation" && val === "Unavailable") return "bear";
  if (col === "Own_Narrow" && val === true) return "narrow";
  if (col === "Follow_Through" && val === "Followed") return "bull";
  if (col === "Follow_Through" && val === "Failed") return "bear";
  return "";
}

function parseFilters() {
  try {
    const q = new URLSearchParams(window.location.hash.slice(1));
    const t = q.get("tab");
    if (t && document.querySelector(`.tabs button[data-tab="${t}"]`)) {
      document.querySelectorAll(".tabs button").forEach(b => { b.classList.remove("on"); b.setAttribute("aria-selected", "false"); });
      const selected = document.querySelector(`.tabs button[data-tab="${t}"]`);
      selected.classList.add("on");
      selected.setAttribute("aria-selected", "true");
      tab = t;
    }
    const sel = (id, name) => { const v = q.get(name); if (v && $(id)) $(id).value = v; };
    sel("search","q"); sel("segment","seg"); sel("industry","ind");
    sel("klass","cls"); sel("bias","bias"); sel("overlay","ovl");
    sel("setup","setup"); sel("ownNarrow","nn"); sel("columnMode","cols");
    if (q.get("nifty") === "1") $("niftyOnly").checked = true;
    if (q.get("hide") === "1") $("hideUnclassified").checked = true;
  } catch (e) {}
}

function rows() {
  const q = $("search").value.trim().toUpperCase();
  const segment = $("segment").value;
  const industry = $("industry").value;
  const klassv = $("klass").value;
  const bias = $("bias").value;
  const overlay = $("overlay").value;
  const setup = $("setup").value;
  const ownNarrow = $("ownNarrow").value;
  const niftyOnly = $("niftyOnly").checked;
  const hideUncl = $("hideUnclassified").checked;
  const source = tab === "mylist" ? (DATA.tables.full || []).filter(r => isWatched(r.SYMBOL)) : (DATA.tables[tab] || []);
  return source.filter(r => {
    if (q && !(String(r.SYMBOL || "").toUpperCase().includes(q) || String(r.NAME || "").toUpperCase().includes(q))) return false;
    if (segment !== "Any" && r.Segment !== segment) return false;
    if (industry !== "Any" && r.Industry !== industry) return false;
    if (klassv !== "Any" && r.CPR_Class !== klassv) return false;
    if (bias !== "Any" && r.Bias !== bias) return false;
    if (overlay !== "Any" && r.Overlay !== overlay) return false;
    if (setup !== "Any" && r.Setup !== setup) return false;
    if (ownNarrow === "Yes" && !r.Own_Narrow) return false;
    if (ownNarrow === "No" && r.Own_Narrow) return false;
    if (niftyOnly && r.Nifty500 !== true) return false;
    if (hideUncl && (r.Industry === "Unclassified" || r.Industry === "Diversified")) return false;

    // Presets filter
    if (currentPreset === "triple_conf" && (r.Triple_Confluence !== "Bullish" && Number(r.Confluence_Score || 0) < 4)) return false;
    if (currentPreset === "nr7" && !r.NR7 && !r.NR4) return false;
    if (currentPreset === "virgin" && (!r.Virgin_CPR || r.Virgin_CPR === "None")) return false;
    if (currentPreset === "narrow" && r.CPR_Class !== "Narrow" && !r.Own_Narrow) return false;
    if (currentPreset === "high_confluence" && Number(r.Confluence_Score || 0) < 3) return false;
    if (currentPreset === "fo_movers" && r.Segment !== "F&O + Cash") return false;
    if (currentPreset === "bullish_geom" && r.Bias !== "Bullish" && r.Overlay !== "Higher") return false;
    if (currentPreset === "bearish_geom" && r.Bias !== "Bearish" && r.Overlay !== "Lower") return false;

    return true;
  });
}

function sortRows(data) {
  if (!sort.col) return data;
  const col = sort.col;
  const dir = sort.asc ? 1 : -1;
  return data.slice().sort((a, b) => {
    const va = a[col], vb = b[col];
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
    return String(va).localeCompare(String(vb)) * dir;
  });
}

function copyTvWatchlist() {
  const current = rows();
  if (!current.length) { showToast("No symbols in current view to copy."); return; }
  const symbols = current.map(r => `NSE:${r.SYMBOL.replace(/\.NS$/, '')}`).join(", ");
  navigator.clipboard.writeText(symbols).then(() => {
    showToast(`✓ Copied ${current.length} symbols to clipboard in TradingView format!`);
  }).catch(() => {
    showToast("Clipboard copy failed. Please grant permission.");
  });
}

function cprMiniChart(row) {
  const pivot = finite(row.Pivot), bc = finite(row.BC), tc = finite(row.TC), close = finite(row.CLOSE);
  const levels = [pivot, bc, tc, close].filter(value => value !== null);
  if (!levels.length) return `<div class="drawer-explanation">CPR levels unavailable for this row.</div>`;
  const min = Math.min(...levels), max = Math.max(...levels);
  const span = Math.max(max - min, Math.abs(max) * 0.0025, 0.01);
  const low = min - span * 0.35, high = max + span * 0.35;
  const y = value => 148 - ((value - low) / (high - low)) * 112;
  const bandLow = bc === null || tc === null ? null : Math.min(bc, tc);
  const bandHigh = bc === null || tc === null ? null : Math.max(bc, tc);
  const bandTop = bandHigh === null ? 0 : y(bandHigh);
  const bandHeight = bandLow === null ? 0 : Math.max(2, y(bandLow) - bandTop);
  const marks = [];
  if (bandLow !== null) marks.push(`<rect class="band" x="38" y="${bandTop.toFixed(1)}" width="344" height="${bandHeight.toFixed(1)}" rx="4"/>`);
  if (pivot !== null) marks.push(`<line class="level-line" x1="38" x2="382" y1="${y(pivot).toFixed(1)}" y2="${y(pivot).toFixed(1)}"/>`);
  if (close !== null) marks.push(`<line class="price-line" x1="38" x2="382" y1="${y(close).toFixed(1)}" y2="${y(close).toFixed(1)}"/><circle class="price-dot" cx="382" cy="${y(close).toFixed(1)}" r="4"/>`);
  const label = value => value === null ? "—" : Number(value).toFixed(2);
  return `<div class="cpr-chart" role="img" aria-label="CPR band mini chart for ${esc(row.SYMBOL)}">
    <svg viewBox="0 0 420 180" preserveAspectRatio="xMidYMid meet">
      ${marks.join("")}
      <text x="6" y="${Math.min(170, Math.max(18, (bandTop || 18) + 4)).toFixed(1)}">CPR</text>
      <text x="38" y="172">BC ${esc(label(bc))}</text>
      <text x="130" y="172">TC ${esc(label(tc))}</text>
      <text x="220" y="172">Pivot ${esc(label(pivot))}</text>
      <text x="320" y="172">Close ${esc(label(close))}</text>
    </svg>
  </div>`;
}

function initTradingViewChart(row) {
  const container = $("tv-chart-container");
  if (!container) return;
  container.innerHTML = "";
  if (typeof LightweightCharts === "undefined") {
    container.innerHTML = cprMiniChart(row);
    return;
  }
  try {
    activeTvChart = LightweightCharts.createChart(container, {
      width: container.clientWidth || 500,
      height: 240,
      layout: { background: { color: "#0d1117" }, textColor: "#8b949e", fontSize: 11 },
      grid: { vertLines: { color: "#161b22" }, horzLines: { color: "#161b22" } },
      timeScale: { borderColor: "#30363d", visible: true },
      rightPriceScale: { borderColor: "#30363d" },
    });
    const candleSeries = activeTvChart.addCandlestickSeries({
      upColor: "#3ecf8e", downColor: "#f85149", borderVisible: false, wickUpColor: "#3ecf8e", wickDownColor: "#f85149"
    });

    const open = finite(row.OPEN) || finite(row.CLOSE);
    const high = finite(row.HIGH) || finite(row.CLOSE);
    const low = finite(row.LOW) || finite(row.CLOSE);
    const close = finite(row.CLOSE);

    if (close !== null) {
      candleSeries.setData([
        { time: DATA.date ? `${DATA.date.slice(0,4)}-${DATA.date.slice(4,6)}-${DATA.date.slice(6,8)}` : "2026-08-14", open, high, low, close }
      ]);
    }

    const tc = finite(row.TC) || finite(row.CPR_Top);
    const pivot = finite(row.Pivot);
    const bc = finite(row.BC) || finite(row.CPR_Bottom);

    if (tc !== null) candleSeries.createPriceLine({ price: tc, color: '#3ecf8e', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'TC (Top Central)' });
    if (pivot !== null) candleSeries.createPriceLine({ price: pivot, color: '#f5a623', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'Pivot' });
    if (bc !== null) candleSeries.createPriceLine({ price: bc, color: '#f85149', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'BC (Bottom Central)' });

    activeTvChart.timeScale().fitContent();
  } catch (e) {
    container.innerHTML = cprMiniChart(row);
  }
}

function detailItem(label, value) {
  return `<div class="detail-item"><span>${esc(label)}</span><strong>${esc(value === null || value === undefined || value === "" ? "—" : value)}</strong></div>`;
}

function openDrawer(row, trigger) {
  drawerTrigger = trigger || null;
  $("drawerTitle").textContent = row.SYMBOL || "Symbol";
  $("drawerSubtitle").textContent = [row.NAME, row.Industry, row.Segment].filter(Boolean).join(" · ");
  const confirmation = row.Strategy_Confirmation;
  const squeezeBadge = row.NR7 ? "⚡ NR7 Squeeze" : (row.NR4 ? "⚡ NR4 Squeeze" : "Standard");
  const virginBadge = row.Virgin_CPR && row.Virgin_CPR !== "None" ? `✨ ${row.Virgin_CPR}` : "Standard";
  const stratLabel = row.Strategy_Setup ? `${row.Strategy_Type || 'CPR'} · ${row.Strategy_Setup}` : (row.Strategy_Type || 'Standard');
  $("drawerBody").innerHTML = `
    <section class="drawer-section drawer-summary">
      <div class="detail-item"><span>Narrow CPR Setup</span><strong>${badgeHtml(row.Setup || 'No setup')}</strong></div>
      <div class="detail-item"><span>Wide Strategy</span><strong>${esc(stratLabel)} ${confirmation ? badgeHtml(confirmation) : ''}</strong></div>
      <div class="detail-item"><span>Confluence Score</span><strong style="color:var(--accent);font-size:16px;">${esc(row.Confluence_Score || '0')} / 6</strong></div>
    </section>
    <section class="drawer-section">
      <h3>Next-Session CPR Chart (TC / Pivot / BC)</h3>
      <div id="tv-chart-container"></div>
    </section>
    <section class="drawer-section">
      <h3>Context &amp; Key Levels</h3>
      <div class="detail-grid">
        ${detailItem("Close Price", "₹" + fmt("CLOSE", row.CLOSE))}
        ${detailItem("CPR Width %", fmt("CPR_Width_Pct", row.CPR_Width_Pct))}
        ${detailItem("CPR Position", row.Price_Position)}
        ${detailItem("Overlay", row.Overlay)}
        ${detailItem("Multi-Day Squeeze", squeezeBadge)}
        ${detailItem("Virgin CPR", virginBadge)}
        ${detailItem("60-Day Turnover", "₹" + fmt("Value_60d", row.Value_60d) + " cr")}
        ${detailItem("Trend (SMA)", `${row.Above_SMA50 === true ? "Above" : "Below"} SMA50 · ${row.Above_SMA100 === true ? "Above" : "Below"} SMA100`)}
      </div>
    </section>
    <section class="drawer-section" style="display:flex;gap:8px;align-items:center;">
      <button id="drawerWatchButton" class="btn btn-primary" type="button"></button>
      <a id="drawerTvButton" href="https://in.tradingview.com/chart/?symbol=NSE%3A${encodeURIComponent(row.SYMBOL || '').replace(/\.NS$/, '')}" target="_blank" class="btn btn-secondary" style="text-decoration:none;">
        <span>🚀 Open on TradingView.com</span>
      </a>
    </section>
    <section class="drawer-section">
      <h3>Trade Setup Plan</h3>
      <p style="margin:0;color:var(--text);font-size:12px;line-height:1.6;">${esc(row.Strategy_Explanation || row.Signal_Explanation || "No explanation available.")}</p>
    </section>`;
  $("drawerBackdrop").hidden = false;
  $("symbolDrawer").hidden = false;
  updateDrawerAction(row.SYMBOL);
  $("drawerWatchButton").addEventListener("click", () => toggleWatchlist(row.SYMBOL));
  document.body.classList.add("drawer-open");
  setTimeout(() => initTradingViewChart(row), 50);
  $("drawerClose").focus();
}

function closeDrawer() {
  $("drawerBackdrop").hidden = true;
  $("symbolDrawer").hidden = true;
  document.body.classList.remove("drawer-open");
  if (drawerTrigger) drawerTrigger.focus();
  drawerTrigger = null;
}

function renderSectorHeatmap() {
  const rows = DATA.tables.full || [];
  const secMap = {};
  rows.forEach(r => {
    const sec = r.Industry || "Diversified";
    if (!secMap[sec]) secMap[sec] = { total: 0, above: 0, below: 0, inside: 0, narrow: 0 };
    secMap[sec].total++;
    if (r.Price_Position === "Above CPR") secMap[sec].above++;
    else if (r.Price_Position === "Below CPR") secMap[sec].below++;
    else secMap[sec].inside++;
    if (r.CPR_Class === "Narrow" || r.Own_Narrow) secMap[sec].narrow++;
  });
  const sorted = Object.entries(secMap).sort((a,b) => b[1].total - a[1].total);
  $("heatmapGrid").innerHTML = sorted.map(([sec, s]) => {
    const bullPct = Math.round((s.above / s.total) * 100);
    const bearPct = Math.round((s.below / s.total) * 100);
    return `<div class="sector-card" data-sector="${esc(sec)}">
      <div class="sector-name"><span>${esc(sec)}</span><span style="color:var(--muted)">${s.total}</span></div>
      <div class="sector-meta"><span>🟢 ${bullPct}% Above</span><span>🔴 ${bearPct}% Below</span><span>⚡ ${s.narrow} Narrow</span></div>
      <div class="sector-bar-outer"><div class="sector-bar-bull" style="width:${bullPct}%"></div><div class="sector-bar-bear" style="width:${bearPct}%"></div></div>
    </div>`;
  }).join("");

  $("heatmapGrid").querySelectorAll("[data-sector]").forEach(card => {
    card.addEventListener("click", () => {
      $("industry").value = card.dataset.sector;
      $("heatmapModal").hidden = true;
      $("heatmapBackdrop").hidden = true;
      render();
    });
  });
  $("heatmapModal").hidden = false;
  $("heatmapBackdrop").hidden = false;
}

function render() {
  const data = sortRows(rows());
  const htf = DATA.htf || {};
  let extra = "";
  if (tab === "weekly" && htf.weekly_applies) extra = ` · applies to ${htf.weekly_applies}`;
  if (tab === "monthly" && htf.monthly_applies) extra = ` · applies to ${htf.monthly_applies}`;
  if (tab === "follow") extra = " · prior-day setups vs this session's close";
  if (tab === "gainers") extra = " · Top 25 outperforming stocks ranked by % gain";
  if (tab === "losers") extra = " · Top 25 underperforming stocks ranked by % loss";
  if (tab === "watchlist") extra = " · every setup with levels to trade next session";
  if (tab === "best") extra = " · Daily Long/Short ranked by confluence, liquid, F&O first";
  if (tab === "mylist") extra = " · symbols saved in this browser";
  if (tab === "bullish") extra = " · strict narrow CPR + close above band + bullish geometry";
  if (tab === "bullish_bias") extra = " · all bullish CPR geometry; not necessarily a narrow breakout";
  $("count").textContent = `${data.length} rows${extra}`;
  const badgeEl = $("tvCountBadge");
  if (badgeEl) badgeEl.textContent = String(data.length);

  const cols = tab === "follow" ? FOLLOW_COLS : ($("columnMode").value === "research" ? COLS : COMPACT_COLS);
  const guide = $("emptyGuide");
  guide.hidden = data.length !== 0;
  if (!data.length) {
    const watchCount = (DATA.tables.watchlist || []).length;
    guide.innerHTML = tab === "best"
      ? `No confirmed Long/Short setups for this session. ${watchCount} watchlist candidate${watchCount === 1 ? "" : "s"} available. <button class="btn btn-primary" type="button" data-empty-tab="watchlist">Open Watchlist</button> <button class="btn btn-secondary" type="button" id="btnResetEmpty">Reset Filters</button>`
      : `No rows match this view and current filters. <button class="btn btn-secondary" type="button" id="btnResetEmpty">Reset Filters</button>`;
    guide.querySelectorAll("[data-empty-tab]").forEach(button => button.addEventListener("click", () => selectTab(button.dataset.emptyTab)));
    const btnR = $("btnResetEmpty");
    if (btnR) btnR.addEventListener("click", resetFilters);
  }
  $("head").innerHTML = "<tr>" + cols.map(c =>
    `<th data-col="${c}" class="${sort.col===c?(sort.asc?'sorted asc':'sorted desc'):''}">${c.replaceAll("_"," ")}${sort.col===c?(sort.asc?' ▲':' ▼'):''}</th>`
  ).join("") + "</tr>";
  $("body").innerHTML = data.map((r, index) =>
    `<tr tabindex="0" data-symbol="${esc(r.SYMBOL)}" data-index="${index}" title="Open symbol context">` + cols.map(c => `<td class="${klass(c, r[c])}">${cellHtml(c, r[c], r)}</td>`).join("") + "</tr>"
  ).join("");
  document.querySelectorAll("#body tr[data-index]").forEach(tr => {
    const open = () => openDrawer(data[Number(tr.dataset.index)], tr);
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
    });
  });
  document.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (sort.col === col) sort.asc = !sort.asc; else { sort.col = col; sort.asc = true; }
    render();
  }));
  syncUrl();
}

function selectTab(nextTab) {
  const selected = document.querySelector(`.tabs button[data-tab="${nextTab}"]`);
  if (!selected) return;
  document.querySelectorAll(".tabs button").forEach(b => { b.classList.remove("on"); b.setAttribute("aria-selected", "false"); });
  selected.classList.add("on");
  selected.setAttribute("aria-selected", "true");
  tab = nextTab;
  if (nextTab === "full") {
    currentPreset = "all";
    document.querySelectorAll(".strategy-pills .pill").forEach(p => p.classList.remove("active"));
    const allPill = document.querySelector('.pill[data-preset="all"]');
    if (allPill) allPill.classList.add("active");
  }
  render();
}

function syncUrl() {
  const p = new URLSearchParams();
  if (tab !== "best") p.set("tab", tab);
  if ($("search").value) p.set("q", $("search").value);
  if ($("segment").value !== "Any") p.set("seg", $("segment").value);
  if ($("industry").value !== "Any") p.set("ind", $("industry").value);
  if ($("klass").value !== "Any") p.set("cls", $("klass").value);
  if ($("bias").value !== "Any") p.set("bias", $("bias").value);
  if ($("overlay").value !== "Any") p.set("ovl", $("overlay").value);
  if ($("setup").value !== "Any") p.set("setup", $("setup").value);
  if ($("ownNarrow").value !== "Any") p.set("nn", $("ownNarrow").value);
  if ($("columnMode").value !== "compact") p.set("cols", $("columnMode").value);
  if ($("niftyOnly").checked) p.set("nifty", "1");
  if ($("hideUnclassified").checked) p.set("hide", "1");
  const h = p.toString();
  if (window.location.hash !== "#" + h) {
    history.replaceState(null, "", "#" + h);
  }
}

// Event Listeners
$("drawerClose").addEventListener("click", closeDrawer);
$("drawerBackdrop").addEventListener("click", closeDrawer);
$("copyTvBtn").addEventListener("click", copyTvWatchlist);
$("sectorHeatmapBtn").addEventListener("click", renderSectorHeatmap);
$("closeHeatmapBtn").addEventListener("click", () => { $("heatmapModal").hidden = true; $("heatmapBackdrop").hidden = true; });
$("heatmapBackdrop").addEventListener("click", () => { $("heatmapModal").hidden = true; $("heatmapBackdrop").hidden = true; });
$("rulesModalBtn").addEventListener("click", () => { $("rulesModal").hidden = false; $("rulesBackdrop").hidden = false; });
$("closeRulesBtn").addEventListener("click", () => { $("rulesModal").hidden = true; $("rulesBackdrop").hidden = true; });
$("rulesBackdrop").addEventListener("click", () => { $("rulesModal").hidden = true; $("rulesBackdrop").hidden = true; });

$("toggleAdvFiltersBtn").addEventListener("click", () => {
  const panel = $("advFiltersPanel");
  panel.hidden = !panel.hidden;
});

$("resetFiltersBtn").addEventListener("click", () => {
  $("klass").value = "Any";
  $("bias").value = "Any";
  $("overlay").value = "Any";
  $("setup").value = "Any";
  $("ownNarrow").value = "Any";
  $("niftyOnly").checked = false;
  $("hideUnclassified").checked = false;
  $("segment").value = "Any";
  $("industry").value = "Any";
  $("search").value = "";
  currentPreset = "all";
  document.querySelectorAll(".pill").forEach(p => p.classList.remove("active"));
  document.querySelector('.pill[data-preset="all"]').classList.add("active");
  render();
});

$("workspaceToggleBtn").addEventListener("click", () => {
  localPanelMode = localPanelMode ? null : "views";
  renderLocalPanel();
});
$("downloadsToggleBtn").addEventListener("click", () => {
  const p = $("downloadsPanel");
  p.hidden = !p.hidden;
});
$("closeDownloadsBtn").addEventListener("click", () => { $("downloadsPanel").hidden = true; });

$("saveViewButton").addEventListener("click", () => { localPanelMode = localPanelMode === "save" ? null : "save"; renderLocalPanel(); });
$("savedViewsButton").addEventListener("click", () => { localPanelMode = localPanelMode === "views" ? null : "views"; renderLocalPanel(); });
$("manageAlertsButton").addEventListener("click", () => { localPanelMode = localPanelMode === "alerts" ? null : "alerts"; renderLocalPanel(); });
$("alertCenterButton").addEventListener("click", () => { localPanelMode = localPanelMode === "center" ? null : "center"; renderLocalPanel(); });

document.querySelectorAll(".pill").forEach(pill => {
  pill.addEventListener("click", () => {
    document.querySelectorAll(".pill").forEach(p => p.classList.remove("active"));
    pill.classList.add("active");
    currentPreset = pill.dataset.preset;
    if (currentPreset === "top20") selectTab("top20");
    else render();
  });
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    if (!$("symbolDrawer").hidden) closeDrawer();
    if (!$("heatmapModal").hidden) { $("heatmapModal").hidden = true; $("heatmapBackdrop").hidden = true; }
    if (!$("rulesModal").hidden) { $("rulesModal").hidden = true; $("rulesBackdrop").hidden = true; }
    if (!$("downloadsPanel").hidden) $("downloadsPanel").hidden = true;
  }
});

document.querySelectorAll(".tabs button").forEach(btn => {
  btn.addEventListener("click", () => {
    selectTab(btn.dataset.tab);
  });
  btn.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const buttons = Array.from(document.querySelectorAll(".tabs button"));
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = buttons[(buttons.indexOf(btn) + step + buttons.length) % buttons.length];
    selectTab(next.dataset.tab);
    next.focus();
  });
});

["search","segment","industry","klass","bias","overlay","setup","ownNarrow","columnMode"].forEach(id => {
  const el = $(id);
  if (el) el.addEventListener("input", render);
});
["niftyOnly","hideUnclassified"].forEach(id => {
  const el = $(id);
  if (el) el.addEventListener("change", render);
});

function loadingStatus(message, error = false) {
  const el = $("dataStatus");
  if (!el) return;
  el.textContent = message;
  el.style.borderColor = error ? "var(--bear)" : "var(--line)";
}

async function boot() {
  if (!PAYLOAD_URL) {
    loadingStatus("Session payload URL is missing.", true);
    return;
  }
  try {
    const response = await fetch(new URL(PAYLOAD_URL, window.location.href).href, {cache: "no-cache"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    DATA = await response.json();
    parseFilters();
    fillDates();
    fillIndustry();
    metrics();
    publicationStatus();
    downloads();
    renderLocalPanel();
    render();
  } catch (error) {
    loadingStatus(`Unable to load session data: ${error.message || error}`, true);
  }
}

loadingStatus("Loading session data…");
boot();
"""


def _write_assets(site_dir: Path) -> None:
    assets = site_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "style.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    (assets / "app.js").write_text(JS.strip() + "\n", encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    
    # Copy Lightweight Charts standalone library if present
    lw_chart = Path("lightweight-charts.standalone.production.js")
    if lw_chart.exists():
        shutil.copy2(lw_chart, assets / "lightweight-charts.standalone.production.js")
        
    # Include standalone interactive TradingView CPR dashboard if present
    tv_dash = Path("cpr_tradingview_dashboard.html")
    if tv_dash.exists():
        shutil.copy2(tv_dash, site_dir / "cpr_tradingview_dashboard.html")


def _write_page(
    result: ScanResult,
    dest: Path,
    dates: List[str],
    home_href: str,
    asset_prefix: str,
    publication: Optional[dict] = None,
    include_downloads: bool = True,
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    downloads = _write_downloads(result, dest / "downloads") if include_downloads else {}
    payload = _payload(result, downloads, dates, home_href, publication=publication)
    (dest / "payload.json").write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (dest / "index.html").write_text(_page_html(payload, asset_prefix), encoding="utf-8")
    (dest / "manifest.json").write_text(json.dumps({"date": result.date, "metrics": payload["metrics"]}, indent=2), encoding="utf-8")


def build_site(
    output_dir: Path = Path("cpr_output"),
    site_dir: Path = SITE_DIR,
    max_sessions: Optional[int] = DEFAULT_PUBLISHED_SESSIONS,
) -> List[str]:
    all_dates = discover_scan_dates(output_dir)
    if not all_dates:
        raise FileNotFoundError(f"No cpr_full_*.csv files in {output_dir}")
    dates = all_dates[:max_sessions] if max_sessions is not None and max_sessions > 0 else all_dates

    publication = read_manifest(output_dir) or {}

    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True)
    _write_assets(site_dir)

    latest = dates[0]
    for date in dates:
        source_index = all_dates.index(date)
        prev = all_dates[source_index + 1] if source_index + 1 < len(all_dates) else None
        result = load_scan_result(date, output_dir=output_dir, previous=prev)
        if date == latest and result.weekly.empty:
            result = attach_htf_to_result(result, output_dir=output_dir, write_csv=True)
        if date == latest:
            _write_page(result, site_dir, dates, home_href="./", asset_prefix="./", publication=publication)
        archive_dir = site_dir / "archive" / date
        _write_page(
            result,
            archive_dir,
            dates,
            home_href="../../",
            asset_prefix="../../",
            publication=publication,
            include_downloads=False,
        )

    archive_index = site_dir / "archive" / "index.html"
    links = "\n".join(
        f'<li><a href="{d}/">{_date_label(d)}</a></li>' for d in dates
    )
    archive_index.write_text(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/><title>Archive</title>
<link rel="stylesheet" href="../assets/style.css"/></head>
<body><header class="top"><div><p class="kicker">Archive</p><h1>Past sessions</h1></div>
<a href="../" style="color:var(--accent)">Latest</a></header>
<ul style="padding:24px;line-height:2">{links}</ul></body></html>
""",
        encoding="utf-8",
    )
    (site_dir / "archive.json").write_text(json.dumps(dates), encoding="utf-8")
    (site_dir / "publication_manifest.json").write_text(
        json.dumps(publication, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"✓ Site: {site_dir.resolve()} ({len(dates)} session(s), latest {latest})")
    return dates


def serve(site_dir: Path, port: int) -> None:
    handler = SimpleHTTPRequestHandler
    httpd = ThreadingHTTPServer(("127.0.0.1", port), lambda *a, **k: handler(*a, directory=str(site_dir), **k))
    print(f"Preview: http://127.0.0.1:{port}")
    httpd.serve_forever()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Build the EOD CPR static site")
    parser.add_argument("--output-dir", default="cpr_output")
    parser.add_argument("--site-dir", default="site")
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=DEFAULT_PUBLISHED_SESSIONS,
        help="Recent sessions to publish; use 0 for the full archive",
    )
    parser.add_argument("--serve", type=int, nargs="?", const=8504, help="Serve preview (default port 8504)")
    args = parser.parse_args(argv)
    build_site(Path(args.output_dir), Path(args.site_dir), max_sessions=args.max_sessions)
    if args.serve:
        serve(Path(args.site_dir), args.serve)


if __name__ == "__main__":
    main()
