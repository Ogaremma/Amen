from fastapi import Header, HTTPException

from app.config.settings import get_settings
from app.services.telegram_auth import TelegramAuthError, TelegramUser, verify_init_data


def verified_telegram_user(
    x_telegram_init_data: str | None = Header(None, alias="X-Telegram-Init-Data"),
) -> TelegramUser:
    settings = get_settings()
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    try:
        return verify_init_data(
            x_telegram_init_data,
            settings.telegram_bot_token or "",
            max_age_seconds=settings.telegram_auth_max_age,
        )
    except TelegramAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def optional_verified_telegram_user(
    x_telegram_init_data: str | None = Header(None, alias="X-Telegram-Init-Data"),
) -> TelegramUser | None:
    if not x_telegram_init_data:
        return None
    return verified_telegram_user(x_telegram_init_data)
