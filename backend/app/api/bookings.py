from fastapi import APIRouter, Path

from app.schemas.booking import BookingResponse, RemoveSelectedRequest
from app.services.sportybet import get_booking, rebook_without_events

router = APIRouter(prefix="/api/v1/bookings", tags=["bookings"])


@router.get(
    "/{booking_code}",
    response_model=BookingResponse,
    summary="Fetch and parse a SportyBet booking by share code",
)
async def fetch_booking(
    booking_code: str = Path(..., description="SportyBet booking / share code, e.g. HW7UDH"),
) -> BookingResponse:
    return await get_booking(booking_code)


@router.post(
    "/{booking_code}/remove-selected",
    response_model=BookingResponse,
    summary="Remove one or more selected games and rebook via SportyBet (single request)",
    description=(
        "Removes every selection named in event_ids from the current booking, "
        "asks SportyBet ONCE to generate a new ticket from the remaining "
        "selections, and returns the newly generated booking (new code + "
        "recalculated authoritative odds). Exactly one SportyBet rebooking "
        "request is issued no matter how many games are removed."
    ),
)
async def remove_selected(
    request: RemoveSelectedRequest,
    booking_code: str = Path(..., description="Current SportyBet booking / share code"),
) -> BookingResponse:
    return await rebook_without_events(booking_code, request.event_ids)
