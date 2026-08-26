import tempfile
import unittest
from datetime import date, datetime, timezone

from app.services.forebet_draw_engine import ForebetDrawEngine
from app.services.forebet_draw_store import ForebetDrawStore


class BatchingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False); self.tmp.close()
        self.engine = ForebetDrawEngine(ForebetDrawStore(self.tmp.name))

    def result(self, i):
        match = type("Match", (), {"event_id": f"e{i}", "home_team": f"h{i}", "away_team": f"a{i}", "kickoff": datetime(2030, 1, 1, tzinfo=timezone.utc), "match_status": "not started", "market_id": "1", "outcome_draw_id": "2", "product_id": 3, "sport_id": "sr:sport:1", "specifier": None})()
        return type("Result", (), {"sportybet_event": match})()

    async def batches(self, count):
        return await self.engine._paper_batches(date(2030, 1, 1), [self.result(i) for i in range(count)], object())

    async def test_boundaries_and_empty(self):
        self.assertEqual([len(b["matches"]) for b in await self.batches(50)], [50])
        self.assertEqual([len(b["matches"]) for b in await self.batches(51)], [50, 1])
        self.assertEqual([len(b["matches"]) for b in await self.batches(100)], [50, 50])
        self.assertEqual(await self.batches(0), [])

    async def test_identity_reuse_and_localized_change(self):
        first = await self.batches(100); second = await self.batches(100)
        self.assertEqual([b["booking_code"] for b in first], [b["booking_code"] for b in second])
        changed = [self.result(i) for i in range(100)]; changed[75] = self.result(1000)
        third = await self.engine._paper_batches(date(2030, 1, 1), changed, object())
        self.assertEqual(first[0]["booking_code"], third[0]["booking_code"])
        self.assertNotEqual(first[1]["booking_code"], third[1]["booking_code"])


if __name__ == "__main__": unittest.main()
