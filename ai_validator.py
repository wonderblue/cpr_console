"""AI Validation & Prop-Desk Risk Audit Layer for CPR / J-Curve setups.

Combines quantitative market microstructure analytics with Gemini LLM reasoning
(with a deterministic prop-desk fallback engine) to evaluate:
1. Smart Money Conviction Score & Grade (0-100, A+ to C)
2. Adversarial 'Red Team' Risk Flags (Overhead friction, liquidity traps, wide stops)
3. Tactical Execution Blueprint (Entry Zone, Hard Stop Loss, T1, T2, R:R Ratio)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
import requests


def _load_env_file():
    """Lightweight loader for .env file if present."""
    for env_path in [Path(".env"), Path(__file__).parent / ".env"]:
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'").strip('"')
                        if k not in os.environ and v:
                            os.environ[k] = v
            except Exception:
                pass

_load_env_file()


def _calc_grade(score: int) -> str:
    if score >= 80:
        return "A+"
    if score >= 70:
        return "A"
    if score >= 60:
        return "B"
    return "C"


def compute_prop_desk_audit(row: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic prop-desk quantitative risk and trade plan generator."""
    sym = str(row.get("SYMBOL", "")).strip().upper()
    close = float(row.get("CLOSE") or row.get("LTP") or 0.0)
    pivot = float(row.get("Pivot") or 0.0)
    cpr_top = float(row.get("CPR_Top") or row.get("TC") or 0.0)
    cpr_bot = float(row.get("CPR_Bottom") or row.get("BC") or 0.0)
    vr = float(row.get("Value_Ratio") or 1.0)
    val = float(row.get("VALUE") or 0.0)
    val_cr = float(row.get("VALUE_CR") or (val / 1e7) or 0.0)
    conf = float(row.get("Confluence_Score") or 0.0)
    stage = str(row.get("JCurve_Stage", "None"))
    overlay = str(row.get("Overlay", "Higher"))
    width_pct = float(row.get("CPR_Width_Pct") or 1.0)
    nr7 = bool(row.get("NR7", False))
    nr4 = bool(row.get("NR4", False))
    own_narrow = bool(row.get("Own_Narrow", False))
    sma50 = bool(row.get("Above_SMA50", True))
    sma100 = bool(row.get("Above_SMA100", True))
    atr = float(row.get("ATR14") or max(close * 0.02, 1.0))
    r1 = float(row.get("R1") or (2 * pivot - cpr_bot) if pivot and cpr_bot else (close * 1.03))

    if close <= 0.0:
        return {
            "score": 0,
            "grade": "C",
            "insight": "Insufficient price data for AI validation.",
            "risk_flag": "No reliable quotes available.",
            "entry_zone": "—",
            "stop_loss": 0.0,
            "target_1": 0.0,
            "target_2": 0.0,
            "rr": "—",
        }

    # 1. Base Score calculation (max 100)
    # Stage & geometry base (40 pts)
    if stage == "Liftoff":
        geom_pts = 35 if overlay == "Higher" else 28
    elif stage == "Ready":
        geom_pts = 30 if overlay in ("Higher", "Inside") else 22
    else:
        geom_pts = 15

    # Volume & Turnover legitimacy (30 pts)
    if val_cr >= 50.0:
        turnover_pts = 15
    elif val_cr >= 20.0:
        turnover_pts = 10
    elif val_cr >= 5.0:
        turnover_pts = 5
    else:
        turnover_pts = 0  # High slippage penalty

    vr_pts = min(15, int(max(0, vr - 1.0) * 5))
    volume_pts = turnover_pts + vr_pts

    # Multi-timeframe confluence & Trend (20 pts)
    conf_pts = min(10, int(max(0, conf) * 3.5))
    trend_pts = (5 if sma50 else 0) + (5 if sma100 else 0)

    # Volatility compression bonus (10 pts)
    comp_pts = 10 if (nr7 or width_pct <= 0.25) else (7 if (nr4 or own_narrow or width_pct <= 0.50) else 3)

    raw_score = geom_pts + volume_pts + conf_pts + trend_pts + comp_pts

    # 2. Red-team penalties
    penalty = 0
    # Wide stop penalty
    stop_dist_pct = ((close - pivot) / close * 100) if stage == "Liftoff" else ((close - cpr_bot) / close * 100)
    if stop_dist_pct > 3.0:
        penalty += 10
    elif stop_dist_pct > 2.0:
        penalty += 5

    # Liquidity risk penalty
    if val_cr < 5.0 and vr > 10.0:
        penalty += 15  # Speculative low-liquidity spike trap

    final_score = int(max(10, min(100, raw_score - penalty)))
    grade = _calc_grade(final_score)

    # 3. Formulate Execution Levels
    if stage == "Liftoff":
        entry_low = round(max(cpr_top, close * 0.995), 2)
        entry_high = round(close * 1.005, 2)
        entry_zone = f"₹{entry_low:,.2f} – ₹{entry_high:,.2f}"
        sl = round(pivot if pivot > 0 else (close - atr), 2)
        risk = max(close - sl, close * 0.01)
        t1 = round(close + (1.5 * risk), 2)
        t2 = round(close + (2.5 * risk), 2)
        rr = f"1 : {round(1.5 * risk / risk, 1)} / 1 : {round(2.5 * risk / risk, 1)}"
        
        insight = (
            f"Stage 2 Liftoff: Institutional volume expansion ({vr:.1f}x) breaking above CPR Top (₹{cpr_top:,.2f}). "
            f"Ascending {overlay} overlay with +{int(conf)} multi-timeframe confluence."
        )
        if val_cr >= 50.0:
            insight += f" Mega liquidity backing (₹{val_cr:.1f}Cr turnover)."

        # Red Team Risk Flag
        risk_flags = []
        if r1 > close and (r1 - close) / close * 100 < 1.0:
            risk_flags.append(f"Proximity to R1 resistance (₹{r1:,.2f}, +{(r1-close)/close*100:.1f}%)")
        if val_cr < 10.0:
            risk_flags.append("Lower turnover tier (<₹10Cr); watch for wider execution slippage")
        if not sma100:
            risk_flags.append("Trading below 100-day SMA trendline")
        risk_flags.append(f"Invalidation on decisive slip below Pivot (₹{sl:,.2f})")
        risk_flag = " ⚠️ " + "; ".join(risk_flags) + "."

    elif stage == "Ready":
        entry_low = round(pivot if pivot > 0 else close * 0.99, 2)
        entry_high = round(cpr_top if cpr_top > 0 else close * 1.005, 2)
        entry_zone = f"₹{entry_low:,.2f} – ₹{entry_high:,.2f} (Inside CPR)"
        sl = round(cpr_bot * 0.997 if cpr_bot > 0 else (close - atr), 2)
        risk = max(close - sl, close * 0.008)
        t1 = round(cpr_top + (1.2 * risk), 2)
        t2 = round(cpr_top + (2.5 * risk), 2)
        rr = f"1 : {round((t1 - close) / risk, 1)} / 1 : {round((t2 - close) / risk, 1)}"

        insight = (
            f"Stage 1 Ready (The Hook): Support defense at Central Pivot (₹{pivot:,.2f}) with {vr:.1f}x volume accumulation. "
            f"Tight CPR width ({width_pct:.2f}%) coiling before directional liftoff."
        )
        risk_flags = []
        if val_cr < 10.0:
            risk_flags.append("Turnover under ₹10Cr; size positions conservatively")
        risk_flags.append(f"Thesis invalidates on close below CPR Bottom (₹{sl:,.2f})")
        risk_flag = " ⚠️ " + "; ".join(risk_flags) + "."

    else:
        entry_zone = f"₹{close:,.2f}"
        sl = round(pivot if pivot > 0 else close * 0.98, 2)
        t1 = round(close * 1.03, 2)
        t2 = round(close * 1.06, 2)
        rr = "1 : 1.5"
        insight = f"Neutral setup coiling near ₹{close:,.2f}."
        risk_flag = f" ⚠️ Risk invalidation below ₹{sl:,.2f}."

    return {
        "score": final_score,
        "grade": grade,
        "insight": insight,
        "risk_flag": risk_flag,
        "entry_zone": entry_zone,
        "stop_loss": sl,
        "target_1": t1,
        "target_2": t2,
        "rr": rr,
    }


def query_groq_batch_audit(candidates: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Query Groq API with llama-3.3-70b-versatile."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or not candidates:
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    prompt = (
        "You are an expert Prop-Desk Quantitative Risk Manager analyzing Indian stock CPR & J-Curve setups.\n"
        "Return a valid JSON object with key 'setups' containing an array of objects for all inputs, with keys:\n"
        "- symbol (string)\n"
        "- score (integer 0-100)\n"
        "- grade ('A+', 'A', 'B', or 'C')\n"
        "- insight (2 concise sentences on smart-money accumulation, volume legitimacy, multi-timeframe confluence)\n"
        "- risk_flag (1 sentence on adversarial Red Team risks, overhead friction, invalidation stop)\n"
        "- entry_zone (formatted string, e.g. '₹1,050.00 – ₹1,065.00')\n"
        "- stop_loss (float)\n"
        "- target_1 (float)\n"
        "- target_2 (float)\n"
        "- rr (string, e.g. '1:2.4')\n\n"
        f"Setups Data:\n{json.dumps(candidates, indent=2)}"
    )

    for model in ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional prop-desk quantitative risk manager. Always respond in valid JSON with a 'setups' array.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                result = resp.json()
                content = result["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                items = parsed.get("setups", parsed if isinstance(parsed, list) else [])
                if isinstance(items, list) and len(items) > 0:
                    return items
        except Exception:
            continue
    return None


def query_gemini_batch_audit(candidates: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Query Gemini REST API if GEMINI_API_KEY / GOOGLE_API_KEY is available."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key or not candidates:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    prompt = (
        "You are an expert Prop-Desk Quantitative Risk Manager. Analyze the following Indian stock CPR & J-Curve setups.\n"
        "For each stock, return a valid JSON array of objects with keys:\n"
        "- symbol (string)\n"
        "- score (integer 0-100)\n"
        "- grade ('A+', 'A', 'B', or 'C')\n"
        "- insight (2-3 concise sentences on smart-money accumulation, volume legitimacy, multi-timeframe confluence)\n"
        "- risk_flag (1-2 sentences on adversarial Red Team risks, overhead friction, key invalidation stop)\n"
        "- entry_zone (formatted string, e.g. '₹1,050.00 – ₹1,065.00')\n"
        "- stop_loss (float)\n"
        "- target_1 (float)\n"
        "- target_2 (float)\n"
        "- rr (string, e.g. '1:2.4')\n\n"
        f"Input Setups Data:\n{json.dumps(candidates, indent=2)}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    try:
        resp = requests.post(url, json=payload, timeout=12)
        if resp.status_code == 200:
            result = resp.json()
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            if isinstance(parsed, list) and len(parsed) == len(candidates):
                return parsed
    except Exception:
        pass
    return None


def attach_ai_validation(df: pd.DataFrame, top_n: Optional[int] = None) -> pd.DataFrame:
    """Attach AI Validation & Prop-Desk Risk Audit fields to a CPR/J-Curve DataFrame."""
    if df is None or df.empty:
        return df

    out = df.copy()
    
    # Initialize columns
    out["AI_Conviction_Score"] = 0
    out["AI_Conviction_Grade"] = "—"
    out["AI_Insight"] = ""
    out["AI_Risk_Flag"] = ""
    out["AI_Trade_Plan"] = ""
    out["AI_Entry_Zone"] = "—"
    out["AI_Stop_Loss"] = 0.0
    out["AI_Target_1"] = 0.0
    out["AI_Target_2"] = 0.0
    out["AI_RR"] = "—"

    # Identify candidates by JCurve_Stage
    if "JCurve_Stage" in out.columns:
        j_candidates = out[out["JCurve_Stage"].isin(["Liftoff", "Ready"])].copy()
    else:
        j_candidates = out.copy()

    if j_candidates.empty:
        j_candidates = out.copy()

    # Sort candidates by JCurve_Score (or Value_Ratio) descending
    sort_col = "JCurve_Score" if "JCurve_Score" in j_candidates.columns else "Value_Ratio"
    if sort_col in j_candidates.columns:
        j_candidates = j_candidates.sort_values(by=sort_col, ascending=False)

    if top_n is not None and top_n > 0:
        j_candidates = j_candidates.head(top_n)

    cand_dicts = []
    for idx, row in j_candidates.iterrows():
        c_dict = row.to_dict()
        c_dict["_orig_idx"] = idx
        cand_dicts.append(c_dict)

    # Try Groq first, then Gemini LLM, fallback to prop-desk deterministic engine
    llm_results = query_groq_batch_audit(cand_dicts) or query_gemini_batch_audit(cand_dicts)
    llm_by_sym = {str(item.get("symbol", "")).upper(): item for item in (llm_results or [])}

    for c in cand_dicts:
        idx = c["_orig_idx"]
        sym = str(c.get("SYMBOL", "")).upper()
        
        if sym in llm_by_sym:
            g = llm_by_sym[sym]
            score = int(g.get("score", 75))
            grade = str(g.get("grade", _calc_grade(score)))
            insight = str(g.get("insight", ""))
            risk = str(g.get("risk_flag", ""))
            entry = str(g.get("entry_zone", ""))
            sl = float(g.get("stop_loss", 0.0))
            t1 = float(g.get("target_1", 0.0))
            t2 = float(g.get("target_2", 0.0))
            rr = str(g.get("rr", "1:2.0"))
        else:
            audit = compute_prop_desk_audit(c)
            score = audit["score"]
            grade = audit["grade"]
            insight = audit["insight"]
            risk = audit["risk_flag"]
            entry = audit["entry_zone"]
            sl = audit["stop_loss"]
            t1 = audit["target_1"]
            t2 = audit["target_2"]
            rr = audit["rr"]

        out.loc[idx, "AI_Conviction_Score"] = score
        out.loc[idx, "AI_Conviction_Grade"] = grade
        out.loc[idx, "AI_Insight"] = insight
        out.loc[idx, "AI_Risk_Flag"] = risk
        out.loc[idx, "AI_Entry_Zone"] = entry
        out.loc[idx, "AI_Stop_Loss"] = sl
        out.loc[idx, "AI_Target_1"] = t1
        out.loc[idx, "AI_Target_2"] = t2
        out.loc[idx, "AI_RR"] = rr
        out.loc[idx, "AI_Trade_Plan"] = f"Entry: {entry} | SL: ₹{sl:,.2f} | T1: ₹{t1:,.2f} | T2: ₹{t2:,.2f} (R:R {rr})"

    return out
