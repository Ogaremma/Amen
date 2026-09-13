from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import mock

from app.prediction_daily_runner import build_summary, main
from app.schemas.prediction_daily_broadcast import (
    DailyPredictionBroadcastResult,
    PredictionRecipientDeliveryResult,
    TelegramRecipient,
)
from app.schemas.prediction_daily_production import (
    DailyProductionDeliveryOutcome,
    PredictionMessageCategory,
    PredictionMessageDeliveryRecord,
    PredictionMessageDeliveryStatus,
)
from app.telegram.bot import (
    MENU_BUTTON_TEXT,
    OPEN_BUTTON_TEXT,
    WELCOME_TEXT,
    build_commands,
    build_start_message,
)
from app.telegram.prediction_formatter import TelegramPredictionFormatter
from backend.tests.test_prediction_daily_production_unittest import production_result


NOW = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
TODAY = date(2026, 9, 12)
TOMORROW = date(2026, 9, 13)


def delivery_record(
    record_id: int,
    category: PredictionMessageCategory,
) -> PredictionMessageDeliveryRecord:
    return PredictionMessageDeliveryRecord(
        id=record_id,
        generation_identity='generation',
        category=category,
        delivery_identity=f'delivery-{record_id}',
        input_identity='input',
        status=PredictionMessageDeliveryStatus.delivered,
        sent_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def recipient_result(
    user_id: int,
    *,
    outcome: DailyProductionDeliveryOutcome = DailyProductionDeliveryOutcome.delivered,
) -> PredictionRecipientDeliveryResult:
    return PredictionRecipientDeliveryResult(
        recipient=TelegramRecipient(
            telegram_user_id=user_id,
            telegram_chat_id=user_id,
        ),
        outcome=outcome,
        qualifying_delivery=delivery_record(user_id * 10, PredictionMessageCategory.qualifying),
        prediction_delivery=delivery_record(
            user_id * 10 + 1,
            PredictionMessageCategory.predictions,
        ),
    )


def broadcast_result(
    *,
    outcome: DailyProductionDeliveryOutcome,
    result=None,
    recipients=None,
) -> DailyPredictionBroadcastResult:
    recipients = recipients or []
    delivered_count = sum(
        recipient.outcome
        in {
            DailyProductionDeliveryOutcome.delivered,
            DailyProductionDeliveryOutcome.already_delivered,
        }
        for recipient in recipients
    )
    return DailyPredictionBroadcastResult(
        outcome=outcome,
        generation_identity='generation',
        input_identity='input',
        timezone_name='Africa/Lagos',
        today=TODAY,
        tomorrow=TOMORROW,
        generated_at=NOW if result is not None else None,
        error_summary=None if result is not None else 'Prediction generation failed: HTTPException',
        result=result,
        recipients=recipients,
        recipient_count=len(recipients),
        delivered_recipient_count=delivered_count,
        failed_recipient_count=0,
        undeliverable_recipient_count=0,
    )


class PredictionDailyRunnerSummaryTests(unittest.TestCase):
    def test_successful_fan_out_reports_safe_counts(self):
        result = broadcast_result(
            outcome=DailyProductionDeliveryOutcome.delivered,
            result=production_result(),
            recipients=[recipient_result(101), recipient_result(102)],
        )

        summary = build_summary(
            result,
            audience_source='database',
            eligible_recipient_count=2,
        )

        self.assertEqual(summary['eligible_recipient_count'], 2)
        self.assertEqual(summary['delivery_success_count'], 2)
        self.assertEqual(summary['delivery_failure_count'], 0)
        self.assertEqual(summary['qualifying_selection_count'], 3)
        self.assertEqual(summary['model_selected_selection_count'], 8)
        self.assertEqual(summary['qualifying_today_batch_count'], 1)
        self.assertEqual(summary['qualifying_tomorrow_batch_count'], 1)
        self.assertEqual(summary['prediction_today_batch_count'], 2)
        self.assertEqual(summary['prediction_tomorrow_batch_count'], 2)
        self.assertEqual(summary['delivery_categories']['qualifying']['delivered'], 2)
        self.assertEqual(summary['delivery_categories']['predictions']['delivered'], 2)
        self.assertEqual(summary['skipped_categories'], [])

    def test_generation_failure_reports_unavailable_counts_and_skipped_categories(self):
        result = broadcast_result(
            outcome=DailyProductionDeliveryOutcome.generation_failed,
        )

        summary = build_summary(
            result,
            audience_source='database',
            eligible_recipient_count=3,
        )

        self.assertEqual(summary['eligible_recipient_count'], 3)
        self.assertFalse(summary['counts_available'])
        self.assertIsNone(summary['qualifying_selection_count'])
        self.assertIsNone(summary['model_selected_selection_count'])
        self.assertEqual(
            summary['skipped_categories'],
            [
                {'category': 'qualifying', 'reason': 'generation_failed'},
                {'category': 'predictions', 'reason': 'generation_failed'},
            ],
        )
        self.assertEqual(summary['catalogue'], {'available': False})

    def test_zero_model_selected_predictions_is_reported_honestly(self):
        result = broadcast_result(
            outcome=DailyProductionDeliveryOutcome.delivered,
            result=production_result(today_predictions=0, tomorrow_predictions=0),
            recipients=[recipient_result(101)],
        )

        summary = build_summary(
            result,
            audience_source='database',
            eligible_recipient_count=1,
        )

        self.assertTrue(summary['counts_available'])
        self.assertEqual(summary['model_selected_selection_count'], 0)
        self.assertEqual(summary['skipped_categories'], [])
        self.assertEqual(summary['delivery_categories']['predictions']['delivered'], 1)

    def test_zero_recipients_reports_skipped_categories(self):
        result = broadcast_result(
            outcome=DailyProductionDeliveryOutcome.delivered,
            result=production_result(),
        )

        summary = build_summary(
            result,
            audience_source='database',
            eligible_recipient_count=0,
        )

        self.assertEqual(summary['eligible_recipient_count'], 0)
        self.assertEqual(summary['delivery_success_count'], 0)
        self.assertEqual(
            summary['skipped_categories'],
            [
                {'category': 'qualifying', 'reason': 'no_eligible_recipients'},
                {'category': 'predictions', 'reason': 'no_eligible_recipients'},
            ],
        )


class PredictionDailyRunnerExitTests(unittest.TestCase):
    def test_main_propagates_nonzero_runner_result(self):
        parser = SimpleNamespace()
        with mock.patch(
            'app.prediction_daily_runner.build_parser',
            return_value=SimpleNamespace(parse_args=lambda: parser),
        ), mock.patch(
            'app.prediction_daily_runner.run',
            return_value=1,
        ) as run:
            with self.assertRaises(SystemExit) as context:
                main()
        self.assertEqual(context.exception.code, 1)
        run.assert_called_once_with(parser)


class TelegramUserFacingTextTests(unittest.TestCase):
    def test_bot_and_prediction_messages_are_emoji_free(self):
        emoji_ranges = (
            range(0x1F000, 0x1FB00),
            range(0x2600, 0x2800),
            range(0x2B00, 0x2C00),
        )

        def assert_emoji_free(value):
            if isinstance(value, str):
                self.assertFalse(
                    any(
                        ord(character) in emoji_range
                        for character in value
                        for emoji_range in emoji_ranges
                    ),
                    value,
                )
            elif isinstance(value, dict):
                for child in value.values():
                    assert_emoji_free(child)
            elif isinstance(value, list):
                for child in value:
                    assert_emoji_free(child)

        result = production_result(today_predictions=0, tomorrow_predictions=0)
        formatter = TelegramPredictionFormatter()
        assert_emoji_free(WELCOME_TEXT)
        assert_emoji_free(OPEN_BUTTON_TEXT)
        assert_emoji_free(MENU_BUTTON_TEXT)
        assert_emoji_free(build_commands())
        assert_emoji_free(build_start_message(555, 'https://amen.example.com'))
        assert_emoji_free(formatter.build_qualifying_payload(555, result))
        assert_emoji_free(formatter.build_prediction_payload(555, result))


if __name__ == '__main__':
    unittest.main()
