"""
execution.py — PERMANENTLY DISABLED
=====================================
This module exists for structural completeness ONLY.

THIS SYSTEM IS A SIGNAL GENERATOR.
No trades are ever executed.
No orders are ever placed.
No real money is ever moved.

All functions in this module are no-ops and log a warning if somehow called.
"""
from core.logger import get_logger

log = get_logger(__name__)

_DISABLED_MSG = (
    "EXECUTION IS DISABLED. This is a signal-only system. "
    "No trades will be placed."
)


def execute_trade(*args, **kwargs) -> None:
    log.warning(_DISABLED_MSG)


def place_order(*args, **kwargs) -> None:
    log.warning(_DISABLED_MSG)


def cancel_order(*args, **kwargs) -> None:
    log.warning(_DISABLED_MSG)


def close_position(*args, **kwargs) -> None:
    log.warning(_DISABLED_MSG)
