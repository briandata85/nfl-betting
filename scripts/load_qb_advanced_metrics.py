"""Load cumulative quarterback pressure/accuracy context from nflverse PFR stats."""

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
import os
from urllib.request import Request, build_opener

try:
    import nflreadpy as nfl
except ModuleNotFoundError:
    nfl = None

try:
    from .load_schedule import NoRedirect, credentials
    from .load_team_metrics import NoCompletedWeek, schedule_completion
except ImportError:
    from load_schedule import NoRedirect, credentials
    from load_team_metrics import NoCompletedWeek, schedule_completion


def num(value):
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def integer(value):
    parsed = num(value)
    return int(parsed) if parsed is not None else None


def pick(row, *names):
    for name in names:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return None


def aggregate(rows, season, through_week):
    """Aggregate weekly QB rows through the specified completed week."""
    grouped = defaultdict(list)
    for row in rows:
        if integer(row.get("season")) != season:
            continue
        week = integer(row.get("week"))
        if week is None or week > through_week:
            continue
        player_id = pick(row, "pfr_player_id", "pfr_id")
        player_name = pick(row, "pfr_player_name", "player", "player_name")
        if not player_id:
            if not player_name or not row.get("team"):
                continue
            player_id = f"name:{row.get('team')}:{player_name}"
        grouped[str(player_id)].append(row)

    output = []
    for player_id, items in grouped.items():
        items.sort(key=lambda r: integer(r.get("week")) or 0)
        latest = items[-1]
        attempts = [num(pick(r, "pass_attempts", "attempts")) for r in items]
        attempt_total = sum((x for x in attempts if x is not None), Decimal(0))

        def sum_field(*names):
            vals = [num(pick(r, *names)) for r in items]
            vals = [v for v in vals if v is not None]
            return int(sum(vals, Decimal(0))) if vals else None

        def weighted_rate(*names):
            vals = []
            weights = []
            for idx, row in enumerate(items):
                value = num(pick(row, *names))
                if value is None:
                    continue
                weight = attempts[idx] if attempts[idx] is not None and attempts[idx] > 0 else Decimal(1)
                vals.append(value * weight)
                weights.append(weight)
            if not weights:
                return None
            return float(sum(vals, Decimal(0)) / sum(weights, Decimal(0)))

        games = {r.get("game_id") for r in items if r.get("game_id")}
        output.append({
            "season": season,
            "through_week": through_week,
            "player_id": player_id,
            "player_name": pick(latest, "pfr_player_name", "player", "player_name"),
            "team": latest.get("team"),
            "games_played": len(games) if games else len(items),
            "pass_attempts": int(attempt_total) if attempt_total else None,
            "times_sacked": sum_field("times_sacked"),
            "times_blitzed": sum_field("times_blitzed"),
            "times_hurried": sum_field("times_hurried"),
            "times_hit": sum_field("times_hit", "def_times_hitqb"),
            "times_pressured": sum_field("times_pressured"),
            "pressure_pct": weighted_rate("times_pressured_pct", "pressure_pct"),
            "bad_throw_pct": weighted_rate("passing_bad_throw_pct", "bad_throw_pct"),
            "drop_pct": weighted_rate("passing_drop_pct", "drop_pct"),
            "source": "nflverse_pfr",
        })
    return output


def load_sources(season):
    if nfl is None:
        raise RuntimeError("nflreadpy is required to load advanced QB metrics")
    schedule = nfl.load_schedules(season).to_dicts()
    through_week, _ = schedule_completion(schedule)
    rows = nfl.load_pfr_advstats(season, stat_type="pass", summary_level="week").to_dicts()
    return rows, through_week


def upsert(rows, url, key):
    if not rows:
        return
    headers = {"apikey": key, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    request = Request(
        f"{url}/rest/v1/qb_advanced_metrics?on_conflict=season,through_week,player_id",
        data=json.dumps(rows, allow_nan=False).encode(),
        headers=headers,
        method="POST",
    )
    with build_opener(NoRedirect()).open(request, timeout=90) as response:
        if not 200 <= response.status < 300:
            raise ValueError("Supabase advanced QB upsert failed")


def safe_error(exc):
    message = str(exc) or exc.__class__.__name__
    for secret in (os.environ.get("SUPABASE_SECRET_KEY", ""), os.environ.get("SUPABASE_URL", "")):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message.replace("\n", " ")[:500]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        rows, through_week = load_sources(args.season)
        metrics = aggregate(rows, args.season, through_week)
        if not args.dry_run:
            url, key = credentials()
            upsert(metrics, url, key)
        print(json.dumps({"season": args.season, "through_week": through_week, "qbs": len(metrics), "dry_run": args.dry_run}))
        return 0
    except NoCompletedWeek:
        print(f"No fully completed {args.season} NFL week yet. Nothing to load.")
        return 0
    except Exception as exc:
        print(f"Advanced QB load failed: {safe_error(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
