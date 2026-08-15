import os
import tempfile
import unittest
from datetime import datetime, timezone

from app.schemas.booking import BookingResponse, BookingSelection
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

    def test_history_survives_store_restart(self):
        self.store.upsert(7, "PERSIST", 3, 2.5)
        restarted_store = HistoryStore(self.path)
        rows = restarted_store.list(7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["booking_code"], "PERSIST")

    def test_limit_removes_oldest_entries(self):
        for index in range(HISTORY_LIMIT + 3):
            self.store.upsert(1, f"CODE{index:02}", index, 1.0)
        rows = self.store.list(1)
        self.assertEqual(len(rows), HISTORY_LIMIT)
        self.assertNotIn("CODE00", {row["booking_code"] for row in rows})

    def _booking(self, status, odds):
        selection = BookingSelection(
            id="event-1", event_id="event-1", market_id="18", outcome_id="12",
            home="Home", away="Away", competition="League", category="Country",
            kickoff=datetime(2026, 8, 15, tzinfo=timezone.utc),
            kickoff_date="2026-08-15", kickoff_time="00:00",
            local_kickoff_date="2026-08-15", local_kickoff_time="01:00",
            market="Over/Under", outcome="Over 2.5", odds=odds,
            specifier="total=2.5", game_status=status,
            result_status="pending" if status != "ended" else "won",
        )
        return BookingResponse(
            booking_code="SNAP1", total_selections=1, total_odds=9.99,
            remaining_odds=odds if status == "upcoming" and odds else 0.0,
            selections=[selection],
        )

    def test_ended_selection_uses_first_active_observation(self):
        active = self.store.apply_observed_odds(self._booking("upcoming", 1.45))
        self.assertEqual(active.selections[0].odds_source, "sportybet_current")
        self.store.upsert(1, "SNAP1", 1, 1.45)
        self.store.upsert(1, "SNAP1", 1, 0.0)
        restarted_store = HistoryStore(self.path)
        ended = restarted_store.apply_observed_odds(self._booking("ended", 4.72))
        self.assertEqual(ended.selections[0].odds, 1.45)
        self.assertEqual(ended.selections[0].odds_source, "preserved_observation")
        self.assertEqual(ended.total_odds, 9.99)

    def test_first_seen_ended_selection_has_unavailable_odds(self):
        ended = self.store.apply_observed_odds(self._booking("ended", 4.72))
        self.assertIsNone(ended.selections[0].odds)
        self.assertEqual(ended.selections[0].odds_source, "unavailable")


if __name__ == "__main__":
    unittest.main()
