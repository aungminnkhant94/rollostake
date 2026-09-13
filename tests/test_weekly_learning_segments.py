import unittest

from analysis.edge_calculator import EdgeCalculator, Pick, RangeConfig


class MarketLineLearningTests(unittest.TestCase):
    def setUp(self):
        self.calc = EdgeCalculator(context_gate={"enabled": False})

    def _pick(self, market: str, selection: str) -> Pick:
        return Pick(
            match_id="test-match",
            home_team="Home",
            away_team="Away",
            league="EPL",
            kickoff="2026-09-10 20:00",
            selection=selection,
            market=market,
            model_prob=0.60,
            book_prob=0.50,
            edge_pct=0.10,
            odds=2.00,
        )

    def test_market_line_keeps_team_total_separate_from_match_total(self):
        self.assertEqual(
            self.calc._market_line_segment_from_values("TT", "Chelsea U1.5"),
            "TT:goal-line-1.5",
        )
        self.assertEqual(
            self.calc._market_line_segment_from_values("OU", "Under 1.5"),
            "OU:goal-line-1.5",
        )

    def test_learned_low_risk_team_total_under_1_5_trap_is_hard_blocked(self):
        traps = {"D": {("market_line", "TT:goal-line-1.5")}}
        self.assertTrue(
            self.calc._is_hard_loss_trap(
                self._pick("TT", "Chelsea U1.5"), "D", traps
            )
        )

    def test_team_total_trap_does_not_block_match_total_under_1_5(self):
        traps = {"D": {("market_line", "TT:goal-line-1.5")}}
        self.assertFalse(
            self.calc._is_hard_loss_trap(
                self._pick("OU", "Under 1.5"), "D", traps
            )
        )

    def test_team_total_under_is_not_blocked_without_learned_trap(self):
        self.assertFalse(
            self.calc._is_hard_loss_trap(
                self._pick("TT", "Chelsea U1.5"), "D", {"D": set()}
            )
        )

    def test_low_risk_goal_pick_with_multiple_context_contradictions_is_blocked(self):
        pick = self._pick("OU", "Under 2.5")
        pick.reasoning = (
            "H2H downgrades lower scoring. "
            "Attack and defence news downgrades lower scoring."
        )
        self.assertTrue(self.calc._is_hard_loss_trap(pick, "D", {"D": set()}))

    def test_one_support_one_downgrade_is_not_a_context_veto(self):
        pick = self._pick("OU", "Under 2.5")
        pick.reasoning = (
            "H2H supports lower scoring. "
            "Attack and defence news downgrades lower scoring."
        )
        self.assertFalse(self.calc._is_hard_loss_trap(pick, "D", {"D": set()}))

    def test_negative_overconfident_market_gets_downside_calibration(self):
        rows = [
            {
                "range_code": "D",
                "market": "BTTS",
                "model_prob": 0.56,
                "result": "win" if index < 5 else "loss",
                "stake": 10,
                "pnl": 8 if index < 5 else -10,
            }
            for index in range(14)
        ]
        info = self.calc._market_reliability_from_rows(rows)["D"]["BTTS"]
        self.assertEqual(info["decisions"], 14)
        self.assertAlmostEqual(info["penalty"], 0.08)

    def test_profitable_market_is_never_penalized(self):
        rows = [
            {
                "range_code": "D",
                "market": "OU",
                "model_prob": 0.58,
                "result": "win" if index < 8 else "loss",
                "stake": 10,
                "pnl": 10 if index < 8 else -10,
            }
            for index in range(10)
        ]
        reliability = self.calc._market_reliability_from_rows(rows)
        self.assertNotIn("OU", reliability.get("D", {}))

    def test_reliability_penalty_recalculates_probability_edge_and_quality(self):
        pick = self._pick("BTTS", "BTTS Yes")
        calibrated = self.calc._apply_settled_reliability(
            pick,
            "D",
            {
                "D": {
                    "BTTS": {
                        "penalty": 0.08,
                        "decisions": 14,
                        "wins": 5,
                        "losses": 9,
                    }
                }
            },
        )
        self.assertEqual(calibrated.model_prob, 0.52)
        self.assertEqual(calibrated.edge_pct, 4.0)
        self.assertEqual(calibrated.quality, "SKIP")
        self.assertEqual(pick.model_prob, 0.60)

    def test_soft_loss_trap_is_not_reintroduced_as_filler(self):
        config = RangeConfig("D", "Low Risk", 100, 10, 1.70, 2.70, 10, 0.10, 0.55)
        calc = EdgeCalculator(
            use_ranges=True,
            range_configs={"D": config},
            context_gate={"enabled": False},
        )
        pick = self._pick("OU", "Over 2.5")
        pick.model_prob = 0.65
        pick.edge_pct = 30.0
        pick.quality = "KEEP"
        calc.generate_picks = lambda league=None, min_edge=0.0: [pick]
        calc._range_bank_state = lambda code, cfg: {"stake_slots": 10}
        calc._learned_performance_adjustments = lambda: {"D": {}}
        calc._settled_market_reliability = lambda: {}
        calc._loss_trap_segments = lambda: {"D": {("quality", "KEEP")}}
        self.assertEqual(calc.generate_range_picks(), [])


if __name__ == "__main__":
    unittest.main()
