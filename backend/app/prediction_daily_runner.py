from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

import httpx

from app.config.settings import get_settings
from app.schemas.prediction_daily_broadcast import TelegramRecipient
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

    summary = {
        'outcome': result.outcome.value,
        'audience_source': audience_source,
        'timezone': result.timezone_name,
        'today': result.today.isoformat(),
        'tomorrow': result.tomorrow.isoformat(),
        'generation_identity': result.generation_identity,
        'recipient_count': result.recipient_count,
        'delivered_recipient_count': result.delivered_recipient_count,
        'failed_recipient_count': result.failed_recipient_count,
        'undeliverable_recipient_count': result.undeliverable_recipient_count,
        'error_summary': result.error_summary,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if result.outcome.value in {'delivered', 'already_delivered'} else 1


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(build_parser().parse_args()))


if __name__ == '__main__':
    main()
