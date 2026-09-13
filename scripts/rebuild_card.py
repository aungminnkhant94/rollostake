#!/usr/bin/env python3
"""Rebuild risk-band picks and dashboard from saved predictions and odds."""

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.edge_calculator import EdgeCalculator
from config.settings import load_settings
from dashboard.generator import DashboardGenerator
from config.paths import DB_PATH
from utils.match_resolver import parse_kickoff_utc


def _input_fingerprint():
    digest = hashlib.sha256()
    with closing(sqlite3.connect(f'file:{Path(DB_PATH).as_posix()}?mode=ro', uri=True)) as conn:
        digest.update(conn.serialize())
    root = Path(__file__).resolve().parents[1]
    for name in ['config/settings.json', 'analysis/edge_calculator.py',
                 'analysis/adjustment_layers.py', 'dashboard/generator.py',
                 'scripts/rebuild_card.py', 'utils/player_news.py']:
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def _fresh_timestamp(raw, now, hours=6):
    try:
        stamp = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return 0 <= (now - stamp).total_seconds() <= hours * 3600
    except (ValueError, TypeError):
        return False


def _evidence_blockers(picks, bookmaker):
    """Reject stale prices/predictions and already-started or uncertain fixtures."""
    blockers = []
    now = datetime.now(timezone.utc)
    with closing(sqlite3.connect(f'file:{Path(DB_PATH).as_posix()}?mode=ro', uri=True)) as conn:
        for pick in picks:
            kickoff = parse_kickoff_utc(pick.get('kickoff'))
            if kickoff is None or kickoff <= now or (kickoff - now).days >= 7:
                blockers.append(f"{pick['match_id']}: kickoff outside future seven-day window")
            row = conn.execute('SELECT calculated_at FROM predictions WHERE match_id=? ORDER BY calculated_at DESC LIMIT 1', (pick['match_id'],)).fetchone()
            if not row or not _fresh_timestamp(row[0], now):
                blockers.append(f"{pick['match_id']}: prediction older than 6 hours or unknown")
            row = conn.execute('SELECT odds,scraped_at FROM odds WHERE match_id=? AND market=? AND selection=? AND bookmaker=? ORDER BY scraped_at DESC LIMIT 1', (pick['match_id'],pick['market'],pick['selection'],bookmaker)).fetchone()
            if not row or not _fresh_timestamp(row[1], now) or abs(float(row[0]) - float(pick['odds'])) > 1e-8:
                blockers.append(f"{pick['match_id']}: current exact-market odds missing, changed, or older than 6 hours")
    return blockers


def draft_card(league: str = None):
    fingerprint = _input_fingerprint()
    settings = load_settings()
    calc = EdgeCalculator(
        bankroll=float(settings.get("bankroll", 10000)),
        use_ranges=bool(settings.get("use_ranges", True)),
        staking_mode=settings.get("staking_mode", "flat"),
        flat_stake=float(settings.get("flat_stake", 200)),
        range_configs=EdgeCalculator.range_configs_from_settings(settings),
        bookmaker=settings.get("default_bookmaker", "polymarket"),
        context_gate=settings.get("context_gate"),
    )
    picks = calc.generate_range_picks(league=league)
    # Existing singles and parlays remain outstanding obligations. A refresh may
    # add picks only on unused matches and only up to each band's total ceiling.
    with closing(sqlite3.connect(f'file:{Path(DB_PATH).as_posix()}?mode=ro', uri=True)) as conn:
        pending_rows = conn.execute(
            "SELECT match_id, COALESCE(range_code, 'D') FROM picks WHERE status='pending'"
        ).fetchall()
        exposed = {row[0] for row in pending_rows}
        existing_by_band = {}
        for _, code in pending_rows:
            existing_by_band[code] = existing_by_band.get(code, 0) + 1
        exposed.update(row[0] for row in conn.execute(
            "SELECT l.match_id FROM parley_legs l JOIN parley_slips s ON s.id=l.slip_id WHERE s.status='pending'"
        ))
    overlap_removed = [p.match_id for p in picks if p.match_id in exposed]
    remaining = {
        code: max(0, config.max_picks - existing_by_band.get(code, 0))
        for code, config in calc.range_configs.items()
    }
    capacity_removed = []
    additions = []
    for pick in picks:
        if pick.match_id in exposed:
            continue
        if remaining.get(pick.range_code, 0) <= 0:
            capacity_removed.append(pick.match_id)
            continue
        additions.append(pick)
        remaining[pick.range_code] -= 1
    picks = additions
    dashboard = DashboardGenerator(initialize=False)
    slips = dashboard.preview_parley_slips(singles=picks)
    selections = [asdict(p) for p in picks]
    blockers = _evidence_blockers(selections + [leg for s in slips for leg in s['legs']], calc.bookmaker)
    if fingerprint != _input_fingerprint():
        raise RuntimeError('Inputs changed while drafting; generate a new preview')
    draft = dict(version=1, input_fingerprint=fingerprint, league=league,
                 singles=selections, new_parleys=slips, blockers=blockers,
                 overlap_removed=overlap_removed, capacity_removed=capacity_removed,
                 context_blocks=calc.context_blocks)
    # Normalize tuples and JSON types for exact comparison with saved previews.
    return json.loads(json.dumps(draft)), picks, calc


def rebuild_card(league=None, reviewed=None):
    if reviewed is None:
        raise ValueError('Create and audit --preview PATH, then use --publish-reviewed PATH')
    expected = json.loads(Path(reviewed).read_text(encoding='utf-8'))
    draft, picks, calc = draft_card(league=league)
    if draft != expected:
        raise ValueError('Reviewed draft no longer matches inputs or selections; create a new preview')
    if draft['blockers']:
        raise ValueError('Publication blocked: ' + '; '.join(draft['blockers']))
    calc.save_range_picks(picks)
    dashboard = DashboardGenerator()
    saved = dashboard.save_parley_slips(draft['new_parleys'])
    dashboard.generate()
    print(f"Published reviewed card: {len(picks)} singles, {len(saved)} new parley slips")
    return picks, calc


def main():
    parser = argparse.ArgumentParser(description="Rebuild official card from saved predictions and odds")
    parser.add_argument("--league", default=None, help="Optional league filter")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', type=Path, help='Write a draft JSON without changing the database or dashboard')
    mode.add_argument('--publish-reviewed', type=Path, help='Publish the exact audited preview if inputs still match and no blockers remain')
    args = parser.parse_args()

    if args.preview:
        draft, picks, calc = draft_card(league=args.league)
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        args.preview.write_text(json.dumps(draft, indent=2), encoding='utf-8')
        print(f"Draft saved to {args.preview}; {len(draft['blockers'])} publication blockers")
        for blocker in draft['blockers']:
            print('BLOCKED ' + blocker)
    else:
        picks, calc = rebuild_card(league=args.league, reviewed=args.publish_reviewed)
    blocked_matches = {item["match_id"] for item in calc.context_blocks}
    if blocked_matches:
        print(f"Context gate blocked candidates from {len(blocked_matches)} matches")
        for item in calc.context_blocks:
            print(
                f"BLOCKED {item['match']} | {item['selection']} | "
                f"missing {', '.join(item['missing'])}"
            )
    for pick in picks:
        risk_name = calc.range_configs[pick.range_code].name
        print(
            f"{risk_name} {pick.market} {pick.home_team} vs {pick.away_team} | "
            f"{pick.selection} @{pick.odds:.2f} | {pick.quality} | edge {pick.edge_pct:.1f}%"
        )


if __name__ == "__main__":
    main()
