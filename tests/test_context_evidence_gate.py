import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import analysis.edge_calculator as edge_module
import models.core as core_module
from analysis.adjustment_layers import (
    AdjustmentLayerEngine,
    EVIDENCE_MISSING,
    EVIDENCE_NO_SIGNAL,
    EVIDENCE_NOT_APPLICABLE,
)
from analysis.edge_calculator import EdgeCalculator


class LayerEvidenceStateTests(unittest.TestCase):
    def test_rolling_blend_accepts_prior_season_carryover(self):
        engine = AdjustmentLayerEngine({"adjustment_layers": {}})
        moves = []

        def stats(_team, _kickoff, limit, days):
            self.assertEqual(limit, 6)
            return {"played": 1 if days == 120 else 6}

        with patch.object(engine, "_team_stats", side_effect=stats), patch.object(
            engine, "_form_lambda", side_effect=[1.5, 1.2]
        ):
            engine._layer_rolling_blend(
                moves, 1.5, 1.2, "Home", "Away", "EPL", "2026-09-01 20:00"
            )

        self.assertFalse(moves[0].active)
        self.assertEqual(moves[0].evidence_state, EVIDENCE_NO_SIGNAL)
        self.assertIn("early-season carryover", moves[0].note)

    def test_finishing_marks_missing_xg_without_forcing_an_adjustment(self):
        engine = AdjustmentLayerEngine({"adjustment_layers": {}})
        moves = []
        with patch.object(engine, "_finishing_multiplier", return_value=(1.0, "")), patch.object(
            engine, "_has_finishing_proxy", return_value=False
        ):
            home_after, away_after = engine._layer_finishing(
                moves, 1.5, 1.2, "Home", "Away", "2026-09-01 20:00"
            )

        self.assertEqual((home_after, away_after), (1.5, 1.2))
        self.assertFalse(moves[0].active)
        self.assertEqual(moves[0].evidence_state, EVIDENCE_MISSING)

    def test_finishing_accepts_populated_goal_history_as_fallback_evidence(self):
        engine = AdjustmentLayerEngine({"adjustment_layers": {}})
        moves = []
        with patch.object(engine, "_finishing_multiplier", return_value=(1.0, "")), patch.object(
            engine, "_has_finishing_proxy", return_value=True
        ):
            engine._layer_finishing(
                moves, 1.5, 1.2, "Home", "Away", "2026-09-01 20:00"
            )

        self.assertFalse(moves[0].active)
        self.assertEqual(moves[0].evidence_state, EVIDENCE_NO_SIGNAL)
        self.assertIn("verified goal-history proxy used", moves[0].note)

    def test_non_derby_is_not_applicable(self):
        engine = AdjustmentLayerEngine({"adjustment_layers": {}})
        moves = []
        engine._layer_derby(moves, 1.5, 1.2, "Chelsea", "Brighton")
        self.assertFalse(moves[0].active)
        self.assertEqual(moves[0].evidence_state, EVIDENCE_NOT_APPLICABLE)


class ContextGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "gate.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE prediction_adjustment_layers (
                id INTEGER PRIMARY KEY,
                match_id TEXT,
                layer_no INTEGER,
                layer_name TEXT,
                evidence_state TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO prediction_adjustment_layers (match_id, layer_no, layer_name, evidence_state) VALUES (?, ?, ?, ?)",
            [
                ("m1", 1, "Rolling blend", "NO_SIGNAL"),
                ("m1", 2, "Elo SoS", "ACTIVE"),
                ("m1", 3, "Finishing quality", "MISSING_DATA"),
                ("m1", 7, "Injuries", "NO_SIGNAL"),
                ("m1", 10, "Rest days", "ACTIVE"),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_goal_market_blocks_on_missing_finishing_context(self):
        with patch.object(edge_module, "DB_PATH", self.db_path):
            calc = EdgeCalculator(context_gate={"enabled": True})
            missing = calc._missing_required_context("m1", "TT")
            self.assertEqual(missing, ("L3 Finishing quality (MISSING_DATA)",))

    def test_non_goal_market_does_not_require_finishing_layer(self):
        with patch.object(edge_module, "DB_PATH", self.db_path):
            calc = EdgeCalculator(context_gate={"enabled": True})
            self.assertEqual(calc._missing_required_context("m1", "1X2"), ())


class MigrationTests(unittest.TestCase):
    def test_init_db_adds_evidence_state_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "schema.db"
            with patch.object(core_module, "DB_PATH", db_path):
                core_module.init_db()
            conn = sqlite3.connect(db_path)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(prediction_adjustment_layers)")}
            conn.close()
            self.assertIn("evidence_state", columns)


if __name__ == "__main__":
    unittest.main()
