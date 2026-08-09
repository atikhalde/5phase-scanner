"""
Daily Scanner — uses EXACT same conditions as backtest that produced 1291 trades
Universe: Nifty 500 (as per user selection) — can switch to full EQ 2075 by changing CSV

Alerts:
- Watchlist: breakout in last 7 days (recent breakout alert)
- Breakout Today: Phase3 breakout today live
- Reversal Entry Today: Phase5 reversal today live

Sends to Telegram via bot
"""

import os
import sys
import pandas as pd
import yfinance as yf
import csv
from datetime import datetime, timedelta
from scanner import scan_5phase, prepare_df, check_today_events
from telegram_helper import send_telegram_message, format_breakout_alert, format_reversal_alert, format_watchlist

# Config
UNIVERSE_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
UNIVERSE_LOCAL = "nifty500list.csv"  # fallback
LOOKBACK_YEARS = 2  # need 2 years history for 180d anchor

def load_nifty500_symbols():
    symbols = []
    try:
        # Try download
        import requests
        import io
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(UNIVERSE_CSV_URL, headers=headers, timeout=15)
        if resp.status_code == 200:
            content = resp.text
            # Parse CSV
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                sym = row.get('Symbol') or row.get('SYMBOL')
                if sym:
                    symbols.append(sym.strip())
            print(f"Loaded {len(symbols)} from NSE Nifty500")
            if symbols:
                return symbols
    except Exception as e:
        print(f"Failed to download Nifty500 list: {e}")

    # Fallback: try local file or equity_l.csv filtered
    try:
        df = pd.read_csv("/tmp/equity_l.csv")
        # Take first 500 EQ as proxy for Nifty500 if download fails
        eq_syms = df[df[' SERIES'] == 'EQ']['SYMBOL'].tolist() if ' SERIES' in df.columns else df[df['SERIES']=='EQ']['SYMBOL'].tolist() if 'SERIES' in df.columns else []
        # Actually equity_l.csv header is SYMBOL,NAME, ...
        # Re-parse correctly
        with open("/tmp/equity_l.csv") as f:
            reader = csv.DictReader(f)
            eq = []
            for row in reader:
                if row.get('SERIES') == 'EQ' or row.get(' SERIES') == 'EQ':
                    eq.append(row['SYMBOL'])
                if len(eq) >= 500:
                    break
            if eq:
                print(f"Fallback loaded {len(eq)} EQ as Nifty500 proxy")
                return eq
    except Exception as e:
        print(f"Fallback failed: {e}")

    # Last fallback: small hardcoded list from previous backtest
    return ["RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","SBIN","BHARTIARTL","ITC","KOTAKBANK","LT","AXISBANK","ASIANPAINT","MARUTI","BAJFINANCE","HINDUNILVR","SUNPHARMA","TITAN","WIPRO","ULTRACEMCO","ONGC"]

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

    start_date = datetime.now() - timedelta(days=LOOKBACK_YEARS*365)

    for idx, sym in enumerate(symbols):
        ns = f"{sym}.NS"
        try:
            t = yf.Ticker(ns)
            hist = t.history(start=start_date.strftime("%Y-%m-%d"), end=(datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d"), auto_adjust=True)
            if hist.empty or len(hist) < 220:
                continue
            hist = hist.reset_index()
            hist['Date'] = pd.to_datetime(hist['Date']).dt.tz_localize(None)
            df = hist[['Date','Open','High','Low','Close','Volume']].copy()

            result = check_today_events(df)
            if result['all_trades']:
                tickers_with_trades += 1

            if result['breakout_today']:
                tr = result['breakout_today']
                tr['ticker'] = ns
                breakout_today_list.append(tr)

            if result['reversal_today']:
                tr = result['reversal_today']
                tr['ticker'] = ns
                reversal_today_list.append(tr)

            if result['watchlist']:
                for w in result['watchlist']:
                    w['ticker'] = ns
                    watchlist_all.append(w)

            if idx % 50 == 0:
                print(f"[{idx}/{len(symbols)}] {ns} — B/O today:{len(breakout_today_list)} Rev today:{len(reversal_today_list)} Watch:{len(watchlist_all)}")

        except Exception as e:
            print(f"{ns} error {e}")
            continue

    # Prepare messages
    today_str = datetime.now().strftime('%Y-%m-%d')
    header = f"📊 *5-Phase Scanner Daily Report* {today_str} (Nifty500, 1291-trade logic)\nUniverse: {len(symbols)} | With setup: {tickers_with_trades}\n"

    # Watchlist message
    watchlist_msg = format_watchlist(watchlist_all, tickers_with_trades)
    full_watchlist_msg = header + "\n" + watchlist_msg

    print(full_watchlist_msg)
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
        # Optionally send only if you want quiet
        # send_telegram_message(bot_token, chat_id, msg)

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
