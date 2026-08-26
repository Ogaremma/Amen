from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.bookings import router as bookings_router
from app.api.telegram import router as telegram_router
from app.api.history import router as history_router
from app.api.forebet import router as forebet_router
from app.config.settings import get_settings
from app.services.forebet_draw_worker import forebet_draw_worker
from sqlalchemy import text
from app.services.forebet_draw_engine import forebet_draw_engine

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.forebet_worker_enabled:
        await forebet_draw_worker.start()
    try:
        yield
    finally:
        if settings.forebet_worker_enabled:
            await forebet_draw_worker.stop()


app = FastAPI(
    title="Amen SportyBet Booking Optimizer",
    description="Backend API for SportyBet football booking retrieval and rebooking.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(bookings_router)
app.include_router(telegram_router)
app.include_router(history_router)
app.include_router(forebet_router)


@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok"}


@app.get("/readiness", summary="Operational readiness")
async def readiness():
    database = {"reachable": False, "error": None}
    try:
        with forebet_draw_engine.store.engine.connect() as db:
            db.execute(text("SELECT 1"))
        database["reachable"] = True
    except Exception as exc:
        database["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return {
        "status": "ready" if database["reachable"] else "not_ready",
        "database": database,
        "worker": {
            "enabled": settings.forebet_worker_enabled,
            "running": forebet_draw_worker.running,
            "last_refresh_started": forebet_draw_worker.last_started,
            "last_refresh_completed": forebet_draw_worker.last_completed,
            "last_failure": forebet_draw_worker.last_failure,
            "last_failure_stage": forebet_draw_worker.last_failure_stage,
            "last_prune_completed": forebet_draw_worker.last_prune_completed,
            "consecutive_forebet_failures": forebet_draw_worker.consecutive_forebet_failures,
            "forebet_cooldown_until": forebet_draw_worker.forebet_cooldown_until,
        },
    }
