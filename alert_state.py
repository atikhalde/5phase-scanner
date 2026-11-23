"""Persistent, once-per-event Telegram alert deduplication.

Both scanners recompute the same alert lists on every run.  Without state,
a breakout that happened at 09:30 is re-sent on every 15-minute scan, and an
EOD full scan re-sends everything the daily scanner already announced.

State is stored in a small JSON file that GitHub Actions restores from the
Actions cache at the start of a run and saves (under a new key) afterwards.
The file is keyed by IST date, so it resets itself every trading session.
Set ALERT_DEDUP=0 to disable deduplication (useful for local debugging).
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_STATE_FILE = ".alert-state/sent_alerts.json"


def dedup_enabled():
    return os.getenv("ALERT_DEDUP", "1").strip().lower() not in ("0", "false", "no", "off")


def state_path():
    return os.getenv("ALERT_STATE_FILE", DEFAULT_STATE_FILE)


def ist_today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()


def empty_state():
    return {"version": 1, "date": ist_today(), "sent": {}}


def load_state():
    """Load today's sent-alert registry, discarding stale/old sessions."""
    if not dedup_enabled():
        return empty_state()
    path = state_path()
    raw = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            raw = loaded
    except (OSError, ValueError):
        pass
    today = ist_today()
    if raw.get("date") != today or not isinstance(raw.get("sent"), dict):
        return {"version": 1, "date": today, "sent": {}}
    return {"version": 1, "date": today, "sent": dict(raw["sent"])}


def save_state(state):
    """Atomically persist the registry. Safe to call even when dedup is off."""
    if not dedup_enabled():
        return False
    path = state_path()
    try:
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print(f"alert_state: could not save {path}: {e}")
        return False


def _date_str(value):
    """Normalise pandas Timestamp / datetime / string to YYYY-MM-DD."""
    if value is None:
        return "none"
    try:
        if hasattr(value, "date"):
            value = value.date()
    except Exception:
        pass
    return str(value)[:10]


def event_key(kind, trade, ticker=None):
    """Stable key for one real-world event, independent of scanner/workflow.

    Using the event's own date (not the scan time) means a rerun or a second
    scanner cannot re-announce the same bar.  The keys intentionally do NOT
    contain the scanner name so daily and full scans share dedup state.
    """
    t = str(ticker or trade.get("ticker") or "?")
    if kind == "breakout":
        return f"breakout|{t}|{_date_str(trade.get('breakout_date'))}"
    if kind == "shakeout":
        return (f"shakeout|{t}|{_date_str(trade.get('breakout_date'))}|"
                f"{_date_str(trade.get('shake_low_date'))}")
    if kind == "reversal":
        return f"reversal|{t}|{_date_str(trade.get('reversal_date'))}"
    if kind == "shake_green":
        return (f"shake_green|{t}|{_date_str(trade.get('shake_low_date'))}|"
                f"{_date_str(trade.get('green_date'))}")
    return f"{kind}|{t}"


def digest_key(kind, text):
    """Key for a message whose content should be sent only when it changes."""
    h = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{kind}|{h}"


def summary_key(watchlist_msg, recent_msg, stale_note=""):
    return digest_key("summary", f"{watchlist_msg}\n{recent_msg}\n{stale_note}")


def failure_key():
    return f"data_failure|{ist_today()}"


def is_new(state, key):
    if not dedup_enabled():
        return True
    return key not in state.get("sent", {})


def mark_sent(state, key):
    state.setdefault("sent", {})[key] = datetime.now(timezone.utc).isoformat(timespec="seconds")
