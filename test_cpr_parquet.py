"""
Unit tests for cpr_parquet.py: Parquet lakehouse storage and DuckDB querying.
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from cpr_parquet import (
    backfill_csv_to_parquet,
    cached_parquet_dates,
    compute_own_narrow_duckdb,
    load_history_panel_parquet,
    load_session_parquet,
    parquet_partition_dir,
    parquet_session_path,
    query_duckdb,
    save_session_parquet,
)

try:
    import duckdb
    import pyarrow
    HAS_PARQUET_DEPS = True
except ImportError:
    HAS_PARQUET_DEPS = False


@unittest.skipUnless(HAS_PARQUET_DEPS, "pyarrow and duckdb required for Parquet tests")
class TestCPRParquet(unittest.TestCase):
    def setUp(self):
        self.sample_df = pd.DataFrame({
            "SYMBOL": ["RELIANCE", "TCS", "INFY"],
            "NAME": ["Reliance Industries", "Tata Consultancy Services", "Infosys"],
            "Industry": ["Oil & Gas", "IT", "IT"],
            "CLOSE": [2500.0, 3500.0, 1500.0],
            "Pivot": [2490.0, 3495.0, 1505.0],
            "BC": [2480.0, 3490.0, 1500.0],
            "TC": [2500.0, 3500.0, 1510.0],
            "CPR_Width_Pct": [0.80, 0.28, 0.66],
            "CPR_Class": ["Wide", "Moderate", "Moderate"],
            "Bias": ["Bullish", "Bullish", "Bearish"],
            "Price_Position": ["Above CPR", "Above CPR", "Below CPR"],
        })

    def test_parquet_partition_path(self):
        date = "20260901"
        out_dir = Path("/tmp/test_cpr")
        part_dir = parquet_partition_dir(date, out_dir)
        self.assertEqual(part_dir, out_dir / "parquet" / "year=2026" / "month=09")
        session_file = parquet_session_path(date, out_dir)
        self.assertEqual(session_file, part_dir / "cpr_full_20260901.parquet")

    def test_save_and_load_session_parquet(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            date = "20260901"
            saved_path = save_session_parquet(self.sample_df, date, tmp_path)
            self.assertTrue(saved_path.exists())
            self.assertTrue(str(saved_path).endswith(".parquet"))

            loaded_df = load_session_parquet(date, tmp_path)
            self.assertIsNotNone(loaded_df)
            self.assertEqual(len(loaded_df), 3)
            self.assertListEqual(list(loaded_df["SYMBOL"]), ["RELIANCE", "TCS", "INFY"])
            self.assertEqual(loaded_df["Date"].iloc[0], "20260901")

    def test_query_duckdb(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            save_session_parquet(self.sample_df, "20260901", tmp_path)
            
            # Query with DuckDB
            result_df = query_duckdb("SELECT SYMBOL, CLOSE, CPR_Class FROM __PARQUET__ WHERE CPR_Class = 'Wide'", output_dir=tmp_path)
            self.assertEqual(len(result_df), 1)
            self.assertEqual(result_df["SYMBOL"].iloc[0], "RELIANCE")

    def test_backfill_csv_to_parquet(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Create dummy nested CSV
            csv_dir = tmp_path / "2026" / "08"
            csv_dir.mkdir(parents=True, exist_ok=True)
            csv_file = csv_dir / "cpr_full_20260814.csv"
            self.sample_df.to_csv(csv_file, index=False)

            count = backfill_csv_to_parquet(tmp_path, verbose=False)
            self.assertEqual(count, 1)

            parquet_file = parquet_session_path("20260814", tmp_path)
            self.assertTrue(parquet_file.exists())

    def test_compute_own_narrow_and_nr7_duckdb(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Create 10 daily sessions
            for i in range(1, 11):
                d = f"202608{i:02d}"
                df = self.sample_df.copy()
                df["CPR_Width_Pct"] = [0.80 - (i * 0.05), 0.28, 0.66]
                save_session_parquet(df, d, tmp_path)

            curr_df = self.sample_df.copy()
            curr_df["CPR_Width_Pct"] = [0.10, 0.35, 0.60]  # RELIANCE is lowest width ever -> NR4 & NR7 True
            result = compute_own_narrow_duckdb(curr_df, "20260812", lookback_sessions=10, output_dir=tmp_path)
            self.assertIsNotNone(result)
            self.assertIn("NR4", result.columns)
            self.assertIn("NR7", result.columns)
            rel_row = result[result["SYMBOL"] == "RELIANCE"].iloc[0]
            self.assertTrue(bool(rel_row["NR4"]))
            self.assertTrue(bool(rel_row["NR7"]))
            self.assertTrue(bool(rel_row["Own_Narrow"]))


    def test_cached_parquet_dates_and_load_panel(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for i in range(1, 6):
                d = f"202608{i:02d}"
                df = self.sample_df.copy()
                df["OPEN"] = 100.0 * i
                df["HIGH"] = 110.0 * i
                df["LOW"] = 90.0 * i
                df["VOLUME"] = 1000 * i
                df["VALUE"] = 100000 * i
                save_session_parquet(df, d, tmp_path)

            dates = cached_parquet_dates("20260805", lookback=3, output_dir=tmp_path)
            self.assertEqual(dates, ["20260805", "20260804", "20260803"])

            panel_df = load_history_panel_parquet(end_date="20260805", lookback=3, output_dir=tmp_path)
            self.assertEqual(len(panel_df), 9)  # 3 symbols * 3 dates
            self.assertIn("session", panel_df.columns)
            self.assertIn("Pivot", panel_df.columns)
            self.assertIn("CPR_Top", panel_df.columns)
            self.assertIn("CPR_Bottom", panel_df.columns)
            self.assertIn("CPR_Width_Pct", panel_df.columns)
            self.assertEqual(sorted(panel_df["session"].unique().tolist()), ["20260803", "20260804", "20260805"])


if __name__ == "__main__":
    unittest.main()
