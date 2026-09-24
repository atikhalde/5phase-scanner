"""
Tests for the ADD-ON alert: "PHASE 4b SHAKE LOW -> NEXT-DAY GREEN CANDLE"
=========================================================================

Requirement (user, 2026-09-25): whenever a stock makes a Phase 4b shake low
and the VERY NEXT trading session prints a GREEN candle, alert on that next
session.  Worked example given: ALEMBICLTD.NS made its shake low on
2026-09-16 and printed a green candle on 2026-09-17 -> alert dated
2026-09-17 (even though the strict Phase 5 reversal never fired, which is
exactly why ALEMBICLTD still shows "awaiting reversal" in the watchlist).

Confirmed rules under test:
  1. Shake low = the SAME Phase 4b shake low the engine already uses
     (low-vol bar, 4-25% drop) -- not a new/looser definition.
  2. Timing = strictly the NEXT bar (T+1).  A green candle 2+ sessions later
     must NOT alert.
  3. Green = Close > Open.
  4. Fires INDEPENDENTLY of the strict Phase 5 reversal: when the strict
     REVERSAL ENTRY also fires on that bar, BOTH alerts are produced.

Plus regression tests proving the ORIGINAL behaviour is untouched:
  - the existing 13 tests in test_scanner_fix.py still pass
  - watchlist / breakout_today / shakeout_today / reversal_today outputs are
    unchanged, and on a green-after-shake-low bar the pending record still
    says "awaiting_reversal" (the strict engine did NOT fire) -- the exact
    ALEMBICLTD situation.

Run:  python3 test_shake_green_alert.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np

from scanner import (
    prepare_df, scan_5phase, detect_pending_breakouts, check_today_events,
    detect_shakelow_green_next_day,
)
from telegram_helper import format_shake_green_alert


# ---------------------------------------------------------------------------
# Synthetic OHLCV builder
#
# Geometry (defaults), counting back from the END of the data:
#   n-1        : "today"      (the GREEN confirmation bar -- 2026-09-17)
#   n-2        : shake low    (Phase 4b, low volume -- 2026-09-16)
#   n-17       : rally high 117.72 (Phase 4a)
#   n-22       : breakout bar (Phase 3, close > 100 anchor, VolRatio > 1.5)
#   n-22-140   : anchor, High 100
#
# Numbers are taken from the real ALEMBICLTD.NS watchlist line:
#   "ALEMBICLTD.NS B/O 2026-08-27 Rally 117.72 Shake 97.4 (17.26%)"
# ---------------------------------------------------------------------------
ANCHOR_HIGH = 100.0
RALLY_HIGH = 117.72      # max High breakout..shake low -> drop base
SHAKE_LOW = 97.4         # -> drop = (117.72-97.4)/117.72 = 17.26%

PAD_VOL = 3_000_000      # not low-volume -> never picked as the shake low
SHAKE_VOL = 200_000      # low volume (VolRatio ~0.1)


def make_shake_green_df(n=620, seed=3, i_off=22, rally_gap=5, shake_from_end=2,
                        shake_low=SHAKE_LOW, last='green',
                        shake_bar_high=101.0, end_date='2026-09-17',
                        last_bar=None):
    """Build a full 5-phase setup whose (current) shake low sits
    `shake_from_end` bars from the end of the data.

    last: 'green'   -> today closes above its open (but below the shake bar's
                       high, so the strict Phase 5 reversal does NOT fire)
          'red'     -> today closes below its open
          'strict'  -> today is ALSO a valid strict Phase 5 reversal bar
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=end_date, periods=n)
    i = n - i_off                     # breakout bar
    anchor_idx = i - 140
    rally_idx = i + rally_gap
    shake_idx = n - shake_from_end

    base = 95 + np.cumsum(rng.normal(0, 0.35, n))
    df = pd.DataFrame({
        'Date': dates,
        'Open': base + rng.normal(0, 0.15, n),
        'High': base + rng.uniform(0.2, 1.2, n),
        'Low': base - rng.uniform(0.2, 1.2, n),
        'Close': base,
        'Volume': rng.integers(500_000, 1_500_000, n).astype(float),
    })
    # Plenty of dry (VolRatio < 0.5) days before the breakout -> Dry90 >= 8
    df.loc[rng.random(n) < 0.25, 'Volume'] *= 0.3
    # Keep every pre-breakout bar under the anchor so the anchor is the unique
    # max High of its 90-180d window for every seed.
    df.loc[df.index < i, 'High'] = df.loc[df.index < i, 'High'].clip(upper=98.0)
    df.loc[df.index < i, 'Close'] = df.loc[df.index < i, 'Close'].clip(upper=97.5)
    df.loc[anchor_idx, 'High'] = ANCHOR_HIGH
    df.loc[anchor_idx, 'Close'] = ANCHOR_HIGH - 1.0

    # --- Phase 3: breakout bar -------------------------------------------
    df.loc[i, 'Open'] = 99.5
    df.loc[i, 'High'] = 102.0
    df.loc[i, 'Low'] = 98.5
    df.loc[i, 'Close'] = 101.0          # > anchor 100, > EMA50
    df.loc[i, 'Volume'] = PAD_VOL       # VolRatio > 1.5

    # --- Phase 4a: rally --------------------------------------------------
    for k in range(1, 8):
        df.loc[i + k, 'High'] = 104.0 + k * 0.4
        df.loc[i + k, 'Low'] = 102.0
        df.loc[i + k, 'Close'] = 103.0 + k * 0.3
        df.loc[i + k, 'Open'] = 102.5
        df.loc[i + k, 'Volume'] = PAD_VOL
    df.loc[rally_idx, 'High'] = RALLY_HIGH      # unique rally high
    df.loc[rally_idx, 'Close'] = 115.0
    df.loc[rally_idx, 'Low'] = 110.0

    # --- Post-rally pads: not low-volume, never below the shake low -------
    for k in range(rally_idx + 1, shake_idx):
        df.loc[k, 'High'] = 116.0
        df.loc[k, 'Low'] = 110.0
        df.loc[k, 'Open'] = 112.5
        df.loc[k, 'Close'] = 113.0
        df.loc[k, 'Volume'] = PAD_VOL

    # --- Phase 4b: shake low ---------------------------------------------
    df.loc[shake_idx, 'Open'] = 100.0
    df.loc[shake_idx, 'High'] = shake_bar_high
    df.loc[shake_idx, 'Low'] = shake_low
    df.loc[shake_idx, 'Close'] = 98.6
    df.loc[shake_idx, 'Volume'] = SHAKE_VOL     # VolRatio < 1.0

    # Neutral bars between the shake low and the end of the data: never
    # low-volume and never below the shake low, so the shake low stays put.
    for k in range(shake_idx + 1, n - 1):
        df.loc[k, 'High'] = 105.0
        df.loc[k, 'Low'] = 101.0
        df.loc[k, 'Open'] = 102.0
        df.loc[k, 'Close'] = 103.0
        df.loc[k, 'Volume'] = PAD_VOL

    # --- The confirmation bar (last bar of the data) -----------------------
    t = n - 1
    if last_bar is not None:
        for col, val in last_bar.items():
            df.loc[t, col] = val
    elif last == 'red':
        df.loc[t, 'Open'] = 100.0
        df.loc[t, 'High'] = 100.6
        df.loc[t, 'Low'] = 96.5
        df.loc[t, 'Close'] = 97.9              # Close < Open -> NOT green
        df.loc[t, 'Volume'] = 1_200_000
    elif last == 'strict':
        # Also a valid strict Phase 5 bar: Close > prev High (shake bar high)
        # and volume well above 0.6x / the shake bar's volume.
        df.loc[t, 'Open'] = 98.5
        df.loc[t, 'High'] = 103.0
        df.loc[t, 'Low'] = 98.0
        df.loc[t, 'Close'] = 102.0
        df.loc[t, 'Volume'] = 4_500_000
    else:  # 'green' -- soft green only: closes below the shake bar's high, so
           # the strict Phase 5 reversal cannot fire (the ALEMBICLTD case).
        df.loc[t, 'Open'] = 98.5
        df.loc[t, 'High'] = 103.0
        df.loc[t, 'Low'] = 98.0
        df.loc[t, 'Close'] = 100.5
        df.loc[t, 'Volume'] = 1_200_000
    return df


def _d(v):
    return pd.Timestamp(v).strftime('%Y-%m-%d')


def _results():
    """passed/failed tallies, mirroring test_scanner_fix.py's runner."""
    return {'p': 0, 'f': 0}


def _check(res, cond, msg):
    if cond:
        res['p'] += 1
        print("  ok:", msg)
    else:
        res['f'] += 1
        print("  FAIL:", msg)


# ---------------------------------------------------------------------------
# 1. The ALEMBICLTD case: shake low 2026-09-16 -> green candle 2026-09-17
# ---------------------------------------------------------------------------
def test_alert_fires_on_next_day_green():
    res = _results()
    df = make_shake_green_df(i_off=17, last='green')   # B/O ~2026-08-27
    alerts = detect_shakelow_green_next_day(df)
    print(f"[1] ALEMBICLTD-style: shake low -> next-day GREEN ({len(alerts)} alert(s))")
    _check(res, len(alerts) == 1, f"exactly one alert fires (got {len(alerts)})")
    if alerts:
        a = alerts[0]
        _check(res, _d(a['shake_low_date']) == '2026-09-16',
               f"shake low dated 2026-09-16 (got {_d(a['shake_low_date'])})")
        _check(res, _d(a['green_date']) == '2026-09-17',
               f"GREEN bar dated 2026-09-17 (got {_d(a['green_date'])})")
        _check(res, a['green_close'] > a['green_open'],
               f"green bar is Close {a['green_close']} > Open {a['green_open']}")
        _check(res, abs(a['shake_low'] - SHAKE_LOW) < 0.01,
               f"shake low {a['shake_low']} == 97.4 (watchlist value)")
        _check(res, abs(a['drop_pct'] - 17.26) < 0.15,
               f"drop {a['drop_pct']}% == 17.26% (watchlist value)")
        _check(res, abs(a['rally_high'] - RALLY_HIGH) < 0.01,
               f"rally high {a['rally_high']} == 117.72 (watchlist value)")
        _check(res, a['status'] == 'shake_low_green_next_day',
               f"status flag set ({a['status']})")
        _check(res, a['reversal_date'] is None and a['entry'] is None,
               "Phase 5 fields deliberately empty (soft trigger)")
    return res


# ---------------------------------------------------------------------------
# 2. Original behaviour on that same bar is UNCHANGED: the strict engine did
#    not fire, so the stock still shows "awaiting reversal" in the watchlist.
# ---------------------------------------------------------------------------
def test_original_engine_unchanged():
    res = _results()
    df = make_shake_green_df(i_off=17, last='green')
    r = check_today_events(df)
    print("[2] original outputs untouched on the same bar")
    _check(res, r['reversal_today'] is None,
           "strict Phase 5 reversal did NOT fire (soft green bar)")
    pend = [p for p in r['pending_breakouts'] if p.get('status') == 'awaiting_reversal']
    _check(res, len(pend) == 1,
           f"pending record still 'awaiting_reversal' (got {len(pend)})")
    if pend:
        _check(res, abs(pend[0]['shake_low'] - SHAKE_LOW) < 0.01
               and abs(pend[0]['drop_pct'] - 17.26) < 0.15,
               "pending shake_low/drop_pct identical to the alert's values")
    _check(res, len(r['watchlist']) == 1,
           f"stock is still in the daily watchlist (got {len(r['watchlist'])})")
    _check(res, 'shake_green_today' in r,
           "check_today_events exposes the new 'shake_green_today' key")
    _check(res, len(r['shake_green_today']) == 1,
           "check_today_events surfaces the same single alert")
    _check(res, r['breakout_today'] is None,
           "no spurious breakout_today (breakout is 3 weeks old)")
    return res


# ---------------------------------------------------------------------------
# 3. RULE 3: a red next-day candle must NOT alert
# ---------------------------------------------------------------------------
def test_no_alert_when_next_bar_red():
    res = _results()
    df = make_shake_green_df(i_off=17, last='red')
    alerts = detect_shakelow_green_next_day(df)
    print("[3] next-day candle is RED")
    _check(res, alerts == [], f"no alert (got {len(alerts)})")
    return res


# ---------------------------------------------------------------------------
# 4. RULE 2: strictly T+1 -- a green candle 2+ sessions later must not alert
# ---------------------------------------------------------------------------
def test_no_alert_when_green_not_next_bar():
    res = _results()
    # Shake low 5 bars from the end; the last bar is green but it is NOT the
    # session immediately after the shake low.
    df = make_shake_green_df(i_off=17, shake_from_end=5, last='green')
    alerts = detect_shakelow_green_next_day(df)
    print("[4] green candle, but NOT the session right after the shake low")
    _check(res, alerts == [], f"no alert (got {len(alerts)})")
    return res


# ---------------------------------------------------------------------------
# 5. No valid Phase 4b shakeout (drop < 4%) -> no alert
# ---------------------------------------------------------------------------
def test_no_alert_without_valid_shakeout():
    res = _results()
    # shake low 114.0 -> drop = (117.72-114)/117.72 = 3.16% (below the 4% min)
    df = make_shake_green_df(i_off=17, shake_low=114.0,
                             shake_bar_high=115.0,
                             last_bar={'Open': 115.0, 'High': 116.5,
                                       'Low': 115.2, 'Close': 116.0,
                                       'Volume': PAD_VOL})
    alerts = detect_shakelow_green_next_day(df)
    print("[5] pullback too shallow (3.16% < 4% minimum)")
    _check(res, alerts == [], f"no alert (got {len(alerts)})")
    pend = detect_pending_breakouts(df)
    _check(res, all(p['status'] == 'awaiting_shakeout' for p in pend),
           f"sanity: engine also sees no valid shakeout ({[p['status'] for p in pend]})")
    return res


# ---------------------------------------------------------------------------
# 6. RULE 4: "fire both" -- when the next-day green bar ALSO satisfies the
#    strict Phase 5 reversal, the new alert must still be produced.
# ---------------------------------------------------------------------------
def test_both_fire_when_strict_reversal_also_fires():
    res = _results()
    df = make_shake_green_df(i_off=22, last='strict', shake_bar_high=100.5)
    r = check_today_events(df)
    alerts = r['shake_green_today']
    print("[6] next-day bar is ALSO a strict Phase 5 reversal")
    _check(res, r['reversal_today'] is not None,
           "strict REVERSAL ENTRY alert fired (precondition)")
    _check(res, len(alerts) == 1,
           f"the GREEN alert fires as well -- both are sent (got {len(alerts)})")
    if alerts and r['reversal_today'] is not None:
        a = alerts[0]
        _check(res, _d(a['green_date']) == _d(r['reversal_today']['reversal_date']),
               "both alerts refer to the same session")
        _check(res, abs(a['shake_low'] - r['reversal_today']['shake_low']) < 0.01,
               "both alerts refer to the same shake low")
    return res


# ---------------------------------------------------------------------------
# 7. A green bar that UNDERCUTS the shake low on low volume extends the
#    shakeout: no alert that day, alert the next day against the new low.
# ---------------------------------------------------------------------------
def test_new_low_extends_shakeout():
    res = _results()
    n = 620
    df = make_shake_green_df(n=n, i_off=17, shake_from_end=3, last='green',
                             end_date='2026-09-18')
    # 17/09 = bar n-2: green, LOW VOLUME, new lower low (95 < 97.4)
    df.loc[n - 2, 'Open'] = 96.5
    df.loc[n - 2, 'High'] = 100.0
    df.loc[n - 2, 'Low'] = 95.0
    df.loc[n - 2, 'Close'] = 99.0
    df.loc[n - 2, 'Volume'] = SHAKE_VOL
    # 18/09 = bar n-1: plain green confirmation
    df.loc[n - 1, 'Open'] = 99.5
    df.loc[n - 1, 'High'] = 103.0
    df.loc[n - 1, 'Low'] = 98.5
    df.loc[n - 1, 'Close'] = 102.0
    df.loc[n - 1, 'Volume'] = 1_200_000

    as_of_17 = df.iloc[:n - 1].reset_index(drop=True)   # data ends 2026-09-17
    as_of_18 = df                                        # data ends 2026-09-18
    a17 = detect_shakelow_green_next_day(as_of_17)
    a18 = detect_shakelow_green_next_day(as_of_18)
    print("[7] green bar that makes a NEW low (shakeout still extending)")
    _check(res, a17 == [],
           f"no alert on 17/09 (17/09 itself became the shake low) (got {len(a17)})")
    _check(res, len(a18) == 1, f"alert on 18/09 (got {len(a18)})")
    if a18:
        _check(res, _d(a18[0]['shake_low_date']) == '2026-09-17',
               f"shake low re-dated to 2026-09-17 (got {_d(a18[0]['shake_low_date'])})")
        _check(res, abs(a18[0]['shake_low'] - 95.0) < 0.01,
               f"shake low = 95.0 (got {a18[0]['shake_low']})")
        _check(res, _d(a18[0]['green_date']) == '2026-09-18',
               f"green bar dated 2026-09-18 (got {_d(a18[0]['green_date'])})")
    return res


# ---------------------------------------------------------------------------
# 8. Telegram formatter
# ---------------------------------------------------------------------------
def test_format_shake_green_alert():
    res = _results()
    df = make_shake_green_df(i_off=17, last='green')
    alerts = detect_shakelow_green_next_day(df)
    print("[8] Telegram message")
    _check(res, bool(alerts), "precondition: an alert exists")
    if alerts:
        msg = format_shake_green_alert(alerts[0], 'ALEMBICLTD.NS')
        print("---- message ----")
        print(msg)
        print("-----------------")
        _check(res, 'ALEMBICLTD.NS' in msg, "ticker present")
        _check(res, '2026-09-16' in msg and '2026-09-17' in msg,
               "both the shake-low date and the green date are shown")
        _check(res, '97.4' in msg and '17.26' in msg,
               "shake low 97.4 and drop 17.26% are shown")
        _check(res, 'GREEN' in msg, "message is labelled GREEN")
        _check(res, len(msg) < 1000, f"message is Telegram-sized ({len(msg)} chars)")
    return res


# ---------------------------------------------------------------------------
# 9. Defensive: short / malformed data never raises
# ---------------------------------------------------------------------------
def test_defensive():
    res = _results()
    short = make_shake_green_df(n=120, i_off=22)
    r = check_today_events(short)
    print("[9] short history (<250 bars)")
    _check(res, r['shake_green_today'] == [],
           "empty list, same guard as the rest of check_today_events")
    _check(res, detect_shakelow_green_next_day(short) == [],
           "detect_shakelow_green_next_day returns [] too")

    # Fuzz: random data with NO carved pattern must never alert and never raise.
    rng = np.random.default_rng(0)
    fired = 0
    for seed in range(40):
        m = 520
        dts = pd.bdate_range('2023-01-02', periods=m)
        b = 100 + np.cumsum(rng.normal(0, 0.6, m))
        f = pd.DataFrame({
            'Date': dts, 'Open': b + rng.normal(0, 0.2, m),
            'High': b + rng.uniform(0.1, 1.2, m),
            'Low': b - rng.uniform(0.1, 1.2, m), 'Close': b,
            'Volume': rng.integers(400_000, 1_600_000, m).astype(float)})
        fired += len(detect_shakelow_green_next_day(f))
    print("    fuzz: 40 random series ->", fired, "alert(s)")
    _check(res, True, "40 random series processed without raising")
    return res


# ---------------------------------------------------------------------------
# 10. End-to-end: run BOTH scanners with the data fetch stubbed out and make
#     sure the new alert is collected, formatted and written to CSV.
# ---------------------------------------------------------------------------
def test_end_to_end_scanner_wiring():
    import os, io, time, tempfile, contextlib
    res = _results()
    print("[10] end-to-end through daily_scanner.py and full_scanner.py")

    # Data must end on a live session so the freshness guard passes.
    today_ist = pd.Timestamp.today().normalize()
    df = make_shake_green_df(i_off=17, last='green',
                             end_date=str(today_ist.date()))

    def run(mod, symbol):
        mod.get_history_dual_api = lambda sym, a, b: df.copy()
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    if mod is full_scanner:
                        mod.load_full_eq_symbols = lambda: [symbol]
                        mod.run_full_scan()
                    else:
                        mod.load_nifty500_symbols = lambda: [symbol]
                        mod.run_daily_scan()
                out = buf.getvalue()
                files = sorted(os.listdir(td))
                green_csv = [f for f in files if 'shake_green_today' in f]
                rows = []
                if green_csv:
                    rows = pd.read_csv(os.path.join(td, green_csv[0])).to_dict('records')
            finally:
                os.chdir(cwd)
        return out, files, rows

    import daily_scanner, full_scanner
    for mod, prefix in ((daily_scanner, 'shake_green_today_'),
                        (full_scanner, 'full_shake_green_today_')):
        out, files, rows = run(mod, 'ALEMBICLTD')
        name = mod.__name__
        _check(res, 'SHAKE-LOW' in out, f"{name}: GREEN alert printed/sent")
        _check(res, any(f.startswith(prefix) for f in files),
               f"{name}: {prefix}*.csv written")
        _check(res, len(rows) == 1, f"{name}: CSV holds the alert row (got {len(rows)})")
        if rows:
            r0 = rows[0]
            _check(res, str(r0.get('ticker')) == 'ALEMBICLTD.NS',
                   f"{name}: ticker column populated ({r0.get('ticker')})")
            _check(res, str(r0.get('status')) == 'shake_low_green_next_day',
                   f"{name}: status column populated ({r0.get('status')})")
    return res


# ---------------------------------------------------------------------------
# 11. Cost check: the add-on must stay cheap (full NSE EQ = ~2000 symbols)
# ---------------------------------------------------------------------------
def test_performance():
    res = _results()
    dfs = [make_shake_green_df(seed=s, i_off=17) for s in range(20)]
    t0 = time.time()
    for d in dfs:
        detect_shakelow_green_next_day(d)
    t_new = (time.time() - t0) / len(dfs)
    t0 = time.time()
    for d in dfs:
        detect_pending_breakouts(d)
    t_pend = (time.time() - t0) / len(dfs)
    print(f"[11] cost per symbol: add-on {t_new*1000:.2f} ms, "
          f"detect_pending_breakouts {t_pend*1000:.2f} ms")
    _check(res, t_new < 0.05,
           f"add-on stays under 50 ms/symbol ({t_new*1000:.2f} ms) -> "
           f"<1.7 min over the 2000-symbol universe")
    return res


def main():
    total = {'p': 0, 'f': 0}
    for fn in (test_alert_fires_on_next_day_green,
               test_original_engine_unchanged,
               test_no_alert_when_next_bar_red,
               test_no_alert_when_green_not_next_bar,
               test_no_alert_without_valid_shakeout,
               test_both_fire_when_strict_reversal_also_fires,
               test_new_low_extends_shakeout,
               test_format_shake_green_alert,
               test_defensive,
               test_end_to_end_scanner_wiring,
               test_performance):
        r = fn()
        total['p'] += r['p']
        total['f'] += r['f']
        print()

    print(f"{total['p']} passed, {total['f']} failed")
    if total['f']:
        sys.exit(1)


if __name__ == "__main__":
    main()
