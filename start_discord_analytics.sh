#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  .venv/bin/python discord_daily_analytics.py
else
  python3 discord_daily_analytics.py
fi
