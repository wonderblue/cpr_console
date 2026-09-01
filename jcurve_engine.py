"""Dedicated J-Curve Liftoff & Hook Screening Engine for CPR setups.

Captures explosive turnaround and breakout momentum through a two-stage lifecycle:
1. Stage 1: J-Curve Ready (The Hook / Pre-Liftoff Coiling) - 0.50R Risk
2. Stage 2: J-Curve Liftoff (The Vertical Expansion) - 1.00R Risk
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_jcurve_score(
    stage: str,
    vr: float,
    val: float,
    width_pct: float,
    confluence: float,
    overlay: str,
    above_sma50: bool,
    above_sma100: bool,
) -> int:
    """Calculate an explainable 0-100 J-Curve momentum score."""
    if stage == "None":
        return 0

    base_score = 60 if stage == "Liftoff" else 45

    # 1. Volume Surge component (up to 20 pts)
    vol_pts = min(20, int(max(0, vr - 1.0) * 8))

    # 2. Volatility Compression component (up to 10 pts)
    comp_pts = 10 if width_pct <= 0.25 else (7 if width_pct <= 0.50 else 3)

    # 3. Structural Overlay component (up to 5 pts)
    ovl_pts = 5 if overlay == "Higher" else (3 if overlay == "Inside" else 0)

    # 4. Multi-timeframe Confluence (up to 5 pts)
    conf_pts = min(5, max(0, int(confluence)))

    total = base_score + vol_pts + comp_pts + ovl_pts + conf_pts
    return int(max(0, min(100, total)))


def generate_jcurve_explanation(
    stage: str,
    sym: str,
    vr: float,
    width_pct: float,
    cpr_top: float,
    cpr_bot: float,
    pivot: float,
    close: float,
    overlay: str,
    confluence: float,
    own_narrow: bool,
    nr7: bool,
) -> str:
    """Generate concise actionable trade plan commentary for J-Curve setups."""
    if stage == "Liftoff":
        vol_str = f"massive {vr:.1f}x volume surge" if vr >= 3.0 else f"strong {vr:.1f}x volume"
        squeeze_str = " (NR7 Volatility Squeeze)" if nr7 else (" (60D Narrow CPR)" if own_narrow else "")
        return (
            f"🚀 J-Curve Liftoff: Decisive kinetic breakout above CPR Top (₹{cpr_top:,.2f}) with {vol_str}{squeeze_str}. "
            f"Ascending {overlay} CPR overlay and +{confluence:.0f} confluence. "
            f"Momentum continuation target expansion above ₹{cpr_top:,.2f} with stop below Pivot (₹{pivot:,.2f})."
        )
    elif stage == "Ready":
        vol_str = f"institutional absorption ({vr:.1f}x volume)" if vr >= 1.8 else f"{vr:.1f}x volume"
        return (
            f"🪝 J-Curve Ready (The Hook): Support defense at Monthly/Weekly CPR level with {vol_str}. "
            f"Coiling inside band (₹{cpr_bot:,.2f} - ₹{cpr_top:,.2f}), closed above Central Pivot (₹{pivot:,.2f}). "
            f"Probe long setup ahead of crowd with invalidation below CPR Bottom (₹{cpr_bot:,.2f})."
        )
    return "No active J-Curve setup."


def attach_jcurve_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """Attach JCurve_Stage, JCurve_Score, and JCurve_Explanation to a CPR scan DataFrame."""
    if df is None or df.empty:
        return df

    out = df.copy()

    # Required numeric & boolean features with safe fallbacks
    close = pd.to_numeric(out["CLOSE"], errors="coerce").fillna(0.0) if "CLOSE" in out.columns else pd.Series(0.0, index=out.index)
    pivot = pd.to_numeric(out["Pivot"], errors="coerce").fillna(0.0) if "Pivot" in out.columns else pd.Series(0.0, index=out.index)
    cpr_top = pd.to_numeric(out["CPR_Top"], errors="coerce").fillna(0.0) if "CPR_Top" in out.columns else pd.Series(0.0, index=out.index)
    cpr_bot = pd.to_numeric(out["CPR_Bottom"], errors="coerce").fillna(0.0) if "CPR_Bottom" in out.columns else pd.Series(0.0, index=out.index)
    width_pct = pd.to_numeric(out["CPR_Width_Pct"], errors="coerce").fillna(1.0) if "CPR_Width_Pct" in out.columns else pd.Series(1.0, index=out.index)
    vr = pd.to_numeric(out["Value_Ratio"], errors="coerce").fillna(1.0) if "Value_Ratio" in out.columns else pd.Series(1.0, index=out.index)
    val = pd.to_numeric(out["VALUE"], errors="coerce").fillna(0.0) if "VALUE" in out.columns else pd.Series(0.0, index=out.index)
    conf = pd.to_numeric(out["Confluence_Score"], errors="coerce").fillna(0.0) if "Confluence_Score" in out.columns else pd.Series(0.0, index=out.index)

    pos = out["Price_Position"].astype(str) if "Price_Position" in out.columns else pd.Series("", index=out.index)
    bias = out["Bias"].astype(str) if "Bias" in out.columns else pd.Series("", index=out.index)
    overlay = out["Overlay"].astype(str) if "Overlay" in out.columns else pd.Series("", index=out.index)
    regime = out["Regime"].astype(str) if "Regime" in out.columns else pd.Series("Neutral", index=out.index)

    own_narrow = out["Own_Narrow"].astype(bool) if "Own_Narrow" in out.columns else pd.Series(False, index=out.index)
    nr4 = out["NR4"].astype(bool) if "NR4" in out.columns else pd.Series(False, index=out.index)
    nr7 = out["NR7"].astype(bool) if "NR7" in out.columns else pd.Series(False, index=out.index)
    above_sma50 = out["Above_SMA50"].astype(bool) if "Above_SMA50" in out.columns else pd.Series(False, index=out.index)
    above_sma100 = out["Above_SMA100"].astype(bool) if "Above_SMA100" in out.columns else pd.Series(False, index=out.index)

    # 1. Base Volatility Squeeze criteria
    is_squeeze = own_narrow | nr4 | nr7 | (width_pct <= 0.35)

    # 2. Liquidity & Institutional Turnover
    has_volume_turn = (vr >= 1.5) & (val >= 1.5e7)
    has_surge = (vr >= 1.8) & (val >= 2.0e7)

    # 3. Stage 2: J-Curve Liftoff (The Vertical Expansion)
    is_liftoff_geom = (
        (pos == "Above CPR")
        & (bias == "Bullish")
        & (overlay == "Higher")
        & ((close - cpr_top) / np.maximum(close, 1e-6) * 100 >= 0.20)
        & is_squeeze
        & has_surge
        & above_sma50
        & (conf >= 0)
    )
    # In Risk Off, Liftoff requires high defiance volume and SMA100 alignment
    liftoff_allowed = np.where(
        regime == "Risk Off",
        is_liftoff_geom & (vr >= 2.2) & above_sma100,
        is_liftoff_geom
    )

    # 4. Stage 1: J-Curve Ready (The Hook / Support Defense)
    # Price defending Pivot or CPR Bottom with institutional accumulation
    is_ready_geom = (
        (~liftoff_allowed)
        & (close >= pivot)
        & (close <= cpr_top * 1.015)
        & (overlay.isin(["Higher", "Inside", "Overlapping"]))
        & is_squeeze
        & has_volume_turn
        & (conf >= 0)
    )
    ready_allowed = np.where(
        regime == "Risk Off",
        is_ready_geom & (vr >= 1.8) & above_sma50,
        is_ready_geom
    )

    # Assign Stage
    out["JCurve_Stage"] = np.where(
        liftoff_allowed,
        "Liftoff",
        np.where(ready_allowed, "Ready", "None")
    )

    # Vectorized / Apply scoring and explanation
    scores = []
    explanations = []

    for idx, row in out.iterrows():
        stage = row["JCurve_Stage"]
        s_vr = float(vr.loc[idx])
        s_val = float(val.loc[idx])
        s_wp = float(width_pct.loc[idx])
        s_conf = float(conf.loc[idx])
        s_ovl = str(overlay.loc[idx])
        s_sma50 = bool(above_sma50.loc[idx])
        s_sma100 = bool(above_sma100.loc[idx])
        s_sym = str(row.get("SYMBOL", ""))
        s_top = float(cpr_top.loc[idx])
        s_bot = float(cpr_bot.loc[idx])
        s_piv = float(pivot.loc[idx])
        s_close = float(close.loc[idx])
        s_own = bool(own_narrow.loc[idx])
        s_nr7 = bool(nr7.loc[idx])

        score = compute_jcurve_score(
            stage=stage,
            vr=s_vr,
            val=s_val,
            width_pct=s_wp,
            confluence=s_conf,
            overlay=s_ovl,
            above_sma50=s_sma50,
            above_sma100=s_sma100,
        )
        expl = generate_jcurve_explanation(
            stage=stage,
            sym=s_sym,
            vr=s_vr,
            width_pct=s_wp,
            cpr_top=s_top,
            cpr_bot=s_bot,
            pivot=s_piv,
            close=s_close,
            overlay=s_ovl,
            confluence=s_conf,
            own_narrow=s_own,
            nr7=s_nr7,
        )
        scores.append(score)
        explanations.append(expl)

    out["JCurve_Score"] = scores
    out["JCurve_Explanation"] = explanations

    return out
