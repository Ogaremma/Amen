from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config.settings import get_settings
from app.schemas.telegram import (
    
    TelegramAuthRequest,
    TelegramAuthResponse,
    TelegramUserOut,
)
from app.services.session_store import session_store
from app.services.telegram_auth import TelegramAuthError, verify_init_data

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


@router.post(
    "/auth",
    response_model=TelegramAuthResponse,
    summary="Validate Telegram Mini App initData and open a user session",
    description=(
        "Verifies the signed initData using the backend-only bot token (HMAC-SHA256). "
        "On success, upserts an in-memory session for the Telegram user and returns "
        "their verified identity plus their latest booking code (if any). The bot "
        "token is never returned or logged."
    ),
)
async def telegram_auth(request: TelegramAuthRequest) -> TelegramAuthResponse:
    settings = get_settings()
    token = settings.telegram_bot_token
    if not token:
        # Not configured -> we cannot validate anything. 503, not 200.
        raise HTTPException(
            status_code=503,
            detail="Telegram authentication is not configured on the server",
        )

    try:
        user = verify_init_data(
            request.init_data,
            token,
            max_age_seconds=settings.telegram_auth_max_age,
        )
    except TelegramAuthError as exc:
        # 401: the caller presented credentials we could not trust.
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    session = await session_store.upsert_from_login(
        user.telegram_user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        profile=user.raw,
    )

    return TelegramAuthResponse(
        ok=True,
        user=TelegramUserOut(
            telegram_user_id=user.telegram_user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
            is_premium=user.is_premium,
        ),
        current_booking_code=session.current_booking_code,
    )
