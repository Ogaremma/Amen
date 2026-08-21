from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase, mock

from app.services import sportybet
from fastapi import HTTPException


def payload(events, total=1):
    return {"bizCode": 10000, "message": "0#0", "data": {"totalNum": total, "tournaments": [{"id": "sr:tournament:17", "name": "Premier League", "events": events}]}}


def event(start=1787338800000):
    return {"eventId": "sr:match:1", "gameId": "39515", "estimateStartTime": start, "status": 0, "matchStatus": "Not start", "homeTeamId": "h", "homeTeamName": "Arsenal", "awayTeamId": "a", "awayTeamName": "Coventry City", "sport": {"id": "sr:sport:1", "name": "Football", "category": {"id": "sr:category:1", "name": "England", "tournament": {"id": "sr:tournament:17", "name": "Premier League"}}}, "markets": [{"id": "1", "product": 3, "outcomes": [{"id": "1", "odds": "1.2", "probability": "0.8", "isActive": 1}, {"id": "2", "odds": "7.8", "probability": "0.1", "isActive": 1}, {"id": "3", "odds": "17", "probability": "0.05", "isActive": 1}]}]}


class UpcomingTests(IsolatedAsyncioTestCase):
    def test_parse_verified_shape(self):
        result = sportybet.parse_upcoming_events_page(payload([event()]))
        item = result.events[0]
        self.assertEqual(result.total_num, 1)
        self.assertEqual(item.event_id, "sr:match:1")
        self.assertEqual(item.competition, "Premier League")
        self.assertEqual(item.market_id, "1")
        self.assertEqual(item.outcome_draw_id, "2")
        self.assertEqual(item.odds_draw, 7.8)
        self.assertEqual(item.probability_draw, 0.1)
        self.assertEqual(item.kickoff.tzinfo, timezone.utc)

    def test_missing_market_and_malformed_event_are_safe(self):
        raw = event(); raw["markets"] = [{"id": "18", "outcomes": []}]
        result = sportybet.parse_upcoming_events_page(payload([raw, None, {"eventId": "bad"}], total=3))
        self.assertEqual(len(result.events), 1)
        self.assertIsNone(result.events[0].market_id)
        self.assertIsNone(result.events[0].outcome_draw_id)

    def test_missing_or_inactive_outcome(self):
        raw = event(); raw["markets"][0]["outcomes"][1]["isActive"] = 0
        item = sportybet.parse_upcoming_events_page(payload([raw])).events[0]
        self.assertIsNone(item.outcome_draw_id)
        self.assertIsNone(item.odds_draw)

    def test_empty_tournament_and_api_error(self):
        self.assertEqual(sportybet.parse_upcoming_events_page(payload([] , total=0)).events, [])
        with self.assertRaises(HTTPException):
            sportybet.parse_upcoming_events_page({"bizCode": 19999})

    async def test_pagination_and_window(self):
        first = payload([event()], total=2)
        second_event = event(1787425200000); second_event["eventId"] = "sr:match:2"
        second = payload([second_event], total=2)
        with mock.patch.object(sportybet, "_fetch_upcoming_page", side_effect=[sportybet.parse_upcoming_events_page(first), sportybet.parse_upcoming_events_page(second)]):
            result = await sportybet.get_upcoming_football_events(page_size=1, max_pages=3, start_datetime=datetime.fromtimestamp(1787338800, timezone.utc), end_datetime=datetime.fromtimestamp(1787338800, timezone.utc) + timedelta(hours=1))
        self.assertEqual(result.total_num, 2)
        self.assertEqual([x.event_id for x in result.events], ["sr:match:1"])
