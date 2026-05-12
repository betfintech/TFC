"""
payment/manager.py — Payment approval logic (PRODUCTION-GRADE)
===============================================================
HARDENING v2:
  - approve(): checks if user is already a subscriber — skips if so (no duplicate approval)
  - approve(): validates user exists in pending before approving
  - approve(): never crashes on invalid/None user_id
  - reject(): validates user exists in pending before rejecting
  - reject(): never crashes on invalid/None user_id
  - All actions emit rich logs: attempt, outcome, rejection reason
  - submit_payment_proof(): validates user_id and proof before storing
"""
from __future__ import annotations

from core.config import SUBSCRIPTION_DAYS
from core.logger import get_logger
from payment import storage

log = get_logger(__name__)


def submit_payment_proof(user_id: int, username: str, proof: str) -> None:
    """Record a user's payment proof. Validates inputs before storing."""
    try:
        uid = int(user_id)
        if uid <= 0:
            log.warning("submit_payment_proof: invalid user_id=%s — skipped", user_id)
            return
    except (TypeError, ValueError):
        log.warning("submit_payment_proof: non-integer user_id=%r — skipped", user_id)
        return

    if not proof or not str(proof).strip():
        log.warning("submit_payment_proof: empty proof from user %s — skipped", uid)
        return

    storage.add_pending(uid, username, proof)
    log.info("Payment proof submitted — user_id=%s username=%s", uid, username or "unknown")


def approve(user_id: int) -> None:
    """
    Approve a subscriber.

    Guards:
      - Invalid/None user_id → logged and skipped
      - Already a subscriber → skipped (no duplicate approval)
      - Not in pending → logged as rejection reason and skipped
    """
    # Validate user_id
    try:
        uid = int(user_id)
        if uid <= 0:
            raise ValueError("non-positive")
    except (TypeError, ValueError):
        log.warning("approve: invalid user_id=%r — skipped", user_id)
        return

    log.info("approve: attempting approval for user_id=%s", uid)

    # Guard: already a subscriber → do not double-approve
    try:
        if storage.is_subscriber(uid):
            log.warning(
                "approve: user_id=%s is already an active subscriber — approval skipped",
                uid,
            )
            return
    except Exception as exc:
        log.error("approve: subscriber check failed for user_id=%s: %s", uid, exc)

    # Guard: must exist in pending
    try:
        pending = storage.get_pending()
        if str(uid) not in pending:
            log.warning(
                "approve: user_id=%s not found in pending payments — approval rejected "
                "(reason: no payment proof on record)",
                uid,
            )
            return
    except Exception as exc:
        log.error("approve: could not read pending list for user_id=%s: %s", uid, exc)

    # Perform approval
    try:
        storage.approve_subscriber(uid, SUBSCRIPTION_DAYS)
        log.info(
            "approve: ✅ user_id=%s approved for %d days",
            uid, SUBSCRIPTION_DAYS,
        )
    except Exception as exc:
        log.error("approve: storage error while approving user_id=%s: %s", uid, exc)


def reject(user_id: int) -> None:
    """
    Reject a pending subscriber.

    Guards:
      - Invalid/None user_id → logged and skipped
      - Not in pending → logged with reason and skipped
    """
    try:
        uid = int(user_id)
        if uid <= 0:
            raise ValueError("non-positive")
    except (TypeError, ValueError):
        log.warning("reject: invalid user_id=%r — skipped", user_id)
        return

    log.info("reject: attempting rejection for user_id=%s", uid)

    try:
        pending = storage.get_pending()
        if str(uid) not in pending:
            log.warning(
                "reject: user_id=%s not in pending — nothing to reject",
                uid,
            )
            return
    except Exception as exc:
        log.error("reject: could not read pending for user_id=%s: %s", uid, exc)

    try:
        storage.reject_pending(uid)
        log.info("reject: user_id=%s removed from pending", uid)
    except Exception as exc:
        log.error("reject: storage error while rejecting user_id=%s: %s", uid, exc)


def check(user_id: int) -> bool:
    """Check if a user has an active subscription. Safe against invalid input."""
    try:
        uid = int(user_id)
        if uid <= 0:
            return False
        return storage.is_subscriber(uid)
    except (TypeError, ValueError):
        return False
    except Exception as exc:
        log.error("check: unexpected error for user_id=%r: %s", user_id, exc)
        return False
