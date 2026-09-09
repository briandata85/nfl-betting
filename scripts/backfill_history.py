"""Backfill historical regular-season games and weekly team metrics for backtesting."""

import argparse
import json

try:
    import nflreadpy as nfl
except ModuleNotFoundError:
    nfl = None

try:
    from .load_schedule import credentials, integer, numeric, nullable, upsert_games
    from .load_team_metrics import calculate_all_weeks, upsert as upsert_team_metrics
except ImportError:
    from load_schedule import credentials, integer, numeric, nullable, upsert_games
    from load_team_metrics import calculate_all_weeks, upsert as upsert_team_metrics


def schedule_rows(frame_rows, season):
    """Map nflverse schedule rows into the existing games table contract."""
    games = []
    for row in frame_rows:
        if int(row.get("season")) != season or str(row.get("game_type", "REG")).upper() != "REG":
            continue
        game_id = nullable(row.get("game_id"))
        away_team = nullable(row.get("away_team"))
        home_team = nullable(row.get("home_team"))
        if not game_id or not away_team or not home_team:
            raise ValueError("Historical schedule has missing game identifiers or teams.")
        games.append({
            "game_id": game_id,
            "season": season,
            "week": integer(row.get("week")),
            "game_date": nullable(row.get("gameday")),
            "kickoff": None,
            "away_team": away_team,
            "home_team": home_team,
            "stadium": nullable(row.get("stadium")),
            "roof": nullable(row.get("roof")),
            "surface": nullable(row.get("surface")),
            "game_type": "REG",
            "away_score": integer(row.get("away_score")),
            "home_score": integer(row.get("home_score")),
            "result": integer(row.get("result")),
            "total": integer(row.get("total")),
            "nflverse_away_moneyline": integer(row.get("away_moneyline")),
            "nflverse_home_moneyline": integer(row.get("home_moneyline")),
            "nflverse_spread_line": numeric(row.get("spread_line")),
            "nflverse_total_line": numeric(row.get("total_line")),
        })
    if not games:
        raise ValueError(f"No regular-season games found for {season}.")
    return games


def load_season(season):
    if nfl is None:
        raise RuntimeError("nflreadpy is required for historical backfill")
    schedule = nfl.load_schedules(season).to_dicts()
    pbp = nfl.load_pbp(season).to_dicts()
    games = schedule_rows(schedule, season)
    metrics = calculate_all_weeks(pbp, schedule, season)
    return games, metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-season", type=int, default=2019)
    parser.add_argument("--end-season", type=int, default=2025)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.start_season < 1999 or args.end_season > 2025 or args.start_season > args.end_season:
        parser.error("Historical backfill range must be between 1999 and 2025.")

    url = key = None
    if not args.dry_run:
        url, key = credentials()

    summary = []
    for season in range(args.start_season, args.end_season + 1):
        games, metrics = load_season(season)
        if not args.dry_run:
            upsert_games(games, url, key)
            upsert_team_metrics(metrics, url, key)
        item = {
            "season": season,
            "games": len(games),
            "metric_rows": len(metrics),
            "through_weeks": sorted({row["through_week"] for row in metrics}),
        }
        summary.append(item)
        print(json.dumps(item))

    print(json.dumps({"dry_run": args.dry_run, "seasons": len(summary)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
