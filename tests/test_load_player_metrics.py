import unittest
from unittest.mock import patch
from scripts import load_player_metrics as m

ROWS = [
    {"season_type": "REG", "week": 1, "player_id": "p1", "player_name": "One", "recent_team": "AAA", "position": "WR", "games": 1, "passing_epa": 0, "targets": 4, "receptions": 2, "receiving_yards": 20, "receiving_epa": 1, "carries": 2, "rushing_yards": 8, "rushing_epa": .4, "cpoe": 5},
    {"season_type": "REG", "week": 2, "player_id": "p1", "player_name": "One", "recent_team": "AAA", "position": "WR", "games": 1, "passing_epa": 0, "targets": 6, "receptions": 3, "receiving_yards": 30, "receiving_epa": 2, "carries": 0, "rushing_yards": 0, "rushing_epa": 0, "cpoe": 7},
    {"season_type": "REG", "week": 1, "player_id": "p2", "player_name": "Two", "recent_team": "AAA", "position": "RB", "games": 1, "passing_epa": 0, "targets": 0, "receptions": 0, "receiving_yards": 0, "receiving_epa": 0, "carries": 8, "rushing_yards": 40, "rushing_epa": 3, "cpoe": None},
    {"season_type": "POST", "week": 19, "player_id": "p1", "player_name": "One", "recent_team": "AAA", "position": "WR", "games": 1, "targets": 99, "carries": 99},
]

class PlayerMetricsTests(unittest.TestCase):
    def test_reg_level_rows_use_source_fields_directly(self):
        aggregate = [{"season_type": "REG", "player_id": "p1", "player_name": "One", "recent_team": "AAA", "position": "QB",
                      "games": 17, "passing_epa": 42, "passing_cpoe": 4.5, "targets": 10, "carries": 20,
                      "rushing_yards": 100, "rushing_epa": 3, "receptions": 7, "receiving_yards": 80, "receiving_epa": 2}]
        row = m.calculate(aggregate)[0]
        self.assertEqual((row["games_played"], row["passing_epa"], row["cpoe"]), (17, 42, 4.5))

    @patch.object(m, "nfl")
    def test_load_rows_uses_reg_summary(self, nfl):
        nfl.load_player_stats.return_value.to_dicts.return_value = ROWS
        self.assertEqual(m.load_rows(), ROWS)
        nfl.load_player_stats.assert_called_once_with(2025, summary_level="reg")

    @patch.object(m, "nfl")
    def test_load_error_identifies_call(self, nfl):
        nfl.load_player_stats.side_effect = RuntimeError("download failed")
        with self.assertRaisesRegex(RuntimeError, "load_player_stats\(2025, summary_level='reg'\)"):
            m.load_rows()
    def test_regular_aggregation_and_team_shares(self):
        rows = m.calculate(ROWS)
        one = next(row for row in rows if row["player_id"] == "p1")
        self.assertEqual(one["targets"], 10)
        self.assertEqual(one["rushing_yards"], 8)
        self.assertEqual(one["target_share"], 1.0)
        self.assertEqual(one["rush_share"], .2)
        self.assertEqual(one["cpoe"], 6.0)
        self.assertTrue(all(row["through_week"] == 18 for row in rows))

    @patch.object(m, "upsert")
    @patch.object(m, "load_rows", return_value=ROWS)
    def test_dry_run_does_not_write(self, load, upsert):
        self.assertEqual(m.main(["--dry-run"]), 0)
        upsert.assert_not_called()

if __name__ == "__main__": unittest.main()
