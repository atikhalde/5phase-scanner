import os
import requests

# Telegram hard limit is 4096. Stay under it; split on newlines.
TELEGRAM_MAX_LEN = 3900


def _post_telegram(bot_token, chat_id, text, parse_mode):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    resp = requests.post(url, json=payload, timeout=10)
    print(f"Telegram response: {resp.status_code} {resp.text[:200]}")
    return resp


def _split_telegram_text(message, limit=TELEGRAM_MAX_LEN):
    if len(message) <= limit:
        return [message]
    chunks, rest = [], message
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind('\n', 0, limit)
        if cut < limit // 3:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip('\n')
    return chunks


def send_telegram_message(bot_token, chat_id, message, parse_mode="Markdown"):
    """Send a Telegram message.

    Legacy Markdown is brittle (unmatched *, _, etc. -> HTTP 400 and the
    alert is silently dropped). If Markdown is rejected we retry as plain
    text so breakout/shakeout/watchlist alerts still arrive. Long reports
    are split into <4096-char chunks.
    """
    if not bot_token or not chat_id:
        print("Telegram credentials missing, skipping send")
        print(message)
        return False
    ok_all = True
    try:
        for chunk in _split_telegram_text(message):
            resp = _post_telegram(bot_token, chat_id, chunk, parse_mode)
            if resp.status_code == 200:
                continue
            # 400 can't parse entities -> retry without parse_mode
            body = (resp.text or '').lower()
            if resp.status_code == 400 and ('parse' in body or 'entities' in body or parse_mode):
                print("Telegram Markdown rejected; retrying as plain text")
                resp2 = _post_telegram(bot_token, chat_id, chunk, None)
                if resp2.status_code != 200:
                    ok_all = False
            else:
                ok_all = False
        return ok_all
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

def format_shakeout_alert(trade, ticker):
    anchor_date = trade['anchor_date'].strftime('%Y-%m-%d') if hasattr(trade['anchor_date'], 'strftime') else str(trade['anchor_date'])
    breakout_date = trade['breakout_date'].strftime('%Y-%m-%d') if hasattr(trade['breakout_date'], 'strftime') else str(trade['breakout_date'])
    rally_date = trade['rally_high_date'].strftime('%Y-%m-%d') if hasattr(trade['rally_high_date'], 'strftime') else str(trade['rally_high_date'])
    shake_date = trade['shake_low_date'].strftime('%Y-%m-%d') if hasattr(trade['shake_low_date'], 'strftime') else str(trade['shake_low_date'])

    anchor_high = trade.get('anchor_high', '')
    breakout_close = trade.get('breakout_close', '')
    vol_break = trade.get('vol_break', '')
    rally_high = trade.get('rally_high', '')
    shake_low = trade.get('shake_low', '')
    shake_low_vol = trade.get('shake_low_vol', '')
    shake_high = trade.get('shake_high', '')
    drop_pct = trade.get('drop_pct', '')
    dry90 = trade.get('dry90', '')

    # Honest level line: the low actually reached the zone (detector-guaranteed)
    try:
        if float(shake_low) < float(anchor_high):
            level_line = f"Level Reached: wick below anchor {anchor_high} (zone {anchor_high} - {breakout_close})"
        else:
            level_line = f"Level Reached: OB/Breakout {breakout_close} (low {shake_low} in zone {anchor_high} - {breakout_close})"
    except (TypeError, ValueError):
        level_line = f"Level Tested: Supply/OB Zone ({anchor_high} - {breakout_close})"

    return (
        f"⚡ *SHAKEOUT / PULLBACK ALERT* `{ticker}`\n"
        f"Phase 4b: Low-Vol Pullback REACHED SSL/Supply/OB\n"
        f"Supply/Anchor: {anchor_high} on {anchor_date}\n"
        f"Breakout: {breakout_close} on {breakout_date} Vol {vol_break}x\n"
        f"Rally High: {rally_high} on {rally_date} (4a window)\n"
        f"Shakeout Low: {shake_low} on {shake_date} Vol {shake_low_vol}x\n"
        f"Off High {shake_high}: -{drop_pct}% (drawdown since breakout)\n"
        f"{level_line}\n"
        f"Dry90: {dry90} | Awaiting Phase 5 Reversal Entry (Vol>0.6x)"
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
            status = trade.get('status')
            if status == 'awaiting_reversal' or shake is not None:
                # Valid 4-25% low-volume shakeout already observed; waiting
                # for the bullish reversal bar (reversal window still open).
                lines.append(
                    f"• `{ticker}` B/O {bd} Rally {rally} Shake {shake} ({drop}%) → *awaiting reversal*")
            else:
                # Phase 4a confirmed; shakeout window still open, no valid
                # shakeout yet.  Expired/failed breakouts are excluded
                # upstream (ABDL-type filter).
                lines.append(
                    f"• `{ticker}` B/O {bd} Rally {rally} → *awaiting shakeout*")
        else:
            rd = trade['reversal_date'].strftime('%Y-%m-%d') if hasattr(trade['reversal_date'], 'strftime') else str(trade['reversal_date'])
            lines.append(
                f"• `{ticker}` B/O {bd} Rally {rally} Shake {shake} ({drop}%) → Waiting reversal {rd}")
    if len(watchlist) > 20:
        lines.append(f"... and {len(watchlist)-20} more")
    # tickers_with_trades is a HISTORICAL count (any setup found in the 2Y
    # scan), not the number of live setups -- label it accordingly.
    lines.append(f"\nTickers with any 2Y setup: {tickers_with_trades} | Pending: {len(pending)} | Logic: 1291 trades (ABDL fix)")
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
