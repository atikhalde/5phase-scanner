"""
Exact same conditions which produced 1291 trades on full NSE EQ 2075
Final v8 with rally continuation filter (ABDL fix)

Phase1: Anchor = max High in [i-180:i-90] trading days, DaysSince 90-180, not broken by CLOSE last 90 days
Phase2: Dry90 >=8 (dry = VolRatio<0.5)
Phase3: Breakout Close > Anchor + VolRatio>1.5 + Close>EMA50
Phase4a: Rally continuation 1-7 days after breakout, rally_high > breakout_high*1.01 (proves breakout survived, filters failed breakouts like ABDL)
Phase4b: Shakeout low after rally, low VolRatio<1.0, drop 4-25% from shake_high (max high breakout to low)
Phase5: Reversal bullish close>prev high and >shake_low high + VolRatio>0.6 and increasing

Watchlist logic (FIXED 2026-08-20):
Previously the watchlist asked `reversal_date > today`, but reversal_date is only
ever set once a reversal has already been observed in historical data, so it could
never be in the future -> the watchlist was always empty even when breakouts were
happening.

Now, in addition to the completed-pattern trades returned by scan_5phase(), we run
detect_pending_breakouts() over the last 60 trading days to find breakouts that
have passed Phase 4a (rally continuation confirmation) but have NOT yet produced a
Phase 4b shakeout / Phase 5 reversal. Those are the genuine "waiting for reversal"
candidates and are used for the 30d/60d watchlist windows. breakout_today is also
fixed to fire on the actual breakout bar (Phase 3), before the 1-7 day rally
confirmation window has elapsed.

Watchlist logic (TIGHTENED 2026-08-20, second pass -- ABDL-type filter):
detect_pending_breakouts() previously returned EVERY 4a-confirmed breakout in the
lookback window indefinitely -- including stale breakouts whose shakeout/reversal
windows had already closed without progress (failed "ABDL-type" breakouts that
sat in the report forever). Each pending breakout now carries a lifecycle
`status` (see detect_pending_breakouts):

  awaiting_shakeout -- 4a confirmed, 15-bar shakeout window still open, no
    valid 4-25% low-volume pullback yet.
  awaiting_reversal -- valid shakeout observed (shake_low/drop_pct populated),
    reversal window still open, no bullish reversal bar yet.

Breakouts whose shakeout or reversal windows have CLOSED without progress are
now EXCLUDED from the report (expired/failed). The Telegram footer
"Total with setup: N tickers" was renamed to "Tickers with any 2Y setup" so
it's clear that count is historical (any setup in the 2Y data), not live.
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

# ---------------------------------------------------------------------------
# Shared Phase 1 + Phase 2 + Phase 3 + Phase 4a breakout-confirmation helper.
# Returns a dict with the breakout's metadata if the bar at `i` passes all
# four phases and the rally continuation is already confirmed by later bars,
# otherwise None.  This is a pure extraction of the same checks used inside
# scan_5phase (below) -- refactored so the pending-breakout detector can use
# them without changing the historical trade logic.
# ---------------------------------------------------------------------------
def _check_breakout_confirmed(df, i, n, dry_thresh=8, vol_break=1.5):
    """Phase 1 -> Phase 4a.  Returns info dict or None."""
    if pd.isna(df.loc[i, 'VolMA20']) or pd.isna(df.loc[i, 'EMA50']) or pd.isna(df.loc[i, 'Dry90']):
        return None
    if i < 180:
        return None
    anchor_window = df.iloc[i-180:i-90]
    anchor_high = anchor_window['High'].max()
    if pd.isna(anchor_high):
        return None
    anchor_idx = anchor_window[anchor_window['High'] == anchor_high].index[-1]
    days_since = i - anchor_idx
    if days_since < 90 or days_since > 180:
        return None
    last90 = df.iloc[i-90:i]
    if last90['Close'].max() >= anchor_high:
        return None
    if df.loc[i-1, 'Dry90'] < dry_thresh:
        return None
    close_b = df.loc[i, 'Close']
    vol_b = df.loc[i, 'VolRatio']
    if pd.isna(vol_b):
        return None
    # Phase 3: breakout bar
    if not (close_b > anchor_high and vol_b > vol_break and close_b > df.loc[i, 'EMA50']):
        return None
    # Phase 4a: rally continuation 1-7 days after breakout
    rally_end = min(i+8, n)
    rally_window = df.iloc[i+1:rally_end]
    if len(rally_window) == 0:
        return None
    rally_high = rally_window['High'].max()
    breakout_high = df.loc[i, 'High']
    if rally_high < breakout_high * 1.01:
        return None
    rally_idx = rally_window[rally_window['High'] == rally_high].index[-1]
    return {
        'i': i,
        'anchor_idx': int(anchor_idx),
        'anchor_date': df.loc[anchor_idx, 'Date'],
        'anchor_high': round(float(anchor_high), 2),
        'days_since': int(days_since),
        'breakout_date': df.loc[i, 'Date'],
        'breakout_high': round(float(breakout_high), 2),
        'breakout_close': round(float(close_b), 2),
        'vol_break': round(float(vol_b), 2),
        'rally_idx': int(rally_idx),
        'rally_high_date': df.loc[rally_idx, 'Date'],
        'rally_high': round(float(rally_high), 2),
        'dry90': int(df.loc[i-1, 'Dry90']),
        'dry30': int(df.loc[i-1, 'Dry30']) if not pd.isna(df.loc[i-1, 'Dry30']) else 0,
    }

def scan_5phase(df, dry_thresh=8, vol_break=1.5, vol_shake_max=1.0, vol_rev_min=0.6, drop_min=4, drop_max=25):
    if len(df) < 250:
        return []
    df = prepare_df(df)
    trades = []
    i = 200
    n = len(df)
    while i < n - 20:
        # Phase 1 -> Phase 4a (same checks, refactored).
        bo = _check_breakout_confirmed(df, i, n, dry_thresh=dry_thresh, vol_break=vol_break)
        if bo is None:
            i += 1
            continue
        rally_idx = bo['rally_idx']
        breakout_high = bo['breakout_high']
        close_b = bo['breakout_close']
        vol_b = bo['vol_break']

        # Phase 4b: shakeout after rally
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

        # Phase 5: reversal bar
        rev_window = df.iloc[low_idx+1:min(low_idx+16, n)]
        for j, rev in rev_window.iterrows():
            if pd.isna(rev['VolRatio']):
                continue
            if rev['Close'] <= rev['Open']:
                continue
            prev_high = df.iloc[j-1]['High'] if j > 0 else 0
            if rev['Close'] > prev_high and rev['Close'] > low_row['High'] and rev['VolRatio'] > vol_rev_min and rev['VolRatio'] > low_row['VolRatio'] * 0.8:
                trades.append({
                    'anchor_date': bo['anchor_date'],
                    'anchor_high': bo['anchor_high'],
                    'days_since': bo['days_since'],
                    'breakout_date': bo['breakout_date'],
                    'breakout_high': bo['breakout_high'],
                    'breakout_close': close_b,
                    'vol_break': vol_b,
                    'rally_high_date': bo['rally_high_date'],
                    'rally_high': bo['rally_high'],
                    'shake_low_date': low_row['Date'],
                    'shake_low': round(float(shake_low), 2),
                    'shake_low_vol': round(float(low_row['VolRatio']), 2),
                    'shake_high': round(float(shake_high), 2),
                    'drop_pct': round(float(drop), 2),
                    'reversal_date': rev['Date'],
                    'entry': round(float(rev['Close']), 2),
                    'entry_vol': round(float(rev['VolRatio']), 2),
                    'dry90': bo['dry90'],
                    'dry30': bo['dry30'],
                })
                i = j + 10
                break
        else:
            i += 1
    return trades


# ---------------------------------------------------------------------------
# Pending (in-progress) breakout detector with lifecycle statuses.
#
# Scans the last `lookback_days` calendar days of the (already-prepared)
# DataFrame and returns Phase 1->4a breakouts that are still ALIVE in their
# Phase 4b / Phase 5 windows -- these are genuine "waiting" candidates for
# the daily watchlist.  Each returned record carries a `status`:
#
#   awaiting_shakeout -- 4a confirmed, 15-bar shakeout window still open,
#     no valid 4-25% low-volume pullback observed yet (shake_* fields None).
#   awaiting_reversal -- valid shakeout OBSERVED (shake_low/drop_pct
#     populated, same computation scan_5phase would use), no bullish
#     reversal bar yet, and the 15-bar reversal window is still open.
#
# ABDL-type filter (TIGHTENED 2026-08-20): a breakout is EXCLUDED once its
# windows have expired -- i.e. the 15-bar shakeout window has fully elapsed
# without a valid shakeout, or the 15-bar reversal window has fully elapsed
# without a bullish reversal.  Previously every 4a-confirmed breakout was
# shown indefinitely, so failed breakouts lingered as stale "waiting"
# entries.  A breakout is also excluded if scan_5phase() already emitted a
# completed trade with the same breakout date, or if an observed reversal
# bar is already present in its reversal window (i.e. it fired).
#
# Window sizes mirror scan_5phase exactly: shakeout = bars rally_idx+1 ..
# rally_idx+15, reversal = bars low_idx+1 .. low_idx+15.  A window counts as
# "closed" only once all 15 of its bars have been observed, so recent
# breakouts are never cut off early.
# ---------------------------------------------------------------------------
def _find_shakeout(df, i, rally_idx, n, vol_shake_max=1.0, drop_min=4, drop_max=25):
    """Phase 4b on OBSERVED bars only (same computation as scan_5phase).

    Returns a dict with:
      window_closed -- all 15 shakeout bars (rally_idx+1..rally_idx+15) seen
      valid         -- a low-volume pullback with a 4-25% drop exists
      when a low-volume pullback exists at all: shake_low, low_row, low_idx,
      shake_high, drop
    """
    info = {'window_closed': n >= rally_idx + 16, 'valid': False}
    shake_start = rally_idx + 1
    shake_end = min(rally_idx + 16, n)
    shake_window = df.iloc[shake_start:shake_end]
    if len(shake_window) == 0:
        return info
    low_vol = shake_window[shake_window['VolRatio'] < vol_shake_max]
    if low_vol.empty:
        return info
    shake_low = low_vol['Low'].min()
    low_candidates = low_vol[low_vol['Low'] == shake_low]
    low_row = low_candidates.iloc[0]
    low_idx = low_row.name
    shake_high = df.iloc[i:low_idx+1]['High'].max()
    drop = (shake_high - shake_low) / shake_high * 100 if shake_high else 0
    info.update({
        'valid': drop_min <= drop <= drop_max,
        'shake_low': shake_low,
        'low_row': low_row,
        'low_idx': low_idx,
        'shake_high': shake_high,
        'drop': drop,
    })
    return info


def _find_reversal(df, low_row, low_idx, n, vol_rev_min=0.6):
    """Phase 5 on OBSERVED bars only (same conditions as scan_5phase).

    Returns (fired, window_closed): `fired` = a bullish reversal bar is
    already present in the observed window; `window_closed` = all 15
    reversal bars (low_idx+1..low_idx+15) have been observed.
    """
    rev_window = df.iloc[low_idx+1:min(low_idx+16, n)]
    fired = False
    for j, rev in rev_window.iterrows():
        if pd.isna(rev['VolRatio']):
            continue
        if rev['Close'] <= rev['Open']:
            continue
        prev_high = df.iloc[j-1]['High'] if j > 0 else 0
        if (rev['Close'] > prev_high and rev['Close'] > low_row['High']
                and rev['VolRatio'] > vol_rev_min
                and rev['VolRatio'] > low_row['VolRatio'] * 0.8):
            fired = True
            break
    return fired, n >= low_idx + 16


def detect_pending_breakouts(df, lookback_days=60, vol_shake_max=1.0,
                             vol_rev_min=0.6, drop_min=4, drop_max=25):
    if len(df) < 250:
        return []
    df = prepare_df(df)
    n = len(df)
    last_date = pd.to_datetime(df.iloc[-1]['Date'])
    cutoff = last_date - pd.Timedelta(days=lookback_days)

    # Completed trades from the full pattern -- breakouts with a matching
    # breakout_date have already fired a reversal and are NOT pending.
    completed = scan_5phase(df)
    completed_breakout_dates = set()
    for tr in completed:
        try:
            completed_breakout_dates.add(pd.Timestamp(tr['breakout_date']).normalize())
        except Exception:
            pass

    # Earliest breakout bar we can still see full rally confirmation for:
    # need up to 7 future bars after i, so don't start from the last 7.
    max_i = n - 8

    pending = []
    i = 180
    while i < max_i:
        bdate = pd.to_datetime(df.loc[i, 'Date'])
        if bdate < cutoff:
            i += 1
            continue
        if bdate.normalize() in completed_breakout_dates:
            i += 1
            continue
        bo = _check_breakout_confirmed(df, i, n)
        if bo is None:
            i += 1
            continue
        rally_idx = bo['rally_idx']

        # --- lifecycle classification (ABDL-type filter) ---
        shake = _find_shakeout(df, i, rally_idx, n,
                               vol_shake_max=vol_shake_max,
                               drop_min=drop_min, drop_max=drop_max)
        if shake['valid']:
            # Valid 4-25% low-volume shakeout already observed.  Still
            # pending only if no bullish reversal bar has fired AND the
            # 15-bar reversal window has not fully elapsed.
            fired, rev_closed = _find_reversal(df, shake['low_row'],
                                               shake['low_idx'], n,
                                               vol_rev_min=vol_rev_min)
            if fired or rev_closed:
                # Reversal already fired, or its window closed without one:
                # expired/failed -> excluded.
                i = rally_idx + 1
                continue
            status = 'awaiting_reversal'
            shake_fields = {
                'shake_low_date': shake['low_row']['Date'],
                'shake_low': round(float(shake['shake_low']), 2),
                'shake_low_vol': round(float(shake['low_row']['VolRatio']), 2),
                'shake_high': round(float(shake['shake_high']), 2),
                'drop_pct': round(float(shake['drop']), 2),
            }
        else:
            # No valid shakeout yet.  Still pending only while the 15-bar
            # shakeout window is open; once it has fully elapsed without a
            # valid shakeout the breakout is expired/failed -> excluded.
            if shake['window_closed']:
                i = rally_idx + 1
                continue
            status = 'awaiting_shakeout'
            shake_fields = {
                'shake_low_date': None,
                'shake_low': None,
                'shake_low_vol': None,
                'shake_high': None,
                'drop_pct': None,
            }

        pending.append({
            'anchor_date': bo['anchor_date'],
            'anchor_high': bo['anchor_high'],
            'days_since': bo['days_since'],
            'breakout_date': bo['breakout_date'],
            'breakout_high': bo['breakout_high'],
            'breakout_close': bo['breakout_close'],
            'vol_break': bo['vol_break'],
            'rally_high_date': bo['rally_high_date'],
            'rally_high': bo['rally_high'],
            # Phase 4b fields: populated only once a valid shakeout is
            # observed (awaiting_reversal), else explicitly None.
            **shake_fields,
            # Phase 5 never populated for pending records.
            'reversal_date': None,
            'entry': None,
            'entry_vol': None,
            'dry90': bo['dry90'],
            'dry30': bo['dry30'],
            'pending': True,
            'status': status,
        })
        # Skip forward past the rally window so we don't re-report the same
        # breakout from a nearby bar.
        i = rally_idx + 1
    return pending


def get_watchlist(trades, today, days=30, only_waiting=True):
    """
    Watchlist logic:
    - breakout in last `days` days
    - if only_waiting True: for COMPLETED trades, reversal_date > today
      (waiting for reversal, not yet fired). For PENDING breakouts
      (reversal_date is None), they are always treated as waiting.
    - else: includes past reversals too
    """
    watchlist = []
    for tr in trades:
        try:
            bdate = tr['breakout_date'].date() if hasattr(tr['breakout_date'], 'date') else pd.to_datetime(tr['breakout_date']).date()
            delta = (today - bdate).days
            if 0 < delta <= days:
                if only_waiting:
                    # Pending breakout (reversal not yet detected) -> waiting.
                    if tr.get('reversal_date') is None or (isinstance(tr.get('reversal_date'), float) and pd.isna(tr.get('reversal_date'))):
                        watchlist.append(tr)
                    else:
                        rdate = tr['reversal_date'].date() if hasattr(tr['reversal_date'], 'date') else pd.to_datetime(tr['reversal_date']).date()
                        if rdate > today:
                            watchlist.append(tr)
                else:
                    watchlist.append(tr)
        except Exception:
            continue
    return watchlist


def _is_phase3_breakout_bar(df, i, dry_thresh=8, vol_break=1.5):
    """Phase 1 + Phase 2 + Phase 3 on bar `i` -- a breakout TODAY, before
    the 1-7 day rally confirmation has elapsed.  Used for immediate
    breakout alerts.  Same checks as _check_breakout_confirmed minus 4a."""
    if i < 180:
        return None
    if pd.isna(df.loc[i, 'VolMA20']) or pd.isna(df.loc[i, 'EMA50']) or pd.isna(df.loc[i, 'Dry90']):
        return None
    anchor_window = df.iloc[i-180:i-90]
    anchor_high = anchor_window['High'].max()
    if pd.isna(anchor_high):
        return None
    anchor_idx = anchor_window[anchor_window['High'] == anchor_high].index[-1]
    days_since = i - anchor_idx
    if days_since < 90 or days_since > 180:
        return None
    last90 = df.iloc[i-90:i]
    if last90['Close'].max() >= anchor_high:
        return None
    if df.loc[i-1, 'Dry90'] < dry_thresh:
        return None
    close_b = df.loc[i, 'Close']
    vol_b = df.loc[i, 'VolRatio']
    if pd.isna(vol_b):
        return None
    if not (close_b > anchor_high and vol_b > vol_break and close_b > df.loc[i, 'EMA50']):
        return None
    return {
        'anchor_idx': int(anchor_idx),
        'anchor_date': df.loc[anchor_idx, 'Date'],
        'anchor_high': round(float(anchor_high), 2),
        'days_since': int(days_since),
        'breakout_date': df.loc[i, 'Date'],
        'breakout_high': round(float(df.loc[i, 'High']), 2),
        'breakout_close': round(float(close_b), 2),
        'vol_break': round(float(vol_b), 2),
        'rally_high_date': None,
        'rally_high': None,
        'shake_low_date': None,
        'shake_low': None,
        'shake_low_vol': None,
        'shake_high': None,
        'drop_pct': None,
        'reversal_date': None,
        'entry': None,
        'entry_vol': None,
        'dry90': int(df.loc[i-1, 'Dry90']),
        'dry30': int(df.loc[i-1, 'Dry30']) if not pd.isna(df.loc[i-1, 'Dry30']) else 0,
        'pending': True,
        'awaiting_rally_confirm': True,
    }


def check_today_events(df):
    if len(df) < 250:
        return {'breakout_today': None, 'watchlist': [], 'reversal_today': None,
                'all_trades': [], 'watchlist_30': [], 'watchlist_60': [],
                'pending_breakouts': []}
    df_prep = prepare_df(df)
    trades = scan_5phase(df_prep)
    today = df_prep.iloc[-1]['Date'].date()

    # Pending (waiting-for-reversal) breakouts from the last 60 days --
    # this is what the daily watchlist is supposed to show.  Tightened:
    # only awaiting_shakeout / awaiting_reversal records whose 15-bar
    # windows are still open; expired/failed (ABDL-type) breakouts are
    # already excluded inside detect_pending_breakouts().
    pending = detect_pending_breakouts(df_prep, lookback_days=60)

    # breakout_today:
    #  1) today's bar passes Phase 1+2+3 (breakout is TODAY, rally confirm
    #     can't have happened yet) -> immediate alert, OR
    #  2) a pending breakout whose Phase 3 was today (already 4a-confirmed
    #     would require future bars, so normally path 1 is the one).
    breakout_today = _is_phase3_breakout_bar(df_prep, len(df_prep) - 1)

    reversal_today = None
    for tr in trades:
        if tr['reversal_date'].date() == today:
            reversal_today = tr
            break

    # Watchlist 30/60 days waiting: combine completed (reversal future --
    # normally empty on live data by construction) with pending breakouts.
    waiting_pool = list(trades) + list(pending)
    watchlist_30 = get_watchlist(waiting_pool, today, days=30, only_waiting=True)
    watchlist_60 = []
    if not watchlist_30:
        watchlist_60 = get_watchlist(waiting_pool, today, days=60, only_waiting=True)
    watchlist = watchlist_30 if watchlist_30 else watchlist_60

    return {
        'breakout_today': breakout_today,
        'reversal_today': reversal_today,
        'watchlist': watchlist,
        'watchlist_30': watchlist_30,
        'watchlist_60': watchlist_60,
        'all_trades': trades,
        'pending_breakouts': pending,
    }
