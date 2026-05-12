"""
core/utils.py — Shared utilities (HARDENED)
============================================
All helpers are purely defensive — no exceptions should escape.

HARDENING CHANGES:
  - safe_float extended to handle None, NaN, Inf explicitly
  - safe_int added
  - atomic_write_json: guaranteed crash-safe JSON file writing
  - load_json_safe: guaranteed safe JSON file reading with fallback
  - All functions log errors; none raise
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("core.utils")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ts_ms() -> int:
    """Current UTC timestamp in milliseconds."""
    return int(time.time() * 1000)


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Convert value to float safely.
    Returns default for None, NaN, Inf, or unconvertible values.
    """
    try:
        result = float(value)
        if not math.isfinite(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """Convert value to int safely. Returns default on any error."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pct_change(a: float, b: float) -> float:
    """Percentage change from a to b. Returns 0.0 if a is zero."""
    if not a or not math.isfinite(a) or not math.isfinite(b):
        return 0.0
    return (b - a) / a


def is_crypto_symbol(symbol: str) -> bool:
    """Detect crypto by absence of slash and presence of USDT/BTC/ETH suffix."""
    if not symbol or not isinstance(symbol, str):
        return False
    return "/" not in symbol and any(
        symbol.upper().endswith(s) for s in ("USDT", "BTC", "ETH", "BNB", "BUSD")
    )


def format_price(price: float, decimals: int = 5) -> str:
    try:
        return f"{price:.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def clamp(value: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, value))
    except (TypeError, ValueError):
        return lo


def atomic_write_json(path: str, data: Any, indent: int = 2) -> bool:
    """
    Write JSON to a file atomically using a temp file + os.replace().

    Guarantees:
      - File is never left partially written
      - Existing file is not touched if write fails
      - Directory is created if it does not exist

    Returns True on success, False on failure.
    """
    try:
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        # Write to a temp file in the same directory (guarantees same filesystem)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            return True
        except Exception:
            # Clean up temp file if rename failed
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        _log.error("atomic_write_json failed for %s: %s", path, exc)
        return False


def load_json_safe(path: str, default: Any = None) -> Any:
    """
    Read and parse a JSON file safely.

    Returns default if:
      - File does not exist
      - File is empty
      - JSON is malformed / corrupted
      - Permission denied
    """
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            _log.warning("load_json_safe: %s is empty — using default", path)
            return default
        return json.loads(content)
    except json.JSONDecodeError as exc:
        _log.warning("load_json_safe: %s is corrupted (%s) — using default", path, exc)
        return default
    except OSError as exc:
        _log.warning("load_json_safe: cannot read %s (%s) — using default", path, exc)
        return default
    except Exception as exc:
        _log.error("load_json_safe: unexpected error reading %s: %s", path, exc)
        return default
