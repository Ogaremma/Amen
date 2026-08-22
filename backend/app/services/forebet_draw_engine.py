from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime

from app.schemas.forebet import FixtureMatchResult, FixtureMatchStatus
from app.schemas.forebet_draw_window import DrawWindowDay, DrawWindowMatch, DrawWindowResponse
from app.config.settings import get_settings
from app.services.fixture_matching import match_forebet_fixtures
from app.services.forebet import fetch_forebet_page, parse_forebet_html
from app.services.forebet_draw_store import ForebetDrawStore, forebet_draw_store
from app.services.sportybet import create_draw_booking, get_upcoming_football_events


class ForebetDrawEngine:
    def __init__(self, store: ForebetDrawStore = forebet_draw_store): self.store = store; self._lock = asyncio.Lock()

    def get_active_window(self) -> DrawWindowResponse:
        days = self.store.list_active()
        return DrawWindowResponse(days=days, active_count=len(days))

    @staticmethod
    def _window_match(result: FixtureMatchResult) -> DrawWindowMatch:
        event = result.sportybet_event
        assert event is not None and event.market_id == "1" and event.outcome_draw_id == "2" and event.product_id is not None and event.sport_id
        return DrawWindowMatch(event_id=event.event_id, home_team=event.home_team, away_team=event.away_team, kickoff=event.kickoff, match_status=event.match_status, market_id="1", outcome_id="2", product_id=event.product_id, sport_id=event.sport_id, specifier=event.specifier)

    @staticmethod
    def _identity(matches: list[DrawWindowMatch]):
        return sorted((m.event_id, m.market_id, m.outcome_id, m.product_id, m.sport_id, m.specifier or "") for m in matches)

    async def refresh_window(self, source_urls: list[str], start_datetime: datetime | None = None, end_datetime: datetime | None = None) -> DrawWindowResponse:
        async with self._lock:
            html_pages = await asyncio.gather(*(fetch_forebet_page(url) for url in source_urls))
            forebet_matches = []
            for url, html in zip(source_urls, html_pages): forebet_matches.extend(parse_forebet_html(html, url))
            sportybet = await get_upcoming_football_events(start_datetime=start_datetime, end_datetime=end_datetime)
            # Preserve every explicit Forebet DRAW; booking receives all validated matches.
            by_day: dict[date, list] = defaultdict(list)
            for match in forebet_matches:
                if match.predicted_result.value != "DRAW" or match.kickoff is None:
                    continue
                day = match.kickoff.date() if isinstance(match.kickoff, datetime) else match.kickoff
                by_day[day].append(match)
            selected = [match for day in sorted(by_day) for match in by_day[day]]
            results = match_forebet_fixtures(selected, sportybet.events)
            grouped: dict[date, list[FixtureMatchResult]] = defaultdict(list)
            diagnostics: dict[date, list[str]] = defaultdict(list)
            for result in results:
                kickoff = result.forebet_match.kickoff
                if kickoff is None: continue
                day = kickoff.date() if isinstance(kickoff, datetime) else kickoff
                if result.status in {FixtureMatchStatus.MATCHED_EXACT, FixtureMatchStatus.MATCHED_NORMALIZED, FixtureMatchStatus.MATCHED_FUZZY} and result.sportybet_event and result.sportybet_event.market_id == "1" and result.sportybet_event.outcome_draw_id == "2" and result.sportybet_event.product_id is not None and result.sportybet_event.sport_id:
                    grouped[day].append(result)
                else:
                    diagnostics[day].append(f"{result.forebet_match.home_team} vs {result.forebet_match.away_team}: {result.status.value} - {result.reason or 'invalid DRAW booking identity'}")

            current = {day.prediction_date: day for day in self.store.list_active()}
            usable_dates = sorted(day for day, items in grouped.items() if items)[:3]
            for day in usable_dates:
                deduped: dict[str, FixtureMatchResult] = {}
                for result in grouped[day]: deduped.setdefault(result.sportybet_event.event_id, result)
                matches = [self._window_match(item) for item in deduped.values()]
                existing = current.get(day)
                if existing and self._identity(existing.matches) == self._identity(matches): continue
                if not get_settings().forebet_draw_booking_enabled:
                    diagnostics[day].append("booking disabled by FOREBET_DRAW_BOOKING_ENABLED")
                    continue
                try:
                    booking = await create_draw_booking(list(deduped.values()))
                    self.store.promote(day, booking.booking_code, matches, source_urls, diagnostics[day])
                except Exception as exc:
                    diagnostics[day].append(f"booking unavailable: {type(exc).__name__}: {str(exc)[:200]}")
            self.store.complete_not_in(set(usable_dates))
            return self.get_active_window()

    async def refresh_day(self, prediction_date: date, source_urls: list[str], start_datetime: datetime | None = None, end_datetime: datetime | None = None) -> DrawWindowResponse:
        result = await self.refresh_window(source_urls, start_datetime, end_datetime)
        return DrawWindowResponse(days=[day for day in result.days if day.prediction_date == prediction_date], active_count=sum(day.prediction_date == prediction_date for day in result.days))


forebet_draw_engine = ForebetDrawEngine()
