"""Leakage-safe walk-forward validation for archived CPR sessions.

The validator treats each archived session as a signal session and evaluates its
outcome only against the next available completed session. It never uses a
future close while constructing the signal cohort for the source session.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from cpr_scoring import attach_confirmation_score
from nse_cpr_scanner import discover_scan_dates
from wide_cpr_strategy import attach_wide_strategy

OUTCOME_FIELDS = ("Followed", "Failed", "Flat", "No data")


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and pd.isna(value):
            return None
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(item) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (float, int)):
        return value
    return str(value)


def _read_session(output_dir: Path, date: str) -> pd.DataFrame:
    from nse_cpr_scanner import resolve_scan_csv

    path = resolve_scan_csv("full", date, output_dir)
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path)
    if "SYMBOL" not in frame.columns:
        raise ValueError(f"{path} has no SYMBOL column")
    frame["SYMBOL"] = frame["SYMBOL"].astype(str).str.strip().str.upper()
    if "Signal_Score" not in frame.columns or "Signal_Grade" not in frame.columns:
        frame = attach_confirmation_score(frame)
    if "Strategy_Type" not in frame.columns or "Strategy_Setup" not in frame.columns:
        frame = attach_wide_strategy(frame)
    return frame


def _direction_and_cohort(row: pd.Series) -> tuple[str | None, str | None]:
    setup = str(row.get("Setup", "") or "").strip()
    strategy_setup = str(row.get("Strategy_Setup", "") or "").strip()
    confirmation = str(row.get("Strategy_Confirmation", "") or "").strip()

    if strategy_setup == "Wide Upside Breakout":
        return "up", "Wide Confirmed Upside"
    if strategy_setup == "Wide Downside Breakout":
        return "down", "Wide Confirmed Downside"
    if strategy_setup == "Wide Upside Watch":
        return "up", "Wide Upside Watch"
    if strategy_setup == "Wide Downside Watch":
        return "down", "Wide Downside Watch"
    if strategy_setup == "Wide Consolidation":
        return None, "Wide Consolidation"
    if strategy_setup == "Wide Watch":
        return None, strategy_setup

    if setup in {"Long", "Watch Long"}:
        return "up", setup
    if setup in {"Short", "Watch Short"}:
        return "down", setup
    if confirmation == "Confirmed" and str(row.get("Signal_Direction", "")) == "Long":
        return "up", "Confirmed Signal Long"
    if confirmation == "Confirmed" and str(row.get("Signal_Direction", "")) == "Short":
        return "down", "Confirmed Signal Short"
    return None, None


def _outcome(direction: str | None, row: pd.Series, next_close: Any) -> str:
    if direction is None:
        return "Not directional"
    try:
        close = float(next_close)
        top = float(row["CPR_Top"])
        bottom = float(row["CPR_Bottom"])
    except (KeyError, TypeError, ValueError):
        return "No data"
    if pd.isna(close) or pd.isna(top) or pd.isna(bottom):
        return "No data"
    if direction == "up":
        if close > top:
            return "Followed"
        if close < bottom:
            return "Failed"
        return "Flat"
    if close < bottom:
        return "Followed"
    if close > top:
        return "Failed"
    return "Flat"


def _iter_dates(output_dir: Path, end_date: str | None, max_sessions: int | None) -> list[str]:
    dates = discover_scan_dates(output_dir)
    dates = sorted(dates)
    if end_date:
        dates = [date for date in dates if date <= end_date]
    if max_sessions is not None and max_sessions > 0:
        dates = dates[-max_sessions:]
    return dates


def build_walk_forward_details(
    output_dir: Path | str,
    *,
    end_date: str | None = None,
    max_sessions: int | None = None,
) -> pd.DataFrame:
    """Build one row per directional setup and its next-session outcome.

    The source row is read only from session *t*. The only future value used is
    the next available completed session's close for the same symbol, session
    *t+1*, and it is used strictly for outcome labeling.
    """
    root = Path(output_dir)
    dates = _iter_dates(root, end_date, max_sessions)
    records: list[dict[str, Any]] = []
    for source_date, next_date in zip(dates, dates[1:]):
        source = _read_session(root, source_date)
        future = _read_session(root, next_date)[["SYMBOL", "CLOSE"]].copy()
        future = future.rename(columns={"CLOSE": "Next_Close"})
        future["Next_Close"] = pd.to_numeric(future["Next_Close"], errors="coerce")
        next_close = future.set_index("SYMBOL")["Next_Close"].to_dict()
        for _, row in source.iterrows():
            direction, cohort = _direction_and_cohort(row)
            if cohort is None:
                continue
            close = next_close.get(row["SYMBOL"])
            record = {
                "Session": source_date,
                "Next_Session": next_date,
                "SYMBOL": row["SYMBOL"],
                "Cohort": cohort,
                "Direction": direction or "Non-directional",
                "Setup": _json_value(row.get("Setup")),
                "Strategy_Setup": _json_value(row.get("Strategy_Setup")),
                "Strategy_Confirmation": _json_value(row.get("Strategy_Confirmation")),
                "Signal_Direction": _json_value(row.get("Signal_Direction")),
                "Signal_Grade": _json_value(row.get("Signal_Grade")),
                "Signal_Score": _json_value(row.get("Signal_Score")),
                "CPR_Class": _json_value(row.get("CPR_Class")),
                "Next_Close": _json_value(close),
                "Outcome": _outcome(direction, row, close),
            }
            records.append(record)
    columns = [
        "Session", "Next_Session", "SYMBOL", "Cohort", "Direction", "Setup",
        "Strategy_Setup", "Strategy_Confirmation", "Signal_Direction", "Signal_Grade",
        "Signal_Score", "CPR_Class", "Next_Close", "Outcome",
    ]
    return pd.DataFrame(records, columns=columns)


def summarize_details(details: pd.DataFrame) -> pd.DataFrame:
    """Aggregate outcome rates by cohort without silently treating missing data as failure."""
    columns = [
        "Cohort", "Signals", "Followed", "Failed", "Flat", "No_Data",
        "Non_Directional", "Resolved", "Follow_Rate", "Failure_Rate", "Flat_Rate",
    ]
    if details.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for cohort, group in details.groupby("Cohort", sort=True):
        counts = group["Outcome"].value_counts()
        followed = int(counts.get("Followed", 0))
        failed = int(counts.get("Failed", 0))
        flat = int(counts.get("Flat", 0))
        no_data = int(counts.get("No data", 0))
        non_directional = int(counts.get("Not directional", 0))
        resolved = followed + failed + flat
        rows.append({
            "Cohort": cohort,
            "Signals": int(len(group)),
            "Followed": followed,
            "Failed": failed,
            "Flat": flat,
            "No_Data": no_data,
            "Non_Directional": non_directional,
            "Resolved": resolved,
            "Follow_Rate": followed / resolved if resolved else None,
            "Failure_Rate": failed / resolved if resolved else None,
            "Flat_Rate": flat / resolved if resolved else None,
        })
    return pd.DataFrame(rows, columns=columns)


def build_report(
    output_dir: Path | str,
    *,
    end_date: str | None = None,
    max_sessions: int | None = None,
) -> dict[str, Any]:
    root = Path(output_dir)
    dates = _iter_dates(root, end_date, max_sessions)
    details = build_walk_forward_details(root, end_date=end_date, max_sessions=max_sessions)
    summary = summarize_details(details)
    return {
        "schema_version": 1,
        "methodology": "Session t signal fields evaluated against next available completed session t+1 close.",
        "lookahead_policy": "No future values are used to construct source-session cohorts; next-session close is used only for outcome labeling.",
        "source_directory": str(root),
        "sessions_requested": len(dates),
        "signal_sessions_evaluated": max(0, len(dates) - 1),
        "first_session": dates[0] if dates else None,
        "last_signal_session": dates[-2] if len(dates) >= 2 else None,
        "last_outcome_session": dates[-1] if dates else None,
        "details": details.to_dict(orient="records"),
        "summary": summary.to_dict(orient="records"),
    }


def write_report(
    output_dir: Path | str,
    report_dir: Path | str,
    *,
    end_date: str | None = None,
    max_sessions: int | None = None,
) -> dict[str, Path]:
    report = build_report(output_dir, end_date=end_date, max_sessions=max_sessions)
    target = Path(report_dir)
    target.mkdir(parents=True, exist_ok=True)
    details = pd.DataFrame(report["details"])
    summary = pd.DataFrame(report["summary"])
    json_path = target / "walk_forward_report.json"
    summary_path = target / "walk_forward_summary.csv"
    details_path = target / "walk_forward_details.csv"
    safe_report = _sanitize_json(report)
    json_path.write_text(json.dumps(safe_report, indent=2, allow_nan=False), encoding="utf-8")
    summary.to_csv(summary_path, index=False)
    details.to_csv(details_path, index=False)
    return {"json": json_path, "summary": summary_path, "details": details_path}


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate CPR setups across completed sessions")
    parser.add_argument("--output-dir", default="cpr_output")
    parser.add_argument("--report-dir", default="stage5_reports")
    parser.add_argument("--end-date")
    parser.add_argument("--max-sessions", type=int)
    args = parser.parse_args(list(argv) if argv is not None else None)
    paths = write_report(args.output_dir, args.report_dir, end_date=args.end_date, max_sessions=args.max_sessions)
    report = json.loads(paths["json"].read_text(encoding="utf-8"))
    print(f"Validated {report['signal_sessions_evaluated']} signal sessions")
    print(f"Summary: {paths['summary']}")
    print(f"Details: {paths['details']}")


if __name__ == "__main__":
    main()


__all__ = [
    "build_report",
    "build_walk_forward_details",
    "summarize_details",
    "write_report",
]
