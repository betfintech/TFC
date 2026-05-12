"""web/routes/public.py — Public user-facing pages"""
from __future__ import annotations
from flask import Blueprint, render_template
from datetime import datetime, timezone
from core.logger import get_logger

log = get_logger("web.routes.public")
bp = Blueprint("public", __name__)


@bp.route("/")
def index():
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        return render_template("base.html", now=now, page="dashboard")
    except Exception as exc:
        log.error("Route /: %s", exc)
        return "<h1>Dashboard</h1><p><a href='/signals'>View Signals</a></p>", 200


@bp.route("/signals")
def signals():
    try:
        return render_template("base.html", page="signals")
    except Exception as exc:
        log.error("Route /signals: %s", exc)
        return "<h1>Signals</h1><p><a href='/'>Back</a></p>", 200


@bp.route("/chart")
def chart():
    try:
        return render_template("base.html", page="chart")
    except Exception as exc:
        log.error("Route /chart: %s", exc)
        return "<h1>Chart</h1><p><a href='/'>Back</a></p>", 200


@bp.route("/risk-calculator")
def risk_calculator():
    try:
        return render_template("base.html", page="risk-calculator")
    except Exception as exc:
        log.error("Route /risk-calculator: %s", exc)
        return "<h1>Risk Calculator</h1><p><a href='/'>Back</a></p>", 200


@bp.route("/chat")
def chat():
    try:
        return render_template("base.html", page="chat")
    except Exception as exc:
        log.error("Route /chat: %s", exc)
        return "<h1>Chat</h1><p><a href='/'>Back</a></p>", 200


@bp.route("/account")
def account():
    try:
        return render_template("base.html", page="account")
    except Exception as exc:
        log.error("Route /account: %s", exc)
        return "<h1>Account</h1><p><a href='/'>Back</a></p>", 200
