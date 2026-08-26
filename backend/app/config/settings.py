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
    forebet_draw_refresh_jitter_seconds: float = Field(default=90.0, validation_alias="FOREBET_DRAW_REFRESH_JITTER_SECONDS", ge=0, le=300)
    forebet_challenge_failure_threshold: int = Field(default=3, validation_alias="FOREBET_CHALLENGE_FAILURE_THRESHOLD", ge=1)
    forebet_challenge_cooldown_seconds: float = Field(default=3600.0, validation_alias="FOREBET_CHALLENGE_COOLDOWN_SECONDS", gt=0)
    forebet_draw_prune_interval_seconds: float = Field(default=60.0, validation_alias="FOREBET_DRAW_PRUNE_INTERVAL_SECONDS", gt=0)
    forebet_draw_missing_event_timeout_hours: float = Field(default=6.0, validation_alias="FOREBET_DRAW_MISSING_EVENT_TIMEOUT_HOURS", gt=0)
    forebet_draw_source_urls: str = Field(default="", validation_alias="FOREBET_DRAW_SOURCE_URLS")
    forebet_browser_fallback_enabled: bool = Field(default=True, validation_alias="FOREBET_BROWSER_FALLBACK_ENABLED")
    forebet_browser_timeout: float = Field(default=30.0, validation_alias="FOREBET_BROWSER_TIMEOUT", gt=0, le=120)
    forebet_draw_selection_limit: int = Field(default=5, validation_alias="FOREBET_DRAW_SELECTION_LIMIT", ge=1)
    forebet_ingestion_token: str | None = Field(default=None, validation_alias="FOREBET_INGESTION_TOKEN")
    forebet_draw_booking_enabled: bool = Field(default=False, validation_alias="FOREBET_DRAW_BOOKING_ENABLED")
    forebet_draw_paper_booking_enabled: bool = Field(default=True, validation_alias="FOREBET_DRAW_PAPER_BOOKING_ENABLED")

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

    sportybet_acquisition_ttl_seconds: float = Field(
        default=300.0,
        validation_alias="SPORTYBET_ACQUISITION_TTL_SECONDS",
        gt=0,
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

    forebet_worker_enabled: bool = Field(default=True, validation_alias="FOREBET_WORKER_ENABLED")
    forebet_worker_lock_seconds: int = Field(default=840, validation_alias="FOREBET_WORKER_LOCK_SECONDS", ge=30)
    # A second, deliberately explicit authorization is required in addition to
    # FOREBET_DRAW_BOOKING_ENABLED. Paper mode remains the safe default.
    forebet_real_booking_authorized: bool = Field(default=False, validation_alias="FOREBET_REAL_BOOKING_AUTHORIZED")

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
