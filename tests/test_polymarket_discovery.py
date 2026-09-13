import unittest

from scripts.scrape_polymarket_full import _is_parent_match_slug, event_match_metadata
from utils.team_normalizer import normalize_team_name


class PolymarketDiscoveryTests(unittest.TestCase):
    def test_parent_match_slug_ends_at_date(self):
        self.assertTrue(_is_parent_match_slug("epl-ips-liv-2026-09-04"))

    def test_child_market_events_are_not_matches(self):
        self.assertFalse(_is_parent_match_slug("epl-ips-liv-2026-09-04-more-markets"))
        self.assertFalse(_is_parent_match_slug("epl-ips-liv-2026-09-04-second-half-result"))
        self.assertFalse(_is_parent_match_slug("epl-ips-liv-2026-09-04-player-props"))

    def test_gamma_end_date_is_used_as_kickoff(self):
        meta = event_match_metadata(
            {
                "title": "Toulouse FC vs. Lille OSC",
                "startDate": "2026-08-21T04:00:13Z",
                "endDate": "2026-09-03T18:45:00Z",
            }
        )
        self.assertEqual(meta["home_team"], "Toulouse FC")
        self.assertEqual(meta["away_team"], "Lille OSC")
        self.assertEqual(meta["kickoff"], "2026-09-03T18:45:00Z")

    def test_current_polymarket_team_aliases_resolve(self):
        expected = {
            "Frosinone Calcio": "Frosinone",
            "ES Troyes AC": "Troyes",
            "SV 07 Elversberg": "SV Elversberg",
            "Real Racing Club": "Racing Santander",
            "AC Monza": "Monza",
        }
        self.assertEqual(
            {name: normalize_team_name(name) for name in expected},
            expected,
        )


if __name__ == "__main__":
    unittest.main()
