"""web/utils/formatters.py — Data formatting helpers"""


def fmt_price(price: float, decimals: int = 4) -> str:
    return f"{price:.{decimals}f}"


def fmt_currency(amount: float) -> str:
    return f"${amount:,.2f}"


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def fmt_lots(lots: float) -> str:
    return f"{lots:.2f} lots"
