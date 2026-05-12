"""web/utils/constants.py — Market constants"""

PIP_VALUES = {
    "forex": {
        "EUR/USD": 10.0, "GBP/USD": 10.0, "USD/JPY": 10.0,
        "AUD/USD": 10.0, "USD/CHF": 10.0, "USD/CAD": 10.0,
        "NZD/USD": 10.0, "GBP/JPY": 10.0, "EUR/JPY": 10.0,
    },
    "crypto": {
        "BTCUSDT": 1.0, "ETHUSDT": 0.01, "BNBUSDT": 0.01, "SOLUSDT": 0.01,
    },
}

LEVERAGE = {"forex": 50, "crypto": 20}
UNITS_PER_LOT = {"forex": 100_000, "crypto": 1}

RECOMMENDED_RISK_PCT = 2.0
MIN_RISK_PCT = 0.01
MAX_RISK_PCT = 20.0

SYMBOLS = {
    "forex":  ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "USD/CAD"],
    "crypto": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"],
}
