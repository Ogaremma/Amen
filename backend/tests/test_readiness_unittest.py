import unittest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest import mock

from app.main import app
from app.services.forebet_draw_worker import forebet_draw_worker


class ReadinessTests(unittest.TestCase):
    def test_readiness_before_first_cycle(self):
        with mock.patch.object(forebet_draw_worker, "_task", None), mock.patch.object(
            forebet_draw_worker, "last_started", None
        ), mock.patch.object(forebet_draw_worker, "last_completed", None), mock.patch.object(
            forebet_draw_worker, "last_prune_completed", None
        ):
            response = TestClient(app).get("/readiness")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn(body["status"], {"ready", "not_ready"})
        self.assertIn("reachable", body["database"])
        self.assertIsNone(body["worker"]["last_refresh_completed"])

    def test_readiness_exposes_worker_cycle_values(self):
        now = datetime.now(timezone.utc)
        fake_task = mock.Mock(done=mock.Mock(return_value=False))
        with mock.patch.object(forebet_draw_worker, "_task", fake_task), mock.patch.object(
            forebet_draw_worker, "last_completed", now
        ), mock.patch.object(forebet_draw_worker, "last_prune_completed", now), mock.patch.object(
            forebet_draw_worker, "last_failure", None
        ):
            body = TestClient(app).get("/readiness").json()
        self.assertIn(body["status"], {"ready", "not_ready"})
        self.assertIsNotNone(body["worker"]["last_refresh_completed"])
        self.assertIsNotNone(body["worker"]["last_prune_completed"])


if __name__ == "__main__":
    unittest.main()
