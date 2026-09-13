import math
import unittest

from analysis.adjustment_layers import AdjustmentLayerEngine
from models.dixon_coles import DixonColesModel


class BTTSProbabilityTests(unittest.TestCase):
    def test_btts_prices_unconditional_event_in_both_production_paths(self):
        for home, away in [(0.3, 0.3), (1.143, 2.007), (1.541, 1.037)]:
            for rho in (0.0, -0.13):
                with self.subTest(home=home, away=away, rho=rho):
                    # Independent Poisson identity, with the DC adjustment to 1-1.
                    expected = ((1 - math.exp(-home)) * (1 - math.exp(-away))
                                - rho * home * away * math.exp(-home - away))
                    model = DixonColesModel()
                    model.avg_goals_home, model.avg_goals_away = home, away
                    model.rho = rho
                    engine = AdjustmentLayerEngine.__new__(AdjustmentLayerEngine)
                    engine.rho = rho
                    for actual in (model.predict('Home', 'Away')['prob_btts_yes'],
                                   engine._probabilities(home, away)['prob_btts_yes']):
                        self.assertAlmostEqual(actual, expected, delta=0.0006)

    def test_saved_card_regressions_fail_existing_low_risk_gates(self):
        engine = AdjustmentLayerEngine.__new__(AdjustmentLayerEngine)
        engine.rho = -0.13
        valencia = engine._probabilities(1.143, 2.007)['prob_btts_yes']
        espanyol = engine._probabilities(1.541, 1.037)['prob_btts_yes']
        self.assertLess(valencia * 1.7857 - 1, 0.10)
        self.assertLess(espanyol, 0.55)


if __name__ == '__main__':
    unittest.main()
