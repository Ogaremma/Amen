from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from app.schemas.prediction import EvidenceQuality, PredictionExplanation
from app.schemas.prediction_booking import (
    PredictionBookingBatch,
    PredictionBookingBatchStatus,
    PredictionBookingCatalogueDiagnostics,
    PredictionBookingDay,
    PredictionBookingDayResult,
    PredictionBookingDayStatus,
    PredictionBookingPool,
    PredictionBookingPoolResult,
    PredictionBookingResult,
    PredictionBookingResultStatus,
    PredictionBookingSelection,
)
from app.schemas.sportybet_markets import SportyBetSelectionIdentity
from app.services.telegram_prediction_delivery import TelegramPredictionDeliveryService
from app.telegram.bot import TelegramBot
from app.telegram.prediction_formatter import (
    TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT,
    TelegramPredictionFormatter,
    TelegramPredictionMessageTooLarge,
    build_prediction_message,
)


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
TODAY = datetime(2026, 9, 12, 14, 0, tzinfo=timezone.utc)
TOMORROW = datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc)


def identity(event_id: str) -> SportyBetSelectionIdentity:
    return SportyBetSelectionIdentity(
        event_id=event_id,
        market_id="18",
        outcome_id="12",
        product_id=3,
        sport_id="sr:sport:1",
        specifier="total=1.5",
    )


def selection(
    event_id: str,
    kickoff: datetime = TODAY,
    *,
    home_team: str = "Home Team",
    away_team: str = "Away Team",
    odds: float = 1.45,
    evidence_score: float | None = None,
    explanation: PredictionExplanation | None = None,
) -> PredictionBookingSelection:
    return PredictionBookingSelection(
        identity=identity(event_id),
        home_team=home_team,
        away_team=away_team,
        competition="Test League",
        kickoff_at=kickoff,
        odds=odds,
        evidence_score=evidence_score,
        evidence_quality=EvidenceQuality.complete if evidence_score is not None else None,
        explanation=explanation,
    )


def batch(
    pool: PredictionBookingPool,
    day: PredictionBookingDay,
    selections: list[PredictionBookingSelection],
    *,
    index: int = 1,
    code: str | None = None,
    status: PredictionBookingBatchStatus = PredictionBookingBatchStatus.created,
    error: str | None = None,
) -> PredictionBookingBatch:
    return PredictionBookingBatch(
        pool=pool,
        day=day,
        batch_index=index,
        selection_count=len(selections),
        selections=selections,
        booking_code=code,
        batch_identity=f"{pool.value}-{day.value}-{index}",
        status=status,
        error=error,
    )


def day_result(
    pool: PredictionBookingPool,
    day: PredictionBookingDay,
    *,
    selections: list[PredictionBookingSelection] | None = None,
    batches: list[PredictionBookingBatch] | None = None,
    status: PredictionBookingDayStatus | None = None,
) -> PredictionBookingDayResult:
    selected = selections or []
    day_batches = batches or []
    codes = [item.booking_code for item in day_batches if item.booking_code]
    if status is None:
        if not selected:
            status = PredictionBookingDayStatus.empty
        elif all(item.booking_code for item in day_batches):
            status = PredictionBookingDayStatus.complete
        else:
            status = PredictionBookingDayStatus.failed
    return PredictionBookingDayResult(
        pool=pool,
        day=day,
        selection_count=len(selected),
        selections=selected,
        batches=day_batches,
        booking_codes=codes,
        rejected=[],
        status=status,
    )


def empty_result() -> PredictionBookingResult:
    return PredictionBookingResult(
        generated_at=NOW,
        window_start=NOW,
        window_end_exclusive=TOMORROW + timedelta(days=1),
        catalogue=PredictionBookingCatalogueDiagnostics(
            expected_total=0,
            retrieved_total=0,
            parsed_fixtures=0,
            pages_fetched=1,
            pagination_complete=True,
            retrieved_at=NOW,
            fresh=True,
            authoritative=True,
        ),
        qualifying=PredictionBookingPoolResult(
            pool=PredictionBookingPool.qualifying,
            today=day_result(PredictionBookingPool.qualifying, PredictionBookingDay.today),
            tomorrow=day_result(PredictionBookingPool.qualifying, PredictionBookingDay.tomorrow),
        ),
        predictions=PredictionBookingPoolResult(
            pool=PredictionBookingPool.predictions,
            today=day_result(PredictionBookingPool.predictions, PredictionBookingDay.today),
            tomorrow=day_result(PredictionBookingPool.predictions, PredictionBookingDay.tomorrow),
        ),
        status=PredictionBookingResultStatus.complete,
    )


def result_with(
    *,
    qualifying_today: PredictionBookingDayResult,
    prediction_today: PredictionBookingDayResult,
    qualifying_tomorrow: PredictionBookingDayResult | None = None,
    prediction_tomorrow: PredictionBookingDayResult | None = None,
) -> PredictionBookingResult:
    current = empty_result()
    current.qualifying.today = qualifying_today
    current.predictions.today = prediction_today
    if qualifying_tomorrow is not None:
        current.qualifying.tomorrow = qualifying_tomorrow
    if prediction_tomorrow is not None:
        current.predictions.tomorrow = prediction_tomorrow
    return current


def explanation() -> PredictionExplanation:
    return PredictionExplanation(
        evidence_quality=EvidenceQuality.complete,
        positive_signals=[
            "Strong recent Over 1.5 evidence",
            "Good goal-production profile",
            "Valid venue-specific evidence",
        ],
        negative_signals=["Away sample is small"],
        missing_signals=["Head-to-head evidence unavailable"],
        final_score=0.82,
        selection_decision="Selected by evidence-v1 threshold",
    )


class TelegramPredictionFormatterTests(unittest.TestCase):
    def test_one_message_contains_both_sections_and_all_days(self):
        payload = build_prediction_message(555, empty_result())
        text = payload["text"]
        self.assertIn("OVER 1.40–1.50 GAMES", text)
        self.assertIn("OVER 1.40–1.50 PREDICTIONS", text)
        self.assertEqual(text.count("<b>TODAY</b>"), 2)
        self.assertEqual(text.count("<b>TOMORROW</b>"), 2)
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertEqual(payload["chat_id"], 555)

    def test_qualifying_and_prediction_pools_stay_separate(self):
        selected = selection("sr:match:selected", home_team="Selected Home")
        unselected = selection("sr:match:unselected", home_team="Unselected Home")
        prediction = selection(
            "sr:match:selected",
            home_team="Selected Home",
            evidence_score=0.82,
        )
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
                selections=[selected, unselected],
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
                selections=[prediction],
            ),
        )
        text = build_prediction_message(1, current)["text"]
        qualifying_section, prediction_section = text.split(
            "OVER 1.40–1.50 PREDICTIONS"
        )
        self.assertIn("Unselected Home", qualifying_section)
        self.assertNotIn("Unselected Home", prediction_section)
        self.assertIn("Selected Home", qualifying_section)
        self.assertIn("Selected Home", prediction_section)

    def test_selections_are_ordered_chronologically_with_event_id_tiebreak(self):
        later = selection("sr:match:10", TODAY + timedelta(hours=2), home_team="Home Ten")
        earlier = selection("sr:match:30", TODAY + timedelta(hours=1), home_team="Home Thirty")
        tie_breaker = selection("sr:match:20", TODAY + timedelta(hours=1), home_team="Home Twenty")
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
                selections=[later, earlier, tie_breaker],
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
            ),
        )
        text = build_prediction_message(1, current)["text"]
        self.assertLess(text.index("Home Twenty"), text.index("Home Thirty"))
        self.assertLess(text.index("Home Thirty"), text.index("Home Ten"))

    def test_prediction_score_and_explanations_render_safely(self):
        prediction = selection(
            "sr:match:prediction",
            evidence_score=0.82,
            explanation=explanation(),
        )
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
                selections=[prediction],
            ),
        )
        text = build_prediction_message(1, current)["text"]
        self.assertIn("Over 1.5 @ 1.45", text)
        self.assertIn("Evidence score: 0.82", text)
        self.assertNotIn("%", text)
        self.assertIn("Strong recent Over 1.5 evidence", text)
        self.assertIn("Evidence notes:", text)
        self.assertIn("Away sample is small", text)
        self.assertIn("Head-to-head evidence unavailable", text)

    def test_special_characters_are_html_escaped(self):
        special = selection(
            "sr:match:special",
            home_team='A & B <strong> "quoted"',
            away_team="O'Brien Ünïcode",
            evidence_score=0.75,
            explanation=explanation(),
        )
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
                selections=[special],
            ),
        )
        text = build_prediction_message(1, current)["text"]
        self.assertIn("A &amp; B &lt;strong&gt; &quot;quoted&quot;", text)
        self.assertIn("O&#x27;Brien Ünïcode", text)
        self.assertNotIn("<strong>", text)

    def test_multiple_booking_codes_have_exact_native_copy_buttons(self):
        first = selection("sr:match:1")
        second = selection("sr:match:2")
        prediction = selection("sr:match:3", evidence_score=0.82)
        tomorrow_qualifying = selection(
            "sr:match:4",
            TOMORROW,
            odds=1.49,
        )
        tomorrow_prediction = selection(
            "sr:match:5",
            TOMORROW,
            odds=1.47,
            evidence_score=0.79,
        )
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
                selections=[first, second],
                batches=[
                    batch(
                        PredictionBookingPool.qualifying,
                        PredictionBookingDay.today,
                        [first],
                        code="QUAL1",
                    ),
                    batch(
                        PredictionBookingPool.qualifying,
                        PredictionBookingDay.today,
                        [second],
                        index=2,
                        code="QUAL2",
                    ),
                ],
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
                selections=[prediction],
                batches=[
                    batch(
                        PredictionBookingPool.predictions,
                        PredictionBookingDay.today,
                        [prediction],
                        code="PRED1",
                    )
                ],
            ),
            qualifying_tomorrow=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.tomorrow,
                selections=[tomorrow_qualifying],
                batches=[
                    batch(
                        PredictionBookingPool.qualifying,
                        PredictionBookingDay.tomorrow,
                        [tomorrow_qualifying],
                        code="QUALTOM",
                    )
                ],
            ),
            prediction_tomorrow=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.tomorrow,
                selections=[tomorrow_prediction],
                batches=[
                    batch(
                        PredictionBookingPool.predictions,
                        PredictionBookingDay.tomorrow,
                        [tomorrow_prediction],
                        code="PREDTOM",
                    )
                ],
            ),
        )
        payload = build_prediction_message(999, current)
        buttons = [
            button
            for row in payload["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertEqual(
            [button["copy_text"]["text"] for button in buttons],
            ["QUAL1", "QUAL2", "QUALTOM", "PRED1", "PREDTOM"],
        )
        self.assertIn("Over 1.5 @ 1.49", payload["text"])
        self.assertIn("Over 1.5 @ 1.47", payload["text"])
        self.assertIn(
            "Booking codes: <code>QUAL1</code>, <code>QUAL2</code>",
            payload["text"],
        )
        self.assertIn('"copy_text": {"text": "QUAL1"}', json.dumps(payload))

    def test_empty_pools_do_not_invent_games_or_buttons(self):
        payload = build_prediction_message(1, empty_result())
        text = payload["text"]
        self.assertEqual(text.count("No qualifying games currently available."), 2)
        self.assertEqual(
            text.count("No model-selected predictions meet the current evidence threshold."),
            2,
        )
        self.assertNotIn("reply_markup", payload)

    def test_current_market_rejection_is_not_described_as_empty_model_pool(self):
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
                status=PredictionBookingDayStatus.rejected,
            ),
        )
        text = build_prediction_message(1, current)["text"]
        self.assertIn(
            "Model-selected predictions are not currently bookable after market validation.",
            text,
        )
        prediction_section = text.split("OVER 1.40–1.50 PREDICTIONS", 1)[1]
        today_section = prediction_section.split("<b>TOMORROW</b>", 1)[0]
        self.assertNotIn(
            "No model-selected predictions meet the current evidence threshold.",
            today_section,
        )

    def test_failed_batch_is_reported_without_a_fake_button(self):
        successful = selection("sr:match:success")
        failed = selection("sr:match:failed")
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
                selections=[successful, failed],
                batches=[
                    batch(
                        PredictionBookingPool.qualifying,
                        PredictionBookingDay.today,
                        [successful],
                        code="GOOD1",
                    ),
                    batch(
                        PredictionBookingPool.qualifying,
                        PredictionBookingDay.today,
                        [failed],
                        index=2,
                        status=PredictionBookingBatchStatus.failed,
                        error="provider failure",
                    ),
                ],
                status=PredictionBookingDayStatus.partial,
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
            ),
        )
        payload = build_prediction_message(1, current)
        buttons = [
            button
            for row in payload["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["copy_text"]["text"], "GOOD1")
        self.assertIn(
            "Booking partly unavailable; 1 of 2 booking batches succeeded.",
            payload["text"],
        )
        self.assertNotIn("provider failure", payload["text"])

    def test_large_pool_raises_instead_of_silently_dropping_games(self):
        selections = [
            selection(
                f"sr:match:{index}",
                TODAY + timedelta(minutes=index),
                home_team=f"Home Team {index} " + "X" * 100,
                away_team=f"Away Team {index} " + "Y" * 100,
            )
            for index in range(100)
        ]
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
                selections=selections,
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
            ),
        )
        with self.assertRaises(TelegramPredictionMessageTooLarge) as context:
            TelegramPredictionFormatter().format(current)
        self.assertEqual(context.exception.qualifying_count, 100)
        self.assertGreater(
            context.exception.required_utf16_length,
            TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT,
        )


class _FakeBot:
    def __init__(self):
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, payload):
        self.messages.append(payload)
        return {"ok": True, "result": True}


class _FakeBookingService:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    async def create_booking_pools(self, evaluations, **kwargs):
        self.calls += 1
        self.evaluations = evaluations
        self.kwargs = kwargs
        return self.value


class TelegramPredictionDeliveryServiceTests(unittest.TestCase):
    def test_current_message_is_generated_and_sent_once(self):
        prediction = selection("sr:match:prediction", evidence_score=0.82)
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
                selections=[prediction],
            ),
        )
        bot = _FakeBot()
        booking_service = _FakeBookingService(current)
        service = TelegramPredictionDeliveryService(
            bot,
            booking_service=booking_service,
        )

        returned = asyncio.run(
            service.send_current_prediction_message(
                777,
                [],
                now=NOW,
                page_size=50,
                max_pages=2,
            )
        )

        self.assertIs(returned, current)
        self.assertEqual(booking_service.calls, 1)
        self.assertEqual(len(bot.messages), 1)
        self.assertEqual(bot.messages[0]["chat_id"], 777)
        self.assertIn("OVER 1.40–1.50 GAMES", bot.messages[0]["text"])
        self.assertIn("OVER 1.40–1.50 PREDICTIONS", bot.messages[0]["text"])


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "result": True}


class _FakeTelegramClient:
    def __init__(self):
        self.posts = []

    async def post(self, url, json=None):
        self.posts.append((url, json))
        return _FakeResponse()


class TelegramBotTransportTests(unittest.TestCase):
    def test_formatted_payload_uses_existing_raw_http_transport(self):
        prediction = selection("sr:match:prediction", evidence_score=0.82)
        current = result_with(
            qualifying_today=day_result(
                PredictionBookingPool.qualifying,
                PredictionBookingDay.today,
                selections=[prediction],
                batches=[
                    batch(
                        PredictionBookingPool.qualifying,
                        PredictionBookingDay.today,
                        [prediction],
                        code="QUALBOT",
                    )
                ],
            ),
            prediction_today=day_result(
                PredictionBookingPool.predictions,
                PredictionBookingDay.today,
                selections=[prediction],
                batches=[
                    batch(
                        PredictionBookingPool.predictions,
                        PredictionBookingDay.today,
                        [prediction],
                        code="PREDBOT",
                    )
                ],
            ),
        )
        payload = build_prediction_message(123, current)
        client = _FakeTelegramClient()
        bot = TelegramBot("123456:FAKE-TOKEN", "https://amen.example.com", client=client)

        response = asyncio.run(bot.send_message(payload))

        self.assertTrue(response["ok"])
        self.assertEqual(len(client.posts), 1)
        url, sent_payload = client.posts[0]
        self.assertTrue(url.endswith("/sendMessage"))
        self.assertIs(sent_payload, payload)
        self.assertIn("copy_text", json.dumps(sent_payload))
        self.assertNotIn("FAKE-TOKEN", json.dumps(sent_payload))


if __name__ == "__main__":
    unittest.main()
