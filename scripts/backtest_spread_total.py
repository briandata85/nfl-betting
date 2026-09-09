"""Walk-forward ATS and total residual models against nflverse closing lines."""

import json
import math
from statistics import mean, pstdev
from urllib.parse import urlencode
from urllib.request import Request, build_opener

try:
    from .load_schedule import NoRedirect, credentials
except ImportError:
    from load_schedule import NoRedirect, credentials

TEST_SEASONS = (2022, 2023, 2024, 2025)

SPREAD_FEATURES = (
    "home_spread_line",
    "net_epa_diff",
    "success_matchup_diff",
    "pass_matchup_diff",
    "rush_matchup_diff",
    "explosive_diff",
)
TOTAL_FEATURES = (
    "closing_total",
    "combined_offense_epa",
    "combined_defense_epa",
    "combined_success_rate",
    "combined_pass_epa",
    "combined_rush_epa",
    "combined_explosive_rate",
)


def spread_features(row):
    return {
        "home_spread_line": float(row["home_spread_line"]),
        "net_epa_diff": float(row["net_epa_diff"]),
        "success_matchup_diff": (
            (float(row["home_offense_success_rate"]) - float(row["away_defense_success_rate"]))
            - (float(row["away_offense_success_rate"]) - float(row["home_defense_success_rate"]))
        ),
        "pass_matchup_diff": (
            (float(row["home_pass_epa_per_play"]) - float(row["away_defense_epa_per_play"]))
            - (float(row["away_pass_epa_per_play"]) - float(row["home_defense_epa_per_play"]))
        ),
        "rush_matchup_diff": (
            (float(row["home_rush_epa_per_play"]) - float(row["away_defense_epa_per_play"]))
            - (float(row["away_rush_epa_per_play"]) - float(row["home_defense_epa_per_play"]))
        ),
        "explosive_diff": float(row["home_explosive_play_rate"]) - float(row["away_explosive_play_rate"]),
    }


def total_features(row):
    return {
        "closing_total": float(row["closing_total"]),
        "combined_offense_epa": float(row["home_offense_epa_per_play"]) + float(row["away_offense_epa_per_play"]),
        "combined_defense_epa": float(row["home_defense_epa_per_play"]) + float(row["away_defense_epa_per_play"]),
        "combined_success_rate": float(row["home_offense_success_rate"]) + float(row["away_offense_success_rate"]),
        "combined_pass_epa": float(row["home_pass_epa_per_play"]) + float(row["away_pass_epa_per_play"]),
        "combined_rush_epa": float(row["home_rush_epa_per_play"]) + float(row["away_rush_epa_per_play"]),
        "combined_explosive_rate": float(row["home_explosive_play_rate"]) + float(row["away_explosive_play_rate"]),
    }


def solve(matrix, vector):
    n = len(vector)
    aug = [list(matrix[i]) + [float(vector[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ValueError("Singular regression matrix")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        divisor = aug[col][col]
        aug[col] = [value / divisor for value in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            if factor:
                aug[row] = [a - factor * b for a, b in zip(aug[row], aug[col])]
    return [aug[i][-1] for i in range(n)]


def fit_ridge(rows, feature_names, feature_fn, target, l2=2.0):
    centers = {}
    scales = {}
    feature_maps = [feature_fn(row) for row in rows]
    for name in feature_names:
        values = [features[name] for features in feature_maps]
        centers[name] = mean(values)
        scales[name] = pstdev(values) or 1.0
    xs = [[1.0] + [(features[name] - centers[name]) / scales[name] for name in feature_names] for features in feature_maps]
    ys = [float(row[target]) for row in rows]
    p = len(xs[0])
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for x, y in zip(xs, ys):
        for i in range(p):
            xty[i] += x[i] * y
            for j in range(p):
                xtx[i][j] += x[i] * x[j]
    for i in range(1, p):
        xtx[i][i] += l2
    return solve(xtx, xty), centers, scales


def predict(row, model, feature_names, feature_fn):
    weights, centers, scales = model
    features = feature_fn(row)
    x = [1.0] + [(features[name] - centers[name]) / scales[name] for name in feature_names]
    return sum(w * value for w, value in zip(weights, x))


def rmse(preds, targets):
    return math.sqrt(mean((p - y) ** 2 for p, y in zip(preds, targets)))


def grade_bets(rows, preds, target_field, threshold, over_under=False):
    bets = wins = losses = pushes = 0
    profit = 0.0
    payout = 100.0 / 110.0
    for row, pred in zip(rows, preds):
        if abs(pred) < threshold:
            continue
        bets += 1
        actual = float(row[target_field])
        if actual == 0:
            pushes += 1
            continue
        positive_side = pred > 0
        won = (actual > 0) == positive_side
        if won:
            wins += 1
            profit += payout
        else:
            losses += 1
            profit -= 1.0
    return {
        "threshold_points": threshold,
        "bets": bets,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "profit_units": round(profit, 3),
        "roi": round(profit / bets, 4) if bets else None,
    }


def evaluate_market(rows, market):
    if market == "spread":
        feature_names, feature_fn, target = SPREAD_FEATURES, spread_features, "home_ats_margin"
        thresholds = (0.5, 1.0, 1.5, 2.0)
    else:
        feature_names, feature_fn, target = TOTAL_FEATURES, total_features, "total_margin"
        thresholds = (0.5, 1.0, 1.5, 2.0)
    seasons = {}
    all_rows = []
    all_preds = []
    for season in TEST_SEASONS:
        train = [row for row in rows if int(row["season"]) < season and row.get(target) is not None]
        test = [row for row in rows if int(row["season"]) == season and row.get(target) is not None]
        if not train or not test:
            continue
        model = fit_ridge(train, feature_names, feature_fn, target)
        preds = [predict(row, model, feature_names, feature_fn) for row in test]
        targets = [float(row[target]) for row in test]
        seasons[str(season)] = {
            "games": len(test),
            "model_rmse": round(rmse(preds, targets), 4),
            "market_zero_residual_rmse": round(rmse([0.0] * len(test), targets), 4),
            "bets": [grade_bets(test, preds, target, t) for t in thresholds],
        }
        all_rows.extend(test)
        all_preds.extend(preds)
    targets = [float(row[target]) for row in all_rows]
    return {
        "walk_forward": seasons,
        "overall": {
            "games": len(all_rows),
            "model_rmse": round(rmse(all_preds, targets), 4),
            "market_zero_residual_rmse": round(rmse([0.0] * len(all_rows), targets), 4),
            "bets": [grade_bets(all_rows, all_preds, target, t) for t in thresholds],
        },
    }


def fetch_rows(url, key):
    fields = [
        "game_id", "season", "week", "home_spread_line", "closing_total", "home_ats_margin", "total_margin",
        "net_epa_diff", "home_offense_epa_per_play", "away_offense_epa_per_play",
        "home_defense_epa_per_play", "away_defense_epa_per_play",
        "home_offense_success_rate", "away_offense_success_rate",
        "home_defense_success_rate", "away_defense_success_rate",
        "home_pass_epa_per_play", "away_pass_epa_per_play",
        "home_rush_epa_per_play", "away_rush_epa_per_play",
        "home_explosive_play_rate", "away_explosive_play_rate",
    ]
    query = urlencode({"select": ",".join(fields), "order": "season.asc,week.asc,game_id.asc"})
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    rows = []
    start = 0
    page_size = 1000
    opener = build_opener(NoRedirect())
    while True:
        request = Request(f"{url}/rest/v1/backtest_games?{query}", headers={**headers, "Range": f"{start}-{start + page_size - 1}"})
        with opener.open(request, timeout=60) as response:
            page = json.loads(response.read().decode("utf-8"))
        rows.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return rows


def run(rows):
    return {
        "spread": evaluate_market(rows, "spread"),
        "total": evaluate_market(rows, "total"),
    }


def main():
    url, key = credentials()
    print(json.dumps(run(fetch_rows(url, key)), sort_keys=True))


if __name__ == "__main__":
    main()
