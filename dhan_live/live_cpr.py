"""
Live CPR rows from previous-session OHLC + Dhan live quotes.

Uses the same CPREngine formulas as the Shah console. Does not modify it.
CPR for the current session always comes from the last *completed* daily bar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional

import pandas as pd
from zoneinfo import ZoneInfo

from cpr_engine import CPREngine, OpenVsCPR
from universe import classify_symbol, from_yahoo

IST = ZoneInfo("Asia/Kolkata")


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def detect_event(prev_ltp: Optional[float], ltp: Optional[float], cpr_bottom: float, cpr_top: float) -> str:
    if ltp is None or prev_ltp is None:
        return ""
    if prev_ltp <= cpr_top < ltp:
        return "Crossed TC"
    if prev_ltp >= cpr_bottom > ltp:
        return "Crossed BC"
    if prev_ltp < cpr_bottom <= ltp <= cpr_top:
        return "Entered CPR"
    if prev_ltp > cpr_top >= ltp >= cpr_bottom:
        return "Entered CPR"
    if cpr_bottom <= prev_ltp <= cpr_top and ltp > cpr_top:
        return "Left above"
    if cpr_bottom <= prev_ltp <= cpr_top and ltp < cpr_bottom:
        return "Left below"
    return ""


def build_live_rows(
    prev_sessions: Dict[str, dict],
    quotes: Dict[str, dict],
    previous_ltps: Optional[Dict[str, float]] = None,
    engine: Optional[CPREngine] = None,
    now: Optional[datetime] = None,
) -> pd.DataFrame:
    engine = engine or CPREngine()
    now = now or datetime.now(IST)
    t = now.astimezone(IST).time()
    session_open = (now.astimezone(IST).weekday() < 5) and (
        (t.hour > 9 or (t.hour == 9 and t.minute >= 15))
        and (t.hour < 15 or (t.hour == 15 and t.minute <= 30))
    )

    previous_ltps = previous_ltps or {}
    rows: List[dict] = []
    for symbol, prev in prev_sessions.items():
        sid = str(prev.get("security_id", ""))
        quote = quotes.get(sid, {})
        if not quote and sid:
            try:
                quote = quotes.get(str(int(float(sid))), {})
            except (TypeError, ValueError):
                quote = {}
        high, low, close = _num(prev.get("high")), _num(prev.get("low")), _num(prev.get("close"))
        ltp = _num(quote.get("ltp"))
        day_open = _num(quote.get("open"))
        day_high = _num(quote.get("high"))
        day_low = _num(quote.get("low"))
        if high is None or low is None or close is None or close <= 0:
            rows.append(
                {
                    "Symbol": symbol,
                    "Segment": classify_symbol(symbol),
                    "Data Status": "No previous session",
                }
            )
            continue
        cpr = engine.calculate_cpr(high, low, close)
        position = engine.determine_position(ltp, cpr["cpr_bottom"], cpr["cpr_top"]) if ltp is not None else None
        virgin = engine.determine_virgin_cpr(day_high, day_low, cpr["cpr_top"], cpr["cpr_bottom"], session_open)
        open_vs = engine.determine_open_vs_cpr(day_open, cpr["cpr_bottom"], cpr["cpr_top"])
        dist = None
        if ltp is not None and cpr["pivot"]:
            dist = ((ltp - cpr["pivot"]) / cpr["pivot"]) * 100
        event = detect_event(previous_ltps.get(symbol), ltp, cpr["cpr_bottom"], cpr["cpr_top"])
        rows.append(
            {
                "Symbol": symbol,
                "Name": prev.get("name") or "",
                "Segment": classify_symbol(symbol),
                "LTP": ltp,
                "Day Open": day_open,
                "Day High": day_high,
                "Day Low": day_low,
                "Prev Close": close,
                "Prev Date": prev.get("date"),
                "CPR Bottom": cpr["cpr_bottom"],
                "Pivot": cpr["pivot"],
                "CPR Top": cpr["cpr_top"],
                "TC": cpr["tc"],
                "BC": cpr["bc"],
                "Width %": cpr["cpr_width_pct"],
                "Width Class": engine.determine_width_class(cpr["cpr_width_pct"]).value,
                "Bias": engine.determine_bias(cpr["pivot"], cpr["bc"]).value,
                "Position": position.value if position else "Unknown",
                "Open vs CPR": open_vs.value if isinstance(open_vs, OpenVsCPR) else str(open_vs),
                "Virgin CPR": virgin.value,
                "Dist from Pivot %": dist,
                "Event": event,
                "Data Status": "Live" if ltp is not None else "Quote missing",
                "security_id": sid,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def ltp_map(df: pd.DataFrame) -> Dict[str, float]:
    if df is None or df.empty or "LTP" not in df.columns:
        return {}
    out = {}
    for _, row in df.iterrows():
        if pd.notna(row.get("LTP")):
            out[str(row["Symbol"])] = float(row["LTP"])
    return out


def requested_symbols(raw: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for symbol in raw:
        bare = from_yahoo(symbol)
        if bare and bare not in seen:
            seen.add(bare)
            out.append(bare)
    return out
