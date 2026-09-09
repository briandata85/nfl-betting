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
