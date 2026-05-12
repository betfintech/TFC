from __future__ import annotations

import requests
from core.config import TELEGRAM_BOT_TOKEN
from core.logger import get_logger

log = get_logger(__name__)
_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_chat_message(chat_id: int, text: str) -> None:
    try:
        requests.post(
            f"{_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        log.error("Chat send error to %s: %s", chat_id, exc)
