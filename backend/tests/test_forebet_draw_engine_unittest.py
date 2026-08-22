from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, mock

from app.schemas.forebet import FixtureMatchResult, FixtureMatchStatus, ForebetMatch, ForebetPredictionResult, SportyBetEvent
from app.services.forebet_draw_engine import ForebetDrawEngine
from app.services.forebet_draw_store import ForebetDrawStore
from app.services.sportybet import SportyBetUpcomingEventsResult


def fm(day, event_id):
    return ForebetMatch(match_id=event_id, home_team=f"Home {event_id}", away_team=f"Away {event_id}", competition="League", kickoff=datetime(2026, 8, day, 15, tzinfo=timezone.utc), predicted_result=ForebetPredictionResult.DRAW)


def ev(day, event_id):
    return SportyBetEvent(event_id=event_id, home_team=f"Home {event_id}", away_team=f"Away {event_id}", home_team_name=f"Home {event_id}", away_team_name=f"Away {event_id}", competition="League", kickoff=datetime(2026, 8, day, 15, tzinfo=timezone.utc), sport_id="sr:sport:1", market_id="1", outcome_draw_id="2", product_id=3, match_status="Not start")


class EngineTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory(); self.store = ForebetDrawStore(str(Path(self.tmp.name) / "state.sqlite3")); self.engine = ForebetDrawEngine(self.store)

    async def asyncTearDown(self): self.tmp.cleanup()

    async def refresh(self, matches, events, codes):
        async def booking(fixtures):
            code = codes.pop(0)
            return mock.Mock(booking_code=code)
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_booking_enabled=True)), mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=matches), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(len(events), events)), mock.patch("app.services.forebet_draw_engine.create_draw_booking", side_effect=booking) as create:
            result = await self.engine.refresh_window(["https://forebet.test"])
        return result, create

    async def test_three_days_one_code_each_and_idempotent(self):
        matches = [fm(d, str(d)) for d in (21, 22, 23, 24)]; events = [ev(d, str(d)) for d in (21, 22, 23, 24)]
        result, create = await self.refresh(matches, events, ["A", "B", "C"])
        self.assertEqual([x.booking_code for x in result.days], ["A", "B", "C"]); self.assertEqual(create.await_count, 3)
        result, create = await self.refresh(matches, events, [])
        self.assertEqual(create.await_count, 0); self.assertEqual(result.active_count, 3)

    async def test_date_only_forebet_persists_sportybet_kickoff(self):
        match = ForebetMatch(match_id="a", home_team="Home a", away_team="Away a", competition="League", kickoff=date(2026, 8, 21), predicted_result=ForebetPredictionResult.DRAW)
        event = ev(21, "a").model_copy(update={"kickoff": datetime(2026, 8, 21, 22, 30, tzinfo=timezone.utc)})
        result, create = await self.refresh([match], [event], ["REAL-MOCKED-CODE"])
        self.assertEqual(create.await_count, 1)
        self.assertEqual(result.days[0].booking_code, "REAL-MOCKED-CODE")
        self.assertEqual(result.days[0].matches[0].kickoff, event.kickoff)

    async def test_all_valid_draws_are_sent_without_five_selection_cap(self):
        matches = [fm(21, str(i)) for i in range(7)]
        events = [ev(21, str(i)) for i in range(7)]
        result, create = await self.refresh(matches, events, ["ALL"])
        self.assertEqual(create.await_args.args[0].__len__(), 7)
        self.assertEqual(result.days[0].selection_count, 7)

    async def test_unmatched_ambiguous_and_duplicate_events_are_not_booked(self):
        matches = [fm(21, "matched"), fm(21, "unmatched"), fm(21, "ambiguous"), fm(21, "duplicate")]
        event = ev(21, "shared")
        results = [FixtureMatchResult(forebet_match=matches[0], status=FixtureMatchStatus.MATCHED_NORMALIZED, sportybet_event=event), FixtureMatchResult(forebet_match=matches[1], status=FixtureMatchStatus.UNMATCHED), FixtureMatchResult(forebet_match=matches[2], status=FixtureMatchStatus.AMBIGUOUS, candidates=[ev(21, "a"), ev(21, "b")]), FixtureMatchResult(forebet_match=matches[3], status=FixtureMatchStatus.MATCHED_NORMALIZED, sportybet_event=event)]
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_booking_enabled=True)), mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=matches), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, [event])), mock.patch("app.services.forebet_draw_engine.match_forebet_fixtures", return_value=results), mock.patch("app.services.forebet_draw_engine.create_draw_booking", return_value=mock.Mock(booking_code="ONLY")) as create:
            response = await self.engine.refresh_window(["url"])
        self.assertEqual(len(create.await_args.args[0]), 1)
        self.assertEqual(response.days[0].booking_code, "ONLY")
        self.assertEqual(response.days[0].matches[0].event_id, "shared")

    async def test_partial_rebook_and_roll_forward(self):
        matches = [fm(21, "a"), fm(21, "b"), fm(22, "c"), fm(23, "d"), fm(24, "e")]
        events = [ev(21, "a"), ev(21, "b"), ev(22, "c"), ev(23, "d"), ev(24, "e")]
        await self.refresh(matches, events, ["A", "B", "C"])
        result, _ = await self.refresh(matches[1:], events[1:], ["A2", "D"])
        self.assertEqual(result.days[0].booking_code, "A2"); self.assertEqual(result.days[0].matches[0].event_id, "b")
        result, _ = await self.refresh(matches[2:], events[2:], ["D"])
        self.assertEqual([x.prediction_date.day for x in result.days], [22, 23, 24])

    async def test_restart_recovery_and_failed_replacement_keeps_old(self):
        matches, events = [fm(21, "a")], [ev(21, "a")]
        await self.refresh(matches, events, ["A"])
        restarted = ForebetDrawEngine(ForebetDrawStore(str(Path(self.tmp.name) / "state.sqlite3")))
        self.assertEqual(restarted.get_active_window().days[0].booking_code, "A")
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_booking_enabled=True)), mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=[fm(21, "b")]), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, [ev(21, "b")])), mock.patch("app.services.forebet_draw_engine.create_draw_booking", side_effect=RuntimeError("failed")) as create:
            await restarted.refresh_window(["url"])
        create.assert_awaited_once()
        self.assertEqual(restarted.get_active_window().days[0].booking_code, "A")

    async def test_booking_disabled_skips_booking_and_promotion(self):
        matches, events = [fm(21, "disabled")], [ev(21, "disabled")]
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_booking_enabled=False)), mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=matches), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, events)), mock.patch("app.services.forebet_draw_engine.create_draw_booking") as create:
            result = await self.engine.refresh_window(["url"])
        create.assert_not_awaited()
        self.assertEqual(result.active_count, 0)
