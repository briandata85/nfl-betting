"""Walk-forward NFL moneyline model backtest against closing market prices.

Uses only pregame team metrics and the no-vig closing market probability from
Supabase `backtest_games`. Each test season is trained only on prior seasons.
"""

import json
import math
import os
from statistics import mean, pstdev
from urllib.parse import urlencode
from urllib.request import Request, build_opener

try:
    from .load_schedule import NoRedirect, credentials
except ImportError:
    from load_schedule import NoRedirect, credentials

FEATURES = (
    "market_logit",
    "net_epa_diff",
    "success_matchup_diff",
    "pass_matchup_diff",
    "rush_matchup_diff",
    "explosive_diff",
)
TEST_SEASONS = (2022, 2023, 2024, 2025)


def clamp(p, lo=1e-6, hi=1 - 1e-6):
    return min(max(float(p), lo), hi)


def american_payout(odds):
    odds = float(odds)
    if odds > 0:
        return odds / 100.0
    if odds < 0:
        return 100.0 / abs(odds)
    raise ValueError("American odds cannot be zero")


def feature_row(row):
    market = clamp(row["market_home_win_prob_no_vig"])
    return {
        "market_logit": math.log(market / (1.0 - market)),
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


def standardizer(rows):
    centers = {}
    scales = {}
    for feature in FEATURES[1:]:
        values = [feature_row(row)[feature] for row in rows]
        centers[feature] = mean(values)
        scales[feature] = pstdev(values) or 1.0
    return centers, scales


def vector(row, centers, scales):
    features = feature_row(row)
    values = [1.0, features["market_logit"]]
    for feature in FEATURES[1:]:
        values.append((features[feature] - centers[feature]) / scales[feature])
    return values


def sigmoid(z):
    if z >= 0:
        exp = math.exp(-z)
        return 1.0 / (1.0 + exp)
    exp = math.exp(z)
    return exp / (1.0 + exp)


def fit_logistic(rows, iterations=2500, learning_rate=0.04, l2=0.08):
    centers, scales = standardizer(rows)
    xs = [vector(row, centers, scales) for row in rows]
    ys = [1.0 if float(row["home_margin"]) > 0 else 0.0 for row in rows]
    weights = [0.0] * len(xs[0])
    n = len(xs)
    for _ in range(iterations):
        gradient = [0.0] * len(weights)
        for x, y in zip(xs, ys):
            error = sigmoid(sum(w * value for w, value in zip(weights, x))) - y
            for i, value in enumerate(x):
                gradient[i] += error * value
        for i in range(len(weights)):
            penalty = 0.0 if i == 0 else l2 * weights[i]
            weights[i] -= learning_rate * (gradient[i] / n + penalty / n)
    return weights, centers, scales


def predict(row, model):
    weights, centers, scales = model
    x = vector(row, centers, scales)
    return sigmoid(sum(w * value for w, value in zip(weights, x)))


def log_loss(probs, outcomes):
    return -mean(y * math.log(clamp(p)) + (1 - y) * math.log(clamp(1 - p)) for p, y in zip(probs, outcomes))


def brier(probs, outcomes):
    return mean((p - y) ** 2 for p, y in zip(probs, outcomes))


def roi_for_threshold(rows, probs, threshold):
    profit = 0.0
    bets = 0
    wins = 0
    losses = 0
    pushes = 0
    for row, home_p in zip(rows, probs):
        away_p = 1.0 - home_p
        home_odds = int(row["home_moneyline"])
        away_odds = int(row["away_moneyline"])
        home_ev = home_p * american_payout(home_odds) - away_p
        away_ev = away_p * american_payout(away_odds) - home_p
        if max(home_ev, away_ev) < threshold:
            continue
        side = "home" if home_ev >= away_ev else "away"
        odds = home_odds if side == "home" else away_odds
        margin = float(row["home_margin"])
        bets += 1
        if margin == 0:
            pushes += 1
            continue
        won = (side == "home" and margin > 0) or (side == "away" and margin < 0)
        if won:
            profit += american_payout(odds)
            wins += 1
        else:
            profit -= 1.0
            losses += 1
    return {
        "threshold": threshold,
        "bets": bets,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "profit_units": round(profit, 3),
        "roi": round(profit / bets, 4) if bets else None,
    }


def evaluate(train, test):
    train = [row for row in train if float(row["home_margin"]) != 0]
    scored = [row for row in test if row.get("home_moneyline") and row.get("away_moneyline")]
    model = fit_logistic(train)
    probs = [predict(row, model) for row in scored]
    outcomes = [1.0 if float(row["home_margin"]) > 0 else 0.0 for row in scored]
    market = [float(row["market_home_win_prob_no_vig"]) for row in scored]
    return {
        "games": len(scored),
        "model_brier": round(brier(probs, outcomes), 6),
        "market_brier": round(brier(market, outcomes), 6),
        "model_log_loss": round(log_loss(probs, outcomes), 6),
        "market_log_loss": round(log_loss(market, outcomes), 6),
        "bets": [roi_for_threshold(scored, probs, threshold) for threshold in (0.02, 0.03, 0.05)],
    }


def fetch_rows(url, key):
    fields = [
        "game_id", "season", "week", "home_margin", "home_moneyline", "away_moneyline",
        "market_home_win_prob_no_vig", "net_epa_diff",
        "home_offense_success_rate", "away_offense_success_rate",
        "home_defense_success_rate", "away_defense_success_rate",
        "home_pass_epa_per_play", "away_pass_epa_per_play",
        "home_rush_epa_per_play", "away_rush_epa_per_play",
        "home_defense_epa_per_play", "away_defense_epa_per_play",
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
    rows = [row for row in rows if row.get("market_home_win_prob_no_vig") is not None]
    seasons = {}
    all_test_rows = []
    all_probs = []
    for season in TEST_SEASONS:
        train = [row for row in rows if int(row["season"]) < season]
        test = [row for row in rows if int(row["season"]) == season]
        if not train or not test:
            continue
        result = evaluate(train, test)
        seasons[str(season)] = result
        model = fit_logistic([row for row in train if float(row["home_margin"]) != 0])
        scored = [row for row in test if row.get("home_moneyline") and row.get("away_moneyline")]
        all_test_rows.extend(scored)
        all_probs.extend(predict(row, model) for row in scored)
    if not all_test_rows:
        raise ValueError("No walk-forward test seasons were available")
    outcomes = [1.0 if float(row["home_margin"]) > 0 else 0.0 for row in all_test_rows]
    market = [float(row["market_home_win_prob_no_vig"]) for row in all_test_rows]
    overall = {
        "games": len(all_test_rows),
        "model_brier": round(brier(all_probs, outcomes), 6),
        "market_brier": round(brier(market, outcomes), 6),
        "model_log_loss": round(log_loss(all_probs, outcomes), 6),
        "market_log_loss": round(log_loss(market, outcomes), 6),
        "bets": [roi_for_threshold(all_test_rows, all_probs, threshold) for threshold in (0.02, 0.03, 0.05)],
    }
    return {"model": "market_plus_team_metrics_v1", "walk_forward": seasons, "overall": overall}


def main():
    url, key = credentials()
    rows = fetch_rows(url, key)
    result = run(rows)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
