"""Append pregame NFL main-market snapshots from SportsGameOdds v2."""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import re
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import quote, quote_plus, urlencode
from urllib.request import Request, build_opener

try:
    from .load_schedule import NoRedirect, credentials
except ImportError:
    from load_schedule import NoRedirect, credentials


EVENTS_URL = "https://api.sportsgameodds.com/v2/events"
MARKETS = {
    "points-home-game-sp-home": ("spread", "home", "spread"),
    "points-away-game-sp-away": ("spread", "away", "spread"),
    "points-home-game-ml-home": ("moneyline", "home", None),
    "points-away-game-ml-away": ("moneyline", "away", None),
    "points-all-game-ou-over": ("total", "over", "overUnder"),
    "points-all-game-ou-under": ("total", "under", "overUnder"),
}
# Exact provider IDs avoid fuzzy name collisions (especially LA/NY teams).
TEAM_IDS = dict(zip((name + "_NFL" for name in (
    "ARIZONA_CARDINALS", "ATLANTA_FALCONS", "BALTIMORE_RAVENS", "BUFFALO_BILLS",
    "CAROLINA_PANTHERS", "CHICAGO_BEARS", "CINCINNATI_BENGALS", "CLEVELAND_BROWNS",
    "DALLAS_COWBOYS", "DENVER_BRONCOS", "DETROIT_LIONS", "GREEN_BAY_PACKERS",
    "HOUSTON_TEXANS", "INDIANAPOLIS_COLTS", "JACKSONVILLE_JAGUARS", "KANSAS_CITY_CHIEFS",
    "LAS_VEGAS_RAIDERS", "LOS_ANGELES_CHARGERS", "LOS_ANGELES_RAMS", "MIAMI_DOLPHINS",
    "MINNESOTA_VIKINGS", "NEW_ENGLAND_PATRIOTS", "NEW_ORLEANS_SAINTS", "NEW_YORK_GIANTS",
    "NEW_YORK_JETS", "PHILADELPHIA_EAGLES", "PITTSBURGH_STEELERS", "SAN_FRANCISCO_49ERS",
    "SEATTLE_SEAHAWKS", "TAMPA_BAY_BUCCANEERS", "TENNESSEE_TITANS", "WASHINGTON_COMMANDERS",
)), "ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LV LAC LA MIA MIN NE NO NYG NYJ PHI PIT SF SEA TB TEN WAS".split()))
ALIASES = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


def request_json(url, headers, payload=None):
    request = Request(url, headers=headers, method="GET" if payload is None else "POST",
                      data=None if payload is None else json.dumps(payload, allow_nan=False).encode())
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        if not 200 <= response.status < 300:
            raise ValueError("Non-success response")
        data = response.read()
        return json.loads(data) if data else None


def supabase_headers(key):
    headers = {"apikey": key, "Content-Type": "application/json"}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def fetch_games(url, key, start, end):
    games = []
    # Offset pagination also handles deployments with a row limit below 1000.
    while True:
        query = urlencode({"select": "game_id,home_team,away_team,kickoff", "order": "game_id",
                           "and": f"(kickoff.gte.{start.isoformat()},kickoff.lte.{end.isoformat()})",
                           "limit": 1000, "offset": len(games)})
        page = request_json(f"{url}/rest/v1/games?{query}", supabase_headers(key))
        if not isinstance(page, list):
            raise ValueError("Invalid games response")
        if not page:
            return games
        if {g["game_id"] for g in games} & {g["game_id"] for g in page}:
            raise ValueError("Repeated games page")
        games.extend(page)


def report_provider_error(exc, key):
    """Log only JSON error/message fields, never headers or raw responses."""
    fallback = "JSON error message unavailable (body omitted)."
    try:
        body = exc.read(16385)
        if len(body) > 16384:
            raise ValueError("Oversized error body")
        data = json.loads(body)
        # Allowlist message fields; reflected request/headers metadata is omitted.
        def messages(value):
            if isinstance(value, str):
                # A provider may echo request headers inside its message.
                if re.search(r"headers?|authorization|x-api-key|apikey", value, re.I):
                    return "[REDACTED header-containing message]"
                secrets = [key] + [os.environ.get(name, "").strip() for name in (
                    "SPORTSGAMEODDS_API_KEY", "SUPABASE_SECRET_KEY", "SUPABASE_URL")]
                for secret in sorted(set(secrets), key=len, reverse=True):
                    if secret:
                        for variant in (secret, quote(secret, safe=""), quote_plus(secret)):
                            value = value.replace(variant, "[REDACTED]")
                return value
            if isinstance(value, dict):
                return {field: messages(value[field]) for field in ("error", "message")
                        if field in value}
            return None

        safe = messages(data) if isinstance(data, dict) else None
        detail = json.dumps(safe, ensure_ascii=True) if safe else fallback
    except Exception:
        detail = fallback
    # JSON escaping keeps untrusted newlines/control characters on one log line.
    print(f"SportsGameOdds HTTP {exc.code}: {detail}", file=sys.stderr)


def fetch_events(key, start, end, max_pages=4, limit=100):
    params = {"apiKey": key, "leagueID": "NFL", "oddsAvailable": "true", "started": "false",
              "live": "false", "ended": "false", "cancelled": "false",
              "oddID": ",".join(MARKETS), "includeAltLines": "false",
              "includeOpposingOdds": "false", "startsAfter": start.isoformat(),
              "startsBefore": end.isoformat(), "limit": limit}
    cursor = None
    cursors = set()
    events = []
    event_ids = set()
    for _ in range(max_pages):
        query = dict(params)
        if cursor:
            query["cursor"] = cursor
        try:
            page = request_json(f"{EVENTS_URL}?{urlencode(query)}", {})
        except HTTPError as exc:
            if exc.code == 404 and cursor:
                return events
            report_provider_error(exc, key)
            raise
        captured = utcnow()
        if not isinstance(page, dict) or page.get("success") is not True or not isinstance(page.get("data"), list):
            raise ValueError("Invalid provider response")
        for event in page["data"]:
            event_id = event["eventID"]
            if event_id not in event_ids:
                events.append((event, captured))
                event_ids.add(event_id)
        cursor = page.get("nextCursor")
        if not cursor:
            return events
        if not isinstance(cursor, str) or cursor in cursors:
            raise ValueError("Repeated or invalid cursor")
        cursors.add(cursor)
    raise ValueError("Page budget exhausted; no snapshot will be inserted")


def team_code(team):
    code = TEAM_IDS.get(team.get("teamID"))
    short = team.get("names", {}).get("short", "").upper()
    short = ALIASES.get(short, short)
    # Accept only a recognized exact abbreviation; reject conflicting identities.
    if code and short in TEAM_IDS.values() and code != short:
        return None
    return code or (short if short in TEAM_IDS.values() else None)


def match_game(event, games):
    home = team_code(event["teams"]["home"])
    away = team_code(event["teams"]["away"])
    if not home or not away or home == away:
        return None
    starts = timestamp(event["status"]["startsAt"])
    matches = [g for g in games
               if ALIASES.get(g["home_team"], g["home_team"]) == home
               and ALIASES.get(g["away_team"], g["away_team"]) == away
               and g.get("kickoff")
               and abs(timestamp(g["kickoff"]) - starts) <= timedelta(minutes=15)]
    return matches[0] if len(matches) == 1 else None


def pregame(event, now):
    status = event.get("status", {})
    return (event.get("leagueID") == "NFL" and status.get("oddsAvailable") is True
            and all(status.get(flag) is False for flag in ("started", "live", "ended", "cancelled"))
            and status.get("completed") is not True
            and timestamp(status["startsAt"]) > now)


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("Missing numeric value")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite numeric value")
    return result


def snapshots(events, games, now):
    rows = []
    counts = {"excluded_events": 0, "unmatched_events": 0, "invalid_quotes": 0}
    seen = set()
    for event, captured in events:
        if not pregame(event, max(now, captured)):
            counts["excluded_events"] += 1
            continue
        game = match_game(event, games)
        if game is None:
            counts["unmatched_events"] += 1
            continue
        # Both sources must still show a future kickoff.
        if timestamp(game["kickoff"]) <= max(now, captured):
            counts["excluded_events"] += 1
            continue
        for odd_id, (market, selection, line_field) in MARKETS.items():
            odd = event.get("odds", {}).get(odd_id)
            if not odd or any(odd.get(flag) is True for flag in ("started", "ended", "cancelled")) or odd.get("playerID"):
                continue
            if odd.get("oddID", odd_id) != odd_id:
                raise ValueError("Conflicting oddID")
            for book, quote in odd.get("byBookmaker", {}).items():
                if quote.get("available") is not True:
                    continue
                try:
                    price = number(quote.get("odds"))
                    if price != price.to_integral_value() or abs(price) < 100:
                        raise ValueError("Invalid American odds")
                    line = number(quote.get(line_field)) if line_field else None
                    if line_field == "overUnder" and line < 0:
                        raise ValueError("Negative total")
                except (ValueError, InvalidOperation):
                    counts["invalid_quotes"] += 1
                    continue
                identity = (game["game_id"], book, market, selection)
                if identity in seen:
                    raise ValueError("Conflicting duplicate selection in snapshot")
                seen.add(identity)
                rows.append({"game_id": game["game_id"], "captured_at": captured.isoformat(),
                             "sportsbook": book, "market": market, "selection": selection,
                             "line": str(line) if line is not None else None,
                             "odds_american": int(price), "is_live": False,
                             "source": "sportsgameodds"})
    return rows, counts


def insert_history(url, key, rows):
    if rows:
        headers = supabase_headers(key)
        headers["Prefer"] = "return=minimal"
        # A single INSERT is atomic. Never upsert, update, delete, or retry writes.
        request_json(f"{url}/rest/v1/odds_history", headers, rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--events-json", type=Path, help="Offline v2 response fixture (dry-run only)")
    parser.add_argument("--games-json", type=Path, help="Offline games rows (dry-run only)")
    args = parser.parse_args(argv)
    if not (1 <= args.days <= 14 and 1 <= args.max_pages <= 10 and 1 <= args.limit <= 100):
        parser.error("days must be 1-14, max-pages 1-10, limit 1-100")
    if (args.events_json or args.games_json) and not args.dry_run:
        parser.error("Offline files require --dry-run")
    try:
        now = utcnow()
        end = now + timedelta(days=args.days)
        key = os.environ.get("SPORTSGAMEODDS_API_KEY", "").strip()
        if not args.events_json and not key:
            raise ValueError("Missing API key")
        if args.games_json:
            games = json.loads(args.games_json.read_text(encoding="utf-8"))
        else:
            url, db_key = credentials()
            games = fetch_games(url, db_key, now - timedelta(minutes=15), end + timedelta(minutes=15))
        if not games:
            raise ValueError("No scheduled games; load schedule before spending API quota")
        if args.events_json:
            page = json.loads(args.events_json.read_text(encoding="utf-8"))
            if page.get("success") is not True or page.get("nextCursor"):
                raise ValueError("Offline fixture must be a complete successful response")
            events = [(event, now) for event in page["data"]]
        else:
            events = fetch_events(key, now, end, args.max_pages, args.limit)
        rows, counts = snapshots(events, games, utcnow())
        if not args.dry_run:
            insert_history(url, db_key, rows)
        print(json.dumps({"dry_run": args.dry_run, "events": len(events),
                          "rows": len(rows), **counts}))
        # Make matching/data issues visible in Actions rather than silently green.
        return 1 if counts["unmatched_events"] or counts["invalid_quotes"] else 0
    except HTTPError as exc:
        print(f"Collector failed (HTTP {exc.code}); no automatic retry.", file=sys.stderr)
    except Exception:
        print("Collector failed. Check credentials, schedule, schema, and API/page limits.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
