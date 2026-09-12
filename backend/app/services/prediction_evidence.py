from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Protocol

from app.schemas.prediction_evidence import (
    EvidenceSourceMetadata,
    FootballAverageFeature,
    FootballFixtureContext,
    FootballFixtureEvidence,
    FootballMarketEvidence,
    FootballMatchResult,
    FootballRateFeature,
    FootballSeasonStats,
    FootballTeamForm,
    FootballVenueForm,
)
from app.schemas.prediction_evidence import FootballEvidenceCollection
from app.schemas.prediction_evidence import FootballEvidenceDataQuality
from app.schemas.sportybet_markets import OverOneHalfCandidate
from app.services.sportybet_markets import (
    OVER_ONE_HALF_MARKET_ID,
    OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE,
    OVER_ONE_HALF_MIN_ODDS,
    OVER_ONE_HALF_OUTCOME_ID,
    OVER_ONE_HALF_OUTCOME_LABEL,
    OVER_ONE_HALF_PRODUCT_ID,
    OVER_ONE_HALF_SPECIFIER,
)

CAPTURED_EVIDENCE_SOURCE = 'sportradar-public-widget-capture'
MATCH_INFO_FEED = 'match_info'
TEAM_LASTX_FEED = 'stats_team_lastx'
SEASON_UNIQUE_TEAM_STATS_FEED = 'stats_season_uniqueteamstats'


class FootballEvidenceError(ValueError):
    pass


class FootballEvidenceProvider(Protocol):
    def get_evidence(
        self,
        candidate: OverOneHalfCandidate,
        *,
        now: datetime | None = None,
    ) -> FootballFixtureEvidence | None:
        ...


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _epoch_datetime(value: Any) -> datetime | None:
    seconds = _to_int(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def normalize_team_name(value: Any) -> str:
    if value is None:
        return ''
    normalized = unicodedata.normalize('NFKC', str(value)).casefold()
    normalized = re.sub(r'[^a-z0-9]+', ' ', normalized)
    return ' '.join(normalized.split())


def sportradar_match_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'sr:match:(\d+)', value.strip())
    return match.group(1) if match else None


def sportradar_team_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'sr:competitor:(\d+)', value.strip())
    return match.group(1) if match else None


def _document(payload: Any, feed: str) -> tuple[Any, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get('doc'), list) or not payload['doc']:
        raise FootballEvidenceError(f'{feed} response has no document')
    document = payload['doc'][0]
    if not isinstance(document, dict) or not isinstance(document.get('data'), dict):
        raise FootballEvidenceError(f'{feed} response has no data')
    return document, document['data']


def _source_metadata(
    payload: Any,
    feed: str,
    *,
    source_identifier: str,
    fixture_identifier: str,
    team_id: str | None = None,
) -> EvidenceSourceMetadata:
    document, _ = _document(payload, feed)
    retrieved_at = _epoch_datetime(document.get('_dob'))
    max_age_seconds = _to_int(document.get('_maxage'))
    if retrieved_at is None or max_age_seconds is None or max_age_seconds <= 0:
        raise FootballEvidenceError(f'{feed} response has invalid freshness metadata')
    return EvidenceSourceMetadata(
        source=CAPTURED_EVIDENCE_SOURCE,
        feed=feed,
        retrieved_at=retrieved_at,
        max_age_seconds=max_age_seconds,
        source_identifier=source_identifier,
        fixture_identifier=fixture_identifier,
        team_id=team_id,
        note='Sanitized public widget capture; no authentication token preserved.',
    )


def _team_name(team: Any) -> str:
    return str(team.get('name', '')) if isinstance(team, dict) else ''


def _team_uid(team: Any) -> str | None:
    if not isinstance(team, dict):
        return None
    value = _to_int(team.get('uid'))
    return str(value) if value is not None else None


def _candidate_matches_fixture(
    candidate: OverOneHalfCandidate,
    *,
    match_id: str | None,
    home_team_id: str | None,
    away_team_id: str | None,
    home_team_name: str,
    away_team_name: str,
) -> bool:
    candidate_match_id = sportradar_match_id(candidate.fixture.event_id)
    if candidate_match_id is not None and match_id is not None:
        if candidate_match_id != match_id:
            return False
    elif not (
        normalize_team_name(candidate.fixture.home_team_name) == normalize_team_name(home_team_name)
        and normalize_team_name(candidate.fixture.away_team_name) == normalize_team_name(away_team_name)
    ):
        return False

    candidate_home_id = sportradar_team_id(candidate.fixture.home_team_id)
    candidate_away_id = sportradar_team_id(candidate.fixture.away_team_id)
    if candidate_home_id is not None and home_team_id is not None and candidate_home_id != home_team_id:
        return False
    if candidate_away_id is not None and away_team_id is not None and candidate_away_id != away_team_id:
        return False
    return True


def parse_match_info(
    payload: Any,
    candidate: OverOneHalfCandidate,
) -> FootballFixtureContext:
    _, data = _document(payload, MATCH_INFO_FEED)
    match = data.get('match')
    teams = match.get('teams') if isinstance(match, dict) else None
    if not isinstance(match, dict) or not isinstance(teams, dict):
        raise FootballEvidenceError('match_info response has no match teams')

    home = teams.get('home')
    away = teams.get('away')
    home_team_id = _team_uid(home)
    away_team_id = _team_uid(away)
    home_team_name = _team_name(home)
    away_team_name = _team_name(away)
    match_id_value = _to_int(match.get('_id'))
    if match_id_value is None or not home_team_id or not away_team_id:
        raise FootballEvidenceError('match_info response has incomplete fixture identity')

    match_id = str(match_id_value)
    if not _candidate_matches_fixture(
        candidate,
        match_id=match_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_team_name=home_team_name,
        away_team_name=away_team_name,
    ):
        raise FootballEvidenceError('candidate does not match match_info fixture')

    tournament = data.get('tournament')
    unique_tournament = data.get('uniquetournament')
    season = data.get('season')
    tournament_id = _to_int(tournament.get('_id')) if isinstance(tournament, dict) else None
    unique_tournament_id = (
        _to_int(unique_tournament.get('_id')) if isinstance(unique_tournament, dict) else None
    )
    season_id = _to_int(season.get('_id')) if isinstance(season, dict) else None
    competition = (
        unique_tournament.get('name') if isinstance(unique_tournament, dict) else None
    ) or (_team_name(tournament) if isinstance(tournament, dict) else None)

    return FootballFixtureContext(
        sportybet_event_id=candidate.fixture.event_id,
        sportybet_source_id=match_id,
        sportybet_home_team_id=candidate.fixture.home_team_id,
        sportybet_away_team_id=candidate.fixture.away_team_id,
        match_id=match_id,
        home_team_id=home_team_id,
        home_team_name=home_team_name,
        away_team_id=away_team_id,
        away_team_name=away_team_name,
        competition=competition,
        tournament_id=str(tournament_id) if tournament_id is not None else None,
        unique_tournament_id=str(unique_tournament_id) if unique_tournament_id is not None else None,
        season_id=str(season_id) if season_id is not None else None,
        source=_source_metadata(
            payload,
            MATCH_INFO_FEED,
            source_identifier=match_id,
            fixture_identifier=candidate.fixture.event_id,
        ),
    )


def _competition_name(data: dict[str, Any], match: dict[str, Any]) -> str | None:
    unique_tournaments = data.get('uniquetournaments')
    tournaments = data.get('tournaments')
    unique_tournament_id = _to_int(match.get('_utid'))
    tournament_id = _to_int(match.get('_tid'))
    if isinstance(unique_tournaments, dict) and unique_tournament_id is not None:
        tournament = unique_tournaments.get(str(unique_tournament_id))
        if isinstance(tournament, dict):
            return tournament.get('name')
    if isinstance(tournaments, dict) and tournament_id is not None:
        tournament = tournaments.get(str(tournament_id))
        if isinstance(tournament, dict):
            return tournament.get('name')
    return None


def _valid_score(value: Any) -> int | None:
    score = _to_int(value)
    return score if score is not None and score >= 0 else None


def _parse_team_matches(
    payload: Any,
    context: FootballFixtureContext,
    *,
    team_id: str,
    team_name: str,
    prediction_kickoff: datetime,
) -> list[FootballMatchResult]:
    _, data = _document(payload, TEAM_LASTX_FEED)
    raw_matches = data.get('matches')
    if not isinstance(raw_matches, list):
        raise FootballEvidenceError('stats_team_lastx response has no matches')

    source = _source_metadata(
        payload,
        TEAM_LASTX_FEED,
        source_identifier=team_id,
        fixture_identifier=context.sportybet_event_id,
        team_id=team_id,
    )
    matches_by_id: dict[str, FootballMatchResult] = {}

    for raw in raw_matches:
        if not isinstance(raw, dict) or raw.get('postponed') is True or raw.get('canceled') is True:
            continue
        match_id_value = _to_int(raw.get('_id'))
        teams = raw.get('teams')
        result = raw.get('result')
        time = raw.get('time')
        if (
            match_id_value is None
            or not isinstance(teams, dict)
            or not isinstance(result, dict)
            or not isinstance(time, dict)
        ):
            continue

        match_id = str(match_id_value)
        if match_id in matches_by_id:
            continue

        home = teams.get('home')
        away = teams.get('away')
        home_id = _team_uid(home)
        away_id = _team_uid(away)
        home_goals = _valid_score(result.get('home'))
        away_goals = _valid_score(result.get('away'))
        kickoff_at = _epoch_datetime(time.get('uts'))
        if (
            home_id is None
            or away_id is None
            or home_goals is None
            or away_goals is None
            or kickoff_at is None
        ):
            continue
        if kickoff_at >= prediction_kickoff or kickoff_at > source.retrieved_at:
            continue
        if home_id == team_id:
            is_home = True
            opponent = away
            opponent_id = away_id
            goals_for = home_goals
            goals_against = away_goals
        elif away_id == team_id:
            is_home = False
            opponent = home
            opponent_id = home_id
            goals_for = away_goals
            goals_against = home_goals
        else:
            continue

        tournament_id = _to_int(raw.get('_utid'))
        season_id = _to_int(raw.get('_seasonid'))
        matches_by_id[match_id] = FootballMatchResult(
            match_id=match_id,
            kickoff_at=kickoff_at,
            team_id=team_id,
            team_name=team_name,
            opponent_id=opponent_id,
            opponent_name=_team_name(opponent),
            is_home=is_home,
            goals_for=goals_for,
            goals_against=goals_against,
            total_goals=goals_for + goals_against,
            over_1_5=goals_for + goals_against > 1,
            competition=_competition_name(data, raw),
            tournament_id=str(tournament_id) if tournament_id is not None else None,
            season_id=str(season_id) if season_id is not None else None,
            source=source.model_copy(update={'fixture_identifier': match_id}),
        )

    return sorted(matches_by_id.values(), key=lambda match: match.kickoff_at, reverse=True)


def _average_feature(
    matches: list[FootballMatchResult],
    field_name: str,
    source: EvidenceSourceMetadata,
) -> FootballAverageFeature:
    sample_size = len(matches)
    total = sum(getattr(match, field_name) for match in matches)
    value = total / sample_size if sample_size else None
    return FootballAverageFeature(
        value=round(value, 4) if value is not None else None,
        sample_size=sample_size,
        valid=sample_size > 0,
        source=CAPTURED_EVIDENCE_SOURCE,
        retrieved_at=source.retrieved_at,
    )


def _over_one_half_feature(
    matches: list[FootballMatchResult],
    source: EvidenceSourceMetadata,
) -> FootballRateFeature:
    sample_size = len(matches)
    value = sum(match.over_1_5 for match in matches) / sample_size if sample_size else None
    return FootballRateFeature(
        value=round(value, 4) if value is not None else None,
        sample_size=sample_size,
        valid=sample_size > 0,
        source=CAPTURED_EVIDENCE_SOURCE,
        retrieved_at=source.retrieved_at,
    )


def _venue_form(
    matches: list[FootballMatchResult],
    *,
    team_id: str,
    team_name: str,
    venue: str,
    source: EvidenceSourceMetadata,
) -> FootballVenueForm:
    filtered = [match for match in matches if match.is_home == (venue == 'home')]
    return FootballVenueForm(
        team_id=team_id,
        team_name=team_name,
        venue=venue,
        matches=filtered,
        goals_scored_average=_average_feature(filtered, 'goals_for', source),
        goals_conceded_average=_average_feature(filtered, 'goals_against', source),
        over_1_5_rate=_over_one_half_feature(filtered, source),
        source=source,
    )


def find_matching_match_info(
    candidate: OverOneHalfCandidate,
    payloads: Iterable[Any],
) -> FootballFixtureContext | None:
    contexts: list[FootballFixtureContext] = []
    for payload in payloads:
        try:
            contexts.append(parse_match_info(payload, candidate))
        except FootballEvidenceError:
            continue

    candidate_match_id = sportradar_match_id(candidate.fixture.event_id)
    if candidate_match_id is not None:
        matches = [context for context in contexts if context.match_id == candidate_match_id]
        return matches[0] if len(matches) == 1 else None

    matches = [
        context
        for context in contexts
        if normalize_team_name(context.home_team_name)
        == normalize_team_name(candidate.fixture.home_team_name)
        and normalize_team_name(context.away_team_name)
        == normalize_team_name(candidate.fixture.away_team_name)
    ]
    return matches[0] if len(matches) == 1 else None


def _team_form(
    matches: list[FootballMatchResult],
    *,
    team_id: str,
    team_name: str,
    source: EvidenceSourceMetadata,
    season_stats: FootballSeasonStats | None,
) -> FootballTeamForm:
    last_5 = matches[:5]
    last_10 = matches[:10]
    return FootballTeamForm(
        team_id=team_id,
        team_name=team_name,
        last_5=last_5,
        last_10=last_10,
        goals_scored_average_last_5=_average_feature(last_5, 'goals_for', source),
        goals_scored_average_last_10=_average_feature(last_10, 'goals_for', source),
        goals_conceded_average_last_5=_average_feature(last_5, 'goals_against', source),
        goals_conceded_average_last_10=_average_feature(last_10, 'goals_against', source),
        over_1_5_rate_last_5=_over_one_half_feature(last_5, source),
        over_1_5_rate_last_10=_over_one_half_feature(last_10, source),
        home=_venue_form(
            matches,
            team_id=team_id,
            team_name=team_name,
            venue='home',
            source=source,
        ),
        away=_venue_form(
            matches,
            team_id=team_id,
            team_name=team_name,
            venue='away',
            source=source,
        ),
        season_stats=season_stats,
        source=source,
    )


def _season_feature(
    value: Any,
    sample_size: int,
    source: EvidenceSourceMetadata,
) -> FootballAverageFeature:
    parsed = _to_float(value)
    valid = sample_size > 0 and parsed is not None
    return FootballAverageFeature(
        value=round(parsed, 4) if valid else None,
        sample_size=sample_size,
        valid=valid,
        source=CAPTURED_EVIDENCE_SOURCE,
        retrieved_at=source.retrieved_at,
    )


def parse_season_team_stats(
    payload: Any,
    context: FootballFixtureContext,
    *,
    team_id: str,
    team_name: str,
) -> FootballSeasonStats | None:
    _, data = _document(payload, SEASON_UNIQUE_TEAM_STATS_FEED)
    season = data.get('season')
    stats = data.get('stats')
    unique_teams = stats.get('uniqueteams') if isinstance(stats, dict) else None
    season_id_value = _to_int(season.get('_id')) if isinstance(season, dict) else None
    if season_id_value is None or not isinstance(unique_teams, dict):
        raise FootballEvidenceError('season statistics response has invalid season data')

    source = _source_metadata(
        payload,
        SEASON_UNIQUE_TEAM_STATS_FEED,
        source_identifier=str(season_id_value),
        fixture_identifier=context.sportybet_event_id,
        team_id=team_id,
    )
    for raw in unique_teams.values():
        if not isinstance(raw, dict):
            continue
        team = raw.get('uniqueteam')
        provider_team_id_value = _to_int(team.get('_id')) if isinstance(team, dict) else None
        provider_team_id = str(provider_team_id_value) if provider_team_id_value is not None else None
        provider_team_name = _team_name(team)
        if provider_team_id != team_id and normalize_team_name(provider_team_name) != normalize_team_name(team_name):
            continue

        matches = raw.get('matches')
        sample_size = _to_int(matches.get('matches')) if isinstance(matches, dict) else None
        if sample_size is None:
            continue
        goals_scored = raw.get('goals_scored')
        goals_conceded = raw.get('goals_conceded')
        return FootballSeasonStats(
            team_id=team_id,
            team_name=team_name,
            season_id=str(season_id_value),
            goals_scored_average=_season_feature(
                goals_scored.get('average') if isinstance(goals_scored, dict) else None,
                sample_size,
                source,
            ),
            goals_conceded_average=_season_feature(
                goals_conceded.get('average') if isinstance(goals_conceded, dict) else None,
                sample_size,
                source,
            ),
            source=source,
        )
    return None


def _market_evidence(candidate: OverOneHalfCandidate) -> FootballMarketEvidence:
    identity = candidate.identity
    fixture = candidate.fixture
    market = candidate.market
    outcome = candidate.outcome
    provider_probability = _to_float(outcome.probability)
    probability_valid = provider_probability is not None and 0 <= provider_probability <= 1
    market_identity_verified = (
        identity.event_id == fixture.event_id
        and identity.market_id == market.market_id
        and identity.outcome_id == outcome.outcome_id
        and identity.product_id == market.product_id
        and identity.sport_id == fixture.sport_id
        and identity.specifier == (market.specifier or '')
        and market.market_id == OVER_ONE_HALF_MARKET_ID
        and market.specifier == OVER_ONE_HALF_SPECIFIER
        and market.product_id == OVER_ONE_HALF_PRODUCT_ID
        and outcome.outcome_id == OVER_ONE_HALF_OUTCOME_ID
        and (outcome.description or '').strip() == OVER_ONE_HALF_OUTCOME_LABEL
    )
    return FootballMarketEvidence(
        identity=identity,
        odds=outcome.odds,
        provider_probability_source=provider_probability,
        provider_probability=provider_probability if probability_valid else None,
        provider_probability_basis='sportybet_decimal_probability',
        active=outcome.is_active is True,
        market_identity_verified=market_identity_verified,
        market_is_banned=market.is_banned is True,
        fixture_is_banned=fixture.is_banned is True,
    )


def _data_quality(
    market: FootballMarketEvidence,
    context: FootballFixtureContext,
    home_form: FootballTeamForm | None,
    away_form: FootballTeamForm | None,
) -> FootballEvidenceDataQuality:
    home_recent = home_form is not None and len(home_form.last_10) > 0
    away_recent = away_form is not None and len(away_form.last_10) > 0
    goal_history = home_recent and away_recent
    season_statistics = (
        home_form is not None
        and away_form is not None
        and home_form.season_stats is not None
        and away_form.season_stats is not None
    )
    home_away = (
        home_form is not None
        and away_form is not None
        and home_form.home is not None
        and home_form.away is not None
        and away_form.home is not None
        and away_form.away is not None
        and all(
            form.matches
            for form in (home_form.home, home_form.away, away_form.home, away_form.away)
        )
    )
    market_available = (
        market.market_identity_verified
        and market.active
        and market.odds is not None
        and OVER_ONE_HALF_MIN_ODDS <= market.odds < OVER_ONE_HALF_MAX_ODDS_EXCLUSIVE
        and not market.market_is_banned
        and not market.fixture_is_banned
    )
    competition_available = context.competition is not None and context.competition.strip() != ''
    core = {
        'market': market_available,
        'fixture_match': True,
        'home_recent_form': home_recent,
        'away_recent_form': away_recent,
        'goal_history': goal_history,
        'home_away_split': home_away,
        'competition': competition_available,
    }
    missing = [name for name, available in core.items() if not available]
    return FootballEvidenceDataQuality(
        market_available=market_available,
        fixture_matched=True,
        home_recent_form_available=home_recent,
        away_recent_form_available=away_recent,
        goal_history_available=goal_history,
        season_statistics_available=season_statistics,
        home_away_split_available=home_away,
        competition_available=competition_available,
        provider_probability_available=market.provider_probability is not None,
        complete=not missing,
        missing=missing,
        available_feature_count=sum(core.values()),
        total_feature_count=len(core),
    )


def _lastx_team(payload: Any) -> tuple[str, str]:
    _, data = _document(payload, TEAM_LASTX_FEED)
    team = data.get('team')
    team_id_value = _to_int(team.get('_id')) if isinstance(team, dict) else None
    team_name = _team_name(team)
    if team_id_value is None or not team_name:
        raise FootballEvidenceError('stats_team_lastx response has incomplete team identity')
    return str(team_id_value), team_name


def _context_team_matches(
    context: FootballFixtureContext,
    *,
    team_id: str,
    team_name: str,
    expected_team_id: str,
    expected_team_name: str,
) -> bool:
    if team_id != expected_team_id:
        return False
    if normalize_team_name(team_name) != normalize_team_name(expected_team_name):
        return False
    return True


def build_football_evidence(
    candidate: OverOneHalfCandidate,
    *,
    match_info_payload: Any,
    home_team_lastx_payload: Any,
    away_team_lastx_payload: Any,
    season_unique_team_stats_payload: Any | None = None,
) -> FootballFixtureEvidence:
    context = parse_match_info(match_info_payload, candidate)
    home_id, home_name = _lastx_team(home_team_lastx_payload)
    away_id, away_name = _lastx_team(away_team_lastx_payload)
    if not _context_team_matches(
        context,
        team_id=home_id,
        team_name=home_name,
        expected_team_id=context.home_team_id,
        expected_team_name=context.home_team_name,
    ):
        raise FootballEvidenceError('home team statistics do not match the fixture')
    if not _context_team_matches(
        context,
        team_id=away_id,
        team_name=away_name,
        expected_team_id=context.away_team_id,
        expected_team_name=context.away_team_name,
    ):
        raise FootballEvidenceError('away team statistics do not match the fixture')

    home_source = _source_metadata(
        home_team_lastx_payload,
        TEAM_LASTX_FEED,
        source_identifier=home_id,
        fixture_identifier=context.sportybet_event_id,
        team_id=home_id,
    )
    away_source = _source_metadata(
        away_team_lastx_payload,
        TEAM_LASTX_FEED,
        source_identifier=away_id,
        fixture_identifier=context.sportybet_event_id,
        team_id=away_id,
    )
    home_matches = _parse_team_matches(
        home_team_lastx_payload,
        context,
        team_id=home_id,
        team_name=home_name,
        prediction_kickoff=candidate.fixture.kickoff,
    )
    away_matches = _parse_team_matches(
        away_team_lastx_payload,
        context,
        team_id=away_id,
        team_name=away_name,
        prediction_kickoff=candidate.fixture.kickoff,
    )
    home_season_stats = (
        parse_season_team_stats(
            season_unique_team_stats_payload,
            context,
            team_id=home_id,
            team_name=home_name,
        )
        if season_unique_team_stats_payload is not None
        else None
    )
    away_season_stats = (
        parse_season_team_stats(
            season_unique_team_stats_payload,
            context,
            team_id=away_id,
            team_name=away_name,
        )
        if season_unique_team_stats_payload is not None
        else None
    )
    home_form = _team_form(
        home_matches,
        team_id=home_id,
        team_name=home_name,
        source=home_source,
        season_stats=home_season_stats,
    )
    away_form = _team_form(
        away_matches,
        team_id=away_id,
        team_name=away_name,
        source=away_source,
        season_stats=away_season_stats,
    )
    market = _market_evidence(candidate)

    return FootballFixtureEvidence(
        context=context,
        home_form=home_form,
        away_form=away_form,
        market=market,
        data_quality=_data_quality(market, context, home_form, away_form),
    )


def is_evidence_fresh(
    evidence: FootballFixtureEvidence,
    *,
    now: datetime | None = None,
) -> bool:
    current = _utc(now or datetime.now(timezone.utc))
    sources = [evidence.context.source]
    if evidence.home_form is not None:
        sources.append(evidence.home_form.source)
        if evidence.home_form.season_stats is not None:
            sources.append(evidence.home_form.season_stats.source)
    if evidence.away_form is not None:
        sources.append(evidence.away_form.source)
        if evidence.away_form.season_stats is not None:
            sources.append(evidence.away_form.season_stats.source)

    for source in sources:
        retrieved_at = _utc(source.retrieved_at)
        if source.max_age_seconds is None or retrieved_at > current:
            return False
        if (current - retrieved_at).total_seconds() > source.max_age_seconds:
            return False
    return True


class CapturedFootballEvidenceProvider:
    def __init__(
        self,
        *,
        match_info_payload: Any,
        home_team_lastx_payload: Any,
        away_team_lastx_payload: Any,
        season_unique_team_stats_payload: Any | None = None,
    ) -> None:
        self.match_info_payload = match_info_payload
        self.home_team_lastx_payload = home_team_lastx_payload
        self.away_team_lastx_payload = away_team_lastx_payload
        self.season_unique_team_stats_payload = season_unique_team_stats_payload

    def get_evidence(
        self,
        candidate: OverOneHalfCandidate,
        *,
        now: datetime | None = None,
    ) -> FootballFixtureEvidence | None:
        try:
            evidence = build_football_evidence(
                candidate,
                match_info_payload=self.match_info_payload,
                home_team_lastx_payload=self.home_team_lastx_payload,
                away_team_lastx_payload=self.away_team_lastx_payload,
                season_unique_team_stats_payload=self.season_unique_team_stats_payload,
            )
        except FootballEvidenceError:
            return None
        return evidence if is_evidence_fresh(evidence, now=now) else None


def _identity_key(candidate: OverOneHalfCandidate) -> tuple[str, str, str, int, str, str]:
    identity = candidate.identity
    return (
        identity.event_id,
        identity.market_id,
        identity.outcome_id,
        identity.product_id,
        identity.sport_id,
        identity.specifier,
    )


def collect_football_evidence(
    candidates: Iterable[OverOneHalfCandidate],
    provider: FootballEvidenceProvider,
    *,
    now: datetime | None = None,
) -> FootballEvidenceCollection:
    collection = FootballEvidenceCollection()
    attempted: set[tuple[str, str, str, int, str, str]] = set()

    for candidate in candidates:
        identity_key = _identity_key(candidate)
        if identity_key in attempted:
            continue
        attempted.add(identity_key)
        event_id = candidate.identity.event_id
        try:
            evidence = provider.get_evidence(candidate, now=now)
        except Exception as error:
            collection.errors.setdefault(event_id, str(error))
            if event_id not in collection.unavailable_event_ids:
                collection.unavailable_event_ids.append(event_id)
            continue
        if evidence is None:
            if event_id not in collection.unavailable_event_ids:
                collection.unavailable_event_ids.append(event_id)
            continue
        collection.evidence_by_event_id[event_id] = evidence

    return collection
    season_id = _to_int(season.get('_id')) if isinstance(season, dict) else None
    competition = (
        unique_tournament.get('name') if isinstance(unique_tournament, dict) else None
    ) or (_team_name(tournament) if isinstance(tournament, dict) else None)

    return FootballFixtureContext(
        sportybet_event_id=candidate.fixture.event_id,
        sportybet_source_id=match_id,
        sportybet_home_team_id=candidate.fixture.home_team_id,
        sportybet_away_team_id=candidate.fixture.away_team_id,
        match_id=match_id,
        home_team_id=home_team_id,
        home_team_name=home_team_name,
        away_team_id=away_team_id,
        away_team_name=away_team_name,
        competition=competition,
        tournament_id=str(tournament_id) if tournament_id is not None else None,
        unique_tournament_id=str(unique_tournament_id) if unique_tournament_id is not None else None,
        season_id=str(season_id) if season_id is not None else None,
        source=_source_metadata(
            payload,
            MATCH_INFO_FEED,
            source_identifier=match_id,
            fixture_identifier=candidate.fixture.event_id,
        ),
    )
