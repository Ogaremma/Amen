from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field
from typing import Literal


class BookingSelection(BaseModel):
    """A single football selection resolved from a SportyBet ticket."""

    id: str = Field(..., description="Selection identifier (uses the SportyBet eventId)")
    event_id: str = Field(..., description="SportyBet event identifier, e.g. sr:match:72348792")
    home: str = Field(..., description="Home team name")
    away: str = Field(..., description="Away team name")
    competition: str = Field(..., description="Tournament / league name")
    category: str = Field(..., description="Country or category name")
    kickoff: datetime = Field(..., description="Full timezone-aware kickoff datetime (UTC)")
    kickoff_date: str = Field(..., description="Kickoff date, YYYY-MM-DD (UTC)")
    kickoff_time: str = Field(..., description="Kickoff time, HH:MM 24-hour (UTC)")
    local_kickoff_date: str = Field(..., description="Kickoff date in Africa/Lagos, YYYY-MM-DD")
    local_kickoff_time: str = Field(..., description="Kickoff time in Africa/Lagos, HH:MM")
    market: str = Field(..., description="Selected market description")
    outcome: str = Field(..., description="Selected outcome description")
    odds: float | None = Field(None, description="Odds for the selected outcome")
    specifier: str | None = Field(None, description="Market specifier, e.g. total=8.5")
    status: str | None = Field(None, description="Match status reported by SportyBet")
    game_status: Literal["upcoming", "live", "ended"] = Field(
        ..., description="Normalized game status for the Amen ticket UI"
    )


class BookingResponse(BaseModel):
    """Clean, chronologically sorted representation of a SportyBet booking."""

    booking_code: str = Field(..., description="SportyBet booking / share code")
    total_selections: int = Field(..., description="Number of selections returned")
    total_odds: float | None = Field(
        None, description="Total odds as reported by SportyBet (displayTotalOdds)"
    )
    remaining_odds: float = Field(
        ..., description="Product of valid odds for upcoming selections only"
    )
    selections: list[BookingSelection] = Field(
        default_factory=list,
        description="Selections sorted by complete kickoff datetime (ascending)",
    )


class RemoveSelectedRequest(BaseModel):
    """Frontend request to remove one or MORE selections in a single rebooking.

    The frontend never sends SportyBet betting payloads; it only names which
    selections to drop (by event_id). The backend re-fetches the authoritative
    ticket, removes exactly these events, and rebuilds the remaining set.
    """

    event_ids: list[str] = Field(
        default_factory=list,
        description="SportyBet eventIds of the selections to remove (deduplicated by the backend)",
    )
