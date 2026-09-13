"""Fourteen football-context adjustment layers for model lambdas.

The production pipeline prices markets from saved lambdas. This module keeps
all pre-market football context in one place so 1X2, totals, team totals,
Asian handicaps, and parlays all inherit the same adjusted view.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from config.paths import DB_PATH, PROJECT_ROOT
from utils.match_resolver import parse_kickoff_utc
from utils.match_history import reconcile_history


DOMESTIC_LEAGUES = {"EPL", "L1", "Bundesliga", "SerieA", "LaLiga"}
EVIDENCE_ACTIVE = "ACTIVE"
EVIDENCE_NO_SIGNAL = "NO_SIGNAL"
EVIDENCE_NOT_APPLICABLE = "NOT_APPLICABLE"
EVIDENCE_MISSING = "MISSING_DATA"
EURO_TERMS = (
    ("champions", 0.95, "CL"),
    ("ucl", 0.95, "CL"),
    ("europa", 0.96, "EL"),
    ("uel", 0.96, "EL"),
    ("conference", 0.965, "ECL"),
    ("uecl", 0.965, "ECL"),
    ("ecl", 0.965, "ECL"),
)
CUP_TERMS = (
    ("fa cup", 0.965, "FA Cup"),
    ("efl cup", 0.97, "EFL Cup"),
    ("carabao", 0.97, "EFL Cup"),
    ("dfb", 0.965, "DFB-Pokal"),
    ("pokal", 0.965, "DFB-Pokal"),
    ("copa del rey", 0.965, "Copa del Rey"),
    ("coppa italia", 0.965, "Coppa Italia"),
    ("coupe de france", 0.965, "Coupe de France"),
    ("cup", 0.97, "Cup"),
)
KNOCKOUT_TERMS = ("2nd leg", "second leg", "quarter", "qf", "semi", "sf", "final")
COMMON_DERBIES = {
    tuple(sorted(pair))
    for pair in (
        ("Arsenal", "Tottenham"),
        ("Arsenal", "Chelsea"),
        ("Chelsea", "Tottenham"),
        ("Man City", "Man United"),
        ("Man City", "Man Utd"),
        ("Man United", "Man Utd"),
        ("Liverpool", "Everton"),
        ("Newcastle", "Sunderland"),
        ("Aston Villa", "Birmingham"),
        ("Real Madrid", "Barcelona"),
        ("Real Madrid", "Atletico Madrid"),
        ("Barcelona", "Espanol"),
        ("Sevilla", "Real Betis"),
        ("Athletic Bilbao", "Real Sociedad"),
        ("Inter", "Milan"),
        ("AC Milan", "Inter"),
        ("Roma", "Lazio"),
        ("Juventus", "Torino"),
        ("Genoa", "Sampdoria"),
        ("Bayern Munich", "Borussia Dortmund"),
        ("Dortmund", "Schalke"),
        ("PSG", "Marseille"),
        ("Lyon", "Saint-Etienne"),
        ("Nice", "Monaco"),
        ("Lens", "Lille"),
    )
}


@dataclass
class LayerMove:
    layer_no: int
    layer_name: str
    home_before: float
    away_before: float
    home_after: float
    away_after: float
    note: str
    active: bool
    evidence_state: str = EVIDENCE_NO_SIGNAL

    def as_row(self, match_id: str) -> Tuple:
        return (
            match_id,
            self.layer_no,
            self.layer_name,
            round(self.home_before, 4),
            round(self.away_before, 4),
            round(self.home_after, 4),
            round(self.away_after, 4),
            self.note,
            1 if self.active else 0,
            self.evidence_state,
        )


class AdjustmentLayerEngine:
    """Apply the 14 requested adjustment layers to base Dixon-Coles lambdas."""

    LAYERS = (
        (1, "Rolling blend"),
        (2, "Elo SoS"),
        (3, "Finishing quality"),
        (4, "Motivation"),
        (5, "Manager bounce"),
        (6, "Derby"),
        (7, "Injuries"),
        (8, "Euro fatigue"),
        (9, "Cup fatigue"),
        (10, "Rest days"),
        (11, "Rotation"),
        (12, "Luck regression"),
        (13, "Lambda cap"),
        (14, "Dixon-Coles rho"),
    )

    def __init__(self, settings: Dict):
        config = settings.get("adjustment_layers", {}) or {}
        self.enabled = bool(config.get("enabled", True))
        self.rolling_threshold = float(config.get("rolling_blend_threshold", 0.30))
        self.lambda_min = float(config.get("lambda_min", 0.30))
        self.lambda_max = float(config.get("lambda_max", 5.00))
        self.rho = float(config.get("dixon_coles_rho", -0.13))
        self.manual_context_path = PROJECT_ROOT / str(
            config.get("manual_context_file", "data/team_context.json")
        )
        self.extra_derbies = {
            tuple(sorted((str(pair[0]), str(pair[1]))))
            for pair in config.get("derby_pairs", [])
            if isinstance(pair, list) and len(pair) == 2
        }
        self._manual_context = self._load_manual_context()
        self._cache: Dict[Tuple, object] = {}
        self._current_match_key: Optional[Tuple[str, str, str]] = None

    def apply(self, match: Dict, preds: Dict) -> Dict:
        """Return adjusted prediction dict with layer notes and audit rows."""
        if not self.enabled:
            capped_h = self._cap_lambda(float(preds.get("lambda_h", 0) or 0))
            capped_a = self._cap_lambda(float(preds.get("lambda_a", 0) or 0))
            return self._with_probabilities(preds, capped_h, capped_a, [])

        home = str(match.get("home_team") or "")
        away = str(match.get("away_team") or "")
        league = str(match.get("league") or "")
        kickoff = str(match.get("kickoff") or "")
        match_id = str(match.get("match_id") or "")
        kickoff_utc = parse_kickoff_utc(kickoff)
        self._current_match_key = (
            home,
            away,
            kickoff_utc.date().isoformat() if kickoff_utc else kickoff[:10],
        )

        home_lambda = max(float(preds.get("lambda_h") or 0), 0.01)
        away_lambda = max(float(preds.get("lambda_a") or 0), 0.01)
        moves: List[LayerMove] = []
        rest_blocked = {"home": False, "away": False}

        home_lambda, away_lambda = self._layer_rolling_blend(
            moves, home_lambda, away_lambda, home, away, league, kickoff
        )
        home_lambda, away_lambda = self._layer_elo_sos(
            moves, home_lambda, away_lambda, home, away, league, kickoff
        )
        home_lambda, away_lambda = self._layer_finishing(
            moves, home_lambda, away_lambda, home, away, kickoff
        )
        home_lambda, away_lambda = self._layer_motivation(
            moves, home_lambda, away_lambda, home, away, league, kickoff
        )
        home_lambda, away_lambda = self._layer_manager(
            moves, home_lambda, away_lambda, home, away
        )
        home_lambda, away_lambda = self._layer_derby(
            moves, home_lambda, away_lambda, home, away
        )
        home_lambda, away_lambda = self._layer_injuries(
            moves, home_lambda, away_lambda, home, away, kickoff
        )
        home_lambda, away_lambda = self._layer_competition_fatigue(
            moves, home_lambda, away_lambda, home, away, kickoff, EURO_TERMS, 8, "Euro fatigue", rest_blocked
        )
        home_lambda, away_lambda = self._layer_competition_fatigue(
            moves, home_lambda, away_lambda, home, away, kickoff, CUP_TERMS, 9, "Cup fatigue", rest_blocked
        )
        home_lambda, away_lambda = self._layer_rest_days(
            moves, home_lambda, away_lambda, home, away, kickoff, rest_blocked
        )
        home_lambda, away_lambda = self._layer_rotation(
            moves, home_lambda, away_lambda, home, away, kickoff
        )
        home_lambda, away_lambda = self._layer_luck_regression(
            moves, home_lambda, away_lambda, home, away, kickoff
        )

        before_h, before_a = home_lambda, away_lambda
        home_lambda = self._cap_lambda(home_lambda)
        away_lambda = self._cap_lambda(away_lambda)
        self._record(
            moves,
            13,
            "Lambda cap",
            before_h,
            before_a,
            home_lambda,
            away_lambda,
            f"lambda cap [{self.lambda_min:.1f}, {self.lambda_max:.1f}]",
            active=(home_lambda != before_h or away_lambda != before_a),
        )
        self._record(
            moves,
            14,
            "Dixon-Coles rho",
            home_lambda,
            away_lambda,
            home_lambda,
            away_lambda,
            f"Dixon-Coles low-score correction rho={self.rho:+.2f}",
            active=True,
        )

        adjusted = self._with_probabilities(preds, home_lambda, away_lambda, moves)
        adjusted["match_id"] = match_id
        return adjusted

    def _with_probabilities(self, preds: Dict, lambda_h: float, lambda_a: float, moves: List[LayerMove]) -> Dict:
        probs = self._probabilities(lambda_h, lambda_a)
        active_notes = [
            f"L{move.layer_no} {move.layer_name}: {move.note}"
            for move in moves
            if move.active and move.layer_no != 14
        ]
        note = "14-layer model"
        if active_notes:
            note += " | " + "; ".join(active_notes[:8])
        else:
            note += " | no material pre-cap context moves"
        if len(note) > 1200:
            note = note[:1197] + "..."

        result = dict(preds)
        result.update(
            {
                "lambda_h": round(lambda_h, 3),
                "lambda_a": round(lambda_a, 3),
                "prob_home_win": probs["prob_home_win"],
                "prob_draw": probs["prob_draw"],
                "prob_away_win": probs["prob_away_win"],
                "prob_over_1_5": probs["prob_over_1_5"],
                "prob_over_2_5": probs["prob_over_2_5"],
                "prob_under_2_5": probs["prob_under_2_5"],
                "prob_btts_yes": probs["prob_btts_yes"],
                "adj_prob_home": probs["prob_home_win"],
                "adj_prob_draw": probs["prob_draw"],
                "adj_prob_away": probs["prob_away_win"],
                "adjustment_note": note,
                "layers": moves,
            }
        )
        return result

    def _layer_rolling_blend(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
        league: str,
        kickoff: str,
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        home_stats = self._team_stats(home, kickoff, limit=6, days=120)
        away_stats = self._team_stats(away, kickoff, limit=6, days=120)
        history_window = 120
        if int(home_stats.get("played") or 0) < 3 or int(away_stats.get("played") or 0) < 3:
            home_carry = self._team_stats(home, kickoff, limit=6, days=365)
            away_carry = self._team_stats(away, kickoff, limit=6, days=365)
            if int(home_carry.get("played") or 0) < 3 or int(away_carry.get("played") or 0) < 3:
                self._record(
                    moves,
                    1,
                    "Rolling blend",
                    before_h,
                    before_a,
                    lambda_h,
                    lambda_a,
                    f"insufficient form history: {home} {int(home_carry.get('played') or 0)} matches, "
                    f"{away} {int(away_carry.get('played') or 0)} matches; need 3 each",
                    active=False,
                    evidence_state=EVIDENCE_MISSING,
                )
                return lambda_h, lambda_a
            home_stats, away_stats = home_carry, away_carry
            history_window = 365
        home_form = self._form_lambda(
            home, away, "home", league, kickoff, lambda_h, days=history_window
        )
        away_form = self._form_lambda(
            away, home, "away", league, kickoff, lambda_a, days=history_window
        )
        notes = []
        if history_window == 365:
            notes.append("early-season carryover: last six matches within 365d")
        if self._diverges(lambda_h, home_form):
            lambda_h = (lambda_h + home_form) / 2
            notes.append(f"home form blend {before_h:.2f}->{lambda_h:.2f}")
        if self._diverges(lambda_a, away_form):
            lambda_a = (lambda_a + away_form) / 2
            notes.append(f"away form blend {before_a:.2f}->{lambda_a:.2f}")
        self._record(
            moves,
            1,
            "Rolling blend",
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            "; ".join(notes) or f"form did not diverge by >{self.rolling_threshold:.0%}",
            active=abs(lambda_h - before_h) >= 0.005 or abs(lambda_a - before_a) >= 0.005,
        )
        return lambda_h, lambda_a

    def _layer_elo_sos(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
        league: str,
        kickoff: str,
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        ratings, sos = self._elo_snapshot(league, kickoff)
        if home not in ratings or away not in ratings:
            self._record(
                moves,
                2,
                "Elo SoS",
                before_h,
                before_a,
                lambda_h,
                lambda_a,
                "not enough league Elo history",
                False,
                evidence_state=EVIDENCE_MISSING,
            )
            return lambda_h, lambda_a

        home_elo = ratings[home]
        away_elo = ratings[away]
        home_sos = sos.get(home, 1500.0)
        away_sos = sos.get(away, 1500.0)
        elo_term = self._clamp(((home_elo + 65.0) - away_elo) / 400.0 * 0.055, -0.08, 0.08)
        sos_term = self._clamp((home_sos - away_sos) / 400.0 * 0.025, -0.025, 0.025)
        home_shift = self._clamp(elo_term + sos_term, -0.09, 0.09)
        away_shift = self._clamp(-elo_term - sos_term, -0.09, 0.09)
        lambda_h *= 1.0 + home_shift
        lambda_a *= 1.0 + away_shift
        active = abs(home_shift) >= 0.01 or abs(away_shift) >= 0.01
        self._record(
            moves,
            2,
            "Elo SoS",
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            f"Elo {home_elo:.0f}-{away_elo:.0f}, SoS {home_sos:.0f}-{away_sos:.0f}",
            active,
        )
        return lambda_h, lambda_a

    def _layer_finishing(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
        kickoff: str,
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        home_mult, home_note = self._finishing_multiplier(home, kickoff)
        away_mult, away_note = self._finishing_multiplier(away, kickoff)
        missing_xg = [team for team in (home, away) if not self._has_xg_context(team)]
        missing_finishing = [
            team for team in missing_xg if not self._has_finishing_proxy(team, kickoff)
        ]
        lambda_h *= home_mult
        lambda_a *= away_mult
        notes = [note for note in (home_note, away_note) if note]
        if missing_xg:
            proxy_teams = [team for team in missing_xg if team not in missing_finishing]
            if proxy_teams:
                notes.insert(
                    0,
                    f"xG unavailable for {', '.join(proxy_teams)}; verified goal-history proxy used",
                )
        if missing_finishing:
            notes.insert(
                0,
                f"insufficient xG or goal-history evidence for {', '.join(missing_finishing)}",
            )
        self._record(
            moves,
            3,
            "Finishing quality",
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            "; ".join(notes) or "xG context present with no finishing divergence",
            active=abs(home_mult - 1.0) >= 0.005 or abs(away_mult - 1.0) >= 0.005,
            evidence_state=EVIDENCE_MISSING if missing_finishing else None,
        )
        return lambda_h, lambda_a

    def _layer_motivation(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
        league: str,
        kickoff: str,
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        home_mult, home_note = self._motivation_multiplier(home, league, kickoff)
        away_mult, away_note = self._motivation_multiplier(away, league, kickoff)
        table = self._league_table(league, kickoff)
        motivation_covered = all(
            bool(self._team_context(team).get("motivation"))
            or bool(table.get(team) and int(table[team].get("played") or 0) >= 6)
            for team in (home, away)
        )
        lambda_h *= home_mult
        lambda_a *= away_mult
        notes = [note for note in (home_note, away_note) if note]
        self._record(
            moves,
            4,
            "Motivation",
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            "; ".join(notes) or "no table/manual motivation signal",
            active=bool(notes),
            evidence_state=None if motivation_covered else EVIDENCE_MISSING,
        )
        return lambda_h, lambda_a

    def _layer_manager(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        home_mult, home_note = self._manager_multiplier(home)
        away_mult, away_note = self._manager_multiplier(away)
        manager_reviewed = all(
            any(key in self._team_context(team) for key in ("manager_bounce", "manager"))
            for team in (home, away)
        )
        lambda_h *= home_mult
        lambda_a *= away_mult
        notes = [note for note in (home_note, away_note) if note]
        self._record(
            moves,
            5,
            "Manager bounce",
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            "; ".join(notes) or "no manager-bounce signal",
            active=bool(notes),
            evidence_state=None if manager_reviewed else EVIDENCE_MISSING,
        )
        return lambda_h, lambda_a

    def _layer_derby(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        if not self._is_derby(home, away):
            self._record(
                moves,
                6,
                "Derby",
                before_h,
                before_a,
                lambda_h,
                lambda_a,
                "not a configured derby",
                False,
                evidence_state=EVIDENCE_NOT_APPLICABLE,
            )
            return lambda_h, lambda_a
        lambda_h *= 1.03
        lambda_a *= 1.03
        self._record(moves, 6, "Derby", before_h, before_a, lambda_h, lambda_a, "derby/open-game uplift", True)
        return lambda_h, lambda_a

    def _layer_injuries(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
        kickoff: str,
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        home_news = self._team_news_items(home)
        away_news = self._team_news_items(away)
        home_depth = self._squad_depth_note(home)
        away_depth = self._squad_depth_note(away)
        depth_note = "; ".join(note for note in (home_depth, away_depth) if note)
        news_feed_fresh = all(self._team_news_feed_fresh(kickoff, team=team) for team in (home, away))
        if not home_news and not away_news:
            self._record(
                moves,
                7,
                "Injuries",
                before_h,
                before_a,
                lambda_h,
                lambda_a,
                (
                    "fresh team-news feed found no active injury/suspension rows"
                    if news_feed_fresh
                    else "team-news coverage is missing or stale"
                )
                + (f"; {depth_note}" if depth_note else "; squad-depth coverage missing"),
                False,
                evidence_state=EVIDENCE_NO_SIGNAL if news_feed_fresh else EVIDENCE_MISSING,
            )
            return lambda_h, lambda_a

        home_mult = 1.0
        away_mult = 1.0
        for item in home_news:
            weight = self._news_weight(item)
            if self._news_attack_hit(item):
                home_mult -= 0.025 * weight
            elif self._news_defense_hit(item):
                away_mult += 0.020 * weight
            else:
                home_mult -= 0.012 * weight
        for item in away_news:
            weight = self._news_weight(item)
            if self._news_attack_hit(item):
                away_mult -= 0.025 * weight
            elif self._news_defense_hit(item):
                home_mult += 0.020 * weight
            else:
                away_mult -= 0.012 * weight
        home_mult = self._clamp(home_mult, 0.86, 1.08)
        away_mult = self._clamp(away_mult, 0.86, 1.08)
        lambda_h *= home_mult
        lambda_a *= away_mult
        self._record(
            moves,
            7,
            "Injuries",
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            f"{home}: {len(home_news)} rows, {away}: {len(away_news)} rows"
            + (f"; {depth_note}" if depth_note else "; squad-depth coverage missing"),
            active=abs(home_mult - 1.0) >= 0.005 or abs(away_mult - 1.0) >= 0.005,
            evidence_state=None if news_feed_fresh else EVIDENCE_MISSING,
        )
        return lambda_h, lambda_a

    def _layer_competition_fatigue(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
        kickoff: str,
        terms: Iterable[Tuple[str, float, str]],
        layer_no: int,
        layer_name: str,
        rest_blocked: Dict[str, bool],
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        home_mult, home_note = self._recent_competition_multiplier(home, kickoff, terms, layer_name)
        away_mult, away_note = self._recent_competition_multiplier(away, kickoff, terms, layer_name)
        if home_note:
            rest_blocked["home"] = True
        if away_note:
            rest_blocked["away"] = True
        lambda_h *= home_mult
        lambda_a *= away_mult
        notes = [note for note in (home_note, away_note) if note]
        coverage_confirmed = all(
            self._team_context(team).get("euro_schedule_checked" if layer_no == 8 else "cup_schedule_checked") is not None
            for team in (home, away)
        )
        self._record(
            moves,
            layer_no,
            layer_name,
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            "; ".join(notes) or f"no recent {layer_name.lower()} signal",
            active=bool(notes),
            evidence_state=None if notes or coverage_confirmed else EVIDENCE_MISSING,
        )
        return lambda_h, lambda_a

    def _layer_rest_days(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
        kickoff: str,
        blocked: Dict[str, bool],
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        home_last = self._last_match_time(home, kickoff)
        away_last = self._last_match_time(away, kickoff)
        home_mult, home_note = self._rest_multiplier(home, kickoff, blocked.get("home", False))
        away_mult, away_note = self._rest_multiplier(away, kickoff, blocked.get("away", False))
        lambda_h *= home_mult
        lambda_a *= away_mult
        notes = [note for note in (home_note, away_note) if note]
        active = abs(home_mult - 1.0) >= 0.005 or abs(away_mult - 1.0) >= 0.005
        self._record(
            moves,
            10,
            "Rest days",
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            "; ".join(notes) or "no rest-days signal",
            active=active,
            evidence_state=EVIDENCE_MISSING if home_last is None or away_last is None else None,
        )
        return lambda_h, lambda_a

    def _layer_rotation(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
        kickoff: str,
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        home_mult, home_note = self._rotation_multiplier(home, kickoff)
        away_mult, away_note = self._rotation_multiplier(away, kickoff)
        lambda_h *= home_mult
        lambda_a *= away_mult
        notes = [note for note in (home_note, away_note) if note]
        rotation_reviewed = all("rotation_risk" in self._team_context(team) for team in (home, away))
        self._record(
            moves,
            11,
            "Rotation",
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            "; ".join(notes) or "no rotation trigger",
            active=bool(notes),
            evidence_state=None if notes or rotation_reviewed else EVIDENCE_MISSING,
        )
        return lambda_h, lambda_a

    def _layer_luck_regression(
        self,
        moves: List[LayerMove],
        lambda_h: float,
        lambda_a: float,
        home: str,
        away: str,
        kickoff: str,
    ) -> Tuple[float, float]:
        before_h, before_a = lambda_h, lambda_a
        home_mult, home_note = self._luck_multiplier(home, kickoff)
        away_mult, away_note = self._luck_multiplier(away, kickoff)
        lambda_h *= home_mult
        lambda_a *= away_mult
        notes = [note for note in (home_note, away_note) if note]
        recent_covered = all(
            int(self._team_stats(team, kickoff, limit=6, days=120).get("played") or 0) >= 4
            and int(self._team_stats(team, kickoff, limit=40, days=365).get("played") or 0) >= 8
            for team in (home, away)
        )
        self._record(
            moves,
            12,
            "Luck regression",
            before_h,
            before_a,
            lambda_h,
            lambda_a,
            "; ".join(notes) or "no recent-vs-season luck gap",
            active=bool(notes),
            evidence_state=None if recent_covered else EVIDENCE_MISSING,
        )
        return lambda_h, lambda_a

    def _form_lambda(
        self,
        team: str,
        opponent: str,
        side: str,
        league: str,
        kickoff: str,
        fallback: float,
        days: int = 120,
    ) -> float:
        team_stats = self._team_stats(team, kickoff, limit=6, days=days)
        opp_stats = self._team_stats(opponent, kickoff, limit=6, days=days)
        league_avg = self._league_averages(league, kickoff)
        team_for = team_stats.get("gf_avg")
        opp_against = opp_stats.get("ga_avg")
        if team_for is None and opp_against is None:
            return fallback
        baseline = league_avg["home_goals"] if side == "home" else league_avg["away_goals"]
        pieces = [value for value in (team_for, opp_against) if value is not None]
        form = sum(pieces) / len(pieces) if pieces else baseline
        return self._clamp((0.75 * form) + (0.25 * baseline), self.lambda_min, self.lambda_max)

    def _diverges(self, base: float, form: float) -> bool:
        if base <= 0:
            return False
        return abs(form - base) / base > self.rolling_threshold

    def _finishing_multiplier(self, team: str, kickoff: str) -> Tuple[float, str]:
        context = self._team_context(team)
        goals_for = self._number(context.get("goals_for_90") or context.get("goals_for"))
        xg_for = self._number(context.get("xg_for_90") or context.get("xg_for"))
        if goals_for and xg_for and xg_for > 0:
            ratio = goals_for / xg_for
            if ratio >= 1.20:
                return 0.98, f"{team} clinical finishing regresses ({ratio:.2f} goals/xG)"
            if ratio <= 0.80:
                return 1.02, f"{team} wasteful finishing rebounds ({ratio:.2f} goals/xG)"
            return 1.0, ""

        recent = self._team_stats(team, kickoff, limit=6, days=120)
        if int(recent.get("played") or 0) < 4:
            recent = self._team_stats(team, kickoff, limit=6, days=365)
        season = self._team_stats(team, kickoff, limit=40, days=365)
        if recent.get("played", 0) < 4 or season.get("played", 0) < 8:
            return 1.0, ""
        gap = float(recent["gf_avg"] or 0) - float(season["gf_avg"] or 0)
        if gap >= 0.45:
            return 0.98, f"{team} recent finishing hot, mean-revert ({gap:+.2f}/g)"
        if gap <= -0.45:
            return 1.02, f"{team} recent finishing cold, rebound ({gap:+.2f}/g)"
        return 1.0, ""

    def _motivation_multiplier(self, team: str, league: str, kickoff: str) -> Tuple[float, str]:
        context = self._team_context(team)
        manual = str(context.get("motivation") or "").lower()
        manual_map = {
            "title_race": (1.03, "title-race motivation"),
            "title race": (1.03, "title-race motivation"),
            "relegation": (1.035, "relegation-pressure motivation"),
            "relegation_fight": (1.035, "relegation-pressure motivation"),
            "european_spot": (1.025, "European-spot motivation"),
            "cl_race": (1.025, "CL-race motivation"),
            "cup_final": (1.02, "cup-final motivation"),
            "clinched": (0.97, "goal already clinched"),
            "safe": (0.985, "low-table-pressure spot"),
            "midtable": (0.99, "mid-table motivation drag"),
        }
        if manual in manual_map:
            mult, note = manual_map[manual]
            return mult, f"{team} {note}"

        table = self._league_table(league, kickoff)
        item = table.get(team)
        if not item or item["played"] < 6:
            return 1.0, ""
        teams = max(len(table), 1)
        if item["rank"] <= 5:
            return 1.015, f"{team} top-table motivation"
        if item["rank"] >= teams - 4:
            return 1.02, f"{team} relegation-pressure motivation"
        return 1.0, ""

    def _manager_multiplier(self, team: str) -> Tuple[float, str]:
        context = self._team_context(team)
        value = str(context.get("manager_bounce") or context.get("manager") or "").lower()
        if value in ("positive", "new_positive", "bounce", "good"):
            return 1.03, f"{team} positive manager bounce"
        if value in ("negative", "transition", "bad"):
            return 0.97, f"{team} manager transition drag"
        return 1.0, ""

    def _is_derby(self, home: str, away: str) -> bool:
        pair = tuple(sorted((home, away)))
        return pair in COMMON_DERBIES or pair in self.extra_derbies

    def _recent_competition_multiplier(
        self,
        team: str,
        kickoff: str,
        terms: Iterable[Tuple[str, float, str]],
        label: str,
    ) -> Tuple[float, str]:
        context = self._team_context(team)
        manual_key = "euro_fatigue" if "Euro" in label else "cup_fatigue"
        if context.get(manual_key):
            mult = 0.95 if "Euro" in label else 0.965
            return mult, f"{team} manual {label.lower()} flag"

        target = parse_kickoff_utc(kickoff)
        if target is None:
            return 1.0, ""
        best = None
        for row in self._team_matches(team):
            row_time = row.get("_kickoff_utc") or parse_kickoff_utc(str(row.get("kickoff") or ""))
            if row_time is None or row_time >= target:
                continue
            delta_days = (target - row_time).total_seconds() / 86400
            if delta_days > 5:
                continue
            text = f"{row.get('league') or ''} {row.get('match_id') or ''}".lower()
            for term, mult, term_label in terms:
                if term in text:
                    if best is None or delta_days < best[0]:
                        best = (delta_days, mult, term_label)
        if best is None:
            return 1.0, ""
        days, mult, term_label = best
        return mult, f"{team} {term_label} match {days:.0f}d ago"

    def _rest_multiplier(self, team: str, kickoff: str, blocked: bool) -> Tuple[float, str]:
        last = self._last_match_time(team, kickoff)
        if last is None:
            return 1.0, ""
        target = parse_kickoff_utc(kickoff)
        if target is None:
            return 1.0, ""
        days = (target - last).total_seconds() / 86400
        if blocked:
            return 1.0, f"{team} rest-days blocked by euro/cup fatigue layer"
        if days <= 3:
            return 0.96, f"{team} short rest ({days:.0f}d)"
        if days <= 4:
            return 0.98, f"{team} limited rest ({days:.0f}d)"
        if days >= 8:
            return 1.01, f"{team} full rest ({days:.0f}d)"
        return 1.0, ""

    def _rotation_multiplier(self, team: str, kickoff: str) -> Tuple[float, str]:
        context = self._team_context(team)
        risk = str(context.get("rotation_risk") or "").lower()
        if risk in ("high", "true", "yes"):
            return 0.95, f"{team} manual high rotation risk"
        if risk in ("medium", "moderate"):
            return 0.97, f"{team} manual rotation risk"

        target = parse_kickoff_utc(kickoff)
        if target is None:
            return 1.0, ""
        for row in self._team_all_fixtures(team):
            row_time = row.get("_kickoff_utc") or parse_kickoff_utc(str(row.get("kickoff") or ""))
            if row_time is None or row_time <= target:
                continue
            delta_days = (row_time - target).total_seconds() / 86400
            if delta_days > 5:
                continue
            text = f"{row.get('league') or ''} {row.get('match_id') or ''}".lower()
            is_euro_or_cup = any(term in text for term, _, _ in EURO_TERMS + CUP_TERMS)
            is_knockout = any(term in text for term in KNOCKOUT_TERMS)
            if is_euro_or_cup and is_knockout:
                return 0.95, f"{team} rotation risk before knockout fixture in {delta_days:.0f}d"
            if is_euro_or_cup:
                return 0.97, f"{team} rotation risk before euro/cup fixture in {delta_days:.0f}d"
        return 1.0, ""

    def _luck_multiplier(self, team: str, kickoff: str) -> Tuple[float, str]:
        recent = self._team_stats(team, kickoff, limit=6, days=120)
        season = self._team_stats(team, kickoff, limit=40, days=365)
        if recent.get("played", 0) < 4 or season.get("played", 0) < 8:
            return 1.0, ""
        recent_gd = float(recent["gd_avg"] or 0)
        season_gd = float(season["gd_avg"] or 0)
        gap = recent_gd - season_gd
        if gap >= 0.55:
            return 0.97, f"{team} positive goal-diff luck regresses ({gap:+.2f}/g)"
        if gap <= -0.55:
            return 1.03, f"{team} negative goal-diff luck rebounds ({gap:+.2f}/g)"
        return 1.0, ""

    def _team_stats(self, team: str, kickoff: str, limit: int, days: int) -> Dict:
        key = ("team_stats", team, kickoff[:16], limit, days, self._current_match_key)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]

        target = parse_kickoff_utc(kickoff)
        cutoff_ts = target.timestamp() - days * 86400 if target else None
        rows = []
        for row in self._team_matches(team):
            if self._is_current_fixture_duplicate(row):
                continue
            row_time = row.get("_kickoff_utc") or parse_kickoff_utc(str(row.get("kickoff") or ""))
            if row_time is None:
                continue
            if target and row_time >= target:
                continue
            if cutoff_ts and row_time.timestamp() < cutoff_ts:
                continue
            rows.append((row_time, row))
        rows.sort(key=lambda item: item[0], reverse=True)
        rows = rows[:limit]

        gf = ga = points = 0
        for _, row in rows:
            hg = int(row.get("home_goals") or 0)
            ag = int(row.get("away_goals") or 0)
            if row.get("home_team") == team:
                team_goals, opp_goals = hg, ag
            else:
                team_goals, opp_goals = ag, hg
            gf += team_goals
            ga += opp_goals
            if team_goals > opp_goals:
                points += 3
            elif team_goals == opp_goals:
                points += 1

        played = len(rows)
        result = {
            "played": played,
            "gf_avg": gf / played if played else None,
            "ga_avg": ga / played if played else None,
            "gd_avg": (gf - ga) / played if played else None,
            "ppg": points / played if played else None,
        }
        self._cache[key] = result
        return result

    def _history_rows(self, league=None) -> List[Dict]:
        """Use one immutable completed-history snapshot per calculation engine."""
        key = ('completed_history', league)
        if key in self._cache:
            return self._cache[key]
        if league is not None:
            rows = [r for r in self._history_rows() if r['league'] == league]
            self._cache[key] = rows
            return rows
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            """
            SELECT match_id, home_team, away_team, league, kickoff, home_goals, away_goals, status
            FROM matches
            WHERE home_goals IS NOT NULL
              AND away_goals IS NOT NULL
              AND status = 'completed'
            """,
        )
        rows = reconcile_history(c.fetchall())
        conn.close()
        for row in rows:
            row['_kickoff_utc'] = parse_kickoff_utc(str(row.get('kickoff') or ''))
        self._cache[key] = rows
        return rows

    def _team_matches(self, team: str) -> List[Dict]:
        from utils.team_normalizer import normalize_team_name
        key = ('team_matches', team)
        if key not in self._cache:
            canonical = normalize_team_name(team)
            self._cache[key] = [r for r in self._history_rows() if canonical in (r['home_team'], r['away_team'])]
        return self._cache[key]

    def _team_all_fixtures(self, team: str) -> List[Dict]:
        key = ("team_all_fixtures", team)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            """
            SELECT match_id, home_team, away_team, league, kickoff, home_goals, away_goals, status
            FROM matches
            WHERE home_team = ? OR away_team = ?
            """,
            (team, team),
        )
        rows = [dict(row) for row in c.fetchall()]
        conn.close()
        self._cache[key] = rows
        return rows

    def _last_match_time(self, team: str, kickoff: str) -> Optional[datetime]:
        target = parse_kickoff_utc(kickoff)
        if target is None:
            return None
        times = []
        for row in self._team_matches(team):
            if self._is_current_fixture_duplicate(row):
                continue
            row_time = row.get("_kickoff_utc") or parse_kickoff_utc(str(row.get("kickoff") or ""))
            if row_time and row_time < target:
                times.append(row_time)
        return max(times) if times else None

    def _is_current_fixture_duplicate(self, row) -> bool:
        if not self._current_match_key:
            return False
        def value(key: str):
            if hasattr(row, "get"):
                return row.get(key)
            try:
                return row[key]
            except (IndexError, KeyError):
                return None

        kickoff = str(value("kickoff") or "")
        row_time = parse_kickoff_utc(kickoff)
        row_day = row_time.date().isoformat() if row_time else kickoff[:10]
        return (
            str(value("home_team") or ""),
            str(value("away_team") or ""),
            row_day,
        ) == self._current_match_key

    def _league_averages(self, league: str, kickoff: str) -> Dict[str, float]:
        key = ("league_avg", league, kickoff[:10], self._current_match_key)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        target = parse_kickoff_utc(kickoff)
        home_goals = []
        away_goals = []
        for row in self._history_rows(league):
            if self._is_current_fixture_duplicate(row):
                continue
            row_time = row["_kickoff_utc"]
            if target and row_time and row_time >= target:
                continue
            home_goals.append(float(row["home_goals"]))
            away_goals.append(float(row["away_goals"]))
        result = {
            "home_goals": sum(home_goals) / len(home_goals) if home_goals else 1.45,
            "away_goals": sum(away_goals) / len(away_goals) if away_goals else 1.15,
        }
        self._cache[key] = result
        return result

    def _league_table(self, league: str, kickoff: str) -> Dict[str, Dict]:
        key = ("league_table", league, kickoff[:10], self._current_match_key)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        target = parse_kickoff_utc(kickoff)
        rows = []
        for row in self._history_rows(league):
            if self._is_current_fixture_duplicate(row):
                continue
            row_time = row["_kickoff_utc"]
            if target and row_time and row_time >= target:
                continue
            rows.append(dict(row))

        table: Dict[str, Dict] = {}
        for row in rows:
            home, away = row["home_team"], row["away_team"]
            table.setdefault(home, {"played": 0, "points": 0, "gf": 0, "ga": 0, "rank": 99})
            table.setdefault(away, {"played": 0, "points": 0, "gf": 0, "ga": 0, "rank": 99})
            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            table[home]["played"] += 1
            table[away]["played"] += 1
            table[home]["gf"] += hg
            table[home]["ga"] += ag
            table[away]["gf"] += ag
            table[away]["ga"] += hg
            if hg > ag:
                table[home]["points"] += 3
            elif ag > hg:
                table[away]["points"] += 3
            else:
                table[home]["points"] += 1
                table[away]["points"] += 1
        ranked = sorted(
            table.items(),
            key=lambda item: (item[1]["points"], item[1]["gf"] - item[1]["ga"], item[1]["gf"]),
            reverse=True,
        )
        for rank, (_, item) in enumerate(ranked, 1):
            item["rank"] = rank
            item["ppg"] = item["points"] / item["played"] if item["played"] else 0.0
        self._cache[key] = table
        return table

    def _elo_snapshot(self, league: str, kickoff: str) -> Tuple[Dict[str, float], Dict[str, float]]:
        key = ("elo", league, kickoff[:10], self._current_match_key)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        target = parse_kickoff_utc(kickoff)
        rows = []
        for row in self._history_rows(league):
            if self._is_current_fixture_duplicate(row):
                continue
            row_time = row["_kickoff_utc"]
            if row_time is None:
                continue
            if target and row_time >= target:
                continue
            rows.append((row_time, dict(row)))
        rows.sort(key=lambda item: item[0])

        ratings: Dict[str, float] = {}
        opponents: Dict[str, List[float]] = {}
        k_factor = 22.0
        home_adv = 65.0
        for _, row in rows:
            home, away = row["home_team"], row["away_team"]
            ratings.setdefault(home, 1500.0)
            ratings.setdefault(away, 1500.0)
            opponents.setdefault(home, [])
            opponents.setdefault(away, [])
            home_pre = ratings[home]
            away_pre = ratings[away]
            expected_home = 1.0 / (1.0 + 10 ** ((away_pre - (home_pre + home_adv)) / 400.0))
            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            if hg > ag:
                actual_home = 1.0
            elif hg == ag:
                actual_home = 0.5
            else:
                actual_home = 0.0
            ratings[home] = home_pre + k_factor * (actual_home - expected_home)
            ratings[away] = away_pre + k_factor * ((1.0 - actual_home) - (1.0 - expected_home))
            opponents[home].append(away_pre)
            opponents[away].append(home_pre)

        sos = {
            team: sum(values[-8:]) / len(values[-8:])
            for team, values in opponents.items()
            if values
        }
        self._cache[key] = (ratings, sos)
        return ratings, sos

    def _team_news_items(self, team: str) -> List[Dict]:
        key = ("team_news", team)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='team_news'")
        if not c.fetchone():
            conn.close()
            self._cache[key] = []
            return []
        c.execute(
            """
            SELECT *
            FROM team_news
            WHERE team = ?
            """,
            (team,),
        )
        from utils.player_news import injury_news, player_key
        rows = injury_news([dict(row) for row in c.fetchall()])
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='squad_players'")
        if c.fetchone():
            c.execute("SELECT player_name, position FROM squad_players WHERE team = ?", (team,))
            positions = {
                player_key(team, player_name): position
                for player_name, position in c.fetchall()
                if player_name
            }
            for row in rows:
                row["position"] = positions.get(player_key(team, row.get("player")), "")
        conn.close()
        self._cache[key] = rows
        return rows

    def _squad_depth_note(self, team: str) -> str:
        key = ("squad_depth", team)
        if key in self._cache:
            return str(self._cache[key])
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='squad_depth'")
        if not c.fetchone():
            conn.close()
            self._cache[key] = ""
            return ""
        c.execute(
            """
            SELECT goalkeepers, defenders, midfielders, forwards, total_players
            FROM squad_depth
            WHERE team = ?
            LIMIT 1
            """,
            (team,),
        )
        row = c.fetchone()
        conn.close()
        if not row:
            note = ""
        else:
            note = (
                f"{team} depth {int(row['total_players'])} "
                f"({int(row['goalkeepers'])}G/{int(row['defenders'])}D/"
                f"{int(row['midfielders'])}M/{int(row['forwards'])}F)"
            )
        self._cache[key] = note
        return note

    def _team_context(self, team: str) -> Dict:
        teams = self._manual_context.get("teams", {})
        return teams.get(team, {})

    def _has_xg_context(self, team: str) -> bool:
        context = self._team_context(team)
        goals_for = self._number(context.get("goals_for_90") or context.get("goals_for"))
        xg_for = self._number(context.get("xg_for_90") or context.get("xg_for"))
        return goals_for is not None and xg_for is not None and xg_for > 0

    def _has_finishing_proxy(self, team: str, kickoff: str) -> bool:
        recent = self._team_stats(team, kickoff, limit=6, days=120)
        if int(recent.get("played") or 0) < 4:
            recent = self._team_stats(team, kickoff, limit=6, days=365)
        season = self._team_stats(team, kickoff, limit=40, days=365)
        return int(recent.get("played") or 0) >= 4 and int(season.get("played") or 0) >= 8

    def _team_news_feed_fresh(self, kickoff: str, max_age_days: int = 7, team: str = None) -> bool:
        from utils.player_news import news_time, reconcile_news
        key = ("team_news_fresh", kickoff, max_age_days, team)
        if key in self._cache:
            return bool(self._cache[key])
        target = parse_kickoff_utc(kickoff)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='team_news'")
        if not c.fetchone():
            conn.close()
            return False
        c.execute("SELECT * FROM team_news" + (" WHERE team = ?" if team else ""), (team,) if team else ())
        rows = reconcile_news([dict(row) for row in c.fetchall()])
        conn.close()
        if not rows or target is None:
            self._cache[key] = False
            return False
        fresh = all(0 <= (target - news_time(row)).total_seconds() / 86400 <= max_age_days for row in rows)
        self._cache[key] = fresh
        return fresh

    def _load_manual_context(self) -> Dict:
        if not self.manual_context_path.exists():
            return {}
        try:
            with self.manual_context_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _news_weight(self, item: Dict) -> float:
        status = str(item.get("status") or "").lower()
        reason = str(item.get("reason") or "").lower()
        weight = 0.5 if status == "doubtful" else 1.0
        if "suspended" in status or "suspended" in reason:
            weight += 0.2
        return weight

    def _person_key(self, value: object) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(char for char in text if not unicodedata.combining(char))
        return re.sub(r"[^a-z0-9]+", "", text.casefold())

    def _news_attack_hit(self, item: Dict) -> bool:
        text = " ".join(
            str(item.get(field) or "")
            for field in ("player", "status", "reason", "position")
        ).lower()
        return any(word in text for word in ("striker", "forward", "winger", "attacker", "playmaker", "top scorer", "scorer", "goal"))

    def _news_defense_hit(self, item: Dict) -> bool:
        text = " ".join(
            str(item.get(field) or "")
            for field in ("player", "status", "reason", "position")
        ).lower()
        return any(word in text for word in ("defender", "defense", "centre-back", "center-back", "full-back", "goalkeeper", "keeper", "gk"))

    def _probabilities(self, lambda_h: float, lambda_a: float) -> Dict[str, float]:
        max_goals = 10
        matrix = {}
        for home_goals in range(max_goals + 1):
            for away_goals in range(max_goals + 1):
                base = self._poisson_pmf(home_goals, lambda_h) * self._poisson_pmf(away_goals, lambda_a)
                correction = self._dc_correction(home_goals, away_goals, lambda_h, lambda_a, self.rho)
                matrix[(home_goals, away_goals)] = max(0.0, base * correction)
        total = sum(matrix.values())
        if total <= 0:
            total = 1.0
        for key in list(matrix.keys()):
            matrix[key] /= total

        prob_home = sum(prob for (h, a), prob in matrix.items() if h > a)
        prob_draw = sum(prob for (h, a), prob in matrix.items() if h == a)
        prob_away = sum(prob for (h, a), prob in matrix.items() if h < a)
        prob_over_1_5 = sum(prob for (h, a), prob in matrix.items() if h + a > 1.5)
        prob_over_2_5 = sum(prob for (h, a), prob in matrix.items() if h + a > 2.5)
        prob_under_2_5 = sum(prob for (h, a), prob in matrix.items() if h + a <= 2.5)
        btts_raw = sum(prob for (h, a), prob in matrix.items() if h > 0 and a > 0)
        prob_btts = btts_raw  # 0-0 remains a losing outcome in the denominator.
        return {
            "prob_home_win": round(prob_home, 3),
            "prob_draw": round(prob_draw, 3),
            "prob_away_win": round(prob_away, 3),
            "prob_over_1_5": round(prob_over_1_5, 3),
            "prob_over_2_5": round(prob_over_2_5, 3),
            "prob_under_2_5": round(prob_under_2_5, 3),
            "prob_btts_yes": round(prob_btts, 3),
        }

    def _record(
        self,
        moves: List[LayerMove],
        layer_no: int,
        layer_name: str,
        before_h: float,
        before_a: float,
        after_h: float,
        after_a: float,
        note: str,
        active: bool,
        evidence_state: Optional[str] = None,
    ) -> None:
        if evidence_state is None:
            evidence_state = EVIDENCE_ACTIVE if active else EVIDENCE_NO_SIGNAL
        moves.append(
            LayerMove(
                layer_no=layer_no,
                layer_name=layer_name,
                home_before=float(before_h),
                away_before=float(before_a),
                home_after=float(after_h),
                away_after=float(after_a),
                note=note,
                active=active,
                evidence_state=evidence_state,
            )
        )

    def _cap_lambda(self, value: float) -> float:
        return self._clamp(value, self.lambda_min, self.lambda_max)

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _number(value) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _poisson_pmf(goals: int, expected: float) -> float:
        return math.exp(-expected) * (expected ** goals) / math.factorial(goals)

    @staticmethod
    def _dc_correction(home_goals: int, away_goals: int, lambda_h: float, lambda_a: float, rho: float) -> float:
        if home_goals == 0 and away_goals == 0:
            return 1 - lambda_h * lambda_a * rho
        if home_goals == 0 and away_goals == 1:
            return 1 + lambda_h * rho
        if home_goals == 1 and away_goals == 0:
            return 1 + lambda_a * rho
        if home_goals == 1 and away_goals == 1:
            return 1 - rho
        return 1.0


def save_prediction_layers(match_id: str, layers: List[LayerMove]) -> None:
    """Persist the per-match layer audit for reports and debugging."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM prediction_adjustment_layers WHERE match_id = ?", (match_id,))
    c.executemany(
        """
        INSERT INTO prediction_adjustment_layers
        (match_id, layer_no, layer_name, home_before, away_before, home_after, away_after, note, active, evidence_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [layer.as_row(match_id) for layer in layers],
    )
    conn.commit()
    conn.close()
