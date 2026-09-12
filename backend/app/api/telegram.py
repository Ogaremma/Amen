from __future__ import annotations

import hmac
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException

from app.config.settings import get_settings
from app.schemas.telegram import (
    
    TelegramAuthRequest,
    TelegramAuthResponse,
    TelegramUserOut,
)
from app.services.session_store import session_store
from app.services.telegram_user_store import TelegramUserStore, telegram_user_store
from app.services.telegram_auth import TelegramAuthError, verify_init_data
from app.telegram.bot import TelegramBot

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


def get_telegram_user_store() -> TelegramUserStore:
    return telegram_user_store


@router.post(
    "/webhook",
    status_code=200,
    summary="Receive Telegram bot updates",
    description=(
        "Authenticates Telegram's secret-token header, processes the update with "
        "the existing bot command handler, and persists verified Telegram users. "
        "No credential or raw update payload is logged."
    ),
)
async def telegram_webhook(
    update: dict[str, Any],
    user_store: TelegramUserStore = Depends(get_telegram_user_store),
    x_telegram_bot_api_secret_token: str | None = Header(
        None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
) -> dict[str, bool]:
    settings = get_settings()
    configured_secret = settings.telegram_webhook_secret
    if not configured_secret:
        raise HTTPException(
            status_code=503,
            detail="Telegram webhook is not configured",
        )
    if (
        x_telegram_bot_api_secret_token is None
        or not hmac.compare_digest(
            x_telegram_bot_api_secret_token.encode("utf-8"),
            configured_secret.encode("utf-8"),
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram webhook secret",
        )
    if not settings.telegram_bot_token or not settings.telegram_webapp_url:
        raise HTTPException(
            status_code=503,
            detail="Telegram bot is not configured",
        )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35.0)) as client:
            bot = TelegramBot(
                settings.telegram_bot_token,
                settings.telegram_webapp_url,
                client=client,
                user_store=user_store,
            )
            await bot.handle_update(update)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Telegram update processing failed",
        ) from None

    return {"ok": True}


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
async def telegram_auth(
    request: TelegramAuthRequest,
    user_store: TelegramUserStore = Depends(get_telegram_user_store),
) -> TelegramAuthResponse:
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
    user_store.upsert_from_auth(user.telegram_user_id)

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
