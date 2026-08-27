from __future__ import annotations

import asyncio
import logging
import socket
import uuid
import random
from datetime import datetime, timezone

from app.config.settings import get_settings
from app.services.forebet_draw_engine import ForebetDrawEngine, forebet_draw_engine
from app.services.forebet_dates import future_prediction_urls

logger = logging.getLogger("amen.forebet_draw_worker")

def _jittered_refresh_delay(interval: float, jitter: float) -> float:
    effective_jitter = min(jitter, interval * 0.25)
    return max(0.001, interval + random.uniform(-effective_jitter, effective_jitter))


def _challenge_cooldown_delay(failures: int, threshold: int, base: float, cap: float) -> float:
    exponent = max(0, failures - threshold)
    return min(cap, base * (2 ** exponent))


class ForebetDrawRefreshWorker:
    def __init__(self, engine: ForebetDrawEngine = forebet_draw_engine) -> None:
        self.engine = engine
        self._task: asyncio.Task | None = None
        self._prune_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_started: datetime | None = None
        self.last_completed: datetime | None = None
        self.last_failure: str | None = None
        self.last_failure_stage: str | None = None
        self.last_prune_completed: datetime | None = None
        self.consecutive_forebet_failures = 0
        self.forebet_cooldown_until: datetime | None = None
        self.owner_id = f"{socket.gethostname()}:{uuid.uuid4()}"

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            logger.info("worker_start_skipped reason=already_running")
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="forebet-draw-refresh-worker")
        self._prune_task = asyncio.create_task(self._run_prune(), name="forebet-draw-prune-worker")
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
        if self._prune_task is not None:
            await self._prune_task
            self._prune_task = None
        self._task = None
        logger.info("worker_shutdown_completed")

    async def _run(self) -> None:
        settings = get_settings()
        interval = settings.forebet_draw_refresh_interval_seconds
        while not self._stop.is_set():
            source_urls = future_prediction_urls()
            logger.info("refresh_started source_count=%s", len(source_urls))
            lock_seconds = getattr(settings, "forebet_worker_lock_seconds", max(30, int(interval) - 60))
            store = getattr(self.engine, "store", None)
            acquired = store.acquire_job_lock("rolling-draw-refresh", self.owner_id, lock_seconds) if store and hasattr(store, "acquire_job_lock") else True
            if not acquired:
                logger.info("refresh_skipped reason=distributed_lock_held")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue
                continue
            try:
                before = {day.prediction_date: day.booking_code for day in self.engine.get_active_window().days}
                response = await self.engine.refresh_window(source_urls)
                self.last_completed = datetime.now(timezone.utc)
                provider_failed = any(
                    any("PROVIDER_FAILURE: ForebetAccessDeniedError" in message for message in day.diagnostics)
                    for day in response.days
                )
                if provider_failed:
                    self.consecutive_forebet_failures += 1
                    self.last_failure = "Forebet Cloudflare challenge blocked the rolling refresh"
                    self.last_failure_stage = "forebet_acquisition"
                else:
                    self.consecutive_forebet_failures = 0
                    self.forebet_cooldown_until = None
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
            finally:
                if store and hasattr(store, "release_job_lock"):
                    store.release_job_lock("rolling-draw-refresh", self.owner_id)
            threshold = getattr(settings, "forebet_challenge_failure_threshold", 3)
            if self.consecutive_forebet_failures >= threshold:
                base_delay = getattr(settings, "forebet_challenge_cooldown_seconds", 3600.0)
                max_delay = getattr(settings, "forebet_challenge_cooldown_max_seconds", 21600.0)
                delay = _challenge_cooldown_delay(self.consecutive_forebet_failures, threshold, base_delay, max_delay)
                self.forebet_cooldown_until = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + delay, timezone.utc)
                logger.warning("forebet_cooldown failures=%s delay_seconds=%s", self.consecutive_forebet_failures, delay)
            else:
                jitter = getattr(settings, "forebet_draw_refresh_jitter_seconds", 90.0)
                delay = _jittered_refresh_delay(interval, jitter)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue

    async def _run_prune(self) -> None:
        interval = getattr(get_settings(), "forebet_draw_prune_interval_seconds", 60.0)
        store = getattr(self.engine, "store", None)
        while not self._stop.is_set():
            acquired = store.acquire_job_lock("rolling-draw-prune", self.owner_id, max(30, int(interval) + 30)) if store and hasattr(store, "acquire_job_lock") else True
            if acquired:
                try:
                    if hasattr(self.engine, "prune_kickoff_passed"):
                        await self.engine.prune_kickoff_passed()
                    self.last_prune_completed = datetime.now(timezone.utc)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("prune_failed")
                finally:
                    if store and hasattr(store, "release_job_lock"):
                        store.release_job_lock("rolling-draw-prune", self.owner_id)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue


forebet_draw_worker = ForebetDrawRefreshWorker()
