"""
web/server.py — Flask app factory (REFACTORED)
"""
from __future__ import annotations
import os, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.logger import get_logger
log = get_logger("web.server")
_PORT = int(os.getenv("WEB_PORT", "5000"))


def create_app():
    from flask import Flask
    from web.routes import public, api, middleware

    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir   = os.path.join(os.path.dirname(__file__), "static")

    try:
        from core.config import SECRET_KEY
        secret = SECRET_KEY or "fallback-secret-key"
    except Exception:
        secret = "fallback-secret-key"

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.config["SECRET_KEY"] = secret
    app.config["JSON_SORT_KEYS"] = False

    app.register_blueprint(public.bp)
    app.register_blueprint(api.bp)

    @app.errorhandler(404)
    def handle_404(e): return middleware.handle_404(e)

    @app.errorhandler(500)
    def handle_500(e): return middleware.handle_500(e)

    @app.before_request
    def before_request(): middleware.before_request()

    return app


def run_web_server() -> None:
    log.info("🚀 Web dashboard starting on http://0.0.0.0:%d", _PORT)
    try:
        app = create_app()
        app.run(host="0.0.0.0", port=_PORT, debug=False, threaded=True)
    except OSError as exc:
        log.error("❌ Port %d may be in use: %s", _PORT, exc); raise
    except Exception as exc:
        log.error("❌ Web server crashed: %s", exc, exc_info=True); raise


if __name__ == "__main__":
    run_web_server()
