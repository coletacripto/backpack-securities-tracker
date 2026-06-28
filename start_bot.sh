#!/bin/sh
set -eu

cd "$(dirname "$0")"
exec .venv/bin/uvicorn solana_token_telegram_bot:app --host 127.0.0.1 --port 8080
