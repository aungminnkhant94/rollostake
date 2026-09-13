import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.match_resolver import resolve_match_id


class MatchResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "matches.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE matches (
                match_id TEXT PRIMARY KEY,
                home_team TEXT,
                away_team TEXT,
                kickoff TEXT,
                status TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO matches VALUES (?, ?, ?, ?, ?)",
            [
                ("old", "Valencia", "Barcelona", "2025-01-01 20:00", "completed"),
                ("current", "Valencia", "Barcelona", "2026-09-06 22:15", "scheduled"),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_known_completed_id_is_not_remapped_to_current_fixture(self):
        row = {"match_id": "old", "home_team": "Valencia", "away_team": "Barcelona"}
        with patch("utils.match_resolver.DB_PATH", self.db_path):
            resolved = resolve_match_id(row, statuses=("scheduled", "stale"))
        self.assertEqual(resolved, "")

    def test_unknown_id_can_fall_back_to_current_fixture(self):
        row = {"match_id": "unknown", "home_team": "Valencia", "away_team": "Barcelona"}
        with patch("utils.match_resolver.DB_PATH", self.db_path):
            resolved = resolve_match_id(row, statuses=("scheduled", "stale"))
        self.assertEqual(resolved, "current")


if __name__ == "__main__":
    unittest.main()
