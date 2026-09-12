from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.prediction_booking import PredictionBookingResult
from app.schemas.prediction_daily_production import (
    DailyProductionDeliveryOutcome,
    PredictionMessageDeliveryRecord,
)


class TelegramRecipient(BaseModel):
    telegram_user_id: int | str
    telegram_chat_id: int | None = None

    @property
    def chat_id(self) -> int | str:
        return self.telegram_chat_id or self.telegram_user_id


class PredictionRecipientDeliveryResult(BaseModel):
    recipient: TelegramRecipient
    outcome: DailyProductionDeliveryOutcome
    qualifying_delivery: PredictionMessageDeliveryRecord | None = None
    prediction_delivery: PredictionMessageDeliveryRecord | None = None


class DailyPredictionBroadcastResult(BaseModel):
    outcome: DailyProductionDeliveryOutcome
    generation_identity: str
    input_identity: str
    delivery_identity: str | None = None
    timezone_name: str
    today: date
    tomorrow: date
    generated_at: datetime | None = None
    error_summary: str | None = None
    result: PredictionBookingResult | None = None
    recipients: list[PredictionRecipientDeliveryResult] = Field(default_factory=list)
    recipient_count: int = 0
    delivered_recipient_count: int = 0
    failed_recipient_count: int = 0
    undeliverable_recipient_count: int = 0
