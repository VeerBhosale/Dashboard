"""Research logger for Breaker+FVG trade ideas.

The dashboard payload is the source of truth. This module turns generated
signals into stable trade-idea records, merging repeated signals that come from
the same liquidity sweep while using the latest structure for entry modelling.
"""

import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "breaker_fvg_dashboard_data.js"
RESEARCH_LOG = BASE_DIR / "breaker_fvg_research_log.csv"
TIMESERIES_LOG = BASE_DIR / "breaker_fvg_trade_timeseries.csv"
PRICE_ROUND_DECIMALS = 2
PRE_SIGNAL_CANDLES = 40
POST_SIGNAL_CANDLES = 80


FIELDNAMES = [
    "trade_idea_id",
    "ticker",
    "first_signal_time",
    "latest_signal_time",
    "signal_count",
    "sweep_low_time",
    "sweep_low_price",
    "first_signal_price",
    "latest_signal_price",
    "latest_signal_high",
    "bull_fvg_lower",
    "bull_fvg_upper",
    "range_50",
    "fvg_50",
    "entry_price",
    "entry_source",
    "entry_model_ready",
    "stop_price",
    "risk",
    "target_1r",
    "target_2r",
    "target_3r",
    "entry_filled",
    "entry_time",
    "entry_delay_candles",
    "stop_hit",
    "stop_time",
    "target_1r_hit",
    "target_1r_time",
    "target_2r_hit",
    "target_2r_time",
    "target_3r_hit",
    "target_3r_time",
    "first_terminal_event",
    "final_status",
    "max_favorable_r",
    "max_adverse_r",
    "best_score",
    "latest_score",
    "best_meter_probability",
    "latest_meter_probability",
    "isl_sweep",
    "protected_lows",
    "bull_fvg_retest",
    "range_quality",
    "range_width_atr",
    "range_age",
    "target_highs",
    "target_distance_atr",
    "deeper_isl_pending",
    "deeper_isl_atr",
    "atr_ratio",
    "fvg_atr",
    "last_updated_at",
]


TIMESERIES_FIELDNAMES = [
    "trade_idea_id",
    "ticker",
    "candle_time",
    "window_index",
    "bars_from_first_signal",
    "bars_from_latest_signal",
    "bars_from_entry",
    "phase",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "candle_range",
    "body",
    "upper_wick",
    "lower_wick",
    "close_location",
    "true_range",
    "atr_20",
    "atr_percentile_60",
    "range_percentile_60",
    "body_percentile_60",
    "return_1",
    "return_3",
    "return_5",
    "r_open",
    "r_high",
    "r_low",
    "r_close",
    "distance_to_entry_r",
    "distance_to_stop_r",
    "distance_to_1r",
    "distance_to_2r",
    "distance_to_3r",
    "touches_entry",
    "touches_stop",
    "touches_1r",
    "touches_2r",
    "touches_3r",
    "entry_price",
    "stop_price",
    "target_1r",
    "target_2r",
    "target_3r",
    "latest_score",
    "latest_meter_probability",
    "final_status",
]


def load_dashboard_payload(path=DATA_FILE):
    text = Path(path).read_text(encoding="utf-8").strip()
    prefix = "window.BREAKER_FVG_DATA = "
    if text.startswith(prefix):
        text = text[len(prefix) :]
    if text.endswith(";"):
        text = text[:-1]
    return json.loads(text)


def as_float(value):
    if value in ("", None):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def as_int(value):
    if value in ("", None):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def rounded(value, digits=4):
    value = as_float(value)
    if value is None:
        return ""
    return round(value, digits)


def bool_text(value):
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return bool(value)


def clamp(value, low, high):
    return max(low, min(high, value))


def norm(value, low, high):
    value = as_float(value)
    if value is None:
        return 0.0
    return clamp((value - low) / (high - low), 0.0, 1.0)


def signal_probability(signal):
    metrics = signal.get("metrics") or {}
    atr_ratio = signal.get("atr_ratio")
    fvg_atr = signal.get("fvg_atr")
    sharpness = (
        0.34 * norm(atr_ratio, 0.92, 1.45)
        + 0.30 * norm(fvg_atr, 0.30, 1.02)
        + 0.18 * norm(signal.get("score"), 0.58, 1.85)
    )
    liquidity = (
        0.06 * norm(metrics.get("protected_lows") or 0, 0, 2)
        + 0.04 * norm(metrics.get("low_hold") or 0, 0, 10)
        + 0.04 * (1 if metrics.get("bull_fvg_retest") else 0)
        + 0.04 * (1 if metrics.get("current_isl_swept") or signal.get("isl_sweep") else 0)
    )
    risk = (
        0.08 * (1 if metrics.get("deeper_isl_pending") and (metrics.get("deeper_isl_atr") or 0) < 1.0 else 0)
        + 0.05 * (1 if atr_ratio is not None and atr_ratio < 0.92 else 0)
        + 0.05 * (1 if fvg_atr is not None and fvg_atr < 0.30 else 0)
    )
    return clamp(0.22 + sharpness + liquidity - risk, 0.05, 0.92)


def short_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def trade_idea_id(ticker, signal):
    levels = signal.get("levels") or {}
    times = signal.get("level_times") or {}
    sweep_time = times.get("T1 Sweep Low")
    sweep_low = as_float(levels.get("T1 Sweep Low"))
    rounded_low = round(sweep_low, PRICE_ROUND_DECIMALS) if sweep_low is not None else "na"
    raw = f"{ticker}|{sweep_time}|{rounded_low}"
    return f"{ticker}_{sweep_time}_{short_hash(raw)}"


def empty_row():
    return {field: "" for field in FIELDNAMES}


def load_existing_rows(path=RESEARCH_LOG):
    if not Path(path).exists():
        return {}
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return {row["trade_idea_id"]: {field: row.get(field, "") for field in FIELDNAMES} for row in reader}


def write_rows(rows, path=RESEARCH_LOG):
    ordered = sorted(
        rows.values(),
        key=lambda row: (row.get("first_signal_time") or "0", row.get("ticker") or ""),
    )
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(ordered)


def write_timeseries_rows(rows, path=TIMESERIES_LOG):
    rows = sorted(rows, key=lambda row: (row["ticker"], row["trade_idea_id"], row["candle_time"]))
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TIMESERIES_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def candle_index(candles):
    return {candle.get("time"): index for index, candle in enumerate(candles or [])}


def latest_signal_for_row(row, signals_by_id):
    idea_id = row.get("trade_idea_id")
    signals = signals_by_id.get(idea_id) or []
    if not signals:
        return None
    return max(signals, key=lambda item: item["signal"].get("time") or 0)


def set_if_blank(row, key, value):
    if row.get(key) in ("", None) and value not in ("", None):
        row[key] = value


def update_trade_setup(row, ticker, signal, probability):
    levels = signal.get("levels") or {}
    times = signal.get("level_times") or {}
    metrics = signal.get("metrics") or {}

    signal_time = signal.get("time")
    signal_price = as_float(signal.get("price"))
    sweep_low = as_float(levels.get("T1 Sweep Low"))
    signal_high = as_float(levels.get("Signal High"))
    fvg_lower = as_float(levels.get("Bull FVG Lower") or metrics.get("bull_fvg_lower"))
    fvg_upper = as_float(levels.get("Bull FVG Upper") or metrics.get("bull_fvg_upper"))
    score = as_float(signal.get("score"))

    row["ticker"] = ticker
    set_if_blank(row, "first_signal_time", signal_time)
    set_if_blank(row, "first_signal_price", signal_price)
    row["latest_signal_time"] = signal_time
    row["latest_signal_price"] = signal_price
    row["latest_signal_high"] = signal_high
    row["sweep_low_time"] = times.get("T1 Sweep Low")
    row["sweep_low_price"] = sweep_low
    row["bull_fvg_lower"] = fvg_lower
    row["bull_fvg_upper"] = fvg_upper
    row["latest_score"] = score
    row["latest_meter_probability"] = round(probability, 4)
    existing_best_score = as_float(row.get("best_score"))
    score_candidates = [value for value in (existing_best_score, score) if value is not None]
    row["best_score"] = max(score_candidates) if score_candidates else ""
    row["best_meter_probability"] = max(as_float(row.get("best_meter_probability")) or 0.0, probability)

    row["isl_sweep"] = bool(signal.get("isl_sweep"))
    row["protected_lows"] = metrics.get("protected_lows")
    row["bull_fvg_retest"] = bool(metrics.get("bull_fvg_retest"))
    row["range_quality"] = metrics.get("range_quality")
    row["range_width_atr"] = metrics.get("range_width_atr")
    row["range_age"] = metrics.get("range_age")
    row["target_highs"] = metrics.get("target_highs")
    row["target_distance_atr"] = metrics.get("target_distance_atr")
    row["deeper_isl_pending"] = bool(metrics.get("deeper_isl_pending"))
    row["deeper_isl_atr"] = metrics.get("deeper_isl_atr")
    row["atr_ratio"] = signal.get("atr_ratio")
    row["fvg_atr"] = signal.get("fvg_atr")

    row["entry_model_ready"] = False
    row["entry_source"] = ""
    if sweep_low is None or signal_high is None or fvg_lower is None or fvg_upper is None:
        return

    range_50 = sweep_low + 0.5 * (signal_high - sweep_low)
    fvg_50 = fvg_lower + 0.5 * (fvg_upper - fvg_lower)
    entry = max(range_50, fvg_50)
    risk = entry - sweep_low
    if risk <= 0:
        return

    row["range_50"] = round(range_50, 4)
    row["fvg_50"] = round(fvg_50, 4)
    row["entry_price"] = round(entry, 4)
    row["entry_source"] = "range_50" if range_50 >= fvg_50 else "fvg_50"
    row["entry_model_ready"] = True
    row["stop_price"] = round(sweep_low, 4)
    row["risk"] = round(risk, 4)
    row["target_1r"] = round(entry + risk, 4)
    row["target_2r"] = round(entry + 2 * risk, 4)
    row["target_3r"] = round(entry + 3 * risk, 4)


def update_outcome(row, candles):
    if not bool_text(row.get("entry_model_ready")):
        row["final_status"] = "entry_model_unavailable"
        return

    entry = as_float(row.get("entry_price"))
    stop = as_float(row.get("stop_price"))
    risk = as_float(row.get("risk"))
    latest_signal_time = as_int(row.get("latest_signal_time"))
    if entry is None or stop is None or risk is None or risk <= 0 or latest_signal_time is None:
        row["final_status"] = "entry_model_unavailable"
        return

    target_1r = as_float(row.get("target_1r"))
    target_2r = as_float(row.get("target_2r"))
    target_3r = as_float(row.get("target_3r"))
    start_index = next((i for i, candle in enumerate(candles) if candle.get("time") > latest_signal_time), None)
    if start_index is None:
        row["final_status"] = "no_future_candles"
        return

    entry_index = None
    for i in range(start_index, len(candles)):
        candle = candles[i]
        if as_float(candle.get("low")) <= entry:
            entry_index = i
            row["entry_filled"] = True
            row["entry_time"] = candle.get("time")
            row["entry_delay_candles"] = i - start_index + 1
            break

    if entry_index is None:
        row["entry_filled"] = False
        row["final_status"] = "no_entry_yet"
        return

    max_favorable = 0.0
    max_adverse = 0.0
    first_terminal = ""
    first_terminal_time = None

    for i in range(entry_index, len(candles)):
        candle = candles[i]
        high = as_float(candle.get("high"))
        low = as_float(candle.get("low"))
        if high is None or low is None:
            continue

        max_favorable = max(max_favorable, (high - entry) / risk)
        max_adverse = min(max_adverse, (low - entry) / risk)

        events = []
        if low <= stop:
            events.append(("stop", candle.get("time")))
        if target_1r is not None and high >= target_1r:
            events.append(("target_1r", candle.get("time")))
        if target_2r is not None and high >= target_2r:
            events.append(("target_2r", candle.get("time")))
        if target_3r is not None and high >= target_3r:
            events.append(("target_3r", candle.get("time")))

        if high >= (target_1r or float("inf")):
            row["target_1r_hit"] = True
            set_if_blank(row, "target_1r_time", candle.get("time"))
        if high >= (target_2r or float("inf")):
            row["target_2r_hit"] = True
            set_if_blank(row, "target_2r_time", candle.get("time"))
        if high >= (target_3r or float("inf")):
            row["target_3r_hit"] = True
            set_if_blank(row, "target_3r_time", candle.get("time"))
        if low <= stop:
            row["stop_hit"] = True
            set_if_blank(row, "stop_time", candle.get("time"))

        if not first_terminal and events:
            names = {name for name, _ in events}
            first_terminal = "ambiguous_stop_and_target" if "stop" in names and len(names) > 1 else events[0][0]
            first_terminal_time = candle.get("time")

    row["max_favorable_r"] = round(max_favorable, 3)
    row["max_adverse_r"] = round(max_adverse, 3)
    row["first_terminal_event"] = first_terminal

    if first_terminal:
        row["final_status"] = first_terminal
    elif bool_text(row.get("target_2r_hit")):
        row["final_status"] = "target_2r_hit"
    elif bool_text(row.get("target_1r_hit")):
        row["final_status"] = "target_1r_hit"
    elif bool_text(row.get("stop_hit")):
        row["final_status"] = "stop_hit"
    else:
        row["final_status"] = "entry_open"


def percentile_rank(history, value):
    value = as_float(value)
    clean = [as_float(item) for item in history]
    clean = [item for item in clean if item is not None]
    if value is None or not clean:
        return ""
    return round(100.0 * sum(item <= value for item in clean) / len(clean), 2)


def candle_features(candles):
    features = []
    true_ranges = []
    ranges = []
    bodies = []

    for index, candle in enumerate(candles):
        open_ = as_float(candle.get("open"))
        high = as_float(candle.get("high"))
        low = as_float(candle.get("low"))
        close = as_float(candle.get("close"))
        prev_close = as_float(candles[index - 1].get("close")) if index > 0 else close

        if None in (open_, high, low, close):
            features.append({})
            true_ranges.append(None)
            ranges.append(None)
            bodies.append(None)
            continue

        candle_range = high - low
        body = abs(close - open_)
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low
        true_range = max(candle_range, abs(high - prev_close), abs(low - prev_close)) if prev_close is not None else candle_range

        true_ranges.append(true_range)
        ranges.append(candle_range)
        bodies.append(body)

        atr_window = [item for item in true_ranges[max(0, index - 19) : index + 1] if item is not None]
        atr_20 = sum(atr_window) / len(atr_window) if atr_window else None
        rank_start = max(0, index - 59)

        close_location = (close - low) / candle_range if candle_range > 0 else None
        return_1 = (close - prev_close) / prev_close if prev_close else None
        close_3 = as_float(candles[index - 3].get("close")) if index >= 3 else None
        close_5 = as_float(candles[index - 5].get("close")) if index >= 5 else None

        features.append(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": candle.get("volume", ""),
                "candle_range": candle_range,
                "body": body,
                "upper_wick": upper_wick,
                "lower_wick": lower_wick,
                "close_location": close_location,
                "true_range": true_range,
                "atr_20": atr_20,
                "atr_percentile_60": percentile_rank(true_ranges[rank_start : index + 1], true_range),
                "range_percentile_60": percentile_rank(ranges[rank_start : index + 1], candle_range),
                "body_percentile_60": percentile_rank(bodies[rank_start : index + 1], body),
                "return_1": return_1,
                "return_3": (close - close_3) / close_3 if close_3 else None,
                "return_5": (close - close_5) / close_5 if close_5 else None,
            }
        )

    return features


def trade_phase(candle_time, first_signal_time, latest_signal_time, entry_time, stop_time, target_time):
    if first_signal_time and candle_time < first_signal_time:
        return "pre_signal"
    if latest_signal_time and candle_time <= latest_signal_time:
        return "signal_build"
    if entry_time and candle_time >= entry_time:
        if stop_time and candle_time >= stop_time:
            return "post_stop"
        if target_time and candle_time >= target_time:
            return "post_target"
        return "post_entry"
    return "signal_to_entry"


def build_trade_timeseries(rows, payload):
    candles_by_ticker = {
        item.get("ticker"): item.get("candles") or []
        for item in payload.get("tickers", [])
    }
    output = []

    for row in rows.values():
        ticker = row.get("ticker")
        candles = candles_by_ticker.get(ticker) or []
        if not candles:
            continue

        first_signal_time = as_int(row.get("first_signal_time"))
        latest_signal_time = as_int(row.get("latest_signal_time"))
        entry_time = as_int(row.get("entry_time"))
        stop_time = as_int(row.get("stop_time"))
        target_time = as_int(row.get("target_2r_time")) or as_int(row.get("target_1r_time")) or as_int(row.get("target_3r_time"))
        if first_signal_time is None or latest_signal_time is None:
            continue

        index_by_time = candle_index(candles)
        first_index = index_by_time.get(first_signal_time)
        latest_index = index_by_time.get(latest_signal_time)
        if first_index is None or latest_index is None:
            continue

        entry_index = index_by_time.get(entry_time) if entry_time else None
        stop_index = index_by_time.get(stop_time) if stop_time else None
        target_index = index_by_time.get(target_time) if target_time else None

        start = max(0, first_index - PRE_SIGNAL_CANDLES)
        end_anchor = max(index for index in (latest_index, entry_index or 0, stop_index or 0, target_index or 0) if index is not None)
        end = min(len(candles) - 1, end_anchor + POST_SIGNAL_CANDLES)

        entry = as_float(row.get("entry_price"))
        stop = as_float(row.get("stop_price"))
        risk = as_float(row.get("risk"))
        target_1r = as_float(row.get("target_1r"))
        target_2r = as_float(row.get("target_2r"))
        target_3r = as_float(row.get("target_3r"))
        features = candle_features(candles)

        for index in range(start, end + 1):
            candle = candles[index]
            feature = features[index]
            if not feature:
                continue
            candle_time = candle.get("time")
            high = feature["high"]
            low = feature["low"]
            open_ = feature["open"]
            close = feature["close"]
            ready = entry is not None and stop is not None and risk is not None and risk > 0
            phase = trade_phase(candle_time, first_signal_time, latest_signal_time, entry_time, stop_time, target_time)

            output.append(
                {
                    "trade_idea_id": row.get("trade_idea_id"),
                    "ticker": ticker,
                    "candle_time": candle_time,
                    "window_index": index - first_index,
                    "bars_from_first_signal": index - first_index,
                    "bars_from_latest_signal": index - latest_index,
                    "bars_from_entry": "" if entry_index is None else index - entry_index,
                    "phase": phase,
                    "open": rounded(open_),
                    "high": rounded(high),
                    "low": rounded(low),
                    "close": rounded(close),
                    "volume": feature.get("volume", ""),
                    "candle_range": rounded(feature.get("candle_range")),
                    "body": rounded(feature.get("body")),
                    "upper_wick": rounded(feature.get("upper_wick")),
                    "lower_wick": rounded(feature.get("lower_wick")),
                    "close_location": rounded(feature.get("close_location")),
                    "true_range": rounded(feature.get("true_range")),
                    "atr_20": rounded(feature.get("atr_20")),
                    "atr_percentile_60": feature.get("atr_percentile_60"),
                    "range_percentile_60": feature.get("range_percentile_60"),
                    "body_percentile_60": feature.get("body_percentile_60"),
                    "return_1": rounded(feature.get("return_1"), 6),
                    "return_3": rounded(feature.get("return_3"), 6),
                    "return_5": rounded(feature.get("return_5"), 6),
                    "r_open": rounded((open_ - entry) / risk, 4) if ready else "",
                    "r_high": rounded((high - entry) / risk, 4) if ready else "",
                    "r_low": rounded((low - entry) / risk, 4) if ready else "",
                    "r_close": rounded((close - entry) / risk, 4) if ready else "",
                    "distance_to_entry_r": rounded((close - entry) / risk, 4) if ready else "",
                    "distance_to_stop_r": rounded((close - stop) / risk, 4) if ready else "",
                    "distance_to_1r": rounded((target_1r - close) / risk, 4) if ready and target_1r is not None else "",
                    "distance_to_2r": rounded((target_2r - close) / risk, 4) if ready and target_2r is not None else "",
                    "distance_to_3r": rounded((target_3r - close) / risk, 4) if ready and target_3r is not None else "",
                    "touches_entry": bool(ready and low <= entry <= high),
                    "touches_stop": bool(ready and low <= stop),
                    "touches_1r": bool(ready and target_1r is not None and high >= target_1r),
                    "touches_2r": bool(ready and target_2r is not None and high >= target_2r),
                    "touches_3r": bool(ready and target_3r is not None and high >= target_3r),
                    "entry_price": rounded(entry),
                    "stop_price": rounded(stop),
                    "target_1r": rounded(target_1r),
                    "target_2r": rounded(target_2r),
                    "target_3r": rounded(target_3r),
                    "latest_score": row.get("latest_score"),
                    "latest_meter_probability": row.get("latest_meter_probability"),
                    "final_status": row.get("final_status"),
                }
            )

    return output


def update_research_log(payload=None, log_path=RESEARCH_LOG, timeseries_path=TIMESERIES_LOG):
    payload = payload or load_dashboard_payload()
    rows = load_existing_rows(log_path)
    signals_by_id = {}
    candles_by_ticker = {}

    for item in payload.get("tickers", []):
        ticker = item.get("ticker")
        candles = item.get("candles") or []
        candles_by_ticker[ticker] = candles
        for signal in item.get("signals") or []:
            idea_id = trade_idea_id(ticker, signal)
            signals_by_id.setdefault(idea_id, []).append({"ticker": ticker, "signal": signal})

    now = datetime.now(timezone.utc).isoformat()
    for idea_id, signal_items in signals_by_id.items():
        signal_items.sort(key=lambda item: item["signal"].get("time") or 0)
        first = signal_items[0]
        latest = signal_items[-1]
        row = rows.get(idea_id) or empty_row()
        row["trade_idea_id"] = idea_id
        row["signal_count"] = max(as_int(row.get("signal_count")) or 0, len(signal_items))

        if row.get("first_signal_time") in ("", None):
            update_trade_setup(row, first["ticker"], first["signal"], signal_probability(first["signal"]))
        update_trade_setup(row, latest["ticker"], latest["signal"], signal_probability(latest["signal"]))
        update_outcome(row, candles_by_ticker.get(latest["ticker"]) or [])
        row["last_updated_at"] = now
        rows[idea_id] = row

    for row in rows.values():
        ticker = row.get("ticker")
        if ticker in candles_by_ticker:
            update_outcome(row, candles_by_ticker[ticker])
            row["last_updated_at"] = now

    write_rows(rows, log_path)
    write_timeseries_rows(build_trade_timeseries(rows, payload), timeseries_path)
    return len(rows)


def main():
    count = update_research_log()
    print(f"Updated {RESEARCH_LOG} with {count} trade ideas.")


if __name__ == "__main__":
    main()
