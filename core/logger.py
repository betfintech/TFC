"""
core/logger.py — Logging setup (HARDENED)
==========================================
HARDENING CHANGES:
  - Log directory creation failure falls back to console-only logging
  - File handler setup errors are caught; console handler is always added
  - Invalid LOG_LEVEL falls back to INFO without crashing
  - RotatingFileHandler errors do not prevent system from starting
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "bot.log")
_FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Try to create log directory; non-fatal if it fails
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
    _LOG_DIR_OK = True
except OSError:
    _LOG_DIR_OK = False


def get_logger(name: str) -> logging.Logger:
    """Return a named logger wired to both file and console (console-only if file fails)."""
    # Deferred import to avoid circular import at module level
    try:
        from core.config import LOG_LEVEL
        level_str = LOG_LEVEL.upper()
    except Exception:
        level_str = "INFO"

    level = getattr(logging, level_str, logging.INFO)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    # ── Console handler — always present ──────────────────────────────────────
    try:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(formatter)
        logger.addHandler(ch)
    except Exception:
        pass  # Last resort — at least don't crash

    # ── Rotating file handler — best-effort ───────────────────────────────────
    if _LOG_DIR_OK:
        try:
            fh = RotatingFileHandler(
                _LOG_FILE,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            fh.setLevel(level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except OSError as exc:
            logger.warning("Could not open log file %s: %s — logging to console only", _LOG_FILE, exc)
    else:
        logger.warning("Log directory unavailable — logging to console only")

    logger.propagate = False
    return logger
