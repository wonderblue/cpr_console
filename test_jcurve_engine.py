"""Unit tests for the J-Curve Detection & Screening Engine."""

import unittest
import pandas as pd
from jcurve_engine import attach_jcurve_strategy, compute_jcurve_score, generate_jcurve_explanation


class TestJCurveEngine(unittest.TestCase):
    def test_jcurve_liftoff_classification(self):
        df = pd.DataFrame({
            "SYMBOL": ["LIFTOFF_STOCK"],
            "CLOSE": [105.0],
            "Pivot": [100.0],
            "CPR_Top": [102.0],
            "CPR_Bottom": [98.0],
            "CPR_Width_Pct": [0.20],
            "Value_Ratio": [3.5],
            "VALUE": [5e7],
            "Price_Position": ["Above CPR"],
            "Bias": ["Bullish"],
            "Overlay": ["Higher"],
            "Regime": ["Neutral"],
            "Own_Narrow": [True],
            "NR7": [True],
            "Above_SMA50": [True],
            "Above_SMA100": [True],
            "Confluence_Score": [4],
        })
        out = attach_jcurve_strategy(df)
        self.assertEqual(out.iloc[0]["JCurve_Stage"], "Liftoff")
        self.assertGreaterEqual(out.iloc[0]["JCurve_Score"], 80)
        self.assertIn("J-Curve Liftoff", out.iloc[0]["JCurve_Explanation"])

    def test_jcurve_ready_classification(self):
        df = pd.DataFrame({
            "SYMBOL": ["READY_STOCK"],
            "CLOSE": [100.5],
            "Pivot": [100.0],
            "CPR_Top": [101.5],
            "CPR_Bottom": [98.5],
            "CPR_Width_Pct": [0.25],
            "Value_Ratio": [1.8],
            "VALUE": [2.5e7],
            "Price_Position": ["Inside CPR"],
            "Bias": ["Bullish"],
            "Overlay": ["Inside"],
            "Regime": ["Neutral"],
            "Own_Narrow": [True],
            "NR7": [False],
            "Above_SMA50": [True],
            "Above_SMA100": [True],
            "Confluence_Score": [2],
        })
        out = attach_jcurve_strategy(df)
        self.assertEqual(out.iloc[0]["JCurve_Stage"], "Ready")
        self.assertGreaterEqual(out.iloc[0]["JCurve_Score"], 50)
        self.assertIn("J-Curve Ready", out.iloc[0]["JCurve_Explanation"])

    def test_jcurve_none_for_flat_stock(self):
        df = pd.DataFrame({
            "SYMBOL": ["FLAT_STOCK"],
            "CLOSE": [95.0],
            "Pivot": [100.0],
            "CPR_Top": [101.5],
            "CPR_Bottom": [98.5],
            "CPR_Width_Pct": [1.5],
            "Value_Ratio": [0.4],
            "VALUE": [1e5],
            "Price_Position": ["Below CPR"],
            "Bias": ["Bearish"],
            "Overlay": ["Lower"],
            "Regime": ["Neutral"],
            "Own_Narrow": [False],
            "NR7": [False],
            "Above_SMA50": [False],
            "Above_SMA100": [False],
            "Confluence_Score": [-4],
        })
        out = attach_jcurve_strategy(df)
        self.assertEqual(out.iloc[0]["JCurve_Stage"], "None")
        self.assertEqual(out.iloc[0]["JCurve_Score"], 0)


if __name__ == "__main__":
    unittest.main()
