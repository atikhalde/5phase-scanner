"""Tests for once-per-session Telegram alert deduplication.

Run: python3 test_alert_dedup.py
"""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import alert_state
import daily_scanner
import full_scanner
from test_shake_green_alert import make_shake_green_df

PASSED = FAILED = 0


def check(condition, message):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok: {message}")
    else:
        FAILED += 1
        print(f"  FAIL: {message}")


def test_current_session_data_guard():
    print("[1] stale prior-session data is not reportable")
    today = pd.Timestamp("2026-11-24").date()
    check(daily_scanner.has_current_session_data(today, today=today),
          "today-dated bar is eligible")
    check(not daily_scanner.has_current_session_data(
        pd.Timestamp("2026-11-23").date(), today=today),
          "previous session bar is suppressed on a closed weekday")
    check(not daily_scanner.has_current_session_data(None, today=today),
          "missing data date is suppressed")


def test_state_basics():
    print("[2] alert_state registry")
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "sent_alerts.json")
        old_path = os.environ.get("ALERT_STATE_FILE")
        old_dedup = os.environ.get("ALERT_DEDUP")
        try:
            os.environ["ALERT_STATE_FILE"] = path
            os.environ["ALERT_DEDUP"] = "1"
            state = alert_state.load_state()
            check(state["sent"] == {}, "fresh registry is empty")

            key = alert_state.event_key(
                "shake_green",
                {"ticker": "ABC.NS", "shake_low_date": "2026-11-20", "green_date": "2026-11-21"},
            )
            check(alert_state.is_new(state, key), "new event is sent")
            alert_state.mark_sent(state, key)
            check(not alert_state.is_new(state, key), "same event is suppressed")
            check(alert_state.save_state(state) and os.path.exists(path), "registry persists to disk")
            reloaded = alert_state.load_state()
            check(not alert_state.is_new(reloaded, key), "survives a reload (Actions cache round-trip)")

            reloaded["date"] = "2000-01-01"
            check(alert_state.save_state(reloaded), "old session saved")
            check(alert_state.load_state()["sent"] == {}, "old sessions reset automatically")

            trade = {"ticker": "ABC.NS", "breakout_date": pd.Timestamp("2026-11-20"),
                     "reversal_date": pd.Timestamp("2026-11-21")}
            k1 = alert_state.event_key("reversal", trade, ticker="ABC.NS")
            k2 = alert_state.event_key("reversal", dict(trade, ticker="ABC.NS"))
            check(k1 == k2, "keys are independent of scanner name/extra fields")
            check(k1 != alert_state.event_key("breakout", trade), "different events have different keys")
            check(alert_state.summary_key("a", "b") == alert_state.summary_key("a", "b"),
                  "identical report content has identical key")
            check(alert_state.summary_key("a", "b") != alert_state.summary_key("a", "c"),
                  "changed report content has new key")

            os.environ["ALERT_DEDUP"] = "0"
            check(alert_state.is_new(reloaded, key), "ALERT_DEDUP=0 disables dedup")
        finally:
            for name, value in (("ALERT_STATE_FILE", old_path), ("ALERT_DEDUP", old_dedup)):
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def run_scanner_twice(module, symbol, df):
    """Patch I/O, run a scanner twice with the same registry, return sent messages."""
    sent = []

    def fake_send(bot_token, chat_id, message, parse_mode="Markdown"):
        sent.append(message)
        return True

    old_send = module.send_telegram_message
    old_fetch = module.get_history_dual_api
    old_universe = (module.load_full_eq_symbols if module is full_scanner
                    else module.load_nifty500_symbols)
    try:
        module.send_telegram_message = fake_send
        module.get_history_dual_api = lambda sym, a, b: df.copy()
        if module is full_scanner:
            module.load_full_eq_symbols = lambda: [symbol]
        else:
            module.load_nifty500_symbols = lambda: [symbol]
        with contextlib.redirect_stdout(io.StringIO()):
            module.run_daily_scan() if module is daily_scanner else module.run_full_scan()
            first = list(sent)
            sent.clear()
            module.run_daily_scan() if module is daily_scanner else module.run_full_scan()
            second = list(sent)
        return first, second
    finally:
        module.send_telegram_message = old_send
        module.get_history_dual_api = old_fetch
        if module is full_scanner:
            module.load_full_eq_symbols = old_universe
        else:
            module.load_nifty500_symbols = old_universe


def test_end_to_end_daily_and_full_share_state():
    print("[3] repeated scans do not re-send the same Telegram alerts")
    today = str(pd.Timestamp.today().date())
    df = make_shake_green_df(i_off=17, last='green', end_date=today)
    symbol = "ALEMBICLTD"

    with tempfile.TemporaryDirectory() as td:
        old = {k: os.environ.get(k) for k in
               ("ALERT_STATE_FILE", "ALERT_DEDUP", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        cwd = os.getcwd()
        try:
            os.chdir(td)
            os.environ["ALERT_STATE_FILE"] = os.path.join(td, "sent_alerts.json")
            os.environ["ALERT_DEDUP"] = "1"
            os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
            os.environ["TELEGRAM_CHAT_ID"] = "test-chat"

            first, second = run_scanner_twice(daily_scanner, symbol, df)
            green_first = [m for m in first if "SHAKE-LOW" in m]
            green_second = [m for m in second if "SHAKE-LOW" in m]
            check(len(green_first) == 1, f"daily scan sends the green alert once (got {len(green_first)})")
            check(len(green_second) == 0, f"second daily scan does not resend it (got {len(green_second)})")
            check(len(second) == 0, f"second daily scan sends nothing at all (got {len(second)})")

            # Full scanner must read the SAME registry: no duplicate EOD alert.
            old_send = full_scanner.send_telegram_message
            full_sent = []
            full_scanner.send_telegram_message = lambda b, c, m, p="Markdown": (full_sent.append(m) or True)
            old_fetch = full_scanner.get_history_dual_api
            old_universe = full_scanner.load_full_eq_symbols
            try:
                full_scanner.get_history_dual_api = lambda sym, a, b: df.copy()
                full_scanner.load_full_eq_symbols = lambda: [symbol]
                with contextlib.redirect_stdout(io.StringIO()):
                    full_scanner.run_full_scan()
            finally:
                full_scanner.send_telegram_message = old_send
                full_scanner.get_history_dual_api = old_fetch
                full_scanner.load_full_eq_symbols = old_universe
            check(not [m for m in full_sent if "SHAKE-LOW" in m],
                  "full EOD scan does not resend the daily scanner's green alert")
            check(not full_sent, f"full EOD scan sends nothing unchanged (got {len(full_sent)})")

            with open(os.environ["ALERT_STATE_FILE"]) as f:
                import json
                registry = json.load(f)
            check(any(k.startswith("shake_green|") for k in registry["sent"]),
                  "green event recorded in shared registry")
        finally:
            os.chdir(cwd)
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def main():
    test_current_session_data_guard()
    test_state_basics()
    test_end_to_end_daily_and_full_share_state()
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
