"""Canonical CPR mathematics and scalar/dataframe classifications.

All CPR consumers should use this module for the numerical levels and the
standard width denominator. Compatibility-specific column names belong in the
calling module, not in the calculation itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
import pandas as pd


CPR_NARROW_MAX_PCT = 0.25
CPR_WIDE_MIN_PCT = 0.75


@dataclass(frozen=True)
class CPRLevels:
    """Canonical CPR levels for one completed OHLC bar."""

    pivot: float
    bc: float
    tc: float
    bottom: float
    top: float
    width: float
    width_pct: float

    def as_dict(self) -> dict[str, float]:
        """Return the canonical field names used by scalar consumers."""
        return {
            "pivot": self.pivot,
            "bc": self.bc,
            "tc": self.tc,
            "cpr_bottom": self.bottom,
            "cpr_top": self.top,
            "cpr_width": self.width,
            "cpr_width_pct": self.width_pct,
        }


def calculate_cpr(high: float, low: float, close: float) -> CPRLevels:
    """Calculate CPR from a completed bar using close as the width denominator."""
    values = (float(high), float(low), float(close))
    if not all(isfinite(value) for value in values):
        raise ValueError("High, low, and close must be finite numbers")
    if close <= 0:
        raise ValueError("Close must be positive")

    pivot = (high + low + close) / 3.0
    bc = (high + low) / 2.0
    tc = 2.0 * pivot - bc
    top = max(bc, tc)
    bottom = min(bc, tc)
    width = top - bottom
    width_pct = width / close * 100.0
    return CPRLevels(pivot, bc, tc, bottom, top, width, width_pct)


def classify_width(
    width_pct: float,
    narrow_max_pct: float = CPR_NARROW_MAX_PCT,
    wide_min_pct: float = CPR_WIDE_MIN_PCT,
) -> str:
    """Return the shared three-class width label with inclusive boundaries."""
    try:
        value = float(width_pct)
    except (TypeError, ValueError):
        return "Unknown"
    if not isfinite(value):
        return "Unknown"
    if value <= narrow_max_pct:
        return "Narrow"
    if value >= wide_min_pct:
        return "Wide"
    return "Moderate"


def classify_bias(pivot: float, bc: float) -> str:
    """Return the shared bias label."""
    if pivot > bc:
        return "Bullish"
    if pivot < bc:
        return "Bearish"
    return "Neutral"


def classify_price_position(close: float, bottom: float, top: float) -> str:
    """Return the EOD-compatible position label for a close versus the CPR."""
    if any(pd.isna(value) for value in (close, bottom, top)):
        return "Unknown"
    if close > top:
        return "Above CPR"
    if close < bottom:
        return "Below CPR"
    return "Inside CPR"


def classify_virgin_cpr(high: float, low: float, bottom: float, top: float) -> str:
    """Check if price traded completely outside the active CPR band during the session."""
    if any(pd.isna(value) for value in (high, low, bottom, top)):
        return "None"
    if float(low) > float(top):
        return "Bullish Virgin"
    if float(high) < float(bottom):
        return "Bearish Virgin"
    return "None"


def calculate_cpr_frame(
    frame: pd.DataFrame,
    high_col: str,
    low_col: str,
    close_col: str,
    narrow_max_pct: float = CPR_NARROW_MAX_PCT,
    wide_min_pct: float = CPR_WIDE_MIN_PCT,
) -> pd.DataFrame:
    """Calculate canonical CPR columns for a pandas frame."""
    high = pd.to_numeric(frame[high_col], errors="coerce")
    low = pd.to_numeric(frame[low_col], errors="coerce")
    close = pd.to_numeric(frame[close_col], errors="coerce")

    result = pd.DataFrame(index=frame.index)
    result["pivot"] = (high + low + close) / 3.0
    result["bc"] = (high + low) / 2.0
    result["tc"] = 2.0 * result["pivot"] - result["bc"]
    result["top"] = result[["bc", "tc"]].max(axis=1)
    result["bottom"] = result[["bc", "tc"]].min(axis=1)
    result["width"] = result["top"] - result["bottom"]
    denominator = close.where(close > 0)
    result["width_pct"] = result["width"].div(denominator).mul(100.0)
    result["width_class"] = [
        classify_width(value, narrow_max_pct, wide_min_pct)
        for value in result["width_pct"]
    ]
    result["bias"] = [
        classify_bias(pivot, bc) if pd.notna(pivot) and pd.notna(bc) else "Unknown"
        for pivot, bc in zip(result["pivot"], result["bc"])
    ]
    result["price_position"] = [
        classify_price_position(close_value, bottom, top)
        for close_value, bottom, top in zip(
            close, result["bottom"], result["top"]
        )
    ]
    return result
