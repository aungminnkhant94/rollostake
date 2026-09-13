"""Canonical player identities and deterministic news reconciliation."""

import re
import unicodedata
from datetime import datetime, timezone


def person_key(value):
    text = unicodedata.normalize('NFKD', str(value or ''))
    return re.sub(r'[^a-z0-9]', '', text.encode('ascii', 'ignore').decode().lower())


def player_key(team, player):
    key = person_key(player)
    # Explicit full-name aliases only; surname matching can merge different people.
    aliases = {
        ('brighton', 'yankubamintehmoat'): 'yankubaminteh',
        ('tottenham', 'saviomoreiradeoliveira'): 'savio',
    }
    return person_key(team), aliases.get((person_key(team), key), key)


def news_time(item):
    """Prefer actual evidence time; retrieval time is only a legacy fallback."""
    raw = item.get('observed_at') or item.get('fetched_at')
    if not raw:
        match = re.search(r'\d{4}-\d{2}-\d{2}', str(item.get('source', '')))
        raw = match.group() if match else None
    try:
        parsed = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def reconcile_news(items):
    """Choose latest evidence before filtering injury status, so recovery wins."""
    selected = {}
    confidence = {'high': 3, 'medium': 2, 'low': 1}
    def rank(item):
        return (news_time(item), confidence.get(item.get('confidence'), 0),
                str(item.get('source') or ''))
    for item in items:
        key = player_key(item.get('team'), item.get('player'))
        if not key[1]:
            # Preserve unnamed team-level reports independently.
            key += (str(item.get('source')), str(item.get('reason')))
        if key not in selected or rank(item) > rank(selected[key]):
            selected[key] = dict(item)
    return list(selected.values())


def injury_news(items):
    return [item for item in reconcile_news(items)
            if str(item.get('status') or '').lower() in
            {'injured', 'injury', 'suspended', 'out', 'doubtful'}]
