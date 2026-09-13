import unittest

from analysis.adjustment_layers import AdjustmentLayerEngine
from scripts.fetch_squad_depth import _position_bucket


class SquadDepthTests(unittest.TestCase):
    def test_position_bucket_mapping(self):
        self.assertEqual(_position_bucket("Goalkeeper", "G"), "goalkeepers")
        self.assertEqual(_position_bucket("Defender", "D"), "defenders")
        self.assertEqual(_position_bucket("Midfielder", "M"), "midfielders")
        self.assertEqual(_position_bucket("Forward", "F"), "forwards")

    def test_roster_position_classifies_plain_injury_row(self):
        engine = AdjustmentLayerEngine({"adjustment_layers": {}})
        self.assertTrue(engine._news_attack_hit({"player": "Example", "position": "Forward"}))
        self.assertFalse(engine._news_defense_hit({"player": "Example", "position": "Forward"}))
        self.assertTrue(engine._news_defense_hit({"player": "Example", "position": "Defender"}))

    def test_player_key_matches_accented_roster_names(self):
        engine = AdjustmentLayerEngine({"adjustment_layers": {}})
        self.assertEqual(engine._person_key("João Gomes"), engine._person_key("Joao Gomes"))
        self.assertEqual(engine._person_key("Jurrien Timber"), engine._person_key("Jurriën Timber"))


if __name__ == "__main__":
    unittest.main()
