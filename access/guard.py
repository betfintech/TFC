from __future__ import annotations

from payment import storage


def is_allowed(user_id: int) -> bool:
    """Return True if user has an active subscription."""
    return storage.is_subscriber(user_id)


def require_subscription_message() -> str:
    from core.config import PAYMENT_AMOUNT, SUBSCRIPTION_DAYS
    return (
        "🔒 *Access Restricted*\n\n"
        f"This content is for subscribers only.\n\n"
        f"💳 Subscribe for ₦{PAYMENT_AMOUNT:,} / {SUBSCRIPTION_DAYS} days\n\n"
        f"Type /pay to get payment details."
    )
