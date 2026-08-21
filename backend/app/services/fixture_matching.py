from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterable

from app.schemas.forebet import FixtureMatchResult, FixtureMatchStatus, ForebetMatch, ForebetPredictionResult, SportyBetEvent

KICKOFF_TOLERANCE_SECONDS = 15 * 60


def normalize_team_name(name: str) -> str:
    value = unicodedata.normalize("NFKD", name or "")
    value = "".join(char for char in value if not unicodedata.combining(char)).casefold()
    value = re.sub(r"[\u2010-\u2015\-]", " ", value)
    value = re.sub(r"[&/.,'’()\[\]{}]", " ", value)
    value = re.sub(r"\b(?:fc|cf|sc|afc)\b", "", value)
    return " ".join(value.split())


def normalize_competition(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())


def _aware_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def draw_matches_by_date(matches: Iterable[ForebetMatch]) -> dict[date, list[ForebetMatch]]:
    grouped: dict[date, list[ForebetMatch]] = defaultdict(list)
    for match in matches:
        if match.predicted_result == ForebetPredictionResult.DRAW and match.kickoff is not None:
            grouped[_aware_datetime(match.kickoff).date()].append(match)
    return dict(sorted(grouped.items()))


def _competition_compatible(left: str | None, right: str | None) -> bool:
    a, b = normalize_competition(left), normalize_competition(right)
    if not a or not b:
        return True
    a_words, b_words = set(a.split()), set(b.split())
    return a == b or a_words <= b_words or b_words <= a_words


def match_forebet_fixtures(forebet_matches: Iterable[ForebetMatch], sportybet_events: Iterable[SportyBetEvent], tolerance_seconds: int = KICKOFF_TOLERANCE_SECONDS) -> list[FixtureMatchResult]:
    events = list(sportybet_events)
    results: list[FixtureMatchResult] = []
    for forebet in forebet_matches:
        if forebet.predicted_result != ForebetPredictionResult.DRAW:
            continue
        if forebet.kickoff is None:
            results.append(FixtureMatchResult(forebet_match=forebet, status=FixtureMatchStatus.UNMATCHED, reason="Forebet kickoff is missing"))
            continue
        f_time = _aware_datetime(forebet.kickoff)
        home, away = normalize_team_name(forebet.home_team), normalize_team_name(forebet.away_team)
        candidates: list[tuple[SportyBetEvent, bool, float]] = []
        for event in events:
            e_time = _aware_datetime(event.kickoff)
            delta = abs((f_time.astimezone(timezone.utc) - e_time.astimezone(timezone.utc)).total_seconds())
            if delta > tolerance_seconds or normalize_team_name(event.home_team) != home or normalize_team_name(event.away_team) != away:
                continue
            if not _competition_compatible(forebet.competition, event.competition):
                continue
            exact = forebet.home_team == event.home_team and forebet.away_team == event.away_team and delta == 0 and normalize_competition(forebet.competition) == normalize_competition(event.competition)
            candidates.append((event, exact, 1.0 if exact else max(0.8, 0.9 - delta / max(tolerance_seconds, 1) * 0.1)))
        if len(candidates) == 1:
            event, exact, confidence = candidates[0]
            results.append(FixtureMatchResult(forebet_match=forebet, status=FixtureMatchStatus.MATCHED_EXACT if exact else FixtureMatchStatus.MATCHED_NORMALIZED, matching_method="exact" if exact else "normalized", matching_confidence=confidence, sportybet_event=event))
        elif len(candidates) > 1:
            results.append(FixtureMatchResult(forebet_match=forebet, status=FixtureMatchStatus.AMBIGUOUS, candidates=[candidate[0] for candidate in candidates], reason="Multiple SportyBet fixtures satisfy the identity constraints"))
        else:
            results.append(FixtureMatchResult(forebet_match=forebet, status=FixtureMatchStatus.UNMATCHED, reason="No SportyBet fixture matched home/away/date/time/competition constraints"))
    return results
