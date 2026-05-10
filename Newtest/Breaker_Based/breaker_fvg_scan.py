"""Telegram alert sender for the Breaker+FVG dashboard payload.

This script intentionally does not download market data or recalculate signals.
The dashboard exporter is the single source of truth; this script reads the
freshly written breaker_fvg_dashboard_data.js file and sends Telegram alerts
from that exact payload.
"""

import json
import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from breaker_fvg_research import update_research_log


BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "breaker_fvg_dashboard_data.js"
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://veerbhosale.github.io/Dashboard/")
ALERT_POSITION = -2
IST = ZoneInfo("Asia/Kolkata")


def telegram_alert(message):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("Telegram alert skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Telegram alert failed: {exc.__class__.__name__}")


def load_dashboard_payload():
    text = DATA_FILE.read_text(encoding="utf-8").strip()
    prefix = "window.BREAKER_FVG_DATA = "
    if text.startswith(prefix):
        text = text[len(prefix) :]
    if text.endswith(";"):
        text = text[:-1]
    return json.loads(text)


def clamp(value, low, high):
    return max(low, min(high, value))


def norm(value, low, high):
    if value is None:
        return 0.0
    try:
        if math.isnan(value):
            return 0.0
    except TypeError:
        return 0.0
    return clamp((float(value) - low) / (high - low), 0.0, 1.0)


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

    probability = clamp(0.22 + sharpness + liquidity - risk, 0.05, 0.92)
    stage = "Neutral"
    if probability < 0.4:
        stage = "Bad"
    if probability >= 0.65:
        stage = "Good"
    return probability, stage


def format_ist_time(unix_time):
    if unix_time is None:
        return "time n/a"
    try:
        dt = datetime.fromtimestamp(int(unix_time), tz=IST)
    except (TypeError, ValueError, OSError):
        return "time n/a"
    return dt.strftime("%d-%b %H:%M IST")


def format_signal_line(rank, ticker, signal):
    probability, stage = signal_probability(signal)
    score = signal.get("score")
    score_text = f"{float(score):.4f}" if score is not None else "n/a"
    meter_text = f"{round(probability * 100)}% {stage}"
    isl_text = " [+ISL Sweep]" if signal.get("isl_sweep") else ""
    return f"{rank}. {ticker}{isl_text} | Score: {score_text} | Meter: {meter_text}"


def target_time_for_ticker(item):
    candles = item.get("candles") or []
    if len(candles) < abs(ALERT_POSITION):
        return None
    return candles[ALERT_POSITION].get("time")


def target_time_for_payload(payload):
    times = [
        target_time_for_ticker(item)
        for item in payload.get("tickers", [])
        if target_time_for_ticker(item)
    ]
    return max(times) if times else None


def latest_signal_alerts(payload):
    alerts = []
    for item in payload.get("tickers", []):
        target_time = target_time_for_ticker(item)
        if not target_time:
            continue
        for signal in item.get("signals") or []:
            if signal.get("time") == target_time:
                probability, _ = signal_probability(signal)
                alerts.append(
                    {
                        "ticker": item.get("ticker", "UNKNOWN"),
                        "signal": signal,
                        "probability": probability,
                        "score": signal.get("score") or 0,
                        "time": target_time,
                    }
                )
    alerts.sort(key=lambda item: (item["probability"], item["score"], item["ticker"]), reverse=True)
    return alerts


def build_message(payload):
    alerts = latest_signal_alerts(payload)
    scan_time = format_ist_time(target_time_for_payload(payload))
    if alerts:
        lines = [format_signal_line(rank, item["ticker"], item["signal"]) for rank, item in enumerate(alerts, start=1)]
        return f"Breaker+FVG signals\nCandle: {scan_time}\n\n" + "\n".join(lines) + f"\n\nDashboard: {DASHBOARD_URL}"

    return f"Breaker+FVG Scanner ran - no signals.\nCandle: {scan_time}\n\nDashboard: {DASHBOARD_URL}"


def main():
    payload = load_dashboard_payload()
    try:
        trade_count = update_research_log(payload)
        print(f"Research log updated with {trade_count} trade ideas.")
    except Exception as exc:
        print(f"Research log update failed: {exc}")
    message = build_message(payload)
    telegram_alert(message)
    print(message)


if __name__ == "__main__":
    main()
