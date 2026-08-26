from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from app.schemas.forebet import FixtureMatchResult, FixtureMatchStatus, ForebetMatch, ForebetPredictionResult, SportyBetEvent
from app.services.forebet_draw_engine import ForebetDrawEngine
from app.services.forebet_draw_store import ForebetDrawStore


class RealBatchingTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory(); self.store = ForebetDrawStore(str(Path(self.tmp.name) / "state.sqlite3")); self.engine = ForebetDrawEngine(self.store); self.day = date(2030, 1, 1)

    async def asyncTearDown(self): self.tmp.cleanup()

    def result(self, index):
        kickoff = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
        forebet = ForebetMatch(match_id=str(index), home_team=f"h{index}", away_team=f"a{index}", competition="l", kickoff=kickoff, predicted_result=ForebetPredictionResult.DRAW)
        event = SportyBetEvent(event_id=str(index), home_team=f"h{index}", away_team=f"a{index}", home_team_name=f"h{index}", away_team_name=f"a{index}", competition="l", kickoff=kickoff, sport_id="sr:sport:1", market_id="1", outcome_draw_id="2", product_id=3, match_status="Not start")
        return FixtureMatchResult(forebet_match=forebet, status=FixtureMatchStatus.MATCHED_EXACT, sportybet_event=event)

    def settings(self, enabled=True, authorized=True, paper=True):
        return SimpleNamespace(forebet_draw_booking_enabled=enabled, forebet_real_booking_authorized=authorized, forebet_draw_paper_booking_enabled=paper)

    async def test_real_call_only_fires_when_identity_changes(self):
        results = [self.result(1), self.result(2)]
        with mock.patch("app.services.forebet_draw_engine.create_draw_booking", new=mock.AsyncMock(side_effect=[mock.Mock(booking_code="REAL-1"), mock.Mock(booking_code="REAL-2")])) as create:
            first = await self.engine._book_batches(scope="daily", prediction_date=self.day, results=results, settings=self.settings())
            second = await self.engine._book_batches(scope="daily", prediction_date=self.day, results=results, settings=self.settings())
            third = await self.engine._book_batches(scope="daily", prediction_date=self.day, results=[results[0], self.result(3)], settings=self.settings())
        self.assertEqual(create.await_count, 2)
        self.assertEqual(first[0]["booking_code"], second[0]["booking_code"])
        self.assertEqual(third[0]["booking_code"], "REAL-2")

    async def test_real_failure_retains_previous_code_and_logs_without_corruption(self):
        original = [self.result(1)]
        with mock.patch("app.services.forebet_draw_engine.create_draw_booking", new=mock.AsyncMock(return_value=mock.Mock(booking_code="REAL-GOOD"))):
            await self.engine._book_batches(scope="daily", prediction_date=self.day, results=original, settings=self.settings())
        with mock.patch("app.services.forebet_draw_engine.create_draw_booking", new=mock.AsyncMock(side_effect=TimeoutError("provider timeout"))):
            batches = await self.engine._book_batches(scope="daily", prediction_date=self.day, results=[self.result(2)], settings=self.settings())
        self.assertEqual(batches[0]["booking_code"], "REAL-GOOD")
        self.assertEqual(self.store.list_daily_batches(self.day)[0]["booking_code"], "REAL-GOOD")
        self.assertIn("real_booking_failed:TimeoutError:provider timeout", self.store.list_rebook_events(self.day)[0]["reasons"])

    async def test_failed_identity_retries_then_successful_identity_is_reused(self):
        results = [self.result(1)]
        create = mock.AsyncMock(side_effect=[TimeoutError("first attempt failed"), mock.Mock(booking_code="REAL-RETRY")])
        with mock.patch("app.services.forebet_draw_engine.create_draw_booking", new=create):
            failed = await self.engine._book_batches(scope="daily", prediction_date=self.day, results=results, settings=self.settings())
            retried = await self.engine._book_batches(scope="daily", prediction_date=self.day, results=results, settings=self.settings())
            reused = await self.engine._book_batches(scope="daily", prediction_date=self.day, results=results, settings=self.settings())
        self.assertEqual(create.await_count, 2)
        self.assertEqual(failed[0]["status"], "error")
        self.assertIsNone(failed[0]["booking_code"])
        self.assertEqual(retried[0]["booking_code"], "REAL-RETRY")
        self.assertEqual(reused[0]["booking_code"], "REAL-RETRY")

    async def test_real_calls_never_exceed_fifty_selections(self):
        results = [self.result(i) for i in range(101)]
        create = mock.AsyncMock(side_effect=[mock.Mock(booking_code=f"REAL-{i}") for i in range(3)])
        with mock.patch("app.services.forebet_draw_engine.create_draw_booking", new=create):
            batches = await self.engine._book_batches(scope="compilation", prediction_date=None, results=results, settings=self.settings())
        self.assertEqual([len(call.args[0]) for call in create.await_args_list], [50, 50, 1])
        self.assertEqual(len(batches), 3)

    async def test_both_real_flags_false_preserves_paper_only_behavior(self):
        create = mock.AsyncMock(side_effect=AssertionError("real booking must remain disabled"))
        with mock.patch("app.services.forebet_draw_engine.create_draw_booking", new=create):
            batches = await self.engine._book_batches(scope="daily", prediction_date=self.day, results=[self.result(1)], settings=self.settings(enabled=False, authorized=False, paper=True))
        create.assert_not_awaited()
        self.assertTrue(batches[0]["booking_code"].startswith("PAPER-"))
