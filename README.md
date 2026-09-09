# NFL betting data

## Load the 2026 schedule

The loader downloads the CSV from the current nflverse schedules release:
https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv

Source verified September 9, 2026 against the current
[nflreadpy load_schedules implementation](https://nflreadpy.nflverse.com/api/load_functions/#nflreadpy.load_schedules),
which uses `nflverse-data` / `schedules/games`. No archived `nfl_data_py` package is used.
Python 3.11+ is required. The loader uses the standard library; only Windows needs
the `tzdata` dependency because it normally lacks a system IANA timezone database.

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/load_schedule.py --dry-run
```

For offline validation, add `--csv path/to/games.csv` to the dry-run command.
To load Supabase, set `SUPABASE_URL` and `SUPABASE_SECRET_KEY` in your environment,
then run `python scripts/load_schedule.py`. Credentials are never hardcoded or logged.
New `sb_secret_` keys and legacy service-role JWTs are supported.

Alternatively, on GitHub open **Actions > Load 2026 NFL Schedule > Run workflow**.
The workflow runs manually only and reads the existing Actions secrets with those
same names. It runs unit tests before loading.

## Mapping and upsert

| Supabase games column | nflverse source / conversion |
| --- | --- |
| game_id | game_id |
| season | season, filtered to 2026 (not calendar year) |
| week | week as integer |
| game_date | gameday as ISO date |
| kickoff | gameday + gametime in America/New_York, converted to UTC ISO timestamptz |
| away_team | away_team |
| home_team | home_team |
| stadium | stadium |
| roof | roof |
| surface | surface |

The [nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_schedules.html)
defines gametime as Eastern time even for games played elsewhere. Daylight saving
is handled by `zoneinfo`; missing dates or times produce a null kickoff.
Missing optional values become SQL nulls. All available game types in season 2026
are included, including postseason rows if/when published.

The existing `games.game_id` primary key supports a single atomic PostgREST upsert
using `on_conflict=game_id` and `resolution=merge-duplicates`. Reruns update these
ten columns without duplicating games. `created_at` is omitted so its database
default applies to new rows and existing timestamps remain unchanged. Rows absent
from the source are not deleted. Empty seasons, malformed data, duplicate source
IDs, download failures, and database failures exit nonzero. Error logs omit URLs,
credentials, and response bodies.
