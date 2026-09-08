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
            "TckrSymb": ["AAA", "BBB", "CCC", "DDD", "AAA"],
            "SctySrs": ["EQ", "EQ", "BE", "GS", "EQ"],
            "OpnPric": [100.0, 50.0, 10.0, 100.0, 999.0],
            "HghPric": [100.2, 55.0, 12.0, 101.0, 999.0],
            "LwPric": [100.0, 45.0, 9.0, 99.0, 999.0],
            "ClsPric": [100.15, 46.0, 11.0, 100.5, 999.0],
        }
    )


class TestNormalize(unittest.TestCase):
    def test_udi_columns_and_eq_filter(self):
        out = normalize_bhavcopy(_udi_cash(), cash_only=True)
        self.assertListEqual(sorted(out["SYMBOL"].tolist()), ["AAA", "BBB", "CCC"])
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

    def test_compute_weekly_top_watchlist(self):
        from nse_cpr_scanner import compute_weekly_top_watchlist
        sample_weekly = pd.DataFrame({
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
        top = compute_weekly_top_watchlist(sample_weekly, n=2)
        self.assertEqual(len(top), 2)
        self.assertIn("Commentary", top.columns)
        self.assertIn("UNIFIED_SCORE", top.columns)
        self.assertIn("Rank", top.columns)

    def test_defiance_long_and_dynamic_risk_multiplier(self):
        from nse_cpr_scanner import attach_history_features
        
        # Create test panel
        hist = pd.DataFrame({
            "SYMBOL": ["LEADER", "WEAKLONG", "SURGESHORT", "WEAKSHORT"] * 50,
            "session": [f"20260{i//4 + 10:02d}{i%4 + 1:02d}" for i in range(200)],
            "OPEN": [100.0, 100.0, 100.0, 100.0] * 50,
            "HIGH": [105.0, 105.0, 105.0, 105.0] * 50,
            "LOW": [95.0, 95.0, 95.0, 95.0] * 50,
            "CLOSE": [104.0, 104.0, 96.0, 96.0] * 50,
            "VOLUME": [1000000] * 200,
            "VALUE": [5e7] * 200,
            "CPR_Width": [0.2] * 200,
            "CPR_Width_Pct": [0.2] * 200,
            "CPR_Top": [101.0, 101.0, 99.0, 99.0] * 50,
            "CPR_Bottom": [99.0, 99.0, 97.0, 97.0] * 50,
            "Pivot": [100.0, 100.0, 98.0, 98.0] * 50,
            "BC": [99.0, 99.0, 97.0, 97.0] * 50,
            "TC": [101.0, 101.0, 99.0, 99.0] * 50,
            "Price_Position": ["Above CPR", "Above CPR", "Below CPR", "Below CPR"] * 50,
            "Bias": ["Bullish", "Bullish", "Bearish", "Bearish"] * 50,
            "Overlay": ["Higher", "Higher", "Lower", "Lower"] * 50,
            "Regime": ["Risk Off"] * 200,
        })
        scan = hist[hist["session"] == "20260594"].copy() if "20260594" in hist["session"].values else hist.tail(4).copy()
        out = attach_history_features(scan, hist, own_window=40, min_history=20)
        
        self.assertIn("Risk_Multiplier", out.columns)
        self.assertTrue((out["Risk_Multiplier"] >= 0.0).all())
        self.assertTrue((out["Risk_Multiplier"] <= 1.0).all())

    def test_weekly_htf_pivot_matches_own_period_hlc(self):
        from nse_cpr_scanner import aggregate_htf_bars
        rows = [
            # Week 1: 2026-08-03 to 2026-08-07
            {"SYMBOL": "TEST", "session": "20260803", "OPEN": 100.0, "HIGH": 105.0, "LOW": 98.0, "CLOSE": 102.0},
            {"SYMBOL": "TEST", "session": "20260807", "OPEN": 102.0, "HIGH": 120.0, "LOW": 100.0, "CLOSE": 115.0},
            # Week 2: 2026-08-10 to 2026-08-14
            {"SYMBOL": "TEST", "session": "20260810", "OPEN": 115.0, "HIGH": 125.0, "LOW": 110.0, "CLOSE": 122.0},
            {"SYMBOL": "TEST", "session": "20260814", "OPEN": 122.0, "HIGH": 135.0, "LOW": 118.0, "CLOSE": 130.0},
        ]
        df = pd.DataFrame(rows)
        weekly = aggregate_htf_bars(df, "W-FRI")
        self.assertEqual(len(weekly), 2)
        for _, row in weekly.iterrows():
            expected_pivot = (row["HIGH"] + row["LOW"] + row["CLOSE"]) / 3.0
            self.assertAlmostEqual(row["Pivot"], expected_pivot, places=6)
            self.assertAlmostEqual(row["BC"], (row["HIGH"] + row["LOW"]) / 2.0, places=6)
            self.assertAlmostEqual(row["TC"], 2.0 * expected_pivot - row["BC"], places=6)

    def test_monthly_htf_pivot_matches_own_period_hlc(self):
        from nse_cpr_scanner import aggregate_htf_bars
        rows = [
            # Month 1: July 2026
            {"SYMBOL": "TEST", "session": "20260701", "OPEN": 100.0, "HIGH": 110.0, "LOW": 95.0, "CLOSE": 105.0},
            {"SYMBOL": "TEST", "session": "20260731", "OPEN": 105.0, "HIGH": 130.0, "LOW": 102.0, "CLOSE": 125.0},
            # Month 2: August 2026
            {"SYMBOL": "TEST", "session": "20260803", "OPEN": 125.0, "HIGH": 140.0, "LOW": 120.0, "CLOSE": 135.0},
            {"SYMBOL": "TEST", "session": "20260831", "OPEN": 135.0, "HIGH": 150.0, "LOW": 130.0, "CLOSE": 145.0},
        ]
        df = pd.DataFrame(rows)
        monthly = aggregate_htf_bars(df, "M")
        self.assertEqual(len(monthly), 2)
        for _, row in monthly.iterrows():
            expected_pivot = (row["HIGH"] + row["LOW"] + row["CLOSE"]) / 3.0
            self.assertAlmostEqual(row["Pivot"], expected_pivot, places=6)
            self.assertAlmostEqual(row["BC"], (row["HIGH"] + row["LOW"]) / 2.0, places=6)
            self.assertAlmostEqual(row["TC"], 2.0 * expected_pivot - row["BC"], places=6)

    def test_next_session_cpr_matches_own_day_ohlc(self):
        df = pd.DataFrame({
            "SYMBOL": ["AAA"],
            "OPEN": [100.0],
            "HIGH": [115.0],
            "LOW": [95.0],
            "CLOSE": [110.0],
        })
        prev_df = pd.DataFrame({
            "SYMBOL": ["AAA"],
            "HIGH": [105.0],
            "LOW": [90.0],
            "CLOSE": [100.0],
        })
        out = compute_cpr(df, prev_df=prev_df).iloc[0]
        # Active session CPR matches prev_df (T-1)
        self.assertAlmostEqual(out["Pivot"], (105.0 + 90.0 + 100.0) / 3.0, places=6)
        # Next session CPR matches day T's own OHLC
        next_pivot = (115.0 + 95.0 + 110.0) / 3.0
        next_bc = (115.0 + 95.0) / 2.0
        next_tc = 2.0 * next_pivot - next_bc
        self.assertAlmostEqual(out["NEXT_Pivot"], next_pivot, places=6)
        self.assertAlmostEqual(out["NEXT_BC"], next_bc, places=6)
        self.assertAlmostEqual(out["NEXT_TC"], next_tc, places=6)
        self.assertAlmostEqual(out["NEXT_CPR_Top"], max(next_bc, next_tc), places=6)
        self.assertAlmostEqual(out["NEXT_CPR_Bottom"], min(next_bc, next_tc), places=6)
        self.assertAlmostEqual(out["NEXT_CPR_Width"], abs(next_tc - next_bc), places=6)
        self.assertAlmostEqual(out["NEXT_CPR_Width_Pct"], (abs(next_tc - next_bc) / 110.0) * 100.0, places=6)

    def test_virgin_cpr_evaluates_against_active_cpr_band(self):
        from nse_cpr_scanner import attach_history_features
        # 3 symbols across 2 sessions
        # Session 1: Establish prior history and T-1 active CPR levels for session 2
        s1 = [
            {"SYMBOL": "BULL_V", "session": "20260813", "OPEN": 100.0, "HIGH": 100.0, "LOW": 90.0, "CLOSE": 95.0, "VALUE": 1e6},
            {"SYMBOL": "TOUCH_V", "session": "20260813", "OPEN": 100.0, "HIGH": 100.0, "LOW": 90.0, "CLOSE": 95.0, "VALUE": 1e6},
            {"SYMBOL": "BEAR_V", "session": "20260813", "OPEN": 100.0, "HIGH": 100.0, "LOW": 90.0, "CLOSE": 95.0, "VALUE": 1e6},
        ]
        # Session 2: Active CPR from S1: Pivot = (100+90+95)/3 = 95.0, BC = 95.0, TC = 95.0, Top = 95.0, Bot = 95.0
        s2 = [
            # BULL_V: LOW = 101.0 > Top (95.0) -> Bullish Virgin
            {"SYMBOL": "BULL_V", "session": "20260814", "OPEN": 102.0, "HIGH": 110.0, "LOW": 101.0, "CLOSE": 108.0, "VALUE": 1e6},
            # TOUCH_V: LOW = 93.0, HIGH = 98.0 straddles 95.0 -> None
            {"SYMBOL": "TOUCH_V", "session": "20260814", "OPEN": 94.0, "HIGH": 98.0, "LOW": 93.0, "CLOSE": 96.0, "VALUE": 1e6},
            # BEAR_V: HIGH = 90.0 < Bot (95.0) -> Bearish Virgin
            {"SYMBOL": "BEAR_V", "session": "20260814", "OPEN": 88.0, "HIGH": 90.0, "LOW": 82.0, "CLOSE": 85.0, "VALUE": 1e6},
        ]
        panel = pd.DataFrame(s1 + s2)
        panel["PREV_HIGH"] = panel.groupby("SYMBOL")["HIGH"].shift(1)
        panel["PREV_LOW"] = panel.groupby("SYMBOL")["LOW"].shift(1)
        panel["PREV_CLOSE"] = panel.groupby("SYMBOL")["CLOSE"].shift(1)
        panel = compute_cpr(panel)
        scan = panel[panel["session"] == "20260814"].copy()
        out = attach_history_features(scan, panel, min_history=1)
        res = dict(zip(out["SYMBOL"], out["Virgin_CPR"]))
        self.assertEqual(res["BULL_V"], "Bullish Virgin")
        self.assertEqual(res["TOUCH_V"], "None")
        self.assertEqual(res["BEAR_V"], "Bearish Virgin")


if __name__ == "__main__":
    unittest.main()
