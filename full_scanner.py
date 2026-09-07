"""
Full-Universe Daily Scanner — same 5-phase logic, ALL NSE EQ (~2000 symbols)
============================================================================

This is a NEW, standalone scanner that runs the *identical* 5-phase pattern
logic as `daily_scanner.py`, but over the FULL NSE cash-equity universe
(SERIES == 'EQ' in NSE's EQUITY_L.csv, ~2000 symbols) instead of just the
Nifty 500.

Why this exists
---------------
`daily_scanner.py` only scans the Nifty 500 (`ind_nifty500list.csv`). Stocks
outside that index — e.g. TVSSRICHAK (TVS Srichakra), which is present in
`dhan_security_map.json` but NOT in the Nifty 500 — are therefore never
fetched and can never fire a breakout / watchlist / reversal alert, even
though the historical 1291-trade backtest (run on the full ~2075 EQ universe)
did produce trades for them.

This file leaves EVERY existing file untouched:
  - It IMPORTS (does not modify) the data-fetch + freshness helpers from
    `daily_scanner.py`, the pattern engine from `scanner.py`, and the message
    formatters from `telegram_helper.py`.
  - The only new behaviour is the universe loader (`load_full_eq_symbols`),
    which pulls the full EQ list, and its own report header / CSV filenames
    (prefixed `full_*`) so its artifacts never collide with the Nifty 500
    scanner's.

Everything else — Dhan-primary / yfinance-fallback fetching, the 2-year
lookback, the freshness + coverage guards, the 30d→60d watchlist window, the
breakout / reversal / recent-reversals sections — is reused verbatim so the
two scanners stay perfectly in sync.

Env vars (same as daily_scanner.py):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   — Telegram creds (optional; prints if absent)
  DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN      — Dhan primary data source
  MAX_SYMBOLS                            — optional cap for testing (e.g. 50)
  ALLOW_STALE_FALLBACK=1                 — opt-in stale cache (default OFF)

Local test:
  export MAX_SYMBOLS=50
  python full_scanner.py
"""

import os
import time
import csv
import io
import requests
import pandas as pd

# Reuse the pattern engine unchanged.
from scanner import check_today_events

# Reuse Telegram formatting unchanged.
from telegram_helper import (
    send_telegram_message,
    format_breakout_alert,
    format_shakeout_alert,
    format_reversal_alert,
    format_watchlist,
    format_recent_reversals_fired,
)

# Reuse the data-fetch + freshness/time helpers from the existing daily
# scanner WITHOUT modifying it. (Importing daily_scanner is side-effect free:
# its run_daily_scan() only executes under `if __name__ == "__main__"`.)
from daily_scanner import (
    get_history_dual_api,
    is_fresh,
    last_data_date,
    expected_trading_day,
    ist_now,
    ist_today,
    LOOKBACK_YEARS,
)
from datetime import datetime, timedelta

# Full NSE cash-market equity list (all series; we filter SERIES == 'EQ').
FULL_EQ_CSV_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
# Local cache written on first successful download so subsequent runs / offline
# environments still have a universe to scan.
FULL_EQ_CACHE_FILE = "nse_eq_universe.csv"


# ---------------------------------------------------------------------------
# Universe: full NSE EQ (~2000 symbols)
# ---------------------------------------------------------------------------
def _parse_eq_rows(text):
    """Return list of SYMBOLs whose SERIES == 'EQ' from an EQUITY_L.csv body."""
    symbols = []
    reader = csv.DictReader(io.StringIO(text))
    # NSE's header sometimes has a leading space (' SERIES'); normalise keys.
    for row in reader:
        norm = {(k or "").strip().upper(): (v or "").strip() for k, v in row.items()}
        sym = norm.get("SYMBOL")
        series = norm.get("SERIES")
        if sym and series == "EQ":
            symbols.append(sym)
    return symbols


def load_full_eq_symbols():
    """Full NSE EQ universe.

    Order of preference:
      1. Live download of EQUITY_L.csv (also refreshes the local cache).
      2. Local cache file `nse_eq_universe.csv` (either raw EQUITY_L format or
         a one-symbol-per-line list) or /tmp/equity_l.csv.
      3. dhan_security_map.json keys (broad NSE coverage, already in repo).
      4. Nifty 500 list (last resort so the scan is never empty).
    """
    # 1) Live download.
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(FULL_EQ_CSV_URL, headers=headers, timeout=20)
        if resp.status_code == 200 and resp.text:
            symbols = _parse_eq_rows(resp.text)
            if symbols:
                try:
                    with open(FULL_EQ_CACHE_FILE, "w") as f:
                        f.write(resp.text)
                except Exception:
                    pass
                print(f"Universe: {len(symbols)} EQ symbols from live EQUITY_L.csv")
                return sorted(set(symbols))
    except Exception as e:
        print(f"Full EQ live download failed: {e}")

    # 2) Local caches.
    for path in (FULL_EQ_CACHE_FILE, "/tmp/equity_l.csv"):
        try:
            if os.path.exists(path):
                with open(path) as f:
                    body = f.read()
                symbols = _parse_eq_rows(body)
                if not symbols:
                    # maybe it's a plain one-symbol-per-line list
                    symbols = [ln.strip() for ln in body.splitlines()
                               if ln.strip() and "," not in ln and ln.strip().upper() != "SYMBOL"]
                if symbols:
                    print(f"Universe: {len(symbols)} EQ symbols from cache {path}")
                    return sorted(set(symbols))
        except Exception:
            continue

    # 3) dhan_security_map.json keys (broad NSE coverage already in the repo).
    try:
        import json
        if os.path.exists("dhan_security_map.json"):
            with open("dhan_security_map.json") as f:
                m = json.load(f)
            symbols = [k for k in m.keys() if k and k.isascii()]
            if symbols:
                print(f"Universe: {len(symbols)} symbols from dhan_security_map.json (fallback)")
                return sorted(set(symbols))
    except Exception:
        pass

    # 4) Nifty 500 (last resort).
    try:
        from daily_scanner import load_nifty500_symbols
        symbols = load_nifty500_symbols()
        print(f"Universe: {len(symbols)} symbols from Nifty500 (last-resort fallback)")
        return symbols
    except Exception:
        return ["RELIANCE", "TCS", "INFY"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_full_scan():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    symbols = load_full_eq_symbols()
    max_syms = os.getenv("MAX_SYMBOLS")  # optional test limit
    if max_syms:
        try:
            symbols = symbols[:int(max_syms)]
        except Exception:
            pass
    print(f"Scanning {len(symbols)} (FULL NSE EQ universe)")

    breakout_today = []
    shakeout_today = []
    reversal_today = []
    watchlist_all = []
    watchlist_30 = []
    watchlist_60 = []
    all_live_trades = []
    tickers_with_trades = 0
    live_ok = 0
    stale_count = 0
    latest_data_date = None
    from_date = datetime.now() - timedelta(days=LOOKBACK_YEARS * 365)
    to_date = datetime.now()

    for idx, sym in enumerate(symbols):
        try:
            df = get_history_dual_api(sym, from_date, to_date)
            if df is None or df.empty or len(df) < 220:
                if idx % 100 == 0:
                    print(f"[{idx}/{len(symbols)}] {sym} no data")
                continue
            # Freshness guard: only treat data as live if it's not stale.
            if not is_fresh(df):
                stale_count += 1
                if idx % 100 == 0:
                    print(f"[{idx}/{len(symbols)}] {sym} STALE (last date {last_data_date(df)})")
                continue
            live_ok += 1
            d = last_data_date(df)
            if latest_data_date is None or (d and d > latest_data_date):
                latest_data_date = d
            result = check_today_events(df)
            if result['all_trades']:
                tickers_with_trades += 1
                for tr in result['all_trades']:
                    tr['ticker'] = f"{sym}.NS"
                all_live_trades.extend(result['all_trades'])
            if result['breakout_today']:
                tr = result['breakout_today']
                tr['ticker'] = f"{sym}.NS"
                breakout_today.append(tr)
            if result.get('shakeout_today'):
                tr = result['shakeout_today']
                tr['ticker'] = f"{sym}.NS"
                shakeout_today.append(tr)
            if result['reversal_today']:
                tr = result['reversal_today']
                tr['ticker'] = f"{sym}.NS"
                reversal_today.append(tr)
            for w in result['watchlist']:
                w['ticker'] = f"{sym}.NS"
                watchlist_all.append(w)
            for w in result.get('watchlist_30', []):
                w['ticker'] = f"{sym}.NS"
                watchlist_30.append(w)
            for w in result.get('watchlist_60', []):
                w['ticker'] = f"{sym}.NS"
                watchlist_60.append(w)
            if idx % 100 == 0:
                print(f"[{idx}] {sym}.NS B/O today:{len(breakout_today)} "
                      f"Shake today:{len(shakeout_today)} "
                      f"Rev today:{len(reversal_today)} "
                      f"Watch30:{len(watchlist_30)} Watch60:{len(watchlist_60)}")
            time.sleep(0.15)
        except Exception as e:
            print(f"{sym} error {e}")
            continue

    def dedup(lst):
        d = {}
        for w in lst:
            key = (w['ticker'], str(w['breakout_date']))
            d[key] = w
        return list(d.values())

    watchlist_all = dedup(watchlist_all)
    watchlist_30 = dedup(watchlist_30)
    watchlist_60 = dedup(watchlist_60)
    breakout_today = dedup(breakout_today)
    shakeout_today = dedup(shakeout_today)
    reversal_today = dedup(reversal_today)

    final_watchlist = watchlist_30
    window_used = 30
    if not final_watchlist:
        print("No breakout in last 30 days waiting reversal - verifying and expanding to 60 days")
        final_watchlist = watchlist_60
        window_used = 60

    # Live "recent reversals fired" (last 30d) from the live scan itself.
    recent_reversals_fired = []
    today_live = latest_data_date or expected_trading_day()
    for tr in all_live_trades:
        try:
            rd = tr['reversal_date'].date() if hasattr(tr['reversal_date'], 'date') else pd.to_datetime(tr['reversal_date']).date()
            delta = (today_live - rd).days
            if 0 <= delta <= 30:
                recent_reversals_fired.append(tr)
        except Exception:
            continue

    today_str = ist_now().strftime('%Y-%m-%d')
    coverage = live_ok / len(symbols) if symbols else 0
    print(f"LIVE DATA: {live_ok}/{len(symbols)} symbols fetched fresh, {stale_count} stale, latest data date {latest_data_date}")
    print(f"Coverage: {coverage:.0%} (need >=50% to send live report)")

    header = (
        f"📊 *5-Phase Scanner Daily Report (FULL NSE EQ)* {today_str} IST (1291-trade logic)\n"
        f"Universe: {len(symbols)} EQ | Live data: {live_ok}/{len(symbols)} | Stale: {stale_count} | Data date: {latest_data_date or 'N/A'}\n"
        f"Today: Breakouts {len(breakout_today)} | Shakeouts {len(shakeout_today)} | Reversals {len(reversal_today)} | Watchlist {len(final_watchlist)}\n"
        f"Watchlist window: last {window_used} days (30d first verified, then 60d) | Dhan primary, yfinance fallback\n"
    )

    # ------------------------------------------------------------------
    # COVERAGE GUARD: never present cached/backtest data as live.
    # ------------------------------------------------------------------
    if coverage < 0.5:
        msg = (
            header + "\n"
            "⚠️ *DATA SOURCE FAILURE — NO LIVE REPORT*\n"
            f"Only {live_ok}/{len(symbols)} symbols returned fresh data "
            f"(stale: {stale_count}).\n"
            "No watchlist/alerts sent — old cached data is NOT shown as live.\n"
            "Check Dhan credentials (DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN in workflow env) "
            "and yfinance/yahoo availability."
        )
        print(msg)
        send_telegram_message(bot_token, chat_id, msg)
        pd.DataFrame(breakout_today).to_csv(f"full_breakouts_today_{today_str}.csv", index=False)
        pd.DataFrame(shakeout_today).to_csv(f"full_shakeouts_today_{today_str}.csv", index=False)
        pd.DataFrame(reversal_today).to_csv(f"full_reversals_today_{today_str}.csv", index=False)
        pd.DataFrame(final_watchlist).to_csv(f"full_daily_watchlist_{today_str}.csv", index=False)
        print("Full scan done (coverage guard - no live data)")
        return

    # ------------------------------------------------------------------
    # OPT-IN stale cache fallback, clearly labelled (default OFF)
    # ------------------------------------------------------------------
    stale_fallback = os.getenv("ALLOW_STALE_FALLBACK") == "1"
    if stale_fallback:
        try:
            cached_path = "sample_1291_trades.csv"
            if os.path.exists(cached_path):
                cdf = pd.read_csv(cached_path)
                cdf['breakout_date'] = pd.to_datetime(cdf['breakout_date'])
                cdf['reversal_date'] = pd.to_datetime(cdf['reversal_date'])
                today = pd.Timestamp(ist_today())
                waiting = cdf[cdf['reversal_date'] > today]
                recent_30 = waiting[(today - waiting['breakout_date']).dt.days <= 30]
                recent_30 = recent_30[(today - recent_30['breakout_date']).dt.days > 0]
                print(f"Stale-cache fallback 30d waiting: {len(recent_30)}")
                if not recent_30.empty:
                    final_watchlist = recent_30.to_dict('records')
                    window_used = 30
                else:
                    recent_60 = waiting[(today - waiting['breakout_date']).dt.days <= 60]
                    recent_60 = recent_60[(today - recent_60['breakout_date']).dt.days > 0]
                    print(f"Stale-cache fallback 60d waiting: {len(recent_60)}")
                    if not recent_60.empty:
                        final_watchlist = recent_60.to_dict('records')
                        window_used = 60
                if not recent_reversals_fired:
                    recent_fired = cdf[(today - cdf['reversal_date']).dt.days <= 30]
                    recent_fired = recent_fired[(today - recent_fired['reversal_date']).dt.days >= 0]
                    recent_reversals_fired = recent_fired.to_dict('records')
        except Exception as e:
            print(f"Stale-cache fallback failed: {e}")

    stale_note = " ⚠️ *STALE CACHE* (sample_1291_trades.csv, NOT live)" if stale_fallback else ""

    watchlist_msg = format_watchlist(final_watchlist, tickers_with_trades)
    recent_msg = format_recent_reversals_fired(recent_reversals_fired)

    full_msg = header + "\n" + watchlist_msg + "\n\n" + recent_msg + stale_note
    print(full_msg)
    send_telegram_message(bot_token, chat_id, full_msg)

    if breakout_today:
        for tr in breakout_today:
            msg = format_breakout_alert(tr, tr['ticker'])
            print(msg)
            send_telegram_message(bot_token, chat_id, msg)
    else:
        print(f"🔍 No new breakouts today {today_str}")

    if shakeout_today:
        for tr in shakeout_today:
            try:
                msg = format_shakeout_alert(tr, tr['ticker'])
                print(msg)
                send_telegram_message(bot_token, chat_id, msg)
            except Exception as e:
                print(f"shakeout alert send failed {tr.get('ticker')}: {e}")
                continue
    else:
        print(f"📉 No shakeout / pullback touches today {today_str}")

    if reversal_today:
        for tr in reversal_today:
            msg = format_reversal_alert(tr, tr['ticker'])
            print(msg)
            send_telegram_message(bot_token, chat_id, msg)
    else:
        print(f"✅ No reversal entries today {today_str}")

    pd.DataFrame(final_watchlist).to_csv(f"full_daily_watchlist_{today_str}.csv", index=False)
    pd.DataFrame(recent_reversals_fired).to_csv(f"full_recent_reversals_fired_{today_str}.csv", index=False)
    pd.DataFrame(breakout_today).to_csv(f"full_breakouts_today_{today_str}.csv", index=False)
    pd.DataFrame(shakeout_today).to_csv(f"full_shakeouts_today_{today_str}.csv", index=False)
    pd.DataFrame(reversal_today).to_csv(f"full_reversals_today_{today_str}.csv", index=False)
    print("Full scan done")


if __name__ == "__main__":
    run_full_scan()
