from __future__ import annotations

from datetime import datetime

from app.schemas.prediction import PredictionEvaluation
from app.schemas.prediction_booking import PredictionBookingResult
from app.services.prediction_booking import (
    PredictionBookingService,
    prediction_booking_service,
)
from app.telegram.bot import TelegramBot
from app.telegram.prediction_formatter import TelegramPredictionFormatter


class TelegramPredictionDeliveryService:
    def __init__(
        self,
        bot: TelegramBot,
        *,
        booking_service: PredictionBookingService | None = None,
        formatter: TelegramPredictionFormatter | None = None,
    ) -> None:
        self.bot = bot
        self.booking_service = booking_service or prediction_booking_service
        self.formatter = formatter or TelegramPredictionFormatter()

    async def send_result(
        self,
        chat_id: int | str,
        result: PredictionBookingResult,
    ) -> PredictionBookingResult:
        payload = self.formatter.build_payload(chat_id, result)
        await self.bot.send_message(payload)
        return result

    async def send_current_prediction_message(
        self,
        chat_id: int | str,
        evaluations: list[PredictionEvaluation],
        *,
        now: datetime | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> PredictionBookingResult:
        result = await self.booking_service.create_booking_pools(
            evaluations,
            now=now,
            page_size=page_size,
            max_pages=max_pages,
        )
        return await self.send_result(chat_id, result)
