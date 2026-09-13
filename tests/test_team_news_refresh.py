import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scrapers.browser_news_scraper import TeamNewsDB, BetinfScraper


class TeamNewsRefreshTests(unittest.TestCase):
    def test_current_betinf_heading_and_table_without_explicit_tbody(self):
        document = '<h3 id="Inter">Inter</h3><table><tr><td>4.9</td><td>Player One <span>(F)</span></td><td>2</td><td>0-0</td><td>Knee</td><td>?</td></tr></table><h3>General news</h3>'
        rows = BetinfScraper.parse_html(document)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['team'],rows[0]['player'],rows[0]['status']), ('Inter Milan','Player One','doubtful'))
        self.assertIn('forward',rows[0]['reason'])

    def test_betinf_uses_public_page_when_browser_is_unavailable(self):
        document = '<h3 id="Inter">Inter</h3><table><tr><td>4.9</td><td>Player One <span>(F)</span></td><td>2</td><td>0-0</td><td>Knee</td><td>?</td></tr></table>'
        scraper = BetinfScraper(leagues=['SerieA'])
        with patch.object(scraper, '_navigate', return_value=False), patch.object(
            scraper, '_fetch_http', return_value=document
        ):
            rows = scraper.fetch()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['team'], 'Inter Milan')

    def test_replace_current_does_not_accumulate_old_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "news.db"
            db = TeamNewsDB(db_path)
            db.save(
                [{"team": "Old Team", "player": "Old Player", "status": "injured"}]
            )
            db.save(
                [{"team": "New Team", "player": "New Player", "status": "injured"}],
                replace_current=True,
            )
            conn = sqlite3.connect(db_path)
            rows = conn.execute("SELECT team, player FROM team_news").fetchall()
            conn.close()
            self.assertEqual(rows, [("New Team", "New Player")])


if __name__ == "__main__":
    unittest.main()
