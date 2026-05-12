#!/usr/bin/env bash
# startup.sh — install dependencies then start the app

set -e

echo "[startup] Installing dependencies..."
if ! pip install -r requirements.txt --quiet; then
    echo "[startup] ERROR: pip install failed. Check requirements.txt and your environment." >&2
    exit 1
fi
echo "[startup] Dependencies installed successfully."

echo "[startup] Starting application..."
exec python app.py
