#!/usr/bin/env python3
"""Import completed UEFA matches that affect the coming domestic fixture pool."""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.paths import DATA_DIR, DB_PATH, ensure_runtime_dirs
from models.core import init_db
from scripts.fetch_weekly_fixtures import _kickoff_local
from utils.match_resolver import parse_kickoff_utc
from utils.team_normalizer import normalize_team_name


API_BASE = "https://site.web.api.espn.com/apis/site/v2/sports/soccer"
COMPETITIONS = {
    "uefa.champions": "Champions League",
    "uefa.europa": "Europa League",
    "uefa.europa.conf": "Conference League",
}
CORE_LEAGUES = ("EPL", "L1", "Bundesliga", "SerieA", "LaLiga")
OBSERVATION_PATH = DATA_DIR / "uefa_context_weekly.json"
TEAM_CONTEXT_PATH = DATA_DIR / "team_context.json"


def _request_json(url: str) -> dict:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "RolloStake/1.0 uefa-context"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _score(value) -> int:
    return int(float(value or 0))


def _clock_minute(event: dict) -> int | None:
    clock = event.get("clock") or {}
    value = clock.get("value")
    if value is not None:
        return int(float(value) // 60)
    display = str(clock.get("displayValue") or "").split("+")[0].rstrip("'")
    return int(display) if display.isdigit() else None


def _team_statistics(summary: dict, team: str) -> dict:
    for row in (summary.get("boxscore") or {}).get("teams") or []:
        display_name = (row.get("team") or {}).get("displayName") or ""
        if normalize_team_name(display_name) != team:
            continue
        wanted = {"possessionPct", "totalShots", "shotsOnTarget", "yellowCards", "redCards"}
        return {
            item.get("name"): item.get("displayValue")
            for item in row.get("statistics") or []
            if item.get("name") in wanted
        }
    return {}


def _team_roster(summary: dict, team: str) -> dict:
    for row in summary.get("rosters") or []:
        display_name = (row.get("team") or {}).get("displayName") or ""
        if normalize_team_name(display_name) != team:
            continue
        starters = []
        full_match_starters = []
        substitutes_used = []
        for player in row.get("roster") or []:
            name = (player.get("athlete") or {}).get("displayName")
            if not name:
                continue
            if player.get("starter"):
                starters.append(name)
                if not player.get("subbedOut"):
                    full_match_starters.append(name)
            if player.get("subbedIn"):
                substitutes_used.append(name)
        return {
            "starters": starters,
            "full_match_starters": full_match_starters,
            "substitutes_used": substitutes_used,
        }
    return {"starters": [], "full_match_starters": [], "substitutes_used": []}


def _key_events(summary: dict, team: str) -> dict:
    substitutions = []
    red_cards = []
    injury_mentions = []
    extra_time = False
    for event in summary.get("keyEvents") or []:
        event_type = (event.get("type") or {}).get("type") or ""
        event_team = normalize_team_name((event.get("team") or {}).get("displayName") or "")
        text = str(event.get("text") or event.get("shortText") or "")
        minute = _clock_minute(event)
        if minute is not None and minute > 90:
            extra_time = True
        if event_team == team and event_type == "substitution":
            substitutions.append({"minute": minute, "text": text})
        if event_team == team and event_type in {"red-card", "second-yellow-card"}:
            red_cards.append({"minute": minute, "text": text})
        if event_team == team and ("injur" in text.lower() or "medical" in text.lower()):
            injury_mentions.append({"minute": minute, "text": text})
    return {
        "substitutions": substitutions,
        "red_cards": red_cards,
        "injury_mentions": injury_mentions,
        "extra_time": extra_time,
    }


def _upcoming_pool(conn: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, list[dict]]:
    placeholders = ",".join("?" for _ in CORE_LEAGUES)
    rows = conn.execute(
        f"""
        SELECT match_id, home_team, away_team, kickoff
        FROM matches
        WHERE status = 'scheduled'
          AND league IN ({placeholders})
          AND date(kickoff) BETWEEN date(?) AND date(?)
        ORDER BY kickoff
        """,
        (*CORE_LEAGUES, start_date, end_date),
    ).fetchall()
    pool: dict[str, list[dict]] = {}
    for match_id, home, away, kickoff in rows:
        for team in (normalize_team_name(home), normalize_team_name(away)):
            pool.setdefault(team, []).append({"match_id": match_id, "kickoff": kickoff})
    return pool


def _event_row(event: dict, competition_slug: str, timezone_name: str) -> dict | None:
    competition = (event.get("competitions") or [{}])[0]
    status = (competition.get("status") or {}).get("type") or {}
    competitors = competition.get("competitors") or []
    home = next((item for item in competitors if item.get("homeAway") == "home"), None)
    away = next((item for item in competitors if item.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    return {
        "match_id": f"espn_{competition_slug.replace('.', '_')}_{event.get('id')}",
        "event_id": str(event.get("id") or ""),
        "competition_slug": competition_slug,
        "league": COMPETITIONS[competition_slug],
        "kickoff": _kickoff_local(str(event.get("date") or ""), timezone_name),
        "home_team": normalize_team_name((home.get("team") or {}).get("displayName") or ""),
        "away_team": normalize_team_name((away.get("team") or {}).get("displayName") or ""),
        "home_goals": _score(home.get("score")),
        "away_goals": _score(away.get("score")),
        "completed": bool(status.get("completed")),
        "status": status.get("name") or status.get("description") or "",
    }


def collect_context(
    observation_start: str,
    observation_end: str,
    upcoming_start: str,
    upcoming_end: str,
    timezone_name: str,
) -> dict:
    conn = sqlite3.connect(DB_PATH)
    pool = _upcoming_pool(conn, upcoming_start, upcoming_end)
    conn.close()

    relevant = []
    unresolved = []
    date_range = f"{observation_start.replace('-', '')}-{observation_end.replace('-', '')}"
    for slug in COMPETITIONS:
        url = f"{API_BASE}/{slug}/scoreboard?{urlencode({'dates': date_range, 'limit': 200})}"
        data = _request_json(url)
        for event in data.get("events") or []:
            row = _event_row(event, slug, timezone_name)
            if not row or not ({row['home_team'], row['away_team']} & set(pool)):
                continue
            if not row["completed"]:
                unresolved.append(row)
                continue
            summary_url = f"{API_BASE}/{slug}/summary?{urlencode({'event': row['event_id']})}"
            summary = _request_json(summary_url)
            observations = {}
            for team in (row["home_team"], row["away_team"]):
                if team not in pool:
                    continue
                roster = _team_roster(summary, team)
                events = _key_events(summary, team)
                european_kickoff = parse_kickoff_utc(row["kickoff"])
                next_domestic = next(
                    (
                        fixture
                        for fixture in pool[team]
                        if parse_kickoff_utc(fixture["kickoff"]) > european_kickoff
                    ),
                    None,
                )
                rest_hours = None
                if next_domestic:
                    rest_hours = round(
                        (
                            parse_kickoff_utc(next_domestic["kickoff"]) - european_kickoff
                        ).total_seconds()
                        / 3600,
                        1,
                    )
                observations[team] = {
                    "statistics": _team_statistics(summary, team),
                    **roster,
                    **events,
                    "next_domestic": next_domestic,
                    "rest_hours_to_next_domestic": rest_hours,
                }
            row["observations"] = observations
            row["score"] = f"{row['home_team']} {row['home_goals']}-{row['away_goals']} {row['away_team']}"
            relevant.append(row)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "ESPN public scoreboard and match summary JSON",
        "observation_window": {"from": observation_start, "to": observation_end},
        "upcoming_window": {"from": upcoming_start, "to": upcoming_end},
        "upcoming_teams": sorted(pool),
        "matches": sorted(relevant, key=lambda item: (item["kickoff"], item["match_id"])),
        "unresolved": unresolved,
    }


def _team_context(observation: dict) -> dict:
    existing = {}
    if TEAM_CONTEXT_PATH.exists():
        try:
            existing = json.loads(TEAM_CONTEXT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    teams = dict(existing.get("teams") or {})
    generated_at = observation["generated_at"]
    for team in observation["upcoming_teams"]:
        entry = dict(teams.get(team) or {})
        entry.update(
            {
                "euro_schedule_checked": generated_at,
                "rotation_risk": "reviewed_no_signal",
            }
        )
        teams[team] = entry
    for match in observation["matches"]:
        for team, details in match["observations"].items():
            teams[team]["euro_observation"] = {
                "competition": match["league"],
                "kickoff": match["kickoff"],
                "score": match["score"],
                "full_match_starters": details["full_match_starters"],
                "substitutes_used": details["substitutes_used"],
                "red_cards": details["red_cards"],
                "injury_mentions": details["injury_mentions"],
                "rest_hours_to_next_domestic": details["rest_hours_to_next_domestic"],
            }
    return {**existing, "generated_at": generated_at, "teams": teams}


def save_context(observation: dict) -> int:
    if observation["unresolved"]:
        raise RuntimeError(f"Unresolved relevant UEFA matches: {len(observation['unresolved'])}")
    ensure_runtime_dirs()
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN")
        for row in observation["matches"]:
            conn.execute(
                """
                INSERT INTO matches (
                    match_id, home_team, away_team, league, kickoff,
                    home_goals, away_goals, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'completed')
                ON CONFLICT(match_id) DO UPDATE SET
                    home_team=excluded.home_team,
                    away_team=excluded.away_team,
                    league=excluded.league,
                    kickoff=excluded.kickoff,
                    home_goals=excluded.home_goals,
                    away_goals=excluded.away_goals,
                    status='completed'
                """,
                (
                    row["match_id"],
                    row["home_team"],
                    row["away_team"],
                    row["league"],
                    row["kickoff"],
                    row["home_goals"],
                    row["away_goals"],
                ),
            )
        conn.commit()
    finally:
        conn.close()
    OBSERVATION_PATH.write_text(json.dumps(observation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    TEAM_CONTEXT_PATH.write_text(
        json.dumps(_team_context(observation), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(observation["matches"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True, help="UEFA observation start, YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="UEFA observation end, YYYY-MM-DD")
    parser.add_argument("--upcoming-from", required=True, help="Domestic pool start, YYYY-MM-DD")
    parser.add_argument("--upcoming-to", required=True, help="Domestic pool end, YYYY-MM-DD")
    parser.add_argument("--timezone", default="Asia/Macau")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    observation = collect_context(
        args.from_date,
        args.to_date,
        args.upcoming_from,
        args.upcoming_to,
        args.timezone,
    )
    print(f"Upcoming teams checked: {len(observation['upcoming_teams'])}")
    print(f"Relevant completed UEFA matches: {len(observation['matches'])}")
    print(f"Unresolved relevant UEFA matches: {len(observation['unresolved'])}")
    for row in observation["matches"]:
        print(f"  {row['kickoff']} | {row['league']} | {row['score']}")
        for team, details in row["observations"].items():
            print(
                f"    {team}: {len(details['full_match_starters'])} full-match starters, "
                f"{len(details['substitutes_used'])} substitutes, "
                f"{len(details['red_cards'])} red cards, "
                f"rest={details['rest_hours_to_next_domestic']}h"
            )
    if observation["unresolved"]:
        raise SystemExit(2)
    if args.dry_run:
        print("Dry run only; no database or file writes")
        return
    count = save_context(observation)
    print(f"Saved {count} completed UEFA context matches")
    print(f"Observation file: {OBSERVATION_PATH}")
    print(f"Team context file: {TEAM_CONTEXT_PATH}")


if __name__ == "__main__":
    main()
