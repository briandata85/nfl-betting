# Prediction results and private dashboard

The prediction-results migration was applied with owner approval on September 10, 2026. NE–SEA final SEA 13, NE 10 was verified against NFLverse and saved; all four historical prediction rows grade as wins. The dashboard selects two positions for that game, totaling $140.31 net profit.

## Updates
The schedule/results workflow runs at minute 17 of every hour. The dashboard polls stored data every minute; source publication and GitHub scheduling can introduce delays.

A separate Codex heartbeat checks predictions every 30 minutes for the earliest unfinished NFL week. It creates preliminary moneyline and spread picks across that full week as usable FanDuel lines and sufficient context become available, reviews official picks 45–90 minutes before kickoff, and advances after the current week finishes. This local automation requires Codex to remain running.

## Accounting
The dashboard uses one $100 position per game and market, regardless of confidence or recommendation. It prefers the newest official prediction, otherwise the newest preliminary prediction. Historical rows remain available for audit and export but are not counted twice. Displayed odds are the odds saved when that prediction was made.

Negative American odds win 10000 / abs(odds); positive odds win the odds amount. Losses lose $100; pushes have $0 net profit. Tied two-way moneylines are pushes under this theoretical rule. Missing scores remain pending. Unsupported selections or invalid odds require review. Cancellations require an explicit reviewed void.

`prediction_results` stores each historical row's latest grade; `prediction_result_history` records changed grades and prediction snapshots. Identical regrading adds no history. Score or prediction corrections regrade automatically. Source and retrieval timestamps are retained. The `bets` table is untouched.

## Access
The migration enables RLS and removes anon/authenticated table, view, and sequence privileges while preserving service-role ingestion and administrative access. The private dashboard uses an authenticated read-only endpoint with credentials stored server-side.

## Validation
Python tests passed, including partial, future, and missing-score preservation cases. An isolated PostgreSQL-compatible engine validated the migration, outcome cases, triggers, payouts, idempotency, correction history, anonymous denial, and service-role reads. Live settlement and dashboard deduplication were verified after activation.
