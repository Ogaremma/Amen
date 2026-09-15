from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app.main import app


class PredictionDailyAdminApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.url = "/api/v1/admin/prediction-daily/run"

    def test_prediction_daily_admin_requires_authentication(self) -> None:
        with mock.patch(
            "app.api.admin.get_settings",
            return_value=SimpleNamespace(prediction_daily_token="secret-token"),
        ):
            response = self.client.post(self.url, json={})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Admin authentication required"})

    def test_prediction_daily_admin_rejects_wrong_token(self) -> None:
        with mock.patch(
            "app.api.admin.get_settings",
            return_value=SimpleNamespace(prediction_daily_token="secret-token"),
        ):
            response = self.client.post(
                self.url,
                json={},
                headers={"Authorization": "Bearer wrong-token"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Admin authentication required"})

    def test_prediction_daily_admin_does_not_accept_forebet_token(self) -> None:
        with mock.patch(
            "app.api.admin.get_settings",
            return_value=SimpleNamespace(
                prediction_daily_token="daily-token",
                forebet_ingestion_token="forebet-token",
            ),
        ):
            response = self.client.post(
                self.url,
                json={},
                headers={"Authorization": "Bearer forebet-token"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Admin authentication required"})

    def test_prediction_daily_admin_returns_summary(self) -> None:
        async def execute(args: SimpleNamespace) -> tuple[dict, int]:
            self.assertEqual(args.chat_id, None)
            self.assertEqual(args.page_size, None)
            self.assertEqual(args.max_pages, None)
            return {"outcome": "delivered"}, 0

        with mock.patch(
            "app.api.admin.get_settings",
            return_value=SimpleNamespace(prediction_daily_token="secret-token"),
        ), mock.patch(
            "app.api.admin.execute_daily_prediction_runner",
            side_effect=execute,
        ):
            response = self.client.post(
                self.url,
                json={},
                headers={"Authorization": "Bearer secret-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"exit_code": 0, "outcome": "delivered"})

    def test_prediction_daily_admin_maps_failure_to_http_502(self) -> None:
        async def execute(_: SimpleNamespace) -> tuple[dict, int]:
            return {"outcome": "catalogue_unavailable"}, 1

        with mock.patch(
            "app.api.admin.get_settings",
            return_value=SimpleNamespace(prediction_daily_token="secret-token"),
        ), mock.patch(
            "app.api.admin.execute_daily_prediction_runner",
            side_effect=execute,
        ):
            response = self.client.post(
                self.url,
                json={},
                headers={"Authorization": "Bearer secret-token"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"exit_code": 1, "outcome": "catalogue_unavailable"},
        )

    def test_prediction_daily_admin_maps_missing_configuration_to_http_503(self) -> None:
        async def execute(_: SimpleNamespace) -> tuple[dict, int]:
            raise SystemExit("TELEGRAM_BOT_TOKEN is required.")

        with mock.patch(
            "app.api.admin.get_settings",
            return_value=SimpleNamespace(prediction_daily_token="secret-token"),
        ), mock.patch(
            "app.api.admin.execute_daily_prediction_runner",
            side_effect=execute,
        ):
            response = self.client.post(
                self.url,
                json={},
                headers={"Authorization": "Bearer secret-token"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "TELEGRAM_BOT_TOKEN is required."},
        )


if __name__ == "__main__":
    unittest.main()
