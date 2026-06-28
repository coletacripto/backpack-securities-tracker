#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python3 tracker_update.py --daily --timezone "${TRACKER_TIMEZONE:-Europe/Rome}" --hour "${TRACKER_UPDATE_HOUR:-14}" --minute "${TRACKER_UPDATE_MINUTE:-30}"
