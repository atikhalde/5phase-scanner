"""
Exact same conditions which produced 1291 trades on full NSE EQ 2075
Final v8 with rally continuation filter (ABDL fix)

Phase1: Anchor = max High in [i-180:i-90] trading days, DaysSince 90-180, not broken by CLOSE last 90 days
Phase2: Dry90 >=8 (dry = VolRatio<0.5)
Phase3: Breakout Close > Anchor + VolRatio>1.5 + Close>EMA50
Phase4a: Rally continuation 1-7 days after breakout, rally_high > breakout_high*1.01 (proves breakout survived, filters failed breakouts like ABDL)
Phase4b: Shakeout low after rally, low VolRatio<1.0, drop 4-25% from shake_high (max high breakout to low)
Phase5: Reversal bullish close>prev high and >shake_low high + VolRatio>0.6 and increasing

Watchlist logic: breakout in last 30 days (or 60 if none), reversal not yet fired (reversal_date > today) = waiting reversal
"""

import pandas as pd
import numpy as np

def prepare_df(hist_df):
    df = hist_df.copy().sort_values('Date').reset_index(drop=True)
    df['VolMA20'] = df['Volume'].rolling(20).mean()
    df['VolRatio'] = df['Volume'] / df['VolMA20']
    df['Dry'] = (df['VolRatio'] < 0.5).astype(int)
    df['Dry90'] = df['Dry'].rolling(90).sum()
    df['Dry30'] = df['Dry'].rolling(30).sum()
    df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
    return df

def scan_5phase(df, dry_thresh=8, vol_break=1.5, vol_shake_max=1.0, vol_rev_min=0.6, drop_min=4, drop_max=25):
    if len(df) < 250:
        return []
    df = prepare_df(df)
    trades = []
    i = 200
    n = len(df)
    while i < n - 20:
        if pd.isna(df.loc[i, 'VolMA20']) or pd.isna(df.loc[i, 'EMA50']) or pd.isna(df.loc[i, 'Dry90']):
            i += 1
            continue
        if i < 180:
            i += 1
            continue
        anchor_window = df.iloc[i-180:i-90]
        anchor_high = anchor_window['High'].max()
        if pd.isna(anchor_high):
            i += 1
            continue
        anchor_idx = anchor_window[anchor_window['High'] == anchor_high].index[-1]
        days_since = i - anchor_idx
        if days_since < 90 or days_since > 180:
            i += 1
            continue
        last90 = df.iloc[i-90:i]
        if last90['Close'].max() >= anchor_high:
            i += 1
            continue
        if df.loc[i-1, 'Dry90'] < dry_thresh:
            i += 1
            continue
        close_b = df.loc[i, 'Close']
        vol_b = df.loc[i, 'VolRatio']
        if pd.isna(vol_b):
            i += 1
            continue
        if not (close_b > anchor_high and vol_b > vol_break and close_b > df.loc[i, 'EMA50']):
            i += 1
            continue
        rally_end = min(i+8, n)
        rally_window = df.iloc[i+1:rally_end]
        if len(rally_window) == 0:
            i += 1
            continue
        rally_high = rally_window['High'].max()
        breakout_high = df.loc[i, 'High']
        if rally_high < breakout_high * 1.01:
            i += 1
            continue
        rally_idx = rally_window[rally_window['High'] == rally_high].index[-1]
        shake_start = rally_idx + 1
        shake_end = min(rally_idx + 16, n)
        shake_window = df.iloc[shake_start:shake_end]
        if len(shake_window) == 0:
            i += 1
            continue
        low_vol = shake_window[shake_window['VolRatio'] < vol_shake_max]
        if low_vol.empty:
            i += 1
            continue
        shake_low = low_vol['Low'].min()
        low_candidates = low_vol[low_vol['Low'] == shake_low]
        low_row = low_candidates.iloc[0]
        low_idx = low_row.name
        shake_high = df.iloc[i:low_idx+1]['High'].max()
        drop = (shake_high - shake_low) / shake_high * 100 if shake_high else 0
        if drop < drop_min or drop > drop_max:
            i += 1
            continue
        rev_window = df.iloc[low_idx+1:min(low_idx+16, n)]
        for j, rev in rev_window.iterrows():
            if pd.isna(rev['VolRatio']):
                continue
            if rev['Close'] <= rev['Open']:
                continue
            prev_high = df.iloc[j-1]['High'] if j > 0 else 0
            if rev['Close'] > prev_high and rev['Close'] > low_row['High'] and rev['VolRatio'] > vol_rev_min and rev['VolRatio'] > low_row['VolRatio'] * 0.8:
                trades.append({
                    'anchor_date': df.loc[anchor_idx, 'Date'],
                    'anchor_high': round(anchor_high, 2),
                    'days_since': int(days_since),
                    'breakout_date': df.loc[i, 'Date'],
                    'breakout_high': round(breakout_high, 2),
                    'breakout_close': round(close_b, 2),
                    'vol_break': round(vol_b, 2),
                    'rally_high_date': df.loc[rally_idx, 'Date'],
                    'rally_high': round(rally_high, 2),
                    'shake_low_date': low_row['Date'],
                    'shake_low': round(shake_low, 2),
                    'shake_low_vol': round(low_row['VolRatio'], 2),
                    'shake_high': round(shake_high, 2),
                    'drop_pct': round(drop, 2),
                    'reversal_date': rev['Date'],
                    'entry': round(rev['Close'], 2),
                    'entry_vol': round(rev['VolRatio'], 2),
                    'dry90': int(df.loc[i-1, 'Dry90']),
                    'dry30': int(df.loc[i-1, 'Dry30']) if not pd.isna(df.loc[i-1, 'Dry30']) else 0,
                })
                i = j + 10
                break
        else:
            i += 1
    return trades

def get_watchlist(trades, today, days=30, only_waiting=True):
    """
    Watchlist logic:
    - breakout in last `days` days
    - if only_waiting True: reversal_date > today (waiting for reversal, not yet fired)
    - else: includes past reversals too
    """
    watchlist = []
    for tr in trades:
        try:
            bdate = tr['breakout_date'].date() if hasattr(tr['breakout_date'], 'date') else pd.to_datetime(tr['breakout_date']).date()
            rdate = tr['reversal_date'].date() if hasattr(tr['reversal_date'], 'date') else pd.to_datetime(tr['reversal_date']).date()
            delta = (today - bdate).days
            if 0 < delta <= days:
                if only_waiting:
                    # Only if reversal not yet fired (future)
                    if rdate > today:
                        watchlist.append(tr)
                else:
                    watchlist.append(tr)
        except:
            continue
    return watchlist

def check_today_events(df):
    if len(df) < 250:
        return {'breakout_today': None, 'watchlist': [], 'reversal_today': None, 'all_trades': [], 'watchlist_30': [], 'watchlist_60': []}
    df_prep = prepare_df(df)
    trades = scan_5phase(df_prep)
    if not df_prep.empty:
        today = df_prep.iloc[-1]['Date'].date()
        breakout_today = None
        for tr in trades:
            if tr['breakout_date'].date() == today:
                breakout_today = tr
                break
        reversal_today = None
        for tr in trades:
            if tr['reversal_date'].date() == today:
                reversal_today = tr
                break
        # Watchlist 30 days waiting
        watchlist_30 = get_watchlist(trades, today, days=30, only_waiting=True)
        # If no breakout in 30 days, expand to 60 days
        watchlist_60 = []
        if not watchlist_30:
            watchlist_60 = get_watchlist(trades, today, days=60, only_waiting=True)
        # Combined for backward compat
        watchlist = watchlist_30 if watchlist_30 else watchlist_60
        return {
            'breakout_today': breakout_today,
            'reversal_today': reversal_today,
            'watchlist': watchlist,
            'watchlist_30': watchlist_30,
            'watchlist_60': watchlist_60,
            'all_trades': trades
        }
    return {'breakout_today': None, 'watchlist': [], 'reversal_today': None, 'all_trades': [], 'watchlist_30': [], 'watchlist_60': []}
