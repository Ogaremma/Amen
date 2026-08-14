from fastapi import APIRouter, Depends, Path

from app.schemas.booking import BookingResponse, RemoveSelectedRequest
from app.services.sportybet import get_booking, rebook_without_events
from app.services.history_store import history_store
from app.services.telegram_auth import TelegramUser
from app.services.telegram_identity import optional_verified_telegram_user

router = APIRouter(prefix="/api/v1/bookings", tags=["bookings"])


@router.get(
    "/{booking_code}",
    response_model=BookingResponse,
    summary="Fetch and parse a SportyBet booking by share code",
)
async def fetch_booking(
    booking_code: str = Path(..., description="SportyBet booking / share code, e.g. HW7UDH"),
    user: TelegramUser | None = Depends(optional_verified_telegram_user),
) -> BookingResponse:
    booking = await get_booking(booking_code)
    if user is not None:
        history_store.upsert(user.telegram_user_id, booking.booking_code, booking.total_selections, booking.remaining_odds)
    return booking


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
