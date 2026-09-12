from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.prediction_booking import PredictionBookingResult


class PredictionMessageCategory(str, Enum):
    qualifying = 'qualifying'
    predictions = 'predictions'


class PredictionMessageDeliveryStatus(str, Enum):
    pending = 'pending'
    delivered = 'delivered'
    failed = 'failed'
    undeliverable = 'undeliverable'


class DailyProductionDeliveryOutcome(str, Enum):
    delivered = 'delivered'
    already_delivered = 'already_delivered'
    partially_delivered = 'partially_delivered'
    failed = 'failed'
    in_progress = 'in_progress'
    generation_failed = 'generation_failed'


class PredictionMessageDeliveryRecord(BaseModel):
    id: int
    generation_identity: str
    category: PredictionMessageCategory
    delivery_identity: str
    input_identity: str
    telegram_user_id: int | None = None
    batch_identities: list[str] = Field(default_factory=list)
    booking_codes: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    status: PredictionMessageDeliveryStatus
    sent_at: datetime | None = None
    error_summary: str | None = None
    retry_count: int = Field(default=0, ge=0)
    claim_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DailyPredictionProductionResult(BaseModel):
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
    qualifying_delivery: PredictionMessageDeliveryRecord | None = None
    prediction_delivery: PredictionMessageDeliveryRecord | None = None
