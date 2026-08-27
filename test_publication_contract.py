import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from publication_contract import (
    PublicationContractError,
    atomic_publish_dir,
    build_manifest,
    validate_manifest,
    validate_output_dir,
    validate_site_dir,
    write_manifest,
)
from nse_cpr_scanner import scan_csv_path


REQUIRED = {
    "SYMBOL": ["AAA"],
    "HIGH": [110.0],
    "LOW": [100.0],
    "CLOSE": [106.0],
    "Pivot": [105.333333],
    "BC": [105.0],
    "TC": [105.666667],
    "CPR_Bottom": [105.0],
    "CPR_Top": [105.666667],
    "CPR_Width": [0.666667],
    "CPR_Width_Pct": [0.629],
    "CPR_Class": ["Moderate"],
    "Bias": ["Bullish"],
    "Price_Position": ["Above CPR"],
}


class TestPublicationContract(unittest.TestCase):
    def _write_daily(self, output: Path, date: str = "20260813") -> None:
        path = scan_csv_path("full", date, output)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(REQUIRED).to_csv(path, index=False)
        pd.DataFrame({"SYMBOL": ["AAA"], "CLOSE": [106.0], "CPR_Width_Pct": [0.629]}).to_csv(
            scan_csv_path("narrow", date, output), index=False
        )

    def test_manifest_round_trip_and_known_freshness(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp)
            self._write_daily(output)
            manifest = build_manifest(
                output,
                requested_date="20260813",
                actual_date="20260813",
                attempted_dates=["20260813"],
                source_mode="requested_session",
                lookback=252,
            )
            write_manifest(output, manifest)
            result = validate_output_dir(output, expected_date="20260813")
            validate_manifest(manifest, result["dates"], require_known=True)
            self.assertEqual(manifest["freshness"]["status"], "known")

    def test_missing_required_column_is_rejected(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp)
            self._write_daily(output)
            frame = pd.read_csv(scan_csv_path("full", "20260813", output)).drop(columns=["CPR_Class"])
            frame.to_csv(scan_csv_path("full", "20260813", output), index=False)
            with self.assertRaises(PublicationContractError):
                validate_output_dir(output)

    def test_unknown_legacy_manifest_requires_explicit_nonfresh_mode(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp)
            self._write_daily(output)
            manifest = build_manifest(
                output,
                requested_date=None,
                actual_date="20260813",
                attempted_dates=["20260813"],
                source_mode="site_only",
                lookback=0,
            )
            result = validate_output_dir(output)
            validate_manifest(manifest, result["dates"], require_known=False)
            with self.assertRaises(PublicationContractError):
                validate_manifest(manifest, result["dates"], require_known=True)

    def test_atomic_publish_replaces_complete_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "site"
            staging = root / ".site.staging"
            destination.mkdir()
            (destination / "old.txt").write_text("old", encoding="utf-8")
            staging.mkdir()
            (staging / "new.txt").write_text("new", encoding="utf-8")
            atomic_publish_dir(staging, destination)
            self.assertFalse((destination / "old.txt").exists())
            self.assertEqual((destination / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse(staging.exists())

    def test_site_contract_rejects_missing_manifest(self):
        with TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "assets").mkdir()
            (site / "index.html").write_text("window.CPR_DATA", encoding="utf-8")
            (site / "assets/style.css").write_text("", encoding="utf-8")
            (site / "assets/app.js").write_text("", encoding="utf-8")
            (site / "archive.json").write_text(json.dumps(["20260813"]), encoding="utf-8")
            with self.assertRaises(PublicationContractError):
                validate_site_dir(site)


if __name__ == "__main__":
    unittest.main()
