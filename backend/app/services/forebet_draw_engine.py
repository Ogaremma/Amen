from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from datetime import date, datetime

from app.schemas.forebet import FixtureMatchResult, FixtureMatchStatus
from app.schemas.forebet_draw_window import DrawWindowDay, DrawWindowMatch, DrawWindowResponse
from app.config.settings import get_settings
from app.services.fixture_matching import match_forebet_fixtures
from app.services.forebet import fetch_forebet_page, parse_forebet_html
from app.services.forebet_dates import prediction_dates_from_urls
from app.services.forebet_draw_store import ForebetDrawStore, forebet_draw_store
from app.services.sportybet import create_draw_booking, get_upcoming_football_events


class ForebetDrawEngine:
    def __init__(self, store: ForebetDrawStore = forebet_draw_store): self.store = store; self._lock = asyncio.Lock()

    def get_active_window(self) -> DrawWindowResponse:
        days = self.store.list_active()
        return DrawWindowResponse(days=days, active_count=len(days), compilation=self.store.get_compilation())

    @staticmethod
    def _window_match(result: FixtureMatchResult) -> DrawWindowMatch:
        event = result.sportybet_event
        assert event is not None and event.market_id == "1" and event.outcome_draw_id == "2" and event.product_id is not None and event.sport_id
        return DrawWindowMatch(event_id=event.event_id, home_team=event.home_team, away_team=event.away_team, kickoff=event.kickoff, match_status=event.match_status, market_id="1", outcome_id="2", product_id=event.product_id, sport_id=event.sport_id, specifier=event.specifier)

    @staticmethod
    def _identity(matches: list[DrawWindowMatch]):
        return sorted((m.event_id, m.market_id, m.outcome_id, m.product_id, m.sport_id, m.specifier or "") for m in matches)

    @classmethod
    def _identity_hash(cls, matches: list[DrawWindowMatch]) -> str:
        return hashlib.sha256(repr(cls._identity(matches)).encode()).hexdigest()

    @classmethod
    def _paper_code(cls, matches: list[DrawWindowMatch]) -> str:
        return f"PAPER-{cls._identity_hash(matches)[:10].upper()}"

    @staticmethod
    def _paper_enabled(settings) -> bool:
        return getattr(settings, "forebet_draw_paper_booking_enabled", False) is True

    async def refresh_window(self, source_urls: list[str], start_datetime: datetime | None = None, end_datetime: datetime | None = None) -> DrawWindowResponse:
        async with self._lock:
            html_pages = await asyncio.gather(*(fetch_forebet_page(url) for url in source_urls), return_exceptions=True)
            forebet_matches = []
            target_dates = prediction_dates_from_urls(source_urls)
            failed_dates: set[date] = set()
            acquisition_diagnostics: dict[date, list[str]] = defaultdict(list)
            for url, html, day in zip(source_urls, html_pages, target_dates):
                if isinstance(html, Exception):
                    failed_dates.add(day)
                    acquisition_diagnostics[day].append(f"PROVIDER_FAILURE: {type(html).__name__}: {str(html)[:200]}")
                    continue
                self.store.save_raw_snapshot(day, url, html)
                try:
                    forebet_matches.extend(parse_forebet_html(html, url))
                except Exception as exc:
                    failed_dates.add(day)
                    acquisition_diagnostics[day].append(f"PARSER_FAILURE: {type(exc).__name__}: {str(exc)[:200]}")
            settings = get_settings()
            sportybet = await get_upcoming_football_events(start_datetime=start_datetime, end_datetime=end_datetime)
            # Preserve every explicit Forebet DRAW; booking receives all validated matches.
            by_day: dict[date, list] = defaultdict(list)
            for match in forebet_matches:
                if match.predicted_result.value != "DRAW" or match.kickoff is None:
                    continue
                day = match.kickoff.date() if isinstance(match.kickoff, datetime) else match.kickoff
                by_day[day].append(match)
            selected = [match for day in target_dates for match in by_day.get(day, [])]
            results = match_forebet_fixtures(selected, sportybet.events)
            grouped: dict[date, list[FixtureMatchResult]] = defaultdict(list)
            diagnostics: dict[date, list[str]] = defaultdict(list)
            for day, messages in acquisition_diagnostics.items(): diagnostics[day].extend(messages)
            for result in results:
                kickoff = result.forebet_match.kickoff
                if kickoff is None: continue
                day = kickoff.date() if isinstance(kickoff, datetime) else kickoff
                if result.status in {FixtureMatchStatus.MATCHED_EXACT, FixtureMatchStatus.MATCHED_NORMALIZED, FixtureMatchStatus.MATCHED_FUZZY} and result.sportybet_event and result.sportybet_event.market_id == "1" and result.sportybet_event.outcome_draw_id == "2" and result.sportybet_event.product_id is not None and result.sportybet_event.sport_id:
                    grouped[day].append(result)
                else:
                    diagnostics[day].append(f"{result.forebet_match.home_team} vs {result.forebet_match.away_team}: {result.status.value} - {result.reason or 'invalid DRAW booking identity'}")

            current = {day.prediction_date: day for day in self.store.list_active()}
            window_dates = target_dates
            compilation_results: dict[tuple, FixtureMatchResult] = {}
            for day in window_dates:
                if day in failed_dates:
                    continue
                deduped: dict[tuple, FixtureMatchResult] = {}
                for result in grouped[day]:
                    event = result.sportybet_event
                    key = (event.event_id, event.market_id, event.outcome_draw_id, event.product_id, event.sport_id, event.specifier or "")
                    deduped.setdefault(key, result)
                compilation_results.update(deduped)
                matches = [self._window_match(item) for item in deduped.values()]
                existing = current.get(day)
                if existing and self._identity(existing.matches) == self._identity(matches): continue
                if not matches:
                    if settings.forebet_draw_booking_enabled or self._paper_enabled(settings):
                        self.store.promote(day, None, [], source_urls, diagnostics[day], status="unavailable")
                    continue
                try:
                    if settings.forebet_draw_booking_enabled:
                        booking_code = (await create_draw_booking(list(deduped.values()))).booking_code
                    elif self._paper_enabled(settings):
                        booking_code = self._paper_code(matches)
                        diagnostics[day].append("paper booking used; real booking disabled")
                    else:
                        diagnostics[day].append("booking disabled by FOREBET_DRAW_BOOKING_ENABLED")
                        continue
                    self.store.promote(day, booking_code, matches, source_urls, diagnostics[day])
                except Exception as exc:
                    diagnostics[day].append(f"booking unavailable: {type(exc).__name__}: {str(exc)[:200]}")
                    self.store.promote(day, None, matches, source_urls, diagnostics[day], status="error")
            if compilation_results and len(compilation_results) > 50:
                self.store.unavailable_compilation(window_dates, status="overflow", diagnostics=["SportyBet supports at most 50 selections per compilation"])
            elif compilation_results:
                compilation_matches = [self._window_match(result) for result in compilation_results.values()]
                existing_compilation = self.store.get_compilation()
                identity = self._identity_hash(compilation_matches)
                if not existing_compilation or existing_compilation.identity != identity:
                    try:
                        if settings.forebet_draw_booking_enabled:
                            booking_code = (await create_draw_booking(list(compilation_results.values()))).booking_code
                        elif self._paper_enabled(settings):
                            booking_code = self._paper_code(compilation_matches)
                        else:
                            booking_code = None
                        if booking_code:
                            self.store.promote_compilation(booking_code, window_dates, compilation_matches, identity)
                    except Exception as exc:
                        self.store.unavailable_compilation(window_dates, status="error", diagnostics=[f"booking unavailable: {type(exc).__name__}: {str(exc)[:200]}"], matches=compilation_matches, identity=identity)
            elif settings.forebet_draw_booking_enabled or self._paper_enabled(settings):
                self.store.unavailable_compilation(window_dates)
            self.store.complete_not_in(set(window_dates))
            return self.get_active_window()

    async def refresh_day(self, prediction_date: date, source_urls: list[str], start_datetime: datetime | None = None, end_datetime: datetime | None = None) -> DrawWindowResponse:
        result = await self.refresh_window(source_urls, start_datetime, end_datetime)
        return DrawWindowResponse(days=[day for day in result.days if day.prediction_date == prediction_date], active_count=sum(day.prediction_date == prediction_date for day in result.days))


forebet_draw_engine = ForebetDrawEngine()
