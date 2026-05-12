"""
chat/user_memory.py — Persistent user memory store
===================================================
Tracks per-user conversation history and metadata:
- New vs returning user detection
- Last conversation topic
- Message count and timestamps
- Display name
"""
from __future__ import annotations

import json
import os
import time
from threading import Lock
from typing import Optional

_MEMORY_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "user_memory.json")
_lock = Lock()
_cache: dict = {}
_loaded = False


def _load() -> dict:
    global _cache, _loaded
    if _loaded:
        return _cache
    try:
        with open(_MEMORY_FILE, "r") as f:
            _cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _cache = {}
    _loaded = True
    return _cache


def _save(data: dict) -> None:
    global _cache, _loaded
    try:
        os.makedirs(os.path.dirname(_MEMORY_FILE), exist_ok=True)
        with open(_MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
        _cache = data
    except Exception:
        pass


def is_new_user(user_id: int) -> bool:
    """Returns True if this user has never messaged before."""
    with _lock:
        data = _load()
        return str(user_id) not in data


def get_user_info(user_id: int) -> dict:
    """Get stored info about a user."""
    with _lock:
        data = _load()
        return dict(data.get(str(user_id), {}))


def record_message(
    user_id: int,
    username: str = "",
    first_name: str = "",
    last_topic: str = "",
) -> bool:
    """
    Record a user's message.
    Returns True if this is their very first message ever (new user).
    """
    with _lock:
        data = _load()
        uid = str(user_id)
        is_new = uid not in data
        now_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

        if is_new:
            data[uid] = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "first_seen": now_str,
                "last_seen": now_str,
                "last_topic": last_topic or "greeting",
                "message_count": 1,
            }
        else:
            data[uid]["last_seen"] = now_str
            data[uid]["message_count"] = data[uid].get("message_count", 0) + 1
            if username:
                data[uid]["username"] = username
            if first_name:
                data[uid]["first_name"] = first_name
            if last_topic:
                data[uid]["last_topic"] = last_topic

        _save(data)
        return is_new


def update_topic(user_id: int, topic: str) -> None:
    """Update the last conversation topic for a user."""
    with _lock:
        data = _load()
        uid = str(user_id)
        if uid in data:
            data[uid]["last_topic"] = topic
            _save(data)


def get_last_topic(user_id: int) -> Optional[str]:
    """Get the last conversation topic for a user."""
    with _lock:
        data = _load()
        return data.get(str(user_id), {}).get("last_topic")


def get_display_name(user_id: int) -> str:
    """Get the best available display name for a user."""
    with _lock:
        data = _load()
        user = data.get(str(user_id), {})
        if user.get("first_name"):
            return user["first_name"]
        if user.get("username"):
            return f"@{user['username']}"
        return f"User {user_id}"


def get_message_count(user_id: int) -> int:
    """Get total number of messages from a user."""
    with _lock:
        data = _load()
        return data.get(str(user_id), {}).get("message_count", 0)
          
