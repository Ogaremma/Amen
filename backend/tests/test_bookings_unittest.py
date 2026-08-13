"""Unit tests for the SportyBet booking retrieval + parsing phase.

All tests use mocked payloads / mocked HTTP so they never depend on
SportyBet being online.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services import sportybet
from app.services.sportybet import determine_game_status, get_booking, parse_booking


def _ms(dt: datetime) -> int:
    """Milliseconds since epoch for a timezone-aware datetime."""
    return int(dt.timestamp() * 1000)


# Two events. Note the deliberate trap for the sorter:
#   Event B is on the EARLIER date (Aug 10) but a LATER clock time (22:00).
#   Event A is on the LATER date (Aug 11) but an EARLIER clock time (08:00).
# A clock-time-only sort would wrongly place A before B.
EVENT_A_START = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)
EVENT_B_START = datetime(2026, 8, 10, 22, 0, tzinfo=timezone.utc)


def make_payload() -> dict:
    return {
        "bizCode": 10000,
        "isAvailable": True,
        "message": "Success",
        "data": {
            "shareCode": "HW7UDH",
            "ticket": {
                "displayTotalOdds": "12.34",
                "totalStake": 1000000,
                "selections": [
                    # Same marketId (166) exists twice with different specifiers.
                    # This must resolve to the total=9.5 market, not total=8.5.
                    {
                        "eventId": "sr:match:1001",
                        "marketId": "166",
                        "specifier": "total=9.5",
                        "outcomeId": "12",
                        "productId": 3,
                        "sportId": "sr:sport:1",
                    },
                    # No specifier; market id 1 has multiple outcomes -> pick id 3.
                    {
                        "eventId": "sr:match:1001",
                        "marketId": "1",
                        "specifier": "",
                        "outcomeId": "3",
                        "productId": 3,
                        "sportId": "sr:sport:1",
                    },
                    # Different event, earlier date.
                    {
                        "eventId": "sr:match:1002",
                        "marketId": "10",
                        "specifier": "",
                        "outcomeId": "2",
                        "productId": 1,
                        "sportId": "sr:sport:1",
                    },
                    # Event not present in outcomes -> must be skipped, not crash.
                    {
                        "eventId": "sr:match:9999",
                        "marketId": "10",
                        "specifier": "",
                        "outcomeId": "2",
                        "productId": 3,
                        "sportId": "sr:sport:1",
                    },
                ],
            },
            "outcomes": [
                {
                    "eventId": "sr:match:1001",
                    "estimateStartTime": _ms(EVENT_A_START),
                    "matchStatus": "Not start",
                    "homeTeamName": "Plymouth Argyle",
                    "awayTeamName": "Exeter City",
                    "sport": {
                        "name": "Football",
                        "category": {
                            "name": "England",
                            "tournament": {"name": "EFL Cup"},
                        },
                    },
                    "markets": [
                        {
                            "id": "166",
                            "specifier": "total=8.5",
                            "desc": "Corners - Over/Under",
                            "outcomes": [
                                {"id": "12", "desc": "Over 8.5", "odds": "1.45"},
                                {"id": "13", "desc": "Under 8.5", "odds": "2.60"},
                            ],
                        },
                        {
                            "id": "166",
                            "specifier": "total=9.5",
                            "desc": "Corners - Over/Under",
                            "outcomes": [
                                {"id": "12", "desc": "Over 9.5", "odds": "1.90"},
                                {"id": "13", "desc": "Under 9.5", "odds": "1.85"},
                            ],
                        },
                        {
                            "id": "1",
                            "specifier": "",
                            "desc": "1X2",
                            "outcomes": [
                                {"id": "1", "desc": "Home", "odds": "2.10"},
                                {"id": "2", "desc": "Draw", "odds": "3.30"},
                                {"id": "3", "desc": "Away", "odds": "3.50"},
                            ],
                        },
                    ],
                },
                {
                    "eventId": "sr:match:1002",
                    "estimateStartTime": _ms(EVENT_B_START),
                    "matchStatus": "Not start",
                    "homeTeamName": "Boston River",
                    "awayTeamName": "Liverpool Montevideo",
                    "sport": {
                        "name": "Football",
                        "category": {
                            "name": "Uruguay",
                            "tournament": {"name": "Tercera Division"},
                        },
                    },
                    "markets": [
                        {
                            "id": "10",
                            "specifier": "",
                            "desc": "Home/Away",
                            "outcomes": [
                                {"id": "1", "desc": "Home", "odds": "1.50"},
                                {"id": "2", "desc": "Away", "odds": "2.50"},
                            ],
                        }
                    ],
                },
            ],
        },
    }


class ParseBookingTests(unittest.TestCase):
    def test_parses_valid_response(self):
        result = parse_booking("HW7UDH", make_payload())
        self.assertEqual(result.booking_code, "HW7UDH")
        # 4 selections in, but one references a missing event -> 3 resolved.
        self.assertEqual(result.total_selections, 3)
        self.assertEqual(len(result.selections), 3)

    def test_total_odds_from_display_total_odds(self):
        result = parse_booking("HW7UDH", make_payload())
        self.assertEqual(result.total_odds, 12.34)

    def test_event_id_matches_outcome(self):
        result = parse_booking("HW7UDH", make_payload())
        by_event = [s.event_id for s in result.selections]
        self.assertIn("sr:match:1001", by_event)
        self.assertIn("sr:match:1002", by_event)
        # The unmatched event must not appear.
        self.assertNotIn("sr:match:9999", by_event)

    def test_market_matching_uses_specifier(self):
        # marketId 166 exists as total=8.5 and total=9.5; selection asked for 9.5.
        result = parse_booking("HW7UDH", make_payload())
        s = next(s for s in result.selections if s.event_id == "sr:match:1001" and s.specifier)
        self.assertEqual(s.specifier, "total=9.5")
        self.assertEqual(s.outcome, "Over 9.5")

    def test_outcome_matching_picks_correct_outcome(self):
        # market id 1 has Home/Draw/Away; selection asked for outcomeId 3 (Away).
        result = parse_booking("HW7UDH", make_payload())
        s = next(
            s for s in result.selections
            if s.event_id == "sr:match:1001" and s.market == "1X2"
        )
        self.assertEqual(s.outcome, "Away")
        self.assertEqual(s.odds, 3.50)

    def test_odds_extracted_as_float(self):
        result = parse_booking("HW7UDH", make_payload())
        for s in result.selections:
            self.assertIsInstance(s.odds, float)
        picked = next(s for s in result.selections if s.outcome == "Over 9.5")
        self.assertEqual(picked.odds, 1.90)

    def test_estimate_start_time_conversion(self):
        result = parse_booking("HW7UDH", make_payload())
        a = next(s for s in result.selections if s.event_id == "sr:match:1001")
        self.assertEqual(a.kickoff, EVENT_A_START)
        self.assertEqual(a.kickoff_date, "2026-08-11")
        self.assertEqual(a.kickoff_time, "08:00")
        self.assertEqual(a.local_kickoff_date, "2026-08-11")
        self.assertEqual(a.local_kickoff_time, "09:00")

    def test_lagos_conversion_can_move_event_to_next_date(self):
        result = parse_booking("HW7UDH", make_payload())
        b = next(s for s in result.selections if s.event_id == "sr:match:1002")
        self.assertEqual(b.kickoff_date, "2026-08-10")
        self.assertEqual(b.kickoff_time, "22:00")
        self.assertEqual(b.local_kickoff_date, "2026-08-10")
        self.assertEqual(b.local_kickoff_time, "23:00")

    def test_local_date_grouping_preserves_chronological_order_across_midnight(self):
        payload = make_payload()
        payload["data"]["outcomes"][1]["estimateStartTime"] = _ms(
            datetime(2026, 8, 10, 23, 30, tzinfo=timezone.utc)
        )
        result = parse_booking("HW7UDH", payload)
        self.assertEqual(result.selections[0].local_kickoff_date, "2026-08-11")
        self.assertEqual(result.selections[0].local_kickoff_time, "00:30")
        self.assertEqual([s.kickoff for s in result.selections], sorted(s.kickoff for s in result.selections))

    def test_explicit_statuses_are_normalized(self):
        kickoff = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
        self.assertEqual(determine_game_status("Not start", kickoff), "upcoming")
        self.assertEqual(determine_game_status("In Progress", kickoff), "live")
        self.assertEqual(determine_game_status("Finished", kickoff), "ended")

    def test_status_fallback_uses_timezone_aware_kickoff(self):
        kickoff = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
        self.assertEqual(determine_game_status(None, kickoff, datetime(2026, 8, 13, 11, 59, tzinfo=timezone.utc)), "upcoming")
        self.assertEqual(determine_game_status(None, kickoff, kickoff), "live")
        self.assertEqual(determine_game_status(None, kickoff, datetime(2026, 8, 13, 15, tzinfo=timezone.utc)), "ended")

    def test_remaining_odds_include_only_upcoming_selections(self):
        payload = make_payload()
        payload["data"]["outcomes"][0]["matchStatus"] = "Live"
        result = parse_booking("HW7UDH", payload)
        upcoming_odds = [s.odds for s in result.selections if s.game_status == "upcoming"]
        expected = 1.0
        for odds in upcoming_odds:
            expected *= odds
        self.assertAlmostEqual(result.remaining_odds, expected)

    def test_remaining_odds_zero_with_no_upcoming_selections(self):
        payload = make_payload()
        for outcome in payload["data"]["outcomes"]:
            outcome["matchStatus"] = "Finished"
        self.assertEqual(parse_booking("HW7UDH", payload).remaining_odds, 0.0)

    def test_remaining_odds_ignore_missing_and_invalid_odds(self):
        payload = make_payload()
        markets = payload["data"]["outcomes"][0]["markets"]
        markets[0]["outcomes"][0]["odds"] = None
        markets[1]["outcomes"][0]["odds"] = "not-a-number"
        result = parse_booking("HW7UDH", payload)
        self.assertGreaterEqual(result.remaining_odds, 0.0)

    def test_remaining_odds_support_large_finite_products(self):
        payload = make_payload()
        for outcome in payload["data"]["outcomes"]:
            for market in outcome["markets"]:
                for picked in market["outcomes"]:
                    picked["odds"] = "1000000"
        result = parse_booking("HW7UDH", payload)
        self.assertTrue(result.remaining_odds > 1_000_000)

    def test_sorted_by_complete_datetime_across_dates(self):
        # Even though event B has a later clock time (22:00), it is on the
        # earlier date, so it must sort FIRST.
        result = parse_booking("HW7UDH", make_payload())
        kickoffs = [s.kickoff for s in result.selections]
        self.assertEqual(kickoffs, sorted(kickoffs))
        self.assertEqual(result.selections[0].event_id, "sr:match:1002")
        self.assertEqual(result.selections[0].kickoff_date, "2026-08-10")
        self.assertEqual(result.selections[-1].kickoff_date, "2026-08-11")

    def test_multiple_dates_present(self):
        result = parse_booking("HW7UDH", make_payload())
        dates = {s.kickoff_date for s in result.selections}
        self.assertEqual(dates, {"2026-08-10", "2026-08-11"})

    def test_missing_outcomes_yields_empty_selections(self):
        payload = make_payload()
        payload["data"]["outcomes"] = []
        result = parse_booking("HW7UDH", payload)
        self.assertEqual(result.total_selections, 0)
        self.assertEqual(result.selections, [])
        # Total odds still reported from the ticket.
        self.assertEqual(result.total_odds, 12.34)

    def test_unavailable_booking_raises_404(self):
        payload = make_payload()
        payload["isAvailable"] = False
        with self.assertRaises(HTTPException) as ctx:
            parse_booking("HW7UDH", payload)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_invalid_biz_code_raises_404(self):
        payload = make_payload()
        payload["bizCode"] = 40000
        with self.assertRaises(HTTPException) as ctx:
            parse_booking("HW7UDH", payload)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_missing_data_raises_404(self):
        with self.assertRaises(HTTPException) as ctx:
            parse_booking("HW7UDH", {"bizCode": 10000, "isAvailable": True})
        self.assertEqual(ctx.exception.status_code, 404)

    def test_malformed_selection_is_skipped_not_fatal(self):
        payload = make_payload()
        # Point one selection at a market id that does not exist on the event.
        payload["data"]["ticket"]["selections"][0]["marketId"] = "does-not-exist"
        result = parse_booking("HW7UDH", payload)
        # Still parses the remaining good selections instead of crashing.
        self.assertEqual(result.total_selections, 2)


# ---- HTTP-layer tests (mocked httpx) -------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, raise_json=False):
        self.status_code = status_code
        self._json_data = json_data
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("no json")
        return self._json_data


def fake_client_factory(*, response=None, get_exc=None):
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None, headers=None):
            if get_exc is not None:
                raise get_exc
            return response

    return _FakeClient


class GetBookingHttpTests(unittest.TestCase):
    def test_empty_booking_code_raises_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(get_booking("   "))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_success_path(self):
        resp = FakeResponse(200, make_payload())
        with mock.patch.object(sportybet.httpx, "AsyncClient", fake_client_factory(response=resp)):
            result = asyncio.run(get_booking("HW7UDH"))
        self.assertEqual(result.total_selections, 3)

    def test_non_200_raises_502(self):
        resp = FakeResponse(500, {})
        with mock.patch.object(sportybet.httpx, "AsyncClient", fake_client_factory(response=resp)):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(get_booking("HW7UDH"))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_timeout_raises_504(self):
        import httpx

        factory = fake_client_factory(get_exc=httpx.TimeoutException("timed out"))
        with mock.patch.object(sportybet.httpx, "AsyncClient", factory):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(get_booking("HW7UDH"))
        self.assertEqual(ctx.exception.status_code, 504)

    def test_request_error_raises_502(self):
        import httpx

        factory = fake_client_factory(get_exc=httpx.ConnectError("boom"))
        with mock.patch.object(sportybet.httpx, "AsyncClient", factory):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(get_booking("HW7UDH"))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_invalid_json_raises_502(self):
        resp = FakeResponse(200, raise_json=True)
        with mock.patch.object(sportybet.httpx, "AsyncClient", fake_client_factory(response=resp)):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(get_booking("HW7UDH"))
        self.assertEqual(ctx.exception.status_code, 502)


# ---- Rebook / remove-selection tests (stateful fake SportyBet) -----------
#
# These tests use an in-memory fake of SportyBet's share API that genuinely
# models the discovered behaviour:
#   * GET  /api/ng/orders/share/{code}  -> ticket built from stored selections
#   * POST /api/ng/orders/share         -> stores posted selections under a NEW
#                                           deterministic code, returns shareCode
# Because POST really stores and GET really serves, consecutive removals operate
# on genuinely-updated tickets (no hand-waving).

import hashlib

# Event catalog. The trio A/B/C is the spec's canonical cross-date case:
#   A = 2026-08-12 23:30, B = 2026-08-13 00:15, C = 2026-08-13 08:30
# D is on an earlier date (2026-08-11) and carries a market specifier.
_UTC = timezone.utc
CATALOG = {
    "sr:match:D": {
        "estimateStartTime": _ms(datetime(2026, 8, 11, 10, 0, tzinfo=_UTC)),
        "matchStatus": "Not start",
        "homeTeamName": "Delta United",
        "awayTeamName": "Delta City",
        "sport": {"category": {"name": "Landia", "tournament": {"name": "D-League"}}},
        "markets": [
            {
                "id": "166",
                "specifier": "total=8.5",
                "desc": "Corners - Over/Under",
                "outcomes": [{"id": "12", "desc": "Over 8.5", "odds": "1.45"}],
            }
        ],
    },
    "sr:match:A": {
        "estimateStartTime": _ms(datetime(2026, 8, 12, 23, 30, tzinfo=_UTC)),
        "matchStatus": "Not start",
        "homeTeamName": "Alpha FC",
        "awayTeamName": "Alpha Town",
        "sport": {"category": {"name": "Aland", "tournament": {"name": "A-League"}}},
        "markets": [
            {"id": "1", "specifier": "", "desc": "1X2",
             "outcomes": [{"id": "1", "desc": "Home", "odds": "2.00"}]}
        ],
    },
    "sr:match:B": {
        "estimateStartTime": _ms(datetime(2026, 8, 13, 0, 15, tzinfo=_UTC)),
        "matchStatus": "Not start",
        "homeTeamName": "Bravo FC",
        "awayTeamName": "Bravo Town",
        "sport": {"category": {"name": "Bland", "tournament": {"name": "B-League"}}},
        "markets": [
            {"id": "1", "specifier": "", "desc": "1X2",
             "outcomes": [{"id": "1", "desc": "Home", "odds": "1.50"}]}
        ],
    },
    "sr:match:C": {
        "estimateStartTime": _ms(datetime(2026, 8, 13, 8, 30, tzinfo=_UTC)),
        "matchStatus": "Not start",
        "homeTeamName": "Charlie FC",
        "awayTeamName": "Charlie Town",
        "sport": {"category": {"name": "Cland", "tournament": {"name": "C-League"}}},
        "markets": [
            {"id": "1", "specifier": "", "desc": "1X2",
             "outcomes": [{"id": "1", "desc": "Home", "odds": "1.80"}]}
        ],
    },
    "sr:match:E": {
        "estimateStartTime": _ms(datetime(2026, 8, 14, 12, 0, tzinfo=_UTC)),
        "matchStatus": "Not start",
        "homeTeamName": "Echo FC",
        "awayTeamName": "Echo Town",
        "sport": {"category": {"name": "Eland", "tournament": {"name": "E-League"}}},
        "markets": [
            {"id": "1", "specifier": "", "desc": "1X2",
             "outcomes": [{"id": "1", "desc": "Home", "odds": "1.60"}]}
        ],
    },
    "sr:match:F": {
        "estimateStartTime": _ms(datetime(2026, 8, 14, 18, 0, tzinfo=_UTC)),
        "matchStatus": "Not start",
        "homeTeamName": "Foxtrot FC",
        "awayTeamName": "Foxtrot Town",
        "sport": {"category": {"name": "Fland", "tournament": {"name": "F-League"}}},
        "markets": [
            {"id": "1", "specifier": "", "desc": "1X2",
             "outcomes": [{"id": "1", "desc": "Home", "odds": "2.20"}]}
        ],
    },
}


def _sel(event_id, market_id, outcome_id, *, specifier=None, product_id=3, sport_id="sr:sport:1"):
    item = {
        "eventId": event_id,
        "marketId": market_id,
        "outcomeId": outcome_id,
        "productId": product_id,
        "sportId": sport_id,
    }
    if specifier:
        item["specifier"] = specifier
    return item


# Default seed booking: D (with specifier) + A + B + C.
DEFAULT_SEED = [
    _sel("sr:match:D", "166", "12", specifier="total=8.5"),
    _sel("sr:match:A", "1", "1"),
    _sel("sr:match:B", "1", "1"),
    _sel("sr:match:C", "1", "1"),
]


def _find_outcome_odds(selection):
    ev = CATALOG[selection["eventId"]]
    for m in ev["markets"]:
        if str(m["id"]) != str(selection["marketId"]):
            continue
        if selection.get("specifier") and str(m.get("specifier")) != str(selection["specifier"]):
            continue
        for oc in m["outcomes"]:
            if str(oc["id"]) == str(selection["outcomeId"]):
                return float(oc["odds"])
    return 1.0


def _build_share_payload(code, selections):
    """Construct a SportyBet-shaped share payload from a set of selections."""
    outcomes = []
    seen = set()
    total = 1.0
    for sel in selections:
        total *= _find_outcome_odds(sel)
        eid = sel["eventId"]
        if eid not in seen:
            seen.add(eid)
            outcomes.append({"eventId": eid, **CATALOG[eid]})
    return {
        "bizCode": 10000,
        "isAvailable": True,
        "message": "Success",
        "data": {
            "shareCode": code,
            "ticket": {"selections": selections, "displayTotalOdds": f"{total:.2f}"},
            "outcomes": outcomes,
        },
    }


def _gen_code(selections):
    key = "|".join(
        sorted(
            f"{s['eventId']}:{s['marketId']}:{s.get('specifier', '')}:{s['outcomeId']}"
            for s in selections
        )
    )
    return "C" + hashlib.sha1(key.encode()).hexdigest()[:6].upper()


def make_fake_sportybet(
    seed_code="HW7UDH",
    seed_selections=None,
    *,
    post_exc=None,
    post_biz=10000,
    post_bad_json=False,
    poison_new_code_fetch=False,
    unavailable_codes=(),
):
    """Return (FakeClientClass, state). state['posted'] captures POST bodies."""
    state = {"store": {}, "posted": []}
    state["store"][seed_code] = (
        list(DEFAULT_SEED) if seed_selections is None else list(seed_selections)
    )

    class _Fake:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None, headers=None):
            code = url.rstrip("/").split("/")[-1]
            if code in unavailable_codes:
                return FakeResponse(200, {"bizCode": 10000, "isAvailable": False, "data": None})
            selections = state["store"].get(code)
            if selections is None:
                return FakeResponse(200, {"bizCode": 10000, "isAvailable": False, "data": None})
            return FakeResponse(200, _build_share_payload(code, selections))

        async def post(self, url, headers=None, json=None):
            state["posted"].append(json)
            if post_exc is not None:
                raise post_exc
            if post_bad_json:
                return FakeResponse(200, raise_json=True)
            if post_biz != 10000:
                return FakeResponse(200, {"bizCode": post_biz, "message": "err", "data": {}})
            new_code = _gen_code(json["selections"])
            # When poisoned, hand back a code we deliberately never store, so the
            # backend's follow-up GET of the new code fails (models "new share
            # code cannot be fetched").
            if not poison_new_code_fetch:
                state["store"][new_code] = json["selections"]
            return FakeResponse(
                200,
                {"bizCode": 10000, "isAvailable": True, "data": {"shareCode": new_code}},
            )

    return _Fake, state



class RebookTests(unittest.TestCase):
    def _remove(self, cls, code="HW7UDH", event_id="sr:match:D"):
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            return asyncio.run(sportybet.rebook_without_event(code, event_id))

    def test_remove_one_selection_returns_new_booking(self):
        cls, state = make_fake_sportybet()
        result = self._remove(cls, event_id="sr:match:D")
        self.assertNotEqual(result.booking_code, "HW7UDH")
        self.assertTrue(result.booking_code.startswith("C"))

    def test_remaining_selection_count(self):
        cls, _ = make_fake_sportybet()
        result = self._remove(cls, event_id="sr:match:D")
        self.assertEqual(result.total_selections, 3)  # 4 seeded - 1 removed

    def test_new_total_odds_from_sportybet(self):
        # Remaining A*B*C = 2.00 * 1.50 * 1.80 = 5.40, computed by the fake, not the UI.
        cls, _ = make_fake_sportybet()
        result = self._remove(cls, event_id="sr:match:D")
        self.assertEqual(result.total_odds, 5.40)

    def test_new_selection_details_fully_resolved(self):
        cls, _ = make_fake_sportybet()
        result = self._remove(cls, event_id="sr:match:D")
        for s in result.selections:
            self.assertTrue(s.home and s.away)
            self.assertTrue(s.competition and s.category)
            self.assertTrue(s.market and s.outcome)
            self.assertTrue(s.kickoff_date and s.kickoff_time)
            self.assertIsNotNone(s.odds)

    def test_payload_preserves_all_identity_fields(self):
        cls, state = make_fake_sportybet()
        self._remove(cls, event_id="sr:match:A")  # remove A; D (with specifier) stays
        posted = state["posted"][0]["selections"]
        self.assertEqual(len(posted), 3)
        for item in posted:
            self.assertIn("eventId", item)
            self.assertIn("marketId", item)
            self.assertIn("outcomeId", item)
            self.assertIn("productId", item)
            self.assertIn("sportId", item)

    def test_payload_preserves_specifier_when_present_and_omits_when_absent(self):
        cls, state = make_fake_sportybet()
        self._remove(cls, event_id="sr:match:A")
        posted = state["posted"][0]["selections"]
        d_sel = next(p for p in posted if p["eventId"] == "sr:match:D")
        a_free = next(p for p in posted if p["eventId"] == "sr:match:B")
        self.assertEqual(d_sel["specifier"], "total=8.5")  # preserved
        self.assertNotIn("specifier", a_free)  # omitted when absent

    def test_removed_event_absent_from_payload(self):
        cls, state = make_fake_sportybet()
        self._remove(cls, event_id="sr:match:D")
        posted = state["posted"][0]["selections"]
        self.assertTrue(all(p["eventId"] != "sr:match:D" for p in posted))

    def test_multiple_consecutive_removals_operate_on_latest_code(self):
        cls, state = make_fake_sportybet()
        # Removal 1: drop D from HW7UDH -> code1 (A, B, C).
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            r1 = asyncio.run(sportybet.rebook_without_event("HW7UDH", "sr:match:D"))
            self.assertEqual(r1.total_selections, 3)
            code1 = r1.booking_code
            # Removal 2: drop B from code1 (the NEW code) -> code2 (A, C).
            r2 = asyncio.run(sportybet.rebook_without_event(code1, "sr:match:B"))
        self.assertEqual(r2.total_selections, 2)
        self.assertNotEqual(r2.booking_code, code1)
        self.assertTrue(all(s.event_id != "sr:match:B" for s in r2.selections))
        # The 2nd POST body must derive from code1's ticket (A, C), not the original.
        second_post = state["posted"][1]["selections"]
        self.assertEqual({p["eventId"] for p in second_post}, {"sr:match:A", "sr:match:C"})

    def test_rebook_result_sorted_across_dates(self):
        # Canonical case: after removing D, order must be A(08-12 23:30) ->
        # B(08-13 00:15) -> C(08-13 08:30), i.e. date first, then time.
        cls, _ = make_fake_sportybet()
        result = self._remove(cls, event_id="sr:match:D")
        order = [(s.kickoff_date, s.kickoff_time) for s in result.selections]
        self.assertEqual(
            order,
            [("2026-08-12", "23:30"), ("2026-08-13", "00:15"), ("2026-08-13", "08:30")],
        )
        self.assertEqual([s.kickoff for s in result.selections],
                         sorted(s.kickoff for s in result.selections))

    def test_invalid_event_id_raises_404(self):
        cls, _ = make_fake_sportybet()
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_event("HW7UDH", "sr:match:nope"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_invalid_booking_code_raises_404(self):
        cls, _ = make_fake_sportybet(unavailable_codes=("HW7UDH",))
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_event("HW7UDH", "sr:match:D"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_empty_booking_code_raises_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(sportybet.rebook_without_event("   ", "sr:match:D"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_empty_event_id_raises_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(sportybet.rebook_without_event("HW7UDH", "  "))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_removing_last_selection_raises_400(self):
        cls, _ = make_fake_sportybet(seed_selections=[_sel("sr:match:A", "1", "1")])
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_event("HW7UDH", "sr:match:A"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_sportybet_rejection_19999_raises_502(self):
        cls, _ = make_fake_sportybet(post_biz=19999)
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_event("HW7UDH", "sr:match:D"))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_sportybet_rejection_19000_raises_502(self):
        cls, _ = make_fake_sportybet(post_biz=19000)
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_event("HW7UDH", "sr:match:D"))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_rebook_timeout_raises_504(self):
        import httpx

        cls, _ = make_fake_sportybet(post_exc=httpx.TimeoutException("slow"))
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_event("HW7UDH", "sr:match:D"))
        self.assertEqual(ctx.exception.status_code, 504)

    def test_rebook_invalid_json_raises_502(self):
        cls, _ = make_fake_sportybet(post_bad_json=True)
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_event("HW7UDH", "sr:match:D"))
        self.assertEqual(ctx.exception.status_code, 502)


# ---- Batch (multi-selection) removal tests -------------------------------
class BatchRebookTests(unittest.TestCase):
    """Covers rebook_without_events: removing many events in ONE rebooking."""

    def _remove(self, cls, event_ids, code="HW7UDH"):
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            return asyncio.run(sportybet.rebook_without_events(code, event_ids))

    # 1. Multiple event IDs removed in one go.
    def test_removes_multiple_events(self):
        cls, _ = make_fake_sportybet()
        result = self._remove(cls, ["sr:match:D", "sr:match:B"])
        self.assertEqual(result.total_selections, 2)  # 4 seeded - 2 removed
        remaining = {s.event_id for s in result.selections}
        self.assertEqual(remaining, {"sr:match:A", "sr:match:C"})

    # 2. Exactly ONE rebooking request is issued for a multi-remove.
    def test_single_post_for_multiple_removals(self):
        cls, state = make_fake_sportybet()
        self._remove(cls, ["sr:match:D", "sr:match:B"])
        self.assertEqual(len(state["posted"]), 1)  # one POST, not two

    # 3. Remaining selections keep their exact SportyBet identity fields.
    def test_remaining_identities_preserved(self):
        cls, state = make_fake_sportybet()
        self._remove(cls, ["sr:match:A"])
        posted = state["posted"][0]["selections"]
        self.assertEqual(len(posted), 3)
        for item in posted:
            for key in ("eventId", "marketId", "outcomeId", "productId", "sportId"):
                self.assertIn(key, item)

    # 4. Specifier preserved when present, omitted when absent.
    def test_specifier_preserved_in_batch(self):
        cls, state = make_fake_sportybet()
        self._remove(cls, ["sr:match:A", "sr:match:C"])  # keep D (specifier) + B
        posted = state["posted"][0]["selections"]
        d_sel = next(p for p in posted if p["eventId"] == "sr:match:D")
        b_sel = next(p for p in posted if p["eventId"] == "sr:match:B")
        self.assertEqual(d_sel["specifier"], "total=8.5")
        self.assertNotIn("specifier", b_sel)

    # 5. A brand-new share code is returned.
    def test_new_share_code_returned(self):
        cls, _ = make_fake_sportybet()
        result = self._remove(cls, ["sr:match:D"])
        self.assertNotEqual(result.booking_code, "HW7UDH")
        self.assertTrue(result.booking_code.startswith("C"))

    # 6. New authoritative odds come from the (re-fetched) new ticket.
    def test_new_authoritative_odds(self):
        # Remaining A*B*C = 2.00 * 1.50 * 1.80 = 5.40 (computed by SportyBet fake).
        cls, _ = make_fake_sportybet()
        result = self._remove(cls, ["sr:match:D"])
        self.assertEqual(result.total_odds, 5.40)

    # 7 & 8. Complete datetime sorting, date-first across dates.
    def test_result_sorted_by_complete_datetime(self):
        cls, _ = make_fake_sportybet()
        result = self._remove(cls, ["sr:match:D"])  # leaves A, B, C
        order = [(s.kickoff_date, s.kickoff_time) for s in result.selections]
        self.assertEqual(
            order,
            [("2026-08-12", "23:30"), ("2026-08-13", "00:15"), ("2026-08-13", "08:30")],
        )
        self.assertEqual([s.kickoff for s in result.selections],
                         sorted(s.kickoff for s in result.selections))

    # 9. Consecutive BULK removals operate on the latest code each time.
    def test_consecutive_bulk_removals_use_latest_code(self):
        # Seed 6 events: A,B,C,D,E,F. Remove [D,F] -> code1 (A,B,C,E, 4);
        # then remove [A,B] from code1 -> code2 (C,E, 2).
        seed = [
            _sel("sr:match:D", "166", "12", specifier="total=8.5"),
            _sel("sr:match:A", "1", "1"),
            _sel("sr:match:B", "1", "1"),
            _sel("sr:match:C", "1", "1"),
            _sel("sr:match:E", "1", "1"),
            _sel("sr:match:F", "1", "1"),
        ]
        cls, state = make_fake_sportybet(seed_selections=seed)
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            r1 = asyncio.run(
                sportybet.rebook_without_events("HW7UDH", ["sr:match:D", "sr:match:F"])
            )
            self.assertEqual(r1.total_selections, 4)
            code1 = r1.booking_code
            r2 = asyncio.run(
                sportybet.rebook_without_events(code1, ["sr:match:A", "sr:match:B"])
            )
        self.assertEqual(r2.total_selections, 2)
        self.assertNotEqual(r2.booking_code, code1)
        self.assertEqual({s.event_id for s in r2.selections}, {"sr:match:C", "sr:match:E"})
        # One POST per batch (two total), and the 2nd derived from code1's ticket.
        self.assertEqual(len(state["posted"]), 2)
        second_post = {p["eventId"] for p in state["posted"][1]["selections"]}
        self.assertEqual(second_post, {"sr:match:C", "sr:match:E"})

    # 10. Removing every game is rejected (no empty ticket sent to SportyBet).
    def test_removing_all_games_raises_400(self):
        cls, state = make_fake_sportybet()
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    sportybet.rebook_without_events(
                        "HW7UDH",
                        ["sr:match:A", "sr:match:B", "sr:match:C", "sr:match:D"],
                    )
                )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(len(state["posted"]), 0)  # never POSTed an empty set

    # 11. Empty selection list is rejected.
    def test_empty_event_ids_raises_400(self):
        cls, state = make_fake_sportybet()
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_events("HW7UDH", []))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(len(state["posted"]), 0)

    def test_blank_only_event_ids_raises_400(self):
        cls, _ = make_fake_sportybet()
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_events("HW7UDH", ["  ", ""]))
        self.assertEqual(ctx.exception.status_code, 400)

    # 12. Any invalid event id in the batch is a 404 (and nothing is POSTed).
    def test_invalid_event_id_in_batch_raises_404(self):
        cls, state = make_fake_sportybet()
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    sportybet.rebook_without_events("HW7UDH", ["sr:match:D", "sr:match:nope"])
                )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(len(state["posted"]), 0)

    # Duplicate event ids are de-duplicated safely (one removal, not an error).
    def test_duplicate_event_ids_deduped(self):
        cls, state = make_fake_sportybet()
        result = self._remove(cls, ["sr:match:D", "sr:match:D", "sr:match:D"])
        self.assertEqual(result.total_selections, 3)  # only D removed once
        self.assertEqual(len(state["posted"]), 1)
        self.assertTrue(all(p["eventId"] != "sr:match:D"
                            for p in state["posted"][0]["selections"]))

    # 13. SportyBet rejects the rebooking -> 502.
    def test_sportybet_failure_raises_502(self):
        cls, _ = make_fake_sportybet(post_biz=19999)
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_events("HW7UDH", ["sr:match:D"]))
        self.assertEqual(ctx.exception.status_code, 502)

    # 14. New code cannot be fetched afterwards -> failure (not a partial result).
    def test_new_code_fetch_failure_raises(self):
        cls, _ = make_fake_sportybet(poison_new_code_fetch=True)
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_events("HW7UDH", ["sr:match:D"]))
        self.assertIn(ctx.exception.status_code, (404, 502))

    # Timeout / invalid JSON on the rebooking POST -> failure.
    def test_batch_timeout_raises_504(self):
        import httpx

        cls, _ = make_fake_sportybet(post_exc=httpx.TimeoutException("slow"))
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_events("HW7UDH", ["sr:match:D"]))
        self.assertEqual(ctx.exception.status_code, 504)

    # 15. Atomic rollback: on failure the ORIGINAL booking is untouched.
    def test_atomic_original_booking_unchanged_on_failure(self):
        cls, _ = make_fake_sportybet(post_biz=19999)
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException):
                asyncio.run(sportybet.rebook_without_events("HW7UDH", ["sr:match:D"]))
            # The original code must still resolve to the full untouched booking.
            original = asyncio.run(sportybet.get_booking("HW7UDH"))
        self.assertEqual(original.booking_code, "HW7UDH")
        self.assertEqual(original.total_selections, 4)
        self.assertIn("sr:match:D", {s.event_id for s in original.selections})

    def test_invalid_booking_code_raises_404(self):
        cls, _ = make_fake_sportybet(unavailable_codes=("HW7UDH",))
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(sportybet.rebook_without_events("HW7UDH", ["sr:match:D"]))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_empty_booking_code_raises_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(sportybet.rebook_without_events("  ", ["sr:match:D"]))
        self.assertEqual(ctx.exception.status_code, 400)


# ---- Route-level tests (TestClient) --------------------------------------
class BookingRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_still_works(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_get_booking_route_returns_sorted_json(self):
        resp = FakeResponse(200, make_payload())
        with mock.patch.object(sportybet.httpx, "AsyncClient", fake_client_factory(response=resp)):
            api = self.client.get("/api/v1/bookings/HW7UDH")
        self.assertEqual(api.status_code, 200)
        body = api.json()
        self.assertEqual(body["booking_code"], "HW7UDH")
        self.assertEqual(body["total_selections"], 3)
        self.assertEqual(body["total_odds"], 12.34)
        dates = [s["kickoff_date"] for s in body["selections"]]
        self.assertEqual(dates, sorted(dates))

    def test_openapi_available(self):
        resp = self.client.get("/openapi.json")
        self.assertEqual(resp.status_code, 200)

    def test_remove_selected_route_returns_new_booking(self):
        # End-to-end through the HTTP route: POST event_ids -> new booking JSON.
        cls, _ = make_fake_sportybet()
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            api = self.client.post(
                "/api/v1/bookings/HW7UDH/remove-selected",
                json={"event_ids": ["sr:match:D", "sr:match:B"]},
            )
        self.assertEqual(api.status_code, 200)
        body = api.json()
        self.assertNotEqual(body["booking_code"], "HW7UDH")
        self.assertEqual(body["total_selections"], 2)
        event_ids = {s["event_id"] for s in body["selections"]}
        self.assertEqual(event_ids, {"sr:match:A", "sr:match:C"})
        dates = [s["kickoff_date"] for s in body["selections"]]
        self.assertEqual(dates, sorted(dates))

    def test_remove_selected_route_empty_list_is_error(self):
        cls, _ = make_fake_sportybet()
        with mock.patch.object(sportybet.httpx, "AsyncClient", cls):
            api = self.client.post(
                "/api/v1/bookings/HW7UDH/remove-selected",
                json={"event_ids": []},
            )
        self.assertEqual(api.status_code, 400)


if __name__ == "__main__":
    unittest.main()
