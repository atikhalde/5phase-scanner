import os
import requests

def send_telegram_message(bot_token, chat_id, message, parse_mode="Markdown"):
    """Send message via Telegram Bot API"""
    if not bot_token or not chat_id:
        print("Telegram credentials missing, skipping send")
        print(message)
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        print(f"Telegram response: {resp.status_code} {resp.text[:200]}")
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram send error: {e}")
        return False

def format_breakout_alert(trade, ticker):
    anchor_date = trade['anchor_date'].strftime('%Y-%m-%d') if hasattr(trade['anchor_date'], 'strftime') else str(trade['anchor_date'])
    breakout_date = trade['breakout_date'].strftime('%Y-%m-%d') if hasattr(trade['breakout_date'], 'strftime') else str(trade['breakout_date'])
    return (
        f"🚀 *BREAKOUT ALERT* `{ticker}`\n"
        f"Anchor: {trade['anchor_high']} on {anchor_date} ({trade['days_since']}d ago)\n"
        f"Breakout: {trade['breakout_close']} on {breakout_date} Vol {trade['vol_break']}x\n"
        f"Rally High: {trade['rally_high']} on {trade['rally_high_date'].strftime('%Y-%m-%d') if hasattr(trade['rally_high_date'], 'strftime') else trade['rally_high_date']}\n"
        f"Dry90: {trade['dry90']} | Drop will be tracked next 15d"
    )

def format_reversal_alert(trade, ticker):
    reversal_date = trade['reversal_date'].strftime('%Y-%m-%d') if hasattr(trade['reversal_date'], 'strftime') else str(trade['reversal_date'])
    shake_low_date = trade['shake_low_date'].strftime('%Y-%m-%d') if hasattr(trade['shake_low_date'], 'strftime') else str(trade['shake_low_date'])
    breakout_date = trade['breakout_date'].strftime('%Y-%m-%d') if hasattr(trade['breakout_date'], 'strftime') else str(trade['breakout_date'])
    return (
        f"✅ *REVERSAL ENTRY ALERT* `{ticker}`\n"
        f"Breakout: {breakout_date} @ {trade['breakout_close']}\n"
        f"Rally High: {trade['rally_high']} on {trade['rally_high_date'].strftime('%Y-%m-%d') if hasattr(trade['rally_high_date'], 'strftime') else trade['rally_high_date']}\n"
        f"Shake Low: {trade['shake_low']} on {shake_low_date} Vol {trade['shake_low_vol']}x Drop {trade['drop_pct']}%\n"
        f"ENTRY: {trade['entry']} on {reversal_date} Vol {trade['entry_vol']}x\n"
        f"SL: {round(trade['shake_low']*0.97,2)} | Dry90: {trade['dry90']}"
    )

def format_watchlist(watchlist, tickers_with_trades):
    if not watchlist:
        return "📋 *Daily Watchlist*: No recent breakouts in last 7 days"
    lines = [f"📋 *Daily Watchlist* — {len(watchlist)} stocks in breakout+shakeout phase (last 7d):\n"]
    for trade in watchlist[:20]:  # limit 20
        ticker = trade.get('ticker', 'UNKNOWN')
        # trade dict from scanner has no ticker, we add externally
        lines.append(
            f"• `{ticker}` B/O {trade['breakout_date'].strftime('%Y-%m-%d') if hasattr(trade['breakout_date'], 'strftime') else trade['breakout_date']} "
            f"Rally {trade['rally_high']} Shake {trade['shake_low']} ({trade['drop_pct']}%) "
            f"Waiting reversal"
        )
    if len(watchlist) > 20:
        lines.append(f"... and {len(watchlist)-20} more")
    lines.append(f"\nTotal with setup: {tickers_with_trades} tickers")
    return "\n".join(lines)
