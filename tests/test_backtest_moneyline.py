import unittest
from scripts import backtest_moneyline as m


class MoneylineBacktestTests(unittest.TestCase):
    def row(self, **changes):
        base = {
            "season": 2022,
            "week": 2,
            "game_id": "g",
            "home_margin": 7,
            "home_moneyline": -150,
            "away_moneyline": 130,
            "market_home_win_prob_no_vig": 0.60,
            "net_epa_diff": 0.10,
            "home_offense_success_rate": 0.48,
            "away_offense_success_rate": 0.43,
            "home_defense_success_rate": 0.42,
            "away_defense_success_rate": 0.47,
            "home_pass_epa_per_play": 0.15,
            "away_pass_epa_per_play": 0.05,
            "home_rush_epa_per_play": 0.02,
            "away_rush_epa_per_play": -0.01,
            "home_defense_epa_per_play": -0.04,
            "away_defense_epa_per_play": 0.03,
            "home_explosive_play_rate": 0.12,
            "away_explosive_play_rate": 0.09,
        }
        base.update(changes)
        return base

    def test_american_payout(self):
        self.assertAlmostEqual(m.american_payout(-200), 0.5)
        self.assertAlmostEqual(m.american_payout(150), 1.5)
        with self.assertRaises(ValueError):
            m.american_payout(0)

    def test_feature_row_uses_pregame_inputs(self):
        features = m.feature_row(self.row())
        self.assertAlmostEqual(features["net_epa_diff"], 0.10)
        self.assertAlmostEqual(features["explosive_diff"], 0.03)
        self.assertGreater(features["market_logit"], 0)

    def test_roi_selects_positive_ev_side(self):
        rows = [self.row(home_margin=3, home_moneyline=100, away_moneyline=-120)]
        result = m.roi_for_threshold(rows, [0.60], 0.02)
        self.assertEqual(result["bets"], 1)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["profit_units"], 1.0)

    def test_logistic_fit_returns_probability(self):
        rows = []
        for i in range(24):
            rows.append(self.row(
                season=2019 + i // 8,
                game_id=f"g{i}",
                home_margin=7 if i % 2 == 0 else -3,
                market_home_win_prob_no_vig=0.62 if i % 2 == 0 else 0.42,
                net_epa_diff=0.15 if i % 2 == 0 else -0.10,
            ))
        model = m.fit_logistic(rows, iterations=300)
        p = m.predict(self.row(), model)
        self.assertGreater(p, 0)
        self.assertLess(p, 1)


if __name__ == "__main__":
    unittest.main()
