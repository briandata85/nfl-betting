"""Load aggregated 2025 regular-season player metrics from nflreadpy."""

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, build_opener

try:
    import nflreadpy as nfl
except ModuleNotFoundError:
    nfl = None

try:
    from .load_schedule import NoRedirect, credentials
except ImportError:
    from load_schedule import NoRedirect, credentials

SEASON = 2025
THROUGH_WEEK = 18
SUM_FIELDS = ("games_played", "passing_epa", "carries", "rushing_yards", "rushing_epa",
              "targets", "receptions", "receiving_yards", "receiving_epa")
FIELDS = ("season", "through_week", "player_id", "player_name", "team", "position",
          *SUM_FIELDS, "cpoe", "target_share", "rush_share")


def value(row, *names, default=None):
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return default


def number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def regular_rows(rows):
    result = []
    for row in rows:
        season_type = str(value(row, "season_type", "season_type_id", default="REG")).upper()
        if season_type not in {"REG", "REGULAR", "REGULAR_SEASON"}:
            continue
        week = int(number(value(row, "week", default=0)))
        if 1 <= week <= THROUGH_WEEK:
            result.append(row)
    return result


def calculate(rows, season=SEASON, through_week=THROUGH_WEEK):
    grouped = {}
    for row in regular_rows(rows):
        player_id = str(value(row, "player_id", "playerid", default="")).strip()
        team = str(value(row, "recent_team", "team", "posteam", default="")).strip()
        if not player_id or not team:
            continue
        key = (player_id, team)
        item = grouped.setdefault(key, {field: 0.0 for field in SUM_FIELDS} |
                                    {"cpoe_sum": 0.0, "cpoe_count": 0,
                                     "player_name": value(row, "player_name", "name", default=""),
                                     "position": value(row, "position", default=""), "weeks": set()})
        item["weeks"].add(value(row, "week"))
        for field in SUM_FIELDS:
            aliases = {"games_played": ("games", "games_played"), "rushing_yards": ("rushing_yards", "rushing_yards_total"),
                       "receiving_yards": ("receiving_yards", "receiving_yards_total")}.get(field, (field,))
            item[field] += number(value(row, *aliases))
        cpoe = value(row, "cpoe")
        if cpoe not in (None, ""):
            item["cpoe_sum"] += number(cpoe); item["cpoe_count"] += 1
    team_totals = defaultdict(lambda: {"targets": 0.0, "carries": 0.0})
    for (_, team), item in grouped.items():
        team_totals[team]["targets"] += item["targets"]
        team_totals[team]["carries"] += item["carries"]
    output = []
    for (player_id, team), item in sorted(grouped.items()):
        output.append({"season": season, "through_week": through_week, "player_id": player_id,
                       "player_name": item["player_name"], "team": team, "position": item["position"],
                       **{field: int(item[field]) if field in {"games_played", "carries", "rushing_yards", "targets", "receptions", "receiving_yards"} else item[field] for field in SUM_FIELDS},
                       "cpoe": item["cpoe_sum"] / item["cpoe_count"] if item["cpoe_count"] else None,
                       "target_share": item["targets"] / team_totals[team]["targets"] if team_totals[team]["targets"] else 0.0,
                       "rush_share": item["carries"] / team_totals[team]["carries"] if team_totals[team]["carries"] else 0.0})
    if not output:
        raise ValueError("No regular-season player rows found.")
    return output


def load_rows():
    if nfl is None:
        raise RuntimeError("nflreadpy is required to load live player data")
    return nfl.load_player_stats(SEASON, summary_level="week").to_dicts()


def upsert(rows, url, key):
    headers = {"apikey": key, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    if not key.startswith("sb_secret_"): headers["Authorization"] = f"Bearer {key}"
    request = Request(f"{url}/rest/v1/player_metrics?on_conflict=season,through_week,player_id", data=json.dumps(rows, allow_nan=False).encode(), headers=headers, method="POST")
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        if not 200 <= response.status < 300: raise ValueError("Supabase returned a non-success status.")


def safe_error(exc):
    message = str(exc) or exc.__class__.__name__
    for secret in (os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SECRET_KEY", "")):
        if secret: message = message.replace(secret, "[REDACTED]")
    return message.replace("\r", " ").replace("\n", " ")[:500]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    try:
        rows = json.loads(args.json.read_text(encoding="utf-8")) if args.json else load_rows()
        metrics = calculate(rows)
        if not args.dry_run:
            url, key = credentials(); upsert(metrics, url, key)
        print(json.dumps({"players_loaded": len(metrics), "season": SEASON, "through_week": THROUGH_WEEK, "dry_run": args.dry_run}))
        return 0
    except Exception as exc:
        print(f"Player metrics load failed: {safe_error(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
