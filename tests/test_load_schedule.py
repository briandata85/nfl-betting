import csv
import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from scripts import load_schedule as loader


def schedule(**changes):
    row = dict(game_id="2026_01_NE_SEA", season="2026", week="1",
               gameday="2026-09-09", gametime="20:20", away_team="NE",
               home_team="SEA", stadium="Lumen Field", roof="outdoors", surface="fieldturf")
    row.update(changes)
    return row


def csv_text(*rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(schedule()))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


class ScheduleTests(unittest.TestCase):
    def test_filter_mapping_and_dst(self):
        games = loader.parse_schedule(csv_text(schedule(season="2025"), schedule()))
        self.assertEqual(games, [dict(
            game_id="2026_01_NE_SEA", season=2026, week=1,
            game_date="2026-09-09", kickoff="2026-09-10T00:20:00+00:00",
            away_team="NE", home_team="SEA", stadium="Lumen Field",
            roof="outdoors", surface="fieldturf")])

    def test_january_stays_in_2026_season_and_uses_standard_time(self):
        game = loader.parse_schedule(csv_text(schedule(gameday="2027-01-03", gametime="20:20")))[0]
        self.assertEqual(game["season"], 2026)
        self.assertEqual(game["kickoff"], "2027-01-04T01:20:00+00:00")

    def test_unknown_values_are_null(self):
        game = loader.parse_schedule(csv_text(schedule(gametime="TBD", roof="NA", surface="")))[0]
        self.assertIsNone(game["kickoff"])
        self.assertIsNone(game["roof"])
        self.assertIsNone(game["surface"])
        game = loader.parse_schedule(csv_text(schedule(gameday="")))[0]
        self.assertIsNone(game["game_date"])
        self.assertIsNone(game["kickoff"])

    def test_invalid_source_fails_before_writes(self):
        for text in ("season\n2026\n", csv_text(schedule(season="2025")),
                     csv_text(schedule(), schedule()), csv_text(schedule(game_id="")),
                     csv_text(schedule(gametime="25:00"))):
            with self.subTest(text=text), self.assertRaises(ValueError):
                loader.parse_schedule(text)

    @patch.object(loader, "build_opener")
    def test_upsert_contract_and_repeat_updates(self, build):
        stored = {}

        def receive(request, timeout):
            self.assertEqual(request.full_url, "https://example.supabase.co/rest/v1/games?on_conflict=game_id")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("Apikey"), "sb_secret_test")
            self.assertIsNone(request.get_header("Authorization"))
            self.assertEqual(request.get_header("Prefer"), "resolution=merge-duplicates,return=minimal")
            for row in json.loads(request.data):
                stored[row["game_id"]] = row
            response = MagicMock()
            response.__enter__.return_value.status = 201
            return response

        build.return_value.open.side_effect = receive
        for stadium in ("Original", "Updated"):
            loader.upsert_games(loader.parse_schedule(csv_text(schedule(stadium=stadium))),
                                "https://example.supabase.co", "sb_secret_test")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored["2026_01_NE_SEA"]["stadium"], "Updated")

    @patch.object(loader, "build_opener")
    def test_legacy_key(self, build):
        build.return_value.open.return_value.__enter__.return_value.status = 201
        loader.upsert_games([], "https://example.supabase.co", "legacy-jwt")
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer legacy-jwt")

    @patch.dict(loader.os.environ, {}, clear=True)
    def test_missing_credentials_fail_without_network(self):
        with patch.object(loader, "urlopen") as download, redirect_stderr(io.StringIO()):
            self.assertEqual(loader.main([]), 1)
        download.assert_not_called()

    def test_errors_do_not_log_secrets(self):
        for error in (HTTPError("secret-url", 401, "secret-key", {}, None),
                      URLError("secret-url secret-key")):
            with patch.object(loader, "credentials", return_value=("secret-url", "secret-key")), \
                 patch.object(loader, "urlopen", side_effect=error), \
                 redirect_stderr(io.StringIO()) as output:
                self.assertEqual(loader.main([]), 1)
                self.assertNotIn("secret-url", output.getvalue())
                self.assertNotIn("secret-key", output.getvalue())

    def test_redirects_are_rejected(self):
        self.assertIsNone(loader.NoRedirect().redirect_request(None, None, 307, "", {}, "https://other.example"))

    @patch.object(loader, "upsert_games")
    @patch.object(loader.Path, "read_text", return_value=csv_text(schedule()))
    def test_dry_run_never_writes(self, read, upsert):
        with patch.object(loader, "credentials") as credentials:
            self.assertEqual(loader.main(["--dry-run", "--csv", "fixture.csv"]), 0)
        credentials.assert_not_called()
        upsert.assert_not_called()

    @patch.object(loader, "build_opener")
    @patch.object(loader.Path, "read_text", return_value=csv_text(schedule()))
    @patch.object(loader, "credentials", return_value=("https://example.supabase.co", "sb_secret_test"))
    def test_database_failure_exits_nonzero(self, credentials, read, build):
        build.return_value.open.side_effect = HTTPError("secret-url", 409, "secret-key", {}, None)
        with redirect_stderr(io.StringIO()) as output:
            self.assertEqual(loader.main(["--csv", "fixture.csv"]), 1)
        self.assertIn("HTTP 409", output.getvalue())
        self.assertNotIn("secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
