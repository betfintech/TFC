"""
communication/notifier.py — Telegram signal dispatcher (PRODUCTION-GRADE)
==========================================================================
HARDENING v2:
  - _send_with_retry: 3 attempts with exponential backoff (1s, 2s, 4s)
  - Admin fallback: if channel fails after retries → try ADMIN_ID
  - If admin also fails → log CRITICAL
  - Delivery confirmation: explicit SUCCESS/FAILURE log after final attempt
  - Duplicate prevention: same signal (symbol+direction+entry) suppressed
    within _DEDUP_WINDOW_SEC seconds
  - All original hardening retained (non-JSON guard, timeout, validation)
"""
from __future__ import annotations

import time
import hashlib
from threading import Lock

import requests

from core.config import TELEGRAM_BOT_TOKEN, CHANNEL_ID, ADMIN_ID
from core.logger import get_logger
from trading.strategy import Signal
from communication.formatter import format_signal
from communication.signal_store import record_signal

log = get_logger(__name__)

_TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Only include non-zero, valid targets
_SIGNAL_TARGETS: list[int] = [t for t in [CHANNEL_ID, ADMIN_ID] if t]

# ── Duplicate-send prevention ──────────────────────────────────────────────────
_DEDUP_WINDOW_SEC: int = 60
_dedup_lock: Lock = Lock()
_dedup_cache: dict[str, float] = {}


def _signal_key(signal: Signal) -> str:
    raw = f"{signal.symbol}|{signal.direction}|{signal.entry}"
    return hashlib.md5(raw.encode()).hexdigest()


def _is_duplicate(signal: Signal) -> bool:
    """Return True if an identical signal was sent within _DEDUP_WINDOW_SEC."""
    key = _signal_key(signal)
    now = time.monotonic()
    with _dedup_lock:
        stale = [k for k, ts in _dedup_cache.items() if now - ts > _DEDUP_WINDOW_SEC * 2]
        for k in stale:
            del _dedup_cache[k]
        if key in _dedup_cache:
            age = now - _dedup_cache[key]
            if age < _DEDUP_WINDOW_SEC:
                log.warning(
                    "Duplicate signal suppressed: %s %s (%.0fs ago)",
                    signal.direction, signal.symbol, age,
                )
                return True
        _dedup_cache[key] = now
        return False


# ── Core send primitives ───────────────────────────────────────────────────────

def _send_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
    """Raw Telegram sendMessage. Returns True on success. Never raises."""
    if not chat_id:
        log.warning("_send_message called with invalid chat_id=%s — skipped", chat_id)
        return False
    if not text or not text.strip():
        log.warning("_send_message called with empty text to %s — skipped", chat_id)
        return False
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set — cannot send message to %s", chat_id)
        return False

    try:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        resp = requests.post(
            f"{_TELEGRAM_API}/sendMessage",
            json=payload,
            timeout=10,
        )

        try:
            data = resp.json()
        except ValueError:
            log.error(
                "Telegram non-JSON response to %s (HTTP %s): %s",
                chat_id, resp.status_code, resp.text[:200],
            )
            return False

        if not data.get("ok"):
            log.error("Telegram error to %s: %s", chat_id, data.get("description", "unknown"))
            return False
        return True

    except requests.exceptions.Timeout:
        log.error("Telegram send timeout to %s", chat_id)
        return False
    except requests.exceptions.ConnectionError as exc:
        log.error("Telegram connection error to %s: %s", chat_id, exc)
        return False
    except Exception as exc:
        log.error("Telegram send failed to %s: %s", chat_id, exc)
        return False


def _send_with_retry(
    chat_id: int,
    text: str,
    parse_mode: str = "Markdown",
    max_attempts: int = 3,
) -> bool:
    """
    Send with up to max_attempts tries. Exponential backoff: 1s → 2s → 4s.
    Returns True if any attempt succeeds.
    """
    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        success = _send_message(chat_id, text, parse_mode=parse_mode)
        if success:
            if attempt > 1:
                log.info("Delivered to %s after %d attempts", chat_id, attempt)
            return True
        if attempt < max_attempts:
            log.warning(
                "Send to %s failed (attempt %d/%d) — retry in %.0fs",
                chat_id, attempt, max_attempts, delay,
            )
            time.sleep(delay)
            delay *= 2
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

def send_signal(signal: Signal) -> None:
    """
    Send a signal to CHANNEL_ID (with retry). If channel fails → fallback to
    ADMIN_ID. If both fail → log CRITICAL. Never sends to private users.
    """
    if not signal:
        log.warning("send_signal called with None signal — skipped")
        return

    if not signal.is_actionable:
        log.debug("send_signal: signal is not actionable — skipped")
        return

    if not _SIGNAL_TARGETS:
        log.error("send_signal: no valid targets configured")
        return

    # Duplicate prevention
    if _is_duplicate(signal):
        return

    # Format
    label = f"{signal.direction} {signal.symbol}"
    try:
        message = format_signal(signal)
        if not message or not message.strip():
            raise ValueError("format_signal returned empty string")
    except Exception as exc:
        log.warning("format_signal failed (%s) — using plain-text fallback", exc)
        message = (
            f"{signal.direction} {signal.symbol}\n"
            f"Entry: {signal.entry}\n"
            f"SL: {signal.stop_loss}\n"
            f"TP1: {signal.tp1}\n"
            f"Reason: {signal.reason}"
        )

    # Store for dashboard
    try:
        record_signal(signal)
    except Exception as exc:
        log.warning("signal_store record failed: %s", exc)

    # ── Dispatch: channel first, admin fallback ───────────────────────────────
    if CHANNEL_ID and CHANNEL_ID in _SIGNAL_TARGETS:
        channel_ok = _send_with_retry(CHANNEL_ID, message)
        if channel_ok:
            log.info("✅ Signal [%s] — delivered to CHANNEL", label)
        else:
            log.error("❌ Signal [%s] — CHANNEL delivery FAILED after all retries", label)
            if ADMIN_ID:
                log.warning("Signal [%s] — attempting ADMIN fallback", label)
                admin_ok = _send_with_retry(
                    ADMIN_ID,
                    f"⚠️ CHANNEL FAILED — signal fallback:\n\n{message}",
                )
                if admin_ok:
                    log.warning("⚠️  Signal [%s] — delivered to ADMIN (fallback)", label)
                else:
                    log.critical(
                        "🚨 CRITICAL: Signal [%s] — BOTH channel AND admin FAILED. "
                        "Signal NOT delivered.",
                        label,
                    )
            else:
                log.critical(
                    "🚨 CRITICAL: Signal [%s] — channel failed, ADMIN_ID not configured. "
                    "Signal NOT delivered.",
                    label,
                )
    elif ADMIN_ID:
        admin_ok = _send_with_retry(ADMIN_ID, message)
        if admin_ok:
            log.info("✅ Signal [%s] — delivered to ADMIN (no channel)", label)
        else:
            log.critical(
                "🚨 CRITICAL: Signal [%s] — ADMIN delivery FAILED. Signal NOT delivered.",
                label,
            )


def notify_admin(text: str) -> None:
    """Best-effort admin notification. Never raises."""
    if not ADMIN_ID:
        return
    if not text or not text.strip():
        return
    try:
        _send_message(ADMIN_ID, text, parse_mode="")
    except Exception as exc:
        log.debug("notify_admin silenced error: %s", exc)
