import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis.adjustment_layers import AdjustmentLayerEngine
from scrapers.browser_news_scraper import ManualJsonSource, TeamNewsDB
from utils.player_news import injury_news, player_key


class PlayerNewsTests(unittest.TestCase):
    def test_alias_does_not_double_count_and_newer_recovery_wins(self):
        rows = [dict(team='Brighton', player='Yankuba Minteh', status='injured', observed_at='2026-08-28'),
                dict(team='Brighton', player='Yankuba Minteh Moat', status='injured', observed_at='2026-09-02')]
        self.assertEqual(len(injury_news(rows)), 1)
        rows.append(dict(team='Brighton', player='Yankuba Minteh',status='available', observed_at='2026-09-04'))
        self.assertEqual(injury_news(rows), [])
        self.assertNotEqual(player_key('Team','John Smith'), player_key('Team','Adam Smith'))
        self.assertEqual(player_key('Tottenham','Sávio'), player_key('Tottenham','Savio Moreira de Oliveira'))

    def test_manual_refresh_preserves_evidence_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'news.json'
            path.write_text('[{"player":"Old","team":"Brighton","source":"report_2026-08-01"}]')
            item = ManualJsonSource(path).fetch()[0]
            self.assertTrue(item['observed_at'].startswith('2026-08-01'))
            db_path = Path(tmp) / 'news.db'
            TeamNewsDB(db_path).save([item, dict(team='Another team', player='New',status='injured',fetched_at='2026-09-05')])
            engine = AdjustmentLayerEngine({'adjustment_layers': {}})
            with patch('analysis.adjustment_layers.DB_PATH', db_path):
                self.assertFalse(engine._team_news_feed_fresh('2026-09-06 20:00', team='Brighton'))
                self.assertFalse(engine._team_news_feed_fresh('2026-09-06 20:00', team='Missing team'))
                self.assertTrue(engine._team_news_feed_fresh('2026-09-06 20:00', team='Another team'))


if __name__ == '__main__':
    unittest.main()
