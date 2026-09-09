import unittest
from unittest.mock import patch

from scripts import backfill_history as b


class HistoricalBackfillTests(unittest.TestCase):
    def test_schedule_rows_keeps_regular_games_and_market_fields(self):
        rows = [
            {
                "game_id": "2025_01_AAA_BBB", "season": 2025, "game_type": "REG", "week": 1,
                "gameday": "2025-09-07", "away_team": "AAA", "home_team": "BBB",
                "stadium": "Test Field", "roof": "outdoors", "surface": "grass",
                "away_score": 17, "home_score": 24, "result": 7, "total": 41,
                "away_moneyline": 140, "home_moneyline": -165, "spread_line": 3.5,
                "total_line": 42.5,
            },
            {
                "game_id": "2025_19_AAA_BBB", "season": 2025, "game_type": "WC", "week": 19,
                "gameday": "2026-01-10", "away_team": "AAA", "home_team": "BBB",
            },
        ]
        games = b.schedule_rows(rows, 2025)
        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual(game["result"], 7)
        self.assertEqual(game["nflverse_home_moneyline"], -165)
        self.assertEqual(game["nflverse_spread_line"], 3.5)
        self.assertEqual(game["nflverse_total_line"], 42.5)

    @patch.object(b, "load_season")
    @patch.object(b, "credentials")
    @patch.object(b, "upsert_games")
    @patch.object(b, "upsert_team_metrics")
    def test_dry_run_never_writes(self, upsert_metrics, upsert_games, credentials, load_season):
        load_season.return_value = (
            [{"game_id": "g1"}],
            [{"season": 2025, "through_week": 1, "team": "AAA"}],
        )
        self.assertEqual(b.main(["--start-season", "2025", "--end-season", "2025", "--dry-run"]), 0)
        credentials.assert_not_called()
        upsert_games.assert_not_called()
        upsert_metrics.assert_not_called()


if __name__ == "__main__":
    unittest.main()
