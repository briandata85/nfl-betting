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
