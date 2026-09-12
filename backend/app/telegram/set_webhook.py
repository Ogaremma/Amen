from __future__ import annotations

import argparse
import json
from urllib.parse import urlparse

import httpx

from app.config.settings import get_settings


TELEGRAM_API_BASE = "https://api.telegram.org"
WEBHOOK_PATH = "/api/v1/telegram/webhook"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register or inspect the production Telegram webhook."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Inspect the currently registered Telegram webhook.",
    )
    return parser


def validate_webhook_config(
    *,
    bot_token: str | None,
    webhook_url: str | None,
    webhook_secret: str | None,
) -> None:
    if not bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required.")
    if not webhook_url:
        raise SystemExit("TELEGRAM_WEBHOOK_URL is required.")
    if not webhook_secret:
        raise SystemExit("TELEGRAM_WEBHOOK_SECRET is required.")

    parsed = urlparse(webhook_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SystemExit("TELEGRAM_WEBHOOK_URL must be an HTTPS URL.")
    if parsed.path != WEBHOOK_PATH:
        raise SystemExit(
            "TELEGRAM_WEBHOOK_URL must end with "
            "/api/v1/telegram/webhook."
        )
    if parsed.query or parsed.fragment:
        raise SystemExit("TELEGRAM_WEBHOOK_URL must not contain a query or fragment.")
    if len(webhook_secret) < 32:
        raise SystemExit(
            "TELEGRAM_WEBHOOK_SECRET must contain at least 32 characters."
        )
    if webhook_secret == bot_token:
        raise SystemExit(
            "TELEGRAM_WEBHOOK_SECRET must not equal TELEGRAM_BOT_TOKEN."
        )


def _api_url(bot_token: str, method: str) -> str:
    return f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    bot_token = settings.telegram_bot_token
    webhook_url = settings.telegram_webhook_url
    webhook_secret = settings.telegram_webhook_secret
    validate_webhook_config(
        bot_token=bot_token,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
    )

    with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
        if args.status:
            response = client.post(_api_url(bot_token, "getWebhookInfo"))
            response.raise_for_status()
            payload = response.json()
            if payload.get("ok") is not True:
                raise SystemExit("Telegram returned an error while reading webhook status.")
            result = payload.get("result", {})
            safe_result = {
                key: result.get(key)
                for key in (
                    "url",
                    "has_custom_certificate",
                    "pending_update_count",
                    "ip_address",
                    "last_error_date",
                    "last_error_message",
                    "max_connections",
                )
            }
            print(json.dumps(safe_result, sort_keys=True))
            return

        response = client.post(
            _api_url(bot_token, "setWebhook"),
            json={
                "url": webhook_url,
                "secret_token": webhook_secret,
                "allowed_updates": ["message", "edited_message"],
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is not True:
            description = payload.get("description", "unknown Telegram error")
            raise SystemExit(f"Telegram webhook registration failed: {description}")
        print("Telegram webhook registered successfully.")


if __name__ == "__main__":
    main()
