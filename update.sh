#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "Pulling latest Aether Control..."
git pull --ff-only

source .venv/bin/activate
pip install -q -r requirements.txt

chmod +x install.sh install-service.sh start.sh kiosk.sh update.sh 2>/dev/null || true

if systemctl is-enabled aether-control.service >/dev/null 2>&1; then
  echo "Restarting aether-control.service..."
  sudo systemctl restart aether-control.service
fi

echo "Aether Control updated."
