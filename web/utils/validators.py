"""web/utils/validators.py — Input validation"""
from typing import Dict, Optional


def validate_position_calc(data: dict) -> Optional[Dict[str, str]]:
    errors = {}

    try:
        balance = float(data.get("account_balance", 0))
        if balance <= 0:
            errors["account_balance"] = "Must be greater than 0"
        elif balance > 1_000_000:
            errors["account_balance"] = "Unreasonably large (max 1 000 000)"
    except (ValueError, TypeError):
        errors["account_balance"] = "Invalid number format"

    try:
        risk_pct = float(data.get("risk_pct", 0))
        if not (0.01 <= risk_pct <= 20):
            errors["risk_pct"] = "Must be between 0.01% and 20%"
    except (ValueError, TypeError):
        errors["risk_pct"] = "Invalid percentage"

    try:
        entry = float(data.get("entry_price", 0))
        if entry <= 0:
            errors["entry_price"] = "Must be positive"
    except (ValueError, TypeError):
        errors["entry_price"] = "Invalid price"

    try:
        stop = float(data.get("stop_loss", 0))
        if stop <= 0:
            errors["stop_loss"] = "Must be positive"
        entry_val = float(data.get("entry_price", 0))
        if stop == entry_val:
            errors["stop_loss"] = "Cannot equal entry price"
    except (ValueError, TypeError):
        errors["stop_loss"] = "Invalid price"

    return errors if errors else None


def validate_tp_breakdown(data: dict) -> Optional[Dict[str, str]]:
    errors = {}

    try:
        entry = float(data.get("entry", 0))
        if entry <= 0:
            errors["entry"] = "Invalid entry price"
    except (ValueError, TypeError):
        errors["entry"] = "Entry must be a number"

    try:
        tp_levels = data.get("tp_levels", [])
        if not tp_levels:
            errors["tp_levels"] = "Must have at least one TP"
        total_qty = 0
        for i, tp in enumerate(tp_levels):
            qty = float(tp.get("qty_pct", 0))
            total_qty += qty
            if not (0 < qty <= 100):
                errors[f"tp_{i}_qty"] = "Quantity must be 0-100%"
        if total_qty > 100:
            errors["tp_levels"] = f"Total quantity exceeds 100% ({total_qty}%)"
    except (ValueError, TypeError):
        errors["tp_levels"] = "Invalid TP configuration"

    return errors if errors else None
