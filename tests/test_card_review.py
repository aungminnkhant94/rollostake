import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from analysis.edge_calculator import EdgeCalculator
from dashboard.generator import DashboardGenerator
from scripts.rebuild_card import rebuild_card, _fresh_timestamp


class CardReviewTests(unittest.TestCase):
    def test_pending_records_are_never_superseded_by_a_refresh(self):
        conn = sqlite3.connect(':memory:')
        conn.executescript('CREATE TABLE matches(match_id TEXT,kickoff TEXT); CREATE TABLE picks(id INTEGER,match_id TEXT,status TEXT);')
        now = datetime.now(timezone.utc)
        fixtures = [('future', (now + timedelta(days=1)).isoformat()),
                    ('past', (now - timedelta(days=1)).isoformat()), ('unknown', None)]
        conn.executemany('INSERT INTO matches VALUES (?,?)', fixtures)
        conn.executemany('INSERT INTO picks VALUES (?,?,?)', [(1,'future','pending'), (2,'past','pending'), (3,'unknown','pending'), (4,'future','settled')])
        self.assertTrue(EdgeCalculator._has_pending_pick_for_match(conn.cursor(), 'future'))
        self.assertEqual(conn.execute('SELECT id,status FROM picks ORDER BY id').fetchall(),
                         [(1,'pending'), (2,'pending'), (3,'pending'), (4,'settled')])
        conn.close()

    def test_publishing_requires_exact_unblocked_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'draft.json'
            draft = {'input_fingerprint': 'old', 'blockers': []}
            path.write_text(json.dumps(draft))
            with patch('scripts.rebuild_card.draft_card', return_value=({'input_fingerprint':'new','blockers':[]}, [], None)):
                with self.assertRaisesRegex(ValueError, 'no longer matches'):
                    rebuild_card(reviewed=path)
            draft['blockers'] = ['stale odds']
            path.write_text(json.dumps(draft))
            with patch('scripts.rebuild_card.draft_card', return_value=(draft, [], None)):
                with self.assertRaisesRegex(ValueError, 'Publication blocked'):
                    rebuild_card(reviewed=path)
        with self.assertRaisesRegex(ValueError, 'preview'):
            rebuild_card()

    def test_timestamp_gate_rejects_stale_unknown_and_future_data(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(_fresh_timestamp((now - timedelta(hours=2)).isoformat(), now))
        for value in (None, 'invalid', (now-timedelta(hours=7)).isoformat(), (now+timedelta(hours=1)).isoformat()):
            self.assertFalse(_fresh_timestamp(value, now))

    def test_parleys_preserve_pending_exposure_and_do_not_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'card.db'
            with sqlite3.connect(db) as conn:
                conn.executescript("CREATE TABLE parley_slips(id INTEGER,status TEXT,pnl REAL,stake REAL); CREATE TABLE parley_legs(slip_id INTEGER,match_id TEXT); CREATE TABLE picks(match_id TEXT,status TEXT); CREATE TABLE matches(match_id TEXT,kickoff TEXT); INSERT INTO parley_slips VALUES(1,'pending',NULL,5); INSERT INTO parley_legs VALUES(1,'existing'); INSERT INTO picks VALUES('single','pending');")
            conn.close()
            gen = DashboardGenerator.__new__(DashboardGenerator)
            gen.settings = {'bankroll':100, 'flat_stake':10}
            candidates = [{'match_id':match,'odds':1.5,'model_prob':0.8,'kickoff':'2099-01-01 20:00'}
                          for match in ['existing','single','a','b','c','d','e','f']]
            proposed = [type('Pick', (), {'match_id': 'a'})()]
            with patch('dashboard.generator.DB_PATH', db), patch.object(gen, '_parley_candidates', return_value=candidates):
                slips = gen.preview_parley_slips(singles=proposed)
            ids = [leg['match_id'] for slip in slips for leg in slip['legs']]
            self.assertEqual(len(slips), 2)
            self.assertEqual(len(ids), len(set(ids)))
            self.assertNotIn('existing', ids)
            self.assertNotIn('single', ids)
            self.assertNotIn('a', ids)

    def test_parley_candidates_reject_skip_quality(self):
        gen = DashboardGenerator.__new__(DashboardGenerator)
        gen.settings = {
            'bankroll': 100,
            'use_ranges': True,
            'staking_mode': 'flat',
            'flat_stake': 10,
            'default_bookmaker': 'polymarket',
            'context_gate': {},
        }
        gen.range_configs = {}
        common = dict(
            home_team='Home', away_team='Away', league='EPL',
            kickoff='2099-01-01 20:00', selection='Away AH +1.5', market='AH',
            model_prob=0.8, book_prob=0.7, edge_pct=8.0, odds=1.4,
            stake=10, reasoning='', risk_note='', missing_context=(),
        )
        skip = SimpleNamespace(match_id='skip', quality='SKIP', **common)
        keep = SimpleNamespace(match_id='keep', quality='KEEP', **common)
        calc = MagicMock()
        calc.context_gate_enabled = True
        calc.generate_picks.return_value = [skip, keep]
        calc._apply_settled_reliability.side_effect = lambda pick, *_: pick
        calc._is_hard_loss_trap.return_value = False
        calc._historical_pick_score.return_value = 1.0
        calc._learned_performance_adjustments.return_value = {}
        calc._loss_trap_segments.return_value = {}
        calc._settled_market_reliability.return_value = {}
        with patch('dashboard.generator.EdgeCalculator', return_value=calc):
            candidates = gen._parley_candidates()
        self.assertEqual([candidate['match_id'] for candidate in candidates], ['keep'])


if __name__ == '__main__':
    unittest.main()
