#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/tracker_site"
python3 -m http.server 8090
