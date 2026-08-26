from datetime import date, datetime, timezone
from unittest import TestCase

from pydantic import ValidationError

from app.schemas.forebet import SportyBetEvent
from app.schemas.forebet_ingestion import SportyBetFixtureSnapshotRequest


def event(event_id="e1", day=24):
    return SportyBetEvent(event_id=event_id, home_team="Home", away_team="Away", kickoff=datetime(2026, 8, day, 12, tzinfo=timezone.utc), sport_id="sr:sport:1", market_id="1", product_id=3, outcome_draw_id="2")


class SportyBetSnapshotTests(TestCase):
    def test_valid_snapshot_preserves_selection_identity_fields(self):
        request = SportyBetFixtureSnapshotRequest(events=[event()])
        self.assertEqual(request.events[0].event_id, "e1")
        self.assertEqual((request.events[0].market_id, request.events[0].outcome_draw_id, request.events[0].product_id, request.events[0].sport_id), ("1", "2", 3, "sr:sport:1"))

    def test_wrong_source_rejected(self):
        with self.assertRaises(ValidationError):
            SportyBetFixtureSnapshotRequest(source="forebet", events=[event()])

    def test_empty_snapshot_rejected(self):
        with self.assertRaises(ValidationError):
            SportyBetFixtureSnapshotRequest(events=[])
