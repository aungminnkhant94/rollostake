import unittest
from utils.match_history import reconcile_history


class MatchHistoryTests(unittest.TestCase):
    def test_aliases_and_date_only_duplicate_do_not_split_team_or_double_count(self):
        old = dict(match_id='historical',league='SerieA',home_team='Inter',away_team='Monza',home_goals=4,away_goals=1,kickoff='2026-08-22')
        new = dict(old,match_id='espn_ita_1_401874931',home_team='Internazionale',kickoff='2026-08-23 00:30')
        later = dict(new,match_id='next',kickoff='2027-01-01 20:00')
        result = reconcile_history([old,new,later])
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['home_team'],'Inter Milan')
        self.assertEqual(result[0]['match_id'],new['match_id'])
        self.assertEqual(old['home_team'],'Inter')


if __name__ == '__main__':
    unittest.main()
