"""
Daily Scanner — Exact same logic as FINAL_CORRECTED_V8_SYMBOL_REVERSAL_BREAKOUT.csv (1291 trades)
Watchlist: breakout last 30d waiting (reversal>today), if none then 60d, first verify
If no waiting, show Recent Reversals Fired (last 30d) as separate section as per user request
Dual API: Dhan primary, yfinance fallback

FIX (2026-08-11): "keeps sending old data" bug
================================================
Root cause: in GitHub Actions ALL live data sources failed (Dhan creds were never
injected into the workflow env, Dhan segment enum was wrong ('NSE' vs 'NSE_EQ'),
and yfinance 0.2.44 is broken against Yahoo's 2025+ API). The scanner then silently
fell back to the static sample_1291_trades.csv backtest snapshot and sent those OLD
trades to Telegram as if they were live.

Changes:
- Dhan: exchange_segment='NSE_EQ' (required enum), more tolerant response parsing
- yfinance: upgraded requirement (>=0.2.54), shared session, 3 retries w/ backoff
- Freshness guard: a symbol's data is only used if its last date is <= 1 trading
  day behind the expected IST trading day; stale symbols are counted, not scanned
- Coverage guard: if <50% of the universe fetched live, do NOT send cached data.
  Instead send an explicit DATA SOURCE FAILURE alert.
- Static sample_1291_trades.csv is only used if ALLOW_STALE_FALLBACK=1, and is then
  clearly labelled as STALE CACHE (never presented as today's live data).
- "Recent Reversals Fired" now comes from the LIVE scan when available.
- Header/report dates are computed in IST (Asia/Kolkata), not UTC.
"""

import os, sys, time, pandas as pd, csv, io, requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from scanner import scan_5phase, prepare_df, check_today_events, get_watchlist
from telegram_helper import send_telegram_message, format_breakout_alert, format_reversal_alert, format_watchlist, format_recent_reversals_fired

IST = ZoneInfo("Asia/Kolkata")

try:
    from dhanhq import DhanContext, dhanhq
    DHAN_AVAILABLE = True
except ImportError:
    DHAN_AVAILABLE = False

UNIVERSE_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
LOOKBACK_YEARS = 2
SECURITY_MAP = {}
SECURITY_MAP_FILE = "dhan_security_map.json"

# ---------------------------------------------------------------------------
# Time helpers (IST)
# ---------------------------------------------------------------------------
def ist_now():
    return datetime.now(IST)

def ist_today():
    return ist_now().date()

def expected_trading_day():
    """Latest Mon-Fri IST date (weekends have no NSE session)."""
    d = ist_today()
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d

def trading_days_between(d1, d2):
    """Number of Mon-Fri days between d1 (older) and d2 (newer)."""
    n = 0
    d = d1
    while d < d2:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n

def is_fresh(df, tolerance_trading_days=1):
    """True if the last row of df is not older than `tolerance` trading days
    vs the expected IST trading day. Prevents stale data being treated as live."""
    if df is None or df.empty:
        return False
    last = pd.to_datetime(df.iloc[-1]['Date']).date()
    return trading_days_between(last, expected_trading_day()) <= tolerance_trading_days

def last_data_date(df):
    if df is None or df.empty:
        return None
    return pd.to_datetime(df.iloc[-1]['Date']).date()

# ---------------------------------------------------------------------------
# Dhan (primary)
# ---------------------------------------------------------------------------
def load_security_map():
    global SECURITY_MAP
    if SECURITY_MAP:
        return SECURITY_MAP
    import json
    if os.path.exists(SECURITY_MAP_FILE):
        try:
            with open(SECURITY_MAP_FILE, 'r') as f:
                SECURITY_MAP = json.load(f)
                return SECURITY_MAP
        except:
            pass
    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token or not DHAN_AVAILABLE:
        return {}
    try:
        context = DhanContext(client_id, access_token)
        dhan = dhanhq(context)
        resp = dhan.fetch_security_list("compact")
        data_list = []
        if isinstance(resp, dict) and 'data' in resp:
            data_list = resp['data'].get('NSE_EQ', []) if isinstance(resp['data'], dict) else resp['data']
        elif isinstance(resp, list):
            data_list = resp
        for item in data_list:
            try:
                ts = item.get('SEM_TRADING_SYMBOL') or item.get('trading_symbol')
                sid = item.get('SEM_SMST_SECURITY_ID') or item.get('security_id')
                if ts and sid:
                    SECURITY_MAP[ts] = str(sid)
            except:
                continue
        with open(SECURITY_MAP_FILE, 'w') as f:
            json.dump(SECURITY_MAP, f)
        return SECURITY_MAP
    except Exception as e:
        print(f"Map fetch fail {e}")
        return {}

def fetch_dhan_history(symbol, from_date, to_date):
    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token or not DHAN_AVAILABLE:
        return None
    try:
        sec_map = load_security_map()
        sec_id = sec_map.get(symbol)
        if not sec_id:
            for k, v in sec_map.items():
                if k.upper() == symbol.upper():
                    sec_id = v
                    break
        if not sec_id:
            return None
        context = DhanContext(client_id, access_token)
        dhan = dhanhq(context)
        try:
            # Dhan API enum is 'NSE_EQ' (NOT 'NSE') - 'NSE' gets rejected
            resp = dhan.historical_daily_data(security_id=sec_id, exchange_segment='NSE_EQ',
                                              instrument_type='EQUITY',
                                              from_date=from_date.strftime('%Y-%m-%d'),
                                              to_date=to_date.strftime('%Y-%m-%d'))
        except TypeError:
            resp = dhan.historical_daily_data(symbol, 'NSE_EQ', 'EQUITY', 0,
                                              from_date.strftime('%Y-%m-%d'),
                                              to_date.strftime('%Y-%m-%d'))
        if not resp:
            return None
        # Dhan returns {"status": "success", "data": {"open": [], "high": [], ...}}
        if isinstance(resp, dict) and resp.get('status') == 'failure':
            return None
        df = None
        if isinstance(resp, dict):
            d = resp.get('data', resp)
            if isinstance(d, dict) and 'open' in d:
                df = pd.DataFrame({'Open': d.get('open', []), 'High': d.get('high', []),
                                   'Low': d.get('low', []), 'Close': d.get('close', []),
                                   'Volume': d.get('volume', []),
                                   'Date': d.get('startTime') or d.get('timestamp') or d.get('date') or []})
            elif isinstance(d, list):
                df = pd.DataFrame(d)
        elif isinstance(resp, list):
            df = pd.DataFrame(resp)
        if df is None or df.empty:
            return None
        rename_map = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close',
                      'volume': 'Volume', 'startTime': 'Date', 'timestamp': 'Date', 'date': 'Date'}
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        if 'Date' not in df.columns:
            return None
        df['Date'] = pd.to_datetime(df['Date'])
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col not in df.columns:
                return None
        return df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].copy()
    except Exception as e:
        print(f"Dhan fail {symbol}: {e}")
        return None

# ---------------------------------------------------------------------------
# yfinance (fallback) - shared session + retries
# ---------------------------------------------------------------------------
_YF_SESSION = None

def _get_yf_session():
    global _YF_SESSION
    if _YF_SESSION is None:
        import yfinance as yf
        try:
            _YF_SESSION = yf.utils.get_session()   # handles Yahoo cookie/crumb
        except Exception:
            _YF_SESSION = requests.Session()
            _YF_SESSION.headers['User-Agent'] = 'Mozilla/5.0'
    return _YF_SESSION

def fetch_yfinance_history(symbol, from_date, to_date):
    try:
        import yfinance as yf
        session = _get_yf_session()
        t = yf.Ticker(f"{symbol}.NS", session=session)
        hist = None
        for attempt in range(3):  # retry w/ backoff - GH Actions IPs get 429s
            try:
                hist = t.history(start=from_date.strftime('%Y-%m-%d'),
                                 end=(to_date + timedelta(days=1)).strftime('%Y-%m-%d'),
                                 auto_adjust=True)
                if hist is not None and not hist.empty:
                    break
            except Exception as e:
                print(f"  yf retry {attempt + 1}/3 {symbol}: {e}")
                time.sleep(2 * (attempt + 1))
        if hist is None or hist.empty or len(hist) < 150:
            return None
        hist = hist.reset_index()
        hist['Date'] = pd.to_datetime(hist['Date']).dt.tz_localize(None)
        return hist[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].copy()
    except Exception as e:
        print(f"yfinance fail {symbol}: {e}")
        return None

def get_history_dual_api(symbol, from_date, to_date):
    df = fetch_dhan_history(symbol, from_date, to_date)
    if df is not None and not df.empty and len(df) >= 150:
        return df
    df = fetch_yfinance_history(symbol, from_date, to_date)
    if df is not None and not df.empty:
        return df
    return None

# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
def load_nifty500_symbols():
    symbols = []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(UNIVERSE_CSV_URL, headers=headers, timeout=15)
        if resp.status_code == 200:
            reader = csv.DictReader(io.StringIO(resp.text))
            for row in reader:
                sym = row.get('Symbol') or row.get('SYMBOL')
                if sym:
                    symbols.append(sym.strip())
            if symbols:
                return symbols
    except Exception as e:
        print(f"Download fail {e}")
    try:
        with open("/tmp/equity_l.csv") as f:
            r = csv.DictReader(f)
            eq = []
            for row in r:
                if row.get('SERIES') == 'EQ' or row.get(' SERIES') == 'EQ':
                    eq.append(row['SYMBOL'])
                if len(eq) >= 500:
                    break
            return eq
    except:
        pass
    return ["RELIANCE", "TCS", "INFY"]

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_daily_scan():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    symbols = load_nifty500_symbols()
    max_syms = os.getenv("MAX_SYMBOLS")  # optional test limit
    if max_syms:
        try:
            symbols = symbols[:int(max_syms)]
        except:
            pass
    print(f"Scanning {len(symbols)}")

    breakout_today = []
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
                if idx % 50 == 0:
                    print(f"[{idx}/{len(symbols)}] {sym} no data")
                continue
            # Freshness guard: only treat data as live if it's not stale
            if not is_fresh(df):
                stale_count += 1
                if idx % 50 == 0:
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
            if idx % 50 == 0:
                print(f"[{idx}] {sym}.NS B/O today:{len(breakout_today)} Rev today:{len(reversal_today)} Watch30:{len(watchlist_30)} Watch60:{len(watchlist_60)}")
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
    reversal_today = dedup(reversal_today)

    final_watchlist = watchlist_30
    window_used = 30
    if not final_watchlist:
        print("No breakout in last 30 days waiting reversal - verifying and expanding to 60 days")
        final_watchlist = watchlist_60
        window_used = 60

    # Live "recent reversals fired" (last 30d) from the live scan itself
    recent_reversals_fired = []
    today_live = latest_data_date or expected_trading_day()
    for tr in all_live_trades:
        try:
            rd = tr['reversal_date'].date() if hasattr(tr['reversal_date'], 'date') else pd.to_datetime(tr['reversal_date']).date()
            delta = (today_live - rd).days
            if 0 <= delta <= 30:
                recent_reversals_fired.append(tr)
        except:
            continue

    today_str = ist_now().strftime('%Y-%m-%d')
    coverage = live_ok / len(symbols) if symbols else 0
    print(f"LIVE DATA: {live_ok}/{len(symbols)} symbols fetched fresh, {stale_count} stale, latest data date {latest_data_date}")
    print(f"Coverage: {coverage:.0%} (need >=50% to send live report)")

    header = (
        f"📊 *5-Phase Scanner Daily Report* {today_str} IST (Nifty500, 1291-trade logic)\n"
        f"Universe: {len(symbols)} | Live data: {live_ok}/{len(symbols)} | Stale: {stale_count} | Data date: {latest_data_date or 'N/A'}\n"
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
        # Still save CSVs so the run has artifacts
        pd.DataFrame(breakout_today).to_csv(f"breakouts_today_{today_str}.csv", index=False)
        pd.DataFrame(reversal_today).to_csv(f"reversals_today_{today_str}.csv", index=False)
        pd.DataFrame(final_watchlist).to_csv(f"daily_watchlist_{today_str}.csv", index=False)
        print("Daily scan done (coverage guard - no live data)")
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

    if reversal_today:
        for tr in reversal_today:
            msg = format_reversal_alert(tr, tr['ticker'])
            print(msg)
            send_telegram_message(bot_token, chat_id, msg)
    else:
        print(f"✅ No reversal entries today {today_str}")

    pd.DataFrame(final_watchlist).to_csv(f"daily_watchlist_{today_str}.csv", index=False)
    pd.DataFrame(recent_reversals_fired).to_csv(f"recent_reversals_fired_{today_str}.csv", index=False)
    pd.DataFrame(breakout_today).to_csv(f"breakouts_today_{today_str}.csv", index=False)
    pd.DataFrame(reversal_today).to_csv(f"reversals_today_{today_str}.csv", index=False)
    print("Daily scan done")


if __name__ == "__main__":
    run_daily_scan()
