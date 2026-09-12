from __future__ import annotations

import contextlib
import io
import sys
import types
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from app.api.telegram import get_telegram_user_store
from app.main import app
from app.telegram import set_webhook


TEST_TOKEN = "123456:TEST-BOT-TOKEN-do-not-use"
TEST_WEBAPP_URL = "https://amen.example.com"
TEST_WEBHOOK_SECRET = "a" * 32 + "-test-secret"


def _settings(
    *,
    secret: str | None = TEST_WEBHOOK_SECRET,
    token: str | None = TEST_TOKEN,
    webapp_url: str | None = TEST_WEBAPP_URL,
):
    return types.SimpleNamespace(
        telegram_webhook_secret=secret,
        telegram_bot_token=token,
        telegram_webapp_url=webapp_url,
    )


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeTelegramClient:
    def __init__(self):
        self.posts = []
        self.gets = []

    async def post(self, url, json=None):
        self.posts.append((url, json))
        return _FakeResponse({"ok": True, "result": True})

    async def get(self, url, params=None):
        self.gets.append((url, params))
        return _FakeResponse({"ok": True, "result": []})

    async def aclose(self):
        return None


class _FakeAsyncClient:
    def __init__(self, inner):
        self.inner = inner

    async def __aenter__(self):
        return self.inner

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None


class _FailingTelegramClient:
    async def post(self, url, json=None):
        raise httpx.ConnectError("Telegram connection failed")

    async def get(self, url, params=None):
        raise AssertionError("Webhook handling must not poll Telegram")

    async def aclose(self):
        return None


class RecordingUserStore:
    def __init__(self):
        self.calls = []

    def upsert_from_start(self, telegram_user_id, telegram_chat_id):
        self.calls.append((telegram_user_id, telegram_chat_id))


class TelegramWebhookEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.user_store = RecordingUserStore()
        self.telegram_client = _FakeTelegramClient()
        app.dependency_overrides[get_telegram_user_store] = lambda: self.user_store

    def tearDown(self):
        app.dependency_overrides.pop(get_telegram_user_store, None)

    def _request(
        self,
        update,
        *,
        secret=TEST_WEBHOOK_SECRET,
        content=None,
        client=None,
    ):
        headers = {"X-Telegram-Bot-Api-Secret-Token": secret}
        with mock.patch(
            "app.api.telegram.get_settings",
            return_value=_settings(),
        ), mock.patch(
            "app.api.telegram.httpx.AsyncClient",
            return_value=_FakeAsyncClient(client or self.telegram_client),
        ):
            if content is None:
                return self.client.post(
                    "/api/v1/telegram/webhook",
                    json=update,
                    headers=headers,
                )
            return self.client.post(
                "/api/v1/telegram/webhook",
                content=content,
                headers=headers,
            )

    def test_valid_start_update_is_processed_and_persisted(self):
        update = {
            "update_id": 10,
            "message": {
                "chat": {"id": 777},
                "from": {"id": 424242},
                "text": "/start",
            },
        }
        response = self._request(update)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(self.user_store.calls, [(424242, 777)])
        self.assertEqual(len(self.telegram_client.posts), 1)
        url, payload = self.telegram_client.posts[0]
        self.assertTrue(url.endswith("/sendMessage"))
        self.assertEqual(payload["chat_id"], 777)
        self.assertNotIn(TEST_TOKEN, response.text)
        self.assertNotIn(TEST_WEBHOOK_SECRET, response.text)

    def test_webhook_reuses_update_handler_without_polling(self):
        update = {
            "update_id": 11,
            "message": {"chat": {"id": 777}, "text": "/start"},
        }
        response = self._request(update)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.telegram_client.gets, [])
        self.assertEqual(len(self.telegram_client.posts), 1)

    def test_invalid_secret_is_rejected_without_processing(self):
        update = {
            "update_id": 12,
            "message": {"chat": {"id": 777}, "text": "/start"},
        }
        response = self._request(update, secret="invalid-secret")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.user_store.calls, [])
        self.assertEqual(self.telegram_client.posts, [])
        self.assertNotIn(TEST_TOKEN, response.text)
        self.assertNotIn(TEST_WEBHOOK_SECRET, response.text)

    def test_missing_secret_configuration_returns_503(self):
        update = {"update_id": 13, "message": {"chat": {"id": 777}, "text": "/start"}}
        headers = {"X-Telegram-Bot-Api-Secret-Token": TEST_WEBHOOK_SECRET}
        with mock.patch(
            "app.api.telegram.get_settings",
            return_value=_settings(secret=None),
        ):
            response = self.client.post(
                "/api/v1/telegram/webhook",
                json=update,
                headers=headers,
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.user_store.calls, [])

    def test_malformed_update_shape_is_accepted_without_reply(self):
        response = self._request({"update_id": 14, "message": {"text": "hello"}})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(self.user_store.calls, [])
        self.assertEqual(self.telegram_client.posts, [])

    def test_invalid_json_is_rejected(self):
        response = self._request(None, content=b"{")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.user_store.calls, [])
        self.assertEqual(self.telegram_client.posts, [])

    def test_telegram_failure_returns_502_without_credential_leak(self):
        update = {
            "update_id": 15,
            "message": {"chat": {"id": 777}, "text": "/start"},
        }
        response = self._request(update, client=_FailingTelegramClient())

        self.assertEqual(response.status_code, 502)
        self.assertNotIn(TEST_TOKEN, response.text)
        self.assertNotIn(TEST_WEBHOOK_SECRET, response.text)


class SetWebhookTests(unittest.TestCase):
    def test_valid_configuration_is_accepted(self):
        set_webhook.validate_webhook_config(
            bot_token=TEST_TOKEN,
            webhook_url="https://amen.example.com/api/v1/telegram/webhook",
            webhook_secret=TEST_WEBHOOK_SECRET,
        )

    def test_invalid_configuration_is_rejected(self):
        valid_url = "https://amen.example.com/api/v1/telegram/webhook"
        cases = [
            {"bot_token": None, "webhook_url": valid_url, "webhook_secret": TEST_WEBHOOK_SECRET},
            {"bot_token": TEST_TOKEN, "webhook_url": None, "webhook_secret": TEST_WEBHOOK_SECRET},
            {"bot_token": TEST_TOKEN, "webhook_url": valid_url, "webhook_secret": None},
            {"bot_token": TEST_TOKEN, "webhook_url": "http://amen.example.com/api/v1/telegram/webhook", "webhook_secret": TEST_WEBHOOK_SECRET},
            {"bot_token": TEST_TOKEN, "webhook_url": "https://amen.example.com/incorrect", "webhook_secret": TEST_WEBHOOK_SECRET},
            {"bot_token": TEST_TOKEN, "webhook_url": valid_url + "?query=1", "webhook_secret": TEST_WEBHOOK_SECRET},
            {"bot_token": TEST_TOKEN, "webhook_url": valid_url, "webhook_secret": "short"},
            {"bot_token": TEST_TOKEN, "webhook_url": valid_url, "webhook_secret": TEST_TOKEN},
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(SystemExit):
                    set_webhook.validate_webhook_config(**case)

    def test_set_webhook_calls_telegram_without_printing_credentials(self):
        class Client:
            def __init__(self):
                self.posts = []

            def post(self, url, json=None):
                self.posts.append((url, json))
                return _FakeResponse({"ok": True, "result": True})

        class ClientManager:
            def __init__(self, client):
                self.client = client

            def __enter__(self):
                return self.client

            def __exit__(self, exc_type, exc_value, traceback):
                return None

        client = Client()
        settings = _settings()
        settings.telegram_webhook_url = "https://amen.example.com/api/v1/telegram/webhook"
        output = io.StringIO()
        with mock.patch.object(
            sys, "argv", ["set_webhook"]
        ), mock.patch.object(
            set_webhook, "get_settings", return_value=settings
        ), mock.patch.object(
            set_webhook.httpx, "Client", return_value=ClientManager(client)
        ), contextlib.redirect_stdout(output):
            set_webhook.main()

        self.assertEqual(len(client.posts), 1)
        url, payload = client.posts[0]
        self.assertTrue(url.endswith("/setWebhook"))
        self.assertEqual(payload["url"], settings.telegram_webhook_url)
        self.assertEqual(payload["secret_token"], TEST_WEBHOOK_SECRET)
        self.assertEqual(payload["allowed_updates"], ["message", "edited_message"])
        self.assertNotIn(TEST_TOKEN, output.getvalue())
        self.assertNotIn(TEST_WEBHOOK_SECRET, output.getvalue())

    def test_status_uses_get_webhook_info_and_prints_safe_fields(self):
        class Client:
            def __init__(self):
                self.posts = []

            def post(self, url, json=None):
                self.posts.append((url, json))
                return _FakeResponse(
                    {
                        "ok": True,
                        "result": {
                            "url": "https://amen.example.com/api/v1/telegram/webhook",
                            "has_custom_certificate": False,
                            "pending_update_count": 0,
                            "last_error_message": None,
                            "secret_token": TEST_WEBHOOK_SECRET,
                        },
                    }
                )

        class ClientManager:
            def __init__(self, client):
                self.client = client

            def __enter__(self):
                return self.client

            def __exit__(self, exc_type, exc_value, traceback):
                return None

        client = Client()
        settings = _settings()
        settings.telegram_webhook_url = "https://amen.example.com/api/v1/telegram/webhook"
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["set_webhook", "--status"]), mock.patch.object(
            set_webhook, "get_settings", return_value=settings
        ), mock.patch.object(
            set_webhook.httpx, "Client", return_value=ClientManager(client)
        ), contextlib.redirect_stdout(output):
            set_webhook.main()

        self.assertEqual(len(client.posts), 1)
        self.assertTrue(client.posts[0][0].endswith("/getWebhookInfo"))
        self.assertNotIn(TEST_WEBHOOK_SECRET, output.getvalue())


if __name__ == "__main__":
    unittest.main()
