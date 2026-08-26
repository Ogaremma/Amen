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

TARGET_URLS = [f"https://forebet.test/{date(2026, 8, day).isoformat()}" for day in (21, 22, 23)]


class EngineTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory(); self.store = ForebetDrawStore(str(Path(self.tmp.name) / "state.sqlite3")); self.engine = ForebetDrawEngine(self.store)

    async def asyncTearDown(self): self.tmp.cleanup()

    async def refresh(self, matches, events, codes, urls=TARGET_URLS):
        async def booking(fixtures):
            code = codes.pop(0)
            return mock.Mock(booking_code=code)
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_booking_enabled=True, forebet_real_booking_authorized=True, forebet_draw_missing_event_timeout_hours=100_000)), mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=matches), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(len(events), events)), mock.patch("app.services.forebet_draw_engine.create_draw_booking", side_effect=booking) as create:
            result = await self.engine.refresh_window(urls)
        return result, create

    async def paper_refresh(self, matches, events):
        settings = mock.Mock(forebet_draw_booking_enabled=False, forebet_draw_paper_booking_enabled=True)
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=settings), mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=matches), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(len(events), events)), mock.patch("app.services.forebet_draw_engine.create_draw_booking", side_effect=AssertionError("real booking forbidden")):
            return await self.engine.refresh_window(TARGET_URLS)

    async def test_paper_three_day_window_compilation_and_idempotency(self):
        matches = [fm(day, f"{day}-{i}") for day, count in ((21, 3), (22, 4), (23, 5)) for i in range(count)]
        events = [ev(day, f"{day}-{i}") for day, count in ((21, 3), (22, 4), (23, 5)) for i in range(count)]
        first = await self.paper_refresh(matches, events)
        self.assertEqual(len(first.days), 3); self.assertEqual([d.selection_count for d in first.days], [3, 4, 5])
        self.assertEqual(first.compilation.selection_count, 12)
        codes = [d.booking_code for d in first.days]; compilation_code = first.compilation.booking_code
        second = await self.paper_refresh(matches, events)
        self.assertEqual([d.booking_code for d in second.days], codes); self.assertEqual(second.compilation.booking_code, compilation_code)

    async def test_paper_changed_and_played_selections_replace_codes(self):
        matches = [fm(21, x) for x in "abc"]
        first = await self.paper_refresh(matches, [ev(21, x) for x in "abc"])
        second = await self.paper_refresh(matches, [ev(21, "a").model_copy(update={"match_status": "Live"}), ev(21, "b"), ev(21, "c")])
        self.assertEqual([m.event_id for m in first.days[0].matches], list("abc"))
        self.assertEqual([m.event_id for m in second.days[0].matches], list("bc"))
        self.assertNotEqual(first.days[0].booking_code, second.days[0].booking_code)

    async def test_paper_empty_day_is_preserved_without_code(self):
        matches = [fm(day, str(day)) for day in (21, 22, 23)]
        result = await self.paper_refresh(matches, [ev(21, "21"), ev(23, "23")])
        self.assertEqual(len(result.days), 3)
        empty = next(day for day in result.days if day.prediction_date.day == 22)
        self.assertEqual(empty.status, "unavailable"); self.assertIsNone(empty.booking_code); self.assertEqual(empty.selection_count, 0)

    async def test_authoritative_urls_ignore_stray_previous_date(self):
        matches = [fm(20, "stray"), fm(21, "a"), fm(22, "b"), fm(23, "c"), fm(24, "next")]
        events = [ev(20, "stray"), ev(21, "a"), ev(22, "b"), ev(23, "c"), ev(24, "next")]
        result = await self.paper_refresh(matches, events)
        self.assertEqual([day.prediction_date.day for day in result.days], [21, 22, 23])
        self.assertNotIn("stray", [match.event_id for day in result.days for match in day.matches])
        self.assertNotIn("next", [match.event_id for day in result.days for match in day.matches])

    async def test_compilation_deduplicates_selection_identity(self):
        matches = [fm(21, "shared"), fm(22, "shared"), fm(23, "unique")]
        events = [ev(21, "shared"), ev(22, "shared"), ev(23, "unique")]
        result = await self.paper_refresh(matches, events)
        self.assertEqual(result.compilation.selection_count, 2)
        self.assertEqual(len(result.compilation.prediction_dates), 3)

    async def test_paper_failure_isolated_and_retryable(self):
        matches = [fm(day, str(day)) for day in (21, 22, 23)]
        events = [ev(day, str(day)) for day in (21, 22, 23)]
        original = self.engine._paper_code
        failed = False
        def paper_code(items):
            nonlocal failed
            if not failed and items[0].event_id == "21":
                failed = True
                raise RuntimeError("paper adapter unavailable")
            return original(items)
        with mock.patch.object(self.engine, "_paper_code", side_effect=paper_code):
            first = await self.paper_refresh(matches, events)
        self.assertEqual([day.prediction_date.day for day in first.days], [21, 22, 23])
        self.assertEqual(first.days[0].status, "error"); self.assertIsNone(first.days[0].booking_code)
        second = await self.paper_refresh(matches, events)
        self.assertEqual([day.prediction_date.day for day in second.days], [21, 22, 23])

    async def test_compilation_batches_do_not_truncate_daily_bookings(self):
        matches = [fm(day, f"{day}-{i}") for day in (21, 22, 23) for i in range(17)]
        events = [ev(day, f"{day}-{i}") for day in (21, 22, 23) for i in range(17)]
        by_id = {event.event_id: event for event in events}
        results = [FixtureMatchResult(forebet_match=match, status=FixtureMatchStatus.MATCHED_EXACT, sportybet_event=by_id[match.match_id]) for match in matches]
        with mock.patch("app.services.forebet_draw_engine.match_forebet_fixtures", return_value=results):
            result = await self.paper_refresh(matches, events)
        self.assertEqual([day.selection_count for day in result.days], [17, 17, 17])
        self.assertEqual(result.compilation.status, "active")
        self.assertEqual(result.compilation.selection_count, 51)
        self.assertEqual([len(batch.matches) for batch in result.compilation.batches], [50, 1])

    async def test_three_days_one_code_each_and_idempotent(self):
        matches = [fm(d, str(d)) for d in (21, 22, 23, 24)]; events = [ev(d, str(d)) for d in (21, 22, 23, 24)]
        result, create = await self.refresh(matches, events, ["A", "B", "C", "COMP"])
        self.assertEqual([x.booking_code for x in result.days], ["A", "B", "C"]); self.assertEqual(create.await_count, 4)
        result, create = await self.refresh(matches, events, [])
        self.assertEqual(create.await_count, 0); self.assertEqual(result.active_count, 3)

    async def test_date_only_forebet_persists_sportybet_kickoff(self):
        match = ForebetMatch(match_id="a", home_team="Home a", away_team="Away a", competition="League", kickoff=date(2026, 8, 21), predicted_result=ForebetPredictionResult.DRAW)
        event = ev(21, "a").model_copy(update={"kickoff": datetime(2026, 8, 21, 22, 30, tzinfo=timezone.utc)})
        result, create = await self.refresh([match], [event], ["REAL-MOCKED-CODE", "COMP"])
        self.assertEqual(create.await_count, 2)
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
            response = await self.engine.refresh_window(TARGET_URLS)
        self.assertEqual(len(create.await_args.args[0]), 1)
        self.assertEqual(response.days[0].booking_code, "ONLY")
        self.assertEqual(response.days[0].matches[0].event_id, "shared")

    async def test_partial_rebook_and_roll_forward(self):
        matches = [fm(21, "a"), fm(21, "b"), fm(22, "c"), fm(23, "d"), fm(24, "e")]
        events = [ev(21, "a"), ev(21, "b"), ev(22, "c"), ev(23, "d"), ev(24, "e")]
        await self.refresh(matches, events, ["A", "B", "C", "COMP"])
        result, _ = await self.refresh(matches[1:], events[1:], ["A2", "D"])
        self.assertEqual(result.days[0].booking_code, "A2"); self.assertEqual(result.days[0].matches[0].event_id, "b")
        rolled_urls = [f"https://forebet.test/2026-08-{day:02d}" for day in (22, 23, 24)]
        result, _ = await self.refresh(matches[2:], events[2:], ["D"], rolled_urls)
        self.assertEqual([x.prediction_date.day for x in result.days], [22, 23, 24])

    async def test_roll_forward_uses_batches_and_retires_exhausted_day(self):
        matches = [fm(21, "a"), fm(22, "b"), fm(23, "c"), fm(24, "d")]
        events = [ev(21, "a"), ev(22, "b"), ev(23, "c"), ev(24, "d")]
        first = await self.paper_refresh(matches[:3], events[:3])
        preserved_codes = {day.prediction_date: day.batches[0].booking_code for day in first.days[1:]}

        stale = self.store.list_daily_batches(date(2026, 8, 21))[0]
        self.assertTrue(self.store.update_daily_batch_if_identity(date(2026, 8, 21), 1, stale["identity"], {**stale, "identity": self.engine._identity_hash([]), "booking_code": None, "matches": [], "status": "unavailable"}))
        self.assertEqual(self.store.list_active([date(2026, 8, 21)])[0].matches, [])

        rolled_urls = [f"https://forebet.test/2026-08-{day:02d}" for day in (22, 23, 24)]
        # Exercise the authoritative B/C/D slots through the normal refresh path.
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_booking_enabled=False, forebet_draw_paper_booking_enabled=True)), mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=matches[1:]), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(3, events[1:])):
            rolled = await self.engine.refresh_window(rolled_urls)

        self.assertEqual([day.prediction_date.day for day in rolled.days], [22, 23, 24])
        self.assertEqual({day.prediction_date: day.batches[0].booking_code for day in rolled.days[:2]}, preserved_codes)
        self.assertEqual(rolled.days[2].matches[0].event_id, "d")
        self.assertEqual([m.event_id for m in rolled.compilation.matches], ["b", "c", "d"])
        self.assertNotIn(date(2026, 8, 21), [day.prediction_date for day in self.store.list_active()])

    async def test_restart_recovery_and_failed_replacement_keeps_old(self):
        matches, events = [fm(21, "a")], [ev(21, "a")]
        await self.refresh(matches, events, ["A"])
        restarted = ForebetDrawEngine(ForebetDrawStore(str(Path(self.tmp.name) / "state.sqlite3")))
        target_dates = [date(2026, 8, day) for day in (21, 22, 23)]
        self.assertEqual(restarted.get_active_window(target_dates=target_dates).days[0].booking_code, "A")
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_booking_enabled=True, forebet_real_booking_authorized=True, forebet_draw_missing_event_timeout_hours=100_000)), mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=[fm(21, "b")]), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, [ev(21, "b")])), mock.patch("app.services.forebet_draw_engine.create_draw_booking", side_effect=RuntimeError("failed")) as create:
            await restarted.refresh_window(TARGET_URLS)
        self.assertGreaterEqual(create.await_count, 1)
        failed = restarted.get_active_window(target_dates=target_dates).days[0]
        self.assertEqual(failed.booking_code, "A"); self.assertEqual(failed.status, "error")
        self.assertTrue(any(reason.startswith("real_booking_failed:") for event in failed.rebook_events for reason in event.reasons))

    async def test_booking_disabled_skips_booking_and_promotion(self):
        matches, events = [fm(21, "disabled")], [ev(21, "disabled")]
        with mock.patch("app.services.forebet_draw_engine.get_settings", return_value=mock.Mock(forebet_draw_booking_enabled=False)), mock.patch("app.services.forebet_draw_engine.fetch_forebet_page", return_value="html"), mock.patch("app.services.forebet_draw_engine.parse_forebet_html", return_value=matches), mock.patch("app.services.forebet_draw_engine.get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, events)), mock.patch("app.services.forebet_draw_engine.create_draw_booking") as create:
            result = await self.engine.refresh_window(TARGET_URLS)
        create.assert_not_awaited()
        self.assertEqual(result.active_count, 0)
