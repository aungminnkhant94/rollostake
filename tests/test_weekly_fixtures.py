import unittest
from unittest.mock import patch

from scripts.fetch_weekly_fixtures import fetch_fixtures


def _event(event_id: str, utc_kickoff: str) -> dict:
    return {
        "id": event_id,
        "date": utc_kickoff,
        "competitions": [
            {
                "status": {"type": {"completed": False, "state": "pre"}},
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"displayName": "Arsenal"},
                    },
                    {
                        "homeAway": "away",
                        "team": {"displayName": "Chelsea"},
                    },
                ],
            }
        ],
    }


class WeeklyFixtureWindowTests(unittest.TestCase):
    @patch("scripts.fetch_weekly_fixtures._request_json")
    def test_filters_on_macau_date_after_utc_conversion(self, request_json):
        request_json.return_value = {
            "events": [
                _event("inside", "2026-09-18T15:00:00Z"),
                _event("outside", "2026-09-18T19:00:00Z"),
            ]
        }

        fixtures = fetch_fixtures(
            ["EPL"],
            "2026-09-11",
            "2026-09-18",
            "Asia/Macau",
        )

        self.assertEqual([fixture["match_id"] for fixture in fixtures], ["espn_eng_1_inside"])
        self.assertEqual(fixtures[0]["kickoff"], "2026-09-18 23:00")


if __name__ == "__main__":
    unittest.main()
