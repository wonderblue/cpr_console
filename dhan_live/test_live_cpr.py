"""Unit tests for Dhan live CPR tracker (no network, no Dhan token)."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from dhan_live.dhan_feed import unwrap_chart, _ts_to_ist
from dhan_live.live_cpr import build_live_rows, detect_event, requested_symbols

IST = ZoneInfo("Asia/Kolkata")


class TestHelpers(unittest.TestCase):
    def test_unwrap_nested(self):
        data = unwrap_chart({"status": "success", "data": {"open": [1], "timestamp": [1]}})
        self.assertEqual(data["open"], [1])

    def test_ms_timestamp(self):
        dt = _ts_to_ist(1_720_000_000_000)
        self.assertEqual(dt.tzinfo, IST)

    def test_requested_symbols_dedupes_ns(self):
        self.assertEqual(requested_symbols(["RELIANCE.NS", "reliance", "TCS"]), ["RELIANCE", "TCS"])


class TestEvents(unittest.TestCase):
    def test_crossed_tc(self):
        self.assertEqual(detect_event(100.0, 101.0, 99.0, 100.5), "Crossed TC")

    def test_crossed_bc(self):
        self.assertEqual(detect_event(100.0, 98.0, 99.0, 101.0), "Crossed BC")

    def test_no_event(self):
        self.assertEqual(detect_event(102.0, 103.0, 99.0, 101.0), "")


class TestBuildRows(unittest.TestCase):
    def test_standard_cpr_and_live_position(self):
        prev = {
            "X": {
                "security_id": "11536",
                "name": "TCS",
                "high": 110,
                "low": 100,
                "close": 106,
                "date": "2026-08-13",
            }
        }
        quotes = {
            "11536": {"ltp": 107.0, "open": 106.5, "high": 108.0, "low": 106.0, "close": 106.0}
        }
        now = datetime(2026, 8, 14, 10, 30, tzinfo=IST)
        df = build_live_rows(prev, quotes, previous_ltps={"X": 105.5}, now=now)
        row = df.iloc[0]
        self.assertAlmostEqual(row["Pivot"], 105.333333, places=4)
        self.assertEqual(row["Bias"], "Bullish")
        self.assertEqual(row["Position"], "Above CPR")
        self.assertEqual(row["Event"], "Crossed TC")
        self.assertEqual(row["Data Status"], "Live")


if __name__ == "__main__":
    unittest.main()
