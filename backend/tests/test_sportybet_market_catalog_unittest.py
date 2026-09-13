import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app.services import sportybet
from fastapi import HTTPException


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, *, json_error: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self) -> dict:
        if self._json_error:
            raise ValueError("invalid JSON")
        return self._payload


def over_market(odds: str = "1.45") -> dict:
    return {
        "id": "18",
        "specifier": "total=1.5",
        "product": 3,
        "desc": "Over/Under",
        "name": "Over/Under",
        "status": 0,
        "sourceType": "BET_RADAR",
        "lastOddsChangeTime": 1787510754000,
        "banned": False,
        "outcomes": [
            {
                "id": "12",
                "odds": odds,
                "probability": "0.6918030000",
                "isActive": 1,
                "desc": "Over 1.5",
            },
            {
                "id": "13",
                "odds": "2.70",
                "probability": "0.3081970000",
                "isActive": 1,
                "desc": "Under 1.5",
            },
        ],
    }


def one_x_two_market() -> dict:
    return {
        "id": "1",
        "product": 3,
        "outcomes": [
            {"id": "1", "odds": "2.10", "isActive": 1, "desc": "Home"},
            {"id": "2", "odds": "3.20", "isActive": 1, "desc": "Draw"},
            {"id": "3", "odds": "3.40", "isActive": 1, "desc": "Away"},
        ],
    }


def raw_event(event_id: str, odds: str = "1.45") -> dict:
    return {
        "eventId": event_id,
        "gameId": "19502",
        "estimateStartTime": 1787683500000,
        "status": 0,
        "matchStatus": "Not start",
        "homeTeamId": "sr:competitor:133",
        "homeTeamName": "Stevenage FC",
        "awayTeamId": "sr:competitor:28",
        "awayTeamName": "Reading FC",
        "sport": {
            "id": "sr:sport:1",
            "name": "Football",
            "category": {
                "id": "sr:category:1",
                "name": "England",
                "tournament": {
                    "id": "sr:tournament:21",
                    "name": "EFL Cup",
                },
            },
        },
        "banned": False,
        "markets": [one_x_two_market(), over_market(odds)],
    }


def page_payload(events: list[dict], total_num: int) -> dict:
    return {
        "bizCode": 10000,
        "message": "0#0",
        "data": {
            "totalNum": total_num,
            "tournaments": [
                {
                    "id": "sr:tournament:21",
                    "name": "EFL Cup",
                    "events": events,
                }
            ],
        },
    }


def wap_page_payload(events: list[dict], more_events: bool) -> dict:
    return {
        "bizCode": 10000,
        "message": "0#0",
        "data": {
            "moreEvents": more_events,
            "tournaments": [
                {
                    "id": "sr:tournament:21",
                    "name": "EFL Cup",
                    "events": events,
                }
            ],
        },
    }


class UpcomingMarketCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_produces_market_preserving_fixture(self):
        payload = page_payload([raw_event("sr:match:1")], 1)
        response = FakeResponse(200, payload)

        with mock.patch.object(
            sportybet, "_request_upcoming_page", new=mock.AsyncMock(return_value=response)
        ) as request:
            catalogue = await sportybet.get_upcoming_football_market_fixtures(
                page_size=100, max_pages=1
            )

        request.assert_awaited_once_with(1, 100)
        self.assertEqual(catalogue.total_num, 1)
        self.assertEqual(catalogue.retrieved_num, 1)
        self.assertTrue(catalogue.complete)
        self.assertEqual(len(catalogue.fixtures), 1)
        fixture = catalogue.fixtures[0]
        self.assertEqual(fixture.event_id, "sr:match:1")
        self.assertEqual(fixture.sport_id, "sr:sport:1")
        self.assertEqual(fixture.competition, "EFL Cup")
        self.assertEqual(len(fixture.markets), 2)
        market = fixture.markets[1]
        self.assertEqual(market.market_id, "18")
        self.assertEqual(market.specifier, "total=1.5")
        self.assertEqual(market.product_id, 3)
        self.assertEqual(market.outcomes[0].outcome_id, "12")
        self.assertEqual(market.outcomes[0].description, "Over 1.5")

    async def test_multiple_pages_use_existing_pagination_behavior(self):
        events = [
            raw_event("sr:match:1", "1.40"),
            raw_event("sr:match:2", "1.49"),
            raw_event("sr:match:3", "1.50"),
        ]
        responses = [
            FakeResponse(200, page_payload([event], 3)) for event in events
        ]

        with mock.patch.object(
            sportybet,
            "_request_upcoming_page",
            new=mock.AsyncMock(side_effect=responses + responses),
        ) as request:
            catalogue = await sportybet.get_upcoming_football_market_fixtures(
                page_size=1, max_pages=10
            )
            candidates = await sportybet.get_upcoming_over_one_half_candidates(
                page_size=1, max_pages=10
            )

        self.assertEqual(request.await_count, 6)
        self.assertEqual(catalogue.pages_fetched, 3)
        self.assertEqual(catalogue.retrieved_num, 3)
        self.assertTrue(catalogue.complete)
        self.assertEqual(len(catalogue.fixtures), 3)
        self.assertEqual([candidate.outcome.odds for candidate in candidates], [1.40, 1.49])

    async def test_existing_one_x_two_path_remains_unchanged(self):
        payload = page_payload([raw_event("sr:match:1")], 1)
        response = FakeResponse(200, payload)

        with mock.patch.object(
            sportybet, "_request_upcoming_page", new=mock.AsyncMock(return_value=response)
        ):
            events = await sportybet.get_upcoming_football_events(
                page_size=100, max_pages=1
            )
            fixtures = await sportybet.get_upcoming_football_market_fixtures(
                page_size=100, max_pages=1
            )

        self.assertEqual(events.total_num, 1)
        self.assertEqual(events.events[0].market_id, "1")
        self.assertEqual(events.events[0].outcome_draw_id, "2")
        self.assertEqual(events.events[0].odds_draw, 3.20)
        self.assertEqual(fixtures.fixtures[0].markets[1].market_id, "18")

    async def test_transport_retries_before_market_parse(self):
        payload = page_payload([raw_event("sr:match:1")], 1)
        responses = [FakeResponse(500, {}), FakeResponse(200, payload)]

        async def no_sleep(_: float) -> None:
            return None

        with mock.patch.object(
            sportybet,
            "_request_upcoming_page",
            new=mock.AsyncMock(side_effect=responses),
        ) as request, mock.patch.object(
            sportybet.asyncio, "sleep", new=no_sleep
        ):
            catalogue = await sportybet.get_upcoming_football_market_fixtures(
                page_size=100, max_pages=1
            )

        self.assertEqual(request.await_count, 2)
        self.assertEqual(len(catalogue.fixtures), 1)

    async def test_kickoff_window_filters_market_fixtures(self):
        early = raw_event("sr:match:1")
        early["estimateStartTime"] = 1787683500000
        late = raw_event("sr:match:2")
        late["estimateStartTime"] = 1787770000000
        response = FakeResponse(200, page_payload([early, late], 2))

        with mock.patch.object(
            sportybet, "_request_upcoming_page", new=mock.AsyncMock(return_value=response)
        ):
            catalogue = await sportybet.get_upcoming_football_market_fixtures(
                start_datetime=datetime.fromtimestamp(1787683500, timezone.utc),
                end_datetime=datetime.fromtimestamp(1787683500, timezone.utc),
                page_size=100,
                max_pages=1,
            )

        self.assertEqual([fixture.event_id for fixture in catalogue.fixtures], ["sr:match:1"])

    async def test_http_202_falls_back_and_preserves_parser_contract(self):
        primary_responses = [FakeResponse(202, {}) for _ in range(3)]
        fallback_responses = [
            FakeResponse(200, wap_page_payload([raw_event("sr:match:1", "1.40")], True)),
            FakeResponse(200, wap_page_payload([raw_event("sr:match:2", "1.49")], False)),
        ]
        calls = []

        async def request(page_num: int, page_size: int, path: str | None = None):
            calls.append((page_num, page_size, path))
            if path is None:
                return primary_responses.pop(0)
            return fallback_responses.pop(0)

        async def no_sleep(_: float) -> None:
            return None

        with mock.patch.object(sportybet, "_request_upcoming_page", new=request), mock.patch.object(
            sportybet.asyncio, "sleep", new=no_sleep
        ):
            catalogue = await sportybet.get_upcoming_football_market_fixtures(
                page_size=1, max_pages=10
            )
            candidates = sportybet.extract_over_one_half_candidates(
                catalogue.fixtures
            )

        fallback_path = sportybet.settings.sportybet_upcoming_fallback_path
        self.assertEqual(calls[:3], [(1, 1, None), (1, 1, None), (1, 1, None)])
        self.assertEqual(
            calls[3:], [(1, 1, fallback_path), (2, 1, fallback_path)]
        )
        self.assertEqual(catalogue.total_num, 2)
        self.assertEqual(catalogue.retrieved_num, 2)
        self.assertEqual(catalogue.pages_fetched, 2)
        self.assertTrue(catalogue.complete)
        self.assertEqual([fixture.event_id for fixture in catalogue.fixtures], ["sr:match:1", "sr:match:2"])
        self.assertEqual([candidate.outcome.odds for candidate in candidates], [1.40, 1.49])

    async def test_all_endpoints_failing_raises_without_fixtures(self):
        responses = [FakeResponse(202, {}) for _ in range(6)]

        async def no_sleep(_: float) -> None:
            return None

        with mock.patch.object(
            sportybet,
            "_request_upcoming_page",
            new=mock.AsyncMock(side_effect=responses),
        ) as request, mock.patch.object(sportybet.asyncio, "sleep", new=no_sleep):
            with self.assertRaises(HTTPException):
                await sportybet.get_upcoming_football_market_fixtures(
                    page_size=100, max_pages=1
                )

        self.assertEqual(request.await_count, 6)

    async def test_malformed_json_fails_honestly_without_fallback(self):
        responses = [FakeResponse(200, {}, json_error=True) for _ in range(3)]

        async def no_sleep(_: float) -> None:
            return None

        with mock.patch.object(
            sportybet,
            "_request_upcoming_page",
            new=mock.AsyncMock(side_effect=responses),
        ) as request, mock.patch.object(
            sportybet.settings, "sportybet_upcoming_fallback_path", ""
        ), mock.patch.object(sportybet.asyncio, "sleep", new=no_sleep):
            with self.assertRaises(HTTPException):
                await sportybet.get_upcoming_football_market_fixtures(
                    page_size=100, max_pages=1
                )

        self.assertEqual(request.await_count, 3)

    async def test_malformed_events_never_become_fixtures(self):
        payload = page_payload([None, {"eventId": "bad"}], 2)
        response = FakeResponse(200, payload)

        with mock.patch.object(
            sportybet, "_request_upcoming_page", new=mock.AsyncMock(return_value=response)
        ):
            catalogue = await sportybet.get_upcoming_football_market_fixtures(
                page_size=100, max_pages=1
            )
            candidates = await sportybet.get_upcoming_over_one_half_candidates(
                page_size=100, max_pages=1
            )

        self.assertEqual(catalogue.total_num, 2)
        self.assertEqual(catalogue.retrieved_num, 0)
        self.assertEqual(catalogue.fixtures, [])
        self.assertEqual(candidates, [])

    async def test_wap_fallback_uses_official_post_contract(self):
        client = mock.AsyncMock()
        client.post.return_value = FakeResponse(
            200, wap_page_payload([raw_event("sr:match:1")], False)
        )
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False

        with mock.patch.object(sportybet.httpx, "AsyncClient", return_value=client):
            response = await sportybet._request_upcoming_page(
                1,
                20,
                sportybet.settings.sportybet_upcoming_fallback_path,
            )

        self.assertEqual(response.status_code, 200)
        client.get.assert_not_called()
        client.post.assert_awaited_once_with(
            "https://www.sportybet.com/api/ng/factsCenter/wapConfigurableEventsByOrder",
            headers=sportybet._wap_headers(),
            json={
                "productId": 3,
                "sportId": "sr:sport:1",
                "order": 0,
                "pageNum": 1,
                "pageSize": 20,
                "withTwoUpMarket": True,
                "withOneUpMarket": True,
            },
        )

    async def test_real_snapshot_flows_through_transport_parser_and_extractor(self):
        snapshot_path = Path(__file__).parents[2] / "snapshots" / "sportybet-page-1.json"
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        response = FakeResponse(200, payload)

        with mock.patch.object(
            sportybet, "_request_upcoming_page", new=mock.AsyncMock(return_value=response)
        ):
            candidates = await sportybet.get_upcoming_over_one_half_candidates(
                page_size=100, max_pages=1
            )

        self.assertEqual(len(candidates), 8)
        for candidate in candidates:
            self.assertEqual(candidate.identity.market_id, "18")
            self.assertEqual(candidate.identity.specifier, "total=1.5")
            self.assertEqual(candidate.identity.outcome_id, "12")
            self.assertEqual(candidate.identity.product_id, 3)
            self.assertEqual(candidate.identity.sport_id, "sr:sport:1")


if __name__ == "__main__":
    unittest.main()
