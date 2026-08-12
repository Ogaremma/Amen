"""Unit tests for the Telegram Mini App integration.

Covers:
  * initData HMAC validation (valid / invalid hash / expired / missing user /
    missing hash / empty / no-token)
  * the /api/v1/telegram/auth endpoint (valid, invalid, unconfigured)
  * the bot's /start handler, the WebApp launch button, and menu-button config
  * the bot token is never exposed in API responses or message payloads

Everything is mocked — no network and no real bot token are required.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import types
import unittest
from unittest import mock
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from app.main import app
from app.services import session_store as session_store_module
from app.services.telegram_auth import (
    TelegramAuthError,
    verify_init_data,
)
from app.telegram import bot as bot_module
from app.telegram.bot import (
    TelegramBot,
    build_commands,
    build_menu_button,
    build_start_message,
)

TEST_TOKEN = "123456:TEST-BOT-TOKEN-do-not-use"
SAMPLE_USER = {
    "id": 424242,
    "first_name": "Ada",
    "last_name": "Lovelace",
    "username": "ada",
    "language_code": "en",
    "is_premium": True,
}


def make_init_data(
    token: str,
    *,
    user: dict | None = SAMPLE_USER,
    auth_date: int | None = 1_700_000_000,
    include_hash: bool = True,
    tamper: bool = False,
    extra: dict | None = None,
) -> str:
    """Build an initData query string signed exactly like Telegram would."""
    fields: dict[str, str] = {"query_id": "AAExampleQueryId"}
    if auth_date is not None:
        fields["auth_date"] = str(auth_date)
    if user is not None:
        fields["user"] = json.dumps(user, separators=(",", ":"))
    if extra:
        fields.update(extra)

    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        digest = ("0" * len(digest)) if digest[0] != "0" else ("1" * len(digest))
    if include_hash:
        fields["hash"] = digest
    return urlencode(fields)


class VerifyInitDataTests(unittest.TestCase):
    def test_valid_init_data_returns_user(self):
        init_data = make_init_data(TEST_TOKEN)
        user = verify_init_data(
            init_data, TEST_TOKEN, max_age_seconds=None
        )
        self.assertEqual(user.telegram_user_id, 424242)
        self.assertEqual(user.username, "ada")
        self.assertEqual(user.first_name, "Ada")
        self.assertEqual(user.last_name, "Lovelace")
        self.assertEqual(user.language_code, "en")
        self.assertTrue(user.is_premium)

    def test_invalid_hash_rejected(self):
        init_data = make_init_data(TEST_TOKEN, tamper=True)
        with self.assertRaises(TelegramAuthError):
            verify_init_data(init_data, TEST_TOKEN, max_age_seconds=None)

    def test_wrong_token_rejected(self):
        # Signed with TEST_TOKEN, verified against a different token.
        init_data = make_init_data(TEST_TOKEN)
        with self.assertRaises(TelegramAuthError):
            verify_init_data(init_data, "999:OTHER-TOKEN", max_age_seconds=None)

    def test_expired_auth_date_rejected(self):
        init_data = make_init_data(TEST_TOKEN, auth_date=1_700_000_000)
        # "now" is one day + 1s after auth_date, max age is one day.
        with self.assertRaises(TelegramAuthError) as ctx:
            verify_init_data(
                init_data,
                TEST_TOKEN,
                max_age_seconds=86_400,
                now_ts=1_700_000_000 + 86_401,
            )
        self.assertIn("expired", str(ctx.exception).lower())

    def test_fresh_auth_date_accepted(self):
        init_data = make_init_data(TEST_TOKEN, auth_date=1_700_000_000)
        user = verify_init_data(
            init_data,
            TEST_TOKEN,
            max_age_seconds=86_400,
            now_ts=1_700_000_000 + 10,
        )
        self.assertEqual(user.telegram_user_id, 424242)

    def test_missing_user_rejected(self):
        init_data = make_init_data(TEST_TOKEN, user=None)
        with self.assertRaises(TelegramAuthError) as ctx:
            verify_init_data(init_data, TEST_TOKEN, max_age_seconds=None)
        self.assertIn("user", str(ctx.exception).lower())

    def test_missing_hash_rejected(self):
        init_data = make_init_data(TEST_TOKEN, include_hash=False)
        with self.assertRaises(TelegramAuthError) as ctx:
            verify_init_data(init_data, TEST_TOKEN, max_age_seconds=None)
        self.assertIn("hash", str(ctx.exception).lower())

    def test_empty_init_data_rejected(self):
        with self.assertRaises(TelegramAuthError):
            verify_init_data("", TEST_TOKEN, max_age_seconds=None)

    def test_missing_token_rejected(self):
        init_data = make_init_data(TEST_TOKEN)
        with self.assertRaises(TelegramAuthError):
            verify_init_data(init_data, "", max_age_seconds=None)

    def test_missing_auth_date_rejected_when_age_enforced(self):
        init_data = make_init_data(TEST_TOKEN, auth_date=None)
        with self.assertRaises(TelegramAuthError) as ctx:
            verify_init_data(init_data, TEST_TOKEN, max_age_seconds=86_400)
        self.assertIn("auth_date", str(ctx.exception).lower())


def _fake_settings(token: str | None, max_age: int = 86_400):
    return types.SimpleNamespace(
        telegram_bot_token=token,
        telegram_auth_max_age=max_age,
    )


class TelegramAuthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Isolate the session store between tests.
        session_store_module.session_store._sessions.clear()

    def test_auth_success_returns_verified_user_without_token(self):
        init_data = make_init_data(TEST_TOKEN, auth_date=1_700_000_000)
        with mock.patch(
            "app.api.telegram.get_settings",
            return_value=_fake_settings(TEST_TOKEN, max_age=10**12),
        ):
            resp = self.client.post(
                "/api/v1/telegram/auth", json={"init_data": init_data}
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["user"]["telegram_user_id"], 424242)
        self.assertEqual(body["user"]["username"], "ada")
        # The bot token must NEVER appear anywhere in the response.
        self.assertNotIn(TEST_TOKEN, resp.text)
        self.assertNotIn("token", body)
        self.assertNotIn("token", body["user"])

    def test_auth_invalid_hash_returns_401(self):
        init_data = make_init_data(TEST_TOKEN, tamper=True)
        with mock.patch(
            "app.api.telegram.get_settings",
            return_value=_fake_settings(TEST_TOKEN, max_age=10**12),
        ):
            resp = self.client.post(
                "/api/v1/telegram/auth", json={"init_data": init_data}
            )
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn(TEST_TOKEN, resp.text)

    def test_auth_not_configured_returns_503(self):
        init_data = make_init_data(TEST_TOKEN)
        with mock.patch(
            "app.api.telegram.get_settings",
            return_value=_fake_settings(None),
        ):
            resp = self.client.post(
                "/api/v1/telegram/auth", json={"init_data": init_data}
            )
        self.assertEqual(resp.status_code, 503)

    def test_auth_opens_session_for_user(self):
        init_data = make_init_data(TEST_TOKEN, auth_date=1_700_000_000)
        with mock.patch(
            "app.api.telegram.get_settings",
            return_value=_fake_settings(TEST_TOKEN, max_age=10**12),
        ):
            self.client.post("/api/v1/telegram/auth", json={"init_data": init_data})
        session = asyncio.run(
            session_store_module.session_store.get(424242)
        )
        self.assertIsNotNone(session)
        self.assertEqual(session.username, "ada")


class BotPayloadTests(unittest.TestCase):
    WEBAPP_URL = "https://amen.example.com"

    def test_start_message_has_webapp_launch_button(self):
        payload = build_start_message(555, self.WEBAPP_URL)
        self.assertEqual(payload["chat_id"], 555)
        button = payload["reply_markup"]["inline_keyboard"][0][0]
        self.assertIn("Open Amen", button["text"])
        self.assertEqual(button["web_app"]["url"], self.WEBAPP_URL)

    def test_menu_button_points_to_webapp(self):
        payload = build_menu_button(self.WEBAPP_URL)
        self.assertEqual(payload["menu_button"]["type"], "web_app")
        self.assertEqual(payload["menu_button"]["web_app"]["url"], self.WEBAPP_URL)

    def test_commands_include_start(self):
        payload = build_commands()
        commands = [c["command"] for c in payload["commands"]]
        self.assertIn("start", commands)


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


class _FakeClient:
    """Records POST/GET calls so we can assert what the bot sent."""

    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict]] = []

    async def post(self, url, json=None):
        self.posts.append((url, json))
        return _FakeResponse({"ok": True, "result": True})

    async def get(self, url, params=None):
        self.gets.append((url, params))
        return _FakeResponse({"ok": True, "result": []})

    async def aclose(self):
        return None


class BotHandlerTests(unittest.TestCase):
    WEBAPP_URL = "https://amen.example.com"

    def _bot(self):
        client = _FakeClient()
        bot = TelegramBot(TEST_TOKEN, self.WEBAPP_URL, client=client)
        return bot, client

    def test_start_command_sends_launch_button(self):
        bot, client = self._bot()
        update = {
            "update_id": 1,
            "message": {"chat": {"id": 777}, "text": "/start"},
        }
        handled = asyncio.run(bot.handle_update(update))
        self.assertTrue(handled)
        self.assertEqual(len(client.posts), 1)
        url, payload = client.posts[0]
        self.assertTrue(url.endswith("/sendMessage"))
        self.assertEqual(payload["chat_id"], 777)
        button = payload["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["web_app"]["url"], self.WEBAPP_URL)

    def test_start_with_bot_suffix_is_handled(self):
        bot, client = self._bot()
        update = {
            "update_id": 2,
            "message": {"chat": {"id": 777}, "text": "/start@AmenBot"},
        }
        self.assertTrue(asyncio.run(bot.handle_update(update)))

    def test_non_command_message_ignored(self):
        bot, client = self._bot()
        update = {
            "update_id": 3,
            "message": {"chat": {"id": 777}, "text": "hello there"},
        }
        self.assertFalse(asyncio.run(bot.handle_update(update)))
        self.assertEqual(len(client.posts), 0)

    def test_unknown_command_ignored(self):
        bot, client = self._bot()
        update = {
            "update_id": 4,
            "message": {"chat": {"id": 777}, "text": "/help"},
        }
        self.assertFalse(asyncio.run(bot.handle_update(update)))
        self.assertEqual(len(client.posts), 0)

    def test_configure_sets_menu_button_and_commands(self):
        bot, client = self._bot()
        asyncio.run(bot.configure())
        methods = [url.rsplit("/", 1)[-1] for url, _ in client.posts]
        self.assertIn("setChatMenuButton", methods)
        self.assertIn("setMyCommands", methods)

    def test_bot_requires_token_and_url(self):
        with self.assertRaises(ValueError):
            TelegramBot("", self.WEBAPP_URL)
        with self.assertRaises(ValueError):
            TelegramBot(TEST_TOKEN, "")

    def test_token_never_in_message_payloads(self):
        # The token belongs only in the outbound URL, never in any JSON body.
        bot, client = self._bot()
        update = {"update_id": 9, "message": {"chat": {"id": 1}, "text": "/start"}}
        asyncio.run(bot.handle_update(update))
        asyncio.run(bot.configure())
        for _url, payload in client.posts:
            self.assertNotIn(TEST_TOKEN, json.dumps(payload))

    def test_redact_strips_token_from_error_text(self):
        # httpx exceptions embed the request URL (with the token); redaction
        # must remove it before anything is logged.
        bot, _client = self._bot()
        leaky = f"HTTP error for url https://api.telegram.org/bot{TEST_TOKEN}/getUpdates"
        self.assertNotIn(TEST_TOKEN, bot._redact(leaky))


if __name__ == "__main__":
    unittest.main()
