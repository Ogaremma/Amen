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


def test_competition_name_difference_is_supporting_only():
    result = match_forebet_fixtures([fm(competition='Brazil Serie A')], [ev(competition='National Championship')])[0]
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

def test_date_only_forebet_uses_sportybet_kickoff():
    forebet = fm(kickoff=datetime(2026, 8, 22).date())
    event = ev(kickoff=datetime(2026, 8, 21, 23, 45, tzinfo=timezone.utc))
    result = match_forebet_fixtures([forebet], [event])[0]
    assert result.status == FixtureMatchStatus.MATCHED_NORMALIZED
    assert result.sportybet_event.kickoff == event.kickoff

def test_date_only_forebet_uses_lagos_sportybet_calendar_date():
    forebet = fm(kickoff=datetime(2026, 8, 22).date())
    event = ev(kickoff=datetime(2026, 8, 21, 23, 0, tzinfo=timezone.utc))
    assert match_forebet_fixtures([forebet], [event])[0].status == FixtureMatchStatus.MATCHED_NORMALIZED


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

def test_strong_live_name_variants_match_directionally():
    cases = [('Universidad Católica', 'Ñublense', 'CD Universidad Catolica', 'Nublense'), ('Ferro Carril Oeste', 'All Boys', 'Ferro Carril Oeste', 'CA All Boys'), ('San Luis Quillota', 'Santiago Wanderers', 'San Luis de Quillota', 'Santiago Wanderers'), ('Alianza FC (PAN)', 'Tauro FC', 'Alianza FC Panama', 'Tauro FC')]
    for home, away, event_home, event_away in cases:
        assert match_forebet_fixtures([fm(home_team=home, away_team=away)], [ev(home_team=event_home, away_team=event_away)])[0].status == FixtureMatchStatus.MATCHED_NORMALIZED

def test_nearby_wrong_direction_or_team_remains_rejected():
    forebet = fm(home_team='San Luis Quillota', away_team='Santiago Wanderers')
    result = match_forebet_fixtures([forebet], [ev(home_team='Santiago Wanderers', away_team='San Luis de Quillota'), ev(home_team='San Luis de Quillota', away_team='Santiago Morning')])[0]
    assert result.status == FixtureMatchStatus.UNMATCHED


def test_plaza_amador_panama_city_alias_matches_directionally():
    forebet = fm(home_team='CD Plaza Amador', away_team='UMECIT', kickoff=datetime(2026, 8, 22).date())
    event = ev(home_team='CD Plaza Amador Panama City', away_team='Umecit', kickoff=datetime(2026, 8, 21, 23, tzinfo=timezone.utc))
    result = match_forebet_fixtures([forebet], [event])[0]
    assert result.status == FixtureMatchStatus.MATCHED_NORMALIZED
    assert result.sportybet_event.event_id == event.event_id


def test_plaza_amador_alias_does_not_reverse_teams():
    forebet = fm(home_team='CD Plaza Amador', away_team='UMECIT', kickoff=datetime(2026, 8, 22).date())
    reversed_event = ev(home_team='Umecit', away_team='CD Plaza Amador Panama City', kickoff=datetime(2026, 8, 21, 23, tzinfo=timezone.utc))
    assert match_forebet_fixtures([forebet], [reversed_event])[0].status == FixtureMatchStatus.UNMATCHED


def test_evidence_fallback_requires_direction_date_and_context():
    forebet = fm(home_team='San Luis Quillota', away_team='Santiago Wanderers', kickoff=datetime(2026, 8, 22).date(), competition='Primera B')
    event = ev(home_team='San Luis Quillotta', away_team='Santiago Wanderers', kickoff=datetime(2026, 8, 21, 23, tzinfo=timezone.utc), competition='Primera B')
    result = match_forebet_fixtures([forebet], [event])[0]
    assert result.status == FixtureMatchStatus.MATCHED_NORMALIZED
    assert result.matching_method == 'evidence'


def test_evidence_fallback_rejects_ambiguous_candidates():
    forebet = fm(home_team='San Luis Quillota', away_team='Santiago Wanderers', kickoff=datetime(2026, 8, 22).date(), competition='Primera B')
    events = [ev(event_id='one', home_team='San Luis Quillotta', away_team='Santiago Wanderers', kickoff=datetime(2026, 8, 21, 23, tzinfo=timezone.utc), competition='Primera B'), ev(event_id='two', home_team='San Luis Quillotta', away_team='Santiago Wanderers', kickoff=datetime(2026, 8, 21, 23, 30, tzinfo=timezone.utc), competition='Primera B')]
    assert match_forebet_fixtures([forebet], events)[0].status == FixtureMatchStatus.AMBIGUOUS


def test_advanced_matcher_recovers_colombian_provider_names():
    forebet = fm(home_team='Junior Barranquilla', away_team='Once Caldas', kickoff=datetime(2026, 8, 22).date(), competition='Colombian Primera A')
    event = ev(home_team='CD Junior FC', away_team='CD Once Caldas', kickoff=datetime(2026, 8, 22, 18, tzinfo=timezone.utc), competition='Liga DIMAYOR')
    result = match_forebet_fixtures([forebet], [event])[0]
    assert result.status == FixtureMatchStatus.MATCHED_NORMALIZED
    assert result.matching_method == 'advanced_evidence'


def test_advanced_matcher_recovers_salvador_provider_names():
    forebet = fm(home_team='Luís Ángel Firpo', away_team='Platense (SLV)', kickoff=datetime(2026, 8, 22).date(), competition='Primera Division')
    event = ev(home_team='CD Luis Angel Firpo Usulutan', away_team='CD Platense Municipal Zacatecoluca', kickoff=datetime(2026, 8, 22, 0, 30, tzinfo=timezone.utc), competition='Primera Division')
    assert match_forebet_fixtures([forebet], [event])[0].status == FixtureMatchStatus.MATCHED_NORMALIZED


def test_advanced_matcher_rejects_wrong_date_and_youth_level():
    forebet = fm(home_team='Leixões U23', away_team='União Leiria U23', kickoff=datetime(2026, 8, 22).date(), competition='Liga Revelacao')
    wrong_date = ev(home_team='Leixoes SC', away_team='UD Leiria', kickoff=datetime(2026, 8, 23, 12, tzinfo=timezone.utc), competition='Liga Revelacao')
    same_date_senior = wrong_date.model_copy(update={'kickoff': datetime(2026, 8, 22, 12, tzinfo=timezone.utc)})
    assert match_forebet_fixtures([forebet], [wrong_date])[0].status == FixtureMatchStatus.UNMATCHED
    assert match_forebet_fixtures([forebet], [same_date_senior])[0].status == FixtureMatchStatus.UNMATCHED


def test_advanced_matcher_rejects_unrelated_and_reversed_fixtures():
    forebet = fm(home_team='Irkutsk', away_team='Zvezda SPb', kickoff=datetime(2026, 8, 22).date(), competition='Second League')
    unrelated = ev(home_team='Kdv Tomsk', away_team='Khimik Dzerzhinsk', kickoff=datetime(2026, 8, 22, 12, tzinfo=timezone.utc), competition='Second League')
    reversed_event = ev(home_team='Zvezda SPb', away_team='Irkutsk', kickoff=datetime(2026, 8, 22, 12, tzinfo=timezone.utc), competition='Second League')
    assert match_forebet_fixtures([forebet], [unrelated])[0].status == FixtureMatchStatus.UNMATCHED
    assert match_forebet_fixtures([forebet], [reversed_event])[0].status == FixtureMatchStatus.UNMATCHED
    assert normalize_team_name('Bayern München') == normalize_team_name('Bayern Munchen FC')
