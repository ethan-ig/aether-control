#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
exec gunicorn --workers 1 --threads 4 --bind "${AETHER_BIND:-127.0.0.1}:${AETHER_PORT:-5000}" --access-logfile - app:app
