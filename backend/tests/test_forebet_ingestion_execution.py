from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, mock

from app.schemas.forebet import ForebetMatch, ForebetPredictionResult, SportyBetEvent
from app.schemas.forebet_ingestion import ForebetAcquisitionSnapshot, ForebetAcquisitionSnapshotRequest
from app.services.forebet_draw_store import ForebetDrawStore
from app.services.sportybet import SportyBetUpcomingEventsResult
from app.services import forebet_ingestion as ingestion

def fm(day, name):
    return ForebetMatch(home_team=f"Home {name}", away_team=f"Away {name}", kickoff=date(2026, 8, day), predicted_result=ForebetPredictionResult.DRAW, competition="League")

def ev(day, name, event_id=None):
    return SportyBetEvent(event_id=event_id or name, home_team=f"Home {name}", away_team=f"Away {name}", kickoff=datetime(2026, 8, day, 12, tzinfo=timezone.utc), competition="League", sport_id="sr:sport:1", market_id="1", outcome_draw_id="2", product_id=3, match_status="Not start")

class SnapshotExecutionTests(IsolatedAsyncioTestCase):
    def test_trusted_snapshot_date_filter_uses_lagos_date(self):
        self.assertEqual(ingestion._kickoff_lagos_date(datetime(2026, 8, 24, 23, 30, tzinfo=timezone.utc)), date(2026, 8, 25))
        self.assertEqual(ingestion._kickoff_lagos_date(date(2026, 8, 25)), date(2026, 8, 25))

    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.store = ForebetDrawStore(str(Path(self.tmp.name) / "state.sqlite3"))
        self.store_patch = mock.patch.object(ingestion, "forebet_draw_store", self.store)
        self.store_patch.start()

    async def asyncTearDown(self):
        self.store_patch.stop(); self.tmp.cleanup()

    def request(self, matches, dry_run=False):
        by_day = {}
        for match in matches: by_day.setdefault(match.kickoff, []).append(match)
        return ForebetAcquisitionSnapshotRequest(snapshots=[ForebetAcquisitionSnapshot(prediction_date=day, source_url=f"https://forebet/{day}", matches=items) for day, items in by_day.items()], dry_run=dry_run)

    async def test_dry_run_never_books_or_promotes(self):
        with mock.patch.object(ingestion, "get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, [ev(22, "a")])), mock.patch.object(ingestion, "create_draw_booking", side_effect=AssertionError("must not book")), mock.patch.object(self.store, "promote", side_effect=AssertionError("must not promote")):
            result = await ingestion.dry_run_snapshot(self.request([fm(22, "a")], True))
        self.assertTrue(result["dry_run"])

    async def test_enabled_execution_deduplicates_and_books_each_date(self):
        matches = [fm(22, "a"), fm(22, "a"), fm(23, "b")]
        events = [ev(22, "a", "shared"), ev(23, "b")]
        async def booking(fixtures): return mock.Mock(booking_code=f"CODE-{fixtures[0].forebet_match.kickoff}")
        with mock.patch.object(ingestion, "get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(2, events)), mock.patch.object(ingestion, "create_draw_booking", side_effect=booking) as create:
            result = await ingestion.execute_snapshot(self.request(matches))
        self.assertEqual(create.await_count, 2)
        self.assertEqual(len(create.await_args_list[0].args[0]), 1)
        self.assertEqual([day.booking_code for day in self.store.list_active()], ["CODE-2026-08-22", "CODE-2026-08-23"])
        self.assertEqual(len(result["bookings"]), 2)

    async def test_booking_failure_preserves_existing_and_identity_reuses(self):
        request = self.request([fm(22, "a")])
        event = ev(22, "a")
        with mock.patch.object(ingestion, "get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, [event])), mock.patch.object(ingestion, "create_draw_booking", return_value=mock.Mock(booking_code="FIRST")):
            await ingestion.execute_snapshot(request)
        with mock.patch.object(ingestion, "get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, [event])), mock.patch.object(ingestion, "create_draw_booking", side_effect=AssertionError("unchanged must reuse")):
            result = await ingestion.execute_snapshot(request)
        self.assertTrue(result["bookings"][0]["reused"])
        changed = ev(22, "a", "changed")
        with mock.patch.object(ingestion, "get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(1, [changed])), mock.patch.object(ingestion, "create_draw_booking", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError): await ingestion.execute_snapshot(request)
        self.assertEqual(self.store.list_active()[0].booking_code, "FIRST")

    async def test_more_than_five_matches_are_not_truncated(self):
        matches = [fm(22, str(i)) for i in range(7)]
        events = [ev(22, str(i)) for i in range(7)]
        with mock.patch.object(ingestion, "get_upcoming_football_events", return_value=SportyBetUpcomingEventsResult(7, events)), mock.patch.object(ingestion, "create_draw_booking", return_value=mock.Mock(booking_code="ALL")) as create:
            await ingestion.execute_snapshot(self.request(matches))
        self.assertEqual(len(create.await_args.args[0]), 7)
