import copy
from datetime import datetime, timedelta, timezone
import io
import json
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from scripts import collect_odds as c

NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
START = "2026-09-10T00:20:00+00:00"


def game():
    return {"game_id": "2026_01_NE_SEA", "home_team": "SEA", "away_team": "NE", "kickoff": START}


def event():
    odds = {}
    for odd_id, (_, _, line) in c.MARKETS.items():
        quote = {"available": True, "odds": "-110", "altLines": [{"odds": "+150", "spread": "-7"}]}
        if line:
            quote[line] = "0" if line == "spread" else "45.5"
        odds[odd_id] = {"oddID": odd_id, "started": False, "byBookmaker": {"draftkings": quote}}
    return {"eventID": "fixture", "leagueID": "NFL", "status": {
        "startsAt": START, "started": False, "live": False, "ended": False,
        "cancelled": False, "completed": False, "oddsAvailable": True},
        "teams": {"home": {"teamID": "SEATTLE_SEAHAWKS_NFL", "names": {"short": "SEA"}},
                  "away": {"teamID": "NEW_ENGLAND_PATRIOTS_NFL", "names": {"short": "NE"}}}, "odds": odds}


class CollectorTests(unittest.TestCase):
    def test_six_main_selections_and_book_specific_values(self):
        e = event()
        e["odds"]["passing_yards-PLAYER-game-ou-over"] = next(iter(e["odds"].values()))
        rows, counts = c.snapshots([(e, NOW)], [game()], NOW)
        self.assertEqual(len(rows), 6)
        self.assertEqual({r["market"] for r in rows}, {"spread", "moneyline", "total"})
        self.assertTrue(all(r["captured_at"] == NOW.isoformat() and r["is_live"] is False for r in rows))
        self.assertTrue(all("player_id" not in r for r in rows))
        self.assertEqual(rows[0]["line"], "0")
        self.assertIsNone(next(r for r in rows if r["market"] == "moneyline")["line"])
        self.assertEqual(sum(counts.values()), 0)

    def test_each_book_is_a_separate_row(self):
        e = event()
        odd = e["odds"]["points-home-game-sp-home"]
        odd["bookSpread"] = "99"
        odd["byBookmaker"]["fanduel"] = {"available": True, "odds": "+120", "spread": "-3.5"}
        rows, _ = c.snapshots([(e, NOW)], [game()], NOW)
        row = next(r for r in rows if r["sportsbook"] == "fanduel")
        self.assertEqual((row["line"], row["odds_american"]), ("-3.5", 120))

    def test_live_started_cancelled_ended_and_past_fail_closed(self):
        for flag in ("live", "started", "cancelled", "ended", "completed"):
            e = event()
            e["status"][flag] = True
            self.assertEqual(c.snapshots([(e, NOW)], [game()], NOW)[0], [])
        for mutation in ({"startsAt": NOW.isoformat()}, {"started": None}, {"oddsAvailable": False}):
            e = event()
            e["status"].update(mutation)
            self.assertEqual(c.snapshots([(e, NOW)], [game()], NOW)[0], [])
        self.assertEqual(c.snapshots([(event(), NOW)], [game()], c.timestamp(START))[0], [])

    def test_market_and_book_availability(self):
        e = event()
        for odd in e["odds"].values():
            odd["byBookmaker"]["draftkings"]["available"] = False
        self.assertEqual(c.snapshots([(e, NOW)], [game()], NOW)[0], [])
        e = event()
        for odd in e["odds"].values():
            odd["started"] = True
        self.assertEqual(c.snapshots([(e, NOW)], [game()], NOW)[0], [])

    def test_matching_ambiguous_reversed_and_changed_time(self):
        self.assertEqual(c.match_game(event(), [game()]), game())
        self.assertIsNone(c.match_game(event(), [game(), game()]))
        g = game()
        g["home_team"], g["away_team"] = g["away_team"], g["home_team"]
        self.assertIsNone(c.match_game(event(), [g]))
        g = game()
        g["kickoff"] = "2026-09-11T00:20:00Z"
        self.assertIsNone(c.match_game(event(), [g]))
        rows, counts = c.snapshots([(event(), NOW)], [g], NOW)
        self.assertEqual(rows, [])
        self.assertEqual(counts["unmatched_events"], 1)

    def test_aliases_and_conflicting_teams(self):
        self.assertEqual(c.team_code({"names": {"short": "LAR"}}), "LA")
        self.assertEqual(c.team_code({"names": {"short": "JAC"}}), "JAX")
        self.assertIsNone(c.team_code({"teamID": "LOS_ANGELES_CHARGERS_NFL", "names": {"short": "LAR"}}))
        self.assertIsNone(c.team_code({"names": {"short": "UNKNOWN"}}))

    def test_bad_prices_and_missing_lines_are_not_inserted(self):
        for value in ("NaN", "Infinity", "0", "-99", "110.5", None):
            e = event()
            e["odds"]["points-home-game-sp-home"]["byBookmaker"]["draftkings"]["odds"] = value
            rows, counts = c.snapshots([(e, NOW)], [game()], NOW)
            self.assertEqual(len(rows), 5)
            self.assertEqual(counts["invalid_quotes"], 1)

    @patch.object(c, "utcnow", return_value=NOW)
    @patch.object(c, "request_json")
    def test_pagination_filters_and_deduplication(self, request, clock):
        request.side_effect = [{"success": True, "data": [event()], "nextCursor": "abc"},
                               {"success": True, "data": [event()]}]
        result = c.fetch_events("secret", NOW, NOW + timedelta(days=7))
        self.assertEqual(len(result), 1)
        query = parse_qs(urlsplit(request.call_args_list[0].args[0]).query)
        self.assertEqual(query["leagueID"], ["NFL"])
        self.assertEqual(query["started"], ["false"])
        self.assertEqual(query["includeAltLines"], ["false"])
        self.assertEqual(set(query["oddID"][0].split(",")), set(c.MARKETS))
        self.assertNotIn("secret", request.call_args_list[0].args[0])
        self.assertEqual(request.call_args_list[0].args[1], {"x-api-key": "secret"})
        next_query = parse_qs(urlsplit(request.call_args_list[1].args[0]).query)
        self.assertEqual(next_query.pop("cursor"), ["abc"])
        self.assertEqual(query, next_query)

    @patch.object(c, "request_json")
    def test_page_budget_and_repeated_cursor_fail(self, request):
        request.return_value = {"success": True, "data": [], "nextCursor": "abc"}
        with self.assertRaises(ValueError):
            c.fetch_events("key", NOW, NOW, max_pages=1)
        with self.assertRaises(ValueError):
            c.fetch_events("key", NOW, NOW, max_pages=3)

    @patch.object(c, "request_json")
    def test_cursor_404_is_end_but_rate_limit_is_failure(self, request):
        request.side_effect = [{"success": True, "data": [], "nextCursor": "abc"},
                               HTTPError("url", 404, "", {}, None)]
        self.assertEqual(c.fetch_events("key", NOW, NOW), [])
        request.side_effect = HTTPError("url", 429, "", {}, None)
        with self.assertRaises(HTTPError):
            c.fetch_events("key", NOW, NOW)

    @patch.object(c, "request_json")
    def test_provider_http_error_reports_json_without_retry(self, request):
        error = HTTPError("https://private.example", 403, "private reason", {},
                          io.BytesIO(b'{"success":false,"error":"Plan does not allow NFL odds"}'))
        request.side_effect = error
        with redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(HTTPError) as raised:
                c.fetch_events("private-key", NOW, NOW)
        self.assertIs(raised.exception, error)
        request.assert_called_once()
        self.assertIn('SportsGameOdds HTTP 403: {"error": "Plan does not allow NFL odds"}', output.getvalue())
        self.assertNotIn("private", output.getvalue())

    @patch.dict(c.os.environ, {"SUPABASE_SECRET_KEY": "db-secret"})
    def test_provider_error_redacts_secrets_and_omits_headers(self):
        body = {"error": {"message": "Denied token a/b and a%2Fb and db-secret\n::warning::echo"},
                "headers": {"x-api-key": "a/b", "custom": "hidden-header"},
                "request": {"headers": {"custom": "hidden-header"}}}
        error = HTTPError("url", 403, "", {}, io.BytesIO(json.dumps(body).encode()))
        with redirect_stderr(io.StringIO()) as output:
            c.report_provider_error(error, "a/b")
        log = output.getvalue()
        for secret in ("a/b", "a%2Fb", "db-secret", "hidden-header", "x-api-key"):
            self.assertNotIn(secret, log)
        self.assertIn("[REDACTED]", log)
        self.assertEqual(len(log.splitlines()), 1)
        error = HTTPError("url", 403, "", {},
                          io.BytesIO(b'{"error":"Request headers: custom=hidden-header"}'))
        with redirect_stderr(io.StringIO()) as output:
            c.report_provider_error(error, "a/b")
        self.assertNotIn("hidden-header", output.getvalue())

    def test_provider_error_non_json_empty_and_oversized_bodies_are_omitted(self):
        for body in (b"", b"<html>private</html>", b'{"error":"' + b'x' * 17000 + b'"}'):
            with self.subTest(body_length=len(body)):
                error = HTTPError("url", 403, "private", {}, io.BytesIO(body))
                with redirect_stderr(io.StringIO()) as output:
                    c.report_provider_error(error, "key")
                self.assertIn("body omitted", output.getvalue())
                self.assertNotIn("private", output.getvalue())

    @patch.object(c, "request_json")
    @patch.object(c, "report_provider_error")
    def test_cursor_end_and_supabase_errors_do_not_log_provider_body(self, report, request):
        request.side_effect = [{"success": True, "data": [], "nextCursor": "abc"},
                               HTTPError("url", 404, "", {}, None)]
        self.assertEqual(c.fetch_events("key", NOW, NOW), [])
        request.side_effect = HTTPError("url", 403, "", {}, None)
        with self.assertRaises(HTTPError):
            c.fetch_games("https://db.example", "key", NOW, NOW)
        report.assert_not_called()

    @patch.object(c, "request_json")
    def test_append_only_write_contract(self, request):
        rows, _ = c.snapshots([(event(), NOW)], [game()], NOW)
        c.insert_history("https://db.example", "sb_secret_test", rows)
        c.insert_history("https://db.example", "sb_secret_test", rows)
        self.assertEqual(request.call_count, 2)
        url, headers, payload = request.call_args.args
        self.assertEqual(url, "https://db.example/rest/v1/odds_history")
        self.assertEqual(headers["Prefer"], "return=minimal")
        self.assertTrue(all("id" not in row for row in payload))

    @patch.object(c, "request_json")
    def test_games_pagination(self, request):
        request.side_effect = [[game()], []]
        self.assertEqual(c.fetch_games("https://db.example", "key", NOW, NOW), [game()])
        self.assertIn("offset=1", request.call_args.args[0])

    @patch.object(c, "utcnow", return_value=NOW)
    @patch.object(c.Path, "read_text")
    @patch.object(c, "insert_history")
    def test_offline_dry_run_never_writes(self, insert, read, clock):
        read.side_effect = [json.dumps([game()]), json.dumps({"success": True, "data": [event()]})]
        self.assertEqual(c.main(["--dry-run", "--events-json", "events.json", "--games-json", "games.json"]), 0)
        insert.assert_not_called()

    @patch.dict(c.os.environ, {}, clear=True)
    @patch.object(c, "request_json")
    def test_missing_key_fails_without_network_or_leak(self, request):
        with redirect_stderr(io.StringIO()) as output:
            self.assertEqual(c.main([]), 1)
        request.assert_not_called()
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
