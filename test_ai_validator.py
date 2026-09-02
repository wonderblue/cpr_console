"""Tests for AI Validator and Prop-Desk Risk Audit engine."""

import unittest
import pandas as pd
from ai_validator import compute_prop_desk_audit, attach_ai_validation


class TestAIValidator(unittest.TestCase):
    def test_compute_prop_desk_audit_liftoff(self):
        sample = {
            "SYMBOL": "WELCORP",
            "CLOSE": 2553.60,
            "Pivot": 2504.53,
            "CPR_Top": 2529.07,
            "CPR_Bottom": 2480.00,
            "Value_Ratio": 6.8,
            "VALUE": 9.023e9,
            "Confluence_Score": 2,
            "JCurve_Stage": "Liftoff",
            "Overlay": "Higher",
            "CPR_Width_Pct": 1.92,
            "NR7": True,
            "Above_SMA50": True,
            "Above_SMA100": True,
            "ATR14": 45.0,
        }
        res = compute_prop_desk_audit(sample)
        self.assertGreaterEqual(res["score"], 80)
        self.assertEqual(res["grade"], "A+")
        self.assertIn("Stage 2 Liftoff", res["insight"])
        self.assertIn("Invalidation", res["risk_flag"])
        self.assertEqual(res["stop_loss"], 2504.53)
        self.assertGreater(res["target_1"], 2553.60)
        self.assertGreater(res["target_2"], res["target_1"])

    def test_compute_prop_desk_audit_ready(self):
        sample = {
            "SYMBOL": "DEEPAKNTR",
            "CLOSE": 1782.80,
            "Pivot": 1782.70,
            "CPR_Top": 1782.80,
            "CPR_Bottom": 1782.60,
            "Value_Ratio": 5.9,
            "VALUE": 1.8e9,
            "Confluence_Score": 2,
            "JCurve_Stage": "Ready",
            "Overlay": "Higher",
            "CPR_Width_Pct": 0.01,
            "NR7": False,
            "Above_SMA50": True,
            "Above_SMA100": True,
            "ATR14": 25.0,
        }
        res = compute_prop_desk_audit(sample)
        self.assertGreaterEqual(res["score"], 70)
        self.assertIn(res["grade"], ["A+", "A"])
        self.assertIn("Stage 1 Ready", res["insight"])
        self.assertIn("CPR Bottom", res["risk_flag"])

    def test_attach_ai_validation_dataframe(self):
        df = pd.DataFrame([
            {
                "SYMBOL": "TEST1",
                "CLOSE": 100.0,
                "Pivot": 98.0,
                "CPR_Top": 99.0,
                "CPR_Bottom": 97.0,
                "Value_Ratio": 3.0,
                "VALUE": 5.0e8,
                "Confluence_Score": 2,
                "JCurve_Stage": "Liftoff",
                "JCurve_Score": 85,
                "Overlay": "Higher",
                "CPR_Width_Pct": 0.5,
                "NR7": True,
            },
            {
                "SYMBOL": "TEST2",
                "CLOSE": 200.0,
                "Pivot": 199.0,
                "CPR_Top": 199.5,
                "CPR_Bottom": 198.5,
                "Value_Ratio": 2.5,
                "VALUE": 3.0e8,
                "Confluence_Score": 1,
                "JCurve_Stage": "Ready",
                "JCurve_Score": 75,
                "Overlay": "Higher",
                "CPR_Width_Pct": 0.3,
                "NR7": False,
            }
        ])
        out = attach_ai_validation(df, top_n=5)
        self.assertIn("AI_Conviction_Score", out.columns)
        self.assertIn("AI_Conviction_Grade", out.columns)
        self.assertIn("AI_Insight", out.columns)
        self.assertIn("AI_Risk_Flag", out.columns)
        self.assertIn("AI_Trade_Plan", out.columns)
        self.assertGreaterEqual(out.loc[0, "AI_Conviction_Score"], 70)


if __name__ == "__main__":
    unittest.main()
