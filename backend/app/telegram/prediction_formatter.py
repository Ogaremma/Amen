from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from app.schemas.prediction_booking import (
    PredictionBookingGroup,
    PredictionBookingBatchStatus,
    PredictionBookingDay,
    PredictionBookingDayResult,
    PredictionBookingDayStatus,
    PredictionBookingPool,
    PredictionBookingResult,
    PredictionBookingSelection,
)


TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT = 4000


class TelegramPredictionMessageTooLarge(RuntimeError):
    def __init__(
        self,
        *,
        required_utf16_length: int,
        limit: int,
        qualifying_count: int,
        prediction_count: int,
    ) -> None:
        self.required_utf16_length = required_utf16_length
        self.limit = limit
        self.qualifying_count = qualifying_count
        self.prediction_count = prediction_count
        super().__init__(
            'The prediction message exceeds the Telegram limit without dropping fixtures.'
        )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _escaped(value: str | None) -> str:
    return html.escape(value or '', quote=True)


def _selection_sort_key(
    selection: PredictionBookingSelection,
) -> tuple[datetime, str, str, str, str, int, str, str]:
    identity = selection.identity
    return (
        _utc(selection.kickoff_at),
        identity.event_id,
        identity.market_id,
        identity.outcome_id,
        identity.sport_id,
        identity.product_id,
        identity.specifier,
        selection.home_team,
    )


def _utf16_length(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def _kickoff_text(selection: PredictionBookingSelection) -> str:
    return _utc(selection.kickoff_at).strftime('%H:%M UTC')


def _odds_text(odds: float) -> str:
    return f'{odds:.2f}'


def _qualifying_lines(selections: list[PredictionBookingSelection]) -> list[str]:
    lines: list[str] = []
    for index, selection in enumerate(selections, start=1):
        lines.append(
            f'{index}. {_kickoff_text(selection)} — '
            f'{_escaped(selection.home_team)} vs {_escaped(selection.away_team)}\n'
            f'    Over 1.5 @ {_odds_text(selection.odds)}'
        )
    return lines


def _prediction_lines(
    selection: PredictionBookingSelection,
    index: int,
    *,
    short_reason: bool = False,
) -> list[str]:
    lines = [
        f'{index}. {_kickoff_text(selection)} — '
        f'{_escaped(selection.home_team)} vs {_escaped(selection.away_team)}',
        f'    Over 1.5 @ {_odds_text(selection.odds)}',
    ]
    if selection.evidence_score is not None:
        lines.append(f'    Evidence score: {selection.evidence_score:.2f}')
    if selection.evidence_quality is not None:
        lines.append(f'    Evidence quality: {_escaped(selection.evidence_quality.value)}')

    if short_reason:
        lines.append(f'    Why selected: {_escaped(_short_reason(selection))}')
        return lines

    explanation = selection.explanation
    if explanation is None:
        lines.append('    Why selected: No structured explanation was supplied.')
        return lines

    lines.append('    Why selected:')
    positive = explanation.positive_signals[:3]
    if positive:
        lines.extend(f'    • {_escaped(signal)}' for signal in positive)
    else:
        lines.append('    • No positive signals were supplied.')

    notes = explanation.negative_signals[:2] + explanation.missing_signals[:2]
    if notes:
        lines.append('    Evidence notes:')
        lines.extend(f'    • {_escaped(signal)}' for signal in notes)
    return lines


def _short_reason(selection: PredictionBookingSelection) -> str:
    explanation = selection.explanation
    if explanation is None:
        return 'No structured explanation was supplied.'
    positives = explanation.positive_signals[:2]
    if positives:
        return '; '.join(positives)
    notes = explanation.negative_signals[:1] + explanation.missing_signals[:1]
    if notes:
        return '; '.join(notes)
    return 'No structured selection signals were supplied.'


def _booking_note(day: PredictionBookingDayResult) -> str:
    if not day.selections:
        return ''
    failed_batches = sum(
        1
        for batch in day.batches
        if batch.status == PredictionBookingBatchStatus.failed or batch.error is not None
    )
    successful_count = len(day.booking_codes)
    if successful_count and failed_batches:
        return (
            f'Booking partly unavailable; {successful_count} of '
            f'{successful_count + failed_batches} booking batches succeeded.'
        )
    if successful_count:
        codes = ', '.join(f'<code>{_escaped(code)}</code>' for code in day.booking_codes)
        return f'Booking codes: {codes}'
    return 'Booking unavailable.'


def _group_booking_note(group: PredictionBookingGroup) -> str:
    if not group.selections:
        return ''
    failed_batches = sum(
        1
        for batch in group.batches
        if batch.status == PredictionBookingBatchStatus.failed or batch.error is not None
    )
    successful_count = len(group.booking_codes)
    if successful_count and failed_batches:
        return (
            f'Booking partly unavailable; {successful_count} of '
            f'{successful_count + failed_batches} booking batches succeeded.'
        )
    if successful_count:
        codes = ', '.join(f'<code>{_escaped(code)}</code>' for code in group.booking_codes)
        return f'Booking codes: {codes}'
    return 'Booking unavailable.'


def _format_day(
    day: PredictionBookingDayResult,
    *,
    prediction: bool,
) -> list[str]:
    lines = [f'<b>{day.day.value.upper()}</b>']
    if day.status.value == 'unavailable':
        lines.append('Current SportyBet data is unavailable.')
        return lines

    selections = sorted(day.selections, key=_selection_sort_key)
    if not selections:
        if day.status == PredictionBookingDayStatus.rejected:
            lines.append(
                'Model-selected predictions are not currently bookable after market validation.'
                if prediction
                else 'No current valid qualifying games are available for booking.'
            )
        else:
            lines.append(
                'No model-selected predictions meet the current evidence threshold.'
                if prediction
                else 'No qualifying games currently available.'
            )
        return lines

    if prediction:
        for index, selection in enumerate(selections, start=1):
            lines.extend(_prediction_lines(selection, index))
    else:
        lines.extend(_qualifying_lines(selections))

    booking_note = _booking_note(day)
    if booking_note:
        lines.append(booking_note)
    return lines


def _copy_button(
    *,
    pool: PredictionBookingPool,
    day: PredictionBookingDay,
    index: int,
    booking_code: str,
) -> dict[str, Any]:
    return {
        'text': f'Copy {pool.value} {day.value} {index}',
        'copy_text': {'text': booking_code},
    }


class TelegramPredictionFormatter:
    def format(
        self,
        result: PredictionBookingResult,
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        qualifying_today = result.qualifying.today
        qualifying_tomorrow = result.qualifying.tomorrow
        prediction_today = result.predictions.today
        prediction_tomorrow = result.predictions.tomorrow

        sections: list[str] = []
        if result.status.value == 'unavailable':
            sections.append(
                'Current SportyBet catalogue data is unavailable. '
                'No prediction pools are listed until fresh data is available.'
            )

        sections.extend([
            '<b>OVER 1.40–1.50 GAMES — TODAY &amp; TOMORROW</b>',
            '<i>Market-qualified games; not all are model predictions.</i>',
            '\n'.join(_format_day(qualifying_today, prediction=False)),
            '\n'.join(_format_day(qualifying_tomorrow, prediction=False)),
            '<b>OVER 1.40–1.50 PREDICTIONS — TODAY &amp; TOMORROW</b>',
            '<i>Model-selected by evidence-v1. Scores are ranking scores, not probabilities.</i>',
            '\n'.join(_format_day(prediction_today, prediction=True)),
            '\n'.join(_format_day(prediction_tomorrow, prediction=True)),
        ])
        text = '\n\n'.join(sections)

        length = _utf16_length(text)
        if length > TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT:
            raise TelegramPredictionMessageTooLarge(
                required_utf16_length=length,
                limit=TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT,
                qualifying_count=(
                    qualifying_today.selection_count
                    + qualifying_tomorrow.selection_count
                ),
                prediction_count=(
                    prediction_today.selection_count
                    + prediction_tomorrow.selection_count
                ),
            )

        buttons: list[list[dict[str, Any]]] = []
        for pool_result, pool in (
            (result.qualifying, PredictionBookingPool.qualifying),
            (result.predictions, PredictionBookingPool.predictions),
        ):
            for day_result in (pool_result.today, pool_result.tomorrow):
                for index, booking_code in enumerate(day_result.booking_codes, start=1):
                    buttons.append([
                        _copy_button(
                            pool=pool,
                            day=day_result.day,
                            index=index,
                            booking_code=booking_code,
                        )
                    ])
        return text, buttons

    def build_payload(
        self,
        chat_id: int | str,
        result: PredictionBookingResult,
    ) -> dict[str, Any]:
        text, buttons = self.format(result)
        payload: dict[str, Any] = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
        }
        if buttons:
            payload['reply_markup'] = {'inline_keyboard': buttons}
        return payload

    def format_qualifying(
        self,
        result: PredictionBookingResult,
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        sections: list[str] = []
        if result.status.value == 'unavailable':
            sections.append(
                'Current SportyBet catalogue data is unavailable. '
                'No qualifying games are listed until fresh data is available.'
            )
        sections.extend([
            '<b>OVER 1.40\u20131.50 GAMES \u2014 TODAY &amp; TOMORROW</b>',
            '<i>Market-qualified games; not all are model predictions.</i>',
            '\n'.join(_format_day(result.qualifying.today, prediction=False)),
            '\n'.join(_format_day(result.qualifying.tomorrow, prediction=False)),
        ])
        text = '\n\n'.join(sections)
        length = _utf16_length(text)
        if length > TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT:
            raise TelegramPredictionMessageTooLarge(
                required_utf16_length=length,
                limit=TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT,
                qualifying_count=(
                    result.qualifying.today.selection_count
                    + result.qualifying.tomorrow.selection_count
                ),
                prediction_count=0,
            )

        buttons: list[list[dict[str, Any]]] = []
        for day_result in (result.qualifying.today, result.qualifying.tomorrow):
            for index, booking_code in enumerate(day_result.booking_codes, start=1):
                buttons.append([
                    _copy_button(
                        pool=PredictionBookingPool.qualifying,
                        day=day_result.day,
                        index=index,
                        booking_code=booking_code,
                    )
                ])
        return text, buttons

    def format_predictions(
        self,
        result: PredictionBookingResult,
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        sections: list[str] = []
        if result.status.value == 'unavailable':
            sections.append(
                'Current SportyBet catalogue data is unavailable. '
                'No model-selected predictions are listed until fresh data is available.'
            )
        sections.extend([
            '<b>OVER 1.40\u20131.50 PREDICTIONS \u2014 TODAY &amp; TOMORROW</b>',
            '<i>Model-selected by evidence-v1. Scores are ranking scores, not probabilities.</i>',
        ])

        for day_result in (result.predictions.today, result.predictions.tomorrow):
            day_sections: list[str] = []
            if not day_result.groups:
                day_sections.extend(_format_day(day_result, prediction=True))
            else:
                for group in day_result.groups:
                    group_lines = [
                        f'<b>{day_result.day.value.upper()} \u2014 PREDICTION GROUP {group.group_index}</b>'
                    ]
                    selections = sorted(group.selections, key=_selection_sort_key)
                    for index, selection in enumerate(selections, start=1):
                        group_lines.extend(
                            _prediction_lines(selection, index, short_reason=True)
                        )
                    booking_note = _group_booking_note(group)
                    booking_note = _group_booking_note(group)
                    if booking_note:
                        group_lines.append(booking_note)
                    day_sections.append('\n'.join(group_lines))
            sections.append('\n\n'.join(day_sections))

        text = '\n\n'.join(sections)
        length = _utf16_length(text)
        if length > TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT:
            raise TelegramPredictionMessageTooLarge(
                required_utf16_length=length,
                limit=TELEGRAM_MESSAGE_SAFE_UTF16_LIMIT,
                qualifying_count=0,
                prediction_count=(
                    result.predictions.today.selection_count
                    + result.predictions.tomorrow.selection_count
                ),
            )

        buttons: list[list[dict[str, Any]]] = []
        for day_result in (result.predictions.today, result.predictions.tomorrow):
            for group in day_result.groups:
                for index, booking_code in enumerate(group.booking_codes, start=1):
                    buttons.append([
                        {
                            'text': (
                                f'Copy predictions {day_result.day.value} '
                                f'group {group.group_index} code {index}'
                            ),
                            'copy_text': {'text': booking_code},
                        }
                    ])
        return text, buttons

    def build_qualifying_payload(
        self,
        chat_id: int | str,
        result: PredictionBookingResult,
    ) -> dict[str, Any]:
        text, buttons = self.format_qualifying(result)
        payload: dict[str, Any] = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
        }
        if buttons:
            payload['reply_markup'] = {'inline_keyboard': buttons}
        return payload

    def build_prediction_payload(
        self,
        chat_id: int | str,
        result: PredictionBookingResult,
    ) -> dict[str, Any]:
        text, buttons = self.format_predictions(result)
        payload: dict[str, Any] = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
        }
        if buttons:
            payload['reply_markup'] = {'inline_keyboard': buttons}
        return payload


def build_prediction_message(
    chat_id: int | str,
    result: PredictionBookingResult,
) -> dict[str, Any]:
    return TelegramPredictionFormatter().build_payload(chat_id, result)
