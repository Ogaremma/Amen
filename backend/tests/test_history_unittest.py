import os
import tempfile
import unittest

from app.services.history_store import HISTORY_LIMIT, HistoryStore


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.store = HistoryStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_users_are_isolated_and_newest_first(self):
        self.store.upsert(1, "FIRST", 1, 1.2)
        self.store.upsert(1, "SECOND", 2, 2.3)
        self.store.upsert(2, "OTHER", 3, 3.4)
        self.assertEqual([row["booking_code"] for row in self.store.list(1)], ["SECOND", "FIRST"])
        self.assertEqual([row["booking_code"] for row in self.store.list(2)], ["OTHER"])

    def test_duplicate_code_is_updated_not_duplicated(self):
        self.store.upsert(1, "same", 1, 1.2)
        self.store.upsert(1, "SAME", 4, 4.5)
        rows = self.store.list(1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["selection_count"], 4)

    def test_limit_removes_oldest_entries(self):
        for index in range(HISTORY_LIMIT + 3):
            self.store.upsert(1, f"CODE{index:02}", index, 1.0)
        rows = self.store.list(1)
        self.assertEqual(len(rows), HISTORY_LIMIT)
        self.assertNotIn("CODE00", {row["booking_code"] for row in rows})


if __name__ == "__main__":
    unittest.main()
