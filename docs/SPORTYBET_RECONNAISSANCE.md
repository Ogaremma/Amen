# SportyBet Data Reconnaissance

Date: 2026-09-10

This is a read-only reconnaissance of the SportyBet integration currently present in Amen. No prediction engine or Forebet behavior was changed.

## 1. Endpoints currently used by Amen

Configuration defaults are in `backend/app/config/settings.py`.

| Method | Path | Request | Response / use | Auth | Tests |
|---|---|---|---|---|---|
| GET | `/api/ng/orders/share/{bookingCode}` | Query `_t=<epoch milliseconds>`; JSON/Accept headers, browser-like Origin/Referer/User-Agent | Booking envelope. `parse_booking` consumes `isAvailable`, `bizCode`, `data`, event objects, markets, outcomes and settlement fields. | No SportyBet credential is added by Amen; public share-code access. | Yes: `backend/tests/test_bookings_unittest.py` (mocked HTTP and parser tests). |
| POST | `/api/ng/orders/share` | JSON `{ "selections": [ {eventId, marketId, outcomeId, productId, sportId, optional specifier} ] }` | Success observed/encoded as `bizCode=10000`, `data.shareCode`; rejection codes 19000/19999 become 502. | No credential added. | Yes: stateful fake POST/GET rebooking tests in `test_bookings_unittest.py`. |
| GET | `/api/ng/factsCenter/pcUpcomingEvents` | `sportId=sr:sport:1`, `marketId=1,18,10,29,11,26,36,14,60100`, `pageSize` (default 100), `pageNum` (1-based), `_t` | `{bizCode,message,data:{totalNum,tournaments[]}}`; each tournament has `events[]`. Parsed into `SportyBetEvent`. | No credential added. | Yes: upcoming parser/pagination tests in the Forebet-related test suite. |

The upcoming parser preserves event identity and metadata: `eventId`, `gameId`, `homeTeamId`, `awayTeamId`, names, `estimateStartTime` (milliseconds), numeric `status`, `matchStatus`, `sport.id/name`, category and tournament ids/names, selected market `id/product/specifier`, outcome ids, odds and probabilities. The parser currently selects football market id `1` for its 1X2 convenience fields; raw event markets remain available in snapshots.

## 2. H2H/H2Hnew result

No H2H or H2Hnew endpoint, request builder, parser, schema, fixture field, or captured response exists in the Amen repository. `rg` found no H2H implementation or payload in application code, tests, or the captured SportyBet pages. The only observed event-detail data is the catalog response above.

Therefore the following are **not verified as available**: previous meetings, H2H dates/scores, H2H home/away teams, competition, H2H goal totals, H2H sample count, Over 1.5/2.5 H2H rates, BTTS history, or H2H pagination/limits. They must not be fabricated. A separate, legitimate browser/application request capture is required before designing an H2H contract.

## 3. Observed event and market data

Evidence: `snapshots/sportybet-page-1.json` through `sportybet-page-10.json`.

An event contains `eventId`, `gameId`, `productStatus`, `estimateStartTime`, `status`, `matchStatus`, team ids/names, nested sport/category/tournament metadata, `totalMarketSize`, `markets`, `bookingStatus`, `commentsNum`, `topicId`, `fixtureVenue`, `eventSource`, and boolean flags such as `banned`.

Observed markets include:

* `id=1`, `product=3`, `desc/name=1X2`; outcomes `1` Home, `2` Draw, `3` Away.
* `id=18`, `desc/name=Over/Under`, with `specifier` values such as `total=1`, `total=2`, `total=3`, `total=4`. Outcomes are `12` Over and `13` Under; descriptions include the line (for example `Over 1`). The response's market guide explicitly documents half lines including 1.5, and the captured catalog contains markets selected by the configured market inventory; exact `total=1.5` presence was not established in the inspected first event and must be checked on a current event response before implementation.
* `id=29`, `desc/name=GG/NG`; outcomes `74` Yes and `76` No, with descriptions stating both teams score / any team does not score.
* `id=36`, `desc/name=Over/Under & GG/NG`, `specifier` such as `total=2.5`; outcomes encode combined over/under and BTTS combinations.
* Other observed ids include `10` Correct Score, `11` Draw No Bet, `14` Handicap, `26` Odd/Even, and `60100/60200` early-payout 1X2 variants.

Market/outcome fields observed are `id`, `product`, `specifier`, `desc`, `status`, `group`, `groupId`, `marketGuide`, `title`, `name`, `favourite`, `outcomes`, `farNearOdds`, `sourceType`, `lastOddsChangeTime`, `banned`; outcome fields include `id`, string `odds`, string `probability`, string `voidProbability`, `isActive`, optional `cashOutIsActive`, and `desc`.

## 4. Over 1.5 identity

Amen already preserves the exact booking identity tuple `eventId`, `marketId`, `outcomeId`, `productId`, `sportId`, and optional `specifier` when rebooking. For an Over/Under selection the observed representation is market `id=18`, outcome `id=12` for Over, `product=3`, and a required line-bearing `specifier` such as `total=1.5` if supplied by SportyBet. The odds are the outcome's string `odds`, and the event id is the enclosing event's `eventId`. The presence and exact odds of `total=1.5` must be confirmed from a current response before filtering at 1.40 <= odds < 1.50.

## 5. Freshness and pagination

`get_upcoming_football_events` fetches pages sequentially, up to configured `max_pages` (default 20), stopping when an empty page is returned or `page * pageSize >= totalNum`. It records retrieval time, expected total, retrieved total, page count, and a completeness flag. The configured TTL is 300 seconds. Filtering by the requested UTC interval happens after page retrieval, so the endpoint is structurally suitable for a three-day window only when the complete catalog is obtained and fresh.

The checked-in captures show `totalNum` about 971-977, pages 1-9 with 100 events and page 10 with the remainder (72 in the capture). This is evidence that ten 100-size pages covered that captured inventory, not a guarantee of today's inventory. No historical odds series or intra-day H2H refresh behavior was observed.

## 6. Prediction feasibility

Direct SportyBet signals: kickoff/status, teams and ids, competition metadata, current market odds/probabilities, Over/Under markets, GG/NG, combined goal/BTTS markets, 1X2 and market timestamps (`lastOddsChangeTime`), where present.

Derived from SportyBet data: implied probabilities, goal-line comparisons, odds movement between snapshots, market availability, and (only if H2H/results payloads are later verified) counts and percentages of matches with 2+ goals or BTTS.

Unavailable from current evidence: H2H history, team form/recent results, standings, team statistics, historical odds, or private bookmaker intelligence. Do not claim or synthesize these until a real SportyBet response is captured.

## 7. Recommended next architecture

Add a read-only SportyBet event-details/H2H client only after manually capturing and validating the legitimate application request and response. Store raw, timestamped responses; normalize event identity and markets without losing `specifier`; expose a provider-neutral read model to a later scorer; and retain the existing booking service as the eventual execution boundary. Keep the rolling three-day scheduler, scoring, booking, Telegram output, and new persistence out of this phase.

## Remaining uncertainty

The H2H request and exact Over 1.5 representation are not present in local evidence. They require manual inspection of a currently accessible SportyBet event in the normal web/application flow, followed by a recorded request/response (with secrets removed). Whether the catalog's configured market list always returns every goal line, and how quickly odds/H2H change during a day, also remain unverified.
