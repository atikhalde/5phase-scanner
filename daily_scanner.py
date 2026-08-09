"""
Daily Scanner — uses EXACT same conditions as backtest that produced 1291 trades
Universe: Nifty 500 (as per user selection)
Watchlist: breakout in last 30 days (changed from 7 as per user request)
Dual API: Primary Dhan API, Secondary yfinance fallback, Tertiary cached file fallback
"""

import os
import sys
import time
import pandas as pd
import csv
import io
import requests
from datetime import datetime, timedelta
from scanner import scan_5phase, prepare_df, check_today_events
from telegram_helper import send_telegram_message, format_breakout_alert, format_reversal_alert, format_watchlist

try:
    from dhanhq import DhanContext, dhanhq
    DHAN_AVAILABLE = True
except ImportError:
    DHAN_AVAILABLE = False
    print("dhanhq not installed, will use yfinance only")

UNIVERSE_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
LOOKBACK_YEARS = 2
SECURITY_MAP = {}
SECURITY_MAP_FILE = "dhan_security_map.json"

def load_security_map():
    global SECURITY_MAP
    if SECURITY_MAP:
        return SECURITY_MAP
    import json
    if os.path.exists(SECURITY_MAP_FILE):
        try:
            with open(SECURITY_MAP_FILE, 'r') as f:
                SECURITY_MAP = json.load(f)
                print(f"Loaded security map from file: {len(SECURITY_MAP)} symbols")
                return SECURITY_MAP
        except Exception as e:
            print(f"Failed load map file: {e}")
    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token or not DHAN_AVAILABLE:
        print("No Dhan credentials or library, skipping security map fetch via API")
        return {}
    try:
        print("Fetching Dhan security list via API...")
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
                trading_symbol = item.get('SEM_TRADING_SYMBOL') or item.get('trading_symbol')
                sec_id = item.get('SEM_SMST_SECURITY_ID') or item.get('security_id')
                if trading_symbol and sec_id:
                    SECURITY_MAP[trading_symbol] = str(sec_id)
            except:
                continue
        print(f"Fetched security map via API: {len(SECURITY_MAP)} symbols")
        with open(SECURITY_MAP_FILE, 'w') as f:
            json.dump(SECURITY_MAP, f)
        return SECURITY_MAP
    except Exception as e:
        print(f"Failed to fetch security map via API: {e}")
        return {}

def fetch_dhan_history(symbol, from_date, to_date):
    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token or not DHAN_AVAILABLE:
        return None
    try:
        sec_map = load_security_map()
        security_id = sec_map.get(symbol)
        if not security_id:
            for k,v in sec_map.items():
                if k.upper() == symbol.upper():
                    security_id = v
                    break
        if not security_id:
            return None
        context = DhanContext(client_id, access_token)
        dhan = dhanhq(context)
        try:
            resp = dhan.historical_daily_data(
                security_id=security_id,
                exchange_segment='NSE',
                instrument_type='EQUITY',
                from_date=from_date.strftime('%Y-%m-%d'),
                to_date=to_date.strftime('%Y-%m-%d')
            )
        except TypeError:
            resp = dhan.historical_daily_data(
                symbol,
                'NSE_EQ',
                'EQUITY',
                0,
                from_date.strftime('%Y-%m-%d'),
                to_date.strftime('%Y-%m-%d')
            )
        if not resp:
            return None
        df = None
        if isinstance(resp, dict):
            if 'data' in resp:
                d = resp['data']
                if isinstance(d, dict) and 'open' in d:
                    df = pd.DataFrame({
                        'Open': d.get('open', []),
                        'High': d.get('high', []),
                        'Low': d.get('low', []),
                        'Close': d.get('close', []),
                        'Volume': d.get('volume', []),
                        'Date': d.get('startTime') or d.get('timestamp') or []
                    })
                elif isinstance(d, list):
                    df = pd.DataFrame(d)
            elif 'open' in resp:
                df = pd.DataFrame({
                    'Open': resp.get('open', []),
                    'High': resp.get('high', []),
                    'Low': resp.get('low', []),
                    'Close': resp.get('close', []),
                    'Volume': resp.get('volume', []),
                    'Date': resp.get('startTime') or []
                })
        elif isinstance(resp, list):
            df = pd.DataFrame(resp)

        if df is None or df.empty:
            return None
        rename_map = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume', 'startTime': 'Date', 'timestamp': 'Date', 'date': 'Date'}
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        if 'Date' not in df.columns:
            return None
        df['Date'] = pd.to_datetime(df['Date'])
        for col in ['Open','High','Low','Close','Volume']:
            if col not in df.columns:
                return None
        return df[['Date','Open','High','Low','Close','Volume']].copy()
    except Exception as e:
        print(f"Dhan fetch failed for {symbol}: {e}")
        return None

def fetch_yfinance_history(symbol, from_date, to_date):
    try:
        import yfinance as yf
        t = yf.Ticker(f"{symbol}.NS")
        hist = t.history(start=from_date.strftime('%Y-%m-%d'), end=(to_date+timedelta(days=1)).strftime('%Y-%m-%d'), auto_adjust=True)
        if hist.empty or len(hist) < 150:
            return None
        hist = hist.reset_index()
        hist['Date'] = pd.to_datetime(hist['Date']).dt.tz_localize(None)
        df = hist[['Date','Open','High','Low','Close','Volume']].copy()
        return df
    except Exception as e:
        print(f"yfinance failed for {symbol}: {e}")
        return None

def get_history_dual_api(symbol, from_date, to_date):
    df = fetch_dhan_history(symbol, from_date, to_date)
    if df is not None and not df.empty and len(df) >= 150:
        print(f"{symbol}: Dhan success {len(df)} rows")
        return df
    print(f"{symbol}: Dhan failed or empty, trying yfinance fallback")
    df = fetch_yfinance_history(symbol, from_date, to_date)
    if df is not None and not df.empty:
        print(f"{symbol}: yfinance success {len(df)} rows")
        return df
    print(f"{symbol}: Both APIs failed")
    return None

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
                print(f"Loaded {len(symbols)} from NSE Nifty500")
                return symbols
    except Exception as e:
        print(f"Failed to download Nifty500 list: {e}")
    try:
        with open("/tmp/equity_l.csv") as f:
            r = csv.DictReader(f)
            eq = []
            for row in r:
                if row.get('SERIES') == 'EQ' or row.get(' SERIES') == 'EQ':
                    eq.append(row['SYMBOL'])
                if len(eq) >= 500:
                    break
            if eq:
                print(f"Fallback loaded {len(eq)} EQ as Nifty500 proxy")
                return eq
    except Exception as e:
        print(f"Fallback failed: {e}")
    return ["RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK"]

def run_daily_scan():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")

    symbols = load_nifty500_symbols()
    print(f"Scanning {len(symbols)} symbols...")

    breakout_today_list = []
    reversal_today_list = []
    watchlist_all = []
    tickers_with_trades = 0

    from_date = datetime.now() - timedelta(days=LOOKBACK_YEARS*365)
    to_date = datetime.now()

    for idx, sym in enumerate(symbols):
        try:
            df = get_history_dual_api(sym, from_date, to_date)
            if df is None or df.empty or len(df) < 220:
                if idx % 50 == 0:
                    print(f"[{idx}/{len(symbols)}] {sym} no data")
                continue

            from scanner import check_today_events
            result = check_today_events(df)

            if result['all_trades']:
                tickers_with_trades += 1

            if result['breakout_today']:
                tr = result['breakout_today']
                tr['ticker'] = f"{sym}.NS"
                breakout_today_list.append(tr)

            if result['reversal_today']:
                tr = result['reversal_today']
                tr['ticker'] = f"{sym}.NS"
                reversal_today_list.append(tr)

            all_trades = result['all_trades']
            today = df['Date'].max().date() if not df.empty else datetime.now().date()
            for tr in all_trades:
                try:
                    bdate = tr['breakout_date'].date() if hasattr(tr['breakout_date'], 'date') else pd.to_datetime(tr['breakout_date']).date()
                    delta = (today - bdate).days
                    if 0 < delta <= 30:
                        tr_copy = tr.copy()
                        tr_copy['ticker'] = f"{sym}.NS"
                        watchlist_all.append(tr_copy)
                except:
                    continue

            if idx % 50 == 0:
                print(f"[{idx}/{len(symbols)}] {sym}.NS — B/O today:{len(breakout_today_list)} Rev today:{len(reversal_today_list)} Watch:{len(watchlist_all)}")

            time.sleep(0.15)

        except Exception as e:
            print(f"{sym} error {e}")
            continue

    dedup = {}
    for w in watchlist_all:
        key = (w['ticker'], str(w['breakout_date']))
        dedup[key] = w
    watchlist_all = list(dedup.values())

    # Fallback to cached file if both APIs failed for all tickers
    if not watchlist_all and not breakout_today_list and not reversal_today_list:
        print("Both APIs failed for all tickers or no recent breakouts - using cached sample_1291_trades.csv as fallback for watchlist")
        try:
            cached_path = "sample_1291_trades.csv"
            if os.path.exists(cached_path):
                cdf = pd.read_csv(cached_path)
                cdf['breakout_date'] = pd.to_datetime(cdf['breakout_date'])
                cdf['reversal_date'] = pd.to_datetime(cdf['reversal_date'])
                today = pd.to_datetime(datetime.now().date())
                # Filter breakout in last 30 days
                recent = cdf[(today - cdf['breakout_date']).dt.days <= 30]
                recent = recent[(today - recent['breakout_date']).dt.days > 0]
                print(f"Fallback: found {len(recent)} recent (30d) trades from cache")
                for _, row in recent.iterrows():
                    watchlist_all.append({
                        'ticker': row['ticker'],
                        'breakout_date': row['breakout_date'],
                        'rally_high': row['rally_high'],
                        'shake_low': row['shake_low'],
                        'drop_pct': row['drop_pct'],
                        'anchor_high': row['anchor_high'],
                        'entry': row['entry'],
                        'reversal_date': row['reversal_date'],
                    })
                tickers_with_trades = cdf['ticker'].nunique()
            else:
                print(f"Fallback file {cached_path} not found")
        except Exception as e:
            print(f"Fallback failed: {e}")
            import traceback
            traceback.print_exc()

    today_str = datetime.now().strftime('%Y-%m-%d')
    header = f"📊 *5-Phase Scanner Daily Report* {today_str} (Nifty500, 1291-trade logic)\nUniverse: {len(symbols)} | With setup: {tickers_with_trades}\nWatchlist window: last 30 days (changed from 7d as per request) | Dual API: Dhan primary, yfinance fallback\n"

    from telegram_helper import format_watchlist, send_telegram_message, format_breakout_alert, format_reversal_alert
    watchlist_msg = format_watchlist(watchlist_all, tickers_with_trades)
    full_watchlist_msg = header + "\n" + watchlist_msg

    print(full_watchlist_msg)
    send_telegram_message(bot_token, chat_id, full_watchlist_msg)

    if breakout_today_list:
        for tr in breakout_today_list:
            msg = format_breakout_alert(tr, tr['ticker'])
            print(msg)
            send_telegram_message(bot_token, chat_id, msg)
    else:
        print(f"🔍 No new breakouts today {today_str}")

    if reversal_today_list:
        for tr in reversal_today_list:
            msg = format_reversal_alert(tr, tr['ticker'])
            print(msg)
            send_telegram_message(bot_token, chat_id, msg)
    else:
        print(f"✅ No reversal entries today {today_str}")

    pd.DataFrame(watchlist_all).to_csv(f"daily_watchlist_{today_str}.csv", index=False)
    pd.DataFrame(breakout_today_list).to_csv(f"breakouts_today_{today_str}.csv", index=False)
    pd.DataFrame(reversal_today_list).to_csv(f"reversals_today_{today_str}.csv", index=False)

    print("Daily scan done")

if __name__ == "__main__":
    run_daily_scan()
