from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from datetime import date, datetime

from app.schemas.forebet import FixtureMatchResult, FixtureMatchStatus
from app.schemas.forebet_draw_window import DrawWindowDay, DrawWindowMatch, DrawWindowResponse
from app.config.settings import get_settings
from app.services.fixture_matching import match_forebet_fixtures
from app.services.forebet import fetch_forebet_page, parse_forebet_html
from app.services.forebet_dates import future_prediction_dates, future_prediction_urls, prediction_dates_from_urls
from app.services.forebet_draw_store import ForebetDrawStore, forebet_draw_store
from app.services.sportybet import create_draw_booking, get_upcoming_football_events
from app.services.sportybet import determine_game_status

logger = logging.getLogger("amen.forebet_draw_engine")


class ForebetDrawEngine:
    def __init__(self, store: ForebetDrawStore = forebet_draw_store): self.store = store; self._lock = asyncio.Lock()

    def get_active_window(self, *, now: datetime | None = None, target_dates: list[date] | None = None) -> DrawWindowResponse:
        target_dates = target_dates or future_prediction_dates(now=now)
        days = self.store.list_active(target_dates)
        found = {day.prediction_date for day in days}
        timestamp = datetime.now().astimezone()
        for prediction_date in target_dates:
            if prediction_date not in found:
                state = self.store.get_acquisition_state(prediction_date)
                days.append(DrawWindowDay(prediction_date=prediction_date, booking_code=None, selection_count=0, status="error", matches=[], diagnostics=[state.get("error_reason") or "acquisition pending"], diagnostic_code="ACQUISITION_PENDING", diagnostic_message=state.get("error_reason") or "Acquisition pending", created_at=timestamp, last_updated=timestamp, acquisition=state))
        days.sort(key=lambda item: item.prediction_date)
        states = {day.prediction_date.isoformat(): day.acquisition for day in days}
        return DrawWindowResponse(days=days, active_count=sum(day.status == "active" for day in days), compilation=self.store.get_compilation(), target_dates=target_dates, acquisition={"days": states})

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

    @staticmethod
    def _real_booking_enabled(settings) -> bool:
        authorization = getattr(settings, "forebet_real_booking_authorized", True)
        authorized = authorization if isinstance(authorization, bool) else True
        return settings.forebet_draw_booking_enabled is True and authorized is True

    async def _paper_batches(self, prediction_date, results, settings):
        matches = [self._window_match(r) for r in results]
        prior = {b["identity"]: b for b in self.store.list_daily_batches(prediction_date)}
        batches = []
        for index in range(0, len(results), 50):
            chunk_results = results[index:index + 50]
            chunk_matches = [self._window_match(r) for r in chunk_results]
            identity = self._identity_hash(chunk_matches)
            old = prior.get(identity)
            code = old["booking_code"] if old else self._paper_code(chunk_matches)
            batches.append({"batch_index": index // 50 + 1, "booking_code": code, "identity": identity, "matches": [m.model_dump(mode="json") for m in chunk_matches], "status": "active"})
        self.store.replace_daily_batches(prediction_date, batches)
        return batches

    async def _book_batches(self, *, scope: str, prediction_date: date | None, results: list, settings) -> list[dict]:
        prior_batches = self.store.list_daily_batches(prediction_date) if scope == "daily" else self.store.list_compilation_batches()
        prior = {batch["batch_index"]: batch for batch in prior_batches}
        batches = []
        for offset in range(0, len(results), 50):
            chunk_results = results[offset:offset + 50]
            chunk_matches = [self._window_match(result) for result in chunk_results]
            batch_index = offset // 50 + 1
            identity = self._identity_hash(chunk_matches)
            old = prior.get(batch_index)
            # Reuse represents a previously successful booking attempt, not
            # merely a matching selection identity. Error batches must retry.
            reusable = old and old["identity"] == identity and old.get("booking_code") and old.get("status") == "active"
            if self._real_booking_enabled(settings) and reusable and str(old["booking_code"]).startswith("PAPER-"):
                reusable = False
            if reusable:
                batches.append(old)
                continue
            if self._real_booking_enabled(settings):
                try:
                    code = (await create_draw_booking(chunk_results)).booking_code
                    batches.append({"batch_index": batch_index, "booking_code": code, "identity": identity, "matches": [m.model_dump(mode="json") for m in chunk_matches], "status": "active"})
                except Exception as exc:
                    logger.exception("real_booking_failed scope=%s prediction_date=%s batch_index=%s identity=%s", scope, prediction_date, batch_index, identity)
                    self.store.log_rebook_event(prediction_date=prediction_date, scope=scope, batch_index=batch_index, removed=[], reasons=[f"real_booking_failed:{type(exc).__name__}:{str(exc)[:200]}"], old_code=old.get("booking_code") if old else None, new_code=old.get("booking_code") if old else None, old_identity=old.get("identity") if old else None, new_identity=identity)
                    batches.append(old or {"batch_index": batch_index, "booking_code": None, "identity": identity, "matches": [m.model_dump(mode="json") for m in chunk_matches], "status": "error"})
            else:
                code = old["booking_code"] if old and old["identity"] == identity else self._paper_code(chunk_matches)
                batches.append({"batch_index": batch_index, "booking_code": code, "identity": identity, "matches": [m.model_dump(mode="json") for m in chunk_matches], "status": "active"})
        expected = {batch["batch_index"]: batch["identity"] for batch in prior_batches}
        if scope == "daily": self.store.replace_daily_batches(prediction_date, batches, expected or None)
        else: self.store.replace_compilation_batches(batches, expected or None)
        return batches

    async def prune_kickoff_passed(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now().astimezone(); changed = 0
        for day in self.store.list_active():
            batches = self.store.list_daily_batches(day.prediction_date)
            for batch in batches:
                active = [m for m in batch["matches"] if datetime.fromisoformat(m["kickoff"]) > now]
                if len(active) == len(batch["matches"]): continue
                identity = hashlib.sha256(repr(sorted((m["event_id"], m["market_id"], m["outcome_id"], m["product_id"], m["sport_id"], m.get("specifier") or "") for m in active)).encode()).hexdigest()
                replacement = dict(batch, identity=identity, booking_code=self._paper_code([DrawWindowMatch.model_validate(m) for m in active]) if active else None, matches=active, status="active" if active else "unavailable")
                if self.store.update_daily_batch_if_identity(day.prediction_date, batch["batch_index"], batch["identity"], replacement):
                    self.store.log_rebook_event(prediction_date=day.prediction_date, scope="daily", batch_index=batch["batch_index"], removed=[m.get("event_id") for m in batch["matches"] if m not in active], reasons=["presumed_live_by_kickoff"], old_code=batch.get("booking_code"), new_code=replacement.get("booking_code"), old_identity=batch["identity"], new_identity=identity)
                    changed += 1
        if changed: self._rebuild_paper_compilation_batches()
        return changed

    def _rebuild_paper_compilation_batches(self) -> bool:
        current = self.store.list_compilation_batches(); expected = {b["batch_index"]: b["identity"] for b in current}
        matches = [DrawWindowMatch.model_validate(m) for day in self.store.list_active() for batch in self.store.list_daily_batches(day.prediction_date) for m in batch["matches"]]
        batches = []
        for index in range(0, len(matches), 50):
            chunk = matches[index:index + 50]; identity = self._identity_hash(chunk); old = next((b for b in current if b["identity"] == identity), None)
            batches.append({"batch_index": index // 50 + 1, "booking_code": old["booking_code"] if old else self._paper_code(chunk), "identity": identity, "matches": [m.model_dump(mode="json") for m in chunk], "status": "active"})
        return self.store.replace_compilation_batches(batches, expected or None)

    async def reconcile_statuses(self, events: list, *, now: datetime | None = None) -> int:
        now = now or datetime.now().astimezone(); by_id = {e.event_id: e for e in events}; changed = 0
        terminal_raw = {"ended", "finished", "complete", "completed", "closed", "cancelled", "canceled"}
        forced_terminal = "not_found_timeout_forced_terminal"
        timeout_hours = getattr(get_settings(), "forebet_draw_missing_event_timeout_hours", 6.0)
        if not isinstance(timeout_hours, (int, float)): timeout_hours = 6.0
        for day in self.store.list_active():
            baseline = self.store.get_daily_baseline_matches(day.prediction_date)
            originals = {m.event_id: m for m in baseline}; statuses = {}; missing = []
            for event_id, match in originals.items():
                event = by_id.get(event_id)
                if event is None:
                    if match.kickoff + __import__("datetime").timedelta(hours=timeout_hours) <= now:
                        statuses[event_id] = forced_terminal
                    else:
                        missing.append(event_id); statuses[event_id] = "not_found_in_reconciliation"
                else:
                    raw = " ".join((event.match_status or "").strip().lower().replace("_", " ").split())
                    statuses[event_id] = raw if raw in terminal_raw else determine_game_status(event.match_status, event.kickoff, now)
            active_ids = {event_id for event_id, status in statuses.items() if status == "upcoming"}
            for batch in self.store.list_daily_batches(day.prediction_date):
                original_chunk = [m for m in baseline if ((self._identity(baseline).index((m.event_id,m.market_id,m.outcome_id,m.product_id,m.sport_id,m.specifier or "")) // 50)+1) == batch["batch_index"]]
                desired = [m for m in original_chunk if m.event_id in active_ids]
                new_identity = self._identity_hash(desired); new_code = self._paper_code(desired) if desired else None
                if new_identity == batch["identity"]: continue
                old_ids = {m["event_id"] for m in batch["matches"]}; new_ids = {m.event_id for m in desired}
                removed = sorted(old_ids-new_ids); restored = sorted(new_ids-old_ids)
                item = dict(batch, identity=new_identity, booking_code=new_code, matches=[m.model_dump(mode="json") for m in desired], status="active" if desired else "unavailable")
                if self.store.update_daily_batch_if_identity(day.prediction_date, batch["batch_index"], batch["identity"], item):
                    reasons = [statuses.get(event_id, "restored_upcoming") for event_id in removed] + ["restored_upcoming" for _ in restored]
                    self.store.log_rebook_event(prediction_date=day.prediction_date, scope="daily", batch_index=batch["batch_index"], removed=removed, reasons=reasons, old_code=batch.get("booking_code"), new_code=new_code, old_identity=batch["identity"], new_identity=new_identity); changed += 1
            exhausted = bool(originals) and not missing and all(status in terminal_raw or status == forced_terminal for status in statuses.values())
            self.store.update_day_monitoring(day.prediction_date, status="complete" if exhausted else day.status, monitoring={"statuses": statuses, "missing_event_ids": missing, "exhausted": exhausted, "last_reconciled_at": now.isoformat()})
        if changed: self._rebuild_paper_compilation_batches()
        return changed

    async def refresh_rolling(self, *, now: datetime | None = None) -> DrawWindowResponse:
        return await self.refresh_window(future_prediction_urls(now=now), now=now)

    async def refresh_trusted(self, source_urls: list[str], forebet_matches: list, sportybet_events: list) -> DrawWindowResponse:
        """Promote already-acquired trusted snapshots through the paper workflow.

        This path never acquires providers and never calls a booking endpoint.
        """
        target_dates = prediction_dates_from_urls(source_urls)
        by_day: dict[date, list] = defaultdict(list)
        for match in forebet_matches:
            if match.predicted_result.value != "DRAW" or match.kickoff is None:
                continue
            day = match.kickoff.date() if isinstance(match.kickoff, datetime) else match.kickoff
            if day in target_dates:
                by_day[day].append(match)
        selected = [m for day in target_dates for m in by_day.get(day, [])]
        results = match_forebet_fixtures(selected, sportybet_events)
        grouped: dict[date, list[FixtureMatchResult]] = defaultdict(list)
        diagnostics: dict[date, list[str]] = defaultdict(list)
        for result in results:
            kickoff = result.forebet_match.kickoff
            if kickoff is None:
                continue
            day = kickoff.date() if isinstance(kickoff, datetime) else kickoff
            event = result.sportybet_event
            if result.status in {FixtureMatchStatus.MATCHED_EXACT, FixtureMatchStatus.MATCHED_NORMALIZED, FixtureMatchStatus.MATCHED_FUZZY} and event and event.market_id == "1" and event.outcome_draw_id == "2" and event.product_id is not None and event.sport_id:
                grouped[day].append(result)
            else:
                diagnostics[day].append(f"{result.forebet_match.home_team} vs {result.forebet_match.away_team}: {result.status.value} - {result.reason or 'invalid DRAW booking identity'}")
        compilation: dict[tuple, FixtureMatchResult] = {}
        current = {day.prediction_date: day for day in self.store.list_active()}
        for day in target_dates:
            deduped = {}
            for result in grouped.get(day, []):
                event = result.sportybet_event
                key = (event.event_id, event.market_id, event.outcome_draw_id, event.product_id, event.sport_id, event.specifier or "")
                deduped.setdefault(key, result)
            compilation.update(deduped)
            matches = [self._window_match(r) for r in deduped.values()]
            existing = current.get(day)
            if existing and self._identity(existing.matches) == self._identity(matches):
                continue
            if matches:
                diagnostics[day].append("paper booking used; real booking disabled")
                self.store.promote(day, self._paper_code(matches), source_urls=source_urls, matches=matches, diagnostics=diagnostics[day], status="active")
            else:
                self.store.promote(day, None, [], source_urls, diagnostics[day] or ["no valid DRAW selections"], status="unavailable")
        if len(compilation) > 50:
            self.store.unavailable_compilation(target_dates, status="overflow", diagnostics=["SportyBet supports at most 50 selections per compilation"])
        elif compilation:
            matches = [self._window_match(r) for r in compilation.values()]
            identity = self._identity_hash(matches)
            existing = self.store.get_compilation()
            if not existing or existing.identity != identity:
                self.store.promote_compilation(self._paper_code(matches), target_dates, matches, identity)
        else:
            self.store.unavailable_compilation(target_dates, status="unavailable", diagnostics=["no valid DRAW selections"])
        self.store.complete_not_in(set(target_dates))
        return self.get_active_window()

    async def refresh_window(self, source_urls: list[str], start_datetime: datetime | None = None, end_datetime: datetime | None = None, *, now: datetime | None = None) -> DrawWindowResponse:
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
            ttl = getattr(settings, "sportybet_acquisition_ttl_seconds", 300.0)
            if not isinstance(ttl, (int, float)): ttl = 300.0
            provider_diagnostics = sportybet.diagnostics(ttl, now=now)
            self.store.save_sportybet_snapshot(__import__("json").dumps(provider_diagnostics, sort_keys=True), sportybet.retrieved_at, source="sportybet-live-acquisition")
            if not provider_diagnostics["authoritative"]:
                reason = "SPORTYBET_INCOMPLETE" if not sportybet.complete else "SPORTYBET_STALE"
                message = f"{reason}: expected={sportybet.total_num} retrieved={sportybet.retrieved_num} pages={sportybet.pages_fetched}"
                for day in target_dates:
                    self.store.record_acquisition(day, "error", provider_diagnostics, error_reason=message)
                    self.store.promote(day, None, [], source_urls, [message], status="error")
                self.store.unavailable_compilation(target_dates, status="error", diagnostics=[message])
                self.store.complete_not_in(set(target_dates))
                return self.get_active_window(now=now, target_dates=target_dates)
            # A real-booking refresh performs its own identity-aware replacement
            # below.  Running the local paper reconciliation first would replace
            # the last known-good real code before the provider call succeeds.
            if not self._real_booking_enabled(settings):
                await self.reconcile_statuses(sportybet.events, now=now)
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
                    reason = diagnostics[day][0] if diagnostics[day] else "Forebet acquisition failed"
                    self.store.record_acquisition(day, "error", provider_diagnostics, error_reason=reason)
                    self.store.promote(day, None, [], source_urls, diagnostics[day], status="error")
                    continue
                deduped: dict[tuple, FixtureMatchResult] = {}
                for result in grouped[day]:
                    event = result.sportybet_event
                    key = (event.event_id, event.market_id, event.outcome_draw_id, event.product_id, event.sport_id, event.specifier or "")
                    deduped.setdefault(key, result)
                compilation_results.update(deduped)
                matches = [self._window_match(item) for item in deduped.values()]
                existing = current.get(day)
                if existing and self._identity(existing.matches) == self._identity(matches):
                    self.store.record_acquisition(day, "success", provider_diagnostics)
                    continue
                if not matches:
                    if settings.forebet_draw_booking_enabled or self._paper_enabled(settings):
                        self.store.promote(day, None, [], source_urls, diagnostics[day], status="unavailable")
                    self.store.record_acquisition(day, "success", provider_diagnostics)
                    continue
                try:
                    if self._real_booking_enabled(settings) or self._paper_enabled(settings):
                        batches = await self._book_batches(scope="daily", prediction_date=day, results=list(deduped.values()), settings=settings)
                        booking_code = batches[0].get("booking_code") if batches else None
                        if self._paper_enabled(settings) and not self._real_booking_enabled(settings):
                            diagnostics[day].append("paper booking used; real booking disabled")
                    else:
                        diagnostics[day].append("booking disabled by FOREBET_DRAW_BOOKING_ENABLED")
                        continue
                    self.store.promote(day, booking_code, matches, source_urls, diagnostics[day])
                    self.store.record_acquisition(day, "success", provider_diagnostics)
                except Exception as exc:
                    diagnostics[day].append(f"booking unavailable: {type(exc).__name__}: {str(exc)[:200]}")
                    self.store.promote(day, None, matches, source_urls, diagnostics[day], status="error")
                    self.store.record_acquisition(day, "error", provider_diagnostics, error_reason=diagnostics[day][-1])
            if compilation_results:
                compilation_matches = [self._window_match(result) for result in compilation_results.values()]
                existing_compilation = self.store.get_compilation()
                identity = self._identity_hash(compilation_matches)
                if not existing_compilation or existing_compilation.identity != identity:
                    try:
                        if self._real_booking_enabled(settings) or self._paper_enabled(settings):
                            batches = await self._book_batches(scope="compilation", prediction_date=None, results=list(compilation_results.values()), settings=settings)
                            booking_code = batches[0].get("booking_code") if batches else None
                        else:
                            booking_code = None
                        if booking_code:
                            self.store.promote_compilation(booking_code, window_dates, compilation_matches, identity)
                    except Exception as exc:
                        self.store.unavailable_compilation(window_dates, status="error", diagnostics=[f"booking unavailable: {type(exc).__name__}: {str(exc)[:200]}"], matches=compilation_matches, identity=identity)
            elif settings.forebet_draw_booking_enabled or self._paper_enabled(settings):
                self.store.unavailable_compilation(window_dates)
            self.store.complete_not_in(set(window_dates))
            return self.get_active_window(now=now, target_dates=target_dates)

    async def refresh_day(self, prediction_date: date, source_urls: list[str], start_datetime: datetime | None = None, end_datetime: datetime | None = None) -> DrawWindowResponse:
        result = await self.refresh_window(source_urls, start_datetime, end_datetime)
        return DrawWindowResponse(days=[day for day in result.days if day.prediction_date == prediction_date], active_count=sum(day.prediction_date == prediction_date for day in result.days))


forebet_draw_engine = ForebetDrawEngine()
