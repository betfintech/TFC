"""web/routes/middleware.py — Auth guards, error handlers"""
from flask import jsonify
from core.logger import get_logger
log = get_logger("web.middleware")


def before_request():
    pass  # Add auth checks here later


def handle_404(e):
    return jsonify({"error": "Not found", "code": 404}), 404


def handle_500(e):
    log.error("500 error: %s", e)
    return jsonify({"error": "Internal server error", "code": 500}), 500
