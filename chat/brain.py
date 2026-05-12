"""
brain.py — EMERGENCY FALLBACK ONLY
====================================
This module is ONLY used when:
1. OPENROUTER_API_KEY is not set, AND
2. The openrouter_brain module cannot be imported

In normal operation, openrouter_brain.py handles ALL responses.
This file should NEVER be called unless the primary AI is completely unavailable.

Static template responses here are intentionally minimal.
"""
from __future__ import annotations

import logging
from core.logger import get_logger

log = get_logger("chat.brain")


def get_response(user_text: str, user_id: int, file_info: dict = None) -> str:
    """
    Emergency fallback response. Called ONLY when OpenRouter AI is unavailable.
    Returns a helpful message directing users to available actions.
    """
    log.warning("chat.brain fallback called for user %s (AI unavailable)", user_id)

    if file_info:
        return (
            "✅ *Image received.*\n\n"
            "Our admin will review your submission shortly.\n"
            "You'll be notified once your subscription is approved."
        )

    text = (user_text or "").strip().lower()

    # Minimal intent matching for common queries when AI is down
    if any(w in text for w in ["pay", "subscribe", "price", "cost", "how much"]):
        return (
            "💳 *Subscription: ₦10,000 / 30 days*\n\n"
            "Bank: Moniepoint | Account: 6576999590 | Name: Isreal Bethel Ojotule\n\n"
            "Type /pay to submit your payment screenshot."
        )

    if any(w in text for w in ["signal", "how", "work", "what", "strategy", "smc"]):
        return (
            "📊 We use *Smart Money Concepts (SMC)* analysis across Crypto & Forex.\n\n"
            "Every signal has Entry, Stop Loss, TP1 (1:2 R:R), TP2, and Final TP.\n\n"
            "Type /pay to subscribe and receive live signals."
        )

    return (
        "⚠️ *AI assistant is temporarily offline.*\n\n"
        "• Type /pay — Subscription details\n"
        "• Type /start — Restart the bot\n\n"
        "We'll be back shortly!"
    )
  
