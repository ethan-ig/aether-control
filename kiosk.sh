#!/usr/bin/env bash
set -euo pipefail
URL="${AETHER_CONTROL_URL:-http://127.0.0.1:5000}"
exec chromium \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-translate \
  --password-store=basic \
  --overscroll-history-navigation=0 \
  --disable-pinch \
  "$URL"
