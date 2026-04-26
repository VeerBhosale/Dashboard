# Breaker+FVG Scanner - standalone script for Task Scheduler
# Run with: EMAFyers.venv/Scripts/python.exe breaker_fvg_scan.py
# Working directory: Newtest/

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os

# ── Config ────────────────────────────────────────────────────────────────────
Ticker_df    = pd.read_csv('NSE_Symbols.csv')
tickers_list = Ticker_df['Symbol'].tolist()

def telegram_alert(message):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("Telegram alert skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=10)

position     = -2
ATR_BASELINE = 14   # candles before T2 used as volatility baseline (configurable)
ISL_LOOKBACK = 30   # extra calendar days fetched purely for ISL/ISH history (no effect on position logic)
period       = round(((1/7) * -position) + 10)
fetch_period = period + ISL_LOOKBACK

def mean_atr(df):
    prev_c = df['Close'].shift(1).fillna(df['Close'].iloc[0])
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev_c).abs(),
        (df['Low']  - prev_c).abs()
    ], axis=1).max(axis=1)
    return tr.mean()

# ── Bulk download ─────────────────────────────────────────────────────────────
raw_data    = yf.download(tickers_list, period=f'{fetch_period}d', interval='1h',
                          group_by='ticker', auto_adjust=True, progress=False)
multi_index = isinstance(raw_data.columns, pd.MultiIndex)

Alert_List    = []
alert_time    = None
scan_count    = 0
breaker_hits  = 0
position_hits = 0

for Ticker in tickers_list:
    try:
        BTC = (raw_data[Ticker] if multi_index else raw_data).copy()
    except KeyError:
        continue

    BTC.dropna(how='all', inplace=True)
    if BTC.empty or len(BTC) < abs(position):
        continue

    scan_count += 1

    BTC['Swing_High'] = np.where(
        (BTC['High'] > BTC['High'].shift(-1)) & (BTC['High'] > BTC['High'].shift(1)),
        'Swing High', 'NA')
    BTC['Swing_Low'] = np.where(
        (BTC['Low'] < BTC['Low'].shift(-1)) & (BTC['Low'] < BTC['Low'].shift(1)),
        'Swing Low', 'NA')

    BTC['Swing_High_Value'] = np.where(BTC['Swing_High'] == 'Swing High', BTC['High'], np.nan)
    BTC['Swing_Low_Value']  = np.where(BTC['Swing_Low']  == 'Swing Low',  BTC['Low'],  np.nan)

    BTC['Last_Swing_High'] = BTC['Swing_High_Value'].ffill()
    BTC['Last_Swing_Low']  = BTC['Swing_Low_Value'].ffill()
    BTC['Prev_Swing_High'] = BTC['Last_Swing_High'].shift(1)
    BTC['Prev_Swing_Low']  = BTC['Last_Swing_Low'].shift(1)

    BTC['Master_Swing'] = np.select(
        [(BTC['Swing_High'] == 'Swing High') & (BTC['Last_Swing_High'] > BTC['Prev_Swing_High']),
         (BTC['Swing_High'] == 'Swing High') & (BTC['Last_Swing_High'] < BTC['Prev_Swing_High']),
         (BTC['Swing_Low']  == 'Swing Low')  & (BTC['Last_Swing_Low']  > BTC['Prev_Swing_Low']),
         (BTC['Swing_Low']  == 'Swing Low')  & (BTC['Last_Swing_Low']  < BTC['Prev_Swing_Low'])],
        ['Higher High', 'Lower High', 'Higher Low', 'Lower Low'], default='')

    BTC['Main_Signal'] = np.select(
        [(BTC['High'] > BTC['High'].shift(-1)) & (BTC['High'] > BTC['High'].shift(1)),
         (BTC['Low']  < BTC['Low'].shift(-1))  & (BTC['Low']  < BTC['Low'].shift(1))],
        ['Swing High', 'Swing Low'], default='')

    Subset_df = BTC[BTC['Main_Signal'] != ''].copy()
    if len(Subset_df) < 4:
        continue

    _sl_s       = Subset_df.loc[Subset_df['Main_Signal'] == 'Swing Low', 'Low']
    _isl_mask   = (_sl_s < _sl_s.shift(1)) & (_sl_s < _sl_s.shift(-1))
    _isl_prices = _sl_s.where(_isl_mask)

    _sh_s       = Subset_df.loc[Subset_df['Main_Signal'] == 'Swing High', 'High']
    _ish_mask   = (_sh_s > _sh_s.shift(1)) & (_sh_s > _sh_s.shift(-1))
    _ish_prices = _sh_s.where(_ish_mask)

    BTC['ISL_Price'] = np.nan
    BTC.loc[_isl_prices.index, 'ISL_Price'] = _isl_prices.values
    BTC['ISL_Price_Ffill'] = BTC['ISL_Price'].ffill()

    BTC['ISH_Price'] = np.nan
    BTC.loc[_ish_prices.index, 'ISH_Price'] = _ish_prices.values
    BTC['ISH_Price_Ffill'] = BTC['ISH_Price'].ffill()

    Subset_df['Breaker_Signal'] = (
        (Subset_df['Main_Signal'] == 'Swing High') &
        (Subset_df['Main_Signal'].shift(1) == 'Swing Low') &
        (Subset_df['Main_Signal'].shift(2) == 'Swing High') &
        (Subset_df['Main_Signal'].shift(3) == 'Swing Low') &
        (Subset_df['High'] > Subset_df['High'].shift(2)) &
        (Subset_df['Low'].shift(1) < Subset_df['Low'].shift(3))
    )
    BTC['Breaker_Signal'] = False
    BTC.loc[Subset_df.index, 'Breaker_Signal'] = Subset_df['Breaker_Signal']

    BTC['FVG_Signal'] = np.select(
        [(BTC['Low'] > BTC['High'].shift(2)),
         (BTC['High'] < BTC['Low'].shift(2))],
        ['Bullish_FVG', 'Bearish_FVG'], default='NA')

    BTC['T0_Bull_Low']     = np.where(BTC['FVG_Signal'] == 'Bullish_FVG', BTC['Low'],           np.nan)
    BTC['T1_Bull_High']    = np.where(BTC['FVG_Signal'] == 'Bullish_FVG', BTC['High'].shift(2), np.nan)
    BTC['T0_Bearish_High'] = np.where(BTC['FVG_Signal'] == 'Bearish_FVG', BTC['High'],          np.nan)
    BTC['T1_Bearish_Low']  = np.where(BTC['FVG_Signal'] == 'Bearish_FVG', BTC['Low'].shift(2),  np.nan)
    BTC['Mid_Bear_Range']  = np.where(BTC['FVG_Signal'] == 'Bearish_FVG',
                                      BTC['High'].shift(1) - BTC['Low'].shift(1), np.nan)

    Sub_FVG_df = BTC[BTC['FVG_Signal'] != 'NA'].copy()
    Sub_FVG_df['Bear_FVG_ts'] = Sub_FVG_df.index.to_series().shift(1)

    Sub_FVG_df['FVG_Overlap'] = np.select(
        [(Sub_FVG_df['FVG_Signal'] == 'Bullish_FVG') &
         (Sub_FVG_df['FVG_Signal'].shift(1) == 'Bearish_FVG') &
         (Sub_FVG_df['T0_Bull_Low'] > Sub_FVG_df['T0_Bearish_High'].shift(1)) &
         (Sub_FVG_df['T1_Bearish_Low'].shift(1) > Sub_FVG_df['T1_Bull_High'])],
        ['FVG_Overlap'], default='NA')

    fvg_overlap_df = Sub_FVG_df.loc[Sub_FVG_df['FVG_Overlap'] == 'FVG_Overlap', ['Bear_FVG_ts']]

    BTC['Breaker+FVG Signal'] = False

    for idx in Subset_df.index[Subset_df['Breaker_Signal']]:
        pos = Subset_df.index.get_loc(idx)
        if pos < 3:
            continue
        t3_ts = Subset_df.index[pos - 3]
        t2_ts = Subset_df.index[pos - 2]

        cands = fvg_overlap_df[
            (fvg_overlap_df.index          >= t2_ts) &
            (fvg_overlap_df.index          <= idx)   &
            (fvg_overlap_df['Bear_FVG_ts'] >= t2_ts)
        ]
        if len(cands) == 0:
            continue

        BTC.loc[idx, 'Breaker+FVG Signal'] = True
        breaker_hits += 1

        if idx == BTC.index[position]:
            position_hits += 1
            t1_ts   = Subset_df.index[pos - 1]
            t2_high = Subset_df.iloc[pos - 2]['High']
            t3_low  = Subset_df.iloc[pos - 3]['Low']
            t1_low  = Subset_df.iloc[pos - 1]['Low']

            rise  = t2_high - t3_low
            drop  = t2_high - t1_low
            ratio = round(drop / rise, 3) if rise > 0 else 0.0

            t2_iloc = BTC.index.get_loc(t2_ts)
            t1_iloc = BTC.index.get_loc(t1_ts)

            b_start      = max(0, t2_iloc - ATR_BASELINE + 1)
            atr_baseline = mean_atr(BTC.iloc[b_start : t2_iloc + 1])

            ctx     = max(0, t2_iloc - 1)
            ds_full = BTC.iloc[ctx : t1_iloc + 1]
            prev_c  = ds_full['Close'].shift(1).fillna(ds_full['Close'].iloc[0])
            tr_all  = pd.concat([
                          ds_full['High'] - ds_full['Low'],
                          (ds_full['High'] - prev_c).abs(),
                          (ds_full['Low']  - prev_c).abs()
                      ], axis=1).max(axis=1)
            tr_ds         = tr_all.iloc[1:] if ctx < t2_iloc else tr_all
            atr_downswing = tr_ds.mean() if len(tr_ds) > 0 else atr_baseline
            atr_ratio     = round(atr_downswing / atr_baseline, 3) if atr_baseline > 0 else 0.0

            bear_in_window = Sub_FVG_df[
                (Sub_FVG_df.index >= t2_ts) &
                (Sub_FVG_df.index <= idx) &
                (Sub_FVG_df['FVG_Signal'] == 'Bearish_FVG')
            ]
            fvg_size = bear_in_window.apply(
                lambda r: max(r['T1_Bearish_Low'] - r['T0_Bearish_High'], 0.0), axis=1
            ).sum()

            fvg_atr  = round(fvg_size / atr_baseline, 3) if atr_baseline > 0 else 0.0
            drop_fvg = round(drop / fvg_size, 3)         if fvg_size > 0     else 0.0
            gap_eff  = round(bear_in_window.apply(
                lambda r: (max(r['T1_Bearish_Low'] - r['T0_Bearish_High'], 0.0) / r['Mid_Bear_Range'])
                          if r['Mid_Bear_Range'] > 0 else 0.0, axis=1
            ).mean(), 3) if len(bear_in_window) > 0 else 0.0

            score = round(ratio * atr_ratio * fvg_atr, 4)

            _isl_hist  = BTC['ISL_Price_Ffill'].loc[:t3_ts].dropna()
            _isl_level = float(_isl_hist.iloc[-1]) if len(_isl_hist) > 0 else float('nan')
            isl_sweep  = (
                not (isinstance(_isl_level, float) and _isl_level != _isl_level) and
                t3_low > _isl_level and
                t1_low < _isl_level
            )

            if alert_time is None:
                alert_time = BTC.index[position]
            Alert_List.append((Ticker, ratio, atr_ratio, fvg_atr, gap_eff, drop_fvg, score, isl_sweep, _isl_level))

    if BTC['Breaker+FVG Signal'].iloc[position] and not any(t == Ticker for t, *_ in Alert_List):
        if alert_time is None:
            alert_time = BTC.index[position]
        Alert_List.append((Ticker, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False, float('nan')))

print(f"Scan complete — tickers processed: {scan_count} | Breaker+FVG hits (any candle): {breaker_hits} | hits at position {position}: {position_hits}")

Alert_List.sort(key=lambda x: x[6], reverse=True)

if Alert_List:
    ranked_lines = "\n".join(
        f"{rank}. {ticker}{f' [+ISL Sweep @ {isl_lvl:.2f}]' if isl_sw else ''}  "
        f"(ratio: {ratio:.3f} | atr_r: {atr_r:.3f} | fvg/atr: {fa:.3f} | gap_eff: {ge:.3f} | drop/fvg: {df:.3f} | score: {score:.4f})"
        for rank, (ticker, ratio, atr_r, fa, ge, df, score, isl_sw, isl_lvl) in enumerate(Alert_List, start=1)
    )
    alert_time_ist = alert_time.tz_convert('Asia/Kolkata') if alert_time is not None else alert_time
    message = (
        "Breaker+FVG signals\n\n"
        f"Candle: {position}\n"
        f"Time: {alert_time_ist}\n\n"
        + ranked_lines
    )
    telegram_alert(message)
    print(message)
else:
    no_signal_msg = (
        f"Breaker+FVG Scanner ran — no signals at candle {position}.\n"
        f"({breaker_hits} patterns exist elsewhere in the data window)"
    )
    telegram_alert(no_signal_msg)
    print(f"No Breaker+FVG signals at candle {position}.")
    print(f"  → {breaker_hits} Breaker+FVG patterns exist elsewhere in the data window.")
    print(f"     Try position closer to 0 (e.g. -7, -14, -21) or check if the scan date has active setups.")
