from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.services.forebet_draw_store import ForebetDrawStore


class JobLockTests(TestCase):
    def test_second_owner_is_excluded_until_first_releases(self):
        with TemporaryDirectory() as tmp:
            store = ForebetDrawStore(str(Path(tmp) / "state.sqlite3"))
            self.assertTrue(store.acquire_job_lock("rolling-draw-refresh", "owner-a", 60))
            self.assertFalse(store.acquire_job_lock("rolling-draw-refresh", "owner-b", 60))
            store.release_job_lock("rolling-draw-refresh", "owner-a")
            self.assertTrue(store.acquire_job_lock("rolling-draw-refresh", "owner-b", 60))
