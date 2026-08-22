from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase, mock

from app.schemas.forebet import ForebetMatch, ForebetPredictionResult, SportyBetEvent
from app.services.sportybet import SportyBetUpcomingEventsResult
from app.services import forebet_draw_diagnostics as diagnostics


def match():
    return ForebetMatch(home_team="Arsenal", away_team="Chelsea", competition="Premier League", kickoff=datetime(2026, 8, 22, 15, tzinfo=timezone.utc), predicted_result=ForebetPredictionResult.DRAW)


def event():
    return SportyBetEvent(event_id="event", home_team="Arsenal", away_team="Chelsea", home_team_name="Arsenal", away_team_name="Chelsea", competition="Premier League", kickoff=datetime(2026, 8, 22, 15, tzinfo=timezone.utc), sport_id="sr:sport:1", market_id="1", outcome_draw_id="2", product_id=3)

def many_matches():
    return [match().model_copy(update={"match_id": str(index), "home_team": f"Home {index}", "away_team": f"Away {index}"}) for index in range(10)]

def many_events():
    return [event().model_copy(update={"event_id": str(index), "home_team": f"Home {index}", "away_team": f"Away {index}", "home_team_name": f"Home {index}", "away_team_name": f"Away {index}"}) for index in range(10)]


class DiagnosticsTests(IsolatedAsyncioTestCase):
    async def test_successful_diagnostics_do_not_book_or_mutate(self):
        with mock.patch.object(diagnostics, "future_prediction_dates", return_value=[datetime(2026, 8, 22).date()]), mock.patch.object(diagnostics, "future_prediction_urls", return_value=["https://forebet.test/date"]), mock.patch.object(diagnostics, "database_diagnostics", return_value={"reachable": True, "daily_booking_table_available": True, "revision_table_available": True, "active_records": 0, "error": None}), mock.patch.object(diagnostics, "fetch_forebet_page", return_value="html"), mock.patch.object(diagnostics, "parse_forebet_html", return_value=[match()]), mock.patch.object(diagnostics, "get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, [event()])), mock.patch("app.services.sportybet._create_share_code", side_effect=AssertionError("must not book")), mock.patch("app.services.forebet_draw_store.ForebetDrawStore.promote", side_effect=AssertionError("must not write")):
            report = await diagnostics.run_forebet_draw_diagnostics()
        self.assertEqual(report["forebet"]["matches_parsed"], 1)
        self.assertEqual(report["forebet"]["draw_matches"], 1)
        self.assertEqual(report["matching"]["matched_exact"], 1)
        self.assertEqual(report["booking_candidates"]["total_valid_selections"], 1)

    async def test_stage_failures_are_reported_without_crashing(self):
        with mock.patch.object(diagnostics, "future_prediction_dates", return_value=[datetime(2026, 8, 22).date()]), mock.patch.object(diagnostics, "future_prediction_urls", return_value=["url"]), mock.patch.object(diagnostics, "database_diagnostics", return_value={}), mock.patch.object(diagnostics, "fetch_forebet_page", side_effect=RuntimeError("blocked")):
            report = await diagnostics.run_forebet_draw_diagnostics()
        self.assertEqual(report["forebet"]["sources_succeeded"], 0)
        self.assertEqual(report["forebet"]["errors"][0]["exception_type"], "RuntimeError")

    async def test_sportybet_failure_is_reported(self):
        with mock.patch.object(diagnostics, "future_prediction_dates", return_value=[datetime(2026, 8, 22).date()]), mock.patch.object(diagnostics, "future_prediction_urls", return_value=["url"]), mock.patch.object(diagnostics, "database_diagnostics", return_value={}), mock.patch.object(diagnostics, "fetch_forebet_page", return_value="html"), mock.patch.object(diagnostics, "parse_forebet_html", return_value=[match()]), mock.patch.object(diagnostics, "get_upcoming_football_events", side_effect=RuntimeError("unavailable")):
            report = await diagnostics.run_forebet_draw_diagnostics()
        self.assertEqual(report["sportybet"]["error"]["stage"], "sportybet_acquisition")

    async def test_diagnostics_retains_more_than_five_draws(self):
        with mock.patch.object(diagnostics, "future_prediction_dates", return_value=[datetime(2026, 8, 22).date()]), mock.patch.object(diagnostics, "future_prediction_urls", return_value=["url"]), mock.patch.object(diagnostics, "database_diagnostics", return_value={}), mock.patch.object(diagnostics, "fetch_forebet_page", return_value="html"), mock.patch.object(diagnostics, "parse_forebet_html", return_value=many_matches()), mock.patch.object(diagnostics, "get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(10, many_events())):
            report = await diagnostics.run_forebet_draw_diagnostics()
        self.assertEqual(report["forebet"]["selected_draw_matches"], 10)
        self.assertEqual(report["booking_candidates"]["total_valid_selections"], 10)
