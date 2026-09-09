"""Calculate completed-game 2026 NFL team metrics from nflverse PBP."""

import argparse
import csv
import gzip
import io
import json
import os
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import Request, build_opener

try:
    from .load_schedule import NoRedirect, credentials
except ImportError:
    from load_schedule import NoRedirect, credentials

SEASON = 2026
PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_2026.csv.gz"
METRIC_FIELDS = (
    "games_played", "offense_epa_per_play", "defense_epa_per_play",
    "offense_success_rate", "defense_success_rate", "pass_epa_per_play",
    "rush_epa_per_play", "explosive_play_rate",
)


class NoCompletedWeek(ValueError):
    """The season has not produced a fully completed week yet."""


def rows_from_text(text):
    reader = csv.DictReader(io.StringIO(text))
    required = {"season", "week", "game_id", "posteam", "defteam", "epa", "yards_gained"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("PBP is missing required columns.")
    return [row for row in reader if row.get("season") == str(SEASON)]


def completed_games(rows):
    """Return game IDs with a final result, grouped by week."""
    games = defaultdict(set)
    for row in rows:
        game_id, week = row.get("game_id"), row.get("week")
        if not game_id or not week or row.get("result", "").strip():
            games[week].add(game_id)
    return games


def latest_completed_week(rows):
    completed = completed_games(rows)
    weeks = sorted(int(week) for week, games in completed.items() if games and week.isdigit())
    if not weeks:
        raise NoCompletedWeek("No completed 2026 games found.")
    return weeks[-1]


def num(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def is_kneel_or_spike(row):
    return row.get("qb_kneel") == "1" or row.get("qb_spike") == "1" or row.get("play_type") in {"qb_kneel", "qb_spike"}


def calculate(rows):
    through_week = latest_completed_week(rows)
    completed = {game for week, games in completed_games(rows).items() if week.isdigit() and int(week) <= through_week for game in games}
    stats = defaultdict(lambda: {"games": set(), "off": [], "def": [], "pass": [], "rush": [], "explosive": 0})
    for row in rows:
        if row.get("game_id") not in completed or not row.get("posteam") or not row.get("defteam"):
            continue
        epa = num(row.get("epa"))
        yards = num(row.get("yards_gained"))
        if epa is None:
            continue
        off, deff = stats[row["posteam"]], stats[row["defteam"]]
        off["games"].add(row["game_id"])
        off["off"].append(epa); deff["def"].append(epa)
        success = row.get("success")
        if success in {"0", "1"}:
            off.setdefault("off_success", []).append(int(success)); deff.setdefault("def_success", []).append(int(success))
        if not is_kneel_or_spike(row):
            if row.get("pass") == "1" or row.get("play_type") in {"pass", "qb_scramble"}:
                off["pass"].append(epa)
            if row.get("rush") == "1" or row.get("play_type") == "run":
                off["rush"].append(epa)
        if yards is not None and yards >= 20:
            off["explosive"] += 1

    def avg(values): return float(sum(values, Decimal(0)) / len(values)) if values else None
    result = []
    for team, s in sorted(stats.items()):
        plays = len(s["off"])
        result.append({"season": SEASON, "through_week": through_week, "team": team,
                       "games_played": len(s["games"]), "offense_epa_per_play": avg(s["off"]),
                       "defense_epa_per_play": avg(s["def"]),
                       "offense_success_rate": avg(s.get("off_success", [])),
                       "defense_success_rate": avg(s.get("def_success", [])),
                       "pass_epa_per_play": avg(s["pass"]), "rush_epa_per_play": avg(s["rush"]),
                       "explosive_play_rate": s["explosive"] / plays if plays else None})
    if not result:
        raise ValueError("No completed team plays found.")
    return through_week, result


def fetch_pbp():
    with build_opener(NoRedirect()).open(Request(PBP_URL), timeout=120) as response:
        return gzip.decompress(response.read()).decode("utf-8-sig")


def upsert(rows, url, key):
    headers = {"apikey": key, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    if not key.startswith("sb_secret_"): headers["Authorization"] = f"Bearer {key}"
    request = Request(f"{url}/rest/v1/team_metrics?on_conflict=season,through_week,team", data=json.dumps(rows, allow_nan=False).encode(), headers=headers, method="POST")
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        if not 200 <= response.status < 300: raise ValueError("Supabase returned a non-success status.")


def safe_error(exc):
    """Return useful diagnostics while removing configured credentials."""
    message = str(exc) or exc.__class__.__name__
    for secret in (os.environ.get("SUPABASE_SECRET_KEY", ""), os.environ.get("SUPABASE_URL", "")):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message.replace("\r", " ").replace("\n", " ")[:500]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.csv: text = args.csv.read_text(encoding="utf-8-sig")
        else: text = fetch_pbp()
        through_week, metrics = calculate(rows_from_text(text))
        if not args.dry_run:
            url, key = credentials(); upsert(metrics, url, key)
        print(json.dumps({"teams_loaded": len(metrics), "through_week": through_week, "dry_run": args.dry_run}))
        return 0
    except NoCompletedWeek:
        print("No fully completed 2026 NFL week yet. Nothing to load.")
        return 0
    except Exception as exc:
        print(f"Team metrics load failed: {safe_error(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
