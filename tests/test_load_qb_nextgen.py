import unittest
from scripts import load_qb_nextgen as m


class QbNextGenTests(unittest.TestCase):
    def test_prefers_regular_summary_row(self):
        rows = [
            {"season": 2025, "season_type": "REG", "week": 0, "player_gsis_id": "qb1", "player_display_name": "QB One", "team_abbr": "BUF", "attempts": 500, "avg_time_to_throw": 2.8, "completion_percentage_above_expectation": 3.5},
            {"season": 2025, "season_type": "REG", "week": 1, "player_gsis_id": "qb1", "player_display_name": "QB One", "team_abbr": "BUF", "attempts": 30, "avg_time_to_throw": 9.9, "completion_percentage_above_expectation": -20},
        ]
        out = m.aggregate(rows, 2025, 18)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["attempts"], 500)
        self.assertEqual(out[0]["avg_time_to_throw"], 2.8)
        self.assertEqual(out[0]["cpoe"], 3.5)
        self.assertEqual(out[0]["through_week"], 18)

    def test_weekly_fallback_is_attempt_weighted_and_pregame_safe(self):
        rows = [
            {"season": 2026, "season_type": "REG", "week": 1, "player_gsis_id": "qb1", "player_display_name": "QB One", "team_abbr": "BUF", "attempts": 20, "avg_time_to_throw": 2.0, "aggressiveness": 10.0},
            {"season": 2026, "season_type": "REG", "week": 2, "player_gsis_id": "qb1", "player_display_name": "QB One", "team_abbr": "BUF", "attempts": 40, "avg_time_to_throw": 3.0, "aggressiveness": 20.0},
            {"season": 2026, "season_type": "REG", "week": 3, "player_gsis_id": "qb1", "player_display_name": "QB One", "team_abbr": "BUF", "attempts": 99, "avg_time_to_throw": 9.0, "aggressiveness": 99.0},
        ]
        out = m.aggregate(rows, 2026, 2)
        qb = out[0]
        self.assertEqual(qb["attempts"], 60)
        self.assertAlmostEqual(qb["avg_time_to_throw"], (2 * 20 + 3 * 40) / 60)
        self.assertAlmostEqual(qb["aggressiveness"], (10 * 20 + 20 * 40) / 60)

    def test_postseason_rows_are_excluded(self):
        rows = [{"season": 2025, "season_type": "POST", "week": 1, "player_gsis_id": "qb1", "player_display_name": "QB", "attempts": 10}]
        self.assertEqual(m.aggregate(rows, 2025, 18), [])


if __name__ == "__main__":
    unittest.main()
