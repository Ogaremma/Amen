# Daily Rolling Prediction Delivery

## Timezone

The prediction system has no prediction-specific timezone setting. Daily rolling generation therefore uses **UTC** explicitly. It does not use the server-local timezone.

Each run derives:

- `TODAY` from the current UTC calendar date.
- `TOMORROW` from `TODAY + 1 calendar day`.

The implementation reuses the existing UTC-normalized `today_tomorrow_window()` helper.

## Generation and delivery identities

The generation identity is a deterministic SHA-256 representation of:

- timezone name
- today
- tomorrow

The delivery identity is a deterministic SHA-256 representation of:

- generation identity
- input identity
- ordered qualifying booking batch identities
- ordered prediction booking batch identities

The input identity includes ordered evaluation identities and the user-visible values that can change the message, such as odds, teams, selection state, evidence score, and explanation signals. Booking codes are deliberately excluded so a retry can reuse the same codes.

## State

`prediction_generation_deliveries` stores one generation/delivery attempt record with:

- generation, input, and delivery identities
- timezone and dates
- generated and delivered timestamps
- state
- safe error summary
- full structured booking-result snapshot
- qualifying and prediction batch identities
- successful booking codes

States are:

- `generating`
- `generated`
- `delivery_pending`
- `delivered`
- `delivery_failed`
- `generation_failed`

A database claim on the generation identity prevents concurrent duplicate delivery. The claim has a five-minute TTL so a crashed process does not block the day permanently.

If a generated result exists but Telegram delivery failed or was interrupted, a retry sends the persisted result. It does not regenerate booking codes first.

## Booking batching

The existing Phase 4D service continues to batch selections in groups of 50. This limit is derived from the existing project compilation constraint and has not yet been conclusively verified as the actual SportyBet API maximum. No batching redesign is included in Phase 4F.
