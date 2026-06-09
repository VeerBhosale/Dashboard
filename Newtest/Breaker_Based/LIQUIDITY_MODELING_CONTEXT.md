# Breaker + FVG Liquidity Modeling Context

This document explains the market-structure data now produced by the Breaker + FVG dashboard experiment and how we plan to use it for future liquidity-pool modeling, especially an XGBoost model for `SSL` / `BSL` strength.

The goal is not to replace the current Breaker + FVG signal logic. The goal is to build a richer structure layer around the signal so we can score liquidity quality, target quality, and trade environment quality.

## Current Modeling Direction

The first ML target should be narrower than full trade success:

```text
Predict whether a BSL / SSL liquidity pool is strong, engineered, and likely to attract price into a sharp raid.
```

This is not the full Breaker + FVG trade-quality model.

The first model should validate one strategy component only:

```text
What does a strong engineered liquidity level look like before price takes it?
```

A strong liquidity pool is not defined by whether price reverses after the sweep. For this model, direction after the sweep is secondary.

The key behavior is:

```text
Price is attracted toward the level, reaches it with urgency, and cuts through it sharply.
```

This means `SSL` and `BSL` should be treated symmetrically:

- Strong `SSL`: price is drawn downward into sell-side liquidity and raids through the pool sharply.
- Strong `BSL`: price is drawn upward into buy-side liquidity and raids through the pool sharply.

This is better than immediately modeling full trade quality because the strategy depends heavily on two separate liquidity-strength questions:

- Was the swept sell-side liquidity actually strong and engineered?
- Is the buy-side target liquidity actually strong enough to attract delivery?

If we can score these pools well, full trade scoring becomes more modular and less black-box.

Later, the strategy can ask separate questions:

```text
Did a long setup sweep a strong SSL pool?
Does the long setup have a strong BSL target above?
```

The liquidity model itself should not decide whether the whole trade is good.

## Key Concepts

### SSL

`SSL` means sell-side liquidity. It is built from lows where stop liquidity is likely resting.

Sources:

- `SL`: raw swing lows.
- `ISL`: confirmed intermediate swing lows.
- Clusters of nearby `SL + ISL`.
- Raw `SL` inside / near a bullish FVG, if it passes validity rules.

Use:

- Potential sweep location.
- Potential engineered liquidity raid location.
- Quality input for long Breaker + FVG signals.

### BSL

`BSL` means buy-side liquidity. It is built from highs where stop liquidity or breakout liquidity is likely resting.

Sources:

- `SH`: raw swing highs.
- `ISH`: confirmed intermediate swing highs.
- Clusters of nearby `SH + ISH`.
- Raw `SH` inside / near a bearish FVG, if it passes validity rules.

Use:

- Potential target liquidity.
- Potential place where swept sell-side inventory may be delivered.
- Quality input for long Breaker + FVG target assessment.

## Data Produced By The Current Implementation

The main dashboard payload is written to:

```text
breaker_fvg_dashboard_data.js
```

Each ticker contains the following useful structures.

## `candles`

The OHLC candles used for analysis.

Useful for:

- Forward outcome labeling.
- Measuring approach, sweep, penetration, and cut-through behavior.
- Measuring target hit / stop hit.
- Measuring candle displacement, volatility, and regime.

Important fields:

- `time`
- `open`
- `high`
- `low`
- `close`

## `swings`

Raw swing highs and swing lows.

Useful for:

- Building raw liquidity levels.
- Measuring market skeleton: `HH`, `HL`, `LH`, `LL`.
- Detecting local stop pools.

Important fields:

- `time`
- `type`: `high` or `low`
- `price`

## `level_ledgers`

This is one of the most important model inputs.

It stores:

- `swing_highs`
- `swing_lows`
- `ish`
- `isl`
- `active_buy_levels`
- `active_sell_levels`
- `visual_buy_levels`
- `visual_sell_levels`

### Raw Swing Levels

Raw levels are `SH` and `SL`.

Important fields:

- `type`: `SH` or `SL`
- `side`: `buy` or `sell`
- `time`
- `price`
- `active`
- `breached_at`
- `age_bars`
- `dual_swing`
- `fvg_context`
- `fvg_liquidity_valid`
- `raw_fvg_survival_valid`

### Confirmed Intermediate Levels

Intermediate levels are `ISH` and `ISL`.

Definitions:

```text
ISL:
SL_left.price > SL_candidate.price
AND
SL_right.price > SL_candidate.price

ISH:
SH_left.price < SH_candidate.price
AND
SH_right.price < SH_candidate.price
```

Important distinction:

The candidate cannot be confirmed when it first forms because the right-side same-type swing does not exist yet. Confirmation happens later during the walk-forward process.

Important fields:

- `type`: `ISH` or `ISL`
- `source_time`
- `confirmation_time`
- `time`
- `price`
- `active`
- `breached_at`
- `age_bars`
- `fvg_context`
- `fvg_liquidity_valid`

## `fvg_context`

Each relevant level can carry FVG relationship metadata.

Important fields:

- `relation`: `inside`, `near`, or `none`
- `remaining_relation`: `inside`, `near`, or `none`
- `fvg_id`
- `fvg_type`: `bullish` or `bearish`
- `fvg_created_time`
- `fvg_age_bars`
- `distance_atr`
- `remaining_distance_atr`
- `remaining_lower`
- `remaining_upper`

Interpretation:

- `inside`: level is inside the original FVG.
- `near`: level is within ATR proximity tolerance of the original FVG.
- `remaining_relation`: whether the level is inside / near the unfilled part of the FVG at the time the level formed.

## Raw FVG-Backed Swing Validity

Raw `SH` / `SL` levels have stricter rules before becoming standalone `BSL x1` / `SSL x1`.

Rules:

- Raw swing must form within 7 candles after the FVG is created.
- Raw swing must survive 5 subsequent candles without breach.
- If fewer than 5 later candles are available, the raw FVG-backed swing is pending, not confirmed.

These rules apply only to raw `SH` / `SL`.

They do not invalidate confirmed `ISH` / `ISL` interpretations of the same physical candle.

## Ordinary Liquidity Clusters

Ordinary clusters are not the same as standalone FVG-backed levels.

Sell-side clusters can combine:

```text
SL + ISL
```

Buy-side clusters can combine:

```text
SH + ISH
```

FVG backing is not required for ordinary multi-point clusters.

Cluster rules:

- Levels must be within adaptive price tolerance.
- The older level must still be resting when the newer level forms.
- If the newer swing is printed on the same candle that sweeps the existing cluster boundary, it cannot join the consumed pool unless the overshoot is tiny.
- Tiny grace currently used: `0.03 ATR`.

This prevents sweep-created extrema from being counted as part of the liquidity they just consumed.

## `liquidity_clusters`

This is the main table-like structure for future liquidity-pool modeling.

It contains:

- `buy`: BSL clusters.
- `sell`: SSL clusters.

Important fields:

- `side`
- `lower`
- `upper`
- `visual_lower`
- `visual_upper`
- `midpoint`
- `level_count`
- `ish_count`
- `isl_count`
- `sh_count`
- `sl_count`
- `oldest_time`
- `newest_time`
- `end_time`
- `first_taken_time`
- `last_taken_time`
- `age_bars`
- `density`
- `fvg_backed_count`
- `fvg_member_count`
- `fvg_ids`
- `active`

Potential model features:

- Pool width in ATR.
- Pool density.
- Number of levels in pool.
- Raw swing count.
- Intermediate level count.
- FVG-backed count.
- FVG member count.
- Age before sweep.
- Time between first and last level formation.
- Whether pool was active at signal time.
- Distance from current price.
- Distance to opposite-side liquidity.

## `fvg_ledgers`

Stores all bullish and bearish FVGs.

Important fields:

- `id`
- `type`
- `created_time`
- `candle_1_time`
- `candle_3_time`
- `original_lower`
- `original_upper`
- `remaining_lower`
- `remaining_upper`
- `partial_fill_pct`
- `original_size_atr`
- `remaining_size_atr`
- `displacement_atr`
- `distance_to_latest_close_atr`
- `valid_for_structure`
- `valid_for_visual`
- `active`
- `partial_touched_at`
- `filled_at`
- `age_bars`

Use:

- Determine whether liquidity formed inside / near imbalance.
- Measure FVG quality.
- Track whether price swept into an unfilled or previously traversed imbalance.
- Identify internal liquidity retests after displacement.

## FVG Box Display Rules

The dashboard does not show every FVG.

Currently shown:

- Active structural FVGs within ATR distance from current price.
- Nearest active bullish FVG.
- Nearest active bearish FVG.
- Active FVGs that back valid `SSL` / `BSL`.

Visual display is not the same as data availability. Hidden FVGs may still exist in `fvg_ledgers` and remain useful for modeling.

## `market_skeleton`

Captures broad swing structure.

Labels:

- `HH`
- `HL`
- `LH`
- `LL`
- `EH`
- `EL`

Potential uses:

- Trend regime.
- Compression / expansion context.
- Whether the pool forms in trend, range, transition, compression, or expansion.

Model features:

- Recent skeleton state.
- Last high type.
- Last low type.
- Number of HH / HL / LH / LL in last N swings.

## `range_state`

Range context is based on confirmed `ISL` / `ISH` boundaries.

Important fields:

- `lower`
- `upper`
- `midpoint`
- `duration`
- `width`
- `width_atr`
- `inside_close_ratio`
- `break_side`
- `quality`
- `internal_isl_count`
- `internal_ish_count`

Use:

- Premium / discount context.
- Compression detection.
- Knowing whether liquidity is forming inside a bounded structure.

## `premium_discount`

Describes where current price sits inside the active / recent range.

Potential uses:

- Long setups in discount may be higher quality.
- Sweeps below range midpoint into bullish FVG may matter more.
- BSL targets in premium may act as cleaner delivery objectives.

## `target_liquidity_landscape`

Summarizes nearby opposite-side liquidity.

For long-side Breaker + FVG trades, buy-side liquidity above price is especially important.

Potential features:

- Nearest BSL distance.
- Strongest BSL cluster above.
- Number of BSL clusters above.
- Distance to nearest SSL below.
- Unresolved sell-side risk below.

## `event_timeline`

Recent structure events.

Examples:

- Swing created.
- ISL / ISH confirmed.
- FVG created.
- Level breached.

Potential use:

- Building sequential features.
- Giving an agent more context.
- Studying whether liquidity forms slowly or suddenly.

## Proposed XGBoost Use Case: Engineered Liquidity Strength

Each `SSL` / `BSL` cluster becomes one training row.

The first model should predict the strength score of engineered liquidity:

```text
Strong liquidity = price is attracted toward the pool and cuts through it with force.
```

The model should not require reversal after the sweep.

### Symmetric Label Definition

```text
For SSL:
price approaches from above, reaches the sell-side pool, and trades below the pool with displacement.

For BSL:
price approaches from below, reaches the buy-side pool, and trades above the pool with displacement.
```

Direction-specific terms can be normalized so both sides share one model:

```text
pool_direction = -1 for SSL, +1 for BSL
distance_to_pool = directional distance from current price to pool boundary
cut_through = directional penetration beyond the pool
approach_move = directional movement toward the pool before sweep
```

### First Continuous Success Metric

```text
liquidity_strength_score = 0 to 100
```

This should be the primary target for the first model.

Reason:

Liquidity strength is not naturally a yes/no concept. A pool can be ignored, drifted into, tapped, raided cleanly, or violently cut through. Those are degrees of strength.

The score should be built from separate observable components:

```text
liquidity_strength_score =
  attraction_component
+ speed_component
+ penetration_component
+ displacement_component
+ optional_volume_component
```

Keep the raw components in the dataset. If the weighting is wrong, we can revise the score without recollecting data.

### Diagnostic Buckets

The continuous target can be bucketed for review:

```text
0-20    ignored / unavailable / very weak
20-40   weak attraction or slow drift
40-60   moderate liquidity, reached but not sharp
60-80   strong liquidity, clean attraction and raid
80-100  very strong liquidity, sharp magnetic cut-through
```

These buckets are for interpretation and model evaluation. They should not replace the raw continuous target.

### Avoid Subjective Labels

Do not label pools as strong because they look beautiful after the fact.

Use mechanical outcomes:

- Swept or not swept.
- Time to sweep.
- Speed of movement toward the pool.
- Penetration through the pool.
- Displacement into and through the pool.
- Volume participation during the approach / sweep, if available.
- Whether price cuts through cleanly or only taps / drifts into the level.

Human judgment should define the measurement rules, not hand-pick the answer.

## Feature Catalog

This section separates:

- **Existing payload features**: already produced by `breaker_fvg_dashboard_export.py`.
- **Engineered from existing data**: can be calculated in the dataset export from existing candles / ledgers.
- **New fields to add later**: should eventually be produced directly by the structure engine if useful.

For v1, prefer features available at `newest_time`, the moment the liquidity pool is fully formed. Outcome columns can use future data, but model input features must not.

### Pool Construction Features

These describe how the liquidity pool was built.

#### Existing Payload Features

`side`

- Calculation: Existing cluster field. `buy` means `BSL`, `sell` means `SSL`.
- Use: Lets one model learn mirrored behavior, or lets us split into separate `BSL` and `SSL` models.
- Why helpful: Some markets may raid upside and downside pools differently, but the core engineered-liquidity idea is symmetric.

`level_count`

- Calculation: Existing cluster field, count of distinct physical swing points in the pool.
- Use: Direct measure of how many highs/lows are stacked at the level.
- Why helpful: More visible repeated highs/lows can make the pool more obvious and attractive.

`sh_count` / `sl_count`

- Calculation: Existing cluster fields. Raw swing highs for `BSL`, raw swing lows for `SSL`.
- Use: Measures how much of the pool comes from raw swing structure.
- Why helpful: Raw swing levels may create obvious retail stop pools, especially when equal or tightly spaced.

`ish_count` / `isl_count`

- Calculation: Existing cluster fields. Confirmed intermediate highs/lows in the pool.
- Use: Measures structural significance.
- Why helpful: Intermediate levels are stronger than raw local swings because they survived same-type confirmation.

`density`

- Calculation: Existing cluster field: `level_count / price_width`, using a minimum width buffer.
- Use: Measures how tightly packed the pool is.
- Why helpful: Tight clusters of levels may look cleaner and more engineered than wide loose pools.

`oldest_time`

- Calculation: Existing cluster field, earliest member time.
- Use: Start of pool formation.
- Why helpful: Used to measure how long the market has been building the pool.

`newest_time`

- Calculation: Existing cluster field, latest member time.
- Use: Recommended feature snapshot time for v1.
- Why helpful: At this point the cluster exists and can be evaluated without future leakage.

`age_bars`

- Calculation: Existing cluster field based on member ages in the current export window.
- Use: Rough age proxy.
- Why helpful: Older pools may be more visible, but very stale pools may be less relevant.

`active`

- Calculation: Existing cluster field, true when the cluster has not yet been taken by latest candle.
- Use: Useful for dashboard scoring; for training at formation time, do not use current export-time `active` directly unless snapshot-corrected.
- Why helpful: Live model output should only score active pools, but historical training should avoid using future-active status as an input.

#### Engineered From Existing Data

`width`

- Calculation: `upper - lower`.
- Use: Raw pool band size.
- Why helpful: Narrow pools are easier to raid sharply; very wide pools may represent a zone rather than a precise engineered level.

`width_atr`

- Calculation: `(upper - lower) / ATR_at_snapshot`.
- Use: Volatility-normalized pool width.
- Why helpful: A 5-point pool is different on a high-volatility stock than on a low-volatility stock.

`time_span_bars`

- Calculation: candle index difference between `oldest_time` and `newest_time`.
- Use: Measures how long the pool took to form.
- Why helpful: Slowly engineered pools and quickly stacked pools may behave differently.

`formation_speed`

- Calculation: `level_count / max(time_span_bars, 1)`.
- Use: Measures how quickly repeated liquidity points appeared.
- Why helpful: Fast repeated equal highs/lows can show intentional compression or urgent liquidity engineering.

`density_atr`

- Calculation: `level_count / max(width_atr, small_value)`.
- Use: ATR-normalized cluster density.
- Why helpful: Cleaner than raw `density` when comparing stocks with different prices and volatility.

`raw_to_intermediate_ratio`

- Calculation for `BSL`: `sh_count / max(ish_count, 1)`. For `SSL`: `sl_count / max(isl_count, 1)`.
- Use: Separates local swing liquidity from structural liquidity.
- Why helpful: A pool made of both raw and intermediate levels may be more meaningful than one made of only minor swings.

`intermediate_share`

- Calculation for `BSL`: `ish_count / level_count`. For `SSL`: `isl_count / level_count`.
- Use: Measures structural quality share.
- Why helpful: Higher intermediate share may indicate a stronger structural pool.

`equal_level_tightness_atr`

- Calculation: standard deviation of member prices divided by ATR.
- Use: Measures equal-high/equal-low cleanliness.
- Why helpful: A strong engineered pool often looks visually obvious because levels line up tightly.

`max_member_gap_atr`

- Calculation: largest gap between sorted member prices divided by ATR.
- Use: Detects whether a cluster has a loose internal gap.
- Why helpful: Pools with one far member may be less clean than compact pools.

`member_recency_balance`

- Calculation: compare bars from `oldest_time` to `newest_time` against bars from `newest_time` to snapshot.
- Use: Measures whether the pool is newly completed or old/stale.
- Why helpful: A freshly completed pool near current price may attract price differently than a stale historical level.

`member_age_bars_min`

- Calculation: for every high/low member in the `BSL` / `SSL` cluster, calculate bars from member candle time to pool breach time if breached, otherwise to latest available candle. Take the minimum.
- Use: Measures youngest constituent age.
- Why helpful: A cluster with a fresh final member may behave differently from a cluster made only of old levels.
- Note: For live prediction at formation time, use age to `snapshot_time`; for outcome analysis, also store age to breach.

`member_age_bars_max`

- Calculation: same member-level age calculation, take the maximum.
- Use: Measures oldest constituent age.
- Why helpful: Very old levels can be highly visible, but may also become stale. This lets the model learn the relationship.

`member_age_bars_mean`

- Calculation: average age across all distinct physical high/low members.
- Use: Cluster-level average age.
- Why helpful: Better than a single cluster age when `BSL xN` / `SSL xN` contains points formed over a long span.

`member_age_bars_std`

- Calculation: standard deviation of member ages.
- Use: Measures whether the cluster formed all at once or over many separated attempts.
- Why helpful: A tight age distribution may represent compact engineering; a wide distribution may represent historical resting liquidity.

`member_age_to_breach_min` / `member_age_to_breach_mean` / `member_age_to_breach_max`

- Calculation: if the pool is swept, calculate bars from each member time to the sweep candle.
- Use: Outcome diagnostics, not live input for formation-time model.
- Why helpful: Directly captures your "candles till breach" idea for the whole cluster and for each constituent.

`cluster_count`

- Calculation: existing `level_count`, displayed as `BSL xN` or `SSL xN`.
- Use: Primary count feature.
- Why helpful: This is one of the cleanest direct markings-based features. `x1`, `x2`, and `x5` should not be treated the same.

`constituent_type_mask`

- Calculation: encode whether the cluster contains raw swing levels, intermediate levels, or both. Examples: `raw_only`, `intermediate_only`, `mixed`.
- Use: Categorical feature derived from `SH`/`SL` and `ISH`/`ISL` counts.
- Why helpful: A mixed pool may carry both visual liquidity and structural liquidity.

`constituent_type_weighted_score`

- Calculation: example score `1.0 * raw_count + 1.5 * intermediate_count`, normalized by `level_count`. Weights are research defaults only.
- Use: Optional heuristic feature.
- Why helpful: Lets us test whether confirmed `ISL` / `ISH` constituents are more important than raw `SL` / `SH`.

### FVG Relationship Features

These describe whether the liquidity formed inside or near imbalance.

#### Existing Payload Features

`fvg_backed_count`

- Calculation: Existing cluster field, count of unique pool members that are valid FVG-backed liquidity.
- Use: Measures direct FVG backing.
- Why helpful: Liquidity engineered around imbalance may be more likely to attract a sharp raid.

`fvg_member_count`

- Calculation: Existing cluster field, count of members eligible for FVG-based cluster membership.
- Use: Measures broader FVG relationship, including traversed original FVG membership.
- Why helpful: A pool can still be associated with an original imbalance even if not every member is a standalone FVG-backed anchor.

`fvg_ids`

- Calculation: Existing cluster field, distinct FVG ids linked to pool members.
- Use: Join key into `fvg_ledgers`.
- Why helpful: Lets us attach FVG quality, displacement, remaining zone, and age.

`fvg_context` on levels

- Calculation: Existing level metadata.
- Use: Count `inside`, `near`, `remaining_relation`, and distance values across pool members.
- Why helpful: Inside remaining imbalance may matter differently than merely near an old original FVG.

#### Engineered From Existing Data

`fvg_backed_ratio`

- Calculation: `fvg_backed_count / level_count`.
- Use: Measures how much of the pool has direct FVG validation.
- Why helpful: A pool with all members FVG-backed may be stronger than one with a single FVG-backed member.

`fvg_member_ratio`

- Calculation: `fvg_member_count / level_count`.
- Use: Broader FVG-membership share.
- Why helpful: Captures pools formed around imbalance even when strict standalone validation is not present.

`inside_fvg_count`

- Calculation: count pool members where `fvg_context.relation == inside`.
- Use: Measures original FVG containment.
- Why helpful: Liquidity inside imbalance may show engineered stops around inefficient price.

`near_fvg_count`

- Calculation: count pool members where `fvg_context.relation == near`.
- Use: Measures ATR-proximity to original FVG.
- Why helpful: Near-FVG liquidity may still attract price while being structurally distinct.

`remaining_inside_count`

- Calculation: count members where `fvg_context.remaining_relation == inside`.
- Use: Measures relation to currently unfilled imbalance at member formation.
- Why helpful: Remaining unfilled FVG liquidity may be more active than liquidity in already traversed FVG portions.

`remaining_near_count`

- Calculation: count members where `fvg_context.remaining_relation == near`.
- Use: Measures proximity to active remaining imbalance.
- Why helpful: Helps separate fresh imbalance context from old imbalance memory.

`nearest_fvg_distance_atr`

- Calculation: minimum absolute `distance_atr` or `remaining_distance_atr` across member contexts.
- Use: Distance-to-imbalance feature.
- Why helpful: The closer the pool is to imbalance, the more likely it may be part of a deliberate liquidity draw.

`avg_fvg_original_size_atr`

- Calculation: join `fvg_ids` to `fvg_ledgers`, average `original_size_atr`.
- Use: Measures size of related imbalance.
- Why helpful: Larger imbalances may imply stronger prior displacement and stronger later liquidity behavior.

`avg_fvg_remaining_size_atr`

- Calculation: average `remaining_size_atr` for related FVGs at snapshot.
- Use: Measures unfilled imbalance still available.
- Why helpful: Remaining inefficiency can act as a magnet or context for liquidity formation.

`avg_fvg_displacement_atr`

- Calculation: average `displacement_atr` for related FVGs.
- Use: Measures force of original imbalance creation.
- Why helpful: Liquidity around high-displacement FVGs may be more meaningful.

`avg_fvg_age_bars`

- Calculation: average bars between FVG creation and pool snapshot.
- Use: Measures freshness of FVG context.
- Why helpful: Fresh imbalance-backed liquidity may behave differently from old imbalance-backed liquidity.

`avg_fvg_partial_fill_pct`

- Calculation: average `partial_fill_pct` for related FVGs.
- Use: Measures how much related FVGs have already been consumed.
- Why helpful: Heavily filled FVG context may be weaker than fresh remaining imbalance.

`inside_fvg_flag`

- Calculation: true if at least one cluster member has `fvg_context.relation == inside` or `fvg_context.remaining_relation == inside`.
- Use: Direct `+FVG` marking feature.
- Why helpful: Matches the visual observation that a `BSL` / `SSL` inside FVG context may be a special class of engineered liquidity.

`all_members_inside_fvg_flag`

- Calculation: true if every distinct physical member in the cluster is inside an associated FVG.
- Use: Stronger version of `inside_fvg_flag`.
- Why helpful: A fully FVG-contained cluster may be cleaner than a cluster with only one FVG-related member.

`fvg_position_percentile_mean`

- Calculation for each FVG-backed member, map member price into its related FVG as a 0-100 position, then average.
- Use: Measures where inside the FVG the liquidity forms.
- Why helpful: Your observation is that not just "inside FVG" matters, but where inside the imbalance the level forms.
- Preferred representation: use continuous percentile first, then quartile buckets for review.

`fvg_position_quartile_mode`

- Calculation: convert `fvg_position_percentile` into quartiles: `Q1` 0-25, `Q2` 25-50, `Q3` 50-75, `Q4` 75-100. Take the most common quartile across members.
- Use: Categorical simplification for analysis.
- Why helpful: Quartiles are easier to read in diagnostics, but percentile preserves more information for the model.

FVG percentile direction rule:

```text
Bullish FVG:
  lower boundary = 0
  upper boundary = 100

Bearish FVG:
  upper boundary = 0
  lower boundary = 100
```

Interpretation:

For a bullish FVG, an `SSL` closer to the lower boundary has a lower percentile. For a bearish FVG, a `BSL` closer to the upper boundary has a lower percentile. This keeps "deeper into the directional FVG" directionally consistent.

Recommendation:

Use both:

- `fvg_position_percentile_mean` for the model.
- `fvg_position_quartile_mode` for human review.

Percentile is better for XGBoost because it keeps more signal. Quartile is better for summaries because it is easier to reason about.

### Location Features

These describe where the pool sits relative to price, range, and other liquidity.

#### Existing Payload Features

`range_state`

- Calculation: Existing ticker-level range list using confirmed `ISL` / `ISH` boundaries.
- Use: Attach the active or most recent range at the pool snapshot.
- Why helpful: Liquidity at range edges is often more visible and may attract sharper raids.

`premium_discount`

- Calculation: Existing ticker-level latest range position.
- Use: Useful for live latest context; for historical training, recompute at snapshot instead of using latest export value.
- Why helpful: BSL in premium and SSL in discount may represent external liquidity; internal pools may behave differently.

`target_liquidity_landscape`

- Calculation: Existing latest summary of nearby opposite-side liquidity.
- Use: Useful conceptually; for training, recompute opposite-side context at snapshot.
- Why helpful: Liquidity does not exist alone. A strong pool may be part of a path between opposing pools.

#### Engineered From Existing Data

`distance_from_close_atr`

- Calculation at snapshot: for `BSL`, `(pool_lower - close) / ATR`; for `SSL`, `(close - pool_upper) / ATR`.
- Use: Measures how far price must travel to reach the pool.
- Why helpful: Nearby pools may be taken quickly; far pools may require broader market drive.

`directional_distance_to_pool_atr`

- Calculation: same as above, clipped so pools already crossed are treated separately.
- Use: Side-normalized distance.
- Why helpful: Lets one model compare BSL/SSL symmetrically.

`range_position_pct`

- Calculation: `(pool_midpoint - range_low) / (range_high - range_low)` at snapshot.
- Use: Measures location inside active range.
- Why helpful: Pools near 0 or 1 are external range-edge liquidity; pools near 0.5 are internal.

`is_external_liquidity`

- Calculation: true when `BSL` is near/above range high or `SSL` is near/below range low.
- Use: Distinguishes external pools from internal pools.
- Why helpful: External pools may be stronger magnets because they sit beyond obvious range boundaries.

`is_internal_liquidity`

- Calculation: true when pool is inside active range boundaries.
- Use: Distinguishes internal cleanup liquidity.
- Why helpful: Internal pools may be raided often but with less displacement.

`distance_to_range_boundary_atr`

- Calculation: for `BSL`, distance to range high; for `SSL`, distance to range low, divided by ATR.
- Use: Measures boundary alignment.
- Why helpful: Liquidity aligned with a range boundary is more visible.

`distance_to_midpoint_atr`

- Calculation: absolute distance from pool midpoint to range midpoint divided by ATR.
- Use: Measures how far from fair value / midpoint the pool sits.
- Why helpful: Pools far from midpoint may require directional expansion to reach.

`nearest_opposite_pool_distance_atr`

- Calculation: distance from this pool midpoint to nearest active opposite-side pool midpoint at snapshot.
- Use: Measures opposing liquidity landscape.
- Why helpful: Strong delivery may occur when price moves from one clear pool toward another.

`same_side_pool_stack_count`

- Calculation: count same-side active pools within K ATR beyond/near this pool.
- Use: Measures stacked liquidity.
- Why helpful: Multiple nearby BSL or SSL pools can create a stronger magnet zone.

`same_side_stack_count_1atr` / `same_side_stack_count_2atr` / `same_side_stack_count_3atr`

- Calculation for `BSL`: count dashboard-visible BSL pools above the current BSL within 1, 2, and 3 ATR.
- Calculation for `SSL`: count dashboard-visible SSL pools below the current SSL within 1, 2, and 3 ATR.
- Use: Measures whether this pool is part of a nearby liquidity ladder.
- Why helpful: A stacked set of nearby pools may be more likely to get taken by the same directional leg.

`same_side_stack_total_levels_1atr` / `same_side_stack_total_levels_2atr` / `same_side_stack_total_levels_3atr`

- Calculation: sum `level_count` across same-side stacked pools within each ATR radius.
- Use: Measures total liquidity markings available beyond this pool.
- Why helpful: One nearby `BSL x4` is different from one nearby `BSL x1`.

`same_side_stack_density_2atr`

- Calculation: `same_side_stack_total_levels_2atr / furthest_stack_distance_atr_within_2atr`.
- Use: Measures how concentrated the nearby same-side stack is.
- Why helpful: Tight stacked liquidity may create a stronger magnet than widely separated pools.

`nearest_higher_bsl_distance_atr`

- Calculation for a `BSL`: find the next active `BSL` cluster with midpoint greater than this cluster midpoint at snapshot. Distance = `(next_bsl_midpoint - current_bsl_midpoint) / ATR_at_snapshot`.
- Use: Measures whether buy-side liquidity is stacked above the current BSL.
- Why helpful: A nearby higher `BSL` may create a stacked magnet path and influence how cleanly price cuts through the lower BSL.
- Note: For mirrored `SSL`, create `nearest_lower_ssl_distance_atr`.

`nearest_lower_ssl_distance_atr`

- Calculation for an `SSL`: find the next active `SSL` cluster with midpoint lower than this cluster midpoint at snapshot. Distance = `(current_ssl_midpoint - next_ssl_midpoint) / ATR_at_snapshot`.
- Use: Mirrored version of nearest higher BSL.
- Why helpful: Stacked sell-side liquidity below may encourage a deeper downside raid.

`same_side_next_pool_exists`

- Calculation: true if `nearest_higher_bsl_distance_atr` exists for `BSL` or `nearest_lower_ssl_distance_atr` exists for `SSL`.
- Use: Binary stacked-liquidity feature.
- Why helpful: Lets the model distinguish isolated pools from pools that are part of a same-side liquidity ladder.

`opposite_side_pool_stack_count`

- Calculation: count opposite-side active pools within K ATR from price at snapshot.
- Use: Measures competing liquidity magnets.
- Why helpful: A strong opposite-side pool may delay or prevent price from reaching this pool.

#### Outcome Stack Diagnostics

These are outcome diagnostics, not formation-time input features.

`same_leg_taken_count`

- Calculation: after this pool is breached, count same-side dashboard-visible pools beyond it that are also breached between the breach candle and the post-break extreme.
- Use: Measures whether the break leg consumed a stack of liquidity, not just one pool.
- Why helpful: Validates the idea that stacked nearby BSL/SSL can be taken by the same leg.

`same_leg_taken_total_levels`

- Calculation: sum `level_count` for the same-leg taken pools.
- Use: Measures total stacked markings consumed.
- Why helpful: A leg that takes `BSL x2 + BSL x3` is stronger than a leg that only takes a single `x1`.

`same_leg_taken_max_reach_atr`

- Calculation for `BSL`: distance from current BSL upper to highest same-leg taken BSL upper, divided by ATR.
- Calculation for `SSL`: distance from current SSL lower to lowest same-leg taken SSL lower, divided by ATR.
- Use: Measures how far through the stack the leg reached.
- Why helpful: Helps distinguish shallow single-pool raids from broad liquidity sweeps.

`same_leg_taken_pool_ids`

- Calculation: serialized list of same-leg consumed pool bounds and formation times.
- Use: Audit/debug column.
- Why helpful: Lets us manually verify cases where multiple pools were taken together.

### Market Structure Features

These describe whether the surrounding structure supports a sharp liquidity raid.

#### Existing Payload Features

`market_skeleton`

- Calculation: Existing ticker-level swing skeleton.
- Use: Attach latest skeleton state before pool snapshot.
- Why helpful: Liquidity in trends, ranges, and transitions may behave differently.

`range_state.quality`

- Calculation: Existing range quality score.
- Use: Range context feature.
- Why helpful: A well-defined range can engineer cleaner highs/lows at its boundaries.

`range_state.width_atr`

- Calculation: Existing range width divided by ATR.
- Use: Measures range compression / expansion space.
- Why helpful: Tight ranges may break sharply through liquidity; huge ranges may produce slower travel.

`range_state.inside_close_ratio`

- Calculation: Existing range metric, share of closes inside range.
- Use: Measures range acceptance.
- Why helpful: A pool at the edge of a well-accepted range may be more obvious.

#### Engineered From Existing Data

`recent_hh_count`

- Calculation: count `HH` labels in last N skeleton events before snapshot.
- Use: Uptrend pressure context.
- Why helpful: BSL may be taken more aggressively in bullish structure; SSL may be raided during downside breaks.

`recent_hl_count`

- Calculation: count `HL` labels in last N skeleton events.
- Use: Higher-low structure context.
- Why helpful: Higher lows can themselves engineer sell-side liquidity beneath price.

`recent_lh_count`

- Calculation: count `LH` labels in last N skeleton events.
- Use: Lower-high structure context.
- Why helpful: Lower highs can engineer buy-side liquidity above price.

`recent_ll_count`

- Calculation: count `LL` labels in last N skeleton events.
- Use: Downtrend pressure context.
- Why helpful: SSL may be cut through faster in bearish structure.

`skeleton_trend_bias`

- Calculation: map recent skeleton counts into `bullish`, `bearish`, `range`, or `mixed`.
- Use: Categorical regime feature.
- Why helpful: Strong pools can exist in any regime, but raid probability and speed may differ by regime.

`bars_since_last_structure_break`

- Calculation: count bars since latest meaningful break in skeleton/range.
- Use: Timing feature.
- Why helpful: Liquidity raids often occur after compression or before/after structure breaks.

`recent_range_break_side`

- Calculation: last `range_state.break_side` before snapshot.
- Use: Context of last accepted range break.
- Why helpful: A pool in the direction of a recent break may be easier to reach.

### Engineering / Compression Features

These features are closest to the idea of liquidity being intentionally built.

#### Engineered From Existing Data

`member_upper_wick_ratio_mean`

- Calculation: for every high/low member candle in the cluster, calculate upper wick ratio: `(high - max(open, close)) / max(high - low, small_value)`. Average across members.
- Use: Measures whether member candles formed with upper rejection wicks.
- Why helpful: For `BSL`, repeated highs with strong upper wicks may indicate visible stop/liquidity formation rather than clean continuation.

`member_lower_wick_ratio_mean`

- Calculation: for every high/low member candle in the cluster, calculate lower wick ratio: `(min(open, close) - low) / max(high - low, small_value)`. Average across members.
- Use: Measures whether member candles formed with lower rejection wicks.
- Why helpful: For `SSL`, repeated lows with strong lower wicks may indicate visible sell-side liquidity formation.

`member_relevant_wick_ratio_mean`

- Calculation: for `BSL`, use upper wick ratio. For `SSL`, use lower wick ratio. Average across members.
- Use: Side-normalized wick feature.
- Why helpful: This directly captures your "strong wick formation as indication of liquidity formation" observation.

`member_relevant_wick_ratio_max`

- Calculation: maximum side-relevant wick ratio across cluster members.
- Use: Measures whether at least one constituent was a strong wick rejection.
- Why helpful: A single dramatic wick high/low may anchor the visual liquidity level.

`member_relevant_wick_ratio_std`

- Calculation: standard deviation of side-relevant wick ratios across members.
- Use: Measures consistency of wick behavior.
- Why helpful: Consistently wick-heavy member candles may mean the pool formed through repeated rejection.

`member_body_ratio_mean`

- Calculation: average `abs(close - open) / max(high - low, small_value)` across member candles.
- Use: Measures body strength of the candles that created the liquidity marks.
- Why helpful: Helps separate wick-created liquidity from body-driven acceptance.

`member_close_location_mean`

- Calculation: `(close - low) / max(high - low, small_value)` averaged across member candles.
- Use: Measures where member candles closed inside their range.
- Why helpful: For `BSL`, closes far below highs can indicate rejection; for `SSL`, closes far above lows can indicate rejection.

`member_rejection_score_mean`

- Calculation for `BSL`: combine upper wick ratio and close-below-high location. For `SSL`: combine lower wick ratio and close-above-low location.
- Use: Side-normalized rejection score for member candles.
- Why helpful: A cleaner single feature for whether the liquidity markings were created by rejection candles.

`bearish_close_high_to_open_atr_mean`

- Calculation for bearish member candles (`close < open`): `(high - open) / ATR` averaged across members.
- Use: Captures upper wick distance before bearish close.
- Why helpful: For `BSL`, a high followed by bearish close may be a strong visual liquidity mark.

`bullish_close_high_to_close_atr_mean`

- Calculation for bullish member candles (`close >= open`): `(high - close) / ATR` averaged across members.
- Use: Captures upper wick distance on bullish-close candles.
- Why helpful: For `BSL`, even bullish candles can leave an upper wick showing rejection from the high.

`bullish_close_open_to_low_atr_mean`

- Calculation for bullish member candles (`close >= open`): `(open - low) / ATR` averaged across members.
- Use: SSL mirror for lower wick before bullish close.
- Why helpful: For `SSL`, a low followed by bullish close may mark visible sell-side liquidity.

`bearish_close_close_to_low_atr_mean`

- Calculation for bearish member candles (`close < open`): `(close - low) / ATR` averaged across members.
- Use: SSL mirror for lower wick on bearish-close candles.
- Why helpful: For `SSL`, even bearish candles can leave a lower wick showing rejection from the low.

`pre_pool_compression_atr_pct`

- Calculation: percentile rank of ATR over last N bars before `newest_time` versus prior M bars.
- Use: Measures volatility compression before pool formation.
- Why helpful: Engineered liquidity often forms while volatility contracts before expansion.

`pre_pool_range_compression_pct`

- Calculation: percentile rank of candle high-low ranges over last N bars.
- Use: Measures candle-range compression.
- Why helpful: Tight candles near repeated highs/lows can indicate coiling.

`pre_pool_body_compression_pct`

- Calculation: percentile rank of candle body sizes over last N bars.
- Use: Measures body compression.
- Why helpful: Smaller bodies before expansion may signal absorption or positioning.

`higher_lows_into_bsl_count`

- Calculation for `BSL`: count rising swing lows in the bars before a buy-side pool.
- Use: Measures pressure building toward upside liquidity.
- Why helpful: Higher lows under equal highs often show price being squeezed upward.

`lower_highs_into_ssl_count`

- Calculation for `SSL`: count falling swing highs before a sell-side pool.
- Use: Measures pressure building toward downside liquidity.
- Why helpful: Lower highs above equal lows often show price being squeezed downward.

`failed_away_attempts`

- Calculation: count attempts where price moves away from the pool by X ATR but returns near the pool before sweep.
- Use: Measures magnetic behavior before the raid.
- Why helpful: Strong liquidity often keeps pulling price back despite attempts to leave.

`pre_sweep_touch_count`

- Calculation: count candles before sweep that come within tolerance of the pool without taking it.
- Use: Measures repeated tests / respect.
- Why helpful: Multiple near-touches may build visible stops and increase raid likelihood.

`pool_visibility_score`

- Calculation: weighted heuristic from `level_count`, `density_atr`, `equal_level_tightness_atr`, `age_bars`, and range-boundary alignment.
- Use: Human-interpretable baseline feature and diagnostic score.
- Why helpful: Captures the obviousness of the pool before the model learns nonlinear combinations.

### General Environment Features

These features describe the environment around the `BSL` / `SSL` marking. They should support the liquidity model, not replace marking-specific features.

#### Engineered From Existing Data

`close_vs_ema_20_atr`

- Calculation: `(snapshot_close - EMA20) / ATR`.
- Use: Measures current price location relative to short-term trend.
- Why helpful: Liquidity raids may behave differently when price is stretched from or compressed near moving averages.

`close_vs_ema_50_atr`

- Calculation: `(snapshot_close - EMA50) / ATR`.
- Use: Measures broader trend location.
- Why helpful: Helps identify whether the pool sits in a trending or mean-reverting environment.

`ema_20_slope_atr`

- Calculation: `(EMA20_now - EMA20_N_bars_ago) / ATR`.
- Use: Trend slope.
- Why helpful: Directional slope may influence how quickly price reaches same-side or opposite-side liquidity.

`ema_20_50_spread_atr`

- Calculation: `(EMA20 - EMA50) / ATR`.
- Use: Trend regime / moving-average separation.
- Why helpful: Wide separation may indicate trend; narrow separation may indicate compression or chop.

`atr_percentile_50`

- Calculation: percentile rank of current ATR versus last 50 candles.
- Use: Volatility regime.
- Why helpful: Strong cut-through behavior may require enough volatility, while very high volatility may also create noisy false raids.

`range_percentile_50`

- Calculation: percentile rank of current candle range versus last 50 candle ranges.
- Use: Candle expansion regime.
- Why helpful: Helps distinguish quiet liquidity building from active expansion.

`body_percentile_50`

- Calculation: percentile rank of current candle body versus last 50 candle bodies.
- Use: Body expansion / compression.
- Why helpful: Helps detect whether price is in a decisive or indecisive state near pool formation.

`volume_percentile_50`

- Calculation: percentile rank of current volume versus last 50 candles, if volume is available and reliable.
- Use: Participation regime.
- Why helpful: Strong liquidity engineering or raids may occur with rising participation.

`relative_volume_20`

- Calculation: current volume divided by 20-candle average volume.
- Use: Simple volume regime feature.
- Why helpful: Easier to interpret than percentile in diagnostics.

`volatility_contraction_ratio`

- Calculation: recent ATR over last N candles divided by prior ATR over previous M candles.
- Use: Compression / expansion context.
- Why helpful: Liquidity often builds during contraction and is raided during expansion.

### Attraction And Cut-Through Outcome Features

These are not model inputs for live prediction. They are label-building columns and research diagnostics.

`was_swept`

- Calculation: after `newest_time`, `SSL` is swept when candle low <= pool lower; `BSL` is swept when candle high >= pool upper.
- Use: Outcome filter / label component.
- Why helpful: A pool cannot show cut-through strength if it is never reached during the horizon.

`bars_to_sweep`

- Calculation: candle index difference between `newest_time` and first sweep candle.
- Use: Label component and bucket analysis.
- Why helpful: Strong attractive liquidity is often reached sooner, though this should be tested.

`approach_move_atr`

- Calculation: directional movement from local pre-sweep pivot or snapshot close to pool boundary divided by ATR.
- Use: Measures how much price traveled toward the pool before the raid.
- Why helpful: The attraction concept needs movement toward the pool, not only boundary contact.

`approach_speed_atr_per_bar`

- Calculation: `approach_move_atr / bars_from_approach_start_to_sweep`.
- Use: Measures urgency of travel into the pool.
- Why helpful: Sharp attraction should show faster travel than slow drift.

`swing_to_sweep_range_atr`

- Calculation for `SSL`: identify the swing high immediately before the SSL break, then the sweep swing low / lowest low after break within the scoring window. Range = `(swing_high_price - sweep_low_price) / ATR`.
- Calculation for `BSL`: identify the swing low immediately before the BSL break, then the sweep swing high / highest high after break within the scoring window. Range = `(sweep_high_price - swing_low_price) / ATR`.
- Use: Outcome/scoring component for attraction plus cut-through.
- Why helpful: This captures your desired full movement into and through the liquidity level, including both pre-break attraction and post-break extension.
- Important: This uses future break information, so it is not a formation-time model input. It belongs in target construction and diagnostics.

`swing_to_sweep_speed_atr_per_bar`

- Calculation: `swing_to_sweep_range_atr / bars_from_pre_break_swing_to_sweep_extreme`.
- Use: Main speed metric for the success score.
- Why helpful: A strong pool should not only be reached; price should travel into and through it with urgency.

`pre_break_swing_distance_to_pool_atr`

- Calculation for `SSL`: `(pre_break_swing_high - pool_upper) / ATR`. For `BSL`: `(pool_lower - pre_break_swing_low) / ATR`.
- Use: Separates how far the move began from the pool versus how deeply it cut through.
- Why helpful: A pool reached from far away at high speed is different from a pool taken after price was already sitting next to it.

`post_break_extension_atr`

- Calculation for `SSL`: `(pool_lower - post_break_sweep_low) / ATR`. For `BSL`: `(post_break_sweep_high - pool_upper) / ATR`.
- Use: Measures extension after the pool is broken.
- Why helpful: This is a clean directional cut-through metric based on the post-break extreme.

`approach_displacement_score`

- Calculation: average or max of candle range ATR, body ATR, and close-location score during final K candles into sweep.
- Use: Measures force of approach.
- Why helpful: Strong liquidity should pull price through decisive candles, not only weak overlap.

`sweep_penetration_atr`

- Calculation for `SSL`: `(pool_lower - sweep_low) / ATR`; for `BSL`: `(sweep_high - pool_upper) / ATR`.
- Use: Primary cut-through measurement.
- Why helpful: Strong liquidity should be raided beyond the boundary, not merely tapped.

`sweep_candle_range_atr`

- Calculation: sweep candle high-low divided by ATR.
- Use: Sweep displacement component.
- Why helpful: Wide sweep candle indicates urgency / expansion.

`sweep_candle_body_atr`

- Calculation: absolute open-close of sweep candle divided by ATR.
- Use: Sweep displacement component.
- Why helpful: Large body suggests acceptance through the level, not just wick probing.

`break_fvg_gap_atr`

- Calculation for `BSL`: use the candle that breaches the `BSL`. Compare the previous candle high with the next candle low:

```text
break_fvg_gap_atr =
  max(next_candle_low - previous_candle_high, 0) / ATR
```

- Calculation for `SSL`: mirror the logic. Compare the previous candle low with the next candle high:

```text
break_fvg_gap_atr =
  max(previous_candle_low - next_candle_high, 0) / ATR
```

- Use: Displacement component.
- Why helpful: This captures whether the break through the liquidity pool created a three-candle displacement gap / FVG-like impulse. It is often cleaner than measuring only the sweep candle range because it captures the actual imbalance left around the break.

`break_fvg_gap_score`

- Calculation:

```text
break_fvg_gap_score =
  min(break_fvg_gap_atr / 1.0, 1.0)
```

- Use: Part of `displacement_score`.
- Why helpful: A liquidity break that leaves an imbalance is stronger than a break that only pokes through the level.

`sweep_body_ratio`

- Calculation: `abs(close - open) / max(high - low, small_value)` on sweep candle.
- Use: Measures candle conviction.
- Why helpful: High body ratio means the sweep was not only a wick.

`sweep_close_through_score`

- Calculation for `SSL`: how far close is below pool lower relative to candle range or ATR. For `BSL`: how far close is above pool upper.
- Use: Measures whether price accepted beyond the pool.
- Why helpful: Closing through a pool is stronger than only wicking through it.

`approach_volume_ratio`

- Calculation: average volume over final K approach candles divided by average volume over prior M candles.
- Use: Volume participation outcome.
- Why helpful: Strong raids may show increased activity into the pool.

`sweep_volume_ratio`

- Calculation: sweep candle volume divided by rolling average volume.
- Use: Sweep participation outcome.
- Why helpful: High volume through the pool may indicate broader participation.

`cut_through_score`

- Calculation: composite diagnostic from `sweep_penetration_atr`, `sweep_candle_range_atr`, `sweep_body_ratio`, and `sweep_close_through_score`.
- Use: Continuous label target or ranking metric.
- Why helpful: A continuous strength score may preserve more information than a binary label.

`liquidity_strength_score`

- Calculation: weighted 0-100 score from attraction, speed, cut-through, displacement, and optional volume components.
- Use: Primary model target.
- Why helpful: Strength is a scale. This is more faithful than forcing every pool into yes/no.

Recommended v1 formula:

```text
liquidity_strength_score =
  100 * (
    0.20 * attraction_score
  + 0.15 * speed_score
  + 0.30 * cut_through_score
  + 0.30 * displacement_score
  + 0.05 * volume_score
  )
```

If volume is missing or unreliable:

```text
liquidity_strength_score =
  100 * (
    0.20 * attraction_score
  + 0.15 * speed_score
  + 0.30 * cut_through_score
  + 0.35 * displacement_score
  )
```

Score caps:

```text
if displacement_score < 0.25:
  liquidity_strength_score = min(liquidity_strength_score, 40)

if cut_through_score < 0.20:
  liquidity_strength_score = min(liquidity_strength_score, 45)
```

Reason:

Speed alone should not push a liquidity break into the moderate/strong zone. If the actual break lacks displacement or cut-through, the score must stay capped even if price moved quickly into the level.

### V1 Scoring Parameters

Initial parameters:

```text
POST_BREAK_EXTENSION_BARS = 5
ATR_NORMALIZATION = ATR at snapshot_time
ATTRACTION_FULL_ATR = 4.0
SPEED_FULL_ATR_PER_BAR = 1.0
POST_BREAK_EXTENSION_FULL_ATR = 2.0
CLOSE_THROUGH_FULL_ATR = 1.0
SWEEP_RANGE_FULL_ATR = 3.0
SWEEP_BODY_FULL_ATR = 2.0
BREAK_FVG_GAP_FULL_ATR = 1.0
```

These are research defaults. They should be reviewed after seeing distributions.

Important:

There is no penalty for an old pool or a late breach. A `BSL` / `SSL` pool does not become weak because it has been resting for many candles. Age is a model input and diagnostic feature, not a negative scoring term.

### Direction Normalization

Use one mirrored formula for `BSL` and `SSL`.

For `SSL`:

```text
breach happens when candle.low <= pool.lower
pre_break_swing = nearest Swing High before breach
post_break_extreme = first Swing Low after breach within scoring window
fallback post_break_extreme = lowest low within POST_BREAK_EXTENSION_BARS after breach
```

For `BSL`:

```text
breach happens when candle.high >= pool.upper
pre_break_swing = nearest Swing Low before breach
post_break_extreme = first Swing High after breach within scoring window
fallback post_break_extreme = highest high within POST_BREAK_EXTENSION_BARS after breach
```

Reason:

This follows the observation that the score should capture the full draw into and through the liquidity marking, not only the breach candle.

### Raw Outcome Measurements

For `SSL`:

```text
pre_break_distance_to_pool_atr =
  (pre_break_swing_high - pool.upper) / ATR

post_break_extension_atr =
  (pool.lower - post_break_sweep_low) / ATR

swing_to_sweep_range_atr =
  (pre_break_swing_high - post_break_sweep_low) / ATR
```

For `BSL`:

```text
pre_break_distance_to_pool_atr =
  (pool.lower - pre_break_swing_low) / ATR

post_break_extension_atr =
  (post_break_sweep_high - pool.upper) / ATR

swing_to_sweep_range_atr =
  (post_break_sweep_high - pre_break_swing_low) / ATR
```

Shared:

```text
bars_to_breach =
  breach_candle_index - snapshot_candle_index

bars_from_pre_break_swing_to_extreme =
  post_break_extreme_index - pre_break_swing_index

swing_to_sweep_speed_atr_per_bar =
  swing_to_sweep_range_atr / max(bars_from_pre_break_swing_to_extreme, 1)
```

### Component Definitions

```text
attraction_score =
  min(pre_break_distance_to_pool_atr / 4.0, 1.0)

speed_score =
  min(swing_to_sweep_speed_atr_per_bar / 1.0, 1.0)

cut_through_score =
  0.70 * min(post_break_extension_atr / 2.0, 1.0)
+ 0.30 * sweep_close_through_score

displacement_score =
  0.25 * min(sweep_candle_range_atr / 3.00, 1.0)
+ 0.25 * min(sweep_candle_body_atr / 2.00, 1.0)
+ 0.25 * sweep_body_ratio
+ 0.25 * break_fvg_gap_score

volume_score =
  min(sweep_volume_ratio / 2.0, 1.0)
```

### Sweep Close-Through Score

For `SSL`:

```text
sweep_close_through_atr =
  max((pool.lower - sweep_close) / ATR, 0)
```

For `BSL`:

```text
sweep_close_through_atr =
  max((sweep_close - pool.upper) / ATR, 0)
```

Then:

```text
sweep_close_through_score =
  min(sweep_close_through_atr / 1.0, 1.0)
```

This gives credit when price accepts beyond the liquidity pool, not only wicks through it.

### Why These Weights

```text
20% attraction
15% speed
30% cut-through
30% displacement
5% volume
```

Interpretation:

- Attraction: Did price travel meaningfully toward the level?
- Speed: Once the draw toward the pool began, did price travel into and through it with urgency?
- Cut-through: Did it actually raid through the level?
- Displacement: Was the sweep candle / sweep move forceful?
- Volume: Was there participation, if volume is trustworthy?

The formula deliberately does not reward reversal after the sweep.
The formula deliberately does not penalize late breaches. `bars_to_breach` should be stored for analysis, but not used as a negative score component.

Unreached pools:

```text
If pool is not swept by the end of available forward data:
  label_observed = false
  liquidity_strength_score = null
```

Reason:

An unbreached pool may still be strong later. Treat it as censored data, not as weak liquidity. Store:

- `bars_available_after_snapshot`
- `label_observed`
- `was_swept`
- `max_directional_progress_toward_pool`
- `progress_toward_pool_pct`

Use unswept pools for live scoring and survival-style analysis later, but exclude them from v1 supervised regression target training unless we build a separate time-to-breach model.

`liquidity_strength_bucket`

- Calculation: convert `liquidity_strength_score` into weak/moderate/strong/very strong buckets.
- Use: Diagnostics and optional classification later.
- Why helpful: Humans review buckets more easily than raw scores.

### User-Specified Feature Priorities

These are the current trader-observation features that must be included in the first dataset design.

1. Age to breach.

- Include pool-level `bars_to_sweep`.
- Include member-level ages for every constituent high/low.
- Aggregate member age using min, mean, max, and standard deviation.
- Keep formation-time age and breach-time age separate to avoid leakage.

2. Cluster count.

- Use `level_count` as the core `BSL xN` / `SSL xN` feature.
- Preserve raw counts of `SH`, `SL`, `ISH`, and `ISL`.

3. `+FVG` relationship.

- Include whether the cluster is inside an FVG.
- Include how many members are FVG-backed.
- Include FVG position percentile/quartile.

4. Candle metrics for each high/low member.

- Include relevant wick ratio.
- Include body ratio.
- Include close location.
- Include side-normalized rejection score.
- Include the specific upper/lower wick distance variants described above.

5. Speed of price attraction into the pool.

- For `SSL`, measure from the swing high before breaking SSL to the sweep low after break.
- For `BSL`, measure from the swing low before breaking BSL to the sweep high after break.
- Use this as an outcome/scoring component, not as a formation-time input.

6. Constituents of the pool.

- Preserve whether each member is `SH`, `SL`, `ISH`, or `ISL`.
- Aggregate as raw count, intermediate count, mixed/raw/intermediate category, and intermediate share.

7. FVG percentile / quartile.

- Use continuous percentile for the model.
- Use quartile for diagnostics.
- For bullish FVG, lower-to-upper maps 0-100.
- For bearish FVG, upper-to-lower maps 0-100.

8. Nearest next same-side liquidity.

- For `BSL`, include nearest higher `BSL` distance normalized by ATR.
- For `SSL`, include nearest lower `SSL` distance normalized by ATR.

9. General environment.

- Include moving-average location/slope.
- Include volatility percentile.
- Include candle range/body percentile.
- Include volume/relative-volume if reliable.

Notes / cautions:

- Age-to-breach and swing-to-sweep speed are excellent success-metric components, but they are future outcomes. They should not be used as live prediction inputs.
- Member candle metrics are valid inputs because they are known when the high/low forms.
- FVG percentile is better as a continuous feature for the model; quartiles are better for human summaries.
- Environment metrics should stay secondary. The model should remain centered on the `BSL` / `SSL` markings.

## Data Collection Plan

The dataset should be built from dashboard payloads, not from a separate signal scanner.

Primary source:

```text
breaker_fvg_dashboard_data.js
```

Primary export:

```text
breaker_fvg_liquidity_pool_dataset.csv
```

Each row should represent:

```text
one liquidity pool instance = one BSL or SSL cluster
```

### Row Identity

Use a stable id so the same pool can be tracked across runs:

```text
pool_id = ticker + side + lower + upper + oldest_time + newest_time
```

Required identity columns:

- `ticker`
- `side`
- `pool_id`
- `oldest_time`
- `newest_time`
- `snapshot_time`
- `lower`
- `upper`
- `midpoint`

Required member-level support:

```text
Each pool row must be traceable back to every constituent SH/SL/ISH/ISL member.
```

Implementation options:

- Store one wide pool row with aggregated member features.
- Also export a second member-detail CSV for audit:

```text
breaker_fvg_liquidity_pool_members.csv
```

Recommended:

Use both.

`breaker_fvg_liquidity_pool_dataset.csv` should contain model-ready aggregated features.

`breaker_fvg_liquidity_pool_members.csv` should contain one row per constituent high/low with:

- `pool_id`
- `ticker`
- `side`
- `member_time`
- `member_price`
- `member_type`
- `open`
- `high`
- `low`
- `close`
- `upper_wick_ratio`
- `lower_wick_ratio`
- `body_ratio`
- `close_location`
- `relevant_wick_ratio`
- `rejection_score`
- `fvg_id`
- `fvg_relation`
- `fvg_position_percentile`

For v1:

```text
snapshot_time = newest_time
```

Reason:

The pool is fully visible at `newest_time`. Features taken at this time are cleaner because they do not depend on knowing the future.

Later dataset versions can add:

- Signal-time snapshots.
- End-of-day snapshots.
- Rolling active-pool snapshots every candle.

### Collection Steps

1. Read the dashboard payload.
2. For every ticker, read `candles`, `liquidity_clusters`, `level_ledgers`, `fvg_ledgers`, `range_state`, and `market_skeleton`.
3. Flatten every `liquidity_clusters.buy` row into one `BSL` training row.
4. Flatten every `liquidity_clusters.sell` row into one `SSL` training row.
5. Resolve every cluster constituent back to its candle and level type.
6. Build member-level candle metrics.
7. Aggregate member metrics into pool-level features.
8. Locate `snapshot_time` in the candle array.
9. Calculate all snapshot-safe features using only candles and structure events at or before `snapshot_time`.
10. Look forward from `snapshot_time` to calculate outcome / score columns.
11. Write one CSV row per pool and optional member-detail rows.

### Snapshot Safety Rules

Feature columns may use:

- Pool members known by `newest_time`.
- Candles up to and including `snapshot_time`.
- FVGs created by `snapshot_time`.
- Range and skeleton state known by `snapshot_time`.
- Other active liquidity known by `snapshot_time`.

Feature columns must not use:

- `first_taken_time` as an input.
- Whether the pool remains active at the latest export candle.
- Future candle movement after `snapshot_time`.
- Future FVGs or future structure confirmations after `snapshot_time`.

Outcome columns may use future data because they are labels.

### Dataset Versions

`v1_formation_snapshot`

- One row per pool at `newest_time`.
- Best for learning what a strong engineered pool looks like once formed.

`v2_active_pool_snapshots`

- Multiple rows per pool while active, sampled every candle or every few candles.
- Best for live dashboard probability updates.

`v3_signal_context_snapshot`

- One row per relevant pool at Breaker + FVG signal time.
- Best for asking whether the swept SSL or target BSL was strong in the actual strategy context.

Do not start with v2 or v3. Build v1 first.

## Model Training Plan

The first model should be a regression model:

```text
target = liquidity_strength_score
```

The model output should be:

```text
predicted_liquidity_strength_score
```

Interpretation:

```text
Expected strength, from 0 to 100, that this pool will attract price and get cut through sharply.
```

### Training Set

Start with one combined dataset:

```text
BSL + SSL together
```

Include `side` as a feature.

Then compare:

- Combined side-aware model.
- Separate `BSL` model.
- Separate `SSL` model.

The combined model is useful if engineered liquidity behaves symmetrically. Separate models are useful if upside and downside raids behave differently in this market.

### Feature Matrix

Input features should include only the snapshot-safe feature groups:

- Pool construction.
- FVG relationship.
- Location.
- Market structure.
- Engineering / compression.

Do not include:

- `was_swept`
- `bars_to_sweep`
- `approach_move_atr`
- `sweep_penetration_atr`
- `cut_through_score`
- `liquidity_strength_score`
- `liquidity_strength_bucket`
- Any post-snapshot outcome feature.

### Model Type

Use XGBoost regression first.

Reasons:

- Handles nonlinear feature interactions.
- Works well with tabular market-structure features.
- Can handle missing values.
- Gives useful feature importance.
- Works with SHAP explanations later.
- Matches the fact that liquidity strength is a scale, not yes/no.

Baseline comparison models:

- Linear regression / ridge regression for simple linear benchmark.
- Random forest regressor or LightGBM regressor only if needed later.

Optional later classifier:

After the continuous score is stable, derive buckets such as `weak`, `moderate`, `strong`, and `very_strong`. A classifier can be trained on buckets later, but this should not be v1.

### Evaluation

Use time-aware validation, not random row splitting if possible.

Preferred:

```text
train on older dates
validate on newer dates
```

Metrics:

- MAE.
- RMSE.
- Spearman rank correlation.
- Precision of top score buckets.
- Calibration by predicted score bucket.
- Average `bars_to_sweep` by predicted score bucket for diagnostics only.
- Average `sweep_penetration_atr` by predicted score bucket.
- Average `cut_through_score` by predicted score bucket.

The most important practical question:

```text
When the model marks the top 10% or top 20% of pools as strongest, do those pools actually get reached and cut through more sharply?
```

Bucket table to inspect:

```text
predicted_score_bucket
sweep_rate
avg_bars_to_sweep_diagnostic_only
avg_approach_speed_atr_per_bar
avg_sweep_penetration_atr
avg_sweep_body_ratio
avg_break_fvg_gap_atr
avg_cut_through_score
```

### Explainability

After the first model, inspect:

- XGBoost gain / split importance.
- Permutation importance.
- SHAP values.
- Feature behavior by side.
- Feature behavior by predicted score bucket.

Questions to answer:

- Does `level_count` actually matter?
- Does tightness matter more than count?
- Does FVG backing matter?
- Does FVG percentile location matter?
- Do wick-heavy member candles matter?
- Do clusters with mixed `SH/SL` and `ISH/ISL` constituents score higher?
- Does nearest higher `BSL` / nearest lower `SSL` stack distance matter?
- Does range-edge location matter?
- Does compression before the pool matter?
- Does age improve the pool's strength or behave nonlinearly?
- Are `BSL` and `SSL` symmetric or different?

### First Success Criteria

The first model is useful if:

- Higher predicted score buckets have higher realized strength score.
- Higher predicted score buckets have higher sweep rate.
- Higher predicted score buckets have stronger penetration through the pool.
- Higher predicted score buckets have stronger break-candle displacement / FVG-gap behavior.
- SHAP explanations match market intuition often enough to trust further refinement.
- The model identifies some non-obvious strong pools that are not captured by a simple rule score.

## Example: HAL Signal 1 Case Study

HAL Signal 1 is a strong template.

Important structure:

- SSL cluster below: `4296.8 - 4310.8`, `SSL x5`.
- Bullish FVG below / around SSL: `4272.0 - 4303.2`.
- Sweep low: `4289.5`.
- Signal high: `4378.9`.
- Internal bullish FVG retest: `4322.6 - 4339.7`.
- BSL targets above:
  - `4397.3 - 4400.0`, `BSL x2`.
  - `4408.0 - 4411.7`, `BSL x2`.

Interpretation:

```text
Price built SSL into bullish imbalance.
Price swept SSL and dipped into the FVG.
Price displaced upward.
Price returned to internal bullish FVG.
Price then delivered into stacked external BSL targets.
```

This is a high-quality labeled example for future model development.

## Near-Term Implementation Plan

### Phase A: Export Liquidity Pool Dataset

Create a CSV where each row is one `SSL` or `BSL` cluster.

Potential file:

```text
breaker_fvg_liquidity_pool_dataset.csv
```

### Phase B: Add Outcome Label Columns

For each pool, calculate forward outcomes.

Columns:

- `was_swept`
- `bars_to_sweep`
- `approach_move_atr`
- `approach_speed_atr_per_bar`
- `approach_displacement_score`
- `sweep_penetration_atr`
- `sweep_candle_range_atr`
- `sweep_candle_body_atr`
- `sweep_body_ratio`
- `break_fvg_gap_atr`
- `break_fvg_gap_score`
- `sweep_close_through_score`
- `approach_volume_ratio`
- `sweep_volume_ratio`
- `cut_through_score`
- `liquidity_strength_score`
- `liquidity_strength_bucket`

### Phase C: Train First XGBoost Model

Initial target:

```text
liquidity_strength_score
```

Initial model output:

```text
predicted_liquidity_strength_score
```

### Phase D: Explain Model

Use feature importance and SHAP-style explanations to understand:

- What makes SSL attract price and get raided sharply.
- What makes BSL attract price and get raided sharply.
- Whether FVG-backed levels really matter.
- Whether cluster density matters more than count.
- Whether age improves liquidity quality or behaves nonlinearly.
- Whether compression, equal-level tightness, and range-boundary alignment define engineered liquidity.

### Phase E: Dashboard Integration

Display:

- `SSL strength`
- `BSL strength`
- Target liquidity quality.
- Swept liquidity quality.

Later, these become inputs into the full trade-quality meter.

## Important Guardrails

- Do not train on future data as input features.
- Features must be known at the time the pool forms or at the time the signal is evaluated.
- Outcome columns can use future data only for labeling.
- Keep pool-strength modeling separate from full trade-outcome modeling at first.
- Keep raw data, engineered features, and labels separate so the model remains auditable.
