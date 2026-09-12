# Prediction Production Scheduling

## Status

Production uses a free-compatible architecture:

- Render Free Web Service for the FastAPI backend and Telegram webhook.
- Render Postgres for production persistence.
- GitHub Actions for the bounded daily prediction runner.

`render.yaml` intentionally defines only the free Docker web service. Render Background Worker and Render Cron services are **not required** and must not be added for this deployment.

Telegram production traffic uses webhook mode. The local long-polling entry point remains available for development, but the backend does not require a polling process in production.

## Timezone

Production prediction dates and windows use the named IANA timezone:

```text
Africa/Lagos
```

Nigeria is normally UTC+1 and does not currently observe daylight-saving time:

```text
00:00:00 Africa/Lagos = 23:00:00 UTC on the previous UTC day
```

The implementation uses `ZoneInfo("Africa/Lagos")`; it does not use the server-local timezone and does not hardcode a numeric offset.

Phase 4F's compatibility service continues to identify its historical UTC generations as UTC. Phase 4I production generations use `Africa/Lagos` in their generation identity. This intentionally creates distinct identities for new production runs rather than silently rewriting historical UTC identities.

## Command

The bounded entry point is:

```bash
python -m app.prediction_daily_runner
```

It reads eligible recipients from the production `telegram_bot_users` table. `TELEGRAM_PREDICTION_CHAT_ID` is not required for production fan-out.

The command performs one generation/delivery attempt and exits. It contains no polling loop.

## Production Scheduling

The required cron expression in UTC is:

```text
0 23 * * *
```

GitHub Actions runs this schedule through `.github/workflows/prediction-daily.yml`. The workflow also supports `workflow_dispatch` for a controlled manual test.

Required GitHub Actions secrets are:

- `DATABASE_URL`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBAPP_URL`

`DATABASE_URL` must be the externally reachable Render Postgres connection URL for the same `amen-postgres` database used by the web service. Render's internal-only database hostname is not reachable from GitHub-hosted runners.

The workflow explicitly supplies the same non-secret SportyBet production values used by the Render web service. It does not need `TELEGRAM_WEBHOOK_SECRET`, because the prediction runner sends Telegram messages but does not receive webhook updates.

## Telegram Webhook

The production webhook route is:

```text
/api/v1/telegram/webhook
```

Requests are authenticated with Telegram's `X-Telegram-Bot-Api-Secret-Token` header. `TELEGRAM_WEBHOOK_SECRET` must be a cryptographically strong value, at least 32 characters long, and must not equal the bot token.

After the Render environment contains `TELEGRAM_WEBHOOK_URL` and `TELEGRAM_WEBHOOK_SECRET`, register the webhook once from an environment with the production credentials:

```bash
python -m app.telegram.set_webhook
```

`TELEGRAM_WEBHOOK_URL` must be the public Render HTTPS URL ending in `/api/v1/telegram/webhook`. Use `python -m app.telegram.set_webhook --status` to inspect the registered webhook without printing credentials.

## Daily Output

One successful run sends two separate Telegram messages:

1. `OVER 1.40–1.50 GAMES — TODAY & TOMORROW`
2. `OVER 1.40–1.50 PREDICTIONS — TODAY & TOMORROW`

Each successful booking code has a native Telegram `copy_text` button. The copied payload contains only the exact booking code.

## Prediction Grouping

For each Nigeria-time day independently:

1. Sort model-selected predictions chronologically.
2. Break ties by the exact SportyBet event ID and full selection identity.
3. Assign alternating indices to at most two groups.
4. Keep each group chronologically ordered.
5. Keep groups disjoint.
6. Book each non-empty group through the existing validated SportyBet booking primitive.

Examples:

```text
0 predictions  -> no groups
1 prediction   -> one group
2 predictions  -> two groups of one
3 predictions  -> groups of two and one
4 predictions  -> two groups of two
5 predictions  -> groups of three and two
```

No fixture is duplicated and no fixture is silently discarded. If a group exceeds the existing 50-selection batching limit, that group may contain multiple 50-selection booking batches. The existing 50-selection limit remains an unresolved verification item.

## Delivery Idempotency

Each category has a separate deterministic delivery identity derived from:

- generation identity
- Nigeria timezone and date window
- input identity
- message category
- relevant booking batch identities
- relevant booking codes

The `prediction_message_deliveries` table stores:

- category
- delivery identity
- batch identities
- booking codes
- exact Telegram payload
- status
- sent timestamp
- safe error summary
- retry count
- claim expiry

No Telegram token or credential is stored.

If one message succeeds and the other fails, the successful message is not sent again. A later invocation retries only the failed message and reuses the persisted booking result and payload.

## Message Size

Each message is checked independently against the existing Telegram UTF-16 safe limit. An oversized message is marked failed with a structured message-too-large error. The formatter does not truncate, sample, or silently remove fixtures or groups.

## Evidence Limitation

The current football evidence provider remains an offline sanitized capture provider and cannot supply arbitrary live fixtures. The command therefore uses an explicit no-op evidence provider by default. This keeps qualifying-game delivery honest while the prediction message reports that no model-selected predictions are available.

When a general legitimate evidence provider is introduced in a future phase, it can replace the no-op provider without changing the scheduler, booking, grouping, or delivery boundary.
