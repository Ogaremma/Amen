# Prediction Production Scheduling

## Status

Phase 4I provides a bounded, scheduler-compatible command and database-backed idempotency. It does **not** start a hidden scheduler inside the FastAPI web process.

The current `render.yaml` defines one Docker web service. That service is not, by itself, a guaranteed daily scheduler. Automatic 00:00 Africa/Lagos execution requires a separately enabled Render Cron Job or another external scheduler.

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
python -m app.prediction_daily_runner --chat-id <telegram-chat-id>
```

It can also read:

```text
TELEGRAM_PREDICTION_CHAT_ID
```

The command performs one generation/delivery attempt and exits. It contains no polling loop.

## Render Scheduling

The required cron expression in UTC is:

```text
0 23 * * *
```

A future Render Cron Job would conceptually use:

```yaml
services:
  - type: cron
    name: amen-prediction-daily
    runtime: docker
    rootDir: backend
    dockerfilePath: ./Dockerfile
    dockerContext: .
    schedule: "0 23 * * *"
    command: python -m app.prediction_daily_runner
    envVars:
      - key: DATABASE_URL
        sync: false
      - key: TELEGRAM_BOT_TOKEN
        sync: false
      - key: TELEGRAM_WEBAPP_URL
        sync: false
      - key: TELEGRAM_PREDICTION_CHAT_ID
        sync: false
```

This cron service has **not** been added to `render.yaml` in Phase 4I. The current deployment therefore does not yet guarantee automatic daily execution.

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
