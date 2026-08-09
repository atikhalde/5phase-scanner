"""
Daily Scanner — uses EXACT same conditions as backtest that produced 1291 trades
Universe: Nifty 500 (as per user selection) — can switch to full EQ 2075 by changing CSV

Alerts:
- Watchlist: breakout in last 30 days (changed from 7 as per user request)
- Breakout Today: Phase3 breakout today live
- Reversal Entry Today: Phase5 reversal today live

Dual API: Primary Dhan API, Secondary yfinance fallback
Secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN
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

# Try import dhanhq
try:
    from dhanhq import DhanContext, dhanhq
    DHAN_AVAILABLE = True
except ImportError:
    DHAN_AVAILABLE = False
    print("dhanhq not installed, will use yfinance only")

UNIVERSE_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
LOOKBACK_YEARS = 2

# Global security map cache
SECURITY_MAP = {}
SECURITY_MAP_FILE = "dhan_security_map.json"

def load_security_map():
    global SECURITY_MAP
    if SECURITY_MAP:
        return SECURITY_MAP
    # Try load from file
    import json
    if os.path.exists(SECURITY_MAP_FILE):
        try:
            with open(SECURITY_MAP_FILE, 'r') as f:
                SECURITY_MAP = json.load(f)
                print(f"Loaded security map from file: {len(SECURITY_MAP)} symbols")
                return SECURITY_MAP
        except:
            pass
    # Try fetch from Dhan API if credentials available
    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token or not DHAN_AVAILABLE:
        print("No Dhan credentials or library, skipping security map fetch")
        return {}
    try:
        print("Fetching Dhan security list...")
        context = DhanContext(client_id, access_token)
        dhan = dhanhq(context)
        # compact list returns dict with data?
        resp = dhan.fetch_security_list("compact")
        # resp may be dict or list
        import json
        # Try to parse
        # Expected format: list of dicts with SEM_TRADING_SYMBOL and SEM_SMST_SECURITY_ID
        # According to docs, fetch_security_list returns list
        data_list = []
        if isinstance(resp, dict) and 'data' in resp:
            data_list = resp['data'].get('NSE_EQ', []) if isinstance(resp['data'], dict) else resp['data']
        elif isinstance(resp, list):
            data_list = resp
        else:
            # Try to get NSE_EQ
            try:
                data_list = resp.get('data', {}).get('NSE_EQ', [])
            except:
                data_list = []

        for item in data_list:
            try:
                # Different possible keys
                trading_symbol = item.get('SEM_TRADING_SYMBOL') or item.get('SEM_TRADING_SYMBOL1') or item.get('SM_SYMBOL_NAME') or item.get('trading_symbol')
                sec_id = item.get('SEM_SMST_SECURITY_ID') or item.get('SMST_SECURITY_ID') or item.get('SEM_EXM_EXCH_ID') or item.get('security_id')
                if trading_symbol and sec_id:
                    SECURITY_MAP[trading_symbol] = str(sec_id)
            except:
                continue
        print(f"Fetched security map: {len(SECURITY_MAP)} symbols")
        # Save to file
        with open(SECURITY_MAP_FILE, 'w') as f:
            json.dump(SECURITY_MAP, f)
        return SECURITY_MAP
    except Exception as e:
        print(f"Failed to fetch security map: {e}")
        return {}

def fetch_dhan_history(symbol, from_date, to_date):
    """
    Fetch via Dhan API: primary
    Returns DataFrame with Date, Open, High, Low, Close, Volume or None if fails
    """
    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token:
        return None
    if not DHAN_AVAILABLE:
        return None
    try:
        # Load map
        sec_map = load_security_map()
        security_id = sec_map.get(symbol)
        if not security_id:
            # Try with symbol itself? Some APIs accept trading symbol directly via Tradehull wrapper
            # Fallback: try to use symbol as is - Dhan may need security_id lookup via instrument
            # For now, if no mapping, return None to fallback to yfinance
            # We can attempt to search by symbol in map case-insensitive
            for k,v in sec_map.items():
                if k.upper() == symbol.upper():
                    security_id = v
                    break
        if not security_id:
            # print(f"No security_id for {symbol}, skipping Dhan")
            return None

        context = DhanContext(client_id, access_token)
        dhan = dhanhq(context)

        # Dhan historical_daily_data expects params: security_id, exchange_segment, instrument_type, expiry_code, from_date, to_date
        # For NSE EQ: exchange_segment=NSE, instrument_type=EQUITY
        # Try both new and old signature
        try:
            # New signature from docs: historical_daily_data(security_id, exchange_segment, instrument_type, from_date, to_date)
            resp = dhan.historical_daily_data(
                security_id=security_id,
                exchange_segment='NSE',
                instrument_type='EQUITY',
                from_date=from_date.strftime('%Y-%m-%d'),
                to_date=to_date.strftime('%Y-%m-%d')
            )
        except TypeError:
            # Old signature with expiry_code
            resp = dhan.historical_daily_data(
                symbol,
                'NSE_EQ',
                'EQUITY',
                0,
                from_date.strftime('%Y-%m-%d'),
                to_date.strftime('%Y-%m-%d')
            )

        # Parse response
        if not resp:
            return None
        data_list = []
        if isinstance(resp, dict):
            if 'data' in resp:
                # data may be list
                d = resp['data']
                if isinstance(d, dict):
                    # maybe OHLC keys
                    # Example: {'open': [...], 'high': [...], ...}
                    # Convert
                    if 'open' in d:
                        # Build DataFrame
                        df = pd.DataFrame({
                            'Open': d.get('open', []),
                            'High': d.get('high', []),
                            'Low': d.get('low', []),
                            'Close': d.get('close', []),
                            'Volume': d.get('volume', []),
                            'Date': d.get('startTime') or d.get('date') or []
                        })
                        if not df.empty:
                            df['Date'] = pd.to_datetime(df['Date'])
                            return df[['Date','Open','High','Low','Close','Volume']]
                elif isinstance(d, list):
                    data_list = d
            elif 'open' in resp:
                # Direct OHLC dict
                df = pd.DataFrame({
                    'Open': resp.get('open', []),
                    'High': resp.get('high', []),
                    'Low': resp.get('low', []),
                    'Close': resp.get('close', []),
                    'Volume': resp.get('volume', []),
                })
                if 'startTime' in resp:
                    df['Date'] = pd.to_datetime(resp['startTime'])
                return df
        if isinstance(resp, list):
            data_list = resp

        if data_list:
            df = pd.DataFrame(data_list)
            # Try to normalize columns
            # Expected columns: open, high, low, close, volume, date/startTime
            rename_map = {
                'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume',
                'startTime': 'Date', 'date': 'Date', 'timestamp': 'Date'
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
            if 'Date' in df.columns:
                df['Date'] = pd.to_datetime(df['Date'])
                return df[['Date','Open','High','Low','Close','Volume']]

        print(f"Dhan response for {symbol} unrecognized format: {str(resp)[:500]}")
        return None

    except Exception as e:
        print(f"Dhan fetch failed for {symbol}: {e}")
        return None

def fetch_yfinance_history(symbol, from_date, to_date):
    """Secondary: yfinance fallback"""
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
    """
    Primary Dhan, Secondary yfinance
    """
    # Try Dhan first
    df = fetch_dhan_history(symbol, from_date, to_date)
    if df is not None and not df.empty and len(df) >= 150:
        print(f"{symbol}: Dhan success {len(df)} rows")
        return df
    # Fallback yfinance
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
            import io
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

    return ["RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","SBIN","BHARTIARTL","ITC","KOTAKBANK","LT"]

def run_daily_scan():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — will print instead of sending")

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

            # Use scanner logic
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

            # Watchlist: breakout in last 30 days (changed from 7 as per user request)
            if result['watchlist']:
                for w in result['watchlist']:
                    # w already filtered to last 7 days in old logic, we need to re-filter to 30 days
                    # check_today_events uses 7 days internally, so we need to manually check 30 days here
                    # For now, we will include all trades where breakout in last 30 days
                    pass

            # For watchlist 30 days: check all trades where breakout in last 30 days
            all_trades = result['all_trades']
            today = df['Date'].max().date() if not df.empty else datetime.now().date()
            for tr in all_trades:
                try:
                    bdate = tr['breakout_date'].date() if hasattr(tr['breakout_date'], 'date') else pd.to_datetime(tr['breakout_date']).date()
                    delta = (today - bdate).days
                    if 0 < delta <= 30:  # Changed from 7 to 30 as per user request
                        tr_copy = tr.copy()
                        tr_copy['ticker'] = f"{sym}.NS"
                        watchlist_all.append(tr_copy)
                except:
                    continue

            if idx % 50 == 0:
                print(f"[{idx}/{len(symbols)}] {sym}.NS — B/O today:{len(breakout_today_list)} Rev today:{len(reversal_today_list)} Watch:{len(watchlist_all)}")

            time.sleep(0.2)  # be nice to APIs

        except Exception as e:
            print(f"{sym} error {e}")
            continue

    # Deduplicate watchlist by ticker+breakout_date
    dedup = {}
    for w in watchlist_all:
        key = (w['ticker'], str(w['breakout_date']))
        dedup[key] = w
    watchlist_all = list(dedup.values())

    # Prepare messages
    today_str = datetime.now().strftime('%Y-%m-%d')
    header = f"📊 *5-Phase Scanner Daily Report* {today_str} (Nifty500, 1291-trade logic)\nUniverse: {len(symbols)} | With setup: {tickers_with_trades}\nWatchlist window: last 30 days (changed from 7d as per request) | Dual API: Dhan primary, yfinance fallback\n"

    # Watchlist message
    from telegram_helper import format_watchlist
    watchlist_msg = format_watchlist(watchlist_all, tickers_with_trades)
    full_watchlist_msg = header + "\n" + watchlist_msg

    print(full_watchlist_msg)
    from telegram_helper import send_telegram_message, format_breakout_alert, format_reversal_alert
    send_telegram_message(bot_token, chat_id, full_watchlist_msg)

    # Breakout alerts
    if breakout_today_list:
        for tr in breakout_today_list:
            msg = format_breakout_alert(tr, tr['ticker'])
            print(msg)
            send_telegram_message(bot_token, chat_id, msg)
    else:
        msg = f"🔍 No new breakouts today {today_str}"
        print(msg)

    # Reversal alerts
    if reversal_today_list:
        for tr in reversal_today_list:
            msg = format_reversal_alert(tr, tr['ticker'])
            print(msg)
            send_telegram_message(bot_token, chat_id, msg)
    else:
        msg = f"✅ No reversal entries today {today_str}"
        print(msg)

    # Save daily snapshot
    pd.DataFrame(watchlist_all).to_csv(f"daily_watchlist_{today_str}.csv", index=False)
    pd.DataFrame(breakout_today_list).to_csv(f"breakouts_today_{today_str}.csv", index=False)
    pd.DataFrame(reversal_today_list).to_csv(f"reversals_today_{today_str}.csv", index=False)

    print("Daily scan done")

if __name__ == "__main__":
    run_daily_scan()
