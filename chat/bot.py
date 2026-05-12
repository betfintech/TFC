"""
chat/bot.py — Unified Telegram Polling Bot
==========================================
Features:
- Single getUpdates polling loop (no dual-poller conflict)
- Admin recognition: bot knows and greets admin specially
- User memory: detects new vs returning users, remembers last topic
- Image/photo handling: routes screenshots to payment system for AI analysis
- All text messages handled by OpenRouter AI only (no mixed static fallbacks)
"""
from __future__ import annotations

import time
import requests

from core.config import TELEGRAM_BOT_TOKEN, ADMIN_ID
from core.logger import get_logger
from chat.openrouter_brain import get_response
from chat.utils import send_chat_message
from chat.user_memory import is_new_user, record_message, get_user_info, get_last_topic

log = get_logger(__name__)
_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

_PAYMENT_COMMANDS = {"/pay", "/subscribe", "/payment", "/approve", "/reject", "/pending"}
_KNOWN_COMMANDS = {"/start"}

_UNKNOWN_COMMAND_REPLY = (
    "🤔 I don't recognise that command.\n\n"
    "Here's what I can help with:\n"
    "• /start — Welcome message\n"
    "• /pay — Subscribe to signals\n\n"
    "Or just ask me anything about our trading signals, markets, or pricing!"
)

_ADMIN_WELCOME = (
    "👑 *Welcome back, Admin!*\n\n"
    "Here are your available commands:\n"
    "• /pending — View all pending payment submissions\n"
    "• /approve <user\\_id> — Approve a payment and send invite link\n"
    "• /reject <user\\_id> — Reject a payment submission\n\n"
    "All payment screenshots from users are forwarded to you automatically for review.\n"
    "The system is running and monitoring markets 24/7. ✅"
)


def _safe_get(d: dict, *keys, default=None):
    """Safely traverse nested dicts without KeyError."""
    try:
        val = d
        for k in keys:
            val = val[k]
        return val
    except (KeyError, TypeError, IndexError):
        return default


def _download_photo(file_id: str) -> bytes | None:
    """Download a Telegram photo by file_id. Returns raw bytes or None."""
    try:
        resp = requests.get(
            f"{_API}/getFile",
            params={"file_id": file_id},
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            log.warning("getFile failed for file_id %s: %s", file_id, data)
            return None

        file_path = _safe_get(data, "result", "file_path")
        if not file_path:
            return None

        dl_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        img_resp = requests.get(dl_url, timeout=15)
        if img_resp.status_code == 200:
            return img_resp.content
        log.warning("Photo download failed: HTTP %s", img_resp.status_code)
        return None
    except Exception as exc:
        log.error("_download_photo error: %s", exc)
        return None


def _handle_photo_message(msg: dict, chat_id: int, user_id: int, username: str, caption: str) -> None:
    """
    Handle a photo/image message. Routes to payment system for AI screenshot analysis.
    """
    try:
        photos = _safe_get(msg, "photo") or []
        if not photos:
            send_chat_message(chat_id, "📷 I received your image but couldn't process it. Please try again.")
            return

        largest = max(photos, key=lambda p: p.get("file_size", 0))
        file_id = largest.get("file_id")

        if not file_id:
            send_chat_message(chat_id, "📷 Could not retrieve your image. Please try again.")
            return

        log.info("Photo received from user %s, downloading...", user_id)
        img_bytes = _download_photo(file_id)

        if not img_bytes:
            send_chat_message(
                chat_id,
                "⚠️ I received your image but couldn't download it. "
                "Please try sending it again or contact admin.",
            )
            return

        # Route to payment system for AI analysis
        try:
            from payment.system import handle_photo
            handle_photo(chat_id, user_id, username, img_bytes, caption)
        except Exception as exc:
            log.error("handle_photo error: %s", exc, exc_info=True)
            send_chat_message(
                chat_id,
                "⚠️ Could not process your image. Please try again or contact admin.",
            )

    except Exception as exc:
        log.error("_handle_photo_message error: %s", exc, exc_info=True)


def _handle_chat_update(update: dict) -> None:
    """Handle a single update for the chat bot."""
    try:
        msg = _safe_get(update, "message")
        if not msg or not isinstance(msg, dict):
            return

        chat_id    = _safe_get(msg, "chat", "id")
        text       = (_safe_get(msg, "text") or "").strip()
        caption    = (_safe_get(msg, "caption") or "").strip()
        user_id    = _safe_get(msg, "from", "id") or chat_id
        chat_type  = _safe_get(msg, "chat", "type") or "private"
        username   = _safe_get(msg, "from", "username") or ""
        first_name = _safe_get(msg, "from", "first_name") or ""

        if not chat_id:
            return

        if chat_type != "private":
            return

        # ── Photo / image messages ────────────────────────────────────────────
        if _safe_get(msg, "photo"):
            record_message(user_id, username, first_name, last_topic="payment_screenshot")
            _handle_photo_message(msg, chat_id, user_id, username, caption)
            return

        # Payment commands routed separately
        if text and any(text.lower().startswith(cmd) for cmd in _PAYMENT_COMMANDS):
            return

        # ── Admin recognition ─────────────────────────────────────────────────
        if ADMIN_ID and user_id == ADMIN_ID:
            if text == "/start":
                send_chat_message(chat_id, _ADMIN_WELCOME)
                record_message(user_id, username, first_name, last_topic="admin_start")
                return
            # Admin messages: AI with admin context
            try:
                record_message(user_id, username, first_name)
                response = get_response(text, user_id, is_admin=True)
                if response and response.strip():
                    send_chat_message(chat_id, response)
            except Exception as exc:
                log.error("Admin brain error: %s", exc)
                send_chat_message(
                    chat_id,
                    "⚠️ AI temporarily unavailable.\n"
                    "Use /pending, /approve <id>, /reject <id> to manage payments.",
                )
            return

        # ── New vs returning user detection ───────────────────────────────────
        is_brand_new = is_new_user(user_id)
        record_message(user_id, username, first_name)

        # /start command
        if text == "/start":
            if is_brand_new:
                greet = (
                    f"👋 *Welcome{', ' + first_name if first_name else ''}!*\n\n"
                    "I'm your trading signal assistant. I can explain how our platform works, "
                    "answer your questions, and help you subscribe.\n\n"
                    "What would you like to know? 😊"
                )
            else:
                info = get_user_info(user_id)
                last_topic = info.get("last_topic", "")
                msg_count = info.get("message_count", 0)
                greet = (
                    f"👋 *Welcome back{', ' + first_name if first_name else ''}!*\n\n"
                    f"Great to see you again! You've chatted with us {msg_count} time(s) before.\n"
                )
                if last_topic and last_topic not in ("greeting", "admin_start", "payment_screenshot"):
                    greet += f"Last time you were asking about *{last_topic.replace('_', ' ')}*.\n\n"
                else:
                    greet += "\n"
                greet += "How can I help you today? 😊"
            send_chat_message(chat_id, greet)
            return

        # Unknown slash commands
        if text and text.startswith("/") and text not in _KNOWN_COMMANDS:
            send_chat_message(chat_id, _UNKNOWN_COMMAND_REPLY)
            return

        # Empty message
        if not text:
            send_chat_message(
                chat_id,
                "👋 I'm here! Ask me about our trading signals, pricing, markets, "
                "or how to subscribe.\n\nOr type /pay to get started.",
            )
            return

        # ── All other messages: AI only ───────────────────────────────────────
        try:
            last_topic = get_last_topic(user_id) if not is_brand_new else None
            response = get_response(text, user_id, is_new=is_brand_new, last_topic=last_topic)
            if not response or not response.strip():
                raise ValueError("empty response")
            send_chat_message(chat_id, response)
        except Exception as exc:
            log.error("Brain response error for user %s: %s", user_id, exc)
            send_chat_message(
                chat_id,
                "I'm experiencing a brief technical issue. "
                "Please try again in a moment, or type /pay to subscribe.",
            )

    except Exception as exc:
        log.error("ChatBot _handle_chat_update unexpected error: %s", exc, exc_info=True)


def _dispatch_update(update: dict) -> None:
    """
    Route a single Telegram update to the correct handler.
    """
    try:
        msg = _safe_get(update, "message")
        if not msg:
            return

        text      = (_safe_get(msg, "text") or "").strip().lower()
        chat_type = _safe_get(msg, "chat", "type") or "private"

        if chat_type != "private":
            return

        # Route payment commands to payment handler
        if text and any(text.startswith(cmd) for cmd in _PAYMENT_COMMANDS):
            try:
                from payment.system import handle_update as payment_handle
                payment_handle(update)
            except Exception as exc:
                log.error("Payment handler error: %s", exc, exc_info=True)
            return

        # Everything else (including photos) → chat handler
        _handle_chat_update(update)

    except Exception as exc:
        log.error("_dispatch_update error: %s", exc, exc_info=True)


class ChatBot:
    """
    Unified Telegram long-poll listener.
    Handles text, photos, and payment commands in a single getUpdates loop.
    """

    def run(self) -> None:
        log.info("Unified Telegram bot started (chat + payment + photo handling).")
        offset = [0]

        while True:
            try:
                resp = requests.get(
                    f"{_API}/getUpdates",
                    params={
                        "offset": offset[0],
                        "timeout": 30,
                        "allowed_updates": ["message"],
                    },
                    timeout=40,
                )

                try:
                    data = resp.json()
                except ValueError:
                    log.warning(
                        "ChatBot: non-JSON response (HTTP %s): %s",
                        resp.status_code, resp.text[:200],
                    )
                    time.sleep(5)
                    continue

                updates = data.get("result", [])
                if not isinstance(updates, list):
                    log.warning("ChatBot: unexpected 'result' type: %s", type(updates))
                    time.sleep(2)
                    continue

                for update in updates:
                    try:
                        update_id = _safe_get(update, "update_id", default=0)
                        offset[0] = update_id + 1
                        _dispatch_update(update)
                    except Exception as exc:
                        log.error("ChatBot update handler error: %s", exc, exc_info=True)

            except requests.exceptions.Timeout:
                continue
            except requests.exceptions.ConnectionError as exc:
                log.warning("ChatBot connection error: %s — retrying in 5s", exc)
                time.sleep(5)
            except Exception as exc:
                log.error("ChatBot polling error: %s", exc, exc_info=True)
                time.sleep(5)
                  
