"""Amen Telegram bot — long-polling, built on raw httpx (no framework).

Why no framework: none is installed, the app is tiny (one command + a WebApp
button), and a raw long-poll loop runs as its own process with zero risk of
clashing with FastAPI/uvicorn's event loop. If the bot grows, swapping in
python-telegram-bot later is a contained change.

The pure ``build_*`` helpers are separated from all network I/O so they can be
unit-tested without hitting Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config.settings import get_settings

logger = logging.getLogger("amen.telegram.bot")

TELEGRAM_API_BASE = "https://api.telegram.org"

WELCOME_TEXT = (
    "🙏 <b>Welcome to Amen</b>\n\n"
    "Amen helps you clean up your SportyBet booking codes — load a code, tick the "
    "games you don't want, and remove them all at once. SportyBet regenerates a "
    "fresh code and recalculates the odds for you.\n\n"
    "Tap the button below to open the app."
)

OPEN_BUTTON_TEXT = "🚀 Open Amen"
MENU_BUTTON_TEXT = "Open Amen"


def build_start_message(chat_id: int, webapp_url: str) -> dict[str, Any]:
    """Payload for ``sendMessage`` with a WebApp launch button under /start."""
    return {
        "chat_id": chat_id,
        "text": WELCOME_TEXT,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": OPEN_BUTTON_TEXT, "web_app": {"url": webapp_url}}]
            ]
        },
    }


def build_menu_button(webapp_url: str) -> dict[str, Any]:
    """Payload for ``setChatMenuButton`` so the chat menu opens the Mini App too."""
    return {
        "menu_button": {
            "type": "web_app",
            "text": MENU_BUTTON_TEXT,
            "web_app": {"url": webapp_url},
        }
    }


def build_commands() -> dict[str, Any]:
    """Payload for ``setMyCommands`` (just /start for now)."""
    return {
        "commands": [
            {"command": "start", "description": "Open Amen and get the app button"}
        ]
    }


def _extract_command(update: dict[str, Any]) -> tuple[int, str] | None:
    """Return (chat_id, command) for a text command update, else None.

    Pure and defensive: tolerates any shape Telegram might send.
    """
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(text, str):
        return None
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    # "/start@BotName arg" -> "start"
    command = stripped.split()[0].lstrip("/").split("@")[0].lower()
    return chat_id, command


class TelegramBot:
    def __init__(
        self,
        token: str,
        webapp_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        api_base: str = TELEGRAM_API_BASE,
    ) -> None:
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required to run the bot")
        if not webapp_url:
            raise ValueError("TELEGRAM_WEBAPP_URL is required to run the bot")
        self._token = token
        self._webapp_url = webapp_url
        self._api_base = api_base.rstrip("/")
        self._client = client
        self._owns_client = client is None

    def _url(self, method: str) -> str:
        # Token appears only in the outbound URL to Telegram, never logged.
        return f"{self._api_base}/bot{self._token}/{method}"

    def _redact(self, text: str) -> str:
        """Strip the bot token from any string before it can reach a log.

        httpx exceptions embed the full request URL (which contains the token),
        so every exception message is passed through here first.
        """
        return text.replace(self._token, "***") if self._token else text

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        response = await self._client.post(self._url(method), json=payload)
        response.raise_for_status()
        return response.json()

    async def configure(self) -> None:
        """Register the chat menu button and command list (idempotent)."""
        await self._call("setChatMenuButton", build_menu_button(self._webapp_url))
        await self._call("setMyCommands", build_commands())
        logger.info("Configured Telegram menu button and commands")

    async def handle_update(self, update: dict[str, Any]) -> bool:
        """Process one update. Returns True if a /start reply was sent."""
        parsed = _extract_command(update)
        if parsed is None:
            return False
        chat_id, command = parsed
        if command != "start":
            return False
        await self._call("sendMessage", build_start_message(chat_id, self._webapp_url))
        logger.info("Handled /start for chat %s", chat_id)
        return True

    async def run(self) -> None:
        """Long-poll ``getUpdates`` forever, dispatching /start."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0))
        try:
            await self.configure()
            offset: int | None = None
            logger.info("Amen bot polling for updates…")
            while True:
                try:
                    params: dict[str, Any] = {"timeout": 30}
                    if offset is not None:
                        params["offset"] = offset
                    resp = await self._client.get(
                        self._url("getUpdates"), params=params
                    )
                    resp.raise_for_status()
                    body = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    # Transient network / decode issue: back off and retry.
                    logger.warning("getUpdates failed: %s", self._redact(str(exc)))
                    await asyncio.sleep(3)
                    continue

                for update in body.get("result", []):
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    try:
                        await self.handle_update(update)
                    except httpx.HTTPError as exc:
                        logger.warning("Failed handling update: %s", self._redact(str(exc)))
        finally:
            if self._owns_client and self._client is not None:
                await self._client.aclose()
                self._client = None


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    token = settings.telegram_bot_token
    webapp_url = settings.telegram_webapp_url
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Add it to backend/.env before running the bot."
        )
    if not webapp_url:
        raise SystemExit(
            "TELEGRAM_WEBAPP_URL is not set. Add your deployed Mini App URL to backend/.env."
        )
    bot = TelegramBot(token, webapp_url)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
