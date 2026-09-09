"""Load the 2026 nflverse schedule/results into Supabase using only the standard library."""

import argparse
import csv
from datetime import date, datetime, time, timezone
import io
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from zoneinfo import ZoneInfo


SCHEDULE_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
)
SEASON = 2026
FIELDS = {
    "game_id", "season", "week", "gameday", "gametime", "away_team",
    "home_team", "stadium", "roof", "surface",
}


def nullable(value):
    value = str(value or "").strip()
    return None if value.upper() in {"", "NA", "N/A", "NULL", "NAN", "TBD"} else value


def numeric(value):
    value = nullable(value)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid numeric schedule value: {value}") from exc


def integer(value):
    value = numeric(value)
    if value is None:
        return None
    if not value.is_integer():
        raise ValueError("Expected an integer schedule value.")
    return int(value)


def parse_schedule(text):
    reader = csv.DictReader(io.StringIO(text))
    if not FIELDS.issubset(reader.fieldnames or []):
        raise ValueError("Schedule is missing required columns.")
    games = []
    seen = set()
    for row in reader:
        if row["season"] != str(SEASON):
            continue
        game = {field: nullable(row[field]) for field in (
            "game_id", "away_team", "home_team", "stadium", "roof", "surface"
        )}
        if not all(game[field] for field in ("game_id", "away_team", "home_team")):
            raise ValueError("Schedule has missing game identifiers or teams.")
        if game["game_id"] in seen:
            raise ValueError("Schedule contains duplicate game IDs.")
        seen.add(game["game_id"])
        gameday = nullable(row["gameday"])
        gametime = nullable(row["gametime"])
        game.update(
            season=SEASON,
            week=int(row["week"]),
            game_date=date.fromisoformat(gameday).isoformat() if gameday else None,
            kickoff=(
                datetime.combine(date.fromisoformat(gameday), time.fromisoformat(gametime))
                .replace(tzinfo=ZoneInfo("America/New_York"))
                .astimezone(timezone.utc).isoformat()
                if gameday and gametime else None
            ),
            game_type=nullable(row.get("game_type")),
            away_score=integer(row.get("away_score")),
            home_score=integer(row.get("home_score")),
            result=integer(row.get("result")),
            total=integer(row.get("total")),
            nflverse_away_moneyline=integer(row.get("away_moneyline")),
            nflverse_home_moneyline=integer(row.get("home_moneyline")),
            nflverse_spread_line=numeric(row.get("spread_line")),
            nflverse_total_line=numeric(row.get("total_line")),
        )
        if game["week"] < 1:
            raise ValueError("Schedule has an invalid week.")
        games.append(game)
    if not games:
        raise ValueError("No 2026 games found; refusing to report a successful load.")
    return games


class NoRedirect(HTTPRedirectHandler):
    """Do not forward Supabase credentials to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def credentials():
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set.")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise ValueError("SUPABASE_URL must be an HTTPS project origin.")
    return url, key


def upsert_games(games, url, key):
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    # Legacy service_role JWTs need Authorization; new sb_secret_ keys do not.
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    request = Request(
        f"{url}/rest/v1/games?on_conflict=game_id",
        data=json.dumps(games, allow_nan=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    # One request makes the season upsert atomic in PostgREST.
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        if not 200 <= response.status < 300:
            raise ValueError("Supabase returned a non-success status.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing to Supabase")
    parser.add_argument("--csv", type=Path, help="Read a local nflverse CSV for offline testing")
    args = parser.parse_args(argv)
    try:
        if not args.dry_run:
            url, key = credentials()
        if args.csv:
            text = args.csv.read_text(encoding="utf-8-sig")
        else:
            with urlopen(SCHEDULE_URL, timeout=60) as response:
                text = response.read().decode("utf-8-sig")
        games = parse_schedule(text)
        if args.dry_run:
            print(f"Validated {len(games)} games for {SEASON}; no database writes.")
        else:
            upsert_games(games, url, key)
            completed = sum(game["home_score"] is not None and game["away_score"] is not None for game in games)
            print(f"Upserted {len(games)} games for {SEASON}; {completed} have final scores.")
        return 0
    except HTTPError as exc:
        print(f"Schedule load failed (HTTP {exc.code}).", file=sys.stderr)
    except Exception:
        # URLs, keys, server responses, and exception text must not enter logs.
        print("Schedule load failed. Check configuration, source data, and games schema.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
