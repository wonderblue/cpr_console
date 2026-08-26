"""Unit tests for the EOD CPR static site (no network)."""

import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import pandas as pd

from eod_site import build_site
from nse_cpr_scanner import (
    apply_bullish_cpr_filters,
    compute_cpr,
    export_results,
    last_completed_session,
    load_scan_result,
    normalize_bhavcopy,
    tag_fo_symbols,
)

IST = ZoneInfo("Asia/Kolkata")


def _sample_cash():
    return pd.DataFrame(
        {
            "TckrSymb": ["AAA", "BBB"],
            "SctySrs": ["EQ", "EQ"],
            "OpnPric": [100.0, 50.0],
            "HghPric": [100.2, 55.0],
            "LwPric": [100.0, 45.0],
            "ClsPric": [100.15, 46.0],
        }
    )


class TestSessionDate(unittest.TestCase):
    def test_before_close_uses_previous_weekday(self):
        now = datetime(2026, 8, 14, 0, 53, tzinfo=IST)
        self.assertEqual(last_completed_session(now), "20260813")

    def test_weekend_rolls_to_friday(self):
        now = datetime(2026, 8, 16, 10, 0, tzinfo=IST)
        self.assertEqual(last_completed_session(now), "20260814")


class TestSiteBuild(unittest.TestCase):
    def test_builds_html_and_downloads(self):
        cash = normalize_bhavcopy(_sample_cash(), cash_only=True)
        cash = tag_fo_symbols(cash, pd.DataFrame({"SYMBOL": ["AAA"]}))
        cash = apply_bullish_cpr_filters(compute_cpr(cash))
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            site = Path(tmp) / "site"
            export_results(cash, "20260813", output_dir=out)
            loaded = load_scan_result("20260813", output_dir=out)
            self.assertEqual(loaded.cash_rows, 2)
            dates = build_site(out, site)
            self.assertEqual(dates, ["20260813"])
            self.assertTrue((site / "index.html").exists())
            self.assertTrue((site / "downloads" / "cpr_full.csv").exists())
            self.assertTrue((site / "downloads" / "cpr_20260813.zip").exists())
            self.assertTrue((site / "archive" / "20260813" / "index.html").exists())
            self.assertTrue((site / "archive" / "20260813" / "payload.json").exists())
            html = (site / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("window.CPR_DATA", html)
            self.assertIn("window.CPR_PAYLOAD_URL", html)
            self.assertIn('name="data-session"', html)
            self.assertLess(len(html), 100_000)
            payload_path = site / "payload.json"
            self.assertTrue(payload_path.exists())
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["date"], "20260813")
            self.assertEqual(payload["tables"]["full"][0]["SYMBOL"], "AAA")
            self.assertIn("Wide CPR", html)
            self.assertIn('data-tab="wide"', html)
            self.assertIn("Bullish CPR", html)
            self.assertIn("Bullish Bias", html)
            self.assertIn('data-tab="bullish_bias"', html)
            self.assertIn("Strategy_Type", payload["tables"]["full"][0])
            self.assertIn("Strategy_Explanation", payload["tables"]["full"][0])
            self.assertIn("wide", payload["tables"])
            self.assertIn("bullish_bias", payload["tables"])
            self.assertIn('id="symbolDrawer"', html)
            self.assertIn('id="drawerBackdrop"', html)
            app_js = (site / "assets" / "app.js").read_text(encoding="utf-8")
            css = (site / "assets" / "style.css").read_text(encoding="utf-8")
            self.assertIn("function cprMiniChart", app_js)
            self.assertIn("function openDrawer", app_js)
            self.assertIn("badgeHtml", app_js)
            self.assertIn(".symbol-drawer", css)
            self.assertIn(".cpr-chart", css)
            self.assertIn(".badge.confirmed", css)
            self.assertIn('data-tab="mylist"', html)
            self.assertIn('id="saveViewButton"', html)
            self.assertIn('id="manageAlertsButton"', html)
            self.assertIn('id="alertCenterButton"', html)
            self.assertIn("localStorage", app_js)
            self.assertIn("currentAlerts", app_js)
            self.assertIn("drawerWatchButton", app_js)
            self.assertIn("cpr_20260813.zip", payload["downloads"]["zip"])
            self.assertTrue((site / "downloads" / "cpr_wide.csv").exists())
            self.assertTrue((site / "downloads" / "cpr_bullish_bias.csv").exists())
            self.assertIn("bullish_bias", payload["downloads"])
            self.assertIn("id=\"industry\"", html)
            self.assertIn("Unclassified", html)
            self.assertIn('id="dataStatus"', html)
            self.assertIn("data session", (site / "assets" / "app.js").read_text(encoding="utf-8"))
            self.assertTrue((site / "publication_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
