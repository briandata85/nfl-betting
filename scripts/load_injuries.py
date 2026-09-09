"""Append an NFL.com injury-report snapshot to Supabase."""

import argparse
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
from urllib.parse import urlencode
from urllib.request import Request, build_opener

try:
    from .load_schedule import NoRedirect, credentials
except ImportError:
    from load_schedule import NoRedirect, credentials

NFL_URL = "https://www.nfl.com/injuries/league/{season}/reg{week}"
TEAM_CODES = {"Arizona Cardinals":"ARI","Atlanta Falcons":"ATL","Baltimore Ravens":"BAL","Buffalo Bills":"BUF","Carolina Panthers":"CAR","Chicago Bears":"CHI","Cincinnati Bengals":"CIN","Cleveland Browns":"CLE","Dallas Cowboys":"DAL","Denver Broncos":"DEN","Detroit Lions":"DET","Green Bay Packers":"GB","Houston Texans":"HOU","Indianapolis Colts":"IND","Jacksonville Jaguars":"JAX","Kansas City Chiefs":"KC","Las Vegas Raiders":"LV","Los Angeles Chargers":"LAC","Los Angeles Rams":"LA","Miami Dolphins":"MIA","Minnesota Vikings":"MIN","New England Patriots":"NE","New Orleans Saints":"NO","New York Giants":"NYG","New York Jets":"NYJ","Philadelphia Eagles":"PHI","Pittsburgh Steelers":"PIT","San Francisco 49ers":"SF","Seattle Seahawks":"SEA","Tampa Bay Buccaneers":"TB","Tennessee Titans":"TEN","Washington Commanders":"WAS"}


def clean(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


class Rows(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self.current = None; self.cell = None; self.text = []
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self.current = []
        if tag in {"td", "th"} and self.current is not None: self.cell = tag; self.text = []
    def handle_data(self, data):
        if self.cell: self.text.append(data)
    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell:
            self.current.append(clean(" ".join(self.text))); self.cell = None
        if tag == "tr" and self.current is not None:
            if self.current: self.rows.append(self.current)
            self.current = None


def parse_report(html):
    parser = Rows(); parser.feed(html); team = None; records = []; found = set()
    for cells in parser.rows:
        joined = " ".join(cells)
        if "No Injuries Reported" in joined: continue
        for full, code in TEAM_CODES.items():
            if full.lower() in joined.lower() and len(cells) <= 2:
                team = code; found.add(code); break
        if team and len(cells) >= 4 and cells[0].lower() not in {"player", "name"}:
            records.append({"team": team, "player_name": cells[0], "position": cells[1],
                            "injury": cells[2], "practice_status": cells[3],
                            "game_status": cells[4] if len(cells) > 4 else None})
    return records, sorted(found)


def get_json(url, headers):
    request = Request(url, headers=headers)
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        if not 200 <= response.status < 300: raise ValueError("HTTP request failed")
        return json.loads(response.read())


def games(url, key, season, week):
    query = urlencode({"select":"game_id,home_team,away_team", "season":f"eq.{season}", "week":f"eq.{week}"})
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"): headers["Authorization"] = f"Bearer {key}"
    return get_json(f"{url}/rest/v1/games?{query}", headers)


def insert(url, key, rows):
    if not rows: return
    headers = {"apikey": key, "Content-Type":"application/json", "Prefer":"return=minimal"}
    if not key.startswith("sb_secret_"): headers["Authorization"] = f"Bearer {key}"
    request = Request(f"{url}/rest/v1/injury_history", data=json.dumps(rows).encode(), headers=headers, method="POST")
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        if not 200 <= response.status < 300: raise ValueError("Supabase insert failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--season", type=int, default=2026); parser.add_argument("--week", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        url, key = credentials()
        html = get_json("data:application/json,{}", {}) if False else None
        request = Request(NFL_URL.format(season=args.season, week=args.week), headers={"User-Agent":"nfl-betting injury loader"})
        with build_opener(NoRedirect()).open(request, timeout=60) as response: html = response.read().decode("utf-8", "replace")
        records, found = parse_report(html); game_rows = games(url, key, args.season, args.week)
        by_team = {t: g["game_id"] for g in game_rows for t in (g.get("home_team"), g.get("away_team"))}
        captured = datetime.now(timezone.utc).isoformat(); unmatched = sorted({r["team"] for r in records if r["team"] not in by_team})
        rows = [{**r, "game_id": by_team.get(r["team"]), "captured_at": captured, "source":"nfl.com"} for r in records if r["team"] in by_team]
        insert(url, key, rows)
        print(json.dumps({"teams_found": len(found), "players_found": len(records), "rows_inserted": len(rows), "unmatched_teams": unmatched}))
        return 0
    except Exception as exc:
        print(f"Injury load failed: {str(exc)[:300]}"); return 1


if __name__ == "__main__": raise SystemExit(main())
