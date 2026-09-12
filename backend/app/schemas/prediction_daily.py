from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel

from app.schemas.prediction_booking import PredictionBookingResult


class PredictionGenerationStatus(str, Enum):
    generating = 'generating'
    generated = 'generated'
    delivery_pending = 'delivery_pending'
    delivered = 'delivered'
    delivery_failed = 'delivery_failed'
    generation_failed = 'generation_failed'


class DailyPredictionDeliveryOutcome(str, Enum):
    delivered = 'delivered'
    already_delivered = 'already_delivered'
    in_progress = 'in_progress'
    failed = 'failed'


class PredictionGenerationDeliveryRecord(BaseModel):
    id: int
    generation_identity: str
    input_identity: str | None = None
    delivery_identity: str | None = None
    timezone_name: str
    today: date
    tomorrow: date
    generated_at: datetime | None = None
    status: PredictionGenerationStatus
    delivered_at: datetime | None = None
    error_summary: str | None = None
    result: PredictionBookingResult | None = None
    qualifying_batch_identities: list[str] = []
    prediction_batch_identities: list[str] = []
    booking_codes: list[str] = []
    claim_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DailyPredictionDeliveryResult(BaseModel):
    outcome: DailyPredictionDeliveryOutcome
    state: PredictionGenerationStatus
    generation_identity: str
    input_identity: str
    delivery_identity: str | None = None
    timezone_name: str
    today: date
    tomorrow: date
    generated_at: datetime | None = None
    delivered_at: datetime | None = None
    error_summary: str | None = None
    result: PredictionBookingResult | None = None
