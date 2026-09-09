#!/usr/bin/env python
"""
verify_shakeout_alert.py -- does the "Shakeout / Pullback Alert" (Phase 4b)
actually fire, or is it silently dead?

WHY THIS EXISTS
---------------
`scanner._is_phase4b_shakeout_today()` is only ever exercised in unit tests on
hand-carved synthetic bars, which always pass by construction.  That tells us
nothing about whether the alert fires on the real geometry the strategy was
built from.  This script replays the alert DAY BY DAY over charts whose
Phase 1-5 geometry is taken from the real 1291-trade backtest
(`sample_1291_trades.csv`) and measures:

  * RECALL  -- of the N real Phase 4b shakeouts, how many produced a
               `shakeout_today` alert ON the actual shakeout day?
  * REASONS -- for every miss, which single filter killed it (volume / window /
               drop band / level-not-reached / level-broken / not-a-new-low /
               no-pending-breakout)?
  * NOISE   -- how often the alert fires on days that are NOT the shakeout day
               (false positives inside the replay window)?

TWO INPUT MODES
---------------
1) `--mode reconstruct` (default, works offline)
   Rebuilds a plausible OHLCV series for every row of `sample_1291_trades.csv`
   using the REAL anchor/breakout/rally/shake/reversal prices, volumes and bar
   offsets from that row, then replays the alert over it.  The reconstruction is
   deliberately "clean" (monotone rally -> decline -> reversal, the shakeout bar
   is the low-volume window low), so the measured recall is an UPPER BOUND:
   real charts are noisier and can only do worse.

2) `--mode bars --bars-dir DIR`
   Replays over REAL bars.  DIR must contain one CSV per symbol
   (`SYMBOL.csv`) with columns Date,Open,High,Low,Close,Volume (this is what
   Dhan / yfinance produce).  For each symbol it walks forward one bar at a
   time and reports every day the alert fires, plus -- for cross-checking --
   every shakeout the offline `scan_5phase()` finds in the same bars.

USAGE
-----
    python tools/verify_shakeout_alert.py                       # 300 trades
    python tools/verify_shakeout_alert.py --limit 1291 --workers 2
    python tools/verify_shakeout_alert.py --mode bars --bars-dir data/nse

Exit code is 0 when recall > 0 (alert is alive), 1 when the alert never fires
once across the whole sample (alert is dead).
"""
import argparse
import os
import sys
import json
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import (  # noqa: E402
    check_today_events,
    prepare_df,
    detect_pending_breakouts,
    _is_phase4b_shakeout_today,
    _check_breakout_confirmed,
    scan_5phase,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_CSV = os.path.join(REPO, "sample_1291_trades.csv")

BO_IDX = 260          # breakout bar index (>=250 so the 250-bar guards are met)
PRE_ANCHOR_CAP = 0.99  # pre-breakout highs stay below the carved anchor high


# ---------------------------------------------------------------------------
# 1. Reconstruct a chart from a real backtest trade row
# ---------------------------------------------------------------------------
def _vol_for_ratio(prev_volumes, ratio):
    """Exact volume that makes VolRatio (= v / rolling20_mean INCLUDING v)
    equal to `ratio`, given the previous 19 bars' volumes.

        ratio = v / ((sum(prev19) + v) / 20)  =>  v = ratio*S19 / (20 - ratio)
    """
    s19 = float(np.sum(prev_volumes[-19:])) if len(prev_volumes) >= 19 else 0.0
    # VolRatio = v / rolling20_mean(v) where the mean INCLUDES v, so the ratio
    # can never reach 20.0 -- clamp carvable targets (some backtest rows carry
    # vol_break up to 20.0 from a slightly different VolRatio definition).
    ratio = min(float(ratio), 19.0)
    v = ratio * s19 / (20.0 - ratio)
    return max(v, 1.0)


def build_bars(row, seed, variant="clean"):
    """Build a daily OHLCV DataFrame whose 5-phase geometry equals `row`."""
    rng = np.random.default_rng(seed)

    anchor_high = float(row["anchor_high"])
    bo_close = float(row["breakout_close"])
    rally_high = float(row["rally_high"])
    shake_high = float(row["shake_high"])
    shake_low = float(row["shake_low"])
    entry = float(row["entry"])

    bo_date = pd.Timestamp(row["breakout_date"])
    rally_date = pd.Timestamp(row["rally_high_date"])
    shake_date = pd.Timestamp(row["shake_low_date"])
    rev_date = pd.Timestamp(row["reversal_date"])
    days_since = int(row["days_since"])

    def gap(a, b):
        return max(0, int(np.busday_count(a.date(), b.date())))

    # Bar offsets.  Phase 4a only looks 7 bars ahead, Phase 4b 15 bars after the
    # rally high -- clip to the windows the CURRENT code uses.
    r_off = min(gap(bo_date, rally_date), 7)
    s_off = r_off + min(max(gap(rally_date, shake_date), 1), 15)
    v_off = s_off + min(max(gap(shake_date, rev_date), 1), 15)
    bo = BO_IDX
    rally, sh, rv = bo + r_off, bo + s_off, bo + v_off
    anchor_i = bo - days_since
    if anchor_i < 0:                      # not enough pre-history, nudge bo up
        bo += -anchor_i
        rally, sh, rv = bo + r_off, bo + s_off, bo + v_off
        anchor_i = bo - days_since
    n = rv + 20

    dates = pd.bdate_range(end=rev_date + pd.Timedelta(days=40), periods=n + 5)[:n + 5]
    n = len(dates)

    # ---- prices: dry consolidation up to the breakout ----------------------
    base = np.linspace(anchor_high * 0.72, anchor_high * 0.94, bo)
    noise = np.cumsum(rng.normal(0, anchor_high * 0.004, bo))
    close = np.clip(base + noise, anchor_high * 0.55, anchor_high * 0.965)
    high = close * (1 + np.abs(rng.normal(0, 0.006, bo)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, bo)))
    open_ = close * (1 + rng.normal(0, 0.003, bo))

    high = np.minimum(high, anchor_high * PRE_ANCHOR_CAP)
    if 0 <= anchor_i < bo:
        high[anchor_i] = anchor_high
        close[anchor_i] = anchor_high * 0.98

    # ---- breakout bar ------------------------------------------------------
    # 4a needs rally_high > breakout_high*1.01, so cap the breakout high.
    bo_high = min(bo_close * 1.005, rally_high / 1.011)
    bo_low = min(bo_close * 0.985, bo_high * 0.995)
    close = np.append(close, bo_close)
    high = np.append(high, bo_high)
    low = np.append(low, bo_low)
    open_ = np.append(open_, close[bo - 1] * 0.995)

    peak = max(rally_high, shake_high)

    # ---- rally bo+1 .. rally ------------------------------------------------
    for k in range(1, r_off + 1):
        f = k / max(r_off, 1)
        c = close[bo] + (rally_high * 0.995 - close[bo]) * f
        h = c * 1.004 if k < r_off else rally_high
        close = np.append(close, c)
        high = np.append(high, max(h, c * 1.002))
        low = np.append(low, c * 0.99)
        open_ = np.append(open_, close[bo + k - 1])

    # optional higher high AFTER the 7-bar rally window (shake_high>rally_high)
    spike_at = None
    if shake_high > rally_high * 1.001:
        spike_at = rally + 1

    # ---- decline to the shakeout low ---------------------------------------
    start = len(close) - 1
    ndec = sh - start
    for k in range(1, ndec + 1):
        f = k / ndec
        c = close[start] + (shake_low * 1.004 - close[start]) * f
        if spike_at is not None and (start + k) == spike_at:
            high = np.append(high, shake_high)
            c = max(c, shake_high * 0.99)
        else:
            high = np.append(high, c * 1.006)
        close = np.append(close, c)
        low = np.append(low, c * 0.99)
        open_ = np.append(open_, close[start + k - 1])

    # the shakeout bar itself
    low[sh] = shake_low
    high[sh] = max(high[sh], shake_low * 1.012)
    close[sh] = shake_low * 1.006
    open_[sh] = shake_low * 1.010

    # keep every decline-bar low above the shakeout low (it is the window low)
    for k in range(start + 1, sh):
        low[k] = max(low[k], shake_low * 1.004)

    # ---- recovery + reversal bar -------------------------------------------
    for k in range(sh + 1, rv):
        c = close[k - 1] * 1.008
        close = np.append(close, c)
        high = np.append(high, c * 1.01)
        low = np.append(low, max(c * 0.99, shake_low * 1.004))
        open_ = np.append(open_, close[k - 1])

    close = np.append(close, entry)
    high = np.append(high, entry * 1.004)
    low = np.append(low, close[rv - 1] * 0.995)
    open_ = np.append(open_, close[rv - 1] * 0.99)   # bullish: close > open

    # ---- quiet drift after the reversal -------------------------------------
    for _ in range(rv + 1, n):
        c = close[-1] * (1 + rng.normal(0, 0.006))
        close = np.append(close, c)
        high = np.append(high, c * 1.008)
        low = np.append(low, min(c * 0.992, close[rv] * 0.98))
        open_ = np.append(open_, close[-2])

    # ---- volumes -------------------------------------------------------------
    base_vol = 1_000_000.0
    vol = np.exp(rng.normal(np.log(base_vol), 0.35, n))
    dry = rng.random(n) < 0.28
    vol[dry] *= 0.28

    carved = {bo: float(row["vol_break"]), sh: float(row["shake_low_vol"]),
              rv: max(float(row["entry_vol"]), 0.61)}
    for idx in sorted(carved):
        vol[idx] = _vol_for_ratio(vol[:idx], carved[idx])

    if variant == "noisy":
        # realistic variant: the decline also contains low-volume bars (the
        # CNL pattern: vol 0.08, 0.07, ...).  Their lows stay above the shake
        # low, so the shake bar remains the window low -- same as real data.
        for k in range(rally + 1, sh):
            if rng.random() < 0.5:
                vol[k] = _vol_for_ratio(vol[:k], float(rng.uniform(0.35, 0.9)))
    else:
        # "clean" variant: decline bars are all >1.0x so volume can never be
        # the reason an alert is missed.
        for k in range(rally + 1, sh):
            if k not in carved:
                vol[k] = _vol_for_ratio(vol[:k], float(rng.uniform(1.15, 1.9)))
    for k in range(sh + 1, rv):
        vol[k] = _vol_for_ratio(vol[:k], float(rng.uniform(0.7, 1.4)))

    df = pd.DataFrame({
        "Date": dates[:n], "Open": open_, "High": high, "Low": low,
        "Close": close, "Volume": vol,
    })
    meta = {"bo": bo, "rally": rally, "shake": sh, "reversal": rv,
            "bo_date": df.loc[bo, "Date"], "shake_date": df.loc[sh, "Date"]}
    return df, meta


# ---------------------------------------------------------------------------
# 2. Why did/didn't it fire?  (mirror of _is_phase4b_shakeout_today, with
#    per-condition bookkeeping -- test/diagnostic code only)
# ---------------------------------------------------------------------------
def diagnose(df, pending, today_idx, wick_tol=0.02, drop_min=4, drop_max=25):
    """Return (fired, reason) replicating _is_phase4b_shakeout_today()."""
    if not pending:
        return False, "no_pending_breakout"
    row = df.loc[today_idx]
    tv = row.get("VolRatio")
    if pd.isna(tv) or tv >= 1.0:
        return False, "volume_not_low"

    reasons = []
    for bo in reversed(pending):
        bd, rd = bo.get("breakout_date"), bo.get("rally_high_date")
        if not bd or not rd:
            continue
        bm = df.index[df["Date"] == bd]
        rm = df.index[df["Date"] == rd]
        if len(bm) == 0 or len(rm) == 0:
            continue
        bo_idx, rally_idx = bm[-1], rm[-1]

        if not (rally_idx < today_idx <= rally_idx + 15):
            reasons.append("outside_4b_window")
            continue
        shake_high = df.loc[bo_idx:today_idx, "High"].max()
        today_low = float(row["Low"])
        drop = (shake_high - today_low) / shake_high * 100.0
        if drop < drop_min or drop > drop_max:
            reasons.append(f"drop_out_of_band({drop:.1f}%)")
            continue
        zone_top = float(bo["breakout_close"])
        zone_floor = float(bo["anchor_high"]) * (1.0 - wick_tol)
        if today_low > zone_top:
            reasons.append("level_not_reached")
            continue
        if today_low < zone_floor:
            reasons.append("level_broken_breakdown")
            continue
        win_low = df.loc[rally_idx + 1:today_idx, "Low"].min()
        if today_low > win_low * 1.005 and str(bo.get("shake_low_date")) != str(row["Date"]):
            reasons.append("not_new_low")
            continue
        return True, "fired"
    if not reasons:
        return False, "pending_but_no_match"
    # most common reason wins
    return False, pd.Series(reasons).mode().iat[0]


# ---------------------------------------------------------------------------
# 3. Replay one reconstructed chart
# ---------------------------------------------------------------------------
def replay_one(args):
    idx, row, variant, window = args
    df, meta = build_bars(row, seed=1000 + idx, variant=variant)
    sh = meta["shake"]

    fired_dates, first_fire = [], None
    lo = max(meta["bo"], sh - 3)
    hi = min(len(df) - 1, sh + window)
    for t in range(lo, hi + 1):
        res = check_today_events(df.iloc[:t + 1])
        st = res.get("shakeout_today")
        if st:
            d = str(st["shake_low_date"])[:10]
            fired_dates.append(d)
            if first_fire is None:
                first_fire = d
        if t == sh:
            shake_day_res = res

    # diagnose the shakeout day itself
    dft = prepare_df(df.iloc[:sh + 1])
    pend = detect_pending_breakouts(dft, lookback_days=60)
    fired_on_day, reason = diagnose(dft, pend, len(dft) - 1)

    # --- sanity check on the reconstruction itself -------------------------
    # The FULL chart must reproduce the very trade we carved into it
    # (same breakout date + same shakeout low), otherwise the replay tells us
    # nothing about that symbol.
    full = scan_5phase(prepare_df(df))
    recon_valid = any(
        str(t["breakout_date"])[:10] == str(meta["bo_date"])[:10]
        and abs(float(t["shake_low"]) - float(row["shake_low"])) < 0.02
        for t in full
    )
    bo_ok = _check_breakout_confirmed(prepare_df(df), meta["bo"],
                                      len(df)) is not None

    shake_date = str(meta["shake_date"])[:10]
    return {
        "recon_valid": bool(recon_valid),
        "phase4a_ok": bool(bo_ok),
        "ticker": row["ticker"],
        "shake_date": shake_date,
        "drop_pct": float(row["drop_pct"]),
        "shake_low": float(row["shake_low"]),
        "anchor_high": float(row["anchor_high"]),
        "breakout_close": float(row["breakout_close"]),
        "zone": [round(float(row["anchor_high"]) * 0.98, 2), float(row["breakout_close"])],
        "fired_on_shake_day": bool(fired_on_day),
        "alert_fired_somewhere": len(fired_dates) > 0,
        "first_fire_date": first_fire,
        "alert_days": fired_dates,
        "reason": reason,
        "pending_status": (pend[0]["status"] if pend else None),
    }


# ---------------------------------------------------------------------------
# 4. Real-bars mode
# ---------------------------------------------------------------------------
def replay_real_bars(bars_dir, limit, window):
    rows = []
    files = sorted(f for f in os.listdir(bars_dir) if f.lower().endswith(".csv"))
    if limit:
        files = files[:limit]
    for f in files:
        path = os.path.join(bars_dir, f)
        try:
            df = pd.read_csv(path)
            df.columns = [c.strip().title() for c in df.columns]
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.sort_values("Date").reset_index(drop=True)
        except Exception as e:
            print(f"  skip {f}: {e}")
            continue
        if len(df) < 260:
            continue
        hist = scan_5phase(prepare_df(df))
        hist_shakes = {str(t["shake_low_date"])[:10] for t in hist}
        fired = []
        start = max(260, len(df) - window)
        for t in range(start, len(df)):
            res = check_today_events(df.iloc[:t + 1])
            if res.get("shakeout_today"):
                fired.append(str(res["shakeout_today"]["shake_low_date"])[:10])
        rows.append({
            "symbol": os.path.splitext(f)[0],
            "bars": len(df),
            "historical_shakeouts": len(hist_shakes),
            "alert_days": fired,
            "alert_count": len(fired),
            "matched_historical": sorted(set(fired) & hist_shakes),
        })
        print(f"  {os.path.splitext(f)[0]:>15} bars={len(df):4d} "
              f"hist_shakeouts={len(hist_shakes):2d} alerts={len(fired)}")
    return rows


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["reconstruct", "bars"], default="reconstruct")
    ap.add_argument("--bars-dir", default=None, help="dir of SYMBOL.csv OHLCV files")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--window", type=int, default=4,
                    help="days after the shakeout day to keep replaying")
    ap.add_argument("--variant", choices=["clean", "noisy"], default="clean")
    ap.add_argument("--out", default=None, help="write per-trade JSON here")
    args = ap.parse_args()

    print("=" * 78)
    print("SHAKEOUT / PULLBACK ALERT -- replay verification")
    print(f"mode={args.mode} variant={args.variant} limit={args.limit}")
    print("=" * 78)

    if args.mode == "bars":
        if not args.bars_dir:
            ap.error("--bars-dir required in bars mode")
        rows = replay_real_bars(args.bars_dir, args.limit, args.window or 60)
        total = sum(r["alert_count"] for r in rows)
        print(f"\nsymbols={len(rows)} total alerts fired={total}")
        for r in rows:
            if r["alert_count"]:
                print(f"  {r['symbol']}: {r['alert_days']}")
        if args.out:
            with open(args.out, "w") as fh:
                json.dump(rows, fh, indent=2)
        return 0 if total else 1

    trades = pd.read_csv(SAMPLE_CSV)
    if args.limit and args.limit < len(trades):
        trades = trades.sample(args.limit, random_state=7).reset_index(drop=True)
    print(f"replaying {len(trades)} real backtest shakeouts "
          f"(out of 1291) ... this takes a few minutes\n")

    jobs = [(i, trades.iloc[i], args.variant, args.window) for i in range(len(trades))]
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(replay_one, jobs, chunksize=4))
    else:
        results = [replay_one(j) for j in jobs]

    valid = [r for r in results if r["recon_valid"]]
    print(f"reconstruction fidelity: {len(valid)}/{len(results)} charts "
          f"reproduce the carved 5-phase trade "
          f"(4a confirmed on {sum(r['phase4a_ok'] for r in results)}), "
          f"analysis below uses all charts\n")
    fired = [r for r in results if r["fired_on_shake_day"]]
    anywhere = [r for r in results if r["alert_fired_somewhere"]]

    if valid:
        vf = [r for r in valid if r["fired_on_shake_day"]]
        print(f"RECALL on valid reconstructions only: {len(vf)}/{len(valid)} "
              f"= {len(vf)/len(valid):.1%}")
    print("-" * 78)
    print(f"RECALL ON THE REAL SHAKEOUT DAY : {len(fired)}/{len(results)} "
          f"= {len(fired)/max(len(results),1):.1%}")
    print(f"alert fires at all in the window: {len(anywhere)}/{len(results)} "
          f"= {len(anywhere)/max(len(results),1):.1%}")
    print("-" * 78)

    reasons = pd.Series([r["reason"] for r in results if not r["fired_on_shake_day"]])
    if len(reasons):
        print("Why the alert did NOT fire on the shakeout day:")
        for reason, cnt in reasons.value_counts().items():
            print(f"  {cnt:5d}  ({cnt/len(results):5.1%})  {reason}")
    print()

    fp = [r for r in results if r["alert_fired_somewhere"] and not r["fired_on_shake_day"]]
    print(f"Fired only on a day OTHER than the shakeout day: {len(fp)}")
    for r in fp[:10]:
        if r["first_fire_date"]:
            print(f"  {r['ticker']:<15} shake day {r['shake_date']} -> "
                  f"alerted {r['first_fire_date']}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(results, fh, indent=2, default=str)
        print(f"\nper-trade detail -> {args.out}")

    print()
    if not fired and not anywhere:
        print("VERDICT: the shakeout alert NEVER fired -> it is effectively DEAD.")
        return 1
    print(f"VERDICT: alert fires on {len(fired)/max(len(results),1):.1%} of genuine "
          f"backtest shakeouts. See the reason breakdown above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
