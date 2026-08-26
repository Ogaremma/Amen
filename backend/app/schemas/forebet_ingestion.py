from datetime import date, datetime
from pydantic import BaseModel, Field, field_validator, model_validator
from app.schemas.forebet import ForebetMatch
from app.schemas.forebet import SportyBetEvent

class ForebetAcquisitionSnapshot(BaseModel):
    prediction_date: date
    source_url: str
    matches: list[ForebetMatch] = Field(default_factory=list)
    raw_html: str | None = None
    source: str = "forebet"

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if value.casefold() != "forebet":
            raise ValueError("snapshot source must be Forebet")
        return "forebet"

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("snapshot source_url is required")
        return value

    @field_validator("raw_html")
    @classmethod
    def validate_raw_html(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("raw_html must be non-empty")
        return value

class ForebetAcquisitionSnapshotRequest(BaseModel):
    snapshots: list[ForebetAcquisitionSnapshot] = Field(min_length=1, max_length=3)
    dry_run: bool = True
    sportybet_events: list[SportyBetEvent] | None = None
    use_local_sportybet_snapshots: bool = False

class SportyBetFixtureSnapshotRequest(BaseModel):
    source: str = "sportybet"
    retrieved_at: datetime | None = None
    events: list[SportyBetEvent] = Field(default_factory=list)
    raw_pages: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_payload(self):
        if not self.events and not self.raw_pages:
            raise ValueError("snapshot must contain events or raw_pages")
        return self

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if value.casefold() != "sportybet":
            raise ValueError("snapshot source must be SportyBet")
        return "sportybet"
