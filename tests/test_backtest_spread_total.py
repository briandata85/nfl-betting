import unittest
from scripts import backtest_spread_total as m


class SpreadTotalBacktestTests(unittest.TestCase):
    def row(self, **changes):
        base = {
            "season": 2022, "week": 2, "game_id": "g",
            "home_spread_line": -3.5, "closing_total": 45.5,
            "home_ats_margin": 4.0, "total_margin": -3.0,
            "net_epa_diff": 0.1,
            "home_offense_epa_per_play": 0.12, "away_offense_epa_per_play": 0.03,
            "home_defense_epa_per_play": -0.02, "away_defense_epa_per_play": 0.04,
            "home_offense_success_rate": 0.48, "away_offense_success_rate": 0.44,
            "home_defense_success_rate": 0.42, "away_defense_success_rate": 0.47,
            "home_pass_epa_per_play": 0.17, "away_pass_epa_per_play": 0.08,
            "home_rush_epa_per_play": 0.01, "away_rush_epa_per_play": -0.02,
            "home_explosive_play_rate": 0.12, "away_explosive_play_rate": 0.09,
        }
        base.update(changes)
        return base

    def test_feature_maps(self):
        self.assertAlmostEqual(m.spread_features(self.row())["explosive_diff"], 0.03)
        self.assertAlmostEqual(m.total_features(self.row())["combined_offense_epa"], 0.15)

    def test_ridge_fit_predicts_numeric(self):
        rows = [self.row(game_id=f"g{i}", home_ats_margin=float(i % 5 - 2), net_epa_diff=(i - 10) / 100) for i in range(20)]
        model = m.fit_ridge(rows, m.SPREAD_FEATURES, m.spread_features, "home_ats_margin")
        self.assertIsInstance(m.predict(rows[0], model, m.SPREAD_FEATURES, m.spread_features), float)

    def test_bet_grading_assumes_minus_110(self):
        result = m.grade_bets([self.row(home_ats_margin=2)], [1.5], "home_ats_margin", 1.0)
        self.assertEqual(result["wins"], 1)
        self.assertAlmostEqual(result["profit_units"], 0.909, places=3)


if __name__ == "__main__":
    unittest.main()
