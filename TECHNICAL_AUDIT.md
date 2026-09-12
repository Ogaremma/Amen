# Amen Technical Audit

**Inspection date:** 2026-09-09  
**Repository:** `Amen`, branch `main`  
**Audited revision:** `a1a1dce0ab2d0bbe45a59ab81f3679f085ad67aa` (`origin/main`)  
**Worktree note:** `frontend/src/components/DashboardPage.tsx` has an uncommitted user change. It is preserved and is part of the local build/lint results below.

## 1. Architecture Overview

Amen is a React 19/Vite Telegram Mini App with a FastAPI backend and SQLAlchemy persistence. The frontend is a compact workspace for loading and editing SportyBet tickets; the backend owns SportyBet calls, Telegram Web App identity validation, history, and the Forebet automation pipeline.

The two principal workflows are:

1. **Manual Optimizer.** A user enters a SportyBet share code. Amen retrieves and normalizes the ticket, presents selections grouped by Africa/Lagos date and status, preserves first-observed odds, supports selecting/removing multiple games through one SportyBet regeneration request, and provides Telegram-scoped history. The Splitter view derives a chronological subset (First/Middle/Last), generates a new code, reveals its games, and supports multiple split cards.
2. **Automated Forebet Draw Analyzer.** A rolling worker targets tomorrow through day+3 in Africa/Lagos, acquires Forebet 1X2 pages using HTTP with a Playwright fallback, parses explicit DRAW predictions, obtains authoritative paginated SportyBet football fixtures, fuzzy-matches identities, persists daily/compilation/batch/rebooking state, and prunes games around kickoff. Paper booking produces deterministic `PAPER-*` codes. Real SportyBet booking requires both safety flags to be true.

FastAPI exposes the API and startup/shutdown worker lifecycle. SQLAlchemy uses PostgreSQL when `DATABASE_URL` is present and SQLite otherwise; tables are created lazily by the stores and there is no migration framework. The browser fallback is synchronous Playwright work serialized behind an async semaphore. Vercel serves the compiled frontend; Render serves the Dockerized backend.

## 2. File, Service, Route, and Schema Inventory

### Backend application

- [`backend/app/main.py`](backend/app/main.py) - FastAPI app, CORS, health/readiness endpoints, router registration, and worker startup/shutdown.
- [`backend/app/config/settings.py`](backend/app/config/settings.py) - Pydantic settings, environment aliases, defaults, and the dual real-booking guard.
- [`backend/app/api/bookings.py`](backend/app/api/bookings.py) - Ticket fetch and selected-game removal/rebooking endpoints.
- [`backend/app/api/history.py`](backend/app/api/history.py) - Authenticated history list and delete endpoints.
- [`backend/app/api/telegram.py`](backend/app/api/telegram.py) - Telegram init-data authentication endpoint.
- [`backend/app/api/forebet.py`](backend/app/api/forebet.py) - Forebet ingestion, analysis, matching, booking, rolling-window, diagnostics, and refresh routes.

### Backend services

- `sportybet.py` - SportyBet share-code retrieval, selection normalization, booking regeneration, upcoming-fixture pagination, and fixture snapshot parsing.
- `forebet.py` - Forebet HTTP/browser acquisition, HTML parsing, DRAW filtering/ranking, challenge detection, cookie retention, and diagnostic logging.
- `forebet_dates.py` - Africa/Lagos future prediction dates and Forebet URL generation.
- `fixture_matching.py` - Normalization and fuzzy/evidence-based Forebet-to-SportyBet fixture matching with identity validation.
- `forebet_draw_engine.py` - Rolling draw-window orchestration, matching, candidate selection, paper/real booking, batching, CAS/reconciliation, and kickoff pruning.
- `forebet_draw_store.py` - SQLAlchemy tables and persistence for analyzer state, snapshots, batches, revisions, rebooks, acquisition state, and job locks.
- `forebet_draw_worker.py` - Background refresh/prune loop, distributed lock usage, challenge backoff, and worker status.
- `forebet_draw_diagnostics.py` - Read-only analyzer diagnostics and persisted-state reporting.
- `forebet_ingestion.py` - Authenticated external snapshot ingestion and execution against trusted Forebet/SportyBet data.
- `history_store.py` - Per-Telegram-user booking history and immutable first-observed odds snapshots.
- `session_store.py` - In-memory per-user session context, including current booking code.
- `telegram_auth.py` - Telegram Web App HMAC validation and freshness checks.
- `telegram_identity.py` - FastAPI dependency extracting and validating `X-Telegram-Init-Data`.

### Telegram integration and deployment files

- `backend/app/telegram/bot.py` - aiogram bot setup/handlers (the Render blueprint does not define a separate bot worker).
- `backend/Dockerfile` - Backend container image and Playwright runtime setup.
- `backend/requirements.txt` - Pinned Python dependencies, including `psycopg2-binary`, Playwright, and BeautifulSoup.
- `render.yaml` - Paper-only Render blueprint; currently names the service `amen-backend-staging`.
- `scripts/local_forebet_agent.py` and related diagnostic/uploader scripts - trusted local Forebet acquisition and snapshot upload tooling.

### Database tables

`booking_history`, `selection_odds_snapshots`, `forebet_draw_daily_bookings`, `forebet_draw_prebooking_candidates`, `forebet_draw_compilation`, `forebet_draw_booking_revisions`, `forebet_draw_daily_batches`, `forebet_draw_compilation_batches`, `forebet_draw_rebook_events`, `forebet_raw_snapshots`, `sportybet_fixture_snapshots`, `forebet_acquisition_state`, and `forebet_job_locks`.

### API routes

- `GET /health` - liveness check.
- `GET /readiness` - database and worker status.
- `GET /api/v1/bookings/{booking_code}` - fetch/normalize a SportyBet ticket.
- `POST /api/v1/bookings/{booking_code}/remove-selected` - regenerate a ticket after removing event IDs.
- `POST /api/v1/telegram/auth` - validate Telegram signed init data and return identity/session information.
- `GET /api/v1/history` - list authenticated user history.
- `DELETE /api/v1/history/{history_id}` - delete one authenticated history item.
- `POST /api/v1/forebet/acquisition-snapshots` - authenticated Forebet HTML snapshot ingestion.
- `POST /api/v1/forebet/sportybet-fixture-snapshots` - authenticated SportyBet fixture snapshot ingestion.
- `POST /api/v1/forebet/analyze` - analyze supplied Forebet/SportyBet data.
- `POST /api/v1/forebet/matches` - match Forebet predictions to fixtures.
- `POST /api/v1/forebet/book-draws` - book selected draw matches (paper or direct SportyBet path).
- `GET /api/v1/forebet/draw-window` - current rolling window.
- `GET /api/v1/forebet/draw-window/diagnostics` - persisted diagnostics.
- `POST /api/v1/forebet/draw-window/refresh` - refresh all target dates.
- `POST /api/v1/forebet/draw-window/{prediction_date}/refresh` - refresh one date.

### Frontend

- `App.tsx` - top-level workspace routing/state composition.
- `DashboardPage.tsx` - manual ticket optimizer, history, ended-game removal, and Splitter UI. The local uncommitted version also contains an unused `deleteSplitGame` handler.
- `AnalyzerPage.tsx` - rolling Forebet draw-window display, paper/real code labels, batches, diagnostics, and rebooking history.
- `WorkspaceNavigation.tsx` - workspace navigation between optimizer and analyzer.
- `PlaceholderPage.tsx` - placeholder view for unfinished workspace destinations.
- `OptimizePage.tsx`, `MatchList.tsx`, `NavBar.tsx`, `QuickActionCard.tsx` - older/legacy-looking components not central to the current routed workflow.
- `components/ui/button.tsx`, `card.tsx`, `input.tsx` - shared UI primitives.
- `lib/api.ts` - typed HTTP client and auth-header handling.
- `lib/clipboard.ts` - clipboard API with deprecated `execCommand` fallback.
- `lib/splitter.ts` - pure chronological First/Middle/Last selection algorithm.
- `lib/utils.ts` - shared class-name/util helpers.
- `hooks/useTelegramWebApp.ts` - Telegram Web App detection, readiness, and auth state.
- `store/useAppStore.ts` - Zustand app state (plus mock `data/matches.ts`).
- `types/booking.ts`, `types/telegram.ts` - frontend contracts.
- `components/*.test.tsx`, `lib/splitter.test.ts`, `test/setup.ts` - Vitest/Testing Library coverage.
- `App.css`, `index.css`, `assets/*`, Vite/Tailwind/PostCSS/TypeScript config - presentation and build configuration.

## 3. Current Test and Build Status

Run on 2026-09-09:

- Backend, from `backend` using `backend/.venv`: **243 passed in 21.67s**.
- Backend with system Python 3.13.7: **17 collection errors**. Fresh setup needs the pinned `backend/requirements.txt` dependencies, notably `psycopg2-binary`, `playwright`, and `beautifulsoup4`; Playwright Chromium must also be installed.
- Frontend `npm.cmd test -- --run`: **4 files passed, 47 tests passed**.
- Frontend `npm.cmd run build`: **fails** with `TS6133: deleteSplitGame is declared but its value is never read` in the dirty `DashboardPage.tsx`.
- Frontend `npm.cmd run lint`: **11 errors and 1 warning**. Besides the unused Splitter handler, failures include Fast Refresh export/type issues in UI primitives, set-state-in-effect in `useTelegramWebApp.ts`, useless assignments in `api.ts`, explicit `any` in Splitter tests/store, Tailwind config parsing, and an Analyzer callback dependency warning.

## 4. Git State and Feature Timeline

`HEAD`, `main`, and `origin/main` all point to `a1a1dce0ab2d0bbe45a59ab81f3679f085ad67aa` (2026-09-03), "Update splitter terminology test assertions". The only local modification is `frontend/src/components/DashboardPage.tsx`.

Significant chronological milestones: Telegram Mini App integration (`8e37993`); ticket details/odds and PostgreSQL history (`5fc81fc`, `9e255e4`); workspace separation (`9990f7f`); Forebet provider/data layer/matching/analyzer (`e48e6ad` through `4fa5976`); authenticated snapshots and persisted candidates (`9877902`, `44ecd4a`); compilation/history gestures and paper rolling window (`5c3a7ab`, `aeca1fb`); batching, kickoff rebooking, dual real-booking wiring, and Render blueprint (`56fd723`); Cloudflare validation/diagnostics/wait/backoff (`a4123b1`, `14b6fee`, `330ab87`, `be0d8b8`); selection controls and trusted-snapshot preservation (`c60807d`, `d89b676`); shared sequential uploader/pacing (`5dd3242`, `c4d753a`, `ac9731e`); Splitter implementation and UX/scaling (`32756d1`, `d11dbe5`, `ed1a241`, `a1a1dce`).

## 5. Deployment State

### Backend (Render)

The public backend is `https://amen-backend.onrender.com`. At inspection, `/readiness` returned HTTP 200:

```json
{"status":"ready","database":{"reachable":true,"error":null},"worker":{"enabled":true,"running":true,"last_refresh_started":"2026-09-09T04:53:12.225921+00:00","last_refresh_completed":null,"last_failure":null,"last_failure_stage":null,"last_prune_completed":"2026-09-09T04:53:12.889581+00:00","consecutive_forebet_failures":0,"forebet_cooldown_until":null}}
```

`https://amen-backend-staging.onrender.com` returns 404, so the checked-in `render.yaml` service name does not match the live hostname. The blueprint defines only one backend web service and no bot worker. Render dashboard/API credentials were unavailable, so actual secret and non-secret environment values on the live service cannot be independently read. The YAML declares paper-only values (`FOREBET_DRAW_BOOKING_ENABLED=false`, `FOREBET_REAL_BOOKING_AUTHORIZED=false`, `FOREBET_DRAW_PAPER_BOOKING_ENABLED=true`, worker/browser fallback enabled) and the public behavior is consistent with paper mode, but the two live flag values still require authorized dashboard/API confirmation.

### Frontend (Vercel)

The repository/configuration identifies `https://amen-six.vercel.app` as the public frontend. A successful deployment for commit `a1a1dce` exists at `https://amen-8ea5kt7y5-ogar-emmas-projects.vercel.app`; its bundle calls `https://amen-backend.onrender.com/api/v1`. Live CORS allows the Vercel origin and `http://localhost:5173`, and rejects arbitrary origins. Telegram auth is configured (invalid init data receives 401 rather than 503).

## 6. Forebet Acquisition Status

Stored Forebet snapshots parse successfully and the SportyBet upcoming-fixture provider is authoritative in production (the last observed acquisition retrieved 1,441 fixtures across 15 pages, fresh and complete). Render HTTP requests to Forebet receive 403; Render headless Chromium reaches a Cloudflare/CAPTCHA challenge. The fallback validates host, challenge markers, recognizable Forebet HTML, and `.schema > .rcnt`, then backs off after repeated challenges. Trusted externally acquired snapshots can be uploaded with bearer authentication and preserve last-known-good data when live acquisition fails.

Current instrumentation in [`backend/app/services/forebet.py`](backend/app/services/forebet.py) includes `http_403`, `browser_fallback_attempted`, `browser_navigation_request url=%s referer=%r`, `clearance_injection_before`, `clearance_injection_after`, `clearance_capture`, structured `browser_validation` (outcome/rule/challenge/title/final URL/host/elapsed/schema count/fixture rows), and `browser_fallback_success`/failure reasons. Only `cf_clearance` and `__cf_bm` cookies are retained, and browser operations are semaphore-serialized.

The engine and `scripts/local_forebet_agent.py` fetch dates sequentially to reuse clearance cookies, but each browser call currently creates a new browser/context/page and directly navigates to the dated URL. The last untested lead is a **referer/navigation-chain signal**: use one persistent browser context/page, navigate first to `/en/` (or an accepted prediction page), then navigate sequentially to later dated URLs while preserving browser-generated `Referer` and history, and compare the existing `browser_navigation_request` logs.

## 7. Splitter Feature Status

Committed and deployed in the Vercel bundle: Splitter entry from a loaded ticket; First/Middle/Last modes; numeric count validation from 1 through total selections; chronological sorting; First selects earliest N, Last latest N, and Middle expands around the center with lower odds as the final tie-break; one backend removal/rebooking request per generated split; result/source booking display; reveal/hide games; edit/reset/copy individual code; reset all; copy all; add additional split cards; and responsive multi-card layout. Pure splitter logic has eight tests, and the live bundle contains these controls.

Still in progress: the dirty `DashboardPage.tsx` adds `SwipeDeleteSurface` and `deleteSplitGame(cardId, eventId)` intending to regenerate a split after swiping a revealed game away, but revealed game rows do not render `SwipeDeleteSurface` or call the handler. The function is unused, so the local TypeScript build/lint fails. The deployed bundle has neither `data-swipe-delete` nor the deletion error string. There are no component-level tests for the Splitter workflow; current coverage is pure selection logic only.

## 8. Known Open Bugs and Gaps

- Telegram Desktop WebView clipboard remains unreliable: `clipboard.ts` tries `navigator.clipboard.writeText`, then deprecated `document.execCommand('copy')`; some desktop environments reject both.
- Splitter per-game swipe deletion is incomplete and currently breaks the local production build.
- `removeSelectedGames` does not attach `X-Telegram-Init-Data`, unlike fetch/history calls. Rebook responses therefore skip verified-user odds restoration and are not identity-bound.
- Rebooked codes are not inserted into history; history is updated only when a verified user fetches a code.
- `SessionStore.set_current_booking` is never called, so Telegram auth returns `current_booking_code: null`; the in-memory store also disappears on restart and is not shared across instances.
- Plain-browser mode can load tickets, but History requires Telegram auth and returns 401.
- `render.yaml` names a different backend service than the live Render service, and does not define a bot worker.
- Root `README.md` still describes Phase 1 and is stale. Tracked `openapi.json` is stale (13 routes and missing readiness, history delete, and fixture snapshot ingestion).
- `/readiness` reports ready on database/worker reachability even when Forebet acquisition is blocked or no analyzer day is active.
- `/api/v1/forebet/book-draws` directly invokes SportyBet generation without visibly enforcing the dual flags or ingestion auth at the route boundary; review whether this endpoint should be protected or diagnostic-only.

## 9. Safety-Critical Configuration and Guardrails

Do not change without explicit authorization:

- `FOREBET_DRAW_BOOKING_ENABLED=false`
- `FOREBET_REAL_BOOKING_AUTHORIZED=false`

Automated real booking requires **both** flags to be true. Keep `FOREBET_DRAW_PAPER_BOOKING_ENABLED=true` during development and deployment validation. Do not expose or log Telegram bot tokens, ingestion tokens, database URLs, or signed init data. Preserve authenticated ingestion, Telegram backend HMAC validation, exact SportyBet identity fields (`eventId`, `marketId`, `outcomeId`, `productId`, `sportId`, optional `specifier`), DRAW identity restrictions (market `1`, outcome `2`, pre-match only), authoritative fixture freshness/completeness checks, trusted last-known-good snapshots, distributed locks/CAS identity checks, 50-selection batching, and no-empty-ticket validation.

## 10. Prioritized Next Steps

1. Finish the Splitter swipe-delete interaction: render the swipe surface around revealed games, wire regeneration state/error handling, add component-level tests, then restore a clean TypeScript build.
2. Clear the remaining frontend lint errors and warning, including UI primitive exports/types, effect state update, `api.ts` assignments, explicit `any`, Tailwind parsing, and Analyzer dependencies.
3. Test the persistent Forebet navigation-chain/referer hypothesis with one browser context/page and sequential navigation; retain the existing safe diagnostics and compare against the current direct-navigation path.
4. Reconcile Render: confirm the live service/environment values through authorized Render dashboard/API access, especially both real-booking flags; rename/update `render.yaml` only with deployment-owner agreement.
5. Fix manual rebooking identity/auth propagation, history insertion for generated codes, and current-booking session updates; decide whether session state should be persisted/shared.
6. Address Telegram Desktop clipboard fallback and document supported Telegram/browser behavior.
7. Improve readiness to report provider/analyzer health separately from database reachability, and add monitoring for blocked Forebet acquisition.
8. Regenerate `openapi.json`, update the root README from "Phase 1," and document the backend test/bootstrap commands (run pytest from `backend`; install requirements and Playwright Chromium).
