import json
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.services.sportybet_markets import (
    extract_over_one_half_candidates,
    parse_upcoming_events_markets,
)


def payload(events, total=1):
    return {
        "bizCode": 10000,
        "message": "0#0",
        "data": {
            "totalNum": total,
            "tournaments": [
                {
                    "id": "sr:tournament:17",
                    "name": "Premier League",
                    "events": events,
                }
            ],
        },
    }


def over_market(**overrides):
    market = {
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
                "odds": "1.45",
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
    market.update(overrides)
    return market


def event(event_id="sr:match:1", markets=None):
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
        "markets": markets if markets is not None else [over_market()],
    }


def parsed_fixture(raw_event):
    return parse_upcoming_events_markets(payload([raw_event])).fixtures[0]


class SportyBetMarketParserTests(unittest.TestCase):
    def test_valid_over_one_half_candidate_preserves_exact_identity(self):
        candidate = extract_over_one_half_candidates(
            [parsed_fixture(event())]
        )[0]

        self.assertEqual(
            candidate.identity.model_dump(),
            {
                "event_id": "sr:match:1",
                "market_id": "18",
                "outcome_id": "12",
                "product_id": 3,
                "sport_id": "sr:sport:1",
                "specifier": "total=1.5",
            },
        )
        self.assertEqual(candidate.fixture.home_team_name, "Stevenage FC")
        self.assertEqual(candidate.fixture.away_team_name, "Reading FC")
        self.assertEqual(candidate.fixture.competition, "EFL Cup")
        self.assertEqual(candidate.market.description, "Over/Under")
        self.assertEqual(candidate.market.source_type, "BET_RADAR")
        self.assertEqual(candidate.outcome.odds, 1.45)
        self.assertEqual(candidate.outcome.probability, 0.691803)

    def test_target_odds_range_is_inclusive_lower_and_exclusive_upper(self):
        odds_values = ["1.39", "1.40", "1.49", "1.50", "1.51"]
        fixtures = []
        for index, odds in enumerate(odds_values):
            market = over_market()
            market["outcomes"][0]["odds"] = odds
            fixtures.append(parsed_fixture(event(f"sr:match:{index}", [market])))

        candidates = extract_over_one_half_candidates(fixtures)

        self.assertEqual(
            [candidate.outcome.odds for candidate in candidates], [1.40, 1.49]
        )

    def test_inactive_missing_and_invalid_odds_are_skipped(self):
        cases = [
            {"isActive": 0},
            {"odds": None},
            {"odds": "not-a-number"},
            {"odds": "0"},
            {"odds": "-1.45"},
        ]
        fixtures = []
        for index, overrides in enumerate(cases):
            market = over_market()
            market["outcomes"][0].update(overrides)
            fixtures.append(parsed_fixture(event(f"sr:match:{index}", [market])))

        self.assertEqual(extract_over_one_half_candidates(fixtures), [])

    def test_wrong_market_specifier_outcome_and_product_are_skipped(self):
        cases = [
            over_market(id="19"),
            over_market(specifier="total=2.5"),
            over_market(outcomes=[
                {"id": "13", "odds": "1.45", "isActive": 1, "desc": "Under 1.5"}
            ]),
            over_market(product=4),
            over_market(outcomes=[
                {"id": "12", "odds": "1.45", "isActive": 1, "desc": "Over 2.5"}
            ]),
        ]
        fixtures = [parsed_fixture(event(f"sr:match:{index}", [market])) for index, market in enumerate(cases)]

        self.assertEqual(extract_over_one_half_candidates(fixtures), [])

    def test_multiple_markets_do_not_confuse_the_extractor(self):
        fixture = parsed_fixture(
            event(
                markets=[
                    {
                        "id": "1",
                        "product": 3,
                        "outcomes": [
                            {"id": "1", "odds": "2.10", "isActive": 1, "desc": "Home"},
                            {"id": "2", "odds": "3.20", "isActive": 1, "desc": "Draw"},
                            {"id": "3", "odds": "3.40", "isActive": 1, "desc": "Away"},
                        ],
                    },
                    over_market(specifier="total=2.5"),
                    over_market(),
                ]
            )
        )

        candidates = extract_over_one_half_candidates([fixture])

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].identity.specifier, "total=1.5")

    def test_duplicate_exact_identity_is_deduplicated(self):
        fixtures = [parsed_fixture(event()), parsed_fixture(event())]

        candidates = extract_over_one_half_candidates(fixtures)

        self.assertEqual(len(candidates), 1)

    def test_malformed_events_and_markets_are_skipped_safely(self):
        raw_page = payload(
            [event(), None, {"eventId": "bad"}, {"eventId": "bad", "markets": []}],
            total=4,
        )

        page = parse_upcoming_events_markets(raw_page)

        self.assertEqual(page.total_num, 4)
        self.assertEqual(len(page.fixtures), 1)

    def test_invalid_business_response_is_rejected(self):
        with self.assertRaises(HTTPException):
            parse_upcoming_events_markets({"bizCode": 19999})


class SportyBetMarketSnapshotTests(unittest.TestCase):
    def test_real_snapshots_parse_and_extract_over_one_half_candidates(self):
        snapshot_dir = Path(__file__).parents[2] / "snapshots"
        fixtures = []
        for page_number in range(1, 11):
            raw = json.loads(
                (snapshot_dir / f"sportybet-page-{page_number}.json").read_text(
                    encoding="utf-8"
                )
            )
            page = parse_upcoming_events_markets(raw)
            self.assertGreater(page.total_num, 0)
            fixtures.extend(page.fixtures)

        all_over_one_half = extract_over_one_half_candidates(
            fixtures, min_odds=0.01, max_odds_exclusive=100.0
        )
        target_candidates = extract_over_one_half_candidates(fixtures)

        self.assertEqual(len(fixtures), 972)
        self.assertEqual(len(all_over_one_half), 872)
        self.assertEqual(len(target_candidates), 84)
        for candidate in target_candidates:
            self.assertGreaterEqual(candidate.outcome.odds, 1.40)
            self.assertLess(candidate.outcome.odds, 1.50)
            self.assertEqual(candidate.identity.market_id, "18")
            self.assertEqual(candidate.identity.specifier, "total=1.5")
            self.assertEqual(candidate.identity.outcome_id, "12")
            self.assertEqual(candidate.identity.product_id, 3)
            self.assertEqual(candidate.identity.sport_id, "sr:sport:1")


if __name__ == "__main__":
    unittest.main()
