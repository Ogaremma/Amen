# SportyBet H2H / Statistics Reconnaissance

Date: 2026-09-11

Phase: 3.5 discovery and transport audit only.

No H2H client, Sportradar client, prediction model, database table, migration, scheduler, worker, Telegram change, booking change, frontend change, new API, Forebet change, Optimizer change, or Splitter change was implemented.

## Result

The exact SportyBet-to-Sportradar mapping, the historical H2H feed, the normal widget token flow, and a successful sanitized H2H response are now confirmed.

The final integration decision is:

```text
BACKEND INTEGRATION NOT YET JUSTIFIED
```

The feed is publicly reachable through SportyBet's normal event-detail widget without login, but the token is issued to the Sportradar widget for the SportyBet browser origin, decrypted and refreshed by widget JavaScript, and is not an official backend API contract. A later implementation must not embed or replicate this browser widget flow without explicit authorization or a more stable integration path.

## 1. Phase 2 SportyBet transport audit

Audited file:

```text
backend/app/services/sportybet.py
```

The uncommitted Phase 2 diff was compared against `HEAD`. No regression was found, and Phase 2 is safe to retain.

### `_fetch_upcoming_page_payload()`

This function contains the transport logic that previously lived directly in `_fetch_upcoming_page()`. The following behavior is preserved:

- three attempts per page;
- `0.75 * 2**attempt` seconds of exponential backoff;
- the existing `httpx.AsyncClient` timeout;
- the existing request headers and user agent;
- the existing URL from `_upcoming_url()`;
- the existing `_t` cache-busting timestamp;
- the existing JSON parse and `HTTPException` behavior.

The only semantic change is that the function now returns the raw successful payload instead of immediately parsing it into `SportyBetEvent`.

### `_fetch_upcoming_page()`

This function now calls:

```text
_fetch_upcoming_page_payload(page_num, page_size)
    -> parse_upcoming_events_page(payload)
```

`parse_upcoming_events_page()` and `_parse_upcoming_event()` are unchanged. Existing 1X2 parsing, market `1` selection, outcome IDs `1/2/3`, probability parsing, status normalization, and `SportyBetEvent` fields remain identical.

### `_fetch_upcoming_market_page()`

This additive function calls the same raw payload transport and passes it to the Phase 1 market-preserving parser:

```text
_fetch_upcoming_page_payload(page_num, page_size)
    -> parse_upcoming_events_markets(payload)
```

It does not add HTTP behavior of its own.

### `_collect_upcoming_catalog()`

The shared pagination helper preserves the original stop conditions:

- stop after an empty retrieved page;
- stop when `page * page_size >= total_num`;
- otherwise continue until `max_pages`;
- preserve the last observed `total_num`;
- preserve `pages_fetched`;
- preserve `retrieved_num`.

For the legacy parser, `SportyBetUpcomingEventsResult.retrieved_num` is initialized from `len(events)`, so the helper's `retrieved_num` remains equivalent to the pre-refactor count.

### `get_upcoming_football_events()`

The public function retains:

- the same signature;
- the same page-size and max-page validation;
- the same UTC normalization and invalid-window error;
- the same kickoff filtering semantics;
- the same return type, `SportyBetUpcomingEventsResult`;
- the same `total_num`, filtered `events`, `pages_fetched`, `retrieved_at`, `complete`, and `retrieved_num` behavior.

### Additive market functions

`get_upcoming_football_market_fixtures()` and `get_upcoming_over_one_half_candidates()` are new. They do not alter the legacy function or its callers.

### Untouched behavior

The Phase 2 diff does not modify booking parsing, SportyBet selection identity, share-code creation, rebooking, Forebet behavior, API routes, or frontend behavior. No booking or order side effect was added.

## 2. Confirmed event mapping

SportyBet event:

```text
sr:match:72221252
```

SportyBet prematch provider source:

```json
{
  sourceType: BET_RADAR,
  sourceId: 72221252
}
```

Sportradar widget match ID:

```text
72221252
```

Fixture:

```text
Aston Villa vs Nottingham Forest
Premier League 2026/27
```

For a `BET_RADAR` football event:

```text
SportyBet eventId: sr:match:<numericId>
Sportradar matchId: <numericId>
```

Evidence:

- A public, unauthenticated SportyBet catalogue response returned the event with `eventSource.preMatchSource.sourceType = BET_RADAR` and `sourceId = 72221252`.
- Public SportyBet pre-match JavaScript computes the widget match ID as the third colon-separated component of the SportyBet event ID.
- The checked-in snapshots contain 972 upcoming events. Of those, 958 have `BET_RADAR` prematch sources, and all 958 source IDs exactly equal the numeric suffix of the SportyBet event ID.
- The remaining 14 snapshot events use `BET_GENIUS`; this mapping must not be applied to them.

## 3. Confirmed normal widget and token flow

All observations were made from a clean, non-logged-in browser context loading the normal public event page. No cookie, authorization header, session credential, CAPTCHA bypass, proxy rotation, or other access-control bypass was used.

### Public SportyBet event page

```http
GET /ng/sport/football/england/premier_league/aston_villa_vs_nottingham_forest/sr:match:72221252
Host: www.sportybet.com
```

- Method: `GET`
- Authentication: none
- Response: HTML
- The HTML embeds the public widget loader:

```text
https://widgets.sir.sportradar.com/638846b93b23ecfc94ce1a6d45b1dbe6/widgetloader
```

### Sportradar widget loader

```http
GET /638846b93b23ecfc94ce1a6d45b1dbe6/widgetloader
Host: widgets.sir.sportradar.com
```

- Method: `GET`
- Query parameters: none observed
- Authentication: none observed
- Response: JavaScript
- Observed cache policy: `public, max-age=120, stale-while-revalidate=60`

The loader dynamically loads public widget chunks, including the chunk containing licensing processing and the chunk containing the Fishnet token module.

### Widget licensing bootstrap

```http
GET /638846b93b23ecfc94ce1a6d45b1dbe6/licensing
Host: widgets.sir.sportradar.com
```

- Method: `GET`
- Query parameters: none observed
- Authentication: none observed
- Response type: JSON

- Top-level response shape:

```text
text  = encrypted licensing payload
valid = true
```

The encrypted `text` value is intentionally not reproduced or committed.

### Licensing decryption and Fishnet token

The public widget JavaScript:

1. checks `valid`;
2. decrypts `text` with AES using the public widget client ID as the passphrase;
3. parses the decrypted result as JSON;
4. extracts `fishnetToken`;
5. calls the widget browser token setter;
6. registers a token refresh function.

The decrypted licensing object contains `fishnetToken` and `packages`.

For the observed capture, `fishnetToken` contained:

```text
token
expirationTs
origin
```

Safe metadata observed:

- token length: 241 characters;
- token format: opaque and non-JWT;
- origin: `https://www.sportybet.com`;
- observed expiration was approximately 24.6 hours after capture;
- the widget refreshes licensing every 3600 seconds.

The licensing `packages` list scopes the widget installation by widget names, sports, and tournaments. This is widget licensing scope, not a user authentication session.

### Token refresh behavior

The loader public code uses:

- normal licensing refresh interval: 3600 seconds;
- failure retry interval: 300 seconds;
- failure retry window: four hours;
- later backoff intervals: `1, 3, 5, 5, 5, 20, 60` seconds.

The Fishnet token module treats a token as usable while:

```text
expirationTs - 60000 > Date.now()
```

Otherwise it invokes the registered refresh function and replaces the stored token.

### Fishnet requests

The widget F3 provider:

- reads the stored Fishnet token;
- appends it as a `T` query parameter;
- makes requests with browser credentials disabled;
- uses the same token for match, team, and season feeds in the observed page.

Observed successful request pattern:

```text
GET https://lmt.fn.sportradar.com/common/en/Etc:UTC/gismo/<feed>/<params>?T=<redacted>
```

Confirmed successful feeds included:

```text
match_info/72221252
stats_season_meta/140756
stats_team_lastx/40
stats_team_lastx/14
stats_team_versusrecent/40/14
stats_team_versusrecent/40/14/100
stats_season_uniqueteamstats/140756
stats_season_goals/140756
stats_match_form/72221252
season_livetable/140756
```

### Token scope conclusion

Confirmed:

- not bound to a user login, because a clean no-login browser context succeeded;
- issued for a public Sportradar widget client;
- associated with the `https://www.sportybet.com` origin;
- used across multiple match, team, and season feeds in the observed page;
- short-lived, with a longer observed expiry than the hourly licensing refresh.

Not yet verified:

- stability across all SportyBet locales;
- acceptance outside the normal browser widget context;
- acceptance for every widget installation using the same public client ID;
- provider rate limits and abuse controls;
- terms of service or contractual permission for direct backend use.

## 4. Confirmed H2H request

### Resolve team UIDs

```http
GET /common/en/Etc:UTC/gismo/match_info/72221252?T=<redacted>
Host: lmt.fn.sportradar.com
```

- Method: `GET`
- Body: none
- Required identifier: Sportradar match ID
- Token: widget-managed `T` query parameter
- The widget reads `data.teams.home.uid` and `data.teams.away.uid`.

For the observed fixture:

```text
home team UID = 40
away team UID = 14
season ID     = 140756
```

A token-less request reached the provider but returned a JSON provider-level unauthorized exception with code `403` and `x-origin: invalid-missing-token`.

### Request historical H2H meetings

```http
GET /common/en/Etc:UTC/gismo/stats_team_versusrecent/<homeTeamUid>/<awayTeamUid>[/<limit>]?T=<redacted>
Host: lmt.fn.sportradar.com
```

Observed successful URLs:

```text
stats_team_versusrecent/40/14
stats_team_versusrecent/40/14/100
```

- Method: `GET`
- Body: none
- Required identifiers: home team UID and away team UID
- Optional identifier: numeric result limit
- Authentication: widget-managed `T` token
- Response: JSON

A successful sanitized response was captured through the normal widget flow. The raw temporary capture was not committed.

## 5. Confirmed H2H response schema

Top-level response shape:

```text
queryUrl
doc[]
```

Each `doc` element contains:

```text
event
_dob
_maxage
data
```

The observed H2H response reported:

```text
event   = stats_team_versusrecent
_maxage = 3600
```

The `data` object contains:

```text
matches
upcomingmatches
tournaments
realcategories
teams
currentmanagers
jersey
next
```

### Historical matches

Observed count for the sample:

```text
matches = 25
```

Each item in `matches` represents one completed fixture and includes:

- `_id`: Sportradar match ID;
- `_tid`: tournament ID;
- `_utid`: unique tournament ID;
- `_seasonid`: season ID;
- `teams.home` and `teams.away`;
- `teams.home.uid` and `teams.away.uid`;
- `time.uts`: UTC epoch seconds;
- `time.date`, `time.time`, and timezone fields;
- `periods.ft.home` and `periods.ft.away`;
- `periods.p1.home` and `periods.p1.away`;
- `result.home` and `result.away`;
- `result.winner`;
- `round`, `week`, and `roundname`;
- `comment` with goal events where available;
- `attendance`, referee, and manager fields where available;
- postponed, canceled, and neutral-ground flags.

Home and away orientation is preserved in each historical item. For example, the first item contained:

```text
home = Aston Villa, uid 40
away = Nottingham Forest, uid 14
full-time score = 4-0
```

### Upcoming meetings

Observed count:

```text
upcomingmatches = 2
```

These items use the same team, date, and tournament structure but have null periods and null final scores.

### Teams and tournament context

`data.teams` maps unique team IDs to names, abbreviations, and basic team metadata.

`data.tournaments` maps tournament IDs to tournament names, season IDs, season year, and tournament metadata.

`data.realcategories` maps real-category IDs to names such as country or international category.

Competition context can be derived by joining `match._tid` to `data.tournaments` and `tournament._rcid` to `data.realcategories`.

### Pagination and limits

The default and limit-100 requests both returned 25 historical matches for this fixture. This does not prove that 25 is a universal maximum; it only shows that the optional limit did not increase the result count for this pair.

No pagination cursor was present in the observed response.

## 6. Useful future Over 1.5 features

No feature calculation or weighting is implemented.

The confirmed H2H response can later support:

- total goals per historical meeting;
- average total goals;
- Over 1.5 frequency;
- Over 2.5 frequency for context;
- both-teams-to-score frequency;
- goals scored and conceded by each team;
- recent H2H windows;
- home and away orientation;
- win, draw, and loss orientation;
- competition and season context;
- upcoming-fixture awareness.

The other confirmed feeds can later support:

- recent form from `stats_team_lastx`;
- season team statistics from `stats_season_uniqueteamstats`;
- season goals from `stats_season_goals`;
- match form from `stats_match_form`;
- league-table context from `season_livetable`.

These would be Amen-derived features and must not be presented as provider-calculated prediction scores.

## 7. Limitations and unknowns

- Coverage varies by provider source, competition, and team; no general coverage guarantee exists.
- The mapping is confirmed only for `BET_RADAR` events. `BET_GENIUS` requires a separate mapping.
- The H2H feed was successfully retrieved before kickoff for the observed fixture, but not every upcoming fixture has been tested.
- The default versus limit-100 behavior has been observed for only one team pair.
- Rate limits, abuse controls, and long-term endpoint stability are unknown.
- Backend reuse of the browser widget token has not been tested and should not be inferred from browser success.

- The licensing response is encrypted specifically for the public widget client and SportyBet origin. Replicating its decryption in the backend would create a fragile, unofficial dependency.
- There is no evidence of an official Sportradar API contract or authorization for Amen backend automation.
- Widget client IDs, chunk hashes, feed hosts, and token behavior can change without notice.

## 8. Integration recommendation

Do not implement a direct backend Sportradar or Fishnet client yet.

The safest future boundary remains:

1. obtain explicit authorization or a documented stable API contract for backend statistics access;
2. define a provider interface that accepts an already-resolved Sportradar match ID;
3. keep SportyBet catalogue mapping separate from the statistics provider;
4. preserve raw authorized responses for audit before normalization;
5. keep feature calculation and scoring in pure, separately tested layers;
6. do not couple statistics acquisition to booking generation, Telegram delivery, scheduling, or Forebet.

Until such a contract exists, the correct status is:

```text
BACKEND INTEGRATION NOT YET JUSTIFIED
```

## 9. Evidence and safety

Evidence sources:

- checked-in SportyBet snapshots;
- public SportyBet catalogue response;
- public SportyBet event-detail HTML;
- public Sportradar widget loader JavaScript;
- public dynamically loaded Sportradar widget chunks;
- sanitized normal-browser network observations;
- successful sanitized H2H response captured through the normal public widget flow;
- direct token-less request that returned the documented provider-level unauthorized exception.

No cookie, authorization header, access token, refresh token, session ID, private key, API secret, or encrypted licensing `text` value is recorded here. Temporary browser captures remain outside the repository.
