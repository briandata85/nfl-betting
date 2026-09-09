import unittest
from scripts import load_depth_charts as m


class DepthChartTests(unittest.TestCase):
    def test_latest_rows_uses_newest_timestamp(self):
        rows = [
            {"season": 2026, "dt": "2026-09-08T07:00:00Z", "team": "BUF", "player_name": "Old QB", "gsis_id": "a", "pos_slot": 1, "pos_rank": 1},
            {"season": 2026, "dt": "2026-09-09T07:00:00Z", "team": "BUF", "player_name": "New QB", "gsis_id": "b", "pos_slot": 1.0, "pos_rank": 1.0},
            {"season": 2025, "dt": "2026-02-01T07:00:00Z", "team": "BUF", "player_name": "Other", "gsis_id": "c", "pos_slot": 1, "pos_rank": 1},
        ]
        dt, output = m.latest_rows(rows, 2026)
        self.assertEqual(dt, "2026-09-09T07:00:00Z")
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["player_name"], "New QB")
        self.assertEqual(output[0]["pos_slot"], 1)
        self.assertEqual(output[0]["pos_rank"], 1)

    def test_requires_timestamped_rows(self):
        with self.assertRaisesRegex(ValueError, "No timestamped depth chart"):
            m.latest_rows([{"season": 2026, "team": "BUF", "player_name": "QB"}], 2026)


if __name__ == "__main__":
    unittest.main()
