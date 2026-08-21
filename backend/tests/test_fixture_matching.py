from datetime import datetime, timezone, timedelta

from app.schemas.forebet import ForebetMatch, ForebetPredictionResult, SportyBetEvent, FixtureMatchStatus
from app.services.fixture_matching import draw_matches_by_date, match_forebet_fixtures, normalize_team_name


def fm(result=ForebetPredictionResult.DRAW, **kwargs):
    return ForebetMatch(home_team=kwargs.pop('home_team', 'Arsenal FC'), away_team=kwargs.pop('away_team', 'Paris Saint-Germain'), kickoff=kwargs.pop('kickoff', datetime(2026, 8, 21, 19, tzinfo=timezone.utc)), predicted_result=result, competition=kwargs.pop('competition', 'Premier League'), **kwargs)


def ev(**kwargs):
    return SportyBetEvent(event_id=kwargs.pop('event_id', 'sr:match:1'), home_team=kwargs.pop('home_team', 'Arsenal'), away_team=kwargs.pop('away_team', 'Paris Saint Germain'), kickoff=kwargs.pop('kickoff', datetime(2026, 8, 21, 19, 5, tzinfo=timezone.utc)), competition=kwargs.pop('competition', 'England Premier League'), **kwargs)


def test_draw_filter_and_grouping():
    grouped = draw_matches_by_date([fm(), fm(ForebetPredictionResult.HOME), fm(kickoff=datetime(2026, 8, 22, 19, tzinfo=timezone.utc))])
    assert list(grouped) == [datetime(2026, 8, 21).date(), datetime(2026, 8, 22).date()]


def test_normalized_exact_directional_match():
    result = match_forebet_fixtures([fm()], [ev()])[0]
    assert result.status == FixtureMatchStatus.MATCHED_NORMALIZED


def test_exact_match():
    event = ev(home_team='Arsenal FC', away_team='Paris Saint-Germain', kickoff=datetime(2026, 8, 21, 19, tzinfo=timezone.utc), competition='Premier League')
    assert match_forebet_fixtures([fm()], [event])[0].status == FixtureMatchStatus.MATCHED_EXACT


def test_time_tolerance_and_rejection():
    assert match_forebet_fixtures([fm()], [ev(kickoff=datetime(2026, 8, 21, 19, 14, tzinfo=timezone.utc))])[0].status != FixtureMatchStatus.UNMATCHED
    assert match_forebet_fixtures([fm()], [ev(kickoff=datetime(2026, 8, 21, 21, tzinfo=timezone.utc))])[0].status == FixtureMatchStatus.UNMATCHED


def test_wrong_date_or_direction_rejected():
    assert match_forebet_fixtures([fm()], [ev(kickoff=datetime(2026, 8, 22, 19, tzinfo=timezone.utc))])[0].status == FixtureMatchStatus.UNMATCHED
    assert match_forebet_fixtures([fm()], [ev(home_team='Paris Saint Germain', away_team='Arsenal')])[0].status == FixtureMatchStatus.UNMATCHED


def test_wrong_individual_teams_rejected():
    assert match_forebet_fixtures([fm()], [ev(home_team='Chelsea')])[0].status == FixtureMatchStatus.UNMATCHED
    assert match_forebet_fixtures([fm()], [ev(away_team='Lyon')])[0].status == FixtureMatchStatus.UNMATCHED


def test_timezone_equivalent_kickoffs_match():
    lagos = timezone(timedelta(hours=1))
    event = ev(kickoff=datetime(2026, 8, 21, 20, tzinfo=lagos))
    assert match_forebet_fixtures([fm()], [event])[0].status == FixtureMatchStatus.MATCHED_NORMALIZED


def test_no_candidate_is_unmatched():
    assert match_forebet_fixtures([fm()], [])[0].status == FixtureMatchStatus.UNMATCHED


def test_ambiguous_candidates_are_not_guessed():
    result = match_forebet_fixtures([fm()], [ev(event_id='1'), ev(event_id='2')])[0]
    assert result.status == FixtureMatchStatus.AMBIGUOUS
    assert len(result.candidates) == 2


def test_non_draws_excluded():
    assert match_forebet_fixtures([fm(ForebetPredictionResult.HOME), fm(ForebetPredictionResult.AWAY)], [ev()]) == []


def test_team_normalization_accents_and_suffixes():
    assert normalize_team_name('Paris Saint-Germain') == normalize_team_name('Paris Saint Germain SC')
    assert normalize_team_name('Bayern München') == normalize_team_name('Bayern Munchen FC')
