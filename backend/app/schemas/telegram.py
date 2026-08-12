from __future__ import annotations

from pydantic import BaseModel, Field


class TelegramAuthRequest(BaseModel):
    """Frontend sends the RAW signed initData string (never initDataUnsafe)."""

    init_data: str = Field(
        ...,
        description="Raw Telegram.WebApp.initData query string, signed by Telegram",
    )


class TelegramUserOut(BaseModel):
    """The verified identity we return to the frontend after validation.

    Note: this NEVER contains the bot token or the raw initData hash.
    """

    telegram_user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None
    is_premium: bool = False


class TelegramAuthResponse(BaseModel):
    ok: bool = Field(True, description="True when initData was cryptographically valid")
    user: TelegramUserOut
    current_booking_code: str | None = Field(
        None, description="This user's latest booking code, if any is on file"
    )
