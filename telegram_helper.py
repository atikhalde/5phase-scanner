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
        f"📍 *Phase 4b: Low-Vol Pullback REACHED SSL / Supply / OB*\n"
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

def format_shake_green_alert(trade, ticker):
    """ADD-ON (2026-09-25): Phase 4b shake low formed on the PREVIOUS bar and
    the very next session printed a GREEN candle (Close > Open).

    This is the softer / earlier trigger: it does NOT require the strict
    Phase 5 conditions (close above the shake bar's high, VolRatio > 0.6 and
    rising), so it can fire on bars the REVERSAL ENTRY alert rejects -- e.g.
    ALEMBICLTD.NS shake low 2026-09-16 -> green candle 2026-09-17.
    """
    def d(v):
        return v.strftime('%Y-%m-%d') if hasattr(v, 'strftime') else str(v)

    breakout_date = d(trade.get('breakout_date'))
    rally_date = d(trade.get('rally_high_date'))
    shake_date = d(trade.get('shake_low_date'))
    green_date = d(trade.get('green_date'))

    anchor_high = trade.get('anchor_high', '')
    breakout_close = trade.get('breakout_close', '')
    rally_high = trade.get('rally_high', '')
    shake_low = trade.get('shake_low', '')
    shake_low_vol = trade.get('shake_low_vol', '')
    shake_high = trade.get('shake_high', '')
    drop_pct = trade.get('drop_pct', '')
    green_open = trade.get('green_open', '')
    green_close = trade.get('green_close', '')
    green_vol = trade.get('green_vol', '')
    dry90 = trade.get('dry90', '')

    try:
        chg = (float(green_close) - float(green_open)) / float(green_open) * 100.0
        body_pct = f" (+{chg:.2f}% intraday O→C)"
    except (TypeError, ValueError, ZeroDivisionError):
        body_pct = ""

    try:
        sl_ref = round(float(shake_low) * 0.97, 2)
    except (TypeError, ValueError):
        sl_ref = '-'

    return (
        f"🟢 *SHAKE-LOW → GREEN NEXT DAY* `{ticker}`\n"
        f"📍 *Shake low made on the previous session, next session closed GREEN*\n"
        f"Breakout: {breakout_close} on {breakout_date} Vol {trade.get('vol_break','')}x\n"
        f"Rally High: {rally_high} on {rally_date}\n"
        f"Shake Low: {shake_low} on {shake_date} Vol {shake_low_vol}x\n"
        f"Off High {shake_high}: -{drop_pct}% (Phase 4b shakeout)\n"
        f"GREEN candle: {green_date} O {green_open} → C {green_close}{body_pct}"
        f" Vol {green_vol}x\n"
        f"Supply/Anchor: {anchor_high} | SL ref: {sl_ref} | Dry90: {dry90}"
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
