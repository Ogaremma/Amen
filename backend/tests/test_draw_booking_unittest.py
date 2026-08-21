from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase, mock

from fastapi import HTTPException

from app.schemas.forebet import FixtureMatchResult, FixtureMatchStatus, ForebetMatch, ForebetPredictionResult, SportyBetEvent
from app.services import sportybet


def fixture(status=FixtureMatchStatus.MATCHED_EXACT, **event_changes):
    values = dict(event_id="sr:match:1", home_team="Arsenal", away_team="Chelsea", home_team_name="Arsenal", away_team_name="Chelsea", kickoff=datetime(2026, 8, 21, 15, tzinfo=timezone.utc), competition="Premier League", sport_id="sr:sport:1", market_id="1", outcome_draw_id="2", product_id=3, match_status="Not start")
    values.update(event_changes)
    event = SportyBetEvent(**values)
    forebet = ForebetMatch(home_team=event.home_team, away_team=event.away_team, kickoff=event.kickoff, predicted_result=ForebetPredictionResult.DRAW)
    return FixtureMatchResult(forebet_match=forebet, status=status, sportybet_event=event if status not in {FixtureMatchStatus.UNMATCHED, FixtureMatchStatus.AMBIGUOUS} else None)


class DrawBookingTests(IsolatedAsyncioTestCase):
    async def test_one_and_multiple_use_exact_identity(self):
        second = fixture(event_id="sr:match:2", home_team="Lyon", away_team="Nice", specifier="verified")
        with mock.patch.object(sportybet, "_create_share_code", return_value="ABC123") as create:
            result = await sportybet.create_draw_booking([fixture(), second])
        self.assertEqual(result.booking_code, "ABC123")
        self.assertEqual(result.selection_count, 2)
        self.assertEqual(create.call_args.args[0][0], {"eventId": "sr:match:1", "marketId": "1", "outcomeId": "2", "productId": 3, "sportId": "sr:sport:1"})
        self.assertEqual(create.call_args.args[0][1]["specifier"], "verified")

    async def test_rejects_unmatched_and_ambiguous(self):
        for status in (FixtureMatchStatus.UNMATCHED, FixtureMatchStatus.AMBIGUOUS):
            with self.assertRaises(HTTPException):
                await sportybet.create_draw_booking([fixture(status=status)])

    async def test_rejects_invalid_market_outcome_identity_and_status(self):
        for changes in ({"market_id": None}, {"outcome_draw_id": None}, {"match_status": "Live"}, {"product_id": None}, {"sport_id": None}):
            with self.assertRaises(HTTPException):
                await sportybet.create_draw_booking([fixture(**changes)])

    async def test_existing_share_response_validation_is_reused(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"bizCode": 10000, "data": {"shareCode": "CODE42"}}
        client = mock.AsyncMock(); client.__aenter__.return_value.post.return_value = response
        with mock.patch("app.services.sportybet.httpx.AsyncClient", return_value=client):
            self.assertEqual(await sportybet._create_share_code([{"eventId": "1"}]), "CODE42")
        response.json.return_value = {"bizCode": 10000, "data": {}}
        with mock.patch("app.services.sportybet.httpx.AsyncClient", return_value=client), self.assertRaises(HTTPException):
            await sportybet._create_share_code([{"eventId": "1"}])
