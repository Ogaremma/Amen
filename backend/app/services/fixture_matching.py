from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.forebet import (
    FixtureMatchResult,
    FixtureMatchStatus,
    ForebetMatch,
    ForebetPredictionResult,
    SportyBetEvent,
)

KICKOFF_TOLERANCE_SECONDS = 15 * 60
try:
    _LAGOS = ZoneInfo("Africa/Lagos")
except ZoneInfoNotFoundError:
    # Africa/Lagos is fixed UTC+1 and does not observe daylight saving time.
    _LAGOS = timezone(timedelta(hours=1), name="Africa/Lagos")


def normalize_team_name(name: str) -> str:
    value = unicodedata.normalize("NFKD", name or "")
    value = "".join(
        char for char in value if not unicodedata.combining(char)
    ).casefold()
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"[\u2010-\u2015\-]", " ", value)
    value = re.sub(r"[&/.,'’()\[\]{}]", " ", value)
    value = re.sub(r"\b(?:fc|cf|sc|afc|cd|ca|de|panama)\b", "", value)
    value = re.sub(r"\bplaza\s+amador\s+city\b", "plaza amador", value)
    return " ".join(value.split())


def normalize_competition(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())


def _aware_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def draw_matches_by_date(
    matches: Iterable[ForebetMatch],
) -> dict[date, list[ForebetMatch]]:
    grouped: dict[date, list[ForebetMatch]] = defaultdict(list)
    for match in matches:
        if (
            match.predicted_result == ForebetPredictionResult.DRAW
            and match.kickoff is not None
        ):
            grouped[_aware_datetime(match.kickoff).date()].append(match)
    return dict(sorted(grouped.items()))


def _competition_compatible(left: str | None, right: str | None) -> bool:
    a, b = normalize_competition(left), normalize_competition(right)
    if not a or not b:
        return True
    a_words, b_words = set(a.split()), set(b.split())
    return a == b or a_words <= b_words or b_words <= a_words


def _team_similarity(left: str, right: str) -> float:
    a, b = normalize_team_name(left), normalize_team_name(right)
    if a == b:
        return 1.0
    at, bt = set(a.split()), set(b.split())
    token = len(at & bt) / max(len(at | bt), 1)
    return max(SequenceMatcher(None, a, b).ratio(), token)


_ADVANCED_TEAM_ALIASES = {
    "junior barranquilla": "junior",
    "cd junior fc": "junior",
    "luis angel firpo usulutan": "luis angel firpo",
    "platense municipal zacatecoluca": "platense",
}


def _advanced_team_similarity(left: str, right: str) -> float:
    left_norm = _ADVANCED_TEAM_ALIASES.get(
        normalize_team_name(left), normalize_team_name(left)
    )
    right_norm = _ADVANCED_TEAM_ALIASES.get(
        normalize_team_name(right), normalize_team_name(right)
    )
    return _team_similarity(left_norm, right_norm)


def _competition_similarity(left: str | None, right: str | None) -> float:
    a, b = normalize_competition(left), normalize_competition(right)
    if not a or not b:
        return 0.35
    if _competition_compatible(a, b):
        return 1.0
    colombia_markers = ("colomb", "dimayor")
    if any(marker in a for marker in colombia_markers) and any(
        marker in b for marker in colombia_markers
    ):
        return 0.85
    at, bt = set(a.split()), set(b.split())
    return len(at & bt) / max(len(at | bt), 1)


def _youth_level(value: str) -> str | None:
    text = normalize_team_name(value)
    found = re.search(r"\b(u\d{2}|\bii\b|\bb\b|reserve|women|ladies|feminine)\b", text)
    return found.group(1) if found else None


def _advanced_candidate(
    forebet: ForebetMatch, event: SportyBetEvent
) -> tuple[float, dict] | None:
    if event.sport_id and event.sport_id != "sr:sport:1":
        return None
    status = (event.match_status or "").strip().lower()
    if status in {
        "live",
        "in play",
        "started",
        "playing",
        "ended",
        "finished",
        "complete",
        "completed",
        "closed",
        "cancelled",
        "canceled",
    }:
        return None
    if _youth_level(forebet.home_team) != _youth_level(event.home_team) or _youth_level(
        forebet.away_team
    ) != _youth_level(event.away_team):
        return None
    fday = (
        forebet.kickoff
        if isinstance(forebet.kickoff, date)
        and not isinstance(forebet.kickoff, datetime)
        else _aware_datetime(forebet.kickoff).astimezone(_LAGOS).date()
    )
    eday = _aware_datetime(event.kickoff).astimezone(_LAGOS).date()
    if fday != eday:
        return None
    home_score = _advanced_team_similarity(forebet.home_team, event.home_team)
    away_score = _advanced_team_similarity(forebet.away_team, event.away_team)
    comp_score = _competition_similarity(forebet.competition, event.competition)
    delta = (
        abs(
            (
                _aware_datetime(forebet.kickoff).astimezone(timezone.utc)
                - _aware_datetime(event.kickoff).astimezone(timezone.utc)
            ).total_seconds()
        )
        / 3600
    )
    kickoff_score = max(0.0, 1.0 - min(delta, 12.0) / 12.0)
    score = (
        min(home_score, away_score) * 0.55
        + ((home_score + away_score) / 2) * 0.2
        + comp_score * 0.2
        + kickoff_score * 0.05
    )
    evidence = {
        "home_similarity": home_score,
        "away_similarity": away_score,
        "minimum_team_similarity": min(home_score, away_score),
        "average_team_similarity": (home_score + away_score) / 2,
        "competition_similarity": comp_score,
        "kickoff_delta_hours": delta,
        "same_lagos_date": True,
        "same_direction": True,
    }
    return score, evidence


def match_forebet_fixtures(
    forebet_matches: Iterable[ForebetMatch],
    sportybet_events: Iterable[SportyBetEvent],
    tolerance_seconds: int = KICKOFF_TOLERANCE_SECONDS,
) -> list[FixtureMatchResult]:
    events = list(sportybet_events)
    results: list[FixtureMatchResult] = []
    for forebet in forebet_matches:
        if forebet.predicted_result != ForebetPredictionResult.DRAW:
            continue
        if forebet.kickoff is None:
            results.append(
                FixtureMatchResult(
                    forebet_match=forebet,
                    status=FixtureMatchStatus.UNMATCHED,
                    reason="Forebet kickoff is missing",
                )
            )
            continue
        forebet_has_precise_time = isinstance(forebet.kickoff, datetime)
        f_time = _aware_datetime(forebet.kickoff)
        home, away = (
            normalize_team_name(forebet.home_team),
            normalize_team_name(forebet.away_team),
        )
        candidates: list[tuple[SportyBetEvent, bool, float]] = []
        fallback_used = False
        for event in events:
            e_time = _aware_datetime(event.kickoff)
            delta = abs(
                (
                    f_time.astimezone(timezone.utc) - e_time.astimezone(timezone.utc)
                ).total_seconds()
            )
            same_prediction_date = (
                forebet.kickoff == e_time.astimezone(_LAGOS).date()
                if not forebet_has_precise_time
                else True
            )
            if (
                (forebet_has_precise_time and delta > tolerance_seconds)
                or not same_prediction_date
                or normalize_team_name(event.home_team) != home
                or normalize_team_name(event.away_team) != away
            ):
                continue
            exact = (
                forebet_has_precise_time
                and forebet.home_team == event.home_team
                and forebet.away_team == event.away_team
                and delta == 0
                and normalize_competition(forebet.competition)
                == normalize_competition(event.competition)
            )
            confidence = (
                1.0
                if exact
                else (
                    0.9
                    if not forebet_has_precise_time
                    else max(0.8, 0.9 - delta / max(tolerance_seconds, 1) * 0.1)
                )
            )
            candidates.append((event, exact, confidence))
        compatible = [
            candidate
            for candidate in candidates
            if _competition_compatible(forebet.competition, candidate[0].competition)
        ]
        if compatible:
            candidates = compatible
        if not candidates and not forebet_has_precise_time:
            fallback: list[tuple[SportyBetEvent, bool, float]] = []
            for event in events:
                e_time = _aware_datetime(event.kickoff)
                same_date = (
                    forebet.kickoff == e_time.astimezone(_LAGOS).date()
                    if not forebet_has_precise_time
                    else f_time.astimezone(_LAGOS).date()
                    == e_time.astimezone(_LAGOS).date()
                )
                if not same_date:
                    continue
                home_score = _team_similarity(forebet.home_team, event.home_team)
                away_score = _team_similarity(forebet.away_team, event.away_team)
                if home_score < 0.86 or away_score < 0.86:
                    continue
                delta = abs(
                    (
                        f_time.astimezone(timezone.utc)
                        - e_time.astimezone(timezone.utc)
                    ).total_seconds()
                )
                context = _competition_compatible(
                    forebet.competition, event.competition
                ) or (forebet_has_precise_time and delta <= 6 * 3600)
                if not context:
                    continue
                fallback.append((event, False, min(home_score, away_score)))
            candidates = fallback
            fallback_used = bool(candidates)
        if not candidates and not forebet_has_precise_time:
            advanced = []
            for event in events:
                item = _advanced_candidate(forebet, event)
                if item:
                    advanced.append((event, item[0], item[1]))
            advanced.sort(key=lambda item: item[1], reverse=True)
            if advanced:
                best = advanced[0]
                second = advanced[1][1] if len(advanced) > 1 else 0.0
                margin = best[1] - second
                if (
                    best[1] >= 0.70
                    and margin >= 0.08
                    and best[2]["minimum_team_similarity"] >= 0.72
                    and best[2]["competition_similarity"] >= 0.25
                ):
                    event, confidence, evidence = best
                    results.append(
                        FixtureMatchResult(
                            forebet_match=forebet,
                            status=FixtureMatchStatus.MATCHED_NORMALIZED,
                            matching_method="advanced_evidence",
                            matching_confidence=confidence,
                            sportybet_event=event,
                            candidate_margin=margin,
                            **evidence,
                        )
                    )
                    continue
                if len(advanced) > 1 and best[1] >= 0.70 and margin < 0.08:
                    results.append(
                        FixtureMatchResult(
                            forebet_match=forebet,
                            status=FixtureMatchStatus.AMBIGUOUS,
                            candidates=[item[0] for item in advanced[:5]],
                            reason="Advanced evidence candidates are too close",
                            candidate_margin=margin,
                            **best[2],
                        )
                    )
                    continue
        if len(candidates) == 1:
            event, exact, confidence = candidates[0]
            method = (
                "exact" if exact else ("evidence" if fallback_used else "normalized")
            )
            results.append(
                FixtureMatchResult(
                    forebet_match=forebet,
                    status=FixtureMatchStatus.MATCHED_EXACT
                    if exact
                    else FixtureMatchStatus.MATCHED_NORMALIZED,
                    matching_method=method,
                    matching_confidence=confidence,
                    sportybet_event=event,
                )
            )
        elif len(candidates) > 1:
            results.append(
                FixtureMatchResult(
                    forebet_match=forebet,
                    status=FixtureMatchStatus.AMBIGUOUS,
                    candidates=[candidate[0] for candidate in candidates],
                    reason="Multiple SportyBet fixtures satisfy the identity constraints",
                )
            )
        else:
            results.append(
                FixtureMatchResult(
                    forebet_match=forebet,
                    status=FixtureMatchStatus.UNMATCHED,
                    reason="No SportyBet fixture matched home/away/date/time/competition constraints",
                )
            )
    return results
