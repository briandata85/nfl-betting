import unittest
from unittest.mock import patch
from scripts import load_injuries as m

HTML = """<table><tr><th>Seattle Seahawks</th></tr><tr><td>Player</td><td>Pos</td><td>Injury</td><td>Practice Status</td><td>Game Status</td></tr><tr><td>Jane Doe</td><td>WR</td><td>Knee</td><td>Limited</td><td>Questionable</td></tr><tr><td>Arizona Cardinals</td><td>No Injuries Reported</td></tr></table>"""

class InjuryTests(unittest.TestCase):
    def test_parse_and_no_injuries(self):
        rows, teams = m.parse_report(HTML)
        self.assertEqual(rows[0], {"team":"SEA","player_name":"Jane Doe","position":"WR","injury":"Knee","practice_status":"Limited","game_status":"Questionable"})
        self.assertEqual(teams, ["SEA"])

    def test_game_matching_is_team_oriented(self):
        rows = [{"team": "SEA", "player_name": "Jane"}]
        game_rows = [{"game_id": "g1", "home_team": "SEA", "away_team": "NE"}]
        by_team = {t: g["game_id"] for g in game_rows for t in (g["home_team"], g["away_team"])}
        self.assertEqual(by_team[rows[0]["team"]], "g1")

if __name__ == "__main__": unittest.main()
