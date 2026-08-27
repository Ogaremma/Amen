import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from app.schemas.forebet_draw_window import DrawWindowResponse
from app.services.forebet_draw_worker import ForebetDrawRefreshWorker, _challenge_cooldown_delay, _jittered_refresh_delay


class WorkerTests(IsolatedAsyncioTestCase):
    def settings(self, interval=0.01, urls="https://forebet.test/a"):
        return SimpleNamespace(forebet_draw_refresh_interval_seconds=interval, forebet_draw_source_urls=urls)

    async def test_start_stop_and_duplicate_prevention(self):
        engine = mock.Mock(); engine.get_active_window.return_value = DrawWindowResponse(days=[], active_count=0)
        engine.refresh_window = mock.AsyncMock(return_value=DrawWindowResponse(days=[], active_count=0))
        worker = ForebetDrawRefreshWorker(engine)
        with mock.patch("app.services.forebet_draw_worker.get_settings", return_value=self.settings(10)):
            await worker.start(); first = worker._task; await worker.start()
            self.assertIs(worker._task, first); self.assertTrue(worker.running)
            await worker.stop()
        self.assertFalse(worker.running); engine.refresh_window.assert_awaited_once()

    async def test_failure_does_not_terminate_worker(self):
        engine = mock.Mock(); engine.get_active_window.return_value = DrawWindowResponse(days=[], active_count=0)
        engine.refresh_window = mock.AsyncMock(side_effect=[RuntimeError("temporary"), DrawWindowResponse(days=[], active_count=0)])
        worker = ForebetDrawRefreshWorker(engine)
        with mock.patch("app.services.forebet_draw_worker.get_settings", return_value=self.settings()):
            await worker.start()
            for _ in range(50):
                if engine.refresh_window.await_count >= 2: break
                await asyncio.sleep(0.005)
            self.assertTrue(worker.running); await worker.stop()
        self.assertGreaterEqual(engine.refresh_window.await_count, 2)

    async def test_configurable_interval_and_disabled_without_urls(self):
        engine = mock.Mock(); engine.get_active_window.return_value = DrawWindowResponse(days=[], active_count=0)
        engine.refresh_window = mock.AsyncMock(return_value=DrawWindowResponse(days=[], active_count=0))
        worker = ForebetDrawRefreshWorker(engine)
        with mock.patch("app.services.forebet_draw_worker.get_settings", return_value=self.settings(0.02)):
            await worker.start(); await asyncio.sleep(0.055); await worker.stop()
        self.assertGreaterEqual(engine.refresh_window.await_count, 2)
        automatic = ForebetDrawRefreshWorker(engine)
        with mock.patch("app.services.forebet_draw_worker.get_settings", return_value=self.settings(urls="")):
            with mock.patch("app.services.forebet_draw_worker.future_prediction_urls", return_value=["https://forebet.test/date"]):
                before = engine.refresh_window.await_count
                await automatic.start(); await automatic.stop()
                self.assertEqual(engine.refresh_window.await_count, before + 1)

    async def test_restart_uses_engine_persisted_window(self):
        engine = mock.Mock(); engine.get_active_window.return_value = DrawWindowResponse(days=[], active_count=0)
        engine.refresh_window = mock.AsyncMock(return_value=DrawWindowResponse(days=[], active_count=0))
        with mock.patch("app.services.forebet_draw_worker.get_settings", return_value=self.settings(10)):
            first = ForebetDrawRefreshWorker(engine); await first.start(); await first.stop()
            second = ForebetDrawRefreshWorker(engine); await second.start(); await second.stop()
        self.assertEqual(engine.refresh_window.await_count, 2)

    def test_jitter_bounds_across_many_samples(self):
        samples = [_jittered_refresh_delay(120.0, 18.0) for _ in range(2000)]
        self.assertTrue(all(102.0 <= value <= 138.0 for value in samples))
        self.assertLess(min(samples), 120.0)
        self.assertGreater(max(samples), 120.0)

    def test_challenge_cooldown_grows_and_respects_cap(self):
        delays = [_challenge_cooldown_delay(failures, 3, 3600.0, 21600.0) for failures in range(3, 9)]
        self.assertEqual(delays, [3600.0, 7200.0, 14400.0, 21600.0, 21600.0, 21600.0])

    async def test_counter_threshold_cooldown_and_success_reset(self):
        blocked = SimpleNamespace(days=[SimpleNamespace(diagnostics=["PROVIDER_FAILURE: ForebetAccessDeniedError"])], active_count=0)
        healthy = SimpleNamespace(days=[], active_count=0)
        engine = mock.Mock()
        engine.get_active_window.return_value = DrawWindowResponse(days=[], active_count=0)
        engine.refresh_window = mock.AsyncMock(side_effect=[blocked, blocked, healthy])
        worker = ForebetDrawRefreshWorker(engine)
        settings = SimpleNamespace(
            forebet_draw_refresh_interval_seconds=20.0,
            forebet_challenge_failure_threshold=2,
            forebet_challenge_cooldown_seconds=45.0,
            forebet_draw_refresh_jitter_seconds=0.0,
        )
        observations = []

        async def advance(awaitable, timeout):
            awaitable.close()
            observations.append((worker.consecutive_forebet_failures, timeout, worker.forebet_cooldown_until))
            if len(observations) == 3:
                worker._stop.set()
                return True
            raise asyncio.TimeoutError

        with mock.patch("app.services.forebet_draw_worker.get_settings", return_value=settings), mock.patch(
            "app.services.forebet_draw_worker.future_prediction_urls", return_value=["https://forebet.test/a"]
        ), mock.patch("app.services.forebet_draw_worker.asyncio.wait_for", side_effect=advance):
            await worker._run()

        self.assertEqual([item[0] for item in observations], [1, 2, 0])
        self.assertEqual([item[1] for item in observations], [20.0, 45.0, 20.0])
        self.assertIsNone(observations[0][2])
        self.assertIsNotNone(observations[1][2])
        self.assertIsNone(observations[2][2])
