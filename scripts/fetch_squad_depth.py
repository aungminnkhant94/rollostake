#!/usr/bin/env python3
"""Fetch current first-team roster snapshots and position depth from ESPN."""

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.paths import DB_PATH
from models.core import init_db
from utils.team_normalizer import normalize_team_name


API_BASE = "https://site.web.api.espn.com/apis/site/v2/sports/soccer"
LEAGUE_CODES = {
    "EPL": "eng.1",
    "L1": "fra.1",
    "Bundesliga": "ger.1",
    "SerieA": "ita.1",
    "LaLiga": "esp.1",
}


def _request_json(url: str, attempts: int = 3) -> dict:
    last_error = None
    for attempt in range(attempts):
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "RolloStake/1.0 squad-depth-fetcher",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"ESPN request failed after {attempts} attempts: {url}: {last_error}")


def _league_teams(league: str) -> list[dict]:
    code = LEAGUE_CODES[league]
    url = f"{API_BASE}/{code}/teams?limit=100"
    data = _request_json(url)
    sports = data.get("sports") or []
    leagues = (sports[0].get("leagues") or []) if sports else []
    if not leagues:
        raise RuntimeError(f"No ESPN team list returned for {league}")
    result = []
    for item in leagues[0].get("teams") or []:
        team = item.get("team") or {}
        if not team.get("id") or not team.get("displayName"):
            continue
        result.append(
            {
                "league": league,
                "league_code": code,
                "team_id": str(team["id"]),
                "team": normalize_team_name(team["displayName"]),
                "raw_team_name": team["displayName"],
            }
        )
    if len(result) < 16:
        raise RuntimeError(f"Incomplete ESPN team list for {league}: {len(result)} clubs")
    return result


def _position_bucket(position: str, abbreviation: str = "") -> str:
    position = str(position or "")
    abbreviation = str(abbreviation or "")
    text = f"{position} {abbreviation}".strip().lower()
    if position.lower() == "goalkeeper" or abbreviation.upper() in {"G", "GK"}:
        return "goalkeepers"
    if "defender" in text or abbreviation.upper() in {"D", "DF"}:
        return "defenders"
    if "midfielder" in text or abbreviation.upper() in {"M", "MF"}:
        return "midfielders"
    if "forward" in text or abbreviation.upper() in {"F", "FW"}:
        return "forwards"
    return "unknown_positions"


def _team_roster(team: dict, min_players: int) -> tuple[list[dict], dict]:
    url = (
        f"{API_BASE}/{team['league_code']}/teams/"
        f"{team['team_id']}/roster"
    )
    data = _request_json(url)
    athletes = data.get("athletes") or []
    if len(athletes) < min_players:
        raise RuntimeError(
            f"Incomplete roster for {team['team']} ({team['league']}): "
            f"{len(athletes)} players"
        )
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    season = str((data.get("season") or {}).get("displayName") or "")
    rows = []
    depth = Counter()
    active_players = 0
    for athlete in athletes:
        player_id = str(athlete.get("id") or "")
        player_name = str(athlete.get("displayName") or athlete.get("fullName") or "").strip()
        if not player_id or not player_name:
            continue
        position = athlete.get("position") or {}
        position_name = str(position.get("displayName") or position.get("name") or "")
        position_abbr = str(position.get("abbreviation") or "")
        player_status = str((athlete.get("status") or {}).get("type") or "unknown")
        if player_status == "active":
            active_players += 1
        depth[_position_bucket(position_name, position_abbr)] += 1
        rows.append(
            {
                **team,
                "player_id": player_id,
                "player_name": player_name,
                "position": position_name,
                "position_abbr": position_abbr,
                "jersey": str(athlete.get("jersey") or ""),
                "player_status": player_status,
                "age": athlete.get("age"),
                "season": season,
                "source": "espn",
                "source_url": url,
                "fetched_at": fetched_at,
            }
        )
    if len(rows) < min_players:
        raise RuntimeError(f"Roster rows failed validation for {team['team']}: {len(rows)}")
    summary = {
        **team,
        **{key: int(depth[key]) for key in (
            "goalkeepers", "defenders", "midfielders", "forwards", "unknown_positions"
        )},
        "total_players": len(rows),
        "active_players": active_players,
        "season": season,
        "source": "espn",
        "source_url": url,
        "fetched_at": fetched_at,
    }
    return rows, summary


def fetch_squads(leagues: list[str], min_players: int, workers: int) -> tuple[list[dict], list[dict]]:
    teams = []
    for league in leagues:
        league_teams = _league_teams(league)
        print(f"{league}: discovered {len(league_teams)} clubs", flush=True)
        teams.extend(league_teams)

    player_rows = []
    summaries = []
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_team_roster, team, min_players): team
            for team in teams
        }
        for future in as_completed(futures):
            team = futures[future]
            try:
                rows, summary = future.result()
                player_rows.extend(rows)
                summaries.append(summary)
                print(
                    f"{team['league']}: {team['team']} — {summary['total_players']} players",
                    flush=True,
                )
            except Exception as exc:
                failures.append(f"{team['league']} {team['team']}: {exc}")

    if failures:
        joined = "\n  ".join(failures)
        raise RuntimeError(f"Squad refresh aborted; existing database kept unchanged:\n  {joined}")
    if len(summaries) != len(teams):
        raise RuntimeError(
            f"Squad refresh incomplete: {len(summaries)} of {len(teams)} clubs"
        )
    return player_rows, summaries


def save_squads(player_rows: list[dict], summaries: list[dict], leagues: list[str]) -> None:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN")
        placeholders = ",".join("?" for _ in leagues)
        conn.execute(f"DELETE FROM squad_players WHERE league IN ({placeholders})", leagues)
        conn.execute(f"DELETE FROM squad_depth WHERE league IN ({placeholders})", leagues)
        conn.executemany(
            """
            INSERT INTO squad_players (
                league, team, raw_team_name, espn_team_id, player_id, player_name,
                position, position_abbr, jersey, player_status, age, season,
                source, source_url, fetched_at
            ) VALUES (
                :league, :team, :raw_team_name, :team_id, :player_id, :player_name,
                :position, :position_abbr, :jersey, :player_status, :age, :season,
                :source, :source_url, :fetched_at
            )
            """,
            player_rows,
        )
        conn.executemany(
            """
            INSERT INTO squad_depth (
                league, team, goalkeepers, defenders, midfielders, forwards,
                unknown_positions, total_players, active_players, season,
                source, source_url, fetched_at
            ) VALUES (
                :league, :team, :goalkeepers, :defenders, :midfielders, :forwards,
                :unknown_positions, :total_players, :active_players, :season,
                :source, :source_url, :fetched_at
            )
            """,
            summaries,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh current squad players and position-depth summaries from ESPN"
    )
    parser.add_argument(
        "--leagues",
        nargs="+",
        choices=list(LEAGUE_CODES),
        default=list(LEAGUE_CODES),
    )
    parser.add_argument("--min-players", type=int, default=15)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    player_rows, summaries = fetch_squads(args.leagues, args.min_players, args.workers)
    if args.dry_run:
        print(
            f"DRY RUN: validated {len(summaries)} clubs and {len(player_rows)} players; no database changes"
        )
        return 0
    save_squads(player_rows, summaries, args.leagues)
    print(f"Saved {len(player_rows)} players across {len(summaries)} clubs")
    for league in args.leagues:
        league_summaries = [row for row in summaries if row["league"] == league]
        print(
            f"{league}: {len(league_summaries)} clubs, "
            f"{sum(row['total_players'] for row in league_summaries)} players"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
