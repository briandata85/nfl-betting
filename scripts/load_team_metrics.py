"""Calculate completed-game NFL team metrics from nflverse PBP."""

import argparse
import csv
import io
import json
import os
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import Request, build_opener

try:
    import nflreadpy as nfl
except ModuleNotFoundError:  # Allows offline fixture tests before dependencies install.
    nfl = None

try:
    from .load_schedule import NoRedirect, credentials
except ImportError:
    from load_schedule import NoRedirect, credentials

SEASON = 2026
METRIC_FIELDS = (
    "games_played", "offense_epa_per_play", "defense_epa_per_play",
    "offense_success_rate", "defense_success_rate", "pass_epa_per_play",
    "rush_epa_per_play", "explosive_play_rate",
)


class NoCompletedWeek(ValueError):
    """The season has not produced a fully completed week yet."""


def rows_from_text(text, season=SEASON):
    reader = csv.DictReader(io.StringIO(text))
    required = {"season", "week", "game_id", "posteam", "defteam", "epa", "yards_gained"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("PBP is missing required columns.")
    return [row for row in reader if row.get("season") == str(season)]


def has_value(value):
    """Return True for populated scalar values, including numeric zero."""
    return value is not None and str(value).strip() != ""


def normalized_week(value):
    """Normalize CSV/Polars week values to digit strings."""
    if not has_value(value):
        return None
    text = str(value).strip()
    try:
        return str(int(float(text)))
    except ValueError:
        return None


def completed_games(rows):
    """Return game IDs with a final result, grouped by week."""
    games = defaultdict(set)
    for row in rows:
        game_id = row.get("game_id")
        week = normalized_week(row.get("week"))
        if not game_id or week is None:
            continue
        if has_value(row.get("result")):
            games[week].add(game_id)
    return games


def schedule_completion(schedule):
    regular = [row for row in schedule if str(row.get("game_type", "REG")).upper() == "REG"]
    by_week = defaultdict(list)
    for row in regular:
        week = normalized_week(row.get("week"))
        if week is not None:
            by_week[week].append(row)
    complete = {}
    for week, games in by_week.items():
        if games and all(has_value(row.get("result")) for row in games):
            complete[week] = {row.get("game_id") for row in games if row.get("game_id")}
    if not complete:
        raise NoCompletedWeek("No completed schedule week found.")
    return max(map(int, complete)), complete


def latest_completed_week(rows):
    completed = completed_games(rows)
    weeks = sorted(int(week) for week, games in completed.items() if games)
    if not weeks:
        raise NoCompletedWeek("No completed NFL games found.")
    return weeks[-1]


def num(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def binary_flag(value):
    """Normalize nflverse 0/1 values from CSV or Polars rows."""
    if isinstance(value, bool):
        return int(value)
    parsed = num(value)
    if parsed == Decimal(0):
        return 0
    if parsed == Decimal(1):
        return 1
    return None


def is_kneel_or_spike(row):
    return (
        binary_flag(row.get("qb_kneel")) == 1
        or binary_flag(row.get("qb_spike")) == 1
        or row.get("play_type") in {"qb_kneel", "qb_spike"}
    )


def _calculate_completed(rows, completed, through_week, season):
    """Calculate cumulative team metrics using only the supplied completed games."""
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
        off["off"].append(epa)
        deff["def"].append(epa)
        success = binary_flag(row.get("success"))
        if success is not None:
            off.setdefault("off_success", []).append(success)
            deff.setdefault("def_success", []).append(success)
        if not is_kneel_or_spike(row):
            if binary_flag(row.get("pass")) == 1 or row.get("play_type") in {"pass", "qb_scramble"}:
                off["pass"].append(epa)
            if binary_flag(row.get("rush")) == 1 or row.get("play_type") == "run":
                off["rush"].append(epa)
        if yards is not None and yards >= 20:
            off["explosive"] += 1

    def avg(values):
        return float(sum(values, Decimal(0)) / len(values)) if values else None

    result = []
    for team, s in sorted(stats.items()):
        plays = len(s["off"])
        result.append({
            "season": season,
            "through_week": through_week,
            "team": team,
            "games_played": len(s["games"]),
            "offense_epa_per_play": avg(s["off"]),
            "defense_epa_per_play": avg(s["def"]),
            "offense_success_rate": avg(s.get("off_success", [])),
            "defense_success_rate": avg(s.get("def_success", [])),
            "pass_epa_per_play": avg(s["pass"]),
            "rush_epa_per_play": avg(s["rush"]),
            "explosive_play_rate": s["explosive"] / plays if plays else None,
        })
    if not result:
        raise ValueError("No completed team plays found.")
    return result


def calculate(rows, schedule=None, season=SEASON):
    """Calculate the latest fully completed cumulative weekly snapshot."""
    if schedule is None:
        through_week = latest_completed_week(rows)
        complete_by_week = completed_games(rows)
    else:
        through_week, complete_by_week = schedule_completion(schedule)
    completed = {
        game
        for week, games in complete_by_week.items()
        if int(week) <= through_week
        for game in games
    }
    return through_week, _calculate_completed(rows, completed, through_week, season)


def calculate_all_weeks(rows, schedule, season=SEASON):
    """Build one cumulative snapshot per fully completed regular-season week."""
    _, complete_by_week = schedule_completion(schedule)
    output = []
    for through_week in sorted(map(int, complete_by_week)):
        completed = {
            game
            for week, games in complete_by_week.items()
            if int(week) <= through_week
            for game in games
        }
        output.extend(_calculate_completed(rows, completed, through_week, season))
    return output


def load_sources(season=SEASON):
    """Load official nflreadpy Polars frames and convert them to plain rows."""
    if nfl is None:
        raise RuntimeError("nflreadpy is required to load live NFL data")
    schedule = nfl.load_schedules(season)
    schedule_rows = schedule.to_dicts()
    schedule_completion(schedule_rows)
    pbp = nfl.load_pbp(season)
    return pbp.to_dicts(), schedule_rows


def upsert(rows, url, key):
    headers = {"apikey": key, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    request = Request(
        f"{url}/rest/v1/team_metrics?on_conflict=season,through_week,team",
        data=json.dumps(rows, allow_nan=False).encode(),
        headers=headers,
        method="POST",
    )
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        if not 200 <= response.status < 300:
            raise ValueError("Supabase returned a non-success status.")


def safe_error(exc):
    """Return useful diagnostics while removing configured credentials."""
    message = str(exc) or exc.__class__.__name__
    for secret in (os.environ.get("SUPABASE_SECRET_KEY", ""), os.environ.get("SUPABASE_URL", "")):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message.replace("\r", " ").replace("\n", " ")[:500]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, choices=tuple(range(1999, 2027)), default=SEASON)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.csv:
            rows, schedule = rows_from_text(args.csv.read_text(encoding="utf-8-sig"), args.season), None
        else:
            rows, schedule = load_sources(args.season)
        through_week, metrics = calculate(rows, schedule, args.season)
        if not args.dry_run:
            url, key = credentials()
            upsert(metrics, url, key)
        print(json.dumps({"teams_loaded": len(metrics), "through_week": through_week, "dry_run": args.dry_run}))
        return 0
    except NoCompletedWeek:
        print(f"No fully completed {args.season} NFL week yet. Nothing to load.")
        return 0
    except Exception as exc:
        print(f"Team metrics load failed: {safe_error(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
