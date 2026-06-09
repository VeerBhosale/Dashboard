# Phase 1 Market Structure Implementation Todo

This is the working checklist for Phase 1 of the Breaker + FVG market structure upgrade.

Status meanings:

- `[ ]` Not started
- `[~]` In progress
- `[x]` Complete
- `[?]` Needs review / decision

Implementation rule:

```text
Do not change existing Breaker + FVG signal behavior unless a task explicitly says so.
Phase 1 builds the market structure foundation around the existing signal system.
```

## 0. Safety / Working Setup

- [x] Work on `dashboard-experiments` branch.
- [ ] Keep `main` untouched until experiment is reviewed.
- [ ] Before major edits, check `git status --short`.
- [ ] Avoid `git add .`; only stage intentional files.
- [ ] Keep notebook/user changes separate from strategy/dashboard code changes.

## 1. Data Model Design

- [x] Define `structure_state` payload schema.
- [x] Define `level_ledgers` payload schema.
- [x] Define `fvg_ledgers` payload schema.
- [x] Define `liquidity_clusters` payload schema.
- [x] Define `range_state` payload schema.
- [x] Define `premium_discount` payload schema.
- [x] Define `market_skeleton` payload schema.
- [x] Define `target_liquidity_landscape` payload schema.
- [x] Define `event_timeline` payload schema.

## 2. Walk-Forward Swing Ledger

- [x] Reuse or isolate existing SH/SL detection.
- [x] Build ordered Swing High ledger.
- [x] Build ordered Swing Low ledger.
- [ ] Store each swing with:
  - [ ] type: `SH` or `SL`
  - [ ] time
  - [ ] price
  - [ ] source candle index
  - [ ] active/unbreached status
  - [ ] breached time
  - [ ] touch count
  - [ ] age bars
- [x] Mark active SH inactive when later candle high breaches it.
- [x] Mark active SL inactive when later candle low breaches it.
- [x] Keep full historical ledger for diagnostics and agent use.

## 3. Confirmed ISL / ISH Ledger

- [x] Confirm ISL only after immediate right Swing Low exists.
- [x] Confirm ISH only after immediate right Swing High exists.
- [ ] ISL rule:

```text
SL_left.price > SL_candidate.price
AND
SL_right.price > SL_candidate.price
```

- [ ] ISH rule:

```text
SH_left.price < SH_candidate.price
AND
SH_right.price < SH_candidate.price
```

- [x] Store confirmed ISL/ISH with:
  - [ ] type: `ISL` or `ISH`
  - [ ] source swing time
  - [ ] confirmation time
  - [ ] price
  - [ ] active/unbreached status
  - [ ] breached time
  - [ ] age bars
  - [ ] touch count
- [x] Mark active ISH inactive when later candle high breaches it.
- [x] Mark active ISL inactive when later candle low breaches it.
- [ ] Do not show standalone ISL/ISH markers by default.

## 4. Active Level Tracking

- [~] Build active unbreached SH list per candle.
- [~] Build active unbreached SL list per candle.
- [~] Build active unbreached ISH list per candle.
- [~] Build active unbreached ISL list per candle.
- [x] Mirror buy-side and sell-side behavior exactly.
- [x] Sell-side liquidity = active SL + active ISL.
- [x] Buy-side liquidity = active SH + active ISH.

## 5. FVG Ledger With Partial-Fill Remaining Zones

- [x] Build bullish FVG ledger.
- [x] Build bearish FVG ledger.
- [x] Store original FVG zone.
- [x] Store active remaining unfilled zone.
- [x] Keep partially filled FVG active.
- [x] Shrink bullish FVG active zone when partially filled.
- [x] Shrink bearish FVG active zone when partially filled.
- [x] Mark bullish FVG inactive only when fully filled.
- [x] Mark bearish FVG inactive only when fully filled.
- [ ] Store:
  - [ ] type
  - [ ] created time
  - [ ] candle 1 time
  - [ ] candle 3 time
  - [ ] original lower / upper
  - [ ] remaining lower / upper
  - [ ] midpoint
  - [ ] partial fill percent
  - [ ] active status
  - [ ] filled time
  - [ ] age bars

## 6. Liquidity Clusters

- [x] Build sell-side clusters from active SL + active ISL.
- [x] Build buy-side clusters from active SH + active ISH.
- [x] Use adaptive tolerance:

```text
cluster_tolerance = max(price_pct_buffer, ATR_buffer, minimum_tick_buffer)
```

- [x] Store each cluster with:
  - [ ] side: `sell` or `buy`
  - [ ] lower
  - [ ] upper
  - [ ] midpoint
  - [ ] level count
  - [ ] ISL/ISH count
  - [ ] SH/SL count
  - [ ] oldest time
  - [ ] newest time
  - [ ] age bars
  - [ ] density
  - [ ] active status
- [ ] Mark clusters swept when price breaches through the cluster.

## 7. Liquidity-To-FVG Proximity

- [ ] For long-side preparation:
  - [ ] Match active sell-side liquidity clusters to active bullish FVG remaining zones.
  - [ ] Look for unbreached SL/ISL liquidity above or near bullish FVGs.
  - [ ] Store prepared sell-side sweep zones.
- [ ] For short-side preparation:
  - [ ] Match active buy-side liquidity clusters to active bearish FVG remaining zones.
  - [ ] Look for unbreached SH/ISH liquidity below or near bearish FVGs.
  - [ ] Store prepared buy-side sweep zones.
- [ ] Store zone quality components, but keep final score lightweight for Phase 1.

## 8. Range State Using ISL / ISH Boundaries Only

- [ ] Candidate range can form only from confirmed active ISL and confirmed active ISH.
- [ ] Do not use raw SH/SL as range boundaries.
- [ ] Confirm range after price spends enough candles between ISL/ISH boundaries.
- [ ] Store:
  - [ ] range low = ISL
  - [ ] range high = ISH
  - [ ] midpoint
  - [ ] duration bars
  - [ ] height
  - [ ] height in ATR
  - [ ] inside close ratio
  - [ ] internal SH/SL count
  - [ ] internal ISL/ISH count
  - [ ] high-side sweep count
  - [ ] low-side sweep count
- [ ] Treat wick break as sweep, not automatic range break.
- [ ] Treat accepted close/follow-through outside range as true break.
- [ ] After true break, rebase to latest confirmed active ISL/ISH pair.

## 9. Premium / Discount

- [ ] Calculate midpoint from active ISL/ISH range.
- [ ] Classify current price location:
  - [ ] deep discount
  - [ ] discount
  - [ ] mid
  - [ ] premium
  - [ ] deep premium
- [ ] Store close position percent within range.
- [ ] Use as data only in Phase 1.
- [ ] Show premium/discount zones visually when range overlay is enabled.

## 10. Market Skeleton

- [ ] Classify latest swing high as HH, LH, or equal high.
- [ ] Classify latest swing low as HL, LL, or equal low.
- [ ] Build recent skeleton sequence.
- [ ] Classify ticker structure:
  - [ ] uptrend
  - [ ] downtrend
  - [ ] range
  - [ ] compression
  - [ ] expansion
  - [ ] transition
- [ ] Store trend age / bars since structure change.
- [ ] Keep this as data for later cross-stock market structure averaging.

## 11. Target Liquidity Landscape

- [ ] For long-side target landscape:
  - [ ] Active unbreached SH levels above price/entry.
  - [ ] Active unbreached ISH levels above price/entry.
  - [ ] Buy-side clusters above price/entry.
  - [ ] Nearest target candidate.
  - [ ] Strongest target candidate.
  - [ ] Cleanest target candidate.
- [ ] For short-side target landscape:
  - [ ] Active unbreached SL levels below price/entry.
  - [ ] Active unbreached ISL levels below price/entry.
  - [ ] Sell-side clusters below price/entry.
  - [ ] Nearest target candidate.
  - [ ] Strongest target candidate.
  - [ ] Cleanest target candidate.
- [ ] Do not assume one single SH/ISH is the target.

## 12. Exhaustion Raw Metrics

- [ ] Measure range duration.
- [ ] Count failed upward attempts inside range.
- [ ] Count failed downward attempts inside range.
- [ ] Count higher lows inside range.
- [ ] Count lower highs inside range.
- [ ] Measure time spent above/below midpoint without breakout.
- [ ] Store buyer exhaustion raw metrics.
- [ ] Store seller exhaustion raw metrics.
- [ ] Keep as data only in Phase 1.

## 13. Event Timeline

- [ ] Emit structured events for:
  - [ ] new SH
  - [ ] new SL
  - [ ] confirmed ISL
  - [ ] confirmed ISH
  - [ ] SH breached
  - [ ] SL breached
  - [ ] ISH breached
  - [ ] ISL breached
  - [ ] bullish FVG created
  - [ ] bearish FVG created
  - [ ] FVG partially filled
  - [ ] FVG fully filled
  - [ ] liquidity cluster formed
  - [ ] liquidity cluster swept
  - [ ] range started
  - [ ] range swept
  - [ ] range broken
  - [ ] prepared sweep zone formed
- [ ] Keep this agent-readable.

## 14. Dashboard Visuals

Show:

- [x] Active unbreached SH/SL levels.
- [x] Active unbreached ISH/ISL levels.
- [x] Active FVG original zones.
- [x] Liquidity clusters.
- [ ] Range high / range low / midpoint.
- [ ] Premium / discount zones.
- [ ] Prepared sweep zones.

Do not show by default:

- [x] Every raw Swing High / Swing Low marker.
- [x] Standalone confirmed ISH / ISL markers.

Visual cleanup decisions:

- [x] Merge overlapping / nearby FVG zones.
- [x] Hide raw SH/SL unless inside or near an original FVG zone.
- [x] Draw ISL/ISH from source swing time, while retaining confirmation time in data.
- [x] Use chart line-series architecture for structure dotted lines.
- [x] Label structure lines.
- [x] Draw cluster bands with `SSL xN` / `BSL xN` labels.

Pending visual fixes to batch together:

- [x] Prioritize multi-level `SSL xN` / `BSL xN` liquidity clusters above single FVG-backed `SSL x1` / `BSL x1` levels before applying the per-side display limit.
- [x] Remove the six-bands-per-side display cap so valid historical `SSL xN` / `BSL xN` bands remain visible through their sweep candle.
- [x] Improve chart interaction smoothness: remove redundant pointer/mouse/touch overlay redraw triggers and synchronize pooled canvas redraws to at most one update per animation frame during drag/zoom.
- [x] Keep structure lines rendered through Lightweight Charts; optimize only the canvas overlays so visual behavior remains unchanged.
- [x] Pool SSL bands into one canvas and BSL bands into one canvas instead of allocating a full-chart canvas per band.
- [x] Regression-check `NTPC.NS` SH at `2026-05-12 09:15`: historical `BSL x1` remains visible until swept at `2026-05-27 09:15`.

Pending FVG-liquidity selection fixes to batch together:

- [x] When a swing is inside multiple active structural FVGs, prefer an FVG whose walk-forward remaining unfilled portion contains the swing over an older broad FVG that only contains the swing in its original zone.
- [x] When multiple containing FVGs have the same remaining-zone relevance, prefer the more recent FVG before falling back to distance.
- [x] Treat a displayed-price-equal FVG boundary tap as inside despite sub-paise download precision drift.
- [x] Regression-check `ABCAPITAL.NS` SLs at `2026-05-13 15:15` and `2026-05-20 10:15`: active `SSL x2` band confirmed.
- [x] Separate FVG anchor marking from liquidity-cluster membership: the remaining FVG portion selects the individual anchor, while distinct swings inside the original FVG still contribute to `SSL xN` / `BSL xN`.
- [x] Regression-check `DLF.NS` SHs at `2026-05-14 12:15`, `2026-05-14 14:15`, and `2026-05-15 09:15`: one anchor and a historical `BSL x3` band confirmed.
- [x] Keep traversed-portion FVG swings as cluster candidates, but hide them as standalone bands unless they are true remaining-zone anchors or form a multi-swing cluster.
- [x] Regression-check `NTPC.NS` SH at `2026-05-14 12:15`: traversed-portion singleton remains in data but does not render as standalone `BSL x1`.
- [x] Require raw FVG-backed `SL → SSL` and `SH → BSL` swings to form within seven candles after the validating FVG is created.
- [x] Treat the seven-candle rule as backend validity, not as a visual display timeout.
- [x] Keep qualified raw swings, multi-point clusters, and ISL/ISH-backed liquidity bands visible according to their normal lifetime until swept.
- [x] Promote confirmed ISL/ISH levels near an original FVG within the ATR proximity tolerance into FVG-backed liquidity while preserving their `near` relation for later weighting.
- [x] Keep the seven-candle raw FVG admission limit scoped to SH/SL only; do not apply it to confirmed ISL/ISH.
- [x] Require raw FVG-backed SH/SL levels to survive five later candles without breach before they become valid `SSL` / `BSL`.
- [x] Keep raw FVG-backed SH/SL pending when fewer than five later candles are available.
- [x] Reject same-candle sweep extrema from an existing cluster when the boundary overshoot exceeds a tiny `0.03 ATR` grace.
- [x] Allow all distinct resting `SL + ISL` and `SH + ISH` levels into mirrored ordinary cluster detection even when a raw swing lacks FVG backing.
- [x] Keep FVG backing and five-candle survival mandatory only for standalone raw `SSL x1` / `BSL x1`.
- [x] Display active structural FVG zones near current price plus the nearest active bullish and bearish FVG fallback.
- [x] Also retain an active structural FVG zone when it backs valid `SSL` or `BSL` liquidity.
- [x] Draw selected FVG zones using full original bounds while keeping remaining unfilled bounds in data.
- [x] Keep FVG box selection visual-only; do not alter classification or signal generation.

## 15. Validation / Diagnostics

- [ ] Add a debug mode for one ticker.
- [ ] Print active levels at selected candle.
- [ ] Print confirmed ISL/ISH ledger for selected ticker.
- [ ] Print active FVG remaining zones.
- [ ] Print active liquidity clusters.
- [ ] Print range state.
- [ ] Compare chart visuals to debug data for known examples.
- [ ] Confirm no existing Breaker + FVG signal logic changed unintentionally.

## 16. Phase 1 Completion Criteria

- [ ] Phase 1 payload fields exist.
- [ ] Visual toggles render correctly.
- [ ] ISL/ISH confirmation delay works correctly.
- [ ] FVG partial fill remaining zones work correctly.
- [ ] Buy-side and sell-side liquidity are mirrored.
- [ ] Ranges use only ISL/ISH boundaries.
- [ ] Dashboard remains usable and not cluttered.
- [ ] Existing Telegram scanner still reads same dashboard payload source.
- [ ] Existing research logging still runs.
- [ ] No AWS/live changes until reviewed and merged intentionally.
