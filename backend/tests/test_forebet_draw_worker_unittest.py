import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from app.schemas.forebet_draw_window import DrawWindowResponse
from app.services.forebet_draw_worker import ForebetDrawRefreshWorker


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
