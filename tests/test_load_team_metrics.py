import io, json, unittest
from unittest.mock import patch
from scripts import load_team_metrics as m

CSV = """season,week,game_id,result,posteam,defteam,epa,success,pass,rush,play_type,yards_gained
2026,1,g1,3,AAA,BBB,1,1,1,0,pass,25
2026,1,g1,3,BBB,AAA,-1,0,0,1,run,2
2026,2,g2,7,AAA,BBB,2,1,1,0,pass,5
2026,2,g2,7,BBB,AAA,-2,0,0,1,run,22
2026,3,g3,,AAA,BBB,9,1,1,0,pass,40
"""

class MetricsTests(unittest.TestCase):
    def test_schedule_requires_every_regular_game_to_have_result(self):
        schedule = [
            {"game_id": "g1", "week": 1, "game_type": "REG", "result": 3},
            {"game_id": "g2", "week": 1, "game_type": "REG", "result": None},
            {"game_id": "g3", "week": 2, "game_type": "REG", "result": 7},
        ]
        self.assertEqual(m.schedule_completion(schedule), (2, {"2": {"g3"}}))

    def test_schedule_no_completed_week_raises_specific_exception(self):
        with self.assertRaises(m.NoCompletedWeek):
            m.schedule_completion([{"game_id": "g1", "week": 1, "game_type": "REG", "result": None}])

    def test_no_completed_week_is_success_and_does_not_write(self):
        incomplete = CSV.replace(",3,", ",,").replace(",7,", ",,")
        with patch.object(m, "upsert") as upsert, patch.object(m.Path, "read_text", return_value=incomplete):
            with patch("builtins.print") as printed:
                self.assertEqual(m.main(["--csv", "fixture.csv"]), 0)
        printed.assert_called_once_with("No fully completed 2026 NFL week yet. Nothing to load.")
        upsert.assert_not_called()

    @patch.dict(m.os.environ, {"SUPABASE_URL": "https://secret.example", "SUPABASE_SECRET_KEY": "service-secret"})
    def test_failure_logs_safe_actual_error_without_credentials(self):
        with patch.object(m, "load_sources", side_effect=ValueError("bad PBP input")):
            with patch("builtins.print") as printed:
                self.assertEqual(m.main([]), 1)
        printed.assert_called_once_with("Team metrics load failed: bad PBP input")

    @patch.dict(m.os.environ, {"SUPABASE_URL": "https://secret.example", "SUPABASE_SECRET_KEY": "service-secret"})
    def test_failure_redacts_credentials(self):
        with patch.object(m, "load_sources", side_effect=ValueError("service-secret https://secret.example")):
            with patch("builtins.print") as printed:
                self.assertEqual(m.main([]), 1)
        self.assertEqual(printed.call_args.args[0], "Team metrics load failed: [REDACTED] [REDACTED]")

    def test_latest_completed_week_excludes_in_progress(self):
        rows = m.rows_from_text(CSV)
        through, metrics = m.calculate(rows)
        self.assertEqual(through, 2)
        self.assertEqual({row["team"] for row in metrics}, {"AAA", "BBB"})
        aaa = next(row for row in metrics if row["team"] == "AAA")
        self.assertEqual(aaa["games_played"], 2)
        self.assertEqual(aaa["explosive_play_rate"], 0.5)

    def test_success_and_split_metrics(self):
        _, metrics = m.calculate(m.rows_from_text(CSV))
        aaa = next(row for row in metrics if row["team"] == "AAA")
        self.assertEqual(aaa["offense_success_rate"], 1.0)
        self.assertEqual(aaa["pass_epa_per_play"], 1.5)
        self.assertIsNone(aaa["rush_epa_per_play"])

    @patch.object(m, "upsert")
    @patch.object(m, "credentials", return_value=("https://db.example", "key"))
    def test_dry_run_does_not_write(self, credentials, upsert):
        path = "fixture-team-metrics.csv"
        with patch.object(m.Path, "read_text", return_value=CSV):
            self.assertEqual(m.main(["--dry-run", "--csv", path]), 0)
        upsert.assert_not_called()

if __name__ == "__main__": unittest.main()
