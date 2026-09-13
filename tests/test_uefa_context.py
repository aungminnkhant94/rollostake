import unittest

from scripts.import_uefa_context import _clock_minute, _event_row


class UefaContextTests(unittest.TestCase):
    def test_event_row_uses_macau_kickoff_and_completed_score(self):
        event = {
            "id": "123",
            "date": "2026-09-10T19:00:00Z",
            "competitions": [
                {
                    "status": {"type": {"completed": True, "name": "STATUS_FULL_TIME"}},
                    "competitors": [
                        {
                            "homeAway": "home",
                            "score": "4",
                            "team": {"displayName": "Manchester United"},
                        },
                        {
                            "homeAway": "away",
                            "score": "0",
                            "team": {"displayName": "Sabah FK"},
                        },
                    ],
                }
            ],
        }

        row = _event_row(event, "uefa.champions", "Asia/Macau")

        self.assertEqual(row["match_id"], "espn_uefa_champions_123")
        self.assertEqual(row["home_team"], "Man United")
        self.assertEqual(row["kickoff"], "2026-09-11 03:00")
        self.assertEqual((row["home_goals"], row["away_goals"]), (4, 0))
        self.assertTrue(row["completed"])

    def test_clock_minute_handles_added_time(self):
        self.assertEqual(_clock_minute({"clock": {"value": 5580, "displayValue": "90'+3'"}}), 93)


if __name__ == "__main__":
    unittest.main()
