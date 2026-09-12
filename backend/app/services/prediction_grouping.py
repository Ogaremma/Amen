from __future__ import annotations

from app.schemas.prediction_booking import PredictionBookingSelection


def split_prediction_selections(
    selections: list[PredictionBookingSelection],
) -> list[list[PredictionBookingSelection]]:
    """Split one day's predictions into at most two deterministic groups."""

    ordered = sorted(
        selections,
        key=lambda selection: (
            selection.kickoff_at,
            selection.identity.event_id,
            selection.identity.market_id,
            selection.identity.outcome_id,
            selection.identity.product_id,
            selection.identity.sport_id,
            selection.identity.specifier,
        ),
    )
    if not ordered:
        return []
    if len(ordered) == 1:
        return [ordered]

    first_group: list[PredictionBookingSelection] = []
    second_group: list[PredictionBookingSelection] = []
    for index, selection in enumerate(ordered):
        if index % 2 == 0:
            first_group.append(selection)
        else:
            second_group.append(selection)
    return [first_group, second_group]
