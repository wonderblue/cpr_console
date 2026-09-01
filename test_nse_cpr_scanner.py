"""Unit tests for the NSE EOD CPR scanner (no network)."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from nse_cpr_scanner import (
    apply_bullish_cpr_filters,
    attach_industry,
    attach_htf_to_result,
    backfill_htf_scans,
    compute_cpr,
    export_results,
    keep_listed_equity,
    load_scan_result,
    normalize_bhavcopy,
    scan_csv_path,
    split_shortlists,
    tag_fo_symbols,
)


def _udi_cash():
    return pd.DataFrame(
        {
            "TckrSymb": ["AAA", "BBB", "CCC", "AAA"],
            "SctySrs": ["EQ", "EQ", "BE", "EQ"],
            "OpnPric": [100.0, 50.0, 10.0, 999.0],
            "HghPric": [100.2, 55.0, 12.0, 999.0],
            "LwPric": [100.0, 45.0, 9.0, 999.0],
            "ClsPric": [100.15, 46.0, 11.0, 999.0],
        }
    )


class TestNormalize(unittest.TestCase):
    def test_udi_columns_and_eq_filter(self):
        out = normalize_bhavcopy(_udi_cash(), cash_only=True)
        self.assertListEqual(sorted(out["SYMBOL"].tolist()), ["AAA", "BBB"])
        self.assertIn("OPEN", out.columns)
        self.assertIn("HIGH", out.columns)

    def test_legacy_columns_pass_through(self):
        raw = pd.DataFrame(
            {
                "SYMBOL": ["RELIANCE"],
                "SERIES": ["EQ"],
                "OPEN": [100],
                "HIGH": [110],
                "LOW": [100],
                "CLOSE": [106],
            }
        )
        out = normalize_bhavcopy(raw, cash_only=True)
        self.assertEqual(out.iloc[0]["SYMBOL"], "RELIANCE")
        self.assertEqual(out.iloc[0]["CLOSE"], 106)


class TestComputeCpr(unittest.TestCase):
    def test_standard_hlc(self):
        df = pd.DataFrame(
            {
                "SYMBOL": ["X"],
                "OPEN": [100],
                "HIGH": [110],
                "LOW": [100],
                "CLOSE": [106],
            }
        )
        out = compute_cpr(df).iloc[0]
        self.assertAlmostEqual(out["Pivot"], 105.3333333333, places=6)
        self.assertAlmostEqual(out["BC"], 105.0, places=6)
        self.assertAlmostEqual(out["TC"], 105.6666666667, places=6)
        self.assertAlmostEqual(out["CPR_Bottom"], 105.0, places=6)
        self.assertAlmostEqual(out["CPR_Top"], 105.6666666667, places=6)
        self.assertAlmostEqual(out["CPR_Width_Pct"], (0.6666666667 / 106) * 100, places=6)
        self.assertEqual(out["CPR_Class"], "Moderate")
        self.assertEqual(out["Bias"], "Bullish")
        self.assertEqual(out["Price_Position"], "Above CPR")


class TestFlagsAndTags(unittest.TestCase):
    def test_narrow_bullish_flag(self):
        df = pd.DataFrame(
            {
                "SYMBOL": ["NARROW"],
                "OPEN": [100.0],
                "HIGH": [100.2],
                "LOW": [100.0],
                "CLOSE": [100.15],
            }
        )
        out = apply_bullish_cpr_filters(compute_cpr(df)).iloc[0]
        self.assertEqual(out["CPR_Class"], "Narrow")
        self.assertTrue(bool(out["Bullish_CPR"]))
        self.assertFalse(bool(out["Bearish_CPR"]))

    def test_bearish_flag(self):
        df = pd.DataFrame(
            {
                "SYMBOL": ["WIDE"],
                "OPEN": [100.0],
                "HIGH": [100.2],
                "LOW": [99.7],
                "CLOSE": [99.8],
            }
        )
        out = apply_bullish_cpr_filters(compute_cpr(df)).iloc[0]
        self.assertEqual(out["Bias"], "Bearish")
        self.assertEqual(out["Price_Position"], "Below CPR")
        self.assertTrue(bool(out["Bearish_CPR"]))
        self.assertFalse(bool(out["Bullish_CPR"]))

    def test_fo_tag(self):
        cash = pd.DataFrame({"SYMBOL": ["AAA", "BBB"]})
        fo = pd.DataFrame({"SYMBOL": ["AAA", "AAA", "NIFTY"]})
        tagged = tag_fo_symbols(cash, fo)
        self.assertEqual(tagged.loc[tagged["SYMBOL"] == "AAA", "Segment"].iloc[0], "F&O + Cash")
        self.assertEqual(tagged.loc[tagged["SYMBOL"] == "BBB", "Segment"].iloc[0], "Cash Only")


class TestExport(unittest.TestCase):
    def test_shortlists_and_csv(self):
        cash = normalize_bhavcopy(_udi_cash(), cash_only=True)
        cash = tag_fo_symbols(cash, pd.DataFrame({"SYMBOL": ["AAA"]}))
        cash = apply_bullish_cpr_filters(compute_cpr(cash))
        full, narrow, bullish, bearish, top20 = split_shortlists(cash)
        self.assertGreaterEqual(len(full), 2)
        self.assertTrue((narrow["CPR_Class"] == "Narrow").all() or narrow.empty)
        with TemporaryDirectory() as tmp:
            result = export_results(cash, "20260813", output_dir=Path(tmp))
            self.assertTrue(scan_csv_path("full", "20260813", Path(tmp)).exists())
            self.assertTrue(scan_csv_path("best", "20260813", Path(tmp)).exists())
            self.assertEqual(result.date, "20260813")
            self.assertFalse(result.top20.empty)

    def test_confluence_cols_in_export_and_reload(self):
        cash = normalize_bhavcopy(_udi_cash(), cash_only=True)
        cash = tag_fo_symbols(cash, pd.DataFrame({"SYMBOL": ["AAA"]}))
        cash = apply_bullish_cpr_filters(compute_cpr(cash))
        cash["Setup"] = "No setup"
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            cache_dir_n = out / "bhavcopy"
            cache_dir_n.mkdir(parents=True, exist_ok=True)
            from nse_cpr_scanner import seed_bhavcopy_cache

            seed_bhavcopy_cache(cash, "20260813", output_dir=out)
            seed_bhavcopy_cache(cash, "20260812", output_dir=out)
            seed_bhavcopy_cache(cash, "20260811", output_dir=out)
            seed_bhavcopy_cache(cash, "20260810", output_dir=out)
            seed_bhavcopy_cache(cash, "20260807", output_dir=out)
            result = export_results(cash, "20260813", output_dir=out)
            result.full = attach_htf_to_result(result, output_dir=out, write_csv=True).full
            self.assertIn("Confluence_Score", result.full.columns)
            reloaded = load_scan_result("20260813", output_dir=out)
            self.assertIn("Confluence_Score", reloaded.full.columns)

    def test_htf_archive_backfill_writes_weekly_monthly(self):
        cash = normalize_bhavcopy(_udi_cash(), cash_only=True)
        cash = tag_fo_symbols(cash, pd.DataFrame({"SYMBOL": ["AAA"]}))
        cash = apply_bullish_cpr_filters(compute_cpr(cash))
        from nse_cpr_scanner import seed_bhavcopy_cache

        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            # 130 session weekdays → ~26 weeks (≥12) and ~7 months (≥6) of HTF bars.
            from datetime import date, timedelta

            d = date(2026, 1, 1)
            sessions = []
            while len(sessions) < 130:
                if d.weekday() < 5:
                    sessions.append(d.strftime("%Y%m%d"))
                d += timedelta(days=1)
            for s in sessions:
                seed_bhavcopy_cache(cash, s, output_dir=out)
                export_results(cash, s, output_dir=out, verbose=False)
            backfill_htf_scans(sessions[-1], output_dir=out, lookback=130)
            self.assertTrue(scan_csv_path("weekly", sessions[-1], out).exists())
            self.assertTrue(scan_csv_path("monthly", sessions[-1], out).exists())


class TestEquityAndIndustry(unittest.TestCase):
    def test_drops_etf_amc_liquid(self):
        df = pd.DataFrame(
            {
                "SYMBOL": ["RELIANCE", "LIQUIDCASE", "NIFTYBEES", "GOLDIAM"],
                "NAME": [
                    "RELIANCE INDUSTRIES LTD",
                    "ZERODHAAMC - LIQUIDCASE",
                    "NIPPON INDIA ETF NIFTY BEES",
                    "GOLDIAM INTERNATIONAL LTD",
                ],
            }
        )
        kept = keep_listed_equity(df)
        self.assertListEqual(kept["SYMBOL"].tolist(), ["RELIANCE", "GOLDIAM"])

    def test_attach_industry_map(self):
        df = pd.DataFrame({"SYMBOL": ["ABB", "ZZZSMALL"]})
        out = attach_industry(df, mapping={"ABB": "Capital Goods"}, fetch=False)
        self.assertEqual(out.loc[out["SYMBOL"] == "ABB", "Industry"].iloc[0], "Capital Goods")
        self.assertIn(out.loc[out["SYMBOL"] == "ZZZSMALL", "Industry"].iloc[0], ["Unclassified", "Diversified"])

    def test_compute_monthly_top_watchlist(self):
        from nse_cpr_scanner import compute_monthly_top_watchlist
        sample_monthly = pd.DataFrame({
            "SYMBOL": ["AAA", "BBB", "CCC"],
            "CLOSE": [100.0, 200.0, 300.0],
            "VALUE": [5e7, 8e7, 9e7],
            "History_OK": [True, True, True],
            "CPR_Class": ["Narrow", "Wide", "Moderate"],
            "Own_Narrow": [True, False, False],
            "Width_Rank_Pct": [0.1, 0.9, 0.5],
            "CPR_Width_Pct": [0.2, 2.5, 0.6],
            "Pivot": [100.0, 200.0, 300.0],
            "CPR_Top": [101.0, 205.0, 302.0],
            "CPR_Bottom": [99.0, 195.0, 298.0],
            "Price_Position": ["Above CPR", "Above CPR", "Below CPR"],
            "Bias": ["Bullish", "Bullish", "Bearish"],
            "Value_Ratio": [2.5, 4.0, 1.1],
            "Nifty500": [True, True, False],
        })
        top = compute_monthly_top_watchlist(sample_monthly, n=2)
        self.assertEqual(len(top), 2)
        self.assertIn("Commentary", top.columns)
        self.assertIn("UNIFIED_SCORE", top.columns)
        self.assertIn("Rank", top.columns)


if __name__ == "__main__":
    unittest.main()
