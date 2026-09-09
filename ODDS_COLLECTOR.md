# NFL odds collector

Uses the existing `games` and `odds_history` tables. No schema changes or new
Python dependencies. Load the NFL schedule first.

## Run

Set `SPORTSGAMEODDS_API_KEY`, `SUPABASE_URL`, and `SUPABASE_SECRET_KEY` in the
environment. Never place keys in source files or command arguments.

```sh
python -m unittest discover -s tests -v
python scripts/collect_odds.py --dry-run
python scripts/collect_odds.py
```

GitHub: add `SPORTSGAMEODDS_API_KEY` as an Actions repository secret, then run
**Collect NFL Odds** on main. Keep **dry_run** checked first; uncheck it for inserts.
The two Supabase secrets already exist. There is no scheduled trigger.

## Verified API contract (September 9, 2026)

Official references:
- https://sportsgameodds.com/docs/endpoints/getEvents
- https://sportsgameodds.com/docs/data-types/odds
- https://sportsgameodds.com/docs/data-types/markets
- https://sportsgameodds.com/docs/guides/data-batches
- https://sportsgameodds.com/docs/basics/quickstart

GET `https://api.sportsgameodds.com/v2/events` authenticates with the `apiKey`
query parameter, populated from `SPORTSGAMEODDS_API_KEY`. Request URLs are never logged.
The response has `success`, `data` (events), and optional `nextCursor`. Pass the
cursor unchanged on the next otherwise-identical query. A cursor-page 404 means
end of results; other HTTP errors fail without automatic retries.

oddID is `{statID}-{statEntityID}-{periodID}-{betTypeID}-{sideID}`. The collector
requests exactly home/away `points-*-game-sp-*`, home/away `points-*-game-ml-*`,
and `points-all-game-ou-over/under`, using the documented `oddID` query parameter.
It reads `odds[oddID].byBookmaker[bookmakerID]`: `odds` (American price),
`spread` or `overUnder` (line), and `available`. It never substitutes consensus
prices. It ignores alternate arrays, team totals, subperiod markets, and props.

## Safety and usage

- Default next-seven-day window; `--days` accepts 1-14. This intentionally does
  not collect distant futures. One request fetches all requested books/markets
  for up to 100 events (the provider may cap a page lower). Follow all cursors,
  with a default four-request ceiling. Exceeding it fails before inserting.
- No polling, per-book requests, team lookup requests, automatic retries, or
  quota-consuming live test when credentials are absent. Dry runs still consume
  provider quota. A smaller window reduces returned events as well as payload.
- Server filters require NFL, available odds, not started/live/ended/cancelled.
  Client checks repeat these conditions and require both the provider start and
  database kickoff to be in the future when preparing the insert. Missing status
  flags fail closed. An upstream incorrectly labelled event cannot be independently
  detected, but future kickoff checks guard against stale status around start time.
- Exact team IDs or recognized abbreviations (including LAR/LA, JAC/JAX, WSH/WAS)
  plus home/away orientation and kickoff within 15 minutes must yield one game.
  No fuzzy names, date-only matches, or invented game IDs. Refresh the schedule
  after rescheduling. Unknown/ambiguous matches are skipped and counted.
- Every row carries the UTC time that its API page was received, not bookmaker
  last-update time. `market` is spread/moneyline/total; `selection` is
  home/away/over/under. Lines retain their bookmaker sign; moneyline line is null.
  Player columns are omitted (null), `is_live=false`, source is sportsgameodds.
- A single plain INSERT appends the snapshot atomically; existing history is
  never updated or deleted. Each successful rerun creates new history, even when
  prices are unchanged. No retries after uncertain writes, to avoid accidental
  duplicate snapshots. Existing defaults generate row IDs.
- Logs contain counts and HTTP status only, never keys, URLs, or response bodies.
  Unmatched events or invalid quotes cause a nonzero exit after valid rows are
  inserted; read the counts before rerunning. A zero-event result inserts nothing.

Offline fixtures can be used without any API credentials or database access:
`python scripts/collect_odds.py --dry-run --events-json events.json --games-json games.json`.
The events file must be a complete v2 response without a nextCursor, and the games
file a JSON array of game_id, home_team, away_team, kickoff rows.

After a real run, inspect:

```sql
select game_id, captured_at, sportsbook, market, selection, line, odds_american
from odds_history
where source = 'sportsgameodds'
order by captured_at desc
limit 30;
```
