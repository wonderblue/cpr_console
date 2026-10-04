import unittest

from live_watchlist import watchlist_membership


class TestWatchlistMembership(unittest.TestCase):
    def test_quote_refresh_reports_entries_and_exits(self):
        entered, exited, baseline = watchlist_membership(
            ["BBB", "CCC"],
            ["AAA", "BBB"],
            record_events=True,
        )
        self.assertEqual(entered, ["CCC"])
        self.assertEqual(exited, ["AAA"])
        self.assertEqual(baseline, ["BBB", "CCC"])

    def test_filter_only_tick_updates_baseline_without_events(self):
        entered, exited, baseline = watchlist_membership(
            ["BBB"],
            ["AAA", "BBB", "CCC"],
            record_events=False,
        )
        self.assertEqual(entered, [])
        self.assertEqual(exited, [])
        self.assertEqual(baseline, ["BBB"])

    def test_first_quote_refresh_does_not_mark_the_whole_list_new(self):
        entered, exited, baseline = watchlist_membership(
            ["AAA", "BBB"],
            [],
            record_events=True,
        )
        self.assertEqual(entered, [])
        self.assertEqual(exited, [])
        self.assertEqual(baseline, ["AAA", "BBB"])


if __name__ == "__main__":
    unittest.main()
