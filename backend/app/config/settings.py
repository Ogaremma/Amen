from __future__ import annotations

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # dotenv is optional for test or container environments where env vars are already set
    load_dotenv = None

# Use BaseSettings from pydantic-settings when available (pydantic v2.13+ migration)
try:
    from pydantic_settings import BaseSettings
    from pydantic import Field, validator
except Exception:
    # Fallback to older pydantic import for BaseSettings if available
    from pydantic import BaseSettings, Field, validator


class Settings(BaseSettings):
    # SportyBet public share endpoint (no API key required).
    sportybet_base_url: str = Field(
        "https://www.sportybet.com", env="SPORTYBET_BASE_URL"
    )
    sportybet_share_path: str = Field(
        "/api/ng/orders/share", env="SPORTYBET_SHARE_PATH"
    )
    sportybet_timeout: float = Field(20.0, env="SPORTYBET_TIMEOUT")
    sportybet_user_agent: str = Field(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        env="SPORTYBET_USER_AGENT",
    )

    # Telegram bot token — BACKEND ONLY. Never exposed to the frontend, logs,
    # or API responses. Used to validate signed WebApp initData and to drive
    # the bot. Absent by default so the API runs fine without Telegram.
    telegram_bot_token: str | None = Field(None, env="TELEGRAM_BOT_TOKEN")
    # Public HTTPS URL of the deployed Mini App (the frontend). Used by the bot
    # for the /start button and the chat menu button. Configurable per env so we
    # never hardcode localhost for production.
    telegram_webapp_url: str | None = Field(None, env="TELEGRAM_WEBAPP_URL")
    # How long a signed initData payload stays valid (seconds). 24h default.
    telegram_auth_max_age: int = Field(86400, env="TELEGRAM_AUTH_MAX_AGE")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"], env="CORS_ORIGINS"
    )

    @validator("cors_origins", pre=True)
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # ignore env vars not declared above (e.g. ODDSPAPI_*)


def get_settings() -> Settings:
    return Settings()
