#!/usr/bin/env python3
"""Daily scan, validation, and atomic static-site publication pipeline."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from eod_site import DEFAULT_PUBLISHED_SESSIONS, build_site
from nse_cpr_scanner import (
    OUTPUT_DIR,
    HISTORY_LOOKBACK_HTF,
    candidate_session_dates,
    discover_scan_dates,
    scan_eod_cpr,
)
from publication_contract import (
    DEFAULT_MAX_ROW_DROP_PCT,
    DEFAULT_MAX_SITE_BYTES,
    DEFAULT_MIN_FULL_ROWS,
    PublicationContractError,
    atomic_publish_dir,
    build_manifest,
    ensure_manifest,
    validate_manifest,
    validate_output_dir,
    validate_site_dir,
    write_manifest,
)


def scan_latest(
    date: str | None,
    output_dir: Path,
    lookback: int = HISTORY_LOOKBACK_HTF,
) -> tuple[str, list[str]]:
    candidates = [date] if date else candidate_session_dates()
    attempted: list[str] = []
    last_error = None
    for candidate in candidates:
        attempted.append(candidate)
        print(f"Trying session {candidate}…")
        try:
            result = scan_eod_cpr(candidate, output_dir=output_dir, lookback=lookback)
            print(f"Scanned {result.date}: {result.cash_rows} EQ names")
            return result.date, attempted
        except Exception as exc:
            last_error = exc
            print(f"  skipped {candidate}: {exc}")
    raise RuntimeError(f"No bhavcopy available. Last error: {last_error}")


def _staging_dir(site_dir: Path) -> Path:
    return site_dir.parent / f".{site_dir.name}.staging"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Scan NSE EOD CPR and publish the static site")
    parser.add_argument("date", nargs="?", help="YYYYMMDD. Default: last completed weekday session")
    parser.add_argument("--site-only", action="store_true", help="Rebuild HTML from existing CSVs")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--site-dir", default="site")
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=DEFAULT_PUBLISHED_SESSIONS,
        help="Recent sessions to publish to the static site; 0 publishes all",
    )
    parser.add_argument("--max-site-bytes", type=int, default=DEFAULT_MAX_SITE_BYTES)
    parser.add_argument(
        "--lookback",
        type=int,
        default=HISTORY_LOOKBACK_HTF,
        help="Prior cash sessions to cache for overlay / own-narrow / HTF bars (default 252)",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    site_dir = Path(args.site_dir)
    if args.date and len(args.date) != 8:
        print("Date must be YYYYMMDD")
        sys.exit(1)

    if not args.site_only:
        actual_date, attempted = scan_latest(args.date, output_dir, lookback=args.lookback)
        manifest = build_manifest(
            output_dir=output_dir,
            requested_date=args.date,
            actual_date=actual_date,
            attempted_dates=attempted,
            source_mode="requested_session" if args.date else "latest_available",
            lookback=args.lookback,
        )
        write_manifest(output_dir, manifest)
    else:
        dates = discover_scan_dates(output_dir)
        if not dates:
            print(f"No CSVs in {output_dir}. Run without --site-only first.")
            sys.exit(1)
        manifest = ensure_manifest(
            output_dir,
            actual_date=dates[0],
            source_mode="site_only",
            lookback=0,
        )
        write_manifest(output_dir, manifest)

    output_contract = validate_output_dir(
        output_dir,
        min_full_rows=DEFAULT_MIN_FULL_ROWS,
        max_row_drop_pct=DEFAULT_MAX_ROW_DROP_PCT,
    )
    validate_manifest(
        manifest,
        output_contract["dates"],
        require_known=not args.site_only,
    )

    staging = _staging_dir(site_dir)
    if staging.exists():
        shutil.rmtree(staging)
    try:
        dates = build_site(output_dir, staging, max_sessions=args.max_sessions)
        validate_site_dir(
            staging,
            expected_date=dates[0],
            max_site_bytes=args.max_site_bytes,
            max_sessions=args.max_sessions if args.max_sessions > 0 else None,
        )
        atomic_publish_dir(staging, site_dir)
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging)
        raise PublicationContractError("Site build failed; existing site was left unchanged") from exc

    print(f"Published {len(dates)} session(s). Open site/index.html or: python eod_site.py --serve")


if __name__ == "__main__":
    main()
