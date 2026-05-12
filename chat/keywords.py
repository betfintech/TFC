"""
keywords.py — Intent detection for the conversational chatbot.

Loose matching: user input is lowercased, stripped, and checked for
substring presence. Specific intents checked before generic ones to
avoid false matches.

Never matches signal/trade data — bot only handles conversational intents.
"""
from __future__ import annotations

INTENT_MAP: dict[str, list[str]] = {
    "greeting": [
        "hi", "hello", "hey", "hiya", "good morning", "good afternoon",
        "good evening", "good night", "sup", "wassup", "what's up",
        "howdy", "greetings", "yo ", "salut", "ola", "hola",
        "morning", "evening", "afternoon",
    ],
    "subscribe": [
        "subscribe", "subscription", "pay", "payment", "join",
        "access", "price", "cost", "how much", "fee", "fees", "plan",
        "package", "get started", "sign up", "sign-up", "enroll",
        "register", "buy", "purchase", "want to join", "how to join",
        "want access", "get access", "become a member", "pricing",
        "monthly", "weekly", "deposit", "transfer", "bank", "account",
        "how do i pay", "how to pay", "payment details",
    ],
    "signals": [
        "signal", "signals", "alert", "alerts", "setup", "setups",
        "notify", "notification", "trade alert", "when signal",
        "next signal", "any signals", "got a signal", "send signal",
        "signal today", "today signal",
    ],
    "results": [
        "result", "results", "performance", "profit", "profitable",
        "win rate", "accuracy", "track record", "success", "history",
        "backtest", "return", "pnl", "p&l", "how good",
        "how accurate", "past trades", "previous trades",
        "has it worked", "does it work", "is it good",
    ],
    "markets": [
        "market", "markets", "crypto", "forex", "bitcoin", "btc", "eth",
        "ethereum", "solana", "sol", "eur", "gbp", "usd", "jpy",
        "pairs", "currencies", "assets", "instruments", "coins",
        "gold", "xau", "nasdaq", "what pairs", "which pairs",
        "which markets", "what markets", "what currencies",
    ],
    "strategy": [
        "smc", "smart money", "strategy", "how does it work",
        "order block", "order blocks", "fvg", "fair value gap",
        "liquidity", "choch", "bos", "break of structure",
        "change of character", "premium", "discount", "bias",
        "confluence", "multi timeframe", "htf", "ltf",
    ],
    "help": [
        "help", "support", "contact", "admin", "issue", "problem",
        "not working", "broken", "error", "stuck", "confused", "lost",
        "can't access", "cannot access", "no access", "still waiting",
        "approved", "not approved", "when will", "invite link",
    ],
    "how_it_works": [
        "how", "what is", "what are", "explain", "tell me",
        "describe", "work", "works", "system", "platform", "bot",
        "trade", "trading", "about", "overview", "intro", "introduction",
        "automated", "automatic", "ai", "algorithm",
    ],
    "risk": [
        "risk", "stop loss", "sl", "take profit", "tp", "risk reward",
        "rr", "r:r", "1:2", "1:3", "drawdown", "lose", "loss",
        "safe", "safety", "how much risk", "capital", "how much to start",
    ],
}

_PRIORITY = [
    "greeting", "subscribe", "signals", "results",
    "markets", "strategy", "risk", "help", "how_it_works",
]


def detect_intent(text: str) -> str:
    """
    Detect user intent from free-form text.
    Returns one of the keys in INTENT_MAP, or 'unknown'.
    """
    lower = text.lower().strip()
    if not lower:
        return "greeting"

    for intent in _PRIORITY:
        for kw in INTENT_MAP[intent]:
            if kw in lower:
                return intent

    # Fuzzy fallback: if the message is very short (1-2 words),
    # treat it as a greeting to keep conversation alive
    if len(lower.split()) <= 2:
        return "greeting"

    return "unknown"
