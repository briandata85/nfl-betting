import unittest
from scripts import load_qb_advanced_metrics as m


class QbAdvancedTests(unittest.TestCase):
    def test_aggregate_is_pregame_safe_and_weighted(self):
        rows = [
            {"season": 2026, "week": 1, "game_id": "g1", "team": "BUF", "pfr_player_id": "AllenJo02", "pfr_player_name": "Josh Allen", "attempts": 20, "times_sacked": 2, "times_blitzed": 8, "times_hurried": 3, "times_hit": 2, "times_pressured": 7, "times_pressured_pct": 35.0, "passing_bad_throw_pct": 10.0, "passing_drop_pct": 5.0},
            {"season": 2026, "week": 2, "game_id": "g2", "team": "BUF", "pfr_player_id": "AllenJo02", "pfr_player_name": "Josh Allen", "attempts": 40, "times_sacked": 1, "times_blitzed": 10, "times_hurried": 2, "times_hit": 1, "times_pressured": 4, "times_pressured_pct": 10.0, "passing_bad_throw_pct": 20.0, "passing_drop_pct": 2.0},
            {"season": 2026, "week": 3, "game_id": "g3", "team": "BUF", "pfr_player_id": "AllenJo02", "pfr_player_name": "Josh Allen", "attempts": 99, "times_pressured_pct": 99.0},
        ]
        out = m.aggregate(rows, 2026, 2)
        self.assertEqual(len(out), 1)
        qb = out[0]
        self.assertEqual(qb["games_played"], 2)
        self.assertEqual(qb["pass_attempts"], 60)
        self.assertEqual(qb["times_sacked"], 3)
        self.assertAlmostEqual(qb["pressure_pct"], (35 * 20 + 10 * 40) / 60)
        self.assertAlmostEqual(qb["bad_throw_pct"], (10 * 20 + 20 * 40) / 60)

    def test_name_fallback_id(self):
        rows = [{"season": 2025, "week": 1, "game_id": "g", "team": "NYG", "pfr_player_name": "Example QB", "times_sacked": 1}]
        out = m.aggregate(rows, 2025, 1)
        self.assertEqual(out[0]["player_id"], "name:NYG:Example QB")


if __name__ == "__main__":
    unittest.main()
