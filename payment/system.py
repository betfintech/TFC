"""
payment/system.py — Enhanced Payment Flow with AI Vision
=========================================================
Features:
- AI-powered payment screenshot analysis (OpenRouter vision)
- Detects if image is actually a payment receipt
- Verifies amount matches required ₦10,000
- Rejects invalid screenshots with clear explanation
- Admin recognition and enhanced notifications
- Subscriber history checking
"""
from __future__ import annotations

import base64
import time
import requests
from datetime import datetime, timezone

from core.config import (
    TELEGRAM_BOT_TOKEN,
    ADMIN_ID,
    CHANNEL_ID,
    PAYMENT_BANK,
    PAYMENT_ACCOUNT,
    PAYMENT_NAME,
    PAYMENT_AMOUNT,
    SUBSCRIPTION_DAYS,
)
from core.logger import get_logger
from payment import storage, manager

log = get_logger(__name__)

_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
_PENDING_PROOF: dict[int, bool] = {}


def _send(chat_id: int, text: str, **kwargs) -> None:
    """Send a Telegram message. Silent on failure."""
    if not chat_id or not text or not text.strip():
        return
    try:
        requests.post(
            f"{_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", **kwargs},
            timeout=10,
        )
    except Exception as exc:
        log.error("Payment _send error to %s: %s", chat_id, exc)


def _safe_get(d: dict, *keys, default=None):
    try:
        val = d
        for k in keys:
            val = val[k]
        return val
    except (KeyError, TypeError, IndexError):
        return default


def _check_subscription_history(user_id: int) -> dict:
    """Check if user has subscription history."""
    try:
        pending = storage.get_pending()
        if isinstance(pending, dict) and str(user_id) in pending:
            return {
                "is_subscriber": False,
                "subscription_status": "PENDING_APPROVAL",
                "last_subscription": "Waiting for admin approval",
            }
        all_subs = storage.get_all_subscribers()
        if isinstance(all_subs, list):
            for sub in all_subs:
                if isinstance(sub, dict) and sub.get("user_id") == user_id:
                    expiry = sub.get("expiry_date", "unknown")
                    status = sub.get("status", "active")
                    return {
                        "is_subscriber": True,
                        "subscription_status": status.upper(),
                        "last_subscription": f"Until {expiry}",
                    }
        return {
            "is_subscriber": False,
            "subscription_status": "NEW_USER",
            "last_subscription": "No previous subscription",
        }
    except Exception as exc:
        log.error("Error checking subscription history: %s", exc)
        return {"is_subscriber": False, "subscription_status": "UNKNOWN", "last_subscription": ""}


def _create_invite_link() -> str:
    """Generate a one-time Telegram channel invite link."""
    if not CHANNEL_ID:
        log.error("_create_invite_link: CHANNEL_ID not set")
        return "⚠️ Could not generate link. Contact admin."
    try:
        resp = requests.post(
            f"{_API}/createChatInviteLink",
            json={"chat_id": CHANNEL_ID, "member_limit": 1, "creates_join_request": False},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            link = _safe_get(data, "result", "invite_link")
            if link:
                return link
        log.error("_create_invite_link: %s", data.get("description", "unknown"))
    except Exception as exc:
        log.error("Could not create invite link: %s", exc)
    return "⚠️ Could not generate link. Contact admin."


def handle_photo(chat_id: int, user_id: int, username: str, img_bytes: bytes, caption: str = "") -> None:
    """
    Handle an incoming payment screenshot photo.
    Uses AI vision to:
    1. Verify it is actually a payment screenshot
    2. Check the transfer amount matches ₦PAYMENT_AMOUNT
    3. Either accept and forward to admin, or reject with explanation
    """
    log.info("Analyzing payment screenshot from user %s (%d bytes)", user_id, len(img_bytes))

    # Show immediate acknowledgment while AI processes
    _send(chat_id, "🔍 Analyzing your image, please wait a moment...")

    try:
        from chat.openrouter_brain import analyze_payment_image
        result = analyze_payment_image(img_bytes)
    except Exception as exc:
        log.error("Image analysis failed: %s", exc)
        result = {
            "is_payment_screenshot": True,
            "amount_detected": None,
            "amount_sufficient": None,
            "description": "Could not analyze (admin will review manually)",
        }

    is_payment = result.get("is_payment_screenshot", False)
    amount_detected = result.get("amount_detected")
    amount_sufficient = result.get("amount_sufficient")
    description = result.get("description", "")

    # ── Not a payment screenshot ──────────────────────────────────────────────
    if not is_payment:
        _send(
            chat_id,
            f"❌ *This doesn't look like a payment screenshot.*\n\n"
            f"{('I can see: _' + description + '_\n\n') if description else ''}"
            f"Please send a *screenshot of your bank transfer receipt* "
            f"showing that you transferred *₦{PAYMENT_AMOUNT:,}* to:\n\n"
            f"🏦 Bank: *{PAYMENT_BANK}*\n"
            f"📋 Account: `{PAYMENT_ACCOUNT}`\n"
            f"👤 Name: *{PAYMENT_NAME}*\n\n"
            f"Once you've sent the payment, send the transfer confirmation screenshot here.",
        )
        log.info("Rejected non-payment image from user %s: %s", user_id, description)
        return

    # ── Payment screenshot but amount too low ────────────────────────────────
    if amount_detected is not None and amount_sufficient is False:
        _send(
            chat_id,
            f"⚠️ *Payment amount is too low.*\n\n"
            f"Amount I detected in your screenshot: *₦{amount_detected:,}*\n"
            f"Required amount: *₦{PAYMENT_AMOUNT:,}*\n\n"
            f"Please make a new transfer of *₦{PAYMENT_AMOUNT:,}* to:\n\n"
            f"🏦 Bank: *{PAYMENT_BANK}*\n"
            f"📋 Account: `{PAYMENT_ACCOUNT}`\n"
            f"👤 Name: *{PAYMENT_NAME}*\n\n"
            f"Then send the new receipt screenshot here.",
        )
        log.info(
            "Rejected underpayment from user %s: detected ₦%s, required ₦%s",
            user_id, amount_detected, PAYMENT_AMOUNT,
        )
        return

    # ── Valid payment screenshot ──────────────────────────────────────────────
    proof_note = (
        f"Amount: ₦{amount_detected:,}" if amount_detected
        else caption or "[Screenshot received]"
    )

    try:
        manager.submit_payment_proof(user_id, username, proof_note)
    except Exception as exc:
        log.error("submit_payment_proof failed for user %s: %s", user_id, exc)
        _send(chat_id, "⚠️ Could not record your proof. Please try again or contact admin.")
        return

    _send(
        chat_id,
        f"✅ *Payment screenshot received and verified!*\n\n"
        f"{'Amount detected: *₦' + str(amount_detected) + ',000*' + chr(10) if amount_detected else ''}"
        f"Our admin will review and approve your subscription shortly.\n"
        f"You'll receive your private channel invite once approved. ✨",
    )

    # Notify admin
    if ADMIN_ID:
        admin_msg = (
            f"📥 *New Payment Screenshot*\n\n"
            f"User: @{username} (`{user_id}`)\n"
        )
        if amount_detected:
            admin_msg += f"Amount Detected: *₦{amount_detected:,}*\n"
            admin_msg += f"Amount Sufficient: {'✅ Yes' if amount_sufficient else '⚠️ Below requirement'}\n"
        if description:
            admin_msg += f"AI Note: _{description}_\n"
        admin_msg += (
            f"\n→ /approve {user_id}\n"
            f"→ /reject {user_id}"
        )
        _send(ADMIN_ID, admin_msg)

    log.info("Payment screenshot accepted from user %s, amount=%s", user_id, amount_detected)


def handle_update(update: dict) -> None:
    """
    Entry point called by the unified ChatBot polling loop.
    Processes payment-related Telegram messages (text commands only).
    Photos are handled directly by chat/bot.py via handle_photo().
    """
    try:
        msg = _safe_get(update, "message") or _safe_get(update, "callback_query", "message")
        if not msg or not isinstance(msg, dict):
            return

        chat_id  = _safe_get(msg, "chat", "id")
        text     = (_safe_get(msg, "text") or "").strip()
        caption  = (_safe_get(msg, "caption") or "").strip()
        username = _safe_get(msg, "from", "username") or "unknown"
        user_id  = _safe_get(msg, "from", "id") or chat_id

        if not chat_id:
            return

        # ── Admin commands ────────────────────────────────────────────────────
        if user_id == ADMIN_ID:
            if text.startswith("/approve"):
                parts = text.split()
                if len(parts) == 2:
                    try:
                        target_id = int(parts[1])
                    except ValueError:
                        _send(chat_id, "Usage: /approve <user\\_id>")
                        return
                    try:
                        manager.approve(target_id)
                        invite = _create_invite_link()
                        _send(chat_id, f"✅ User `{target_id}` approved.")
                        _send(
                            target_id,
                            f"🎉 *Your subscription is now active!*\n\n"
                            f"Join the private signals channel:\n{invite}\n\n"
                            f"⚠️ This invite link is one-time use only. Do not share it.",
                        )
                        log.info("Admin approved user %s", target_id)
                    except Exception as exc:
                        log.error("approve failed for %s: %s", target_id, exc)
                        _send(chat_id, f"⚠️ Error approving `{target_id}`. Check logs.")
                else:
                    _send(chat_id, "Usage: /approve <user\\_id>")
                return

            if text.startswith("/reject"):
                parts = text.split()
                if len(parts) == 2:
                    try:
                        target_id = int(parts[1])
                    except ValueError:
                        _send(chat_id, "Usage: /reject <user\\_id>")
                        return
                    try:
                        manager.reject(target_id)
                        _send(chat_id, f"❌ User `{target_id}` rejected.")
                        _send(
                            target_id,
                            "❌ *Your payment could not be verified.*\n\n"
                            "The screenshot you submitted was not accepted.\n"
                            "Please ensure you:\n"
                            "1. Transferred the correct amount (₦10,000)\n"
                            "2. Sent a clear screenshot of the confirmation\n\n"
                            "Type /pay to try again.",
                        )
                        log.info("Admin rejected user %s", target_id)
                    except Exception as exc:
                        log.error("reject failed for %s: %s", target_id, exc)
                        _send(chat_id, f"⚠️ Error rejecting `{target_id}`. Check logs.")
                else:
                    _send(chat_id, "Usage: /reject <user\\_id>")
                return

            if text == "/pending":
                try:
                    pending = storage.get_pending()
                    if not pending:
                        _send(chat_id, "✅ No pending payment submissions.")
                    else:
                        lines = ["*⏳ Pending Submissions:*\n"]
                        for uid, info in list(pending.items()):
                            if not isinstance(info, dict):
                                continue
                            lines.append(
                                f"• ID: `{uid}` | @{info.get('username', '?')}\n"
                                f"  Proof: _{str(info.get('proof', ''))[:200]}_\n"
                                f"  Submitted: {info.get('submitted', '?')}\n"
                                f"  → /approve {uid}  |  /reject {uid}\n"
                            )
                        _send(chat_id, "\n".join(lines))
                except Exception as exc:
                    log.error("Error fetching pending list: %s", exc)
                    _send(chat_id, "⚠️ Error fetching pending list.")
                return

        # ── User payment flow ─────────────────────────────────────────────────
        if text in ("/pay", "/subscribe", "/payment"):
            history = _check_subscription_history(user_id)

            status_msg = ""
            if history["is_subscriber"]:
                status_msg = (
                    f"\n\nℹ️ *Your Status:* {history['subscription_status']}\n"
                    f"{history['last_subscription']}\n"
                )
            elif history["subscription_status"] == "PENDING_APPROVAL":
                status_msg = "\n\n⏳ *Your previous submission is still pending admin review.*\n"
            else:
                status_msg = "\n"

            _send(
                chat_id,
                f"💳 *Payment Details*\n\n"
                f"🏦 Bank: *{PAYMENT_BANK}*\n"
                f"📋 Account: `{PAYMENT_ACCOUNT}`\n"
                f"👤 Name: *{PAYMENT_NAME}*\n"
                f"💰 Amount: *₦{PAYMENT_AMOUNT:,}*\n"
                f"📅 Duration: *{SUBSCRIPTION_DAYS} days*\n"
                f"{status_msg}\n"
                f"📸 *How to subscribe:*\n"
                f"1️⃣ Transfer *₦{PAYMENT_AMOUNT:,}* to the account above\n"
                f"2️⃣ Send a *screenshot* of your transfer confirmation here\n"
                f"3️⃣ Admin reviews and sends your exclusive invite link ✅\n\n"
                f"_Make sure your screenshot clearly shows the transfer amount and recipient._",
            )
            _PENDING_PROOF[user_id] = True
            return

        # ── Text proof (manual reference number) ─────────────────────────────
        if _PENDING_PROOF.get(user_id) and text:
            _PENDING_PROOF.pop(user_id, None)
            try:
                manager.submit_payment_proof(user_id, username, text)
                _send(
                    chat_id,
                    "✅ *Proof received!*\n\n"
                    "Admin will review and approve your subscription shortly.\n"
                    "You'll receive your channel invite once approved.",
                )
                if ADMIN_ID:
                    _send(
                        ADMIN_ID,
                        f"📥 *New Payment Proof (Text)*\n\n"
                        f"User: @{username} (`{user_id}`)\n"
                        f"Reference: _{text[:500]}_\n\n"
                        f"→ /approve {user_id}\n"
                        f"→ /reject {user_id}",
                    )
            except Exception as exc:
                log.error("submit_payment_proof failed for user %s: %s", user_id, exc)
                _send(chat_id, "⚠️ Could not record your proof. Please try again or contact admin.")
            return

    except Exception as exc:
        log.error("handle_update unexpected error: %s", exc, exc_info=True)


class PaymentSystem:
    """
    Keepalive stub — real work is done via handle_update() called from chat/bot.py.
    """

    def run(self) -> None:
        log.info("PaymentSystem keepalive started. Payment messages handled by unified ChatBot loop.")
        while True:
            time.sleep(300)

