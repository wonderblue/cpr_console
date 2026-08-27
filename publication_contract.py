"""Publication freshness, CSV schema, and atomic-site contracts."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


MANIFEST_NAME = "publication_manifest.json"
SCHEMA_VERSION = 1
DATE_RE = re.compile(r"^(?:cpr_[a-z0-9]+|cm)_(\d{8})\.csv$")

DAILY_REQUIRED = {
    "SYMBOL",
    "HIGH",
    "LOW",
    "CLOSE",
    "Pivot",
    "BC",
    "TC",
    "CPR_Bottom",
    "CPR_Top",
    "CPR_Width",
    "CPR_Width_Pct",
    "CPR_Class",
    "Bias",
    "Price_Position",
}
SHORTLIST_REQUIRED = {"SYMBOL", "CLOSE", "CPR_Width_Pct"}
HTF_REQUIRED = {"SYMBOL", "CLOSE", "CPR_Width_Pct", "CPR_Class", "Timeframe", "Applies"}
SITE_REQUIRED = {
    "index.html",
    "assets/style.css",
    "assets/app.js",
    "archive.json",
    "publication_manifest.json",
}


class PublicationContractError(RuntimeError):
    """Raised when generated data or a site artifact violates the contract."""


def _date_from_name(path: Path) -> Optional[str]:
    match = DATE_RE.match(path.name)
    return match.group(1) if match else None


def _scan_dates(output_dir: Path) -> list[str]:
    from nse_cpr_scanner import discover_scan_dates

    return discover_scan_dates(output_dir)


def _read_csv_checked(path: Path, required: set[str], allow_empty: bool = True) -> pd.DataFrame:
    if not path.exists():
        raise PublicationContractError(f"Missing required publication file: {path}")
    if path.stat().st_size == 0:
        raise PublicationContractError(f"Publication file is empty: {path}")
    try:
        frame = pd.read_csv(path)
    except Exception as exc:  # pragma: no cover - pandas supplies the detail
        raise PublicationContractError(f"Cannot read publication CSV {path}: {exc}") from exc
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise PublicationContractError(f"{path.name} is missing columns: {missing}")
    if not allow_empty and frame.empty:
        raise PublicationContractError(f"Required publication CSV has no rows: {path}")
    return frame


def validate_output_dir(output_dir: Path, expected_date: Optional[str] = None) -> dict:
    """Validate every generated daily/HTF CSV before site publication."""
    output_dir = Path(output_dir)
    dates = _scan_dates(output_dir)
    if not dates:
        raise PublicationContractError(f"No cpr_full_YYYYMMDD.csv files in {output_dir}")
    if expected_date and expected_date not in dates:
        raise PublicationContractError(
            f"Expected scan date {expected_date} is not present in {output_dir}"
        )

    checked = 0
    row_counts: dict[str, int] = {}
    from nse_cpr_scanner import resolve_scan_csv

    for date in dates:
        full = _read_csv_checked(resolve_scan_csv("full", date, output_dir), DAILY_REQUIRED, allow_empty=False)
        row_counts[date] = int(len(full))
        checked += 1
        for suffix in ("narrow", "bullish", "bearish", "top20_narrow", "best", "watchlist"):
            path = resolve_scan_csv(suffix, date, output_dir)
            if path.exists():
                _read_csv_checked(path, SHORTLIST_REQUIRED, allow_empty=True)
                checked += 1
        for suffix in ("weekly", "monthly"):
            path = resolve_scan_csv(suffix, date, output_dir)
            if path.exists():
                _read_csv_checked(path, HTF_REQUIRED, allow_empty=True)
                checked += 1

    return {
        "latest_date": dates[0],
        "dates": dates,
        "files_checked": checked,
        "rows_by_date": row_counts,
    }


def _history_dates(output_dir: Path) -> list[str]:
    history_dir = output_dir / "bhavcopy"
    return sorted(
        {
            match.group(1)
            for path in history_dir.glob("cm_*.csv")
            if (match := re.match(r"cm_(\d{8})\.csv$", path.name))
        },
        reverse=True,
    )


def build_manifest(
    output_dir: Path,
    requested_date: Optional[str],
    actual_date: str,
    attempted_dates: Iterable[str],
    source_mode: str,
    lookback: int,
) -> dict:
    """Build the publication metadata written beside the generated CSVs."""
    attempted = list(dict.fromkeys(str(value) for value in attempted_dates))
    history = _history_dates(Path(output_dir))
    freshness_status = "unknown" if source_mode == "site_only" else "known"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "requested_date": requested_date,
        "actual_data_date": actual_date,
        "source": {
            "name": "NSE UDI bhavcopy",
            "mode": source_mode,
            "attempted_dates": attempted,
        },
        "freshness": {
            "status": freshness_status,
            "display": (
                "Source session verified"
                if freshness_status == "known"
                else "Legacy archive freshness unknown"
            ),
        },
        "history": {
            "requested_sessions": int(lookback),
            "cached_sessions": len(history),
            "oldest_cached_date": history[-1] if history else None,
        },
    }


def write_manifest(output_dir: Path, manifest: dict) -> Path:
    """Atomically write the manifest into the generated-data directory."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / MANIFEST_NAME
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return destination


def read_manifest(output_dir: Path) -> Optional[dict]:
    path = Path(output_dir) / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PublicationContractError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PublicationContractError(f"Manifest must contain a JSON object: {path}")
    return value


def ensure_manifest(
    output_dir: Path,
    actual_date: str,
    requested_date: Optional[str] = None,
    source_mode: str = "site_only",
    lookback: int = 0,
) -> dict:
    """Load a manifest or create an explicit legacy/unknown manifest."""
    existing = read_manifest(output_dir)
    if existing is not None:
        return existing
    return build_manifest(
        output_dir=output_dir,
        requested_date=requested_date,
        actual_date=actual_date,
        attempted_dates=[actual_date],
        source_mode=source_mode,
        lookback=lookback,
    )


def validate_manifest(
    manifest: dict,
    available_dates: Iterable[str],
    require_known: bool = False,
) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise PublicationContractError(
            f"Unsupported publication manifest schema: {manifest.get('schema_version')}"
        )
    actual = str(manifest.get("actual_data_date", ""))
    if actual not in set(available_dates):
        raise PublicationContractError(
            f"Manifest actual_data_date {actual!r} is not present in the generated CSV archive"
        )
    freshness = manifest.get("freshness") or {}
    if require_known and freshness.get("status") != "known":
        raise PublicationContractError(
            f"Publication freshness is not verified: {freshness.get('display', 'unknown')}"
        )


def validate_site_dir(site_dir: Path, expected_date: Optional[str] = None) -> None:
    site_dir = Path(site_dir)
    missing = [str(site_dir / item) for item in SITE_REQUIRED if not (site_dir / item).is_file()]
    if missing:
        raise PublicationContractError(f"Generated site is incomplete: missing {missing}")
    html = (site_dir / "index.html").read_text(encoding="utf-8")
    if "window.CPR_PAYLOAD_URL" not in html:
        raise PublicationContractError("Generated site is missing window.CPR_PAYLOAD_URL")
    if expected_date and expected_date not in html:
        raise PublicationContractError(
            f"Generated site does not contain expected latest date {expected_date}"
        )
    payload_path = site_dir / "payload.json"
    if not payload_path.is_file():
        raise PublicationContractError("Generated site is missing payload.json")
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PublicationContractError(f"Invalid site payload.json: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), dict):
        raise PublicationContractError("site/payload.json must contain a tables object")
    if expected_date and payload.get("date") != expected_date:
        raise PublicationContractError(
            f"site/payload.json date does not match expected latest date {expected_date}"
        )
    try:
        archive = json.loads((site_dir / "archive.json").read_text(encoding="utf-8"))
    except Exception as exc:
        raise PublicationContractError(f"Invalid site archive.json: {exc}") from exc
    if not isinstance(archive, list) or not archive:
        raise PublicationContractError("site/archive.json must contain at least one session date")


def atomic_publish_dir(staging_dir: Path, destination_dir: Path) -> None:
    """Replace a site directory atomically within one filesystem."""
    staging_dir = Path(staging_dir)
    destination_dir = Path(destination_dir)
    if not staging_dir.is_dir():
        raise PublicationContractError(f"Staging site does not exist: {staging_dir}")
    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = destination_dir.parent / f".{destination_dir.name}.previous"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    moved_old = False
    try:
        if destination_dir.exists():
            os.replace(destination_dir, backup_dir)
            moved_old = True
        os.replace(staging_dir, destination_dir)
    except Exception:
        if destination_dir.exists():
            shutil.rmtree(destination_dir)
        if moved_old and backup_dir.exists():
            os.replace(backup_dir, destination_dir)
        raise
    finally:
        if backup_dir.exists():
            shutil.rmtree(backup_dir)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Validate CPR publication data and site artifacts")
    parser.add_argument("--output-dir", default="cpr_output")
    parser.add_argument("--site-dir", default="site")
    parser.add_argument("--require-known", action="store_true")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output = validate_output_dir(output_dir)
    manifest = read_manifest(output_dir)
    if manifest is None:
        raise PublicationContractError(f"Missing {output_dir / MANIFEST_NAME}")
    validate_manifest(manifest, output["dates"], require_known=args.require_known)
    validate_site_dir(Path(args.site_dir), expected_date=output["latest_date"])
    print(
        f"Publication contract OK: latest={output['latest_date']} "
        f"files_checked={output['files_checked']}"
    )


if __name__ == "__main__":
    main()
