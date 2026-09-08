import unittest

import pandas as pd

from cpr_contract import (
    CPR_NARROW_MAX_PCT,
    CPR_WIDE_MIN_PCT,
    calculate_cpr,
    calculate_cpr_frame,
    classify_width,
)
from signal_contract import (
    BreakoutSignal,
    SetupLabel,
    breakout_signal_rank,
    setup_score,
)


class TestCanonicalCpr(unittest.TestCase):
    def test_scalar_matches_standard_formula(self):
        levels = calculate_cpr(110, 100, 106)
        self.assertAlmostEqual(levels.pivot, 105.3333333333, places=6)
        self.assertAlmostEqual(levels.bc, 105.0, places=6)
        self.assertAlmostEqual(levels.tc, 105.6666666667, places=6)
        self.assertAlmostEqual(levels.width, 0.6666666667, places=6)
        self.assertAlmostEqual(levels.width_pct, levels.width / 106 * 100, places=6)

    def test_frame_matches_scalar_and_uses_close_denominator(self):
        frame = pd.DataFrame({"HIGH": [110], "LOW": [100], "CLOSE": [106]})
        result = calculate_cpr_frame(frame, "HIGH", "LOW", "CLOSE")
        scalar = calculate_cpr(110, 100, 106)
        self.assertAlmostEqual(result.iloc[0]["pivot"], scalar.pivot, places=6)
        self.assertAlmostEqual(result.iloc[0]["width_pct"], scalar.width_pct, places=6)
        self.assertEqual(result.iloc[0]["width_class"], "Moderate")
        self.assertEqual(result.iloc[0]["bias"], "Bullish")
        self.assertEqual(result.iloc[0]["price_position"], "Above CPR")

    def test_boundary_classes_are_consistent(self):
        self.assertEqual(classify_width(CPR_NARROW_MAX_PCT), "Narrow")
        self.assertEqual(classify_width(CPR_WIDE_MIN_PCT), "Wide")
        self.assertEqual(classify_width(0.50), "Moderate")

    def test_invalid_close_is_unknown_in_frame_and_rejected_as_scalar(self):
        with self.assertRaises(ValueError):
            calculate_cpr(110, 100, 0)
        frame = pd.DataFrame({"HIGH": [110], "LOW": [100], "CLOSE": [0]})
        result = calculate_cpr_frame(frame, "HIGH", "LOW", "CLOSE")
        self.assertTrue(pd.isna(result.iloc[0]["width_pct"]))
        self.assertEqual(result.iloc[0]["width_class"], "Unknown")

    def test_missing_reference_columns_produce_nan_and_unknown(self):
        frame = pd.DataFrame({
            "HIGH": [110.0, 200.0],
            "LOW": [100.0, 190.0],
            "CLOSE": [106.0, 195.0],
            "PREV_HIGH": [108.0, float("nan")],
            "PREV_LOW": [98.0, float("nan")],
            "PREV_CLOSE": [104.0, float("nan")],
        })
        result = calculate_cpr_frame(
            frame,
            "HIGH",
            "LOW",
            "CLOSE",
            ref_high_col="PREV_HIGH",
            ref_low_col="PREV_LOW",
            ref_close_col="PREV_CLOSE",
        )
        # Row 0: valid reference data
        self.assertAlmostEqual(result.iloc[0]["pivot"], (108 + 98 + 104) / 3.0, places=6)
        self.assertNotEqual(result.iloc[0]["width_class"], "Unknown")
        # Row 1: missing reference data -> NaN levels, Unknown classifications (never self-referencing)
        self.assertTrue(pd.isna(result.iloc[1]["pivot"]))
        self.assertTrue(pd.isna(result.iloc[1]["bc"]))
        self.assertTrue(pd.isna(result.iloc[1]["tc"]))
        self.assertTrue(pd.isna(result.iloc[1]["top"]))
        self.assertTrue(pd.isna(result.iloc[1]["bottom"]))
        self.assertTrue(pd.isna(result.iloc[1]["width"]))
        self.assertTrue(pd.isna(result.iloc[1]["width_pct"]))
        self.assertEqual(result.iloc[1]["width_class"], "Unknown")
        self.assertEqual(result.iloc[1]["bias"], "Unknown")
        self.assertEqual(result.iloc[1]["price_position"], "Unknown")


class TestSignalContract(unittest.TestCase):
    def test_eod_scores_preserve_existing_semantics(self):
        self.assertEqual(setup_score(SetupLabel.LONG.value), 2)
        self.assertEqual(setup_score(SetupLabel.WATCH_LONG.value), 1)
        self.assertEqual(setup_score(SetupLabel.SHORT.value), -2)
        self.assertEqual(setup_score(SetupLabel.WATCH_SHORT.value), -1)
        self.assertEqual(setup_score(SetupLabel.WATCH.value), 0)
        self.assertEqual(setup_score(SetupLabel.NO_SETUP.value), 0)

    def test_breakout_labels_and_rank_are_stable(self):
        self.assertEqual(BreakoutSignal.LONG.value, "Long")
        self.assertEqual(BreakoutSignal.SHORT.value, "Short")
        self.assertEqual(BreakoutSignal.WATCH.value, "Watch")
        self.assertEqual(BreakoutSignal.NONE.value, "None")
        self.assertLess(breakout_signal_rank("Long"), breakout_signal_rank("Watch"))


if __name__ == "__main__":
    unittest.main()
