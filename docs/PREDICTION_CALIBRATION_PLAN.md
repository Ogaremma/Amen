# Prediction Scoring And Calibration Plan

## Status

Amen currently has two explicit scoring model versions:

- `baseline-v1`: the original market and data-quality score.
- `evidence-v1`: an additive evidence-aware ranking score.

`evidence-v1` is a **ranking and selection score**. It is not a calibrated probability, a guaranteed outcome measure, or a win percentage. No historical outcome dataset has yet been used to calibrate it.

## evidence-v1 Formula

The normalized score is constrained to `0.0` through `1.0`:

```text
evidence_v1 =
    market_quality_component
  + provider_probability_component
  + historical_over_1_5_component
  + goal_production_component
  + venue_context_component
```

The component weights are:

| Component | Weight |
| --- | ---: |
| Market quality | 0.35 |
| SportyBet provider probability | 0.15 |
| Historical Over 1.5 evidence | 0.25 |
| Goal production | 0.15 |
| Venue context | 0.10 |

### Market Quality

An invalid, inactive, banned, incomplete, or out-of-range market receives zero market credit.

For a valid market:

```text
market_quality = 0.70 + 0.30 * odds_proximity_to_1.40
```

Odds proximity is 1.0 at `1.40` and declines linearly to 0.0 immediately before the exclusive upper bound of `1.50`.

### Provider Probability

If SportyBet supplies a valid decimal probability `p`:

```text
provider_component = 0.15 * p
```

The value is labeled as a SportyBet provider probability. It is not recalibrated and is not an Amen-generated probability. Missing or invalid values contribute zero and are reported as missing or invalid signals.

### Historical Over 1.5

For each team, the model selects:

- last-10 Over 1.5 rate when at least five valid matches are available;
- otherwise, the last-5 rate.

A sample-reliability factor is applied:

```text
last_10_reliability = min(sample_size, 10) / 10
last_5_reliability = min(sample_size, 5) / 5
team_signal = over_1_5_rate * reliability
```

The component uses the mean of the home and away team signals. This prevents a rate based on two or three matches from receiving the same weight as a rate based on eight or ten matches.

### Goal Production

For each team, the model estimates recent average total goals:

```text
expected_total_goals = goals_scored_average + goals_conceded_average
```

It prefers recent last-10 or last-5 averages and falls back to season aggregates only when recent goal averages are unavailable. The value is normalized against 2.5 total goals and multiplied by sample reliability:

```text
goal_signal = min(expected_total_goals / 2.5, 1.0) * reliability
```

The component uses the mean of the home and away goal signals.

### Venue Context

Only the home team's recent home split and the away team's recent away split are used. All-match form is not substituted for venue-specific evidence.

Venue rates are weighted by venue sample reliability, capped at five matches.

## Evidence Quality

Evidence quality describes availability and reliability, not prediction strength.

| Quality | Rule |
| --- | --- |
| `complete` | Both teams have at least five valid recent matches, venue splits, season statistics, provider probability, competition context, and complete Phase 4B evidence quality. |
| `partial` | Both teams have at least three valid recent matches, but at least one completeness condition is missing. |
| `insufficient` | Either team has fewer than three valid recent matches, evidence is missing, stale, or contains a result at or after prediction kickoff. |

The football-evidence multiplier is:

- `complete`: 1.00
- `partial`: 0.75
- `insufficient`, stale, or look-ahead-contaminated: 0.00

Market quality and provider probability remain independent of the football-evidence multiplier.

## Selection Policy

Default `evidence-v1` policy:

- minimum score: `0.70`
- minimum evidence quality: `partial`
- minimum valid recent sample per team: `3`

The system does not force a fixed number of selections. It may return zero, two, or any other number of selected candidates. It does not pad results with weaker fixtures.

## Explanation Output

Every `evidence-v1` evaluation records:

- positive signals
- negative signals
- missing signals
- evidence quality
- final ranking score
- selection decision

Explanations use actual feature values and sample sizes. They do not use certainty, guarantee, or sure-win language.

## Leakage Protection

Prediction-time scoring is protected as follows:

1. Historical match rows with kickoff at or after the selected fixture kickoff are discarded.
2. Historical match rows with kickoff after the evidence capture timestamp are discarded.
3. Evidence must be within its provider max age.
4. The evaluator independently rejects stale evidence.
5. The evaluator independently rejects evidence containing a future-result row.
6. `actual_goals`, `actual_result`, and `settled_at` are settlement fields and are not fields in `PredictionFeatureSnapshot`.
7. The scoring engine accepts only the candidate and prediction-time evidence; it does not read the prediction database or settled outcomes.

## Persistence

Each selected prediction retains:

- exact six-field SportyBet selection identity
- model version
- baseline score
- evidence score
- evidence quality
- active model score
- confidence basis
- provider probability
- exact provider probability source value
- structured explanation
- full feature snapshot
- odds at prediction
- pending status
- eventual `actual_goals`
- eventual `actual_result`
- eventual `settled_at`

Historical model scores are not overwritten when a new model version is introduced.

## Future Calibration Process

```text
Prediction generated
        ↓
prediction stored
        ↓
match settles
        ↓
actual result recorded
        ↓
predicted score/provider probability compared with outcome
        ↓
calibration metrics calculated
        ↓
model version evaluated
        ↓
new model version only if evidence supports it
```

Future calibration should evaluate:

- hit rate
- Brier score
- log loss
- calibration curve
- precision among selected predictions
- performance by odds band
- performance by evidence completeness
- performance by feature regime

Automatic retraining is not implemented. A calibrated probability model must not be introduced until a sufficiently large settled-outcome dataset exists and temporal validation prevents look-ahead bias.

## Phase 4H Performance Analysis

Performance analysis is a read-only operation computed from persisted prediction and settlement records. It does not rewrite odds, provider probabilities, evidence scores, feature snapshots, evidence snapshots, model versions, booking metadata, generation metadata, or settlement fields.

The default sample policy is configurable:

| Purpose | Default |
| --- | ---: |
| Minimum group sample | 5 settled predictions |
| Minimum calibration-analysis sample | 30 settled predictions |
| Minimum candidate-evaluation sample | 100 settled predictions |

Only `settled_win` and `settled_miss` contribute to win-rate metrics. Pending, live, unresolved, postponed, cancelled, abandoned, and other non-result states are counted separately and never treated as misses.

Win-rate intervals use the Wilson score interval at 95% confidence. A zero-settled sample returns `win_rate: null` rather than zero.

Evidence-v1 remains a ranking score, not a probability. It is analyzed by score bands and observed win rate, but no Brier score is calculated for the raw evidence score. SportyBet provider probability is analyzed separately as a provider probability signal and may receive a Brier score because its source semantics are probability-like. Provider probability is never replaced with `1 / odds`.

The analyzer also reports:

- average and median evidence score with sample size;
- average odds with sample size;
- performance by evidence score band;
- performance by odds band;
- performance by evidence quality;
- performance by model version;
- performance by Africa/Lagos kickoff date;
- performance by competition, with insufficient-sample labeling;
- provider-probability reliability bins and Brier score;
- prediction-pool versus qualifying-pool observed difference when qualifying outcomes are available.

Qualifying-pool outcomes are not currently persisted, so the qualifying comparison remains unavailable rather than being reconstructed from prediction-pool records.

When at least 100 settled predictions exist, future candidate evaluation must use a chronological 60/20/20 training/validation/holdout split. The split is never shuffled. Phase 4H does not generate or activate a candidate model; `evidence-v1` remains the active model until a separately validated future version is introduced.
