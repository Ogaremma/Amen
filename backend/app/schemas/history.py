from datetime import datetime

from pydantic import BaseModel


class HistoryItem(BaseModel):
    id: int
    booking_code: str
    loaded_at: datetime
    selection_count: int | None = None
    remaining_odds: float | None = None
