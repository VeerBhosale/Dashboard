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
    }


def main():
    parser = argparse.ArgumentParser(description="Export Breaker+FVG chart dashboard data.")
    parser.add_argument("--period-days", type=int, default=PERIOD_DAYS, help="Download window in days. Keep <= 59 for Yahoo intraday reliability.")
    parser.add_argument("--interval", default=INTERVAL, help="Yahoo interval, e.g. 1h, 30m, 1d.")
    parser.add_argument("--limit", type=int, default=None, help="Optional ticker limit for quick testing.")
    args = parser.parse_args()

    tickers = pd.read_csv(SYMBOLS_FILE)["Symbol"].dropna().tolist()
    if args.limit:
        tickers = tickers[: args.limit]

    print(f"Downloading {len(tickers)} tickers...")
    raw_data = yf.download(
        tickers,
        period=f"{args.period_days}d",
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
    OUTPUT_FILE.write_text(js, encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
