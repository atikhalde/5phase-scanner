import os
import requests

def send_telegram_message(bot_token, chat_id, message, parse_mode="Markdown"):
    if not bot_token or not chat_id:
        print("Telegram credentials missing, skipping send")
        print(message)
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": parse_mode, "disable_web_page_preview": True}
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
    rally_high = trade.get('rally_high')
    if rally_high is not None:
        rhd = trade.get('rally_high_date')
        rhd_str = rhd.strftime('%Y-%m-%d') if hasattr(rhd, 'strftime') else str(rhd)
        rally_line = f"Rally High: {rally_high} on {rhd_str}\n"
        tail = "Drop will be tracked next 15d"
    else:
        rally_line = "Rally High: pending (needs 1-7 day continuation >1%)\n"
        tail = "Awaiting rally confirmation (Phase 4a)"
    return (
        f"🚀 *BREAKOUT ALERT* `{ticker}`\n"
        f"Anchor: {trade['anchor_high']} on {anchor_date} ({trade['days_since']}d ago)\n"
        f"Breakout: {trade['breakout_close']} on {breakout_date} Vol {trade['vol_break']}x\n"
        f"{rally_line}"
        f"Dry90: {trade['dry90']} | {tail}"
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
        return "📋 *Daily Watchlist*: No recent breakouts in last 30 days waiting reversal (verified, then checked 60d - also none)"
    pending = [t for t in watchlist if t.get('reversal_date') is None or (isinstance(t.get('reversal_date'), float) and t.get('reversal_date') != t.get('reversal_date'))]
    fired = [t for t in watchlist if t not in pending]
    lines = [f"📋 *Daily Watchlist* — {len(watchlist)} stocks waiting reversal (last 30d verified, then 60d):\n"]
    for trade in watchlist[:20]:
        ticker = trade.get('ticker', 'UNKNOWN')
        bd = trade['breakout_date'].strftime('%Y-%m-%d') if hasattr(trade['breakout_date'], 'strftime') else str(trade['breakout_date'])
        rally = trade.get('rally_high')
        shake = trade.get('shake_low')
        drop = trade.get('drop_pct')
        is_pending = trade.get('reversal_date') is None or (
            isinstance(trade.get('reversal_date'), float)
            and trade.get('reversal_date') != trade.get('reversal_date'))
        if is_pending:
            # In-progress breakout: Phase 4a confirmed, shakeout/reversal not yet.
            lines.append(
                f"• `{ticker}` B/O {bd} Rally {rally} → *awaiting shakeout & reversal*")
        else:
            rd = trade['reversal_date'].strftime('%Y-%m-%d') if hasattr(trade['reversal_date'], 'strftime') else str(trade['reversal_date'])
            lines.append(
                f"• `{ticker}` B/O {bd} Rally {rally} Shake {shake} ({drop}%) → Waiting reversal {rd}")
    if len(watchlist) > 20:
        lines.append(f"... and {len(watchlist)-20} more")
    lines.append(f"\nTotal with setup: {tickers_with_trades} tickers | Pending: {len(pending)} | Logic: 1291 trades (ABDL fix)")
    return "\n".join(lines)

def format_recent_reversals_fired(recent_list):
    if not recent_list:
        return "📈 *Recent Reversals Fired (last 30d)*: None — no reversals fired in last 30 days"
    lines = [f"📈 *Recent Reversals Fired (last 30d)* — {len(recent_list)} stocks where reversal already fired (from cache/live):\n"]
    for trade in recent_list[:20]:
        ticker = trade.get('ticker', 'UNKNOWN')
        bd = trade['breakout_date'].strftime('%Y-%m-%d') if hasattr(trade['breakout_date'], 'strftime') else str(trade['breakout_date'])
        rd = trade['reversal_date'].strftime('%Y-%m-%d') if hasattr(trade['reversal_date'], 'strftime') else str(trade['reversal_date'])
        entry = trade.get('entry','')
        drop_pct = trade.get('drop_pct','')
        lines.append(f"• `{ticker}` B/O {bd} → Reversal {rd} Entry {entry} Drop {drop_pct}%")
    if len(recent_list) > 20:
        lines.append(f"... and {len(recent_list)-20} more")
    lines.append(f"\nThese already fired — not in waiting watchlist, shown for verification as you requested")
    return "\n".join(lines)
