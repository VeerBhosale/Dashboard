# Breaker + FVG Market Structure Analytics Spec

This document is the working blueprint for the Breaker + FVG strategy system. It is meant to be readable by us now and by a future agent later. It documents what already exists, what we are trying to build, why each layer matters, and how the pieces should fit together.

The main shift is this:

Current system:

```text
Signal fires -> analyze signal -> alert/dashboard
```

Target system:

```text
Continuously analyze market structure -> identify prepared liquidity zones -> wait for Breaker + FVG signal -> judge whether the signal occurred inside a high-quality pre-analyzed context
```

The signal should not be the start of the analysis. The signal should be the confirmation event inside an already understood market structure.

## Current Files

Primary files:

- `breaker_fvg_dashboard_export.py`
  - Main dashboard payload generator.
  - Fetches OHLCV data.
  - Detects swing highs/lows, ISL/ISH context, Breaker + FVG signals, liquidity metrics, FVG retests, range context, and chart overlays.
  - Writes `breaker_fvg_dashboard_data.js`.

- `breaker_fvg_scan.py`
  - Telegram alert sender.
  - Reads `breaker_fvg_dashboard_data.js`.
  - Important: this should not independently recalculate signals. The dashboard payload is the single source of truth.

- `breaker_fvg_research.py`
  - Research logger.
  - Reads the dashboard payload and writes trade research rows.
  - Maintains stable trade ideas so repeated versions of the same sweep do not become duplicate research trades.

- `breaker_fvg_research_log.csv`
  - One row per trade idea.

- `breaker_fvg_trade_timeseries.csv`
  - Candle-by-candle path data for each trade idea.

- `breaker_fvg_dashboard.html`
  - Frontend dashboard.
  - Shows watchlist, chart, signal details, score meter, overlays, FVG zones, context lines, and panels.

- `run_breaker_fvg_dashboard_aws.sh`
  - AWS wrapper script template.
  - Pulls GitHub, runs exporter/scanner, and stages outputs.

- `stage_breaker_fvg_outputs.py`
  - Stages dashboard every run.
  - Stages research CSVs only once daily near/after market close to avoid heavy Git churn.

## Existing Strategy Logic

### Core Long Breaker + FVG Pattern

The current primary setup is a bullish Breaker + FVG setup.

Structure pattern:

```text
T3 = swing low
T2 = swing high
T1 = swing low / sweep low
T0 = breaker high / signal high
```

For a valid bullish setup:

- The structure must form as:
  - Swing Low
  - Swing High
  - Swing Low
  - Swing High
- The final breaker high must be above the previous swing high.
- The original low must remain unbroken until the sweep leg.
- The downside sweep leg should take liquidity.
- A bearish FVG must exist in the breaker structure window.
- A bullish FVG retest can improve context when the sweep low dips into a valid historical bullish FVG.

Recent hard validation rules added:

- `idx_high > t2_high`
  - The final breaker high must strictly break the previous swing high.

- `t2_high - t3_low > 0`
  - The rise from the first low to the first high must be valid.

- `t2_high - t1_low > 0`
  - The sweep leg must be below the prior high.

- No break of `T3` low before `T1`.
  - If price breaks the original low before the supposed sweep low, the structure is invalid.

This last rule fixed the case where a low was marked, price broke that low, then a tiny local high formed, and the later move was incorrectly treated as a clean breaker structure.

### FVG Inclusion Rule

The bullish breaker setup uses bearish FVG inclusion for the breaker circuit.

Current important setting:

```python
FVG_CONFIRM_AFTER_SIGNAL_CANDLES = 1
```

Reason:

- A bearish FVG can be validated on the candle immediately after the swing high candle.
- The system should include FVGs up to one candle after the signal candle where appropriate.
- This was added to avoid missing structures where the FVG confirmation occurs right after the swing high.

### Bullish FVG Sweep Retest

Additional context:

- Look left for bullish FVGs.
- Carry valid bullish FVG zones forward.
- A bullish FVG remains valid unless fully filled.
- A partially filled FVG remains active, but only the remaining unfilled portion should be carried forward.
- If the sweep low lies inside a valid bullish FVG zone, mark this as a positive context factor.

FVG zone definition:

```text
Bullish FVG zone = high of first candle in the FVG sequence to low of third candle
```

Partial touch does not invalidate the FVG. Full fill invalidates it.

Final Phase 1 FVG treatment:

- Track bullish and bearish FVGs in a ledger.
- Store the original zone and the currently unfilled remaining zone.
- If price partially fills the zone, shrink the active remaining zone.
- If price fully fills the zone, mark it inactive.
- This applies to both bullish and bearish FVGs.

Bullish FVG lifecycle:

```text
Original bullish FVG:
lower = candle_1_high
upper = candle_3_low

If a later candle enters the zone but does not fully fill it:
carry forward only the unfilled portion.

If a later candle fully trades through the zone:
mark the bullish FVG inactive.
```

Bearish FVG lifecycle:

```text
Original bearish FVG:
lower = candle_3_high
upper = candle_1_low

If a later candle enters the zone but does not fully fill it:
carry forward only the unfilled portion.

If a later candle fully trades through the zone:
mark the bearish FVG inactive.
```

Structural interpretation:

- Sell-side interest: unbreached SL/ISL liquidity above or near active bullish FVG remaining zones.
- Buy-side interest: unbreached SH/ISH liquidity below or near active bearish FVG remaining zones.
- Both sides must be treated with mirrored logic.

FVG anchor marking and liquidity-cluster membership are separate:

- The remaining unfilled FVG portion determines which directional extreme receives the individual FVG-liquidity marker.
- A swing point inside the original FVG remains eligible to contribute to an `SSL xN` or `BSL xN` cluster even when price has already traversed that portion of the gap.
- A traversed-portion swing is only a candidate cluster member. By itself it must not create a visible `SSL x1` or `BSL x1`.
- A standalone visible `SSL x1` or `BSL x1` requires a true remaining-zone anchor.
- A traversed-portion swing becomes visible liquidity only when the adaptive price-clustering logic groups at least two distinct swing points into `SSL xN` or `BSL xN`.
- A raw SL or SH may inherit FVG-backed `SSL` or `BSL` validity only when it forms within seven candles after the FVG is created.
- Once more than seven candles have passed since FVG creation, that FVG must not validate a newly formed raw SL or SH.
- This is a backend validity rule, not a visual display timeout. A raw swing that qualified within the seven-candle window remains visible according to its normal liquidity lifetime until swept.
- A raw FVG-backed SL or SH must also survive the next five candles without breach before it becomes valid `SSL` or `BSL`.
- If fewer than five later candles are available, keep the raw FVG swing pending rather than displaying it as confirmed liquidity.
- The five-candle survival rule applies only to raw FVG-backed SH/SL levels. It must not invalidate confirmed ISL/ISH interpretations of the same physical swing.
- ISL/ISH and ordinary multi-point liquidity clustering remain separate from this raw FVG-backed swing admission rule.
- A confirmed ISL/ISH inside the original FVG is valid FVG-backed liquidity.
- A confirmed ISL/ISH near the original FVG within the configured ATR proximity tolerance is also valid FVG-backed liquidity. Preserve `near` as a distinct relation for later weighting.
- The seven-candle raw swing admission rule does not apply to confirmed ISL/ISH levels.
- Display active structural FVGs whose remaining zone is within the configured ATR distance from current price.
- Also display the nearest active bullish FVG and nearest active bearish FVG as directional fallbacks when they sit outside that distance.
- Also display an active structural FVG when it backs valid `SSL` or `BSL` liquidity, even when it is outside the configured ATR distance.
- Draw the selected FVGs using their full original bounds while retaining the remaining unfilled portion in data.
- FVG box selection is visual-only. It must not change FVG classification, liquidity validity, or signal generation.
- For buy-side liquidity, mark the highest qualifying SH/ISH anchor while retaining the other distinct SH/ISH points inside the original bearish FVG as BSL cluster members.
- For sell-side liquidity, mirror the rule: mark the lowest qualifying SL/ISL anchor while retaining the other distinct SL/ISL points inside the original bullish FVG as SSL cluster members.
- Count distinct physical swing points only. If the same candle is both SH and ISH, or both SL and ISL, it contributes one unit to `xN`.
- When a new swing is printed on the same candle that sweeps an existing cluster boundary, do not automatically add it to the consumed pool.
- Allow only a tiny `0.03 ATR` overshoot grace for same-candle cluster continuation. A larger sweep starts a fresh liquidity candidate.
- Ordinary sell-side clusters may combine distinct resting SL and ISL levels even when the raw SL has no FVG backing.
- Ordinary buy-side clusters may combine distinct resting SH and ISH levels even when the raw SH has no FVG backing.
- FVG backing and five-candle survival remain required only when a raw SH/SL creates a standalone visible `BSL x1` or `SSL x1`.

### ISL / ISH

Current use:

- ISL is used for sell-side liquidity context.
- ISH is used for buy-side liquidity / target liquidity context.
- Existing dashboard has rolling context overlays and range overlays based on ISL/ISH.

Final Phase 1 definition:

- ISL is confirmed only from the ordered list of Swing Lows.
- ISH is confirmed only from the ordered list of Swing Highs.
- Confirmation is delayed because the immediate right same-type swing must exist first.
- Do not identify an ISL/ISH at the moment the middle swing forms. The system must wait until the next same-type swing appears.

ISL rule:

```text
Given three immediate same-type Swing Lows:

SL_left, SL_candidate, SL_right

SL_candidate is ISL only if:

SL_left.price > SL_candidate.price
AND
SL_right.price > SL_candidate.price
```

ISH rule:

```text
Given three immediate same-type Swing Highs:

SH_left, SH_candidate, SH_right

SH_candidate is ISH only if:

SH_left.price < SH_candidate.price
AND
SH_right.price < SH_candidate.price
```

Walk-forward implementation requirement:

```text
1. Walk candles from oldest to newest.
2. Detect SH/SL as they become available.
3. Maintain separate ordered lists of Swing Highs and Swing Lows.
4. Whenever a new SL appears, evaluate the previous SL as possible ISL.
5. Whenever a new SH appears, evaluate the previous SH as possible ISH.
6. Once confirmed, add the ISL/ISH to the level ledger.
7. Carry confirmed ISL/ISH forward until breached.
```

Known direction:

- The ISL/ISH implementation should evolve into a proper market structure engine, not just chart markings.
- Breaker setup marking should be driven by strict structure logic, not loose visual approximations.

### Liquidity Metrics Already Started

Existing or partially existing metrics include:

- Accumulated swept lows.
- Protected lows.
- Base/current ISL ideas.
- Target liquidity from swing highs / ISH.
- Liquidity cluster tolerance.
- Unresolved deeper ISL risk.
- Bullish FVG retest yes/no.
- Range context around signal.
- Score / probability meter.

### Current Entry Research Model

The research system defines a model entry after a signal.

Entry level:

```text
range_50 = sweep_low + 0.5 * (signal_high - sweep_low)
fvg_50 = bull_fvg_lower + 0.5 * (bull_fvg_upper - bull_fvg_lower)
entry = max(range_50, fvg_50)
```

Stop:

```text
stop = sweep_low
```

Targets:

```text
target_1r = entry + 1 * risk
target_2r = entry + 2 * risk
target_3r = entry + 3 * risk
```

Where:

```text
risk = entry - stop
```

Important philosophy:

- Stable trade identity should be based on the sweep idea.
- Latest structure can still be used for entry logic because entry happens at retracement and the breaker high may evolve before entry.

### Research Logging Already Built

The research logger captures:

- Trade idea identity.
- Signal time.
- First signal time.
- Latest signal time.
- Ticker.
- Structure prices.
- Entry/stop/target levels.
- Score/probability.
- FVG metrics.
- Liquidity metrics.
- Outcome status.
- Time series after signal.

The time series captures candle-level information:

- Open, high, low, close, volume.
- Candle body, range, wick details.
- ATR and percentile features.
- Distance to entry, stop, targets.
- Whether entry/stop/targets were touched.
- Phase labels such as pre-entry, post-entry, post-stop, post-target.

This is the foundation for later statistical analysis.

## New Target Architecture

The system should be divided into five major engines:

1. Instrument structure engine
2. Liquidity pool engine
3. Market-wide structure engine
4. Signal engine
5. Trade assistant / research engine

## 1. Instrument Structure Engine

Purpose:

Build a rolling market structure state for every stock using only available candles at that point in time.

For each ticker, calculate:

- Swing highs.
- Swing lows.
- Intermediate swing highs.
- Intermediate swing lows.
- Current protected high.
- Current protected low.
- Current range high.
- Current range low.
- Current dealing range midpoint.
- Premium/discount location.
- Current structural direction.
- Latest break of structure direction.
- Time since last structure break.
- Whether price is trending, ranging, compressing, expanding, or chopping.

Outputs per ticker:

```json
{
  "ticker": "EXAMPLE.NS",
  "structure_state": {
    "direction": "range|bullish|bearish|compression|chop",
    "range_high": 0.0,
    "range_low": 0.0,
    "midpoint": 0.0,
    "protected_high": 0.0,
    "protected_low": 0.0,
    "latest_ish": 0.0,
    "latest_isl": 0.0,
    "last_structure_break": "up|down|none",
    "bars_since_break": 0
  }
}
```

Key rule:

This engine must run before signal evaluation. The Breaker + FVG signal should be judged against this prepared structure state.

## 2. Liquidity Pool Engine

Purpose:

Measure where liquidity is resting and whether price is likely being drawn toward it.

Downside liquidity:

- Unbreached swing lows below price.
- Unbreached ISL levels below price.
- Equal/near-equal lows.
- Higher lows building into sell-side liquidity.
- Age of lows.
- Distance from price.
- Cluster density.
- Whether the pool has been partially attacked.
- Whether the pool has been completely swept.

Upside liquidity:

- Unbreached swing highs above price.
- Unbreached ISH levels above price.
- Equal/near-equal highs.
- Lower highs building into buy-side liquidity.
- Age of highs.
- Distance from price.
- Cluster density.
- Whether the pool has been partially attacked.
- Whether the pool has been completely swept.

Liquidity pool metrics:

```json
{
  "sell_side": {
    "pool_count": 0,
    "cluster_count": 0,
    "nearest_distance_atr": 0.0,
    "density_score": 0.0,
    "age_score": 0.0,
    "freshness_score": 0.0,
    "engineering_score": 0.0
  },
  "buy_side": {
    "pool_count": 0,
    "cluster_count": 0,
    "nearest_distance_atr": 0.0,
    "density_score": 0.0,
    "age_score": 0.0,
    "freshness_score": 0.0,
    "engineering_score": 0.0
  }
}
```

## 3. Market-Wide Structure Engine

User requirement:

Do not rely only on index comparison. We have stock-level data for all symbols, so use the average of market structure across the universe.

The market structure engine should create two versions of market comparison:

### Version A: Whole Watchlist Structure Average

Use all available tickers in the watchlist.

Calculate:

- Percent of stocks in bullish structure.
- Percent of stocks in bearish structure.
- Percent of stocks in compression.
- Percent of stocks sweeping sell-side liquidity.
- Percent of stocks sweeping buy-side liquidity.
- Percent of stocks reclaiming after downside sweep.
- Percent of stocks rejecting after upside sweep.
- Percent of stocks above their structure midpoint.
- Percent of stocks below their structure midpoint.
- Average distance to sell-side liquidity.
- Average distance to buy-side liquidity.
- Average compression score.
- Average liquidity density.

This tells us the broader internal market state, independent of index labels.

Example output:

```json
{
  "market_structure_average": {
    "bullish_structure_pct": 42.0,
    "bearish_structure_pct": 38.0,
    "compression_pct": 20.0,
    "sell_side_sweep_pct": 18.0,
    "buy_side_sweep_pct": 9.0,
    "reclaim_after_sell_side_sweep_pct": 11.0,
    "average_compression_score": 64.0,
    "market_bias": "mixed_recovery"
  }
}
```

### Version B: Peer / Similar-Behavior Comparison

Instead of comparing a stock only to the full market, compare it to stocks in a similar condition.

Possible peer groups:

- Same sector if sector mapping is available.
- Same volatility bucket.
- Same structure state.
- Same compression state.
- Same liquidity setup type.
- Same price regime: trending, ranging, expanding, compressing.

Examples:

```text
This stock has a long signal, but 72% of similar compressed stocks are breaking down.
```

```text
This stock swept sell-side liquidity while 61% of peers are also reclaiming after sell-side sweeps.
```

This matters because signals are not independent. Stocks are influenced by market-wide flows, sector pressure, and crowd behavior.

### Market Context Score

Market context should be a weight or filter.

For a long Breaker + FVG:

Positive market conditions:

- More stocks reclaiming after downside sweeps.
- Sell-side sweeps followed by recovery across the watchlist.
- Broad structure improving.
- More stocks above structure midpoint.
- Compression resolving upward.
- Upside liquidity open across many stocks.

Negative market conditions:

- More stocks breaking sell-side and failing to reclaim.
- Bearish structure dominating.
- Long signals appearing during broad sell pressure.
- High correlation selling.
- Market-wide compression resolving downward.

Output:

```json
{
  "market_context": {
    "bias": "supportive|neutral|hostile",
    "score": 0.0,
    "reason": "Broad sell-side sweeps are reclaiming across the watchlist."
  }
}
```

## 4. Preparedness Engine

Purpose:

The system should know beforehand where we want signals to occur.

Before a Breaker + FVG signal exists, each ticker should be classified as:

- Prepared long candidate.
- Prepared short candidate.
- Neutral.
- Avoid.

For long preparedness:

Look for:

- Sell-side liquidity formed below price.
- Buy-side liquidity available above price.
- Price compressing between meaningful pools.
- Higher lows forming sell-side liquidity.
- Lower highs forming buy-side liquidity.
- Valid bullish FVG or imbalance nearby.
- Price near discount relative to range.
- Broad market structure supportive or improving.
- Clean target path.

Long preparedness output:

```json
{
  "long_preparedness": {
    "status": "prepared|watch|neutral|avoid",
    "score": 0.0,
    "preferred_signal_zone": {
      "lower": 0.0,
      "upper": 0.0,
      "reason": "Sell-side liquidity pool plus bullish FVG overlap."
    },
    "expected_path": "Sweep sell-side -> reclaim -> retrace to entry zone -> target buy-side liquidity"
  }
}
```

Important idea:

The best signal is not just a valid signal. The best signal is the signal the system was already waiting for.

## 5. Consolidation / Range Engine

Purpose:

Detect ranges and consolidations before they break.

Rules:

- Use only confirmed ISL and confirmed ISH as range boundaries.
- Swing Highs and Swing Lows are liquidity levels, not range boundaries.
- Maintain outer range boundaries.
- If price forms lower ISH or higher ISL inside the range, do not immediately rebase the outer range.
- The range persists until price meaningfully breaks one side.
- After a true break, rebase both sides to the latest valid ISL/ISH.

Range boundary rule:

```text
range_low = confirmed active ISL
range_high = confirmed active ISH
```

Reason:

- Breaking an ISL is a major factor in the Breaker + FVG trade setup.
- Breaking an ISH is the mirrored buy-side structural event.
- SH/SL levels still matter, but they should be treated as liquidity levels inside or near the range.
- This keeps the range model tied to structural levels instead of noisy swing points.

Range formation logic:

```text
1. Walk forward through candles.
2. Maintain active confirmed ISLs and ISHs.
3. A candidate range can form only when an active ISL and active ISH bracket recent price.
4. Confirm the range after price spends enough candles between those boundaries.
5. Keep the outer ISL/ISH boundaries until a true accepted break occurs.
6. Treat wick breaches as sweeps, not automatic range breaks.
7. Treat accepted closes/follow-through outside the boundary as true range breaks.
8. After true break, rebase to the latest confirmed active ISL/ISH pair.
```

Metrics:

- Range high.
- Range low.
- Range midpoint.
- Range duration.
- Range height in ATR.
- Touches of high.
- Touches of low.
- Internal ISL count.
- Internal ISH count.
- Compression score.
- Expansion after break.
- Fakeout count.
- Direction of likely liquidity magnet.

Output:

```json
{
  "range_context": {
    "active": true,
    "high": 0.0,
    "low": 0.0,
    "midpoint": 0.0,
    "duration": 0,
    "height_atr": 0.0,
    "compression_score": 0.0,
    "quality_score": 0.0
  }
}
```

## 6. Compression Engine

Purpose:

Measure whether price is coiling before expansion.

Metrics:

- ATR compression percentile.
- Candle range compression percentile.
- Candle body compression percentile.
- ISH/ISL range narrowing.
- Consecutive lower highs.
- Consecutive higher lows.
- Volume compression, if volume is reliable.
- Compression duration.

Output:

```json
{
  "compression": {
    "score": 0.0,
    "duration": 0,
    "range_percentile": 0.0,
    "body_percentile": 0.0,
    "atr_percentile": 0.0,
    "pattern": "triangle|flag|box|none"
  }
}
```

## 7. Liquidity Engineering Engine

Purpose:

Detect whether liquidity is intentionally being built before the sweep.

Patterns:

- Equal lows.
- Equal highs.
- Higher lows building sell-side liquidity.
- Lower highs building buy-side liquidity.
- Price compressing toward one side.
- Bull flag-like continuation:
  - Sell-side liquidity sweep first.
  - Then buy-side liquidity target remains open.
- Repeated failed moves into the same side of the range.

Metrics:

- Sell-side engineering score.
- Buy-side engineering score.
- Symmetry score.
- Compression + liquidity overlap score.
- Sweep readiness score.

Output:

```json
{
  "liquidity_engineering": {
    "sell_side_score": 0.0,
    "buy_side_score": 0.0,
    "dominant_pool": "sell_side|buy_side|balanced|none",
    "pattern": "higher_lows_lower_highs|equal_lows|equal_highs|bull_flag|none"
  }
}
```

## 8. Signal Context Validator

Purpose:

When a Breaker + FVG signal fires, compare it against the pre-signal analysis.

Questions:

- Was this stock already a prepared long candidate?
- Did the signal occur inside the preferred signal zone?
- Did the sweep take the expected sell-side liquidity?
- Did the sweep enter a valid bullish FVG?
- Was the target buy-side liquidity already identified?
- Was the broad market context supportive?
- Was the signal in discount or premium?
- Was it a genuine sweep or just retracement under sell pressure?

Output:

```json
{
  "signal_context": {
    "prepared_before_signal": true,
    "preferred_zone_hit": true,
    "market_context_fit": "supportive|neutral|hostile",
    "liquidity_context_fit": "strong|medium|weak",
    "target_context_fit": "strong|medium|weak",
    "final_quality": "bad|neutral|good"
  }
}
```

## 9. Trade Assistant State Machine

Purpose:

After a signal, guide the trade decision instead of treating alert as automatic entry.

States:

```text
prepared
signal_generated
waiting_for_retracement
entry_zone_touched
entry_valid
entry_degraded
invalidated_before_entry
entered
target_hit
stop_hit
expired
```

Each state should have hard rule explanations.

Example:

```json
{
  "trade_state": "waiting_for_retracement",
  "entry_zone": {
    "lower": 0.0,
    "upper": 0.0
  },
  "warnings": [
    "Broad market context is hostile.",
    "Price has not reclaimed structure strongly."
  ],
  "decision_note": "Signal is valid, but entry should wait for controlled retracement into model zone."
}
```

This can later become the agent layer.

## Dashboard Target Design

The dashboard should eventually move from signal-only to structure-first.

### Page-Level Sections

1. Market structure summary
2. Prepared watchlist
3. Current signals
4. Selected ticker chart
5. Selected ticker structure diagnostics
6. Selected signal/trade assistant panel
7. Research/history panel

### Watchlist Views

View 1: Prepared candidates

```text
Ticker | Prep Score | Market Fit | Sell Liquidity | Buy Liquidity | Compression | Status
```

View 2: Current signals

```text
Ticker | Signal Score | Prep Score | Market Fit | Final Quality | Time
```

View 3: Market-wide structure

```text
Bullish % | Bearish % | Compression % | Reclaim % | Sell Pressure % | Bias
```

### Chart Overlays

Existing overlays:

- Candles.
- Signal markers.
- Dotted swing high/low lines.
- ISL/ISLB/ISH/BSL lines.
- Bullish FVG zone.
- Range overlay.
- Rolling context lines.

Future overlays:

- Prepared signal zone.
- Buy-side liquidity pool shading.
- Sell-side liquidity pool shading.
- Active range box.
- Compression zone.
- Market regime label.
- Entry zone.
- Stop and R targets.

### Phase 1 Visual Policy

Phase 1 should show only useful active context, not every raw structural point.

Do show:

- Active unbreached SH/SL levels.
- Active unbreached ISH/ISL levels.
- Active FVG remaining zones.
- Liquidity clusters.
- Range high / range low / midpoint.
- Premium / discount zones.
- Prepared sweep zones.

Do not show by default:

- Every Swing High / Swing Low marker.
- Confirmed ISH / ISL markers by themselves.

Reason:

- Raw SH/SL markers will clutter the chart.
- ISL/ISH are useful as active levels and range boundaries, not as standalone labels everywhere.
- The chart should show actionable active structure, while the payload stores the full ledger for analysis and future agent use.

## Scoring Architecture

Avoid one blended score too early. Use separate components first.

Recommended scores:

```text
Preparedness Score
Market Context Score
Liquidity Pool Score
Liquidity Engineering Score
Compression Score
FVG Context Score
Signal Quality Score
Target Quality Score
Execution Path Score
```

Final trade quality can later be:

```text
Trade Quality = weighted combination of:
Preparedness + Signal Quality + Market Context + Target Quality + Execution Path
```

But for research, keep components separate so we can test which factors actually matter.

## Phased Build Plan

### Phase 1: Foundation - Market Structure State

Goal:

Create reliable rolling market structure analytics for each ticker.

Build:

- Walk-forward Swing High / Swing Low ledger.
- Confirmed ISL / ISH ledger using immediate same-type neighbor confirmation.
- Active unbreached SH/SL levels for both sell-side and buy-side liquidity.
- Active unbreached ISL/ISH levels for both structural liquidity and range boundaries.
- FVG ledger for bullish and bearish imbalances, including remaining unfilled zones after partial fills.
- Liquidity clusters for both sell-side and buy-side levels.
- Range state using only confirmed ISL/ISH as boundaries.
- Range high, range low, midpoint, premium/discount location.
- Basic market skeleton: HH, HL, LH, LL, range, compression, transition.
- Target liquidity landscape, not a single target.
- Exhaustion raw metrics inside ranges.
- Event timeline for future agent use.

Deliverables:

- Add structured `structure_state` to dashboard payload.
- Add structured `level_ledgers`, `fvg_ledgers`, `liquidity_clusters`, `range_state`, and `event_timeline` fields.
- Add dashboard toggles for Phase 1 approved visuals.
- Add a diagnostics panel in dashboard.
- Keep existing Breaker + FVG signal logic stable.

### Phase 2: Liquidity Pool Analytics

Goal:

Measure upside and downside liquidity, not just mark levels.

Build:

- Sell-side pool count/density/age/distance.
- Buy-side pool count/density/age/distance.
- Equal high/low clustering.
- Higher-low/lower-high liquidity creation.

Deliverables:

- Add `liquidity_pools` to dashboard payload.
- Add chart shading for active pools.
- Add watchlist columns for sell-side and buy-side pool quality.

### Phase 3: Market-Wide Structure Average

Goal:

Use all stock data to measure broad market structure.

Build:

- Whole-watchlist market structure average.
- Peer comparison framework.
- Market bias classification.
- Market context score for long signals.

Deliverables:

- Add `market_context` to dashboard payload.
- Add market summary ribbon/panel.
- Add market fit label to each signal.

### Phase 4: Preparedness Engine

Goal:

Rank stocks before signals occur.

Build:

- Long preparedness score.
- Preferred signal zone.
- Expected path.
- Avoid conditions.

Deliverables:

- Add prepared watchlist mode.
- Sort candidates by prep score.
- Show whether a signal was anticipated or reactive.

### Phase 5: Signal Context Validator

Goal:

Judge Breaker + FVG signals against pre-existing context.

Build:

- Prepared-before-signal yes/no.
- Preferred zone hit yes/no.
- Market context fit.
- Liquidity context fit.
- Target context fit.

Deliverables:

- Add final quality label: Bad / Neutral / Good.
- Improve Telegram message with context if needed.
- Keep detailed diagnostics in dashboard, not Telegram.

### Phase 6: Trade Assistant / Agent-Ready Layer

Goal:

Move from alerting to guided trade management.

Build:

- Trade state machine.
- Entry zone monitoring.
- Invalidation before entry.
- Path quality after signal.
- Agent-readable diagnostic notes.

Deliverables:

- Add assistant panel.
- Add structured JSON notes for each signal/trade idea.
- Use hard-rule diagnostics first; any natural language commentary should be generated from facts.

### Phase 7: Statistical Research

Goal:

Find which factors actually predict good trades.

Analyze:

- Win rate by score bucket.
- R-multiple distribution.
- Entry touched vs not touched.
- Stop-first vs target-first.
- Market context fit.
- Compression score.
- Liquidity pool score.
- FVG retest.
- Prepared-before-signal.
- Sector/peer context if available.
- Time of day.
- Volatility percentile.

Deliverables:

- Research notebook or script.
- Factor table.
- Score calibration.
- Evidence-backed scoring weights.

## Agent Requirements

A future agent should be given this document plus structured payload data.

The agent should not invent signals. It should:

- Read structure facts.
- Read market-wide context.
- Read signal facts.
- Read trade state.
- Explain quality.
- Warn when the setup is attractive but context is weak.
- Help avoid trades when signal is valid but structure is hostile.
- Keep decision-making grounded in pre-defined rules.

Agent output should be based on rule-derived fields, not raw intuition.

Example agent note:

```text
This is a valid Breaker + FVG signal, but it was not a prepared long candidate before the signal. Sell-side liquidity was swept, but broad market structure is hostile and target liquidity is weak. Treat as neutral, not a high-conviction trade.
```

## Guiding Principles

- Signals are not independent between stocks.
- The market structure average matters.
- A valid signal is not automatically a good trade.
- The system should know where it wants signals before they happen.
- Liquidity creation matters as much as liquidity sweep.
- Compression before expansion is a key factor.
- Research should preserve separate factors instead of hiding everything inside one score.
- The dashboard should help decision-making, not encourage overtrading.
- Telegram should stay concise; dashboard should hold the full diagnostics.
