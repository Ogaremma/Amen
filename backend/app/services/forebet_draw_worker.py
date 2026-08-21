from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.config.settings import get_settings
from app.services.forebet_draw_engine import ForebetDrawEngine, forebet_draw_engine
from app.services.forebet_dates import future_prediction_urls

logger = logging.getLogger("amen.forebet_draw_worker")


class ForebetDrawRefreshWorker:
    def __init__(self, engine: ForebetDrawEngine = forebet_draw_engine) -> None:
        self.engine = engine
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_started: datetime | None = None
        self.last_completed: datetime | None = None
        self.last_failure: str | None = None
        self.last_failure_stage: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            logger.info("worker_start_skipped reason=already_running")
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="forebet-draw-refresh-worker")
        self.last_started = datetime.now(timezone.utc)
        logger.info("worker_started interval_seconds=%s", get_settings().forebet_draw_refresh_interval_seconds)
        await asyncio.sleep(0)

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        logger.info("worker_shutdown_started")
        self._stop.set()
        await task
        self._task = None
        logger.info("worker_shutdown_completed")

    async def _run(self) -> None:
        settings = get_settings()
        interval = settings.forebet_draw_refresh_interval_seconds
        configured_urls = [url.strip() for url in settings.forebet_draw_source_urls.split(",") if url.strip()]
        while not self._stop.is_set():
            source_urls = configured_urls or future_prediction_urls()
            logger.info("refresh_started source_count=%s", len(source_urls))
            try:
                before = {day.prediction_date: day.booking_code for day in self.engine.get_active_window().days}
                response = await self.engine.refresh_window(source_urls)
                self.last_completed = datetime.now(timezone.utc)
                self.last_failure = None
                self.last_failure_stage = None
                after = {day.prediction_date: day.booking_code for day in response.days}
                created = [code for day, code in after.items() if day not in before]
                reused = [code for day, code in after.items() if before.get(day) == code]
                replaced = [{"old": before[day], "new": code} for day, code in after.items() if day in before and before[day] != code]
                logger.info("refresh_completed active_prediction_dates=%s created=%s reused=%s replaced=%s", response.active_count, created, reused, replaced)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_failure = f"{type(exc).__name__}: {str(exc)[:500]}"
                self.last_failure_stage = "refresh_window"
                logger.exception("refresh_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue


forebet_draw_worker = ForebetDrawRefreshWorker()
