from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, mock

from app.schemas.forebet import ForebetMatch, ForebetPredictionResult, SportyBetEvent
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
        with mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=matches), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(len(events), events)), mock.patch("app.services.forebet_draw_engine.create_draw_booking", side_effect=booking) as create:
            result = await self.engine.refresh_window(["https://forebet.test"])
        return result, create

    async def test_three_days_one_code_each_and_idempotent(self):
        matches = [fm(d, str(d)) for d in (21, 22, 23, 24)]; events = [ev(d, str(d)) for d in (21, 22, 23, 24)]
        result, create = await self.refresh(matches, events, ["A", "B", "C"])
        self.assertEqual([x.booking_code for x in result.days], ["A", "B", "C"]); self.assertEqual(create.await_count, 3)
        result, create = await self.refresh(matches, events, [])
        self.assertEqual(create.await_count, 0); self.assertEqual(result.active_count, 3)

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
        with mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=[fm(21, "b")]), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, [ev(21, "b")])), mock.patch("app.services.forebet_draw_engine.create_draw_booking", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError): await restarted.refresh_window(["url"])
        self.assertEqual(restarted.get_active_window().days[0].booking_code, "A")
