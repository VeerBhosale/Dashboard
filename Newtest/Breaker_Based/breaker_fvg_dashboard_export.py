import argparse
import json
import sys
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
except ModuleNotFoundError as exc:
    print(f"Missing Python package: {exc.name}")
    print("Install dashboard dependencies in the same Python environment you use to run this script:")
    print("  python -m pip install -r requirements_dashboard.txt")
    sys.exit(1)


BASE_DIR = Path(__file__).resolve().parent
SYMBOLS_FILE = BASE_DIR / "NSE_Symbols.csv"
OUTPUT_FILE = BASE_DIR / "breaker_fvg_dashboard_data.js"

PERIOD_DAYS = 30
INTERVAL = "1h"
ATR_BASELINE = 14
LIQ_ISL_MAX_ATR_DISTANCE = 3.0
LIQ_BREACH_TOLERANCE_ATR = 0.05
FVG_CONFIRM_AFTER_SIGNAL_CANDLES = 1
CLUSTER_ATR_FACTOR = 0.15
INTERMEDIATE_CLUSTER_ATR_FACTOR = 0.25
CLUSTER_SWEEP_GRACE_ATR = 0.03
CLUSTER_PRICE_PCT = 0.001
CLUSTER_MIN_BUFFER = 0.05
RANGE_MIN_BARS = 5
RANGE_INSIDE_CLOSE_RATIO = 0.65
FVG_MIN_ORIGINAL_ATR = 0.20
FVG_MIN_REMAINING_ATR = 0.10
FVG_MIN_DISPLACEMENT_ATR = 0.60
FVG_MAX_VISUAL_AGE_BARS = 100
FVG_MAX_VISUAL_DISTANCE_ATR = 3.0
FVG_LEVEL_NEAR_ATR = 0.30
FVG_MERGE_ATR = 0.25
FVG_MERGE_MAX_GAP_BARS = 5
RAW_SWING_FVG_MAX_AGE_BARS = 7
RAW_SWING_FVG_MIN_SURVIVAL_BARS = 5


def mean_atr(df):
    prev_c = df["Close"].shift(1).fillna(df["Close"].iloc[0])
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_c).abs(),
            (df["Low"] - prev_c).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.mean()


def active_isls(btc, isl_prices, t3_ts, t1_ts, t3_low, t1_low, tolerance, atr_baseline):
    prior_lows = isl_prices.dropna()
    prior_lows = prior_lows[prior_lows.index < t3_ts]
    if atr_baseline > 0:
        max_dist = LIQ_ISL_MAX_ATR_DISTANCE * atr_baseline
        prior_lows = prior_lows[(t3_low - prior_lows).abs() <= max_dist]

    active = []
    for sl_ts, sl_price in prior_lows.items():
        sl_price = float(sl_price)
        later_isls = prior_lows[(prior_lows.index > sl_ts) & (prior_lows.index < t1_ts)]
        if len(later_isls) and later_isls.min() < sl_price - tolerance:
            continue

        active.append(
            {
                "time": sl_ts,
                "price": sl_price,
                "swept": bool(t1_low < sl_price - tolerance),
            }
        )

    return active


def walkback_isl_levels(active_lows):
    if not active_lows:
        return None, None

    current = active_lows[-1]
    base = current
    for level in reversed(active_lows[:-1]):
        if level["price"] < base["price"]:
            base = level
        else:
            break
    return current, base


def rolling_context_levels(btc, isl_prices, ish_prices):
    return {
        "isl": [
            {"time": unix_time(ts), "value": as_float(price)}
            for ts, price in isl_prices.dropna().items()
        ],
        "ish": [
            {"time": unix_time(ts), "value": as_float(price)}
            for ts, price in ish_prices.dropna().items()
        ],
        "end_time": unix_time(btc.index[-1]),
    }


def rolling_ranges(btc, isl_prices, ish_prices):
    events = []
    for ts, price in isl_prices.dropna().items():
        events.append({"time": ts, "type": "isl", "price": float(price)})
    for ts, price in ish_prices.dropna().items():
        events.append({"time": ts, "type": "ish", "price": float(price)})
    events.sort(key=lambda event: event["time"])

    latest_isl = None
    latest_ish = None
    active = None
    ranges = []

    def close_active(end_ts, break_side=None):
        nonlocal active
        if not active:
            return
        if end_ts <= active["start_time"]:
            active = None
            return
        active["end_time"] = end_ts
        active["break_side"] = break_side
        active["duration"] = btc.index.get_loc(end_ts) - btc.index.get_loc(active["start_time"])
        active["width"] = active["upper"] - active["lower"]
        ranges.append(active)
        active = None

    for event in events:
        ts = event["time"]
        price = event["price"]

        if active:
            if event["type"] == "ish" and price > active["upper"]:
                close_active(ts, "up")
            elif event["type"] == "isl" and price < active["lower"]:
                close_active(ts, "down")
            else:
                if event["type"] == "ish":
                    active["internal_ish_count"] += 1
                else:
                    active["internal_isl_count"] += 1

        if event["type"] == "isl":
            latest_isl = event
        else:
            latest_ish = event

        if active is None and latest_isl and latest_ish and latest_isl["price"] < latest_ish["price"]:
            active = {
                "start_time": max(latest_isl["time"], latest_ish["time"]),
                "end_time": btc.index[-1],
                "lower": latest_isl["price"],
                "upper": latest_ish["price"],
                "lower_time": latest_isl["time"],
                "upper_time": latest_ish["time"],
                "internal_isl_count": 0,
                "internal_ish_count": 0,
                "break_side": None,
            }

    if active:
        close_active(btc.index[-1], None)

    output = []
    for item in ranges:
        width = item["upper"] - item["lower"]
        if width <= 0:
            continue
        start_loc = btc.index.get_loc(item["start_time"])
        end_loc = btc.index.get_loc(item["end_time"])
        atr_start = max(0, start_loc - ATR_BASELINE + 1)
        atr = mean_atr(btc.iloc[atr_start : start_loc + 1]) if start_loc >= 0 else 0.0
        width_atr = round(width / atr, 3) if atr and atr > 0 else None
        duration = max(end_loc - start_loc, 0)
        compression = 0.0 if width_atr is None else max(0.0, 1.0 - min(width_atr / 4.0, 1.0))
        age_score = min(duration / 20.0, 1.0)
        internal_score = min((item["internal_isl_count"] + item["internal_ish_count"]) / 6.0, 1.0)
        quality = round(100.0 * (0.40 * age_score + 0.40 * compression + 0.20 * internal_score), 1)
        output.append(
            {
                "start_time": unix_time(item["start_time"]),
                "end_time": unix_time(item["end_time"]),
                "lower": as_float(item["lower"]),
                "upper": as_float(item["upper"]),
                "lower_time": unix_time(item["lower_time"]),
                "upper_time": unix_time(item["upper_time"]),
                "duration": duration,
                "width": round(width, 4),
                "width_atr": width_atr,
                "internal_isl_count": item["internal_isl_count"],
                "internal_ish_count": item["internal_ish_count"],
                "quality": quality,
                "break_side": item["break_side"],
            }
        )
    return output


def range_context_for_signal(ranges, signal_ts):
    prior = [
        item for item in ranges
        if item["start_time"] <= unix_time(signal_ts) <= item["end_time"]
    ]
    if not prior:
        prior = [item for item in ranges if item["end_time"] <= unix_time(signal_ts)]
    if not prior:
        return {
            "range_active": False,
            "range_quality": None,
            "range_age": 0,
            "range_width_atr": None,
            "range_internal_isl": 0,
            "range_internal_ish": 0,
            "range_break_side": None,
            "range_lower": None,
            "range_upper": None,
        }
    item = prior[-1]
    return {
        "range_active": bool(item["start_time"] <= unix_time(signal_ts) <= item["end_time"] and item["break_side"] is None),
        "range_quality": item["quality"],
        "range_age": item["duration"],
        "range_width_atr": item["width_atr"],
        "range_internal_isl": item["internal_isl_count"],
        "range_internal_ish": item["internal_ish_count"],
        "range_break_side": item["break_side"],
        "range_lower": item["lower"],
        "range_upper": item["upper"],
    }


def accumulated_liquidity(btc, isl_prices, t3_ts, t1_ts, t3_low, t1_low, atr_baseline):
    tolerance = atr_baseline * LIQ_BREACH_TOLERANCE_ATR if atr_baseline > 0 else 0.0
    active_lows = active_isls(btc, isl_prices, t3_ts, t1_ts, t3_low, t1_low, tolerance, atr_baseline)
    current_isl, base_isl_info = walkback_isl_levels(active_lows)
    swept_lows = [level for level in active_lows if level["swept"]]

    protected_levels = sorted([level["price"] for level in swept_lows])
    hold_bars = [btc.index.get_loc(t1_ts) - btc.index.get_loc(level["time"]) for level in swept_lows]
    touches = 0
    volume_scores = []
    has_volume = "Volume" in btc.columns
    avg_start_ts = active_lows[0]["time"] if active_lows else t3_ts
    avg_volume = btc["Volume"].loc[avg_start_ts:t3_ts].replace(0, np.nan).mean() if has_volume else np.nan

    for level in swept_lows:
        sl_ts = level["time"]
        sl_price = level["price"]
        defend_window = btc[(btc.index > sl_ts) & (btc.index < t1_ts)]
        touches += int(defend_window["Low"].between(sl_price - tolerance, sl_price + tolerance).sum())

        if has_volume and avg_volume > 0:
            sl_iloc = btc.index.get_loc(sl_ts)
            swing_vol = btc["Volume"].iloc[max(0, sl_iloc - 1) : min(len(btc), sl_iloc + 2)].replace(0, np.nan).mean()
            if not np.isnan(swing_vol):
                volume_scores.append(swing_vol / avg_volume)

    clusters = 0
    if protected_levels:
        clusters = 1
        for prev_level, level in zip(protected_levels, protected_levels[1:]):
            if abs(level - prev_level) > tolerance:
                clusters += 1

    spacing_atr = 0.0
    if len(protected_levels) > 1 and atr_baseline > 0:
        spacing_atr = round(float(np.mean(np.diff(protected_levels)) / atr_baseline), 3)

    base_isl = base_isl_info["price"] if base_isl_info else None
    base_isl_ts = base_isl_info["time"] if base_isl_info else None
    current_isl_price = current_isl["price"] if current_isl else None
    current_isl_ts = current_isl["time"] if current_isl else None

    return {
        "protected_lows": len(swept_lows),
        "current_isl": current_isl_price,
        "current_isl_time": unix_time(current_isl_ts) if current_isl_ts is not None else None,
        "base_isl": base_isl,
        "base_isl_time": unix_time(base_isl_ts) if base_isl_ts is not None else None,
        "base_isl_swept": bool(base_isl is not None and t1_low < base_isl - tolerance),
        "current_isl_swept": bool(current_isl and current_isl.get("swept")),
        "low_hold": round(float(np.mean(hold_bars)), 2) if hold_bars else 0.0,
        "low_touches": touches,
        "low_vol": round(float(np.mean(volume_scores)), 3) if volume_scores else 0.0,
        "low_clusters": clusters,
        "low_spacing_atr": spacing_atr,
        "low_count_mode": "isl_price_levels",
    }


def unresolved_deeper_isl(btc, t3_ts, t1_low, atr_baseline):
    historical_isls = btc["ISL_Price"].loc[:t3_ts].dropna()
    deeper_isls = historical_isls[historical_isls < t1_low]
    if deeper_isls.empty:
        return {"deeper_isl_pending": False, "nearest_deeper_isl": None, "deeper_isl_atr": None, "deeper_isl_count": 0}

    nearest = float(deeper_isls.max())
    distance_atr = round((t1_low - nearest) / atr_baseline, 3) if atr_baseline > 0 else None
    return {
        "deeper_isl_pending": True,
        "nearest_deeper_isl": nearest,
        "deeper_isl_atr": distance_atr,
        "deeper_isl_count": int(len(deeper_isls)),
    }


def target_liquidity(btc, swings, idx, idx_high, atr_baseline):
    tolerance = atr_baseline * LIQ_BREACH_TOLERANCE_ATR if atr_baseline > 0 else 0.0
    prior_ish = btc["ISH_Price"].loc[:idx].dropna()
    prior_ish = prior_ish[prior_ish.index < idx]

    if prior_ish.empty:
        return {
            "target_highs": 0,
            "base_ish": None,
            "base_ish_time": None,
            "base_ish_swept": False,
            "target_distance_atr": None,
            "target_hold": 0.0,
            "target_touches": 0,
            "target_vol": 0.0,
            "target_clusters": 0,
            "target_spacing_atr": 0.0,
        }

    base_ish_ts = prior_ish.index[-1]
    base_ish = float(prior_ish.iloc[-1])

    if base_ish <= idx_high + tolerance:
        return {
            "target_highs": 0,
            "base_ish": None,
            "base_ish_time": None,
            "base_ish_swept": True,
            "target_distance_atr": None,
            "target_hold": 0.0,
            "target_touches": 0,
            "target_vol": 0.0,
            "target_clusters": 0,
            "target_spacing_atr": 0.0,
        }

    target_distance_atr = round((base_ish - idx_high) / atr_baseline, 3) if atr_baseline > 0 else None
    prior_highs = swings[
        (swings.index >= base_ish_ts)
        & (swings.index < idx)
        & (swings["Main_Signal"] == "Swing High")
    ]

    target_levels = []
    hold_bars = []
    touches = 0
    volume_scores = []
    has_volume = "Volume" in btc.columns
    avg_volume = btc["Volume"].loc[base_ish_ts:idx].replace(0, np.nan).mean() if has_volume else np.nan

    for sh_ts, row in prior_highs.iterrows():
        sh_price = row["High"]
        later_highs = prior_highs[(prior_highs.index > sh_ts) & (prior_highs.index < idx)]
        breached_before_signal = (later_highs["High"] > (sh_price + tolerance)).any()
        still_above_breaker = sh_price > (idx_high + tolerance)

        if still_above_breaker and not breached_before_signal:
            target_levels.append(float(sh_price))
            hold_bars.append(btc.index.get_loc(idx) - btc.index.get_loc(sh_ts))
            defend_window = btc[(btc.index > sh_ts) & (btc.index < idx)]
            touches += int(defend_window["High"].between(sh_price - tolerance, sh_price + tolerance).sum())

            if has_volume and avg_volume > 0:
                sh_iloc = btc.index.get_loc(sh_ts)
                swing_vol = btc["Volume"].iloc[max(0, sh_iloc - 1) : min(len(btc), sh_iloc + 2)].replace(0, np.nan).mean()
                if not np.isnan(swing_vol):
                    volume_scores.append(swing_vol / avg_volume)

    target_levels = sorted(target_levels)
    clusters = 0
    if target_levels:
        clusters = 1
        for prev_level, level in zip(target_levels, target_levels[1:]):
            if abs(level - prev_level) > tolerance:
                clusters += 1

    spacing_atr = 0.0
    if len(target_levels) > 1 and atr_baseline > 0:
        spacing_atr = round(float(np.mean(np.diff(target_levels)) / atr_baseline), 3)

    return {
        "target_highs": len(target_levels),
        "base_ish": base_ish,
        "base_ish_time": unix_time(base_ish_ts),
        "base_ish_swept": bool(idx_high > base_ish),
        "target_distance_atr": target_distance_atr,
        "target_hold": round(float(np.mean(hold_bars)), 2) if hold_bars else 0.0,
        "target_touches": touches,
        "target_vol": round(float(np.mean(volume_scores)), 3) if volume_scores else 0.0,
        "target_clusters": clusters,
        "target_spacing_atr": spacing_atr,
    }


def bullish_fvg_sweep_retest(btc, t1_ts, t1_low):
    bull_fvgs = btc[(btc.index < t1_ts) & (btc["FVG_Signal"] == "Bullish_FVG")].copy()
    active_count = 0
    touched = []

    for fvg_ts, row in bull_fvgs.iterrows():
        lower = row["T1_Bull_High"]
        upper = row["T0_Bull_Low"]
        if pd.isna(lower) or pd.isna(upper) or upper <= lower:
            continue

        prior_lows = btc["Low"].loc[fvg_ts:t1_ts].iloc[:-1]
        invalidated_before_sweep = len(prior_lows) > 0 and prior_lows.min() < lower
        if invalidated_before_sweep:
            continue

        active_count += 1
        if lower <= t1_low <= upper:
            size = upper - lower
            fill = round((upper - t1_low) / size, 3) if size > 0 else 0.0
            fvg_start_ts = btc.index[btc.index.get_loc(fvg_ts) - 2]
            touched.append(
                {
                    "bull_fvg_time": unix_time(fvg_start_ts),
                    "bull_fvg_lower": float(lower),
                    "bull_fvg_upper": float(upper),
                    "bull_fvg_fill": max(0.0, min(fill, 1.0)),
                    "bull_fvg_age": btc.index.get_loc(t1_ts) - btc.index.get_loc(fvg_ts),
                }
            )

    if not touched:
        return {
            "bull_fvg_retest": False,
            "active_bull_fvgs": active_count,
            "bull_fvg_lower": None,
            "bull_fvg_upper": None,
            "bull_fvg_fill": None,
            "bull_fvg_age": None,
            "bull_fvg_time": None,
        }

    selected = touched[-1]
    return {
        "bull_fvg_retest": True,
        "active_bull_fvgs": active_count,
        **selected,
    }


def unix_time(ts):
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def as_float(value):
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def ts_from_unix(value, index):
    ts = pd.to_datetime(value, unit="s", utc=True)
    if getattr(index, "tz", None) is not None:
        return ts.tz_convert(index.tz)
    return ts.tz_localize(None)


def phase1_structure_swings(btc):
    high_mask = btc["Swing_High"] == "Swing High"
    low_mask = btc["Swing_Low"] == "Swing Low"
    dual_mask = high_mask & low_mask

    highs = btc.loc[high_mask].copy()
    highs["Main_Signal"] = "Swing High"
    highs["Structure_Dual_Swing"] = dual_mask.loc[highs.index]

    lows = btc.loc[low_mask].copy()
    lows["Main_Signal"] = "Swing Low"
    lows["Structure_Dual_Swing"] = dual_mask.loc[lows.index]

    if highs.empty and lows.empty:
        return pd.DataFrame(columns=[*btc.columns, "Main_Signal", "Structure_Dual_Swing"])

    return pd.concat([highs, lows]).sort_index(kind="stable")


def atr_series(df):
    prev_c = df["Close"].shift(1).fillna(df["Close"].iloc[0])
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_c).abs(),
            (df["Low"] - prev_c).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def phase1_swing_ledgers(btc, swings):
    ledgers = {"sh": [], "sl": []}
    events = []

    for ts, row in swings.iterrows():
        is_high = row["Main_Signal"] == "Swing High"
        price = float(row["High"] if is_high else row["Low"])
        kind = "SH" if is_high else "SL"
        later = btc.loc[btc.index > ts]
        if is_high:
            breach = later[later["High"] > price]
            side = "buy"
        else:
            breach = later[later["Low"] < price]
            side = "sell"
        breached_at = breach.index[0] if not breach.empty else None
        latest_loc = len(btc.index) - 1
        age_bars = latest_loc - btc.index.get_loc(ts)
        item = {
            "id": f"{kind}_{unix_time(ts)}_{round(price, 4)}",
            "type": kind,
            "side": side,
            "time": unix_time(ts),
            "price": as_float(price),
            "dual_swing": bool(row.get("Structure_Dual_Swing", False)),
            "active": breached_at is None,
            "breached_at": unix_time(breached_at) if breached_at is not None else None,
            "age_bars": int(max(age_bars, 0)),
            "touch_count": 0,
        }
        ledgers["sh" if is_high else "sl"].append(item)
        events.append({"time": unix_time(ts), "event_type": f"new_{kind.lower()}", "side": side, "price": as_float(price)})
        if breached_at is not None:
            events.append(
                {
                    "time": unix_time(breached_at),
                    "event_type": f"{kind.lower()}_breached",
                    "side": side,
                    "price": as_float(price),
                }
            )

    return ledgers, sorted(events, key=lambda item: item["time"])


def phase1_intermediate_ledgers(btc, swing_ledgers):
    ledgers = {"ish": [], "isl": []}
    events = []

    def build(items, kind):
        out = []
        for index in range(1, len(items) - 1):
            left = items[index - 1]
            candidate = items[index]
            right = items[index + 1]
            price = candidate["price"]
            if kind == "ISL":
                qualifies = left["price"] > price and right["price"] > price
                side = "sell"
                breach_col = "Low"
                breached = lambda values: values < price
            else:
                qualifies = left["price"] < price and right["price"] < price
                side = "buy"
                breach_col = "High"
                breached = lambda values: values > price
            if not qualifies:
                continue

            source_ts = ts_from_unix(candidate["time"], btc.index)
            confirmation_ts = ts_from_unix(right["time"], btc.index)
            later = btc.loc[btc.index > source_ts]
            breach = later[breached(later[breach_col])]
            breached_at = breach.index[0] if not breach.empty else None
            latest_loc = len(btc.index) - 1
            age_bars = latest_loc - btc.index.get_loc(confirmation_ts)
            item = {
                "id": f"{kind}_{candidate['time']}_{round(price, 4)}",
                "type": kind,
                "side": side,
                "source_time": candidate["time"],
                "confirmation_time": right["time"],
                "time": candidate["time"],
                "price": as_float(price),
                "active": breached_at is None,
                "breached_at": unix_time(breached_at) if breached_at is not None else None,
                "age_bars": int(max(age_bars, 0)),
                "touch_count": 0,
            }
            out.append(item)
            events.append(
                {
                    "time": right["time"],
                    "event_type": f"confirmed_{kind.lower()}",
                    "side": side,
                    "price": as_float(price),
                    "source_time": candidate["time"],
                }
            )
            if breached_at is not None:
                events.append(
                    {
                        "time": unix_time(breached_at),
                        "event_type": f"{kind.lower()}_breached",
                        "side": side,
                        "price": as_float(price),
                    }
                )
        return out

    ledgers["isl"] = build(swing_ledgers["sl"], "ISL")
    ledgers["ish"] = build(swing_ledgers["sh"], "ISH")
    return ledgers, sorted(events, key=lambda item: item["time"])


def phase1_fvg_ledgers(btc):
    ledgers = {"bullish": [], "bearish": []}
    events = []
    atr_values = atr_series(btc).rolling(ATR_BASELINE, min_periods=1).mean()
    latest_close = float(btc["Close"].iloc[-1])
    for idx in range(2, len(btc)):
        ts = btc.index[idx]
        c1_ts = btc.index[idx - 2]
        c1 = btc.iloc[idx - 2]
        c2 = btc.iloc[idx - 1]
        c3 = btc.iloc[idx]

        fvg_type = None
        lower = upper = None
        if c3["Low"] > c1["High"]:
            fvg_type = "bullish"
            lower = float(c1["High"])
            upper = float(c3["Low"])
        elif c3["High"] < c1["Low"]:
            fvg_type = "bearish"
            lower = float(c3["High"])
            upper = float(c1["Low"])

        if not fvg_type or upper <= lower:
            continue

        remaining_lower = lower
        remaining_upper = upper
        partial_touched_at = None
        filled_at = None
        later = btc.iloc[idx + 1 :]
        for later_ts, row in later.iterrows():
            if fvg_type == "bullish":
                low = float(row["Low"])
                if low <= lower:
                    filled_at = later_ts
                    remaining_upper = lower
                    break
                if low < remaining_upper:
                    remaining_upper = max(lower, low)
                    partial_touched_at = partial_touched_at or later_ts
            else:
                high = float(row["High"])
                if high >= upper:
                    filled_at = later_ts
                    remaining_lower = upper
                    break
                if high > remaining_lower:
                    remaining_lower = min(upper, high)
                    partial_touched_at = partial_touched_at or later_ts

        size = upper - lower
        remaining_size = max(remaining_upper - remaining_lower, 0.0)
        fill_pct = 1.0 - (remaining_size / size if size > 0 else 0.0)
        atr_at_creation = float(atr_values.iloc[idx]) if atr_values.iloc[idx] > 0 else mean_atr(btc.iloc[max(0, idx - ATR_BASELINE + 1) : idx + 1])
        original_size_atr = size / atr_at_creation if atr_at_creation else 0.0
        remaining_size_atr = remaining_size / atr_at_creation if atr_at_creation else 0.0
        middle_range = float(c2["High"] - c2["Low"])
        displacement_atr = middle_range / atr_at_creation if atr_at_creation else 0.0
        distance_atr = abs(((remaining_lower + remaining_upper) / 2) - latest_close) / atr_at_creation if atr_at_creation else 0.0
        valid_for_structure = original_size_atr >= FVG_MIN_ORIGINAL_ATR and displacement_atr >= FVG_MIN_DISPLACEMENT_ATR
        valid_for_visual = (
            valid_for_structure
            and remaining_size_atr >= FVG_MIN_REMAINING_ATR
            and (filled_at is None)
            and (((len(btc.index) - 1) - idx) <= FVG_MAX_VISUAL_AGE_BARS or distance_atr <= FVG_MAX_VISUAL_DISTANCE_ATR)
        )
        item = {
            "id": f"{fvg_type}_fvg_{unix_time(c1_ts)}_{round(lower, 4)}_{round(upper, 4)}",
            "type": fvg_type,
            "created_time": unix_time(ts),
            "candle_1_time": unix_time(c1_ts),
            "candle_3_time": unix_time(ts),
            "start_bar": int(idx - 2),
            "end_bar": int(idx),
            "original_lower": as_float(lower),
            "original_upper": as_float(upper),
            "remaining_lower": as_float(remaining_lower),
            "remaining_upper": as_float(remaining_upper),
            "midpoint": as_float((remaining_lower + remaining_upper) / 2) if remaining_size > 0 else None,
            "partial_fill_pct": round(float(max(0.0, min(fill_pct, 1.0))), 4),
            "original_size_atr": round(float(original_size_atr), 4),
            "remaining_size_atr": round(float(remaining_size_atr), 4),
            "displacement_atr": round(float(displacement_atr), 4),
            "distance_to_latest_close_atr": round(float(distance_atr), 4),
            "valid_for_structure": bool(valid_for_structure),
            "valid_for_visual": bool(valid_for_visual),
            "active": filled_at is None and remaining_size > 0,
            "partial_touched_at": unix_time(partial_touched_at) if partial_touched_at is not None else None,
            "filled_at": unix_time(filled_at) if filled_at is not None else None,
            "age_bars": int(max((len(btc.index) - 1) - idx, 0)),
        }
        ledgers[fvg_type].append(item)
        events.append({"time": unix_time(ts), "event_type": f"{fvg_type}_fvg_created", "side": fvg_type, "price": as_float((lower + upper) / 2)})
        if partial_touched_at is not None:
            events.append({"time": unix_time(partial_touched_at), "event_type": "fvg_partially_filled", "side": fvg_type, "price": item["midpoint"]})
        if filled_at is not None:
            events.append({"time": unix_time(filled_at), "event_type": "fvg_fully_filled", "side": fvg_type, "price": as_float((lower + upper) / 2)})

    return ledgers, sorted(events, key=lambda item: item["time"])


def phase1_cluster_tolerance(price, atr_baseline, atr_factor=CLUSTER_ATR_FACTOR):
    return max(abs(price) * CLUSTER_PRICE_PCT, atr_baseline * atr_factor if atr_baseline else 0.0, CLUSTER_MIN_BUFFER)


def phase1_liquidity_clusters(levels, side, atr_baseline, btc):
    candidates = sorted(
        [level for level in levels if level.get("price") is not None and (level.get("time") or level.get("source_time"))],
        key=lambda item: item.get("time") or item.get("source_time"),
    )
    clusters = []

    def cluster_taken_time(cluster_lower, cluster_upper, formed_time):
        if not formed_time:
            return None
        formed_ts = ts_from_unix(formed_time, btc.index)
        future = btc.loc[btc.index > formed_ts]
        if future.empty:
            return None
        if side == "sell":
            taken = future[future["Low"] <= cluster_lower]
        else:
            taken = future[future["High"] >= cluster_upper]
        if taken.empty:
            return None
        return unix_time(taken.index[0])

    def payload(members):
        def physical_key(level):
            return (
                level.get("source_time") or level.get("time"),
                level.get("price"),
            )

        unique_points = {}
        for level in members:
            key = physical_key(level)
            current = unique_points.get(key)
            if current is None or (
                level.get("fvg_liquidity_valid")
                and not current.get("fvg_liquidity_valid")
            ):
                unique_points[key] = level

        prices = [level["price"] for level in members]
        lower = min(prices)
        upper = max(prices)
        width = max(upper - lower, CLUSTER_MIN_BUFFER)
        midpoint = (lower + upper) / 2
        min_visual_width = max(
            atr_baseline * 0.08 if atr_baseline else 0.0,
            abs(midpoint) * 0.0004,
            CLUSTER_MIN_BUFFER,
        )
        visual_lower = lower
        visual_upper = upper
        if visual_upper - visual_lower < min_visual_width:
            padding = (min_visual_width - (visual_upper - visual_lower)) / 2
            visual_lower -= padding
            visual_upper += padding
        kind_key = "isl_count" if side == "sell" else "ish_count"
        swing_key = "sl_count" if side == "sell" else "sh_count"
        kind_name = "ISL" if side == "sell" else "ISH"
        swing_name = "SL" if side == "sell" else "SH"
        formed_time = max(level.get("time") or level.get("source_time") for level in members)
        end_time = cluster_taken_time(lower, upper, formed_time)
        still_active = end_time is None
        return {
            "side": side,
            "lower": as_float(lower),
            "upper": as_float(upper),
            "visual_lower": as_float(visual_lower),
            "visual_upper": as_float(visual_upper),
            "midpoint": as_float(midpoint),
            "level_count": len(unique_points),
            kind_key: sum(1 for level in members if level.get("type") == kind_name),
            swing_key: sum(1 for level in members if level.get("type") == swing_name),
            "oldest_time": min(level.get("time") or level.get("source_time") for level in members),
            "newest_time": formed_time,
            "end_time": end_time,
            "first_taken_time": end_time,
            "last_taken_time": end_time,
            "age_bars": max(level.get("age_bars", 0) for level in members),
            "density": round(len(unique_points) / width, 4),
            "fvg_backed_count": sum(
                1
                for level in unique_points.values()
                if level.get("fvg_liquidity_valid")
            ),
            "fvg_member_count": sum(
                1
                for level in unique_points.values()
                if level.get("fvg_cluster_eligible")
            ),
            "fvg_ids": sorted(
                {
                    (level.get("fvg_context") or {}).get("fvg_id")
                    for level in members
                    if level.get("fvg_cluster_eligible")
                    and (level.get("fvg_context") or {}).get("fvg_id")
                }
            ),
            "active": bool(still_active),
        }

    for level in candidates:
        level_time = level.get("time") or level.get("source_time")
        price = level["price"]
        best_cluster = None
        best_gap = None

        for cluster in clusters:
            current = cluster["members"]
            current_payload = payload(current)
            lower = current_payload["lower"]
            upper = current_payload["upper"]
            if current_payload.get("end_time"):
                if level_time > current_payload["end_time"]:
                    continue
                if level_time == current_payload["end_time"]:
                    sweep_grace = atr_baseline * CLUSTER_SWEEP_GRACE_ATR if atr_baseline else 0.0
                    sweep_extension = max(lower - price, 0.0) if side == "sell" else max(price - upper, 0.0)
                    if sweep_extension > sweep_grace:
                        continue
            if lower <= price <= upper:
                gap = 0.0
            else:
                gap = min(abs(price - lower), abs(price - upper))
            has_intermediate_pair = any(member.get("type") in {"ISL", "ISH"} for member in current) and level.get("type") in {"ISL", "ISH"}
            atr_factor = INTERMEDIATE_CLUSTER_ATR_FACTOR if has_intermediate_pair else CLUSTER_ATR_FACTOR
            tolerance = phase1_cluster_tolerance((current_payload["midpoint"] + price) / 2, atr_baseline, atr_factor)
            if gap <= tolerance and (best_gap is None or gap < best_gap):
                best_cluster = cluster
                best_gap = gap

        if best_cluster is None:
            clusters.append({"members": [level]})
        else:
            best_cluster["members"].append(level)

    return [payload(cluster["members"]) for cluster in clusters]


def phase1_fvg_state_at_time(fvg, level_time, btc, state_cache):
    cache_key = (fvg.get("id"), level_time)
    if cache_key in state_cache:
        return state_cache[cache_key]

    lower = fvg.get("original_lower")
    upper = fvg.get("original_upper")
    created_time = fvg.get("created_time") or fvg.get("candle_3_time")
    if lower is None or upper is None or not created_time or not level_time or created_time > level_time:
        state_cache[cache_key] = None
        return None

    remaining_lower = float(lower)
    remaining_upper = float(upper)
    created_ts = ts_from_unix(created_time, btc.index)
    level_ts = ts_from_unix(level_time, btc.index)
    later = btc.loc[(btc.index > created_ts) & (btc.index <= level_ts)]
    if not later.empty and fvg.get("type") == "bullish":
        lowest_low = float(later["Low"].min())
        if lowest_low <= lower:
            state_cache[cache_key] = None
            return None
        remaining_upper = max(float(lower), min(float(upper), lowest_low))
    elif not later.empty:
        highest_high = float(later["High"].max())
        if highest_high >= upper:
            state_cache[cache_key] = None
            return None
        remaining_lower = min(float(upper), max(float(lower), highest_high))

    state = {
        "remaining_lower": remaining_lower,
        "remaining_upper": remaining_upper,
    }
    state_cache[cache_key] = state
    return state


def phase1_level_fvg_relation(level, fvgs, atr_baseline, btc, state_cache):
    price = level.get("price")
    if price is None:
        return {"relation": "none", "fvg_id": None, "fvg_type": None, "distance_atr": None}

    level_time = level.get("time") or level.get("source_time")
    level_ts = ts_from_unix(level_time, btc.index) if level_time else None
    level_bar = int(btc.index.searchsorted(level_ts, side="left")) if level_ts is not None else None
    is_raw_swing = level.get("type") in {"SL", "SH"}
    tolerance = atr_baseline * FVG_LEVEL_NEAR_ATR if atr_baseline else 0.0
    # Downloaded OHLC values can differ by a few sub-paise decimals even when
    # they represent the same displayed price. Keep exact edge taps inside.
    boundary_epsilon = 0.0001
    best = None
    for fvg in fvgs:
        if not fvg.get("valid_for_structure"):
            continue
        fvg_created_time = fvg.get("created_time") or fvg.get("candle_3_time")
        if level_time and fvg_created_time and fvg_created_time > level_time:
            continue
        fvg_created_ts = ts_from_unix(fvg_created_time, btc.index) if fvg_created_time else None
        fvg_created_bar = int(btc.index.searchsorted(fvg_created_ts, side="left")) if fvg_created_ts is not None else None
        fvg_age_bars = level_bar - fvg_created_bar if level_bar is not None and fvg_created_bar is not None else None
        if is_raw_swing and fvg_age_bars is not None and fvg_age_bars > RAW_SWING_FVG_MAX_AGE_BARS:
            continue
        lower = fvg.get("original_lower")
        upper = fvg.get("original_upper")
        if lower is None or upper is None:
            continue
        fvg_state = phase1_fvg_state_at_time(fvg, level_time, btc, state_cache)
        if not fvg_state:
            continue
        remaining_lower = fvg_state["remaining_lower"]
        remaining_upper = fvg_state["remaining_upper"]

        if lower - boundary_epsilon <= price <= upper + boundary_epsilon:
            distance = 0.0
            relation = "inside"
        elif price < lower:
            distance = lower - price
            relation = "near" if distance <= tolerance else "none"
        else:
            distance = price - upper
            relation = "near" if distance <= tolerance else "none"

        if relation == "none":
            continue

        remaining_relation = "none"
        remaining_distance = None
        if remaining_lower is not None and remaining_upper is not None and remaining_upper >= remaining_lower:
            if remaining_lower - boundary_epsilon <= price <= remaining_upper + boundary_epsilon:
                remaining_relation = "inside"
                remaining_distance = 0.0
            elif price < remaining_lower:
                remaining_distance = remaining_lower - price
                remaining_relation = "near" if remaining_distance <= tolerance else "none"
            else:
                remaining_distance = price - remaining_upper
                remaining_relation = "near" if remaining_distance <= tolerance else "none"

        distance_atr = distance / atr_baseline if atr_baseline else 0.0
        candidate = {
            "relation": relation,
            "remaining_relation": remaining_relation,
            "remaining_lower": as_float(remaining_lower),
            "remaining_upper": as_float(remaining_upper),
            "fvg_id": fvg.get("id"),
            "fvg_type": fvg.get("type"),
            "fvg_created_time": fvg_created_time,
            "fvg_age_bars": fvg_age_bars,
            "distance_atr": round(float(distance_atr), 4),
            "remaining_distance_atr": round(float(remaining_distance / atr_baseline), 4)
            if remaining_distance is not None and atr_baseline
            else None,
        }
        candidate_remaining_rank = {"inside": 2, "near": 1, "none": 0}[candidate["remaining_relation"]]
        if best is None:
            best = candidate
        else:
            best_remaining_rank = {"inside": 2, "near": 1, "none": 0}[best["remaining_relation"]]
            candidate_relation_rank = 1 if candidate["relation"] == "inside" else 0
            best_relation_rank = 1 if best["relation"] == "inside" else 0
            if candidate_remaining_rank > best_remaining_rank:
                best = candidate
            elif candidate_remaining_rank == best_remaining_rank and candidate_relation_rank > best_relation_rank:
                best = candidate
            elif (
                candidate_remaining_rank == best_remaining_rank
                and candidate_relation_rank == best_relation_rank
                and candidate["fvg_created_time"] > best["fvg_created_time"]
            ):
                best = candidate
            elif (
                candidate_remaining_rank == best_remaining_rank
                and candidate_relation_rank == best_relation_rank
                and candidate["fvg_created_time"] == best["fvg_created_time"]
                and candidate["distance_atr"] < best["distance_atr"]
            ):
                best = candidate

    return best or {"relation": "none", "fvg_id": None, "fvg_type": None, "distance_atr": None}


def phase1_apply_fvg_context(levels, fvgs, atr_baseline, btc, state_cache):
    output = []
    for level in levels:
        item = dict(level)
        item["fvg_context"] = phase1_level_fvg_relation(item, fvgs, atr_baseline, btc, state_cache)
        item["fvg_significant"] = item["fvg_context"]["relation"] in {"inside", "near"}
        item["raw_fvg_survival_valid"] = True
        if item.get("type") in {"SL", "SH"}:
            level_time = item.get("time") or item.get("source_time")
            level_ts = ts_from_unix(level_time, btc.index) if level_time else None
            level_bar = int(btc.index.searchsorted(level_ts, side="left")) if level_ts is not None else None
            latest_bar = len(btc.index) - 1
            survival_bar = level_bar + RAW_SWING_FVG_MIN_SURVIVAL_BARS if level_bar is not None else None
            breached_time = item.get("breached_at")
            breached_ts = ts_from_unix(breached_time, btc.index) if breached_time else None
            breached_bar = int(btc.index.searchsorted(breached_ts, side="left")) if breached_ts is not None else None
            item["raw_fvg_survival_valid"] = bool(
                survival_bar is not None
                and latest_bar >= survival_bar
                and (breached_bar is None or breached_bar > survival_bar)
            )
            item["raw_fvg_survival_bars"] = RAW_SWING_FVG_MIN_SURVIVAL_BARS
        # The remaining unfilled zone selects the individual marker. Cluster
        # membership is broader: swings inside the original FVG still describe
        # a real liquidity pool after price has traversed part of the gap.
        item["fvg_cluster_eligible"] = (
            item["fvg_context"]["relation"] == "inside"
            and item["raw_fvg_survival_valid"]
        )
        item["fvg_liquidity_valid"] = False
        output.append(item)
    return output


def phase1_mark_fvg_liquidity_valid(levels, side):
    anchors = {}
    ordered = sorted(levels, key=lambda item: item.get("time") or item.get("source_time") or 0)
    for level in ordered:
        context = level.get("fvg_context") or {}
        fvg_id = context.get("fvg_id")
        price = level.get("price")
        if not fvg_id or price is None:
            continue
        if level.get("type") in {"SL", "SH"} and not level.get("raw_fvg_survival_valid"):
            continue

        # Intermediate structure remains meaningful slightly outside an FVG.
        # Preserve the near relation for later weighting, but promote the level
        # into FVG-backed liquidity without widening the raw SH/SL rule.
        if level.get("type") in {"ISL", "ISH"} and context.get("relation") == "near":
            level["fvg_liquidity_valid"] = True
            continue

        if context.get("relation") != "inside":
            continue

        if context.get("remaining_relation") == "inside":
            level["fvg_liquidity_valid"] = True
            if side == "buy":
                anchors[fvg_id] = max(price, anchors.get(fvg_id, price))
            else:
                anchors[fvg_id] = min(price, anchors.get(fvg_id, price))
            continue

        anchor = anchors.get(fvg_id)
        if anchor is None:
            continue
        if side == "buy" and price > anchor:
            level["fvg_liquidity_valid"] = True
            anchors[fvg_id] = price
        elif side == "sell" and price < anchor:
            level["fvg_liquidity_valid"] = True
            anchors[fvg_id] = price
    return levels


def phase1_raw_fvg_level_ids(levels):
    raw_fvg_level_ids = set()
    for level in levels:
        if level.get("type") not in {"SL", "SH"} or not level.get("fvg_significant"):
            continue
        if not level.get("fvg_liquidity_valid"):
            continue
        fvg_context = level.get("fvg_context") or {}
        fvg_id = fvg_context.get("fvg_id")
        if not fvg_id:
            continue
        raw_fvg_level_ids.add(id(level))
    return raw_fvg_level_ids


def phase1_visual_levels(active_sell_levels, active_buy_levels, fvg_ledgers, atr_baseline, btc, state_cache):
    sell = phase1_apply_fvg_context(active_sell_levels, fvg_ledgers["bullish"], atr_baseline, btc, state_cache)
    buy = phase1_apply_fvg_context(active_buy_levels, fvg_ledgers["bearish"], atr_baseline, btc, state_cache)
    sell = phase1_mark_fvg_liquidity_valid(sell, "sell")
    buy = phase1_mark_fvg_liquidity_valid(buy, "buy")
    sell_raw_fvg_ids = phase1_raw_fvg_level_ids(sell)
    buy_raw_fvg_ids = phase1_raw_fvg_level_ids(buy)

    sell_visual = [
        level for level in sell
        if level.get("type") in {"ISL", "ISH"} or id(level) in sell_raw_fvg_ids
    ]
    buy_visual = [
        level for level in buy
        if level.get("type") in {"ISL", "ISH"} or id(level) in buy_raw_fvg_ids
    ]
    sell_cluster_levels = [
        level for level in sell
        if level.get("type") in {"SL", "ISL"}
    ]
    buy_cluster_levels = [
        level for level in buy
        if level.get("type") in {"SH", "ISH"}
    ]
    return sell, buy, sell_visual, buy_visual, sell_cluster_levels, buy_cluster_levels


def phase1_merge_fvgs(fvgs, fvg_type, atr_baseline, retained_fvg_ids=None):
    retained_fvg_ids = set(retained_fvg_ids or [])
    active = [
        fvg for fvg in fvgs
        if fvg.get("active") and fvg.get("valid_for_structure")
    ]
    visual_fvg_ids = {
        fvg["id"]
        for fvg in active
        if (fvg.get("distance_to_latest_close_atr") or 0) <= FVG_MAX_VISUAL_DISTANCE_ATR
    }
    visual_fvg_ids.update(
        fvg["id"]
        for fvg in active
        if fvg.get("id") in retained_fvg_ids
    )
    if active:
        nearest = min(
            active,
            key=lambda item: item.get("distance_to_latest_close_atr")
            if item.get("distance_to_latest_close_atr") is not None
            else float("inf"),
        )
        visual_fvg_ids.add(nearest["id"])
    candidates = sorted(
        [
            fvg for fvg in fvgs
            if fvg.get("id") in visual_fvg_ids
        ],
        key=lambda item: (item["original_lower"], item["original_upper"]),
    )
    merged = []
    tolerance = atr_baseline * FVG_MERGE_ATR if atr_baseline else 0.0

    for fvg in candidates:
        lower = fvg["original_lower"]
        upper = fvg["original_upper"]
        if lower is None or upper is None:
            continue
        start_bar = fvg.get("start_bar")
        end_bar = fvg.get("end_bar")
        if not merged:
            merged.append(
                {
                    "type": fvg_type,
                    "lower": lower,
                    "upper": upper,
                    "start_time": fvg["candle_1_time"],
                    "oldest_time": fvg["candle_1_time"],
                    "newest_time": fvg["candle_3_time"],
                    "fvg_count": 1,
                    "ids": [fvg["id"]],
                    "start_bar": start_bar,
                    "end_bar": end_bar,
                    "max_original_size_atr": fvg.get("original_size_atr"),
                    "max_displacement_atr": fvg.get("displacement_atr"),
                    "liquidity_backed": fvg["id"] in retained_fvg_ids,
                }
            )
            continue

        current = merged[-1]
        gap = lower - current["upper"]
        current_end_bar = current.get("end_bar")
        bar_gap = None
        if start_bar is not None and current_end_bar is not None:
            bar_gap = max(int(start_bar) - int(current_end_bar), 0)
        time_near = bar_gap is not None and bar_gap <= FVG_MERGE_MAX_GAP_BARS
        if gap <= tolerance and time_near:
            current["lower"] = min(current["lower"], lower)
            current["upper"] = max(current["upper"], upper)
            current["start_time"] = min(current["start_time"], fvg["candle_1_time"])
            current["oldest_time"] = min(current["oldest_time"], fvg["candle_1_time"])
            current["newest_time"] = max(current["newest_time"], fvg["candle_3_time"])
            current["fvg_count"] += 1
            current["ids"].append(fvg["id"])
            current["liquidity_backed"] = current["liquidity_backed"] or fvg["id"] in retained_fvg_ids
            if end_bar is not None:
                current["end_bar"] = max(current.get("end_bar") or end_bar, end_bar)
            current["max_original_size_atr"] = max(current["max_original_size_atr"] or 0, fvg.get("original_size_atr") or 0)
            current["max_displacement_atr"] = max(current["max_displacement_atr"] or 0, fvg.get("displacement_atr") or 0)
        else:
            merged.append(
                {
                    "type": fvg_type,
                    "lower": lower,
                    "upper": upper,
                    "start_time": fvg["candle_1_time"],
                    "oldest_time": fvg["candle_1_time"],
                    "newest_time": fvg["candle_3_time"],
                    "fvg_count": 1,
                    "ids": [fvg["id"]],
                    "start_bar": start_bar,
                    "end_bar": end_bar,
                    "max_original_size_atr": fvg.get("original_size_atr"),
                    "max_displacement_atr": fvg.get("displacement_atr"),
                    "liquidity_backed": fvg["id"] in retained_fvg_ids,
                }
            )

    for zone in merged:
        zone["lower"] = as_float(zone["lower"])
        zone["upper"] = as_float(zone["upper"])
        zone["max_original_size_atr"] = round(float(zone["max_original_size_atr"] or 0), 4)
        zone["max_displacement_atr"] = round(float(zone["max_displacement_atr"] or 0), 4)
    return merged


def phase1_market_skeleton(swing_ledgers):
    highs = swing_ledgers["sh"]
    lows = swing_ledgers["sl"]
    sequence = []

    for prev, curr in zip(highs, highs[1:]):
        label = "HH" if curr["price"] > prev["price"] else "LH" if curr["price"] < prev["price"] else "EH"
        sequence.append({"time": curr["time"], "label": label, "type": "high", "price": curr["price"]})
    for prev, curr in zip(lows, lows[1:]):
        label = "HL" if curr["price"] > prev["price"] else "LL" if curr["price"] < prev["price"] else "EL"
        sequence.append({"time": curr["time"], "label": label, "type": "low", "price": curr["price"]})

    sequence.sort(key=lambda item: item["time"])
    recent = sequence[-6:]
    labels = [item["label"] for item in recent]
    if labels.count("HH") >= 1 and labels.count("HL") >= 1 and labels.count("LL") == 0:
        state = "uptrend"
    elif labels.count("LH") >= 1 and labels.count("LL") >= 1 and labels.count("HH") == 0:
        state = "downtrend"
    elif labels.count("LH") >= 1 and labels.count("HL") >= 1:
        state = "compression"
    elif labels.count("HH") and labels.count("LL"):
        state = "expansion"
    else:
        state = "range"

    return {
        "state": state,
        "recent_sequence": labels[-6:],
        "last_high_type": next((item["label"] for item in reversed(sequence) if item["type"] == "high"), None),
        "last_low_type": next((item["label"] for item in reversed(sequence) if item["type"] == "low"), None),
        "last_change_time": sequence[-1]["time"] if sequence else None,
    }


def phase1_ranges(btc, intermediate_ledgers, swing_ledgers, atr_values):
    isls = sorted(intermediate_ledgers["isl"], key=lambda item: item["confirmation_time"])
    ishs = sorted(intermediate_ledgers["ish"], key=lambda item: item["confirmation_time"])
    events = [
        {"type": "isl", "time": item["confirmation_time"], "source_time": item["time"], "price": item["price"]}
        for item in isls
    ] + [
        {"type": "ish", "time": item["confirmation_time"], "source_time": item["time"], "price": item["price"]}
        for item in ishs
    ]
    events.sort(key=lambda item: item["time"])
    if not events:
        return []

    candle_by_time = {unix_time(ts): idx for idx, ts in enumerate(btc.index)}
    latest_isl = None
    latest_ish = None
    active = None
    ranges = []

    def finalize(end_time, break_side=None):
        nonlocal active
        if not active:
            return
        start_idx = candle_by_time.get(active["start_time"], 0)
        end_idx = candle_by_time.get(end_time, len(btc.index) - 1)
        if end_idx <= start_idx:
            active = None
            return
        window = btc.iloc[start_idx : end_idx + 1]
        inside = ((window["Close"] >= active["lower"]) & (window["Close"] <= active["upper"])).mean()
        duration = end_idx - start_idx
        if duration >= RANGE_MIN_BARS and inside >= RANGE_INSIDE_CLOSE_RATIO:
            width = active["upper"] - active["lower"]
            atr = float(atr_values.iloc[start_idx]) if start_idx < len(atr_values) and atr_values.iloc[start_idx] > 0 else mean_atr(window)
            midpoint = (active["upper"] + active["lower"]) / 2
            ranges.append(
                {
                    **active,
                    "end_time": end_time,
                    "midpoint": as_float(midpoint),
                    "duration": int(duration),
                    "width": as_float(width),
                    "width_atr": round(width / atr, 3) if atr else None,
                    "inside_close_ratio": round(float(inside), 3),
                    "break_side": break_side,
                    "quality": round(100.0 * min(duration / 20.0, 1.0) * min(float(inside), 1.0), 1),
                }
            )
        active = None

    for event in events:
        if event["type"] == "isl":
            latest_isl = event
        else:
            latest_ish = event

        if active:
            event_idx = candle_by_time.get(event["time"])
            if event_idx is not None:
                prior = btc.iloc[candle_by_time[active["start_time"]] : event_idx + 1]
                closed_up = (prior["Close"] > active["upper"]).any()
                closed_down = (prior["Close"] < active["lower"]).any()
                if closed_up:
                    finalize(event["time"], "up")
                elif closed_down:
                    finalize(event["time"], "down")
                elif event["type"] == "isl":
                    active["internal_isl_count"] += 1
                else:
                    active["internal_ish_count"] += 1

        if not active and latest_isl and latest_ish and latest_isl["price"] < latest_ish["price"]:
            active = {
                "start_time": max(latest_isl["time"], latest_ish["time"]),
                "lower": latest_isl["price"],
                "upper": latest_ish["price"],
                "lower_time": latest_isl["source_time"],
                "upper_time": latest_ish["source_time"],
                "internal_isl_count": 0,
                "internal_ish_count": 0,
            }

    if active:
        finalize(unix_time(btc.index[-1]), None)

    return ranges


def phase1_premium_discount(ranges, latest_close):
    active = next((item for item in reversed(ranges) if item.get("break_side") is None), None)
    if not active or active["upper"] <= active["lower"]:
        return {"active": False, "zone": None, "position_pct": None, "range_high": None, "range_low": None, "midpoint": None}

    position = (latest_close - active["lower"]) / (active["upper"] - active["lower"])
    if position < 0.25:
        zone = "deep_discount"
    elif position < 0.5:
        zone = "discount"
    elif position < 0.75:
        zone = "premium"
    else:
        zone = "deep_premium"
    return {
        "active": True,
        "zone": zone,
        "position_pct": round(float(max(0.0, min(position, 1.0))), 4),
        "range_high": as_float(active["upper"]),
        "range_low": as_float(active["lower"]),
        "midpoint": as_float(active["midpoint"]),
    }


def phase1_target_landscape(active_buy_levels, active_sell_levels, buy_clusters, sell_clusters, latest_close):
    buy_above = sorted([level for level in active_buy_levels if level["price"] > latest_close], key=lambda item: item["price"])
    sell_below = sorted([level for level in active_sell_levels if level["price"] < latest_close], key=lambda item: item["price"], reverse=True)
    buy_cluster_above = sorted([c for c in buy_clusters if c["midpoint"] > latest_close], key=lambda item: item["midpoint"])
    sell_cluster_below = sorted([c for c in sell_clusters if c["midpoint"] < latest_close], key=lambda item: item["midpoint"], reverse=True)
    return {
        "long": {
            "nearest": buy_above[0] if buy_above else None,
            "strongest_cluster": max(buy_cluster_above, key=lambda item: (item["level_count"], item["density"]), default=None),
            "candidate_count": len(buy_above),
            "cluster_count": len(buy_cluster_above),
        },
        "short": {
            "nearest": sell_below[0] if sell_below else None,
            "strongest_cluster": max(sell_cluster_below, key=lambda item: (item["level_count"], item["density"]), default=None),
            "candidate_count": len(sell_below),
            "cluster_count": len(sell_cluster_below),
        },
    }


def phase1_structure_engine(btc, swings):
    atr_values = atr_series(btc).rolling(ATR_BASELINE, min_periods=1).mean()
    latest_atr = float(atr_values.iloc[-1]) if not atr_values.empty and atr_values.iloc[-1] > 0 else mean_atr(btc)
    swing_ledgers, swing_events = phase1_swing_ledgers(btc, swings)
    intermediate_ledgers, intermediate_events = phase1_intermediate_ledgers(btc, swing_ledgers)
    fvg_ledgers, fvg_events = phase1_fvg_ledgers(btc)

    all_sell_levels = [*swing_ledgers["sl"], *intermediate_ledgers["isl"]]
    all_buy_levels = [*swing_ledgers["sh"], *intermediate_ledgers["ish"]]
    active_sell_levels = [*filter(lambda item: item.get("active"), all_sell_levels)]
    active_buy_levels = [*filter(lambda item: item.get("active"), all_buy_levels)]
    fvg_state_cache = {}
    active_sell_levels, active_buy_levels, visual_sell_levels, visual_buy_levels, _, _ = phase1_visual_levels(
        active_sell_levels,
        active_buy_levels,
        fvg_ledgers,
        latest_atr,
        btc,
        fvg_state_cache,
    )
    _, _, _, _, all_sell_cluster_levels, all_buy_cluster_levels = phase1_visual_levels(
        all_sell_levels,
        all_buy_levels,
        fvg_ledgers,
        latest_atr,
        btc,
        fvg_state_cache,
    )
    sell_clusters = phase1_liquidity_clusters(all_sell_cluster_levels, "sell", latest_atr, btc)
    buy_clusters = phase1_liquidity_clusters(all_buy_cluster_levels, "buy", latest_atr, btc)
    retained_bullish_fvg_ids = {
        (item.get("fvg_context") or {}).get("fvg_id")
        for item in active_sell_levels
        if item.get("fvg_liquidity_valid")
        and (item.get("fvg_context") or {}).get("fvg_id")
    }
    retained_bearish_fvg_ids = {
        (item.get("fvg_context") or {}).get("fvg_id")
        for item in active_buy_levels
        if item.get("fvg_liquidity_valid")
        and (item.get("fvg_context") or {}).get("fvg_id")
    }
    merged_fvg_zones = {
        "bullish": phase1_merge_fvgs(
            fvg_ledgers["bullish"],
            "bullish",
            latest_atr,
            retained_bullish_fvg_ids,
        ),
        "bearish": phase1_merge_fvgs(
            fvg_ledgers["bearish"],
            "bearish",
            latest_atr,
            retained_bearish_fvg_ids,
        ),
    }
    ranges = phase1_ranges(btc, intermediate_ledgers, swing_ledgers, atr_values)
    latest_close = float(btc["Close"].iloc[-1])

    structure_state = {
        "latest_close": as_float(latest_close),
        "active_sell_levels": len(active_sell_levels),
        "active_buy_levels": len(active_buy_levels),
        "active_isl": sum(1 for item in intermediate_ledgers["isl"] if item.get("active")),
        "active_ish": sum(1 for item in intermediate_ledgers["ish"] if item.get("active")),
        "sell_clusters": len(sell_clusters),
        "buy_clusters": len(buy_clusters),
        "active_bullish_fvgs": sum(1 for item in fvg_ledgers["bullish"] if item.get("active")),
        "active_bearish_fvgs": sum(1 for item in fvg_ledgers["bearish"] if item.get("active")),
    }
    return {
        "structure_state": structure_state,
        "level_ledgers": {
            "swing_highs": swing_ledgers["sh"],
            "swing_lows": swing_ledgers["sl"],
            "ish": intermediate_ledgers["ish"],
            "isl": intermediate_ledgers["isl"],
            "active_buy_levels": active_buy_levels,
            "active_sell_levels": active_sell_levels,
            "visual_buy_levels": visual_buy_levels,
            "visual_sell_levels": visual_sell_levels,
        },
        "fvg_ledgers": fvg_ledgers,
        "merged_fvg_zones": merged_fvg_zones,
        "liquidity_clusters": {"buy": buy_clusters, "sell": sell_clusters},
        "range_state": ranges,
        "premium_discount": phase1_premium_discount(ranges, latest_close),
        "market_skeleton": phase1_market_skeleton(swing_ledgers),
        "target_liquidity_landscape": phase1_target_landscape(active_buy_levels, active_sell_levels, buy_clusters, sell_clusters, latest_close),
        "event_timeline": sorted([*swing_events, *intermediate_events, *fvg_events], key=lambda item: item["time"])[-250:],
    }


def analyze_ticker(ticker, raw_data, multi_index):
    try:
        btc = (raw_data[ticker] if multi_index else raw_data).copy()
    except KeyError:
        return None

    btc.dropna(how="all", inplace=True)
    if btc.empty or len(btc) < 50:
        return None

    btc["Swing_High"] = np.where(
        (btc["High"] > btc["High"].shift(-1)) & (btc["High"] > btc["High"].shift(1)),
        "Swing High",
        "NA",
    )
    btc["Swing_Low"] = np.where(
        (btc["Low"] < btc["Low"].shift(-1)) & (btc["Low"] < btc["Low"].shift(1)),
        "Swing Low",
        "NA",
    )
    btc["Main_Signal"] = np.select(
        [
            (btc["High"] > btc["High"].shift(-1)) & (btc["High"] > btc["High"].shift(1)),
            (btc["Low"] < btc["Low"].shift(-1)) & (btc["Low"] < btc["Low"].shift(1)),
        ],
        ["Swing High", "Swing Low"],
        default="",
    )
    swings = btc[btc["Main_Signal"] != ""].copy()
    if len(swings) < 4:
        return None

    sl_s = swings.loc[swings["Main_Signal"] == "Swing Low", "Low"]
    isl_prices = sl_s.where((sl_s < sl_s.shift(1)) & (sl_s < sl_s.shift(-1)))
    sh_s = swings.loc[swings["Main_Signal"] == "Swing High", "High"]
    ish_prices = sh_s.where((sh_s > sh_s.shift(1)) & (sh_s > sh_s.shift(-1)))

    btc["ISL_Price"] = np.nan
    btc.loc[isl_prices.index, "ISL_Price"] = isl_prices.values
    btc["ISL_Price_Ffill"] = btc["ISL_Price"].ffill()
    btc["ISH_Price"] = np.nan
    btc.loc[ish_prices.index, "ISH_Price"] = ish_prices.values
    btc["ISH_Price_Ffill"] = btc["ISH_Price"].ffill()

    swings["Breaker_Signal"] = (
        (swings["Main_Signal"] == "Swing High")
        & (swings["Main_Signal"].shift(1) == "Swing Low")
        & (swings["Main_Signal"].shift(2) == "Swing High")
        & (swings["Main_Signal"].shift(3) == "Swing Low")
        & (swings["High"] > swings["High"].shift(2))
        & (swings["Low"].shift(1) < swings["Low"].shift(3))
    )

    btc["FVG_Signal"] = np.select(
        [(btc["Low"] > btc["High"].shift(2)), (btc["High"] < btc["Low"].shift(2))],
        ["Bullish_FVG", "Bearish_FVG"],
        default="NA",
    )
    btc["T0_Bull_Low"] = np.where(btc["FVG_Signal"] == "Bullish_FVG", btc["Low"], np.nan)
    btc["T1_Bull_High"] = np.where(btc["FVG_Signal"] == "Bullish_FVG", btc["High"].shift(2), np.nan)
    btc["T0_Bearish_High"] = np.where(btc["FVG_Signal"] == "Bearish_FVG", btc["High"], np.nan)
    btc["T1_Bearish_Low"] = np.where(btc["FVG_Signal"] == "Bearish_FVG", btc["Low"].shift(2), np.nan)
    btc["Mid_Bear_Range"] = np.where(
        btc["FVG_Signal"] == "Bearish_FVG", btc["High"].shift(1) - btc["Low"].shift(1), np.nan
    )
    fvg = btc[btc["FVG_Signal"] != "NA"].copy()
    fvg["Bear_FVG_ts"] = fvg.index.to_series().shift(1)
    fvg["FVG_Overlap"] = np.select(
        [
            (fvg["FVG_Signal"] == "Bullish_FVG")
            & (fvg["FVG_Signal"].shift(1) == "Bearish_FVG")
            & (fvg["T0_Bull_Low"] > fvg["T0_Bearish_High"].shift(1))
            & (fvg["T1_Bearish_Low"].shift(1) > fvg["T1_Bull_High"])
        ],
        ["FVG_Overlap"],
        default="NA",
    )
    fvg_overlap = fvg.loc[fvg["FVG_Overlap"] == "FVG_Overlap", ["Bear_FVG_ts"]]
    ranges = rolling_ranges(btc, isl_prices, ish_prices)
    structure_swings = phase1_structure_swings(btc)
    phase1 = phase1_structure_engine(btc, structure_swings)

    signals = []
    for idx in swings.index[swings["Breaker_Signal"]]:
        pos = swings.index.get_loc(idx)
        if pos < 3:
            continue
        t3_ts = swings.index[pos - 3]
        t2_ts = swings.index[pos - 2]
        t1_ts = swings.index[pos - 1]

        idx_iloc = btc.index.get_loc(idx)
        fvg_end_ts = btc.index[min(idx_iloc + FVG_CONFIRM_AFTER_SIGNAL_CANDLES, len(btc.index) - 1)]

        cands = fvg_overlap[
            (fvg_overlap.index >= t2_ts)
            & (fvg_overlap.index <= fvg_end_ts)
            & (fvg_overlap["Bear_FVG_ts"] >= t2_ts)
        ]
        if cands.empty:
            continue

        t2_high = float(swings.iloc[pos - 2]["High"])
        t3_low = float(swings.iloc[pos - 3]["Low"])
        t1_low = float(swings.iloc[pos - 1]["Low"])
        idx_high = float(btc.loc[idx, "High"])

        prior_breaks_t3_low = btc.loc[(btc.index > t3_ts) & (btc.index < t2_ts), "Low"].lt(t3_low).any()
        if prior_breaks_t3_low:
            continue

        # Hard gate for the breaker itself: no higher-high break, no signal.
        if idx_high <= t2_high:
            continue

        rise = t2_high - t3_low
        drop = t2_high - t1_low
        if rise <= 0 or drop <= 0:
            continue
        ratio = round(drop / rise, 3) if rise > 0 else 0.0

        t2_iloc = btc.index.get_loc(t2_ts)
        t1_iloc = btc.index.get_loc(t1_ts)
        atr_baseline = mean_atr(btc.iloc[max(0, t2_iloc - ATR_BASELINE + 1) : t2_iloc + 1])
        ds_full = btc.iloc[max(0, t2_iloc - 1) : t1_iloc + 1]
        prev_c = ds_full["Close"].shift(1).fillna(ds_full["Close"].iloc[0])
        tr_all = pd.concat(
            [
                ds_full["High"] - ds_full["Low"],
                (ds_full["High"] - prev_c).abs(),
                (ds_full["Low"] - prev_c).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_downswing = tr_all.iloc[1:].mean() if len(tr_all) > 1 else atr_baseline
        atr_ratio = round(atr_downswing / atr_baseline, 3) if atr_baseline > 0 else 0.0

        bear_window = fvg[
            (fvg.index >= t2_ts) & (fvg.index <= idx) & (fvg["FVG_Signal"] == "Bearish_FVG")
        ]
        fvg_size = bear_window.apply(
            lambda r: max(r["T1_Bearish_Low"] - r["T0_Bearish_High"], 0.0), axis=1
        ).sum()
        fvg_atr = round(fvg_size / atr_baseline, 3) if atr_baseline > 0 else 0.0
        score = round(ratio * atr_ratio * fvg_atr, 4)

        metrics = {}
        metrics.update(accumulated_liquidity(btc, isl_prices, t3_ts, t1_ts, t3_low, t1_low, atr_baseline))
        metrics.update(unresolved_deeper_isl(btc, t3_ts, t1_low, atr_baseline))
        metrics.update(target_liquidity(btc, swings, idx, idx_high, atr_baseline))
        metrics.update(bullish_fvg_sweep_retest(btc, t1_ts, t1_low))
        metrics.update(range_context_for_signal(ranges, t1_ts))
        isl_level = metrics.get("current_isl")
        isl_time = metrics.get("current_isl_time")
        isl_sweep = bool(metrics.get("current_isl_swept"))

        signals.append(
            {
                "time": unix_time(idx),
                "timestamp": str(idx),
                "price": as_float(btc.loc[idx, "Close"]),
                "score": score,
                "ratio": ratio,
                "atr_ratio": atr_ratio,
                "fvg_atr": fvg_atr,
                "isl_sweep": isl_sweep,
                "isl_level": as_float(isl_level),
                "levels": {
                    "T3 Low": as_float(t3_low),
                    "T2 High": as_float(t2_high),
                    "T1 Sweep Low": as_float(t1_low),
                    "Signal High": as_float(idx_high),
                    "Current ISL": as_float(metrics.get("current_isl")),
                    "Base ISL": as_float(metrics.get("base_isl")),
                    "Base ISH": as_float(metrics.get("base_ish")),
                    "Deeper ISL": as_float(metrics.get("nearest_deeper_isl")),
                    "Bull FVG Lower": as_float(metrics.get("bull_fvg_lower")),
                    "Bull FVG Upper": as_float(metrics.get("bull_fvg_upper")),
                },
                "level_times": {
                    "T3 Low": unix_time(t3_ts),
                    "T2 High": unix_time(t2_ts),
                    "T1 Sweep Low": unix_time(t1_ts),
                    "Signal High": unix_time(idx),
                    "Current ISL": isl_time,
                    "Base ISL": metrics.get("base_isl_time"),
                    "Base ISH": metrics.get("base_ish_time"),
                    "Deeper ISL": unix_time(t3_ts) if metrics.get("nearest_deeper_isl") is not None else None,
                    "Bull FVG": metrics.get("bull_fvg_time"),
                },
                "metrics": metrics,
            }
        )

    candles = [
        {
            "time": unix_time(ts),
            "open": as_float(row["Open"]),
            "high": as_float(row["High"]),
            "low": as_float(row["Low"]),
            "close": as_float(row["Close"]),
        }
        for ts, row in btc.iterrows()
        if not pd.isna(row["Open"])
    ]

    swing_marks = []
    for ts, row in swings.iterrows():
        swing_marks.append(
            {
                "time": unix_time(ts),
                "type": "high" if row["Main_Signal"] == "Swing High" else "low",
                "price": as_float(row["High"] if row["Main_Signal"] == "Swing High" else row["Low"]),
            }
        )

    rolling_levels = rolling_context_levels(btc, isl_prices, ish_prices)

    return {
        "ticker": ticker,
        "candles": candles,
        "signals": signals,
        "swings": swing_marks,
        "rolling_levels": rolling_levels,
        "ranges": ranges,
        "phase1_market_structure": phase1,
        "structure_state": phase1["structure_state"],
        "level_ledgers": phase1["level_ledgers"],
        "fvg_ledgers": phase1["fvg_ledgers"],
        "merged_fvg_zones": phase1["merged_fvg_zones"],
        "liquidity_clusters": phase1["liquidity_clusters"],
        "range_state": phase1["range_state"],
        "premium_discount": phase1["premium_discount"],
        "market_skeleton": phase1["market_skeleton"],
        "target_liquidity_landscape": phase1["target_liquidity_landscape"],
        "event_timeline": phase1["event_timeline"],
    }


def main():
    parser = argparse.ArgumentParser(description="Export Breaker+FVG chart dashboard data.")
    parser.add_argument("--period-days", type=int, default=PERIOD_DAYS, help="Download window in days. Keep <= 59 for Yahoo intraday reliability.")
    parser.add_argument("--period", default=None, help="Optional yfinance period string such as 59d, 6mo, 1y, or 2y. Overrides --period-days.")
    parser.add_argument("--interval", default=INTERVAL, help="Yahoo interval, e.g. 1h, 30m, 1d.")
    parser.add_argument("--limit", type=int, default=None, help="Optional ticker limit for quick testing.")
    parser.add_argument("--output-file", type=Path, default=OUTPUT_FILE, help="Output JS file. Use a separate file for research exports to avoid overwriting the dashboard payload.")
    args = parser.parse_args()

    tickers = pd.read_csv(SYMBOLS_FILE)["Symbol"].dropna().tolist()
    if args.limit:
        tickers = tickers[: args.limit]

    print(f"Downloading {len(tickers)} tickers...")
    download_period = args.period or f"{args.period_days}d"
    raw_data = yf.download(
        tickers,
        period=download_period,
        interval=args.interval,
        group_by="ticker",
        auto_adjust=True,
        progress=True,
        threads=True,
    )
    multi_index = isinstance(raw_data.columns, pd.MultiIndex)

    payload = {
        "generated_at": pd.Timestamp.now(tz="Asia/Kolkata").isoformat(),
        "schema_version": 3,
        "period_days": args.period_days,
        "period": download_period,
        "interval": args.interval,
        "fvg_confirm_after_signal_candles": FVG_CONFIRM_AFTER_SIGNAL_CANDLES,
        "tickers": [],
    }

    for ticker in tickers:
        item = analyze_ticker(ticker, raw_data, multi_index)
        if item is not None:
            payload["tickers"].append(item)
            print(f"{ticker}: {len(item['signals'])} signals")

    if not payload["tickers"]:
        print("No chart data was returned. Check yfinance/network availability before opening the dashboard.")

    js = "window.BREAKER_FVG_DATA = " + json.dumps(payload, separators=(",", ":"), allow_nan=False) + ";\n"
    args.output_file.write_text(js, encoding="utf-8")
    print(f"Wrote {args.output_file}")


if __name__ == "__main__":
    main()
