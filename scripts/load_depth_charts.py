"""Load the latest nflverse depth-chart snapshot into Supabase."""

import argparse
from datetime import datetime, timezone
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, build_opener

try:
    import nflreadpy as nfl
except ModuleNotFoundError:
    nfl = None

try:
    from .load_schedule import NoRedirect, credentials
except ImportError:
    from load_schedule import NoRedirect, credentials


def safe_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def latest_rows(rows, season):
    """Keep only the newest timestamped depth-chart snapshot for a season."""
    filtered = [r for r in rows if safe_int(r.get("season")) == season and r.get("dt")]
    if not filtered:
        raise ValueError(f"No timestamped depth chart rows found for {season}")
    source_dt = max(str(r["dt"]) for r in filtered)
    output = []
    captured = datetime.now(timezone.utc).isoformat()
    for row in filtered:
        if str(row.get("dt")) != source_dt:
            continue
        team = row.get("team")
        player_name = row.get("player_name")
        if not team or not player_name:
            continue
        output.append({
            "season": season,
            "source_dt": source_dt,
            "captured_at": captured,
            "team": str(team),
            "player_name": str(player_name),
            "gsis_id": row.get("gsis_id"),
            "espn_id": row.get("espn_id"),
            "pos_grp": row.get("pos_grp"),
            "pos_name": row.get("pos_name"),
            "pos_abb": row.get("pos_abb"),
            "pos_slot": safe_int(row.get("pos_slot")),
            "pos_rank": safe_int(row.get("pos_rank")),
            "source": "nflverse",
        })
    if not output:
        raise ValueError("Latest depth chart snapshot contained no usable rows")
    return source_dt, output


def load_source(season):
    if nfl is None:
        raise RuntimeError("nflreadpy is required to load live depth charts")
    return nfl.load_depth_charts(season).to_dicts()


def headers_for(key, *, json_body=False):
    headers = {"apikey": key}
    if json_body:
        headers.update({"Content-Type": "application/json", "Prefer": "return=minimal"})
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def post_rows(url, key, rows):
    request = Request(
        f"{url}/rest/v1/depth_chart_history",
        data=json.dumps(rows, allow_nan=False).encode(),
        headers=headers_for(key, json_body=True),
        method="POST",
    )
    with build_opener(NoRedirect()).open(request, timeout=90) as response:
        if not 200 <= response.status < 300:
            raise ValueError("Supabase depth-chart insert failed")


def snapshot_exists(url, key, season, source_dt):
    query = urlencode({
        "select": "id",
        "season": f"eq.{season}",
        "source_dt": f"eq.{source_dt}",
        "limit": "1",
    })
    request = Request(f"{url}/rest/v1/depth_chart_history?{query}", headers=headers_for(key))
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        return bool(json.loads(response.read()))


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
        source_dt, rows = latest_rows(load_source(args.season), args.season)
        skipped = False
        if not args.dry_run:
            url, key = credentials()
            if snapshot_exists(url, key, args.season, source_dt):
                skipped = True
            else:
                post_rows(url, key, rows)
        print(json.dumps({
            "season": args.season,
            "source_dt": source_dt,
            "rows": len(rows),
            "dry_run": args.dry_run,
            "already_loaded": skipped,
        }))
        return 0
    except Exception as exc:
        print(f"Depth chart load failed: {safe_error(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
