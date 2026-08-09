"""
Exact same conditions which produced 1291 trades on full NSE EQ 2075
Final v8 with rally continuation filter (ABDL fix)

Phase1: Anchor = max High in [i-180:i-90] trading days, DaysSince 90-180, not broken by CLOSE last 90 days
Phase2: Dry90 >=8 (dry = VolRatio<0.5)
Phase3: Breakout Close > Anchor + VolRatio>1.5 + Close>EMA50
Phase4a: Rally continuation 1-7 days after breakout, rally_high > breakout_high*1.01 (proves breakout survived, filters failed breakouts like ABDL)
Phase4b: Shakeout low after rally, low VolRatio<1.0, drop 4-25% from shake_high (max high breakout to low)
Phase5: Reversal bullish close>prev high and >shake_low high + VolRatio>0.6 and increasing

This version produced 1291 trades on 2075 EQ, ABDL 0, CNL 23/07 low 867 Vol 0.07 reversal 24/07 Vol 9.21 correct,
AEGISVOPAK 17/07 hammer/doji -> entry 20/07 correct per user.

Updated: Watchlist window changed from 7 to 30 days as per user request
"""

import pandas as pd
import numpy as np

def prepare_df(hist_df):
    """hist_df must have Date, Open, High, Low, Close, Volume"""
    df = hist_df.copy().sort_values('Date').reset_index(drop=True)
    df['VolMA20'] = df['Volume'].rolling(20).mean()
    df['VolRatio'] = df['Volume'] / df['VolMA20']
    df['Dry'] = (df['VolRatio'] < 0.5).astype(int)
    df['Dry90'] = df['Dry'].rolling(90).sum()
    df['Dry30'] = df['Dry'].rolling(30).sum()
    df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
    return df

def scan_5phase(df, dry_thresh=8, vol_break=1.5, vol_shake_max=1.0, vol_rev_min=0.6, drop_min=4, drop_max=25):
    """
    Returns list of trades with full details
    """
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
        # Phase1 Anchor
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
        # not broken by CLOSE last 90 days
        last90 = df.iloc[i-90:i]
        if last90['Close'].max() >= anchor_high:
            i += 1
            continue
        # Phase2 Dry
        if df.loc[i-1, 'Dry90'] < dry_thresh:
            i += 1
            continue
        # Phase3 Breakout
        close_b = df.loc[i, 'Close']
        vol_b = df.loc[i, 'VolRatio']
        if pd.isna(vol_b):
            i += 1
            continue
        if not (close_b > anchor_high and vol_b > vol_break and close_b > df.loc[i, 'EMA50']):
            i += 1
            continue
        # Phase4a Rally continuation 1-7 days after breakout, must be >1% higher to prove survival (ABDL fix)
        rally_end = min(i+8, n)
        rally_window = df.iloc[i+1:rally_end]
        if len(rally_window) == 0:
            i += 1
            continue
        rally_high = rally_window['High'].max()
        breakout_high = df.loc[i, 'High']
        if rally_high < breakout_high * 1.01:
            i += 1
            continue  # failed breakout like ABDL
        rally_idx = rally_window[rally_window['High'] == rally_high].index[-1]
        # Phase4b Shakeout low after rally, low vol
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
        # Phase5 Reversal
        rev_window = df.iloc[low_idx+1:min(low_idx+16, n)]
        for j, rev in rev_window.iterrows():
            if pd.isna(rev['VolRatio']):
                continue
            if rev['Close'] <= rev['Open']:
                continue
            prev_high = df.iloc[j-1]['High'] if j > 0 else 0
            # close > prev high and > shake low high and vol increasing and >0.6
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

def check_today_events(df):
    """
    For daily scanner live alerts:
    Returns dict with breakout_today, watchlist (breakout in last 30 days - changed from 7 as per user request), reversal_today
    Uses same conditions but checks today only
    """
    if len(df) < 250:
        return {'breakout_today': None, 'watchlist': [], 'reversal_today': None, 'all_trades': []}
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
        # Watchlist: breakout in last 30 days (changed from 7 as per user request)
        watchlist = []
        for tr in trades:
            delta = (today - tr['breakout_date'].date()).days
            if 0 < delta <= 30:  # Changed from 7 to 30
                if tr['rally_high_date'].date() <= today <= tr['reversal_date'].date() or tr['breakout_date'].date() <= today < tr['reversal_date'].date():
                    watchlist.append(tr)
        recent_breakouts = [tr for tr in trades if (today - tr['breakout_date'].date()).days <= 30 and (today - tr['breakout_date'].date()).days >=0]
        watchlist_dict = { (w['breakout_date'], w['anchor_high']): w for w in watchlist + recent_breakouts }
        watchlist = list(watchlist_dict.values())
        return {
            'breakout_today': breakout_today,
            'reversal_today': reversal_today,
            'watchlist': watchlist,
            'all_trades': trades
        }
    return {'breakout_today': None, 'watchlist': [], 'reversal_today': None, 'all_trades': []}
