# SportyBet Prediction Implementation Handoff

Date: 2026-09-10

This document is the read-only architecture handoff for a future **SportyBet-only Over 1.5 prediction system**. No source code, migration, dependency, environment value, schema, or production behavior was changed while producing it.

## 1. Repository architecture

### Backend

- `backend/app/main.py` is the FastAPI entrypoint. It installs CORS, mounts booking, Telegram, history, and Forebet routers, exposes `/health` and `/readiness`, and starts/stops the Forebet worker in its lifespan.
- API routers are flat modules under `backend/app/api/`:
  - `bookings.py`: booking fetch and batch remove/rebook.
  - `telegram.py`: Mini App initData authentication.
  - `history.py`: per-Telegram-user booking history.
  - `forebet.py`: Forebet analysis, trusted ingestion, draw matching, draw booking, and rolling draw-window endpoints.
- Pydantic contracts are under `backend/app/schemas/`. The main SportyBet booking contracts are in `booking.py`; the current upcoming-event model is `SportyBetEvent` in `forebet.py`.
- Business services are flat modules under `backend/app/services/`. The key SportyBet integration is `sportybet.py`; Forebet has separate parser, matching, engine, store, worker, ingestion, diagnostics, and date modules.
- The Telegram bot is under `backend/app/telegram/bot.py`, with auth/session/identity services under `backend/app/services/`.
- `openapi.json` contains the current route inventory, although the source routers are authoritative.

### Frontend

- `frontend/src/App.tsx` is the Mini App shell. It keeps Optimizer and Analyzer mounted behind hidden containers and switches workspaces without unmounting loaded state.
- `WorkspaceNavigation.tsx` exposes the current Optimizer and Analyzer workspaces.
- `DashboardPage.tsx` is both the normal Optimizer and the Splitter host. It loads a code, tracks selected event IDs, performs batch removal, and creates split cards.
- `GameSelectionList.tsx` is the shared chronological game display. The Splitter intentionally uses the same component as the normal Optimizer.
- `frontend/src/lib/api.ts` is the API client. `fetchBookingByCode` and `removeSelectedGames` are the Optimizer/Splitter transport.
- `frontend/src/lib/gameSelection.ts` sorts selections by full kickoff timestamp.
- `frontend/src/lib/splitter.ts` implements First (`top`), Middle (`middle`), and Last (`bottom`) selection.
- `useTelegramWebApp.ts` initializes Telegram WebApp, sends raw signed initData to the backend, and exposes the backend-verified user.

### Infrastructure

- `backend/Dockerfile` builds Python 3.13, installs `requirements.txt`, installs Playwright Chromium, and runs `uvicorn app.main:app`.
- `render.yaml` defines one Docker web service for the backend, uses `/readiness`, and explicitly keeps Forebet real booking safety flags false.
- `frontend/package.json` provides Vite, Vitest, Testing Library, TypeScript build, and lint scripts.
- `vercel.json` only disables automatic job cancellation; there is no project-specific Vercel build configuration.
- `docker/` contains no files.
- There is no `.github/` CI workflow.

## 2. Existing SportyBet integration

All current SportyBet transport and normalization is concentrated in `backend/app/services/sportybet.py`.

### Upcoming catalogue

- `SportyBetUpcomingEventsResult` stores events, expected/retrieved counts, page count, retrieval time, completeness, and freshness diagnostics.
- `_parse_upcoming_event(raw)` parses event identity, teams, kickoff, sport/category/tournament, and **only the 1X2 market with id `1`** and outcomes `1`, `2`, and `3`.
- `parse_upcoming_events_page(payload)` validates `bizCode`, walks `data.tournaments[].events[]`, and returns the normalized result.
- `_upcoming_url()` builds `GET /api/ng/factsCenter/pcUpcomingEvents`.
- `_fetch_upcoming_page(page_num, page_size)` performs up to three attempts with exponential backoff and parses a successful JSON page.
- `_request_upcoming_page(page_num, page_size)` sends configured sport, market IDs, page size, page number, cache-busting timestamp, headers, and timeout.
- `get_upcoming_football_events(...)` paginates until the expected total is covered, an empty page is returned, or the configured maximum is reached; it then filters by UTC kickoff range.

**Important limitation:** although the request is configured with market IDs including `18`, the current `SportyBetEvent` schema and `_parse_upcoming_event` do not preserve Over/Under market `18`. Calling the current `get_upcoming_football_events()` therefore does **not** yield an Over 1.5 candidate. A future market-preserving read model is required.

### Booking share retrieval and parsing

- `get_booking(booking_code)` trims the code, calls `_fetch_share`, and delegates to `parse_booking`.
- `_fetch_share(code)` performs `GET /api/ng/orders/share/{code}` with JSON and browser-like headers.
- `parse_booking(booking_code, payload, now)` is a pure parser. It validates the SportyBet business response, maps ticket selections to event outcomes by `eventId`, resolves the market with `_match_market`, resolves the exact outcome with `_match_outcome`, normalizes kickoff and Africa/Lagos display fields, resolves descriptions, odds, status, result status, and specifier, and sorts by complete kickoff datetime.
- `_match_market` requires a market ID match and, when supplied, an exact specifier match. If no specifier is present, it refuses to guess among multiple candidates.
- `_match_outcome` requires the exact outcome ID.
- `determine_game_status` normalizes pre-match/live/ended states and uses a conservative time fallback.
- `determine_result_status` uses SportyBet settlement metadata rather than deriving results from scores.
- `calculate_remaining_odds` multiplies finite positive odds for upcoming selections only.

### Booking identity and share-code creation

- `_selection_identity(selection)` extracts the exact SportyBet identity: `eventId`, `marketId`, `outcomeId`, `productId`, `sportId`, and optional non-empty `specifier`.
- `_create_share_code(selections)` posts `{"selections": [...]}` to `POST /api/ng/orders/share`, requires `bizCode=10000` and `data.shareCode`, and returns the new code.
- `_headers()` supplies shared JSON, user-agent, origin, and referer headers.
- Settings in `backend/app/config/settings.py` control base URL, share path, upcoming path, football sport ID, market IDs, pagination, timeout, freshness TTL, and user agent.
- `_draw_selection(event)` builds and validates the 1X2 DRAW identity.
- `create_draw_booking(fixtures)` validates matched Forebet fixtures, builds DRAW identities, calls `_create_share_code`, and returns a draw booking response. It must not be reused for Over 1.5.

## 3. Existing booking pipeline

The future prediction-to-booking flow should reuse these exact components:

1. Construct a dict with the same six-field identity shape as `_selection_identity`; for Over 1.5, `specifier` is required when the provider supplies `total=1.5`.
2. Reuse the SportyBet POST behavior in `_create_share_code`, preferably through a future thin generic wrapper rather than a second HTTP implementation.
3. Re-fetch and validate a generated code with `get_booking(new_code)` and `parse_booking`.
4. Use `rebook_without_events(booking_code, event_ids)` for existing bookings. It fetches authoritatively, validates requested IDs, preserves exact identities, performs exactly one POST, re-fetches the new ticket, and leaves the original unchanged on failure.
5. Use `rebook_without_event` only as the single-event wrapper over the batch path.
6. Keep using `frontend/src/lib/api.ts` for existing frontend booking operations; it never builds SportyBet payloads.

The existing endpoint is `POST /api/v1/bookings/{booking_code}/remove-selected` in `backend/app/api/bookings.py`.

## 4. Existing database architecture

### General pattern

- The backend uses SQLAlchemy 2.x Core `Table` definitions, not declarative ORM models.
- Each store owns its `MetaData` and engine: `HistoryStore` and `ForebetDrawStore`.
- Both use `metadata.create_all(engine)` on first use.
- Both normalize `postgres://`/`postgresql://` URLs to `postgresql+psycopg2://`.
- SQLite uses `NullPool`; production engines use `pool_pre_ping=True`.
- Writes use `engine.begin()`; reads use `engine.connect()`.
- Process-level singletons are `history_store` and `forebet_draw_store`.
- There is no Alembic directory, migration script, or migration configuration.
- `ForebetDrawStore._ensure()` contains guarded ad hoc `ALTER TABLE` compatibility statements.

### History tables

`HistoryStore` defines:

- `booking_history`: integer autoincrement ID, Telegram user BigInteger, uppercase booking code, UTC loaded timestamp, selection count, remaining odds, and unique user/code constraint.
- `selection_odds_snapshots`: booking code, event ID, market ID, outcome ID, specifier, first observed odds, observation timestamp, and observation status. The unique identity includes specifier.

Conventions are integer autoincrement IDs, UTC timezone-aware timestamps, string external IDs and booking codes, constrained string statuses, and JSON-like payloads in `Text`. History is capped at 50 records per user.

### Forebet tables

`ForebetDrawStore` defines daily bookings, prebooking candidates, compilation, revisions, daily batches, compilation batches, rebook events, Forebet raw snapshots, SportyBet fixture snapshots, acquisition state, and job locks. A booking identity is a SHA-256 hash over the sorted six-field selection identity; batches are one-based and capped by the engine at 50; raw snapshots retain content hashes; and job locks use a lock name, owner ID, expiry, and update timestamp.

### Test database pattern

Backend tests instantiate stores against temporary SQLite files or directories and restart stores against the same path to test persistence. There is no shared test database or transactional fixture.

## 5. Existing worker/scheduler architecture

### What exists

- `ForebetDrawRefreshWorker` in `backend/app/services/forebet_draw_worker.py` runs two asyncio tasks:
  - rolling draw refresh, default every 900 seconds with jitter and provider cooldown;
  - prune loop, default every 60 seconds.
- The worker starts only when `FOREBET_WORKER_ENABLED=true`, from FastAPI's lifespan in `backend/app/main.py`.
- It uses database lease locks in `forebet_job_locks` to avoid duplicate work across processes.
- `ForebetDrawEngine.refresh_rolling()` obtains tomorrow through day+3 in Africa/Lagos, fetches Forebet, matches to SportyBet, deduplicates by identity, and stores daily and compilation results.
- Forebet bookings are chunked into batches of at most 50 selections in `ForebetDrawEngine._book_batches`.
- The worker has failure diagnostics, cooldown, pruning, and reconciliation.

### What does not exist

- There is no generic scheduler framework.
- There is no Celery, RQ, APScheduler, cron configuration, or Redis-backed task queue. Redis is present in `requirements.txt` but unused by application code.
- There is no separate worker process definition in `render.yaml`.
- There is no SportyBet prediction worker.

A future prediction worker should follow the existing asyncio task, database lease lock, and FastAPI lifespan pattern, but use separate prediction lock names and settings. It must not reuse or alter Forebet lock names, safety flags, or worker state.

## 6. Existing Telegram architecture

### Mini App authentication

- `backend/app/api/telegram.py` exposes `POST /api/v1/telegram/auth`.
- `verify_init_data` in `backend/app/services/telegram_auth.py` validates Telegram's HMAC-SHA256 signed initData, enforces age, and returns a `TelegramUser`.
- `SessionStore` is an async, lock-guarded, process-local in-memory session map. Its interface was intentionally made async so it can later be replaced by Redis or a database.
- `telegram_identity.py` provides FastAPI dependencies:
  - `verified_telegram_user` requires the `X-Telegram-Init-Data` header.
  - `optional_verified_telegram_user` allows unauthenticated browser use while still verifying supplied data.
- `frontend/src/hooks/useTelegramWebApp.ts` calls `tg.ready()`, `tg.expand()`, forwards raw initData to the backend, and only trusts the backend-verified identity.
- `frontend/src/lib/api.ts` attaches `X-Telegram-Init-Data` to booking and history requests.

### Bot

- `backend/app/telegram/bot.py` implements a raw `httpx` long-polling bot, not a webhook.
- Pure builders are separated from I/O: `build_start_message`, `build_menu_button`, and `build_commands`.
- `TelegramBot.handle_update()` currently handles only `/start`.
- `TelegramBot.run()` configures the menu, long-polls `getUpdates`, tracks offsets, retries transient errors, and redacts the bot token from logs.
- `TelegramBot.main()` requires both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBAPP_URL`.
- The bot is not started by the backend Docker command and is not defined as a Render service in `render.yaml`.

For future prediction delivery, a separate injectable Telegram message sender is safer than coupling the prediction worker to the long-polling bot. It can follow the same token-redaction and pure-payload conventions. Delivery requires an explicit, authorized chat or channel target; none is currently configured.

## 7. Existing tests

### Backend

- Tests live in `backend/tests/` and use `unittest.TestCase`, `unittest.IsolatedAsyncioTestCase`, and FastAPI's `TestClient`.
- Tests are network-independent and mock:
  - SportyBet HTTP through fake `httpx.AsyncClient` classes or `unittest.mock.patch`;
  - page fetch functions for pagination tests;
  - Telegram responses with fake clients;
  - store and engine dependencies where needed.
- Database tests use temporary SQLite files.
- The most relevant files for future work are:
  - `test_sportybet_upcoming_unittest.py`: upcoming parser, pagination, and window behavior.
  - `test_bookings_unittest.py`: booking parsing, specifier matching, share creation, atomic rebooking, identity preservation, route behavior, and mocked HTTP failures.
  - `test_history_unittest.py`: temporary SQLite persistence and odds snapshots.
  - `test_forebet_draw_engine_unittest.py`, `test_forebet_draw_batching_unittest.py`, and related Forebet worker/store tests: rolling windows, batching, locks, pruning, reconciliation, and paper/real booking gates.
  - `test_telegram_unittest.py`: signed initData, auth endpoint, bot payloads, long-poll handlers, token redaction, and session behavior.
- `backend/tests/fixtures/sportybet_real_response_sample.json` is a real response sample, but the relevant assertion currently focuses on 1X2. It does not cover Over 1.5 extraction.

### Frontend

- Vitest with jsdom and Testing Library is configured in `frontend/vite.config.ts`.
- API tests stub global `fetch` with `vi.stubGlobal`.
- Component tests mock `../lib/api` with `vi.mock`.
- `DashboardPage.test.tsx` covers loading, chronological display, selection, batch removal, history, Splitter generation, shared game display, split deletion, and failure handling.
- `splitter.test.ts` covers First, Middle, and Last behavior with pure data.
- `AnalyzerPage.test.tsx` covers the existing Forebet analyzer display.

No test command was run during this read-only audit. Future implementation should start with focused new tests and then run the relevant existing backend and frontend suites.

## 8. Existing prediction/Forebet architecture

The current prediction/analyzer pipeline is Forebet-specific and must remain untouched.

- `backend/app/services/forebet.py` parses Forebet HTML into `ForebetMatch`, identifies DRAW predictions, fetches pages with retries and an optional Playwright browser fallback, and handles Cloudflare/access-denied failures.
- `backend/app/services/fixture_matching.py` normalizes team and competition names and matches Forebet fixtures to SportyBet events through exact, normalized, advanced, and fuzzy methods.
- `backend/app/services/forebet_dates.py` derives tomorrow through day+3 in Africa/Lagos and Forebet date URLs.
- `backend/app/services/forebet_draw_engine.py` orchestrates rolling windows, matching, deduplication, paper/real booking gates, 50-selection batches, persistence, reconciliation, and pruning.
- `backend/app/services/forebet_draw_store.py` owns persistence and job locks.
- `backend/app/services/forebet_draw_worker.py` schedules refresh and prune loops.
- `backend/app/services/forebet_ingestion.py` processes trusted external snapshots.
- `backend/app/api/forebet.py` exposes analysis, matching, trusted ingestion, draw booking, window, diagnostics, and refresh routes.

Safety controls that must remain unchanged:

- `FOREBET_DRAW_BOOKING_ENABLED=false`
- `FOREBET_REAL_BOOKING_AUTHORIZED=false`
- `FOREBET_DRAW_PAPER_BOOKING_ENABLED=true`

These settings are false, false, and true by default in source and in the Render declaration. The new SportyBet-only system must not repurpose or silently change these Forebet flags.

## 9. H2H/statistics findings

### VERIFIED

The following is verified from checked-in SportyBet catalogue snapshots, not inferred:

- The SportyBet catalogue payload contains an Over/Under market with:
  - `marketId = "18"`
  - `specifier = "total=1.5"`
  - `productId = 3`
  - Over outcome `id = "12"`
  - Over outcome description `Over 1.5`
  - Under outcome `id = "13"`
  - string `odds` and `probability`
  - `isActive`
  - `lastOddsChangeTime`
  - `sourceType = "BET_RADAR"`
- The enclosing event supplies the actual `eventId`, kickoff, teams, competition metadata, and `sportId`.
- The exact booking identity shape is `eventId`, `marketId="18"`, `outcomeId="12"`, `productId=3`, `sportId="sr:sport:1"`, and `specifier="total=1.5"`.
- A read-only scan of `snapshots/sportybet-page-1.json` through `sportybet-page-10.json` found 873 Over 1.5 outcomes, of which 84 were in the target range `1.40 <= odds < 1.50` at snapshot time.
- The existing booking pipeline correctly preserves optional `specifier` and all other identity fields when rebooking.

These market values are verified only for the checked-in SportyBet responses. They are time-sensitive and must still be validated against current responses. The future extractor should match ID, specifier, and label rather than trusting IDs alone.

### NOT YET VERIFIED

The repository contains **no H2H client, request builder, endpoint constant, parser, schema, test, or captured H2H payload**.

The following are not implemented or verified in this repository:

- any H2H endpoint path, query, or body;
- any H2H authentication, cookies, or headers;
- any Sportradar statistics endpoint;
- `stats_season_meta` as a callable endpoint;
- `lmt` requests;
- historical H2H match list, scores, dates, teams, competitions, or pagination;
- `headtohead=true` as anything other than externally observed metadata;
- `formtable`, `fixtures`, `overunder`, and `insights` data retrieval;
- recent-form provider;
- home/away performance provider;
- H2H sample counts or historical Over 1.5 rates;
- H2H freshness, caching, rate limits, or authorization requirements.

A repository-wide search found no occurrence of `sportradar`, `lmt`, `formtable`, `overunder`, `insights`, `match_info`, `stats_season_meta`, `72221252`, or `140756`. The terms `h2h`, `headtohead`, and `head_to_head` occur only in existing reconnaissance documentation. Therefore **H2H is not implemented anywhere in Amen**, and no endpoint may be invented or guessed.

The existing documents `docs/SPORTYBET_H2H_RECONNAISSANCE.md` and `docs/SPORTYBET_RECONNAISSANCE.md` also record that a live H2H request was not captured and that the exact H2H data contract remains unknown.

## 10. Recommended prediction architecture

This recommendation follows the repository's existing flat-service, Pydantic-schema, SQLAlchemy-Core, asyncio-worker, and mocked-test conventions. It is staged so the first phases require no H2H endpoint, database write, scheduler, booking side effect, or Telegram delivery.

### New schemas

Create a separate SportyBet prediction schema module, for example `backend/app/schemas/sportybet_prediction.py`.

Suggested read models:

- `SportyBetMarketOutcome`: market/outcome IDs, specifier, label, product ID, odds, probability, active state, odds-change time, and provider source.
- `SportyBetFixtureWithMarkets`: event identity, kickoff, status, teams, competition metadata, sport ID, and retained markets.
- `OverOneHalfCandidate`: fixture, exact booking identity, odds, captured probability, extraction diagnostics, and provenance.
- `PredictionFeatureSet`: feature values, sample sizes, missing-data flags, and provenance.
- `PredictionScore`: confidence, probability estimate, feature contributions, model version, and diagnostics.
- `PredictionDay` and `PredictionBatch`: three-day grouping and future booking grouping.

Do **not** extend the existing `SportyBetEvent` with Over 1.5 fields in the first change. It is consumed by Forebet matching, snapshots, tests, and booking code. A separate market-preserving model is safer.

### Fixture ingestion

- Reuse the existing configured base URL, upcoming path, sport ID, market ID list, page size, maximum pages, timeout, headers, and retry behavior.
- Add a market-preserving catalogue parser that retains the raw event and relevant markets instead of selecting only 1X2.
- The cleanest integration is to make the existing upcoming result optionally carry raw page payloads or a second normalized market view while keeping the existing `events` behavior unchanged. This must be covered by regression tests before runtime integration.
- Record page diagnostics: expected total, retrieved total, parsed events, pages, completeness, retrieval time, and freshness.

### Market extraction

- Filter to football sport ID, pre-match status, Africa/Lagos target date, active market/outcome, market ID `18`, specifier `total=1.5`, outcome ID `12`, label `Over 1.5`, finite positive odds, and `1.40 <= odds < 1.50`.
- Require IDs and semantic labels/specifier to match. If an expected field is missing or ambiguous, skip the candidate and record diagnostics rather than guessing.
- Deduplicate by the exact six-field booking identity.
- Preserve raw extraction evidence for audit.

### Statistics provider

Create an interface first, but do not implement a network client until the legitimate request is manually captured:

- A method such as `get_fixture_statistics(event_id)`.
- A normalized statistics read model or `None`.
- Provenance, retrieval time, coverage flags, and raw payload reference.
- Explicit unsupported/unauthorized errors rather than synthesized values.

### H2H provider interface

- Define a narrow interface such as `get_head_to_head(event_id)`.
- Do not provide a concrete SportyBet/Sportradar HTTP implementation until the exact endpoint, method, parameters, authentication, response, pagination, and freshness are captured and verified.
- If captured H2H records contain scores, derive goals, Over 1.5 results, BTTS, and averages in a separate pure feature layer.

### Form provider

- Do not assume form is available.
- Initially model form as optional and missing.
- If verified H2H or history responses provide prior matches, derive recent form from those records.
- Add a separate form endpoint/client only after a legitimate capture proves it exists.

### Feature calculation and scoring

- Keep feature calculation pure: no HTTP, database, booking, or Telegram calls.
- Initial features can include SportyBet implied probability, provider probability, market freshness, historical Over 1.5 rate, H2H average goals and sample count, and recent form/home/away metrics, but only the latter groups when verified data exists.
- Always expose sample size and missing-data indicators.
- Keep scoring deterministic and versioned. Return feature contributions so each selection is explainable.
- Do not use Forebet data or predictions in this new system.

### Candidate selector

- Apply minimum sample and confidence thresholds only after the feature contract is defined.
- Group by Africa/Lagos date for tomorrow, day+2, and day+3.
- Deduplicate by exact booking identity.
- Sort transparently by score and tie-breakers.
- Enforce a maximum of 50 selections per future booking batch.

### Prediction persistence

- Add a separate prediction store using the existing SQLAlchemy Core conventions.
- Suggested tables include raw catalogue snapshots, optional statistics/H2H raw snapshots, candidates with exact identity and feature JSON, daily prediction results, prediction batches and booking codes, and worker/acquisition state and locks.
- Use UTC timestamps, string external IDs, constrained string statuses, integer autoincrement IDs, and JSON in `Text`, matching current stores.
- Use separate prediction lock names and a separate store singleton.
- Do not create these tables during this reconnaissance phase.

### Rolling scheduler

- Model a new worker on `ForebetDrawRefreshWorker`, but do not modify that worker.
- Use an asyncio task started in FastAPI lifespan, a separate conservatively defaulted enabled setting, a separate database lease lock, interval and jitter settings, explicit failure/cooldown diagnostics, and identity-based idempotent refresh.
- The three-day date logic should preserve the existing Africa/Lagos semantics without changing `forebet_dates.py`.

### SportyBet booking integration

- The prediction system should eventually call one generic SportyBet share-code creation boundary that delegates to the existing `_create_share_code` behavior.
- Do not duplicate HTTP client, headers, URL construction, response validation, or identity extraction.
- Do not reuse `_draw_selection`; it is DRAW-specific.
- Construct Over 1.5 identities directly from the market-preserving candidate model.
- Validate each generated code with `get_booking(new_code)`.
- Chunk at 50.
- Add separate explicit paper/real booking controls for the new system; do not use or alter the Forebet flags.

### Telegram delivery

- Add a later, separately tested message sender using the existing backend-only bot token conventions.
- Keep payload construction pure and testable.
- Redact the token from all errors and logs.
- Require explicit authorized chat or channel IDs before sending.
- Do not couple scheduler execution to successful Telegram delivery; persist prediction results first and report delivery failures separately.

### Optional API/UI

- If the Mini App needs a prediction view later, add a separate router and frontend workspace.
- The initial product direction only requires Telegram delivery and manually loaded booking codes, so API/UI is not a prerequisite.

## 11. Files that should not be modified during prediction development

The following are stable, manually tested, or safety-critical. They should remain unchanged unless a later prompt explicitly authorizes a narrowly scoped, regression-tested integration.

### Optimizer and Splitter

- `frontend/src/components/DashboardPage.tsx`
- `frontend/src/components/GameSelectionList.tsx`
- `frontend/src/lib/gameSelection.ts`
- `frontend/src/lib/splitter.ts`
- `frontend/src/components/DashboardPage.test.tsx`
- `frontend/src/lib/splitter.test.ts`
- `frontend/src/lib/api.ts` booking functions

### Booking backend

- `backend/app/api/bookings.py`
- `backend/app/services/sportybet.py` booking and identity functions
- `backend/app/schemas/booking.py`
- `backend/tests/test_bookings_unittest.py`

Any future change in `sportybet.py` should be additive and focused on market-preserving catalogue parsing. In particular, do not change `_selection_identity`, `_create_share_code`, `_fetch_share`, `parse_booking`, `get_booking`, or `rebook_without_events` semantics without explicit authorization and regression tests.

### Forebet pipeline

- `backend/app/api/forebet.py`
- `backend/app/services/forebet.py`
- `backend/app/services/forebet_dates.py`
- `backend/app/services/fixture_matching.py`
- `backend/app/services/forebet_draw_engine.py`
- `backend/app/services/forebet_draw_store.py`
- `backend/app/services/forebet_draw_worker.py`
- `backend/app/services/forebet_draw_diagnostics.py`
- `backend/app/services/forebet_ingestion.py`
- all `backend/tests/test_forebet*.py` files

### Safety and deployment

- `backend/app/config/settings.py` Forebet safety flags
- `backend/.env.example` Forebet safety flags
- `render.yaml`
- `backend/requirements.txt`
- `frontend/package.json` and `frontend/package-lock.json`
- `backend/Dockerfile`

## 12. Implementation risks

1. **Current catalogue model drops market 18.** Naively consuming `get_upcoming_football_events()` will not produce Over 1.5 candidates even though the raw response contains them.
2. **Extending `SportyBetEvent` can break Forebet.** The existing model, matcher, snapshots, ingestion route, and tests assume its current 1X2-oriented shape.
3. **Changing `_fetch_upcoming_page` return behavior can break callers.** Any raw-payload addition must be backward-compatible.
4. **Duplicate SportyBet clients can diverge.** A second implementation of URL, headers, retries, pagination, or share POST would be a maintenance and correctness risk.
5. **Booking creation has external side effects.** Every real share POST can generate a SportyBet code; avoid accidental booking calls in parser, feature, or scheduler tests.
6. **Odds are volatile.** Candidate odds and identities must be captured together and revalidated before booking.
7. **The 50-selection limit is not optional.** Existing Forebet batching enforces it; the new system must enforce it independently.
8. **Duplicate identities can create invalid bookings.** Deduplicate by the full six-field identity, including specifier.
9. **H2H remains unknown.** Calling an invented endpoint could fail, leak identity information, or violate provider expectations. No H2H network client should be implemented before a legitimate capture.
10. **External metadata is not endpoint proof.** `headtohead=true`, season IDs, or coverage flags do not establish a request contract.
11. **Scheduler duplication can multiply provider traffic.** A new worker needs its own distributed lock and must not reuse Forebet lock names.
12. **Render currently runs one web process.** Telegram long polling and prediction scheduling need deliberate process and deployment design; the current bot is not part of the Render service definition.
13. **Database evolution is manual.** There is no Alembic migration framework; new tables must follow the existing create-all pattern deliberately or introduce migrations as a separate, explicitly approved project change.
14. **Checked-in snapshots are stale evidence.** They are excellent parser fixtures but cannot represent current odds, market availability, or fixture counts.
15. **No CI exists.** Tests must be run explicitly for backend and frontend changes.

## 13. Exact recommended implementation sequence

### Phase 0 — Safety baseline

- Make no source change.
- Preserve the read-only guarantee.
- Confirm the current backend and frontend test commands that will be used before implementation.

### Phase 1 — Pure market-preserving parser

- Add a new market-preserving Pydantic read model.
- Add a pure parser for the existing upcoming-events JSON shape.
- Add tests based on checked-in snapshots, covering Over 1.5 identity extraction, target odds range, inactive outcomes, missing market/outcome, multiple market 18 lines, duplicate event identity, and malformed events.
- No network, database, scheduler, booking, Telegram, or Forebet change.

### Phase 2 — Catalogue integration

- Integrate the parser with the existing SportyBet catalogue transport while preserving current `SportyBetEvent` behavior.
- Reuse existing settings, headers, retries, pagination, and diagnostics.
- Add an extraction function that filters active Over 1.5 candidates in the odds range.
- Continue to avoid persistence and side effects.

### Phase 3 — Manual H2H/statistics capture

- Outside code, capture the actual legitimate SportyBet/Sportradar H2H and statistics request and response from a normal user session.
- Sanitize secrets and personal data.
- Record method, full path, query/body, status, relevant non-secret headers, response envelope, pagination, and timing.
- Only after this evidence exists, design concrete provider schemas and clients with mocked tests.

### Phase 4 — Features and scoring

- Implement pure feature calculation.
- Implement a versioned, transparent scoring function.
- Add optional H2H/form features only where verified data exists.
- Keep missing-data behavior explicit.

### Phase 5 — Persistence

- Add a separate prediction store with temporary-SQLite tests.
- Persist raw snapshots, candidates, feature sets, scores, daily groups, and diagnostics.
- Add prediction-specific acquisition state and job locks.

### Phase 6 — Rolling scheduler

- Add a separate asyncio prediction worker.
- Use a separate enabled setting and lease lock.
- Refresh tomorrow, day+2, and day+3 in Africa/Lagos.
- Keep first runs paper-only and side-effect-free.

### Phase 7 — Booking integration

- Add a generic, tested SportyBet booking boundary that delegates to the existing share POST behavior.
- Generate Over 1.5 identity dicts from persisted candidates.
- Deduplicate and batch at 50.
- Re-fetch and validate each generated code with `get_booking`.
- Introduce new, separate paper/real authorization controls.

### Phase 8 — Telegram delivery

- Add a separately tested Telegram message sender.
- Persist predictions before delivery.
- Require explicit authorized chat targets.
- Report delivery success or failure independently from prediction success.

### Phase 9 — Optional API/UI

- Only after the backend pipeline is stable, add a read-only prediction API and, if needed, a separate Mini App workspace.

## Safest first implementation step

The safest first implementation is **Phase 1 only**: a pure, network-free market-preserving parser and Over 1.5 extractor tested against the existing checked-in SportyBet snapshots. It closes the exact current gap without touching the working Optimizer, Splitter, booking pipeline, Forebet pipeline, database, scheduler, environment, or Telegram behavior.

## Audit scope

The reconnaissance included the backend entrypoint and routers; SportyBet service and schemas; booking and history services; Forebet services and worker; SQLAlchemy stores; Telegram auth, session, identity, and bot code; frontend app, Optimizer/Splitter components, API client, shared list, state, and Telegram hook; backend and frontend tests; Docker, Render, Vercel, package, dependency, and environment declarations; existing SportyBet and Forebet documentation; and all checked-in SportyBet snapshot JSON files.
