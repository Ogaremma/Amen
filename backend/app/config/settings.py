from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---------------------------------------------------------
    # Forebet
    # ---------------------------------------------------------

    forebet_base_url: str = Field(default="https://www.forebet.com", validation_alias="FOREBET_BASE_URL")
    forebet_timeout: float = Field(default=20.0, validation_alias="FOREBET_TIMEOUT")
    forebet_user_agent: str = Field(default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", validation_alias="FOREBET_USER_AGENT")
    forebet_retries: int = Field(default=2, validation_alias="FOREBET_RETRIES", ge=0, le=5)
    forebet_retry_backoff: float = Field(default=0.5, validation_alias="FOREBET_RETRY_BACKOFF", ge=0)
    forebet_draw_refresh_interval_seconds: float = Field(default=900.0, validation_alias="FOREBET_DRAW_REFRESH_INTERVAL_SECONDS", gt=0)
    forebet_draw_source_urls: str = Field(default="", validation_alias="FOREBET_DRAW_SOURCE_URLS")

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

    sportybet_upcoming_path: str = Field(
        default="/api/ng/factsCenter/pcUpcomingEvents",
        validation_alias="SPORTYBET_UPCOMING_PATH",
    )

    sportybet_football_sport_id: str = Field(
        default="sr:sport:1",
        validation_alias="SPORTYBET_FOOTBALL_SPORT_ID",
    )

    sportybet_upcoming_market_ids: str = Field(
        default="1,18,10,29,11,26,36,14,60100",
        validation_alias="SPORTYBET_UPCOMING_MARKET_IDS",
    )

    sportybet_upcoming_page_size: int = Field(
        default=100,
        validation_alias="SPORTYBET_UPCOMING_PAGE_SIZE",
        ge=1,
        le=100,
    )

    sportybet_upcoming_max_pages: int = Field(
        default=20,
        validation_alias="SPORTYBET_UPCOMING_MAX_PAGES",
        ge=1,
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

    history_database_path: str = Field(
        default="amen_history.sqlite3",
        validation_alias="HISTORY_DATABASE_PATH",
    )

    database_url: str | None = Field(
        default=None,
        validation_alias="DATABASE_URL",
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
