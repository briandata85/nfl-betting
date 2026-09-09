"""Append an NFL.com injury-report snapshot to Supabase."""

import argparse
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from urllib.error import HTTPError

try:
    from .load_schedule import NoRedirect, credentials
except ImportError:
    from load_schedule import NoRedirect, credentials

NFL_URL = "https://www.nfl.com/injuries/league/{season}/reg{week}"
TEAM_CODES = {"Arizona Cardinals":"ARI","Atlanta Falcons":"ATL","Baltimore Ravens":"BAL","Buffalo Bills":"BUF","Carolina Panthers":"CAR","Chicago Bears":"CHI","Cincinnati Bengals":"CIN","Cleveland Browns":"CLE","Dallas Cowboys":"DAL","Denver Broncos":"DEN","Detroit Lions":"DET","Green Bay Packers":"GB","Houston Texans":"HOU","Indianapolis Colts":"IND","Jacksonville Jaguars":"JAX","Kansas City Chiefs":"KC","Las Vegas Raiders":"LV","Los Angeles Chargers":"LAC","Los Angeles Rams":"LA","Miami Dolphins":"MIA","Minnesota Vikings":"MIN","New England Patriots":"NE","New Orleans Saints":"NO","New York Giants":"NYG","New York Jets":"NYJ","Philadelphia Eagles":"PHI","Pittsburgh Steelers":"PIT","San Francisco 49ers":"SF","Seattle Seahawks":"SEA","Tampa Bay Buccaneers":"TB","Tennessee Titans":"TEN","Washington Commanders":"WAS"}
TEAM_LABELS = {**{name.lower(): code for name, code in TEAM_CODES.items()}, **{
    label.lower(): code for label, code in {
        "Cardinals":"ARI", "Falcons":"ATL", "Ravens":"BAL", "Bills":"BUF", "Panthers":"CAR", "Bears":"CHI",
        "Bengals":"CIN", "Browns":"CLE", "Cowboys":"DAL", "Broncos":"DEN", "Lions":"DET", "Packers":"GB",
        "Texans":"HOU", "Colts":"IND", "Jaguars":"JAX", "Chiefs":"KC", "Raiders":"LV", "Chargers":"LAC",
        "Rams":"LA", "Dolphins":"MIA", "Vikings":"MIN", "Patriots":"NE", "Saints":"NO", "Giants":"NYG",
        "Jets":"NYJ", "Eagles":"PHI", "Steelers":"PIT", "49ers":"SF", "Seahawks":"SEA", "Buccaneers":"TB",
        "Titans":"TEN", "Commanders":"WAS"}.items()}
}


def clean(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


class Rows(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self.current = None; self.cell = None; self.text = []; self.pending_team = None
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self.current = []; self.row_team = self.pending_team
        if tag in {"td", "th"} and self.current is not None: self.cell = tag; self.text = []
    def handle_data(self, data):
        if self.cell:
            self.text.append(data)
        else:
            label = clean(data).lower()
            if label in TEAM_LABELS: self.pending_team = TEAM_LABELS[label]
            elif label == "no injuries reported" and self.pending_team:
                self.rows.append((self.pending_team, ["No Injuries Reported"]))
    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell:
            self.current.append(clean(" ".join(self.text))); self.cell = None
        if tag == "tr" and self.current is not None:
            if self.current: self.rows.append((self.row_team, self.current))
            self.current = None


def parse_report(html):
    parser = Rows(); parser.feed(html); team = None; records = []; found = set()
    for row_team, cells in parser.rows:
        if row_team: team = row_team; found.add(row_team)
        joined = " ".join(cells)
        if "No Injuries Reported" in joined:
            for label, code in TEAM_LABELS.items():
                if label in joined.lower(): team = code; found.add(code)
            continue
        for label, code in TEAM_LABELS.items():
            if label in joined.lower() and len(cells) <= 2:
                team = code; found.add(code); break
        if team and len(cells) >= 4 and cells[0].lower() not in {"player", "name"}:
            records.append({"team": team, "player_name": cells[0], "position": cells[1],
                            "injury": cells[2], "practice_status": cells[3],
                            "game_status": cells[4] if len(cells) > 4 else None})
    return records, sorted(found | {r["team"] for r in records})


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


def current_week(url, key, season):
    query = urlencode({"select": "week,kickoff", "season": f"eq.{season}", "order": "kickoff"})
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"): headers["Authorization"] = f"Bearer {key}"
    rows = get_json(f"{url}/rest/v1/games?{query}", headers)
    now = datetime.now(timezone.utc)
    candidates = []
    for row in rows:
        try:
            kickoff = datetime.fromisoformat(str(row["kickoff"]).replace("Z", "+00:00"))
            if kickoff.tzinfo is None: kickoff = kickoff.replace(tzinfo=timezone.utc)
            if kickoff >= now - timedelta(days=7): candidates.append((kickoff, int(row["week"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not candidates: raise ValueError("No current NFL week found in Supabase games")
    return min(candidates, key=lambda item: item[0])[1]


def post_rows(url, key, table, rows):
    if not rows: return
    headers = {"apikey": key, "Content-Type":"application/json", "Prefer":"return=minimal"}
    if not key.startswith("sb_secret_"): headers["Authorization"] = f"Bearer {key}"
    request = Request(f"{url}/rest/v1/{table}", data=json.dumps(rows).encode(), headers=headers, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=60) as response:
            if not 200 <= response.status < 300: raise ValueError("Supabase insert failed")
    except HTTPError as exc:
        body = exc.read(16384).decode("utf-8", "replace")
        try:
            detail = json.dumps(json.loads(body), ensure_ascii=True)
        except (ValueError, TypeError):
            detail = "response body was not JSON"
        for secret in (key, os.environ.get("SUPABASE_URL", "")):
            if secret: detail = detail.replace(secret, "[REDACTED]")
        raise RuntimeError(f"Supabase HTTP {exc.code}: {detail}") from exc


def insert(url, key, rows):
    post_rows(url, key, "injury_history", rows)


def insert_report_status(url, key, rows):
    post_rows(url, key, "injury_report_status", rows)


def build_report_status(records, found, game_rows, season, week, captured):
    by_team = {t: g["game_id"] for g in game_rows for t in (g.get("home_team"), g.get("away_team"))}
    counts = {team: 0 for team in found}
    for record in records:
        counts[record["team"]] = counts.get(record["team"], 0) + 1
    return [{
        "season": season,
        "week": week,
        "game_id": by_team.get(team),
        "team": team,
        "players_reported": counts.get(team, 0),
        "report_status": "players_reported" if counts.get(team, 0) else "no_injuries_reported",
        "captured_at": captured,
        "source": "nfl.com",
    } for team in sorted(found) if team in by_team]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--season", type=int, default=2026); parser.add_argument("--week", type=int); parser.add_argument("--auto-week", action="store_true")
    args = parser.parse_args(argv)
    try:
        url, key = credentials()
        if args.auto_week: args.week = current_week(url, key, args.season)
        if args.week is None: parser.error("--week is required unless --auto-week is used")
        request = Request(NFL_URL.format(season=args.season, week=args.week), headers={"User-Agent":"nfl-betting injury loader"})
        with build_opener(NoRedirect()).open(request, timeout=60) as response: html = response.read().decode("utf-8", "replace")
        records, found = parse_report(html)
        if not records and not found:
            if "Practice Status" in html:
                raise ValueError("NFL.com page contains Practice Status but no injury rows or team report states were parsed")
            print(f"NFL.com has not posted an injury report for {args.season} Week {args.week} yet. Nothing to load.")
            return 0
        game_rows = games(url, key, args.season, args.week)
        by_team = {t: g["game_id"] for g in game_rows for t in (g.get("home_team"), g.get("away_team"))}
        captured = datetime.now(timezone.utc).isoformat(); unmatched = sorted({r["team"] for r in records if r["team"] not in by_team})
        rows = [{**r, "season": args.season, "week": args.week, "game_id": by_team.get(r["team"]), "captured_at": captured, "source":"nfl.com"} for r in records if r["team"] in by_team]
        status_rows = build_report_status(records, found, game_rows, args.season, args.week, captured)
        insert(url, key, rows)
        insert_report_status(url, key, status_rows)
        print(json.dumps({"teams_found": len(found), "players_found": len(records), "rows_inserted": len(rows), "report_status_rows": len(status_rows), "unmatched_teams": unmatched}))
        return 0
    except Exception as exc:
        print(f"Injury load failed: {str(exc)[:300]}"); return 1


if __name__ == "__main__": raise SystemExit(main())
