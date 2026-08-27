import tempfile
import unittest
from pathlib import Path

import pandas as pd

from nse_cpr_scanner import bullish_bias_view, export_results, load_scan_result, scan_csv_path, split_shortlists


class TestBullishViews(unittest.TestCase):
    def _frame(self):
        return pd.DataFrame(
            [
                {
                    "SYMBOL": "HINDZINC", "CLOSE": 100.0, "Pivot": 99.8, "BC": 99.6,
                    "TC": 100.0, "CPR_Top": 100.0, "CPR_Bottom": 99.6,
                    "CPR_Width_Pct": 0.40, "CPR_Class": "Moderate", "Bias": "Bullish",
                    "Price_Position": "Inside CPR", "Bullish_CPR": False, "Bearish_CPR": False,
                },
                {
                    "SYMBOL": "TVSSRICHAK", "CLOSE": 120.0, "Pivot": 119.8, "BC": 119.6,
                    "TC": 120.0, "CPR_Top": 120.0, "CPR_Bottom": 119.6,
                    "CPR_Width_Pct": 1.20, "CPR_Class": "Wide", "Bias": "Bullish",
                    "Price_Position": "Above CPR", "Bullish_CPR": False, "Bearish_CPR": False,
                },
                {
                    "SYMBOL": "NARROWLONG", "CLOSE": 110.5, "Pivot": 109.8, "BC": 109.6,
                    "TC": 110.0, "CPR_Top": 110.0, "CPR_Bottom": 109.6,
                    "CPR_Width_Pct": 0.10, "CPR_Class": "Narrow", "Bias": "Bullish",
                    "Price_Position": "Above CPR", "Bullish_CPR": True, "Bearish_CPR": False,
                },
            ]
        )

    def test_bias_view_is_broader_than_strict_bullish_cpr(self):
        frame = self._frame()
        _, _, strict, _, _ = split_shortlists(frame)
        bias = bullish_bias_view(frame)
        self.assertEqual(set(bias["SYMBOL"]), {"HINDZINC", "TVSSRICHAK", "NARROWLONG"})
        self.assertEqual(list(strict["SYMBOL"]), ["NARROWLONG"])

    def test_export_and_reload_preserve_both_views(self):
        frame = self._frame()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            export_results(frame, "20260826", output_dir=output, verbose=False)
            bias_path = scan_csv_path("bullish_bias", "20260826", output)
            strict_path = scan_csv_path("bullish", "20260826", output)
            self.assertTrue(bias_path.exists())
            self.assertTrue(strict_path.exists())
            self.assertEqual(
                set(pd.read_csv(bias_path)["SYMBOL"]),
                {"HINDZINC", "TVSSRICHAK", "NARROWLONG"},
            )
            self.assertEqual(list(pd.read_csv(strict_path)["SYMBOL"]), ["NARROWLONG"])
            loaded = load_scan_result("20260826", output_dir=output)
            self.assertEqual(
                set(loaded.bullish_bias["SYMBOL"]),
                {"HINDZINC", "TVSSRICHAK", "NARROWLONG"},
            )


if __name__ == "__main__":
    unittest.main()
