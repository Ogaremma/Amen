from datetime import date
from pydantic import BaseModel, Field
from app.schemas.forebet import ForebetMatch

class ForebetAcquisitionSnapshot(BaseModel):
    prediction_date: date
    source_url: str
    matches: list[ForebetMatch] = Field(default_factory=list)

class ForebetAcquisitionSnapshotRequest(BaseModel):
    snapshots: list[ForebetAcquisitionSnapshot] = Field(min_length=1, max_length=3)
    dry_run: bool = True
