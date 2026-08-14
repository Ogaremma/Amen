from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.bookings import router as bookings_router
from app.api.telegram import router as telegram_router
from app.api.history import router as history_router
from app.config.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="Amen SportyBet Booking Optimizer",
    description="Backend API for SportyBet football booking retrieval and rebooking.",
    version="0.1.0",
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


@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok"}
