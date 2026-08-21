from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException

from app.config.settings import get_settings
from app.schemas.booking import BookingResponse, BookingSelection
from app.schemas.forebet import SportyBetEvent

logger = logging.getLogger("amen.sportybet")

settings = get_settings()

# SportyBet success business code.
_BIZ_CODE_OK = 10000
try:
    _LAGOS = ZoneInfo("Africa/Lagos")
except ZoneInfoNotFoundError:
    # Windows Python installations may not ship the IANA database. Nigeria has
    # no daylight-saving transitions, so this remains an exact aware fallback.
    _LAGOS = timezone(timedelta(hours=1), name="Africa/Lagos")
_FALLBACK_LIVE_WINDOW = timedelta(hours=3)
_FOOTBALL_MARKET_ID = "1"
_HOME_OUTCOME_ID = "1"
_DRAW_OUTCOME_ID = "2"
_AWAY_OUTCOME_ID = "3"


class SportyBetUpcomingEventsResult:
    def __init__(self, total_num: int, events: list[SportyBetEvent]) -> None:
        self.total_num = total_num
        self.events = events


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_upcoming_event(raw: Any) -> SportyBetEvent | None:
    if not isinstance(raw, dict):
        return None
    event_id = raw.get("eventId")
    home = raw.get("homeTeamName")
    away = raw.get("awayTeamName")
    start_ms = raw.get("estimateStartTime")
    if not event_id or not home or not away or start_ms is None:
        return None
    try:
        kickoff = datetime.fromtimestamp(int(start_ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None

    sport = raw.get("sport") if isinstance(raw.get("sport"), dict) else {}
    category = sport.get("category") if isinstance(sport.get("category"), dict) else {}
    tournament = (
        category.get("tournament") if isinstance(category.get("tournament"), dict) else {}
    )
    market = next(
        (
            item
            for item in raw.get("markets", [])
            if isinstance(item, dict) and str(item.get("id")) == _FOOTBALL_MARKET_ID
        ),
        None,
    )
    outcomes = market.get("outcomes", []) if isinstance(market, dict) else []

    def active_outcome(outcome_id: str) -> dict[str, Any] | None:
        for outcome in outcomes:
            if (
                isinstance(outcome, dict)
                and str(outcome.get("id")) == outcome_id
                and outcome.get("isActive") in (1, "1", True)
            ):
                return outcome
        return None

    home_outcome = active_outcome(_HOME_OUTCOME_ID)
    draw_outcome = active_outcome(_DRAW_OUTCOME_ID)
    away_outcome = active_outcome(_AWAY_OUTCOME_ID)
    tournament_name = tournament.get("name")

    return SportyBetEvent(
        event_id=str(event_id),
        game_id=str(raw["gameId"]) if raw.get("gameId") is not None else None,
        home_team_id=raw.get("homeTeamId"),
        home_team_name=str(home),
        away_team_id=raw.get("awayTeamId"),
        away_team_name=str(away),
        home_team=str(home),
        away_team=str(away),
        sport_id=sport.get("id"),
        sport_name=sport.get("name"),
        category_id=category.get("id"),
        category_name=category.get("name"),
        tournament_id=tournament.get("id"),
        tournament_name=tournament_name,
        competition=tournament_name,
        kickoff=kickoff,
        status=_to_int(raw.get("status")),
        match_status=raw.get("matchStatus"),
        market_id=str(market.get("id")) if market else None,
        product_id=_to_int(market.get("product")) if market else None,
        specifier=market.get("specifier") or None if market else None,
        outcome_home_id=str(home_outcome.get("id")) if home_outcome else None,
        outcome_draw_id=str(draw_outcome.get("id")) if draw_outcome else None,
        outcome_away_id=str(away_outcome.get("id")) if away_outcome else None,
        odds_home=_to_float(home_outcome.get("odds")) if home_outcome else None,
        odds_draw=_to_float(draw_outcome.get("odds")) if draw_outcome else None,
        odds_away=_to_float(away_outcome.get("odds")) if away_outcome else None,
        probability_home=_to_float(home_outcome.get("probability")) if home_outcome else None,
        probability_draw=_to_float(draw_outcome.get("probability")) if draw_outcome else None,
        probability_away=_to_float(away_outcome.get("probability")) if away_outcome else None,
    )


def parse_upcoming_events_page(payload: Any) -> SportyBetUpcomingEventsResult:
    if not isinstance(payload, dict) or payload.get("bizCode") != _BIZ_CODE_OK:
        raise HTTPException(status_code=502, detail="Invalid SportyBet upcoming-events response")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("tournaments", []), list):
        raise HTTPException(status_code=502, detail="Invalid SportyBet upcoming-events response")
    events: list[SportyBetEvent] = []
    for tournament in data.get("tournaments", []):
        if not isinstance(tournament, dict):
            continue
        for raw in tournament.get("events", []) or []:
            parsed = _parse_upcoming_event(raw)
            if parsed is not None:
                events.append(parsed)
    return SportyBetUpcomingEventsResult(_to_int(data.get("totalNum")) or 0, events)


def _upcoming_url() -> str:
    return f"{settings.sportybet_base_url.rstrip('/')}/{settings.sportybet_upcoming_path.strip('/')}"


async def _fetch_upcoming_page(page_num: int, page_size: int) -> SportyBetUpcomingEventsResult:
    params = {
        "sportId": settings.sportybet_football_sport_id,
        "marketId": settings.sportybet_upcoming_market_ids,
        "pageSize": page_size,
        "pageNum": page_num,
        "_t": int(time.time() * 1000),
    }
    try:
        async with httpx.AsyncClient(timeout=settings.sportybet_timeout) as client:
            response = await client.get(_upcoming_url(), params=params, headers=_headers())
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="SportyBet request timed out") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach SportyBet") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Unable to fetch upcoming events from SportyBet")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid SportyBet response") from exc
    return parse_upcoming_events_page(payload)


async def get_upcoming_football_events(
    start_datetime: datetime | None = None,
    end_datetime: datetime | None = None,
    page_size: int | None = None,
    max_pages: int | None = None,
) -> SportyBetUpcomingEventsResult:
    size = page_size or settings.sportybet_upcoming_page_size
    limit = max_pages or settings.sportybet_upcoming_max_pages
    if size < 1 or size > 100 or limit < 1:
        raise ValueError("Invalid SportyBet pagination configuration")

    def as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc
        )

    start, end = as_utc(start_datetime), as_utc(end_datetime)
    if start and end and start > end:
        raise ValueError("start_datetime must not be after end_datetime")

    all_events: list[SportyBetEvent] = []
    total_num = 0
    for page in range(1, limit + 1):
        result = await _fetch_upcoming_page(page, size)
        total_num = result.total_num
        all_events.extend(result.events)
        if not result.events or page * size >= total_num:
            break

    filtered = [
        event
        for event in all_events
        if (start is None or event.kickoff >= start) and (end is None or event.kickoff <= end)
    ]
    return SportyBetUpcomingEventsResult(total_num, filtered)


def determine_game_status(
    raw_status: Any, kickoff: datetime, now: datetime | None = None
) -> str:
    """Normalize SportyBet status, with a conservative time fallback."""
    normalized = " ".join(str(raw_status or "").strip().lower().replace("_", " ").split())
    upcoming = {"not start", "not started", "scheduled", "upcoming", "pre match", "prematch"}
    live = {"live", "in progress", "in play", "started", "playing"}
    ended = {"ended", "finished", "complete", "completed", "closed", "cancelled", "canceled"}
    if normalized in upcoming:
        return "upcoming"
    if normalized in live:
        return "live"
    if normalized in ended:
        return "ended"

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if current < kickoff:
        return "upcoming"
    if current < kickoff + _FALLBACK_LIVE_WINDOW:
        return "live"
    return "ended"


def determine_result_status(game_status: str, picked: dict[str, Any]) -> str:
    """Normalize SportyBet settlement metadata for the exact selected outcome.

    SportyBet exposes settlement on the matched market outcome as ``isWinning``
    plus ``refundFactor``. Scores and market descriptions are never evaluated.
    """
    if game_status != "ended":
        return "pending"

    refund_factor = _to_float(picked.get("refundFactor"))
    if refund_factor is not None and math.isfinite(refund_factor) and refund_factor > 0:
        return "void"

    winning = picked.get("isWinning")
    if winning in (1, "1", True):
        return "won"
    if winning in (0, "0", False):
        return "lost"
    return "unknown"


def calculate_remaining_odds(selections: list[BookingSelection]) -> float:
    """Multiply finite, positive odds for upcoming games only."""
    upcoming = [selection for selection in selections if selection.game_status == "upcoming"]
    if not upcoming:
        return 0.0

    result = Decimal("1")
    valid_count = 0
    for selection in upcoming:
        if selection.odds is None:
            continue
        try:
            odds = Decimal(str(selection.odds))
        except (InvalidOperation, ValueError):
            continue
        if not odds.is_finite() or odds <= 0:
            continue
        result *= odds
        valid_count += 1

    if valid_count == 0:
        return 0.0
    converted = float(result)
    return converted if math.isfinite(converted) else 0.0


def _to_float(value: Any) -> float | None:
    """Parse SportyBet's string odds/values into a float, or None if not parseable."""
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _match_market(outcome: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any] | None:
    """Find the market on an outcome that corresponds to the ticket selection.

    Matches on market id, and additionally on specifier when the selection
    carries one (an event can expose several markets with the same id but
    different specifiers, e.g. total=8.5 vs total=9.5).
    """
    market_id = str(selection.get("marketId"))
    selection_specifier = selection.get("specifier")

    id_matches = [m for m in outcome.get("markets", []) if str(m.get("id")) == market_id]
    if not id_matches:
        return None

    if selection_specifier:
        for market in id_matches:
            if str(market.get("specifier")) == str(selection_specifier):
                return market
        return None

    # An absent specifier is only safe when SportyBet returned exactly one
    # candidate (or exactly one specifier-less candidate). Never guess by
    # taking the first market when several candidates remain.
    no_specifier = [market for market in id_matches if not market.get("specifier")]
    if len(no_specifier) == 1:
        return no_specifier[0]
    if len(id_matches) == 1:
        return id_matches[0]
    logger.warning("Ambiguous market %s for event %s without specifier", market_id, selection.get("eventId"))
    return None


def _match_outcome(market: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any] | None:
    """Find the specific selected outcome within a market by outcome id."""
    outcome_id = str(selection.get("outcomeId"))
    for candidate in market.get("outcomes", []):
        if str(candidate.get("id")) == outcome_id:
            return candidate
    return None


def _build_selection(
    selection: dict[str, Any], event: dict[str, Any], now: datetime | None = None
) -> BookingSelection | None:
    """Resolve one ticket selection against its event outcome into a clean model.

    Returns None (and logs the reason) when the selection cannot be resolved,
    so a single malformed selection never breaks the whole booking.
    """
    event_id = selection.get("eventId")

    market = _match_market(event, selection)
    if market is None:
        logger.warning("No market %s found for event %s", selection.get("marketId"), event_id)
        return None

    picked = _match_outcome(market, selection)
    if picked is None:
        logger.warning(
            "No outcome %s found in market %s for event %s",
            selection.get("outcomeId"),
            selection.get("marketId"),
            event_id,
        )
        return None

    start_ms = event.get("estimateStartTime")
    if not start_ms:
        logger.warning("Missing estimateStartTime for event %s; skipping", event_id)
        return None
    try:
        kickoff = datetime.fromtimestamp(int(start_ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        logger.warning("Invalid estimateStartTime %r for event %s; skipping", start_ms, event_id)
        return None

    sport = event.get("sport", {}) or {}
    category = sport.get("category", {}) or {}
    tournament = category.get("tournament", {}) or {}
    specifier = selection.get("specifier") or None
    local_kickoff = kickoff.astimezone(_LAGOS)
    raw_status = event.get("matchStatus")
    game_status = determine_game_status(raw_status, kickoff, now)

    return BookingSelection(
        id=str(event_id),
        event_id=str(event_id),
        market_id=str(selection.get("marketId")),
        outcome_id=str(selection.get("outcomeId")),
        home=event.get("homeTeamName") or "Unknown",
        away=event.get("awayTeamName") or "Unknown",
        competition=tournament.get("name") or "Unknown competition",
        category=category.get("name") or "Unknown",
        kickoff=kickoff,
        kickoff_date=kickoff.strftime("%Y-%m-%d"),
        kickoff_time=kickoff.strftime("%H:%M"),
        local_kickoff_date=local_kickoff.strftime("%Y-%m-%d"),
        local_kickoff_time=local_kickoff.strftime("%H:%M"),
        market=market.get("desc") or market.get("name") or "Unknown market",
        outcome=picked.get("desc") or "Unknown outcome",
        odds=_to_float(picked.get("odds")),
        specifier=specifier,
        status=raw_status,
        game_status=game_status,
        result_status=determine_result_status(game_status, picked),
    )


def parse_booking(
    booking_code: str, payload: dict[str, Any], now: datetime | None = None
) -> BookingResponse:
    """Turn a raw SportyBet share response into a clean, sorted BookingResponse.

    Pure function (no I/O) so it can be unit-tested with mocked payloads.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Unexpected SportyBet response")

    if payload.get("isAvailable") is False or payload.get("bizCode") not in (None, _BIZ_CODE_OK):
        raise HTTPException(status_code=404, detail="Booking code not found or unavailable")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=404, detail="Booking code not found or unavailable")

    ticket = data.get("ticket") or {}
    ticket_selections = ticket.get("selections") or []
    outcomes = data.get("outcomes") or []

    if not outcomes:
        logger.warning("Booking %s returned no outcomes", booking_code)

    outcomes_by_event: dict[str, dict[str, Any]] = {
        o.get("eventId"): o for o in outcomes if isinstance(o, dict) and o.get("eventId")
    }

    resolved: list[BookingSelection] = []
    for selection in ticket_selections:
        if not isinstance(selection, dict):
            continue
        event = outcomes_by_event.get(selection.get("eventId"))
        if event is None:
            logger.warning("No outcome found for selection event %s", selection.get("eventId"))
            continue
        built = _build_selection(selection, event, now)
        if built is not None:
            resolved.append(built)

    # Sort by the COMPLETE kickoff datetime: year -> month -> day -> hour -> minute.
    resolved.sort(key=lambda s: s.kickoff)

    return BookingResponse(
        booking_code=data.get("shareCode") or booking_code,
        total_selections=len(resolved),
        total_odds=_to_float(ticket.get("displayTotalOdds")),
        remaining_odds=calculate_remaining_odds(resolved),
        selections=resolved,
    )


async def get_booking(booking_code: str) -> BookingResponse:
    """Fetch a booking from SportyBet's public share endpoint and parse it."""
    code = (booking_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Booking code is required")

    payload = await _fetch_share(code)
    return parse_booking(code, payload)


def _share_base_url() -> str:
    base = settings.sportybet_base_url.rstrip("/")
    path = settings.sportybet_share_path.strip("/")
    return f"{base}/{path}"


def _headers() -> dict[str, str]:
    base = settings.sportybet_base_url.rstrip("/")
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": settings.sportybet_user_agent,
        "Origin": base,
        "Referer": f"{base}/",
    }


async def _fetch_share(code: str) -> dict[str, Any]:
    """GET the SportyBet share endpoint for a code and return raw JSON.

    Raises HTTPException with an appropriate status for network / HTTP / JSON
    failures. This is the single choke point used by both retrieval and the
    post-rebook re-fetch.
    """
    url = f"{_share_base_url()}/{code}"
    params = {"_t": int(time.time() * 1000)}

    try:
        async with httpx.AsyncClient(timeout=settings.sportybet_timeout) as client:
            response = await client.get(url, params=params, headers=_headers())
    except httpx.TimeoutException as exc:
        logger.warning("SportyBet request timed out for %s", code)
        raise HTTPException(status_code=504, detail="SportyBet request timed out") from exc
    except httpx.RequestError as exc:
        logger.warning("SportyBet request failed for %s: %s", code, exc)
        raise HTTPException(status_code=502, detail="Unable to reach SportyBet") from exc

    if response.status_code != 200:
        logger.warning("SportyBet returned HTTP %s for %s", response.status_code, code)
        raise HTTPException(status_code=502, detail="Unable to fetch booking from SportyBet")

    try:
        return response.json()
    except ValueError as exc:
        logger.warning("SportyBet returned non-JSON for %s", code)
        raise HTTPException(status_code=502, detail="Invalid SportyBet response") from exc


def _selection_identity(selection: dict[str, Any]) -> dict[str, Any]:
    """Extract the exact SportyBet identity fields needed to re-book a selection.

    Preserves eventId, marketId, outcomeId, productId, sportId, and specifier
    (only when present). Never reconstructs a selection from team names.
    """
    identity: dict[str, Any] = {
        "eventId": selection.get("eventId"),
        "marketId": selection.get("marketId"),
        "outcomeId": selection.get("outcomeId"),
        "productId": selection.get("productId"),
        "sportId": selection.get("sportId"),
    }
    specifier = selection.get("specifier")
    if specifier:
        identity["specifier"] = specifier
    return identity


async def _create_share_code(selections: list[dict[str, Any]]) -> str:
    """POST remaining selections to SportyBet and return the new share code.

    Discovered endpoint: POST /api/ng/orders/share with body {"selections": [...]}.
    SportyBet returns bizCode 10000 + data.shareCode on success, 19000 when the
    selection list is empty, and 19999 for an otherwise-invalid selection set.
    """
    if not selections:
        raise HTTPException(status_code=400, detail="No selections remaining to rebook")

    try:
        async with httpx.AsyncClient(timeout=settings.sportybet_timeout) as client:
            response = await client.post(
                _share_base_url(), headers=_headers(), json={"selections": selections}
            )
    except httpx.TimeoutException as exc:
        logger.warning("SportyBet rebook timed out")
        raise HTTPException(status_code=504, detail="SportyBet request timed out") from exc
    except httpx.RequestError as exc:
        logger.warning("SportyBet rebook request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Unable to reach SportyBet") from exc

    if response.status_code != 200:
        logger.warning("SportyBet rebook returned HTTP %s", response.status_code)
        raise HTTPException(status_code=502, detail="Unable to rebook with SportyBet")

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("SportyBet rebook returned non-JSON")
        raise HTTPException(status_code=502, detail="Invalid SportyBet response") from exc

    biz_code = payload.get("bizCode")
    share_code = (payload.get("data") or {}).get("shareCode")
    if biz_code != _BIZ_CODE_OK or not share_code:
        logger.warning(
            "SportyBet rebook rejected: bizCode=%s message=%r",
            biz_code,
            payload.get("message"),
        )
        raise HTTPException(
            status_code=502, detail="SportyBet could not generate the new booking"
        )

    return share_code


async def rebook_without_events(booking_code: str, event_ids: list[str]) -> BookingResponse:
    """Remove one OR MANY selections (by event_id) in a SINGLE rebooking.

    Flow (backend is the source of truth):
      1. Fetch the authoritative current booking from SportyBet.
      2. Drop every named event from its raw ticket selections.
      3. POST the remaining selections ONCE to create a new share code.
      4. Re-fetch and parse that new code with the existing resolver.

    Exactly one SportyBet rebooking request is issued regardless of how many
    events are removed. The current booking is never mutated on failure — any
    error raises before a new BookingResponse is produced (atomic replacement).
    """
    code = (booking_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Booking code is required")

    # Normalise: strip, drop blanks, de-duplicate while preserving order.
    targets: list[str] = []
    seen_targets: set[str] = set()
    for raw in event_ids or []:
        cleaned = (raw or "").strip()
        if cleaned and cleaned not in seen_targets:
            seen_targets.add(cleaned)
            targets.append(cleaned)

    if not targets:
        raise HTTPException(status_code=400, detail="No selections specified for removal")

    # 1. Authoritative current ticket straight from SportyBet.
    payload = await _fetch_share(code)
    if payload.get("isAvailable") is False or payload.get("bizCode") not in (None, _BIZ_CODE_OK):
        raise HTTPException(status_code=404, detail="Booking code not found or unavailable")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=404, detail="Booking code not found or unavailable")

    raw_selections = (data.get("ticket") or {}).get("selections") or []

    # 2. Split the ticket into kept vs removed, validating every requested id.
    present_event_ids: set[str] = set()
    remaining: list[dict[str, Any]] = []
    for selection in raw_selections:
        if not isinstance(selection, dict):
            continue
        event_id = selection.get("eventId")
        if event_id is not None:
            present_event_ids.add(event_id)
        if event_id in seen_targets:
            continue  # drop this one
        remaining.append(_selection_identity(selection))

    missing = [t for t in targets if t not in present_event_ids]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Selection(s) not found in booking: {', '.join(missing)}",
        )

    if not remaining:
        raise HTTPException(
            status_code=400, detail="You cannot remove every game from a booking."
        )

    # 3. Ask SportyBet to generate the new ticket + code (ONE request).
    new_code = await _create_share_code(remaining)

    # 4. Re-fetch and parse with the existing resolver so the new ticket is as
    #    fully detailed (and identically sorted) as the original.
    return await get_booking(new_code)


async def rebook_without_event(booking_code: str, event_id: str) -> BookingResponse:
    """Remove a single selection. Thin wrapper over the batch path.

    Kept for internal reuse and existing unit coverage; a single removal is
    simply a batch of one.
    """
    target = (event_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="event_id is required")
    return await rebook_without_events(booking_code, [target])
