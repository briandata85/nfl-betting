# Prediction results and private dashboard

Status: prepared and tested; database activation requires explicit approval.

## Activation order
1. Apply `sql/prediction_results.sql` to Supabase after approval.
2. Merge the loader and workflow changes, then run Load 2026 NFL Schedule and Results.
3. Verify NE–SEA final 13–10 from the current nflverse feed, four winning prediction rows, and no new bets.

The workflow retries at minute 17 of every hour. The dashboard polls stored data every minute; source publication and GitHub scheduling can introduce delays.

## Accounting
Each prediction row risks $100, regardless of recommendation or confidence. Preliminary and official are separate rows; selecting both counts both. Backfills are labeled and filterable. Negative American odds win 10000 / abs(odds); positive odds win the odds amount. Losses lose $100. Pushes return the stake ($0 net). Tied two-way moneylines are pushes under this theoretical rule. Missing scores remain pending. Invalid selections/unsupported markets need review. Cancellations require an explicit reviewed void; they are never inferred from missing scores. Missing/invalid odds must be resolved before financial settlement.

`prediction_results` stores the latest grade; `prediction_result_history` records changed grades and the full prediction snapshot. Repeated identical grading does not add history. Changes to saved predictions or final scores regrade automatically. Source and retrieval timestamps are retained. The `bets` table is untouched.

## Access change requiring approval
The migration enables RLS on existing public tables and removes anon/authenticated table/view/sequence privileges. It preserves service-role ingestion and MCP administrative access. Existing clients using public or logged-in Supabase keys directly will need an explicit policy before they can read again. The private dashboard uses a separately authenticated read-only endpoint with its access token stored only server-side.

## Validation
All existing Python tests plus partial-score, future-score, and missing-score preservation cases passed. An isolated PostgreSQL-compatible engine validated the full migration, 10 outcome cases, triggers, payouts, repeat-run idempotency, correction history, anonymous denial, and service-role reads. Live migration has not run.

