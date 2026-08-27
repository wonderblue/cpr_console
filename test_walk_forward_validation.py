import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from walk_forward_validation import (
    build_report,
    build_walk_forward_details,
    summarize_details,
    write_report,
)
from nse_cpr_scanner import scan_csv_path


BASE_COLUMNS = [
    "SYMBOL", "CLOSE", "TC", "BC", "CPR_Top", "CPR_Bottom", "CPR_Class", "Setup",
    "Signal_Direction", "Signal_Score", "Signal_Grade", "Strategy_Type",
    "Strategy_Setup", "Strategy_Confirmation", "Strategy_Explanation",
]


class TestWalkForwardValidation(unittest.TestCase):
    def _write_session(self, root: Path, date: str, closes: dict[str, float]) -> None:
        rows = [
            {
                "SYMBOL": "AAA", "CLOSE": closes["AAA"], "TC": 105.0, "BC": 95.0,
                "CPR_Top": 105.0, "CPR_Bottom": 95.0,
                "CPR_Class": "Narrow", "Setup": "Long", "Signal_Direction": "Long",
                "Signal_Score": 80, "Signal_Grade": "A", "Strategy_Type": "Narrow CPR",
                "Strategy_Setup": "Not applicable", "Strategy_Confirmation": "Not applicable",
                "Strategy_Explanation": "legacy narrow",
            },
            {
                "SYMBOL": "BBB", "CLOSE": closes["BBB"], "TC": 195.0, "BC": 205.0,
                "CPR_Top": 205.0, "CPR_Bottom": 195.0,
                "CPR_Class": "Narrow", "Setup": "Short", "Signal_Direction": "Short",
                "Signal_Score": 70, "Signal_Grade": "B", "Strategy_Type": "Narrow CPR",
                "Strategy_Setup": "Not applicable", "Strategy_Confirmation": "Not applicable",
                "Strategy_Explanation": "legacy narrow",
            },
            {
                "SYMBOL": "CCC", "CLOSE": closes["CCC"], "TC": 305.0, "BC": 295.0,
                "CPR_Top": 305.0, "CPR_Bottom": 295.0,
                "CPR_Class": "Wide", "Setup": "No setup", "Signal_Direction": "Long",
                "Signal_Score": 75, "Signal_Grade": "B", "Strategy_Type": "Wide CPR",
                "Strategy_Setup": "Wide Upside Breakout", "Strategy_Confirmation": "Confirmed",
                "Strategy_Explanation": "confirmed wide",
            },
            {
                "SYMBOL": "DDD", "CLOSE": closes["DDD"], "TC": 395.0, "BC": 405.0,
                "CPR_Top": 405.0, "CPR_Bottom": 395.0,
                "CPR_Class": "Wide", "Setup": "No setup", "Signal_Direction": "Neutral",
                "Signal_Score": 50, "Signal_Grade": "C", "Strategy_Type": "Wide CPR",
                "Strategy_Setup": "Wide Consolidation", "Strategy_Confirmation": "Watch",
                "Strategy_Explanation": "consolidation",
            },
        ]
        path = scan_csv_path("full", date, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=BASE_COLUMNS).to_csv(path, index=False)

    def test_directional_and_wide_outcomes_use_next_completed_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_session(root, "20260813", {"AAA": 100, "BBB": 200, "CCC": 300, "DDD": 400})
            self._write_session(root, "20260814", {"AAA": 110, "BBB": 210, "CCC": 300, "DDD": 400})
            details = build_walk_forward_details(root)
            outcomes = dict(zip(details["SYMBOL"], details["Outcome"]))
            cohorts = dict(zip(details["SYMBOL"], details["Cohort"]))
            self.assertEqual(outcomes["AAA"], "Followed")
            self.assertEqual(outcomes["BBB"], "Failed")
            self.assertEqual(outcomes["CCC"], "Flat")
            self.assertEqual(outcomes["DDD"], "Not directional")
            self.assertEqual(cohorts["AAA"], "Long")
            self.assertEqual(cohorts["CCC"], "Wide Confirmed Upside")

    def test_bearish_geometry_uses_ordered_cpr_bounds_not_tc_bc_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_session(root, "20260813", {"AAA": 100, "BBB": 200, "CCC": 300, "DDD": 400})
            self._write_session(root, "20260814", {"AAA": 100, "BBB": 190, "CCC": 300, "DDD": 390})
            details = build_walk_forward_details(root)
            outcomes = dict(zip(details["SYMBOL"], details["Outcome"]))
            self.assertEqual(outcomes["BBB"], "Followed")

    def test_validator_never_uses_unpaired_final_session_as_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_session(root, "20260813", {"AAA": 100, "BBB": 200, "CCC": 300, "DDD": 400})
            details = build_walk_forward_details(root)
            self.assertTrue(details.empty)
            report = build_report(root)
            self.assertEqual(report["sessions_requested"], 1)
            self.assertEqual(report["signal_sessions_evaluated"], 0)

    def test_summary_excludes_nonresolved_rows_from_rates(self):
        details = pd.DataFrame([
            {"Cohort": "Long", "Outcome": "Followed"},
            {"Cohort": "Long", "Outcome": "Failed"},
            {"Cohort": "Long", "Outcome": "Flat"},
            {"Cohort": "Long", "Outcome": "No data"},
        ])
        summary = summarize_details(details).iloc[0]
        self.assertEqual(summary["Signals"], 4)
        self.assertEqual(summary["No_Data"], 1)
        self.assertEqual(summary["Resolved"], 3)
        self.assertAlmostEqual(summary["Follow_Rate"], 1 / 3)

    def test_report_writes_compact_json_and_csv_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "output"
            report_dir = Path(tmp) / "report"
            root.mkdir()
            self._write_session(root, "20260813", {"AAA": 100, "BBB": 200, "CCC": 300, "DDD": 400})
            self._write_session(root, "20260814", {"AAA": 110, "BBB": 210, "CCC": 300, "DDD": 400})
            paths = write_report(root, report_dir)
            self.assertTrue(paths["json"].exists())
            self.assertTrue(paths["summary"].exists())
            self.assertTrue(paths["details"].exists())
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertIn("lookahead_policy", payload)
            self.assertEqual(len(payload["details"]), 4)


if __name__ == "__main__":
    unittest.main()
