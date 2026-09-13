"""Reconcile history at read time without rewriting recorded bets or fixtures."""
from utils.team_normalizer import normalize_team_name
from utils.match_resolver import parse_kickoff_utc


def reconcile_history(rows):
    groups = {}
    timestamps = {}
    def timestamp(value):
        if value not in timestamps:
            timestamps[value] = parse_kickoff_utc(value)
        return timestamps[value]
    for original in rows:
        row = dict(original)
        row['home_team'] = normalize_team_name(row['home_team'])
        row['away_team'] = normalize_team_name(row['away_team'])
        key = (row.get('league'), row['home_team'], row['away_team'], row['home_goals'], row['away_goals'])
        group = groups.setdefault(key, [])
        stamp = timestamp(row.get('kickoff'))
        duplicate = None
        for index, old in enumerate(group):
            previous = timestamp(old.get('kickoff'))
            date_only = len(str(row.get('kickoff'))) == 10 or len(str(old.get('kickoff'))) == 10
            if stamp and previous and abs((stamp-previous).total_seconds()) <= (36 if date_only else 18)*3600:
                duplicate = index
                break
        if duplicate is None:
            group.append(row)
        elif str(row.get('match_id') or '').startswith('espn_'):
            group[duplicate] = row
    return [row for group in groups.values() for row in group]
