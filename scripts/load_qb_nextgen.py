"""Load cumulative NFL Next Gen Stats passing context into Supabase."""

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


def summary_row(row, season, through_week):
    return {
        "season": season,
        "through_week": through_week,
        "player_id": str(row.get("player_gsis_id")),
        "player_name": row.get("player_display_name"),
        "team": row.get("team_abbr"),
        "attempts": integer(row.get("attempts")),
        "avg_time_to_throw": float(num(row.get("avg_time_to_throw"))) if num(row.get("avg_time_to_throw")) is not None else None,
        "avg_intended_air_yards": float(num(row.get("avg_intended_air_yards"))) if num(row.get("avg_intended_air_yards")) is not None else None,
        "aggressiveness": float(num(row.get("aggressiveness"))) if num(row.get("aggressiveness")) is not None else None,
        "completion_percentage": float(num(row.get("completion_percentage"))) if num(row.get("completion_percentage")) is not None else None,
        "expected_completion_percentage": float(num(row.get("expected_completion_percentage"))) if num(row.get("expected_completion_percentage")) is not None else None,
        "cpoe": float(num(row.get("completion_percentage_above_expectation"))) if num(row.get("completion_percentage_above_expectation")) is not None else None,
        "passer_rating": float(num(row.get("passer_rating"))) if num(row.get("passer_rating")) is not None else None,
        "source": "nflverse_ngs",
    }


def aggregate(rows, season, through_week):
    """Use the NGS regular-season summary when available, else attempt-weight weekly rows."""
    regular = [
        r for r in rows
        if integer(r.get("season")) == season
        and str(r.get("season_type", "REG")).upper() == "REG"
        and r.get("player_gsis_id")
    ]
    summaries = [r for r in regular if integer(r.get("week")) == 0]
    if summaries:
        return [summary_row(r, season, through_week) for r in summaries]

    grouped = defaultdict(list)
    for row in regular:
        week = integer(row.get("week"))
        if week is None or week < 1 or week > through_week:
            continue
        grouped[str(row["player_gsis_id"])].append(row)

    fields = (
        "avg_time_to_throw",
        "avg_intended_air_yards",
        "aggressiveness",
        "completion_percentage",
        "expected_completion_percentage",
        "completion_percentage_above_expectation",
        "passer_rating",
    )
    output = []
    for player_id, items in grouped.items():
        attempts = [num(r.get("attempts")) or Decimal(0) for r in items]
        total_attempts = sum(attempts, Decimal(0))
        latest = max(items, key=lambda r: integer(r.get("week")) or 0)
        values = {}
        for field in fields:
            weighted_sum = Decimal(0)
            weight_sum = Decimal(0)
            for row, weight in zip(items, attempts):
                value = num(row.get(field))
                if value is None:
                    continue
                effective_weight = weight if weight > 0 else Decimal(1)
                weighted_sum += value * effective_weight
                weight_sum += effective_weight
            values[field] = float(weighted_sum / weight_sum) if weight_sum else None
        output.append({
            "season": season,
            "through_week": through_week,
            "player_id": player_id,
            "player_name": latest.get("player_display_name"),
            "team": latest.get("team_abbr"),
            "attempts": int(total_attempts) if total_attempts else None,
            "avg_time_to_throw": values["avg_time_to_throw"],
            "avg_intended_air_yards": values["avg_intended_air_yards"],
            "aggressiveness": values["aggressiveness"],
            "completion_percentage": values["completion_percentage"],
            "expected_completion_percentage": values["expected_completion_percentage"],
            "cpoe": values["completion_percentage_above_expectation"],
            "passer_rating": values["passer_rating"],
            "source": "nflverse_ngs",
        })
    return output


def load_sources(season):
    if nfl is None:
        raise RuntimeError("nflreadpy is required to load Next Gen Stats")
    schedule = nfl.load_schedules(season).to_dicts()
    through_week, _ = schedule_completion(schedule)
    rows = nfl.load_nextgen_stats(season, stat_type="passing").to_dicts()
    return rows, through_week


def upsert(rows, url, key):
    if not rows:
        return
    headers = {"apikey": key, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    request = Request(
        f"{url}/rest/v1/qb_nextgen_metrics?on_conflict=season,through_week,player_id",
        data=json.dumps(rows, allow_nan=False).encode(),
        headers=headers,
        method="POST",
    )
    with build_opener(NoRedirect()).open(request, timeout=90) as response:
        if not 200 <= response.status < 300:
            raise ValueError("Supabase Next Gen QB upsert failed")


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
        print(f"Next Gen QB load failed: {safe_error(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
