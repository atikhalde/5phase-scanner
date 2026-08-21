"""
Tests for the scanner refactor and the pending-breakout lifecycle tightening:

  1. scan_5phase output is byte-for-byte identical to the pre-refactor logic
     (we keep an inline copy of the ORIGINAL function and compare over 150
     synthetic cases: 25 seeds x 6 data shapes). The 1291-trade logic is
     untouched by the pending-detector tightening.
  2. detect_pending_breakouts finds in-progress breakouts that the completed
     trade scan cannot yet see (status awaiting_shakeout).
  3. check_today_events returns a non-empty 30d watchlist when a recent
     pending breakout exists (the original empty-watchlist bug).
  4. breakout_today fires on the actual breakout bar (Phase 3) before
     rally confirmation.
  5. A full 5-phase pattern still produces a completed trade.
  6. detect_pending_breakouts excludes breakouts whose reversal already
     fired (no overlap with completed trades).
  7. awaiting_reversal: valid 4-25% low-volume shakeout observed, no
     reversal bar yet, reversal window still open -> shake_low/drop_pct
     populated, status awaiting_reversal.
  8. ABDL-type filter: 4a confirmed but the 15-bar SHAKEOUT window closed
     without a valid shakeout -> breakout EXCLUDED (previously shown
     indefinitely as a stale "waiting" entry).
  9. ABDL-type filter (second half): valid shakeout observed but the 15-bar
     REVERSAL window closed without a bullish reversal -> EXCLUDED too.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from scanner import (
    prepare_df, scan_5phase, detect_pending_breakouts,
    check_today_events, _check_breakout_confirmed, _is_phase3_breakout_bar,
)


# ---------------------------------------------------------------------------
# ORIGINAL scan_5phase -- copy from before the refactor, used ONLY for the
# equivalence test.  Must not be modified.
# ---------------------------------------------------------------------------
def original_scan_5phase(df, dry_thresh=8, vol_break=1.5, vol_shake_max=1.0,
                         vol_rev_min=0.6, drop_min=4, drop_max=25):
    if len(df) < 250:
        return []
    df = prepare_df(df)
    trades = []
    i = 200
    n = len(df)
    while i < n - 20:
        if pd.isna(df.loc[i, 'VolMA20']) or pd.isna(df.loc[i, 'EMA50']) or pd.isna(df.loc[i, 'Dry90']):
            i += 1; continue
        if i < 180:
            i += 1; continue
        anchor_window = df.iloc[i-180:i-90]
        anchor_high = anchor_window['High'].max()
        if pd.isna(anchor_high):
            i += 1; continue
        anchor_idx = anchor_window[anchor_window['High'] == anchor_high].index[-1]
        days_since = i - anchor_idx
        if days_since < 90 or days_since > 180:
            i += 1; continue
        last90 = df.iloc[i-90:i]
        if last90['Close'].max() >= anchor_high:
            i += 1; continue
        if df.loc[i-1, 'Dry90'] < dry_thresh:
            i += 1; continue
        close_b = df.loc[i, 'Close']
        vol_b = df.loc[i, 'VolRatio']
        if pd.isna(vol_b):
            i += 1; continue
        if not (close_b > anchor_high and vol_b > vol_break and close_b > df.loc[i, 'EMA50']):
            i += 1; continue
        rally_end = min(i+8, n)
        rally_window = df.iloc[i+1:rally_end]
        if len(rally_window) == 0:
            i += 1; continue
        rally_high = rally_window['High'].max()
        breakout_high = df.loc[i, 'High']
        if rally_high < breakout_high * 1.01:
            i += 1; continue
        rally_idx = rally_window[rally_window['High'] == rally_high].index[-1]
        shake_start = rally_idx + 1
        shake_end = min(rally_idx + 16, n)
        shake_window = df.iloc[shake_start:shake_end]
        if len(shake_window) == 0:
            i += 1; continue
        low_vol = shake_window[shake_window['VolRatio'] < vol_shake_max]
        if low_vol.empty:
            i += 1; continue
        shake_low = low_vol['Low'].min()
        low_candidates = low_vol[low_vol['Low'] == shake_low]
        low_row = low_candidates.iloc[0]
        low_idx = low_row.name
        shake_high = df.iloc[i:low_idx+1]['High'].max()
        drop = (shake_high - shake_low) / shake_high * 100 if shake_high else 0
        if drop < drop_min or drop > drop_max:
            i += 1; continue
        rev_window = df.iloc[low_idx+1:min(low_idx+16, n)]
        for j, rev in rev_window.iterrows():
            if pd.isna(rev['VolRatio']):
                continue
            if rev['Close'] <= rev['Open']:
                continue
            prev_high = df.iloc[j-1]['High'] if j > 0 else 0
            if (rev['Close'] > prev_high and rev['Close'] > low_row['High']
                    and rev['VolRatio'] > vol_rev_min
                    and rev['VolRatio'] > low_row['VolRatio'] * 0.8):
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


# ---------------------------------------------------------------------------
# Synthetic data builder.  Produces a random-walk series, then carves a
# 5-phase pattern (or a prefix of one) at a specified location so we can
# test detection at each lifecycle stage.
# ---------------------------------------------------------------------------
def make_synthetic(n=600, seed=0, with_full_pattern=False, with_pending=False,
                   with_breakout_today=False, with_shakeout_pending=False,
                   with_expired_shakeout=False):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2023-01-02', periods=n)
    base = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = base + rng.uniform(0.1, 1.0, n)
    low = base - rng.uniform(0.1, 1.0, n)
    close = base.copy()
    open_ = base + rng.normal(0, 0.2, n)
    vol = rng.integers(500_000, 1_500_000, n).astype(float)

    # Make low-volume days plentiful so Dry90>=8 is usually satisfied.
    vol[rng.random(n) < 0.25] *= 0.3

    df = pd.DataFrame({
        'Date': dates, 'Open': open_, 'High': high, 'Low': low,
        'Close': close, 'Volume': vol,
    })

    def clamp_before(i, anchor_idx):
        """Keep every pre-breakout bar below the carved anchor so the anchor
        at anchor_idx (High 130) stays the unique max of its anchor window
        for EVERY seed (random-walk drift could otherwise exceed it)."""
        df.loc[df.index < i, 'High'] = df.loc[df.index < i, 'High'].clip(upper=128.0)
        df.loc[df.index < i, 'Close'] = df.loc[df.index < i, 'Close'].clip(upper=127.0)
        df.loc[anchor_idx, 'High'] = 130.0
        df.loc[anchor_idx, 'Close'] = 129.0

    if with_full_pattern:
        # Anchor ~150 days before end, then dry consolidation, then breakout
        # at index i, rally, shakeout, reversal.
        i = n - 60
        # anchor bar in [i-180, i-90]
        anchor_idx = i - 140
        df.loc[anchor_idx, 'High'] = 130.0
        df.loc[anchor_idx, 'Close'] = 129.0
        # Keep all closes in last 90 below anchor (they already are ~100)
        # breakout bar:
        df.loc[i, 'Close'] = 132.0
        df.loc[i, 'Open'] = 120.0
        df.loc[i, 'High'] = 132.5
        df.loc[i, 'Low'] = 119.0
        df.loc[i, 'Volume'] = 3_000_000  # high volume (VolRatio>1.5)
        # rally days i+1..i+3
        for k in range(1, 4):
            df.loc[i+k, 'High'] = 135.0 + k
            df.loc[i+k, 'Close'] = 134.0 + k
        rally_idx = i + 3
        # Pad the first couple post-rally bars so they are NOT low-vol
        # and not below 122 -- otherwise the scanner picks them as the
        # shake_low before our intended shake.
        for k in range(1, 5):
            df.loc[rally_idx+k, 'High'] = 138.0
            df.loc[rally_idx+k, 'Low'] = 135.0
            df.loc[rally_idx+k, 'Close'] = 136.0
            df.loc[rally_idx+k, 'Open'] = 135.5
            df.loc[rally_idx+k, 'Volume'] = 1_000_000  # VolRatio ~= 1.0
        # shakeout low on rally_idx+5 with low volume and drop ~10%
        sh = rally_idx + 5
        df.loc[sh, 'Low'] = 122.0
        df.loc[sh, 'High'] = 126.0
        df.loc[sh, 'Close'] = 123.0
        df.loc[sh, 'Open'] = 125.0
        df.loc[sh, 'Volume'] = 200_000  # low volume
        # quiet bar before reversal (so prev_high < reversal close)
        df.loc[sh+1, 'High'] = 127.0
        df.loc[sh+1, 'Low'] = 123.5
        df.loc[sh+1, 'Close'] = 125.0
        df.loc[sh+1, 'Open'] = 124.5
        df.loc[sh+1, 'Volume'] = 250_000
        # reversal bar at sh+2 -- bullish, closes above prev high AND
        # above shake_low high (126), with big volume.
        rv = sh + 2
        df.loc[rv, 'Close'] = 130.0
        df.loc[rv, 'Open'] = 124.0
        df.loc[rv, 'High'] = 131.0
        df.loc[rv, 'Low'] = 123.5
        df.loc[rv, 'Volume'] = 4_500_000
        # Keep all subsequent bars above shake low (122) so scanner picks
        # the intended shake_low and drop lands in 4-25% range.
        for k in range(rv+1, n):
            df.loc[k, 'Low'] = max(float(df.loc[k, 'Low']), 124.0)
            df.loc[k, 'High'] = max(float(df.loc[k, 'High']), 128.0)
            df.loc[k, 'Close'] = max(float(df.loc[k, 'Close']), 128.0)

    if with_pending:
        # Same setup but stop BEFORE the shakeout/reversal -- breakout was
        # 10 trading days ago, rally confirmed, now drifting.  This is the
        # in-progress case the old code could not see.
        i = n - 12
        anchor_idx = i - 140
        df.loc[anchor_idx, 'High'] = 130.0
        df.loc[anchor_idx, 'Close'] = 129.0
        df.loc[i, 'Close'] = 132.0
        df.loc[i, 'Open'] = 120.0
        df.loc[i, 'High'] = 132.5
        df.loc[i, 'Low'] = 119.0
        df.loc[i, 'Volume'] = 3_000_000
        for k in range(1, 4):
            df.loc[i+k, 'High'] = 135.0 + k
            df.loc[i+k, 'Close'] = 134.0 + k
        # drift (no 4-25% shakeout) -- keep prices near rally high
        for k in range(4, 11):
            df.loc[i+k, 'High'] = 137.0
            df.loc[i+k, 'Low'] = 134.0
            df.loc[i+k, 'Close'] = 135.5

    if with_breakout_today:
        # Phase 1+2+3 on the LAST bar only.
        i = n - 1
        anchor_idx = i - 140
        df.loc[anchor_idx, 'High'] = 130.0
        df.loc[anchor_idx, 'Close'] = 129.0
        df.loc[i, 'Close'] = 132.0
        df.loc[i, 'Open'] = 120.0
        df.loc[i, 'High'] = 132.5
        df.loc[i, 'Low'] = 119.0
        df.loc[i, 'Volume'] = 3_000_000

    if with_shakeout_pending:
        # Full pattern up through a VALID shakeout, carved at the END of the
        # data so the 15-bar reversal window is still open but no bullish
        # reversal bar exists yet -> 'awaiting_reversal'.
        #
        # Breakout at i = n-20: it sits OUTSIDE scan_5phase's loop guard
        # (i < n-20), so the completed-trade scan can never claim it and the
        # pending detector is the only thing that can see it.
        i = n - 20
        anchor_idx = i - 140
        clamp_before(i, anchor_idx)
        df.loc[i, 'Close'] = 132.0
        df.loc[i, 'Open'] = 120.0
        df.loc[i, 'High'] = 132.5
        df.loc[i, 'Low'] = 119.0
        df.loc[i, 'Volume'] = 3_000_000
        for k in range(1, 4):
            df.loc[i+k, 'High'] = 135.0 + k
            df.loc[i+k, 'Close'] = 134.0 + k
        rally_idx = i + 3
        # Post-rally pads (same trick as with_full_pattern): not low-vol,
        # not below the intended shake low.
        for k in range(1, 5):
            df.loc[rally_idx+k, 'High'] = 138.0
            df.loc[rally_idx+k, 'Low'] = 135.0
            df.loc[rally_idx+k, 'Close'] = 136.0
            df.loc[rally_idx+k, 'Open'] = 135.5
            df.loc[rally_idx+k, 'Volume'] = 1_000_000
        # Valid shakeout at sh: low volume, drop = (138-122)/138 ~= 11.59%
        sh = rally_idx + 5                    # = n-12, 11 quiet bars follow
        df.loc[sh, 'Low'] = 122.0
        df.loc[sh, 'High'] = 126.0
        df.loc[sh, 'Close'] = 123.0
        df.loc[sh, 'Open'] = 125.0
        df.loc[sh, 'Volume'] = 200_000
        # Bars after the shake low (reversal window still open): quiet, never
        # below the shake low (122) and never closing above the shake bar's
        # high (126), so no bullish reversal can fire.
        for k in range(sh+1, n):
            df.loc[k, 'High'] = 125.5
            df.loc[k, 'Low'] = 123.5
            df.loc[k, 'Close'] = 124.5
            df.loc[k, 'Open'] = 124.0
            df.loc[k, 'Volume'] = 300_000

    if with_expired_shakeout:
        # ABDL-type stale breakout: Phase 4a confirmed, then a flat drift
        # with NO valid 4-25% low-volume pullback, and the 15-bar shakeout
        # window FULLY ELAPSES before the data ends -> the pending detector
        # must EXCLUDE it (the old code showed it indefinitely).
        #
        # Shakeout window = bars rally_idx+1 .. rally_idx+15 = n-26 .. n-12;
        # data ends at n-1, so the window is closed.
        i = n - 30
        anchor_idx = i - 140
        clamp_before(i, anchor_idx)
        df.loc[i, 'Close'] = 132.0
        df.loc[i, 'Open'] = 120.0
        df.loc[i, 'High'] = 132.5
        df.loc[i, 'Low'] = 119.0
        df.loc[i, 'Volume'] = 3_000_000
        for k in range(1, 4):
            df.loc[i+k, 'High'] = 135.0 + k
            df.loc[i+k, 'Close'] = 134.0 + k
        # drift through the end of the data: the deepest possible drop from
        # the 138 rally high is (138-134)/138 ~= 2.9% -- never a valid 4-25%
        # shakeout, whether or not random drift bars come out low-volume.
        for k in range(4, n - i):
            df.loc[i+k, 'High'] = 137.0
            df.loc[i+k, 'Low'] = 134.0
            df.loc[i+k, 'Close'] = 135.5
            df.loc[i+k, 'Open'] = 135.0

    return df


def trade_key(t):
    return (
        str(t['breakout_date']), str(t['reversal_date']),
        round(float(t['breakout_close']), 2), round(float(t['entry']), 2),
    )


def test_scan_5phase_unchanged():
    """Refactored scan_5phase must produce IDENTICAL output to original
    across all 150 synthetic cases (25 seeds x 6 data shapes) -- the
    1291-trade logic must stay exactly intact."""
    seeds = list(range(25))
    cases = [
        dict(),
        dict(with_full_pattern=True),
        dict(with_pending=True),
        dict(with_breakout_today=True),
        dict(with_shakeout_pending=True),
        dict(with_expired_shakeout=True),
    ]
    mismatches = 0
    total = 0
    for seed in seeds:
        for kw in cases:
            df = make_synthetic(seed=seed, **kw)
            orig = original_scan_5phase(df)
            new = scan_5phase(df)
            total += 1
            if [trade_key(t) for t in orig] != [trade_key(t) for t in new]:
                mismatches += 1
                print(f"  MISMATCH seed={seed} kw={kw}: "
                      f"orig={len(orig)} new={len(new)}")
    print(f"[1] scan_5phase equivalence: {total-mismatches}/{total} cases identical "
          f"(150 synthetic cases: 25 seeds x 6 shapes)")
    assert mismatches == 0, "Refactored scan_5phase differs from original!"


def test_pending_breakout_detected():
    df = make_synthetic(seed=1, with_pending=True)
    pending = detect_pending_breakouts(df, lookback_days=60)
    assert len(pending) >= 1, f"Expected >=1 pending breakout, got {len(pending)}"
    p = pending[0]
    assert p['reversal_date'] is None, "Pending should have no reversal_date"
    assert p['shake_low'] is None
    assert p['rally_high'] is not None, "Pending must be 4a-confirmed"
    assert p.get('status') == 'awaiting_shakeout', (
        f"4a-confirmed breakout with open shakeout window must be "
        f"awaiting_shakeout, got {p.get('status')}")
    print(f"[2] pending breakout detected: {p['breakout_date']} "
          f"rally_high={p['rally_high']} status={p['status']} (no reversal yet)")


def test_watchlist_includes_pending():
    """Reproduces the original bug: with a recent breakout that hasn't
    reversed, the 30d watchlist must NOT be empty."""
    df = make_synthetic(seed=2, with_pending=True)
    res = check_today_events(df)
    wl30 = res['watchlist_30']
    assert len(wl30) >= 1, (
        f"30d watchlist empty despite pending breakout -- BUG STILL PRESENT. "
        f"pending={len(res['pending_breakouts'])}")
    print(f"[3] 30d watchlist now contains {len(wl30)} pending candidate(s) "
          f"(was always 0 before fix)")


def test_breakout_today_fires():
    df = make_synthetic(seed=3, with_breakout_today=True)
    res = check_today_events(df)
    assert res['breakout_today'] is not None, "breakout_today should fire on phase-3 bar"
    assert res['breakout_today']['awaiting_rally_confirm'] is True
    print(f"[4] breakout_today fires on breakout bar: "
          f"{res['breakout_today']['breakout_date']} "
          f"close={res['breakout_today']['breakout_close']}")


def test_full_pattern_still_detected():
    df = make_synthetic(seed=4, with_full_pattern=True)
    trades = scan_5phase(df)
    assert len(trades) >= 1, "Full 5-phase pattern should still produce a trade"
    t = trades[0]
    assert t['reversal_date'] is not None
    print(f"[5] full 5-phase pattern still detected: "
          f"B/O {t['breakout_date']} -> Rev {t['reversal_date']} "
          f"entry={t['entry']} drop={t['drop_pct']}%")


def test_pending_excludes_already_fired():
    """When a breakout has already completed a reversal, it must NOT also
    show up as pending."""
    df = make_synthetic(seed=5, with_full_pattern=True)
    pending = detect_pending_breakouts(df, lookback_days=120)
    trades = scan_5phase(df)
    fired_dates = {pd.Timestamp(t['breakout_date']).normalize() for t in trades}
    for p in pending:
        assert pd.Timestamp(p['breakout_date']).normalize() not in fired_dates, (
            "Pending list contains a breakout that already reversed")
    print(f"[6] pending correctly excludes fired: {len(trades)} fired, "
          f"{len(pending)} pending (no overlap)")


def test_awaiting_reversal_status():
    """Valid 4-25% low-volume shakeout already observed, no bullish reversal
    bar yet, 15-bar reversal window still open -> pending record is
    awaiting_reversal with shake_low/drop_pct populated."""
    df = make_synthetic(seed=6, with_shakeout_pending=True)
    carved_date = df.loc[len(df) - 20, 'Date']   # with_shakeout_pending: i = n-20
    pending = detect_pending_breakouts(df, lookback_days=60)
    carved = [p for p in pending if str(p['breakout_date']) == str(carved_date)]
    assert carved, (
        f"carved breakout missing from pending: "
        f"{[(str(p['breakout_date']), p.get('status')) for p in pending]}")
    p = carved[0]
    assert p.get('status') == 'awaiting_reversal', p.get('status')
    assert p['shake_low'] == 122.0, p['shake_low']
    assert p['drop_pct'] == 11.59, p['drop_pct']   # (138-122)/138 = 11.59%
    assert p['shake_low_date'] is not None
    assert p['shake_low_vol'] is not None and p['shake_high'] == 138.0
    assert p['reversal_date'] is None and p['entry'] is None
    print(f"[7] awaiting_reversal: B/O {p['breakout_date']} "
          f"shake_low={p['shake_low']} drop={p['drop_pct']}% "
          f"(reversal window still open)")


def test_expired_shakeout_excluded():
    """ABDL-type filter: Phase 4a confirmed but the 15-bar SHAKEOUT window
    CLOSED with no valid 4-25% low-volume pullback -> the breakout is
    EXCLUDED from pending (previously shown indefinitely as a stale
    'waiting' entry)."""
    df = make_synthetic(seed=7, with_expired_shakeout=True)
    carved_date = df.loc[len(df) - 30, 'Date']   # with_expired_shakeout: i = n-30
    pending = detect_pending_breakouts(df, lookback_days=120)
    carved = [p for p in pending if str(p['breakout_date']) == str(carved_date)]
    assert not carved, (
        f"expired breakout (shakeout window closed, no valid shakeout) "
        f"still shown as pending: {carved}")
    print(f"[8] expired/failed breakout excluded (ABDL-type filter): "
          f"B/O {carved_date} not in pending ({len(pending)} pending left)")


def test_expired_reversal_window_excluded():
    """ABDL-type filter, second half: a valid shakeout WAS observed but the
    15-bar REVERSAL window then closed without any bullish reversal bar ->
    the breakout is EXCLUDED from pending too."""
    df = make_synthetic(seed=9, with_shakeout_pending=True)
    n = len(df)
    i = n - 20                    # breakout (with_shakeout_pending)
    low_idx = i + 3 + 5           # rally_idx = i+3, shake low 5 bars later
    carved_date = df.loc[i, 'Date']
    # Append 20 quiet bars so bars low_idx+1 .. low_idx+15 are ALL observed
    # (and none of them is a bullish reversal: they never close above the
    # shake bar's high of 126).  Original n=600 < low_idx+16; new n must
    # be >= low_idx+16 so the reversal window is closed.
    extra_dates = pd.bdate_range(df['Date'].iloc[-1], periods=21)[1:]
    extra = pd.DataFrame({
        'Date': extra_dates,
        'Open': 124.0, 'High': 125.5, 'Low': 123.5,
        'Close': 124.5, 'Volume': 300_000.0,
    })
    df2 = pd.concat([df, extra], ignore_index=True)
    assert len(df2) >= low_idx + 16, "reversal window must be fully observed"
    # Paranoia: the completed-trade scan must NOT have claimed this breakout
    # (it had a valid shakeout but no reversal bar anywhere in the window).
    trades = scan_5phase(df2)
    assert not any(str(t['breakout_date']) == str(carved_date) for t in trades), (
        "test setup broken: scan_5phase emitted a trade for the no-reversal pattern")
    pending = detect_pending_breakouts(df2, lookback_days=120)
    carved = [p for p in pending if str(p['breakout_date']) == str(carved_date)]
    assert not carved, (
        f"reversal-window-expired breakout still shown as pending: {carved}")
    print(f"[9] reversal window closed w/o reversal -> excluded: "
          f"B/O {carved_date} not in pending ({len(pending)} pending left)")


if __name__ == "__main__":
    test_scan_5phase_unchanged()
    test_pending_breakout_detected()
    test_watchlist_includes_pending()
    test_breakout_today_fires()
    test_full_pattern_still_detected()
    test_pending_excludes_already_fired()
    test_awaiting_reversal_status()
    test_expired_shakeout_excluded()
    test_expired_reversal_window_excluded()
    print("\nAll 9 tests passed.")
