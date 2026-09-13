from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

import httpx

from app.config.settings import get_settings
from app.schemas.prediction_daily_broadcast import (
    DailyPredictionBroadcastResult,
    PredictionRecipientDeliveryResult,
    TelegramRecipient,
)
from app.schemas.prediction_daily_production import (
    PredictionMessageCategory,
    PredictionMessageDeliveryStatus,
)
from app.services.prediction_booking import PredictionBookingService
from app.services.prediction_daily import SportyBetEvidenceEvaluationProvider
from app.services.prediction_daily_production import DailyPredictionProductionService
from app.services.prediction_production_time import production_prediction_window
from app.services.telegram_user_store import TelegramUserStore
from app.telegram.bot import TelegramBot


logger = logging.getLogger('amen.prediction_daily_runner')


class NullEvidenceProvider:
    """Explicit no-op evidence provider.

    The current Phase 4B provider is an offline sanitized capture provider and
    cannot supply arbitrary live fixtures. This no-op keeps production honest:
    qualifying games can still be delivered, while the prediction message
    reports that no model-selected predictions are available.
    """

    def get_evidence(self, candidate: Any, *, now: Any = None) -> None:
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Run one bounded Africa/Lagos daily prediction delivery.'
    )
    parser.add_argument(
        '--chat-id',
        default=None,
        help=(
            'Optional single-recipient override; defaults to '
            'TELEGRAM_PREDICTION_CHAT_ID, then eligible database users.'
        ),
    )
    parser.add_argument('--page-size', type=int, default=None)
    parser.add_argument('--max-pages', type=int, default=None)
    return parser


def _result_diagnostics(result) -> dict[str, Any]:
    if result is None:
        return {
            'counts_available': False,
            'qualifying_selection_count': None,
            'model_selected_selection_count': None,
            'qualifying_today_batch_count': None,
            'qualifying_tomorrow_batch_count': None,
            'prediction_today_batch_count': None,
            'prediction_tomorrow_batch_count': None,
            'catalogue': {'available': False},
        }

    catalogue = result.catalogue
    return {
        'counts_available': True,
        'qualifying_selection_count': (
            result.qualifying.today.selection_count
            + result.qualifying.tomorrow.selection_count
        ),
        'model_selected_selection_count': (
            result.predictions.today.selection_count
            + result.predictions.tomorrow.selection_count
        ),
        'qualifying_today_batch_count': len(result.qualifying.today.batches),
        'qualifying_tomorrow_batch_count': len(result.qualifying.tomorrow.batches),
        'prediction_today_batch_count': sum(
            len(group.batches) for group in result.predictions.today.groups
        ),
        'prediction_tomorrow_batch_count': sum(
            len(group.batches) for group in result.predictions.tomorrow.groups
        ),
        'catalogue': {
            'available': True,
            'status': result.status.value,
            'retrieved_total': catalogue.retrieved_total,
            'parsed_fixtures': catalogue.parsed_fixtures,
            'pages_fetched': catalogue.pages_fetched,
            'pagination_complete': catalogue.pagination_complete,
            'fresh': catalogue.fresh,
            'authoritative': catalogue.authoritative,
            'error': catalogue.error,
        },
    }


def _delivery_diagnostics(
    recipients: list[PredictionRecipientDeliveryResult],
) -> dict[str, Any]:
    def category_counts(category: PredictionMessageCategory) -> dict[str, int]:
        records = []
        for recipient in recipients:
            record = (
                recipient.qualifying_delivery
                if category == PredictionMessageCategory.qualifying
                else recipient.prediction_delivery
            )
            if record is not None:
                records.append(record)
        return {
            'delivered': sum(
                record.status == PredictionMessageDeliveryStatus.delivered
                for record in records
            ),
            'failed': sum(
                record.status == PredictionMessageDeliveryStatus.failed
                for record in records
            ),
            'undeliverable': sum(
                record.status == PredictionMessageDeliveryStatus.undeliverable
                for record in records
            ),
            'pending': sum(
                record.status == PredictionMessageDeliveryStatus.pending
                for record in records
            ),
        }

    return {
        'qualifying': category_counts(PredictionMessageCategory.qualifying),
        'predictions': category_counts(PredictionMessageCategory.predictions),
    }


def _skipped_categories(
    result: DailyPredictionBroadcastResult,
    eligible_recipient_count: int,
) -> list[dict[str, str | int]]:
    if result.result is None:
        reason = result.outcome.value
        return [
            {'category': category.value, 'reason': reason}
            for category in PredictionMessageCategory
        ]
    if eligible_recipient_count == 0:
        return [
            {'category': category.value, 'reason': 'no_eligible_recipients'}
            for category in PredictionMessageCategory
        ]

    skipped: list[dict[str, str | int]] = []
    undeliverable_qualifying_recipients = sum(
        recipient.qualifying_delivery is not None
        and recipient.qualifying_delivery.status
        == PredictionMessageDeliveryStatus.undeliverable
        for recipient in result.recipients
    )
    if undeliverable_qualifying_recipients:
        skipped.append({
            'category': PredictionMessageCategory.predictions.value,
            'reason': 'recipient_undeliverable',
            'recipient_count': undeliverable_qualifying_recipients,
        })
    return skipped


def build_summary(
    result: DailyPredictionBroadcastResult,
    *,
    audience_source: str,
    eligible_recipient_count: int,
) -> dict[str, Any]:
    return {
        'outcome': result.outcome.value,
        'audience_source': audience_source,
        'eligible_recipient_count': eligible_recipient_count,
        'timezone': result.timezone_name,
        'today': result.today.isoformat(),
        'tomorrow': result.tomorrow.isoformat(),
        'generation_identity': result.generation_identity,
        'recipient_count': result.recipient_count,
        'delivered_recipient_count': result.delivered_recipient_count,
        'failed_recipient_count': result.failed_recipient_count,
        'undeliverable_recipient_count': result.undeliverable_recipient_count,
        'delivery_success_count': result.delivered_recipient_count,
        'delivery_failure_count': (
            result.failed_recipient_count
            + result.undeliverable_recipient_count
        ),
        'delivery_categories': _delivery_diagnostics(result.recipients),
        'skipped_categories': _skipped_categories(
            result,
            eligible_recipient_count,
        ),
        'error_summary': result.error_summary,
        **_result_diagnostics(result.result),
    }


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit('TELEGRAM_BOT_TOKEN is required.')
    if not settings.telegram_webapp_url:
        raise SystemExit('TELEGRAM_WEBAPP_URL is required.')

    override_chat_id = args.chat_id or settings.telegram_prediction_chat_id
    user_store: TelegramUserStore | None = None
    if override_chat_id:
        recipients = [
            TelegramRecipient(
                telegram_user_id=override_chat_id,
                telegram_chat_id=(
                    override_chat_id if isinstance(override_chat_id, int) else None
                ),
            )
        ]
        audience_source = 'override'
    else:
        user_store = TelegramUserStore()
        recipients = [
            TelegramRecipient(
                telegram_user_id=user.telegram_user_id,
                telegram_chat_id=user.telegram_chat_id,
            )
            for user in user_store.list_eligible_users()
        ]
        audience_source = 'database'

    timeout = httpx.Timeout(35.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        bot = TelegramBot(
            settings.telegram_bot_token,
            settings.telegram_webapp_url,
            client=client,
        )
        service = DailyPredictionProductionService(
            evaluation_provider=SportyBetEvidenceEvaluationProvider(
                NullEvidenceProvider(),
                window_builder=production_prediction_window,
            ),
            booking_service=PredictionBookingService(),
            telegram_transport=bot,
            telegram_user_store=user_store,
        )
        result = await service.run_daily_prediction_broadcast(
            recipients,
            page_size=args.page_size,
            max_pages=args.max_pages,
        )

    summary = build_summary(
        result,
        audience_source=audience_source,
        eligible_recipient_count=len(recipients),
    )
    print(json.dumps(summary, sort_keys=True))
    return 0 if result.outcome.value in {'delivered', 'already_delivered'} else 1


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(run(build_parser().parse_args())))


if __name__ == '__main__':
    main()
