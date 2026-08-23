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

KICKOFF_TOLERANCE_SECONDS = 60 * 60
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
    value = re.sub(r"\b(?:f\.?c\.?|c\.?f\.?|s\.?c\.?)\b", "", value)
    value = re.sub(r"\b(?:utd|united)\b", "united", value)
    value = re.sub(r"\bmanchester\b", "man", value)
    value = re.sub(r"\bsaint\b", "st", value)
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
            results.append(FixtureMatchResult(forebet_match=forebet, status=FixtureMatchStatus.UNMATCHED, reason="Forebet kickoff is missing")); continue
        ftime = _aware_datetime(forebet.kickoff)
        fday = ftime.astimezone(_LAGOS).date()
        scored: list[tuple[SportyBetEvent, float, dict]] = []
        for event in events:
            if event.sport_id and event.sport_id != "sr:sport:1":
                continue
            if (event.match_status or "").lower() in {"live", "started", "playing", "ended", "finished", "complete", "completed", "closed", "cancelled", "canceled"}:
                continue
            etime = _aware_datetime(event.kickoff)
            if etime.astimezone(_LAGOS).date() != fday:
                continue
            hs, aws = _advanced_team_similarity(forebet.home_team, event.home_team), _advanced_team_similarity(forebet.away_team, event.away_team)
            if hs < 0.68 or aws < 0.68:
                continue
            delta = abs((ftime.astimezone(timezone.utc) - etime.astimezone(timezone.utc)).total_seconds()) / 60
            if isinstance(forebet.kickoff, datetime) and delta > max(tolerance_seconds, 60 * 60) / 60:
                continue
            comp = _competition_similarity(forebet.competition, event.competition)
            score = min(hs, aws) * 0.55 + ((hs + aws) / 2) * 0.25 + comp * 0.10 + max(0, 1 - min(delta, 180) / 180) * 0.10
            scored.append((event, score, {"home_similarity": hs, "away_similarity": aws, "minimum_team_similarity": min(hs, aws), "average_team_similarity": (hs + aws) / 2, "competition_similarity": comp, "kickoff_delta_hours": delta / 60, "same_lagos_date": True, "same_direction": True}))
        scored.sort(key=lambda item: item[1], reverse=True)
        if not scored:
            results.append(FixtureMatchResult(forebet_match=forebet, status=FixtureMatchStatus.UNMATCHED, reason="No same-date directional SportyBet candidate")); continue
        best = scored[0]; second = scored[1][1] if len(scored) > 1 else 0
        margin = best[1] - second
        if best[1] < 0.68 or (len(scored) > 1 and margin < 0.06):
            results.append(FixtureMatchResult(forebet_match=forebet, status=FixtureMatchStatus.AMBIGUOUS if len(scored) > 1 else FixtureMatchStatus.UNMATCHED, candidates=[x[0] for x in scored[:5]], reason="Candidates lack sufficient evidence or separation", candidate_margin=margin, **best[2])); continue
        event, score, evidence = best
        exact = normalize_team_name(forebet.home_team) == normalize_team_name(event.home_team) and normalize_team_name(forebet.away_team) == normalize_team_name(event.away_team)
        raw_exact = forebet.home_team.casefold() == event.home_team.casefold() and forebet.away_team.casefold() == event.away_team.casefold() and evidence["kickoff_delta_hours"] == 0
        alias_used = normalize_team_name(forebet.home_team) in _ADVANCED_TEAM_ALIASES or normalize_team_name(forebet.away_team) in _ADVANCED_TEAM_ALIASES
        method = "exact" if raw_exact else ("normalized" if exact else ("advanced_evidence" if alias_used else "evidence"))
        status = FixtureMatchStatus.MATCHED_EXACT if raw_exact else (FixtureMatchStatus.MATCHED_NORMALIZED if score >= 0.82 else FixtureMatchStatus.MATCHED_FUZZY)
        results.append(FixtureMatchResult(forebet_match=forebet, status=status, matching_method=method, matching_confidence=score, sportybet_event=event, candidate_margin=margin, **evidence))
    return results
