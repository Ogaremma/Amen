from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---------------------------------------------------------
    # SportyBet
    # ---------------------------------------------------------

    sportybet_base_url: str = Field(
        default="https://www.sportybet.com",
        validation_alias="SPORTYBET_BASE_URL",
    )

    sportybet_share_path: str = Field(
        default="/api/ng/orders/share",
        validation_alias="SPORTYBET_SHARE_PATH",
    )

    sportybet_timeout: float = Field(
        default=20.0,
        validation_alias="SPORTYBET_TIMEOUT",
    )

    sportybet_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        validation_alias="SPORTYBET_USER_AGENT",
    )

    # ---------------------------------------------------------
    # Telegram
    # ---------------------------------------------------------

    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias="TELEGRAM_BOT_TOKEN",
    )

    telegram_webapp_url: str | None = Field(
        default=None,
        validation_alias="TELEGRAM_WEBAPP_URL",
    )

    telegram_auth_max_age: int = Field(
        default=86400,
        validation_alias="TELEGRAM_AUTH_MAX_AGE",
    )

    # ---------------------------------------------------------
    # CORS
    # ---------------------------------------------------------

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        validation_alias="CORS_ORIGINS",
    )

    # ---------------------------------------------------------
    # Pydantic Settings configuration
    # ---------------------------------------------------------

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_settings() -> Settings:
    return Settings()
