from __future__ import annotations

import os
import tempfile
import unittest

from app.services.history_store import HistoryStore
from app.services.telegram_user_store import TelegramUserStore


class TelegramUserStoreTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.store = TelegramUserStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_auth_user_persists_and_survives_restart(self):
        self.store.upsert_from_auth(424242)
        restarted = TelegramUserStore(self.path)
        user = restarted.get(424242)
        self.assertIsNotNone(user)
        self.assertEqual(user.telegram_user_id, 424242)
        self.assertIsNone(user.telegram_chat_id)
        self.assertEqual(user.status, "active")

    def test_start_user_stores_chat_id_and_reactivates(self):
        self.store.upsert_from_start(777, 777)
        self.store.mark_undeliverable(777)
        self.store.upsert_from_start(777, 777)
        user = self.store.get(777)
        self.assertEqual(user.telegram_chat_id, 777)
        self.assertEqual(user.status, "active")

    def test_eligible_users_exclude_undeliverable_users(self):
        self.store.upsert_from_auth(1)
        self.store.upsert_from_auth(2)
        self.store.mark_undeliverable(2)
        eligible = self.store.list_eligible_users()
        self.assertEqual([user.telegram_user_id for user in eligible], [1])

    def test_booking_history_backfills_verified_bot_users(self):
        history = HistoryStore(self.path)
        history.upsert(10, "CODE10", 1, 1.5)
        history.upsert(11, "CODE11", 2, 2.5)
        store = TelegramUserStore(self.path)
        eligible = store.list_eligible_users()
        self.assertEqual(
            [user.telegram_user_id for user in eligible],
            [10, 11],
        )

    def test_auth_preserves_existing_start_chat_id(self):
        self.store.upsert_from_start(999, 999)
        self.store.upsert_from_auth(999)
        user = self.store.get(999)
        self.assertEqual(user.telegram_chat_id, 999)


if __name__ == "__main__":
    unittest.main()
