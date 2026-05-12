"""
payment/storage.py — Subscriber & pending payment storage (HARDENED)
======================================================================
HARDENING CHANGES:
  - _save: replaced raw open() write with atomic_write_json (crash-safe)
  - _load: uses load_json_safe (handles corruption + missing file)
  - approve_subscriber: timedelta import moved to top; error logged on failure
  - is_subscriber: robust timezone-aware datetime comparison
  - All public functions wrapped in try/except — never crash callers
  - get_all_subscribers: always returns list, never raises
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from threading import Lock
from typing import Optional

from core.logger import get_logger
from core.utils import atomic_write_json, load_json_safe

_DATA_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_SUBS_FILE = os.path.join(_DATA_DIR, "subscribers.json")

log = get_logger(__name__)
_lock = Lock()

_EMPTY_STORE: dict = {"subscribers": {}, "pending": {}}


def _load() -> dict:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
    except OSError as exc:
        log.warning("payment.storage: cannot create data dir: %s", exc)

    data = load_json_safe(_SUBS_FILE, default=None)

    if data is None or not isinstance(data, dict):
        log.warning("payment.storage: %s missing or corrupt — starting with empty store", _SUBS_FILE)
        return {"subscribers": {}, "pending": {}}

    # Ensure required keys exist (handles partial corruption)
    if "subscribers" not in data or not isinstance(data["subscribers"], dict):
        data["subscribers"] = {}
    if "pending" not in data or not isinstance(data["pending"], dict):
        data["pending"] = {}

    return data


def _save(data: dict) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
    except OSError:
        pass
    success = atomic_write_json(_SUBS_FILE, data)
    if not success:
        log.error("payment.storage: failed to save subscribers file — data may be lost on restart")


# ── Public API ─────────────────────────────────────────────────────────────────

def add_pending(user_id: int, username: str, proof_text: str) -> None:
    try:
        with _lock:
            data = _load()
            data["pending"][str(user_id)] = {
                "user_id":   user_id,
                "username":  username or "unknown",
                "proof":     (proof_text or "")[:1000],  # cap proof length
                "submitted": datetime.now(timezone.utc).isoformat(),
            }
            _save(data)
            log.debug("Pending payment added for user %s", user_id)
    except Exception as exc:
        log.error("add_pending failed for user %s: %s", user_id, exc)


def approve_subscriber(user_id: int, days: int) -> None:
    try:
        with _lock:
            data = _load()
            expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            data["subscribers"][str(user_id)] = {
                "user_id":  user_id,
                "approved": datetime.now(timezone.utc).isoformat(),
                "expires":  expiry,
            }
            data["pending"].pop(str(user_id), None)
            _save(data)
            log.info("Subscriber %s approved for %d days (expires %s)", user_id, days, expiry)
    except Exception as exc:
        log.error("approve_subscriber failed for user %s: %s", user_id, exc)


def reject_pending(user_id: int) -> None:
    try:
        with _lock:
            data = _load()
            data["pending"].pop(str(user_id), None)
            _save(data)
            log.debug("Pending payment rejected for user %s", user_id)
    except Exception as exc:
        log.error("reject_pending failed for user %s: %s", user_id, exc)


def is_subscriber(user_id: int) -> bool:
    try:
        with _lock:
            data = _load()
            sub = data["subscribers"].get(str(user_id))
            if not sub or not isinstance(sub, dict):
                return False
            expires_raw = sub.get("expires", "")
            if not expires_raw:
                return False
            expiry = datetime.fromisoformat(expires_raw)
            # Make timezone-aware if naive
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < expiry
    except Exception as exc:
        log.error("is_subscriber check failed for user %s: %s", user_id, exc)
        return False


def get_pending() -> dict:
    try:
        with _lock:
            return _load().get("pending", {})
    except Exception as exc:
        log.error("get_pending failed: %s", exc)
        return {}


def get_subscriber(user_id: int) -> Optional[dict]:
    try:
        with _lock:
            return _load()["subscribers"].get(str(user_id))
    except Exception as exc:
        log.error("get_subscriber failed for user %s: %s", user_id, exc)
        return None


def get_all_subscribers() -> list[dict]:
    """Return all subscriber records as a list. Public API for dashboard."""
    try:
        with _lock:
            return list(_load().get("subscribers", {}).values())
    except Exception as exc:
        log.error("get_all_subscribers failed: %s", exc)
        return []
