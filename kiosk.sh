#!/usr/bin/env bash
set -euo pipefail

URL="${AETHER_CONTROL_URL:-http://127.0.0.1:5000}"
PROFILE_DIR="${AETHER_KIOSK_PROFILE_DIR:-$HOME/.config/aether-kiosk}"

# Wait for Aether Control to actually be ready before opening Chromium.
for _ in $(seq 1 30); do
  if curl -fsS "$URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Avoid reusing a normal Chromium session/profile that can ignore kiosk flags
# and reopen the default start page instead of Aether Control.
pkill -f 'chromium.*aether-kiosk' >/dev/null 2>&1 || true
mkdir -p "$PROFILE_DIR"

exec chromium \
  --kiosk \
  --no-first-run \
  --no-default-browser-check \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-translate \
  --password-store=basic \
  --overscroll-history-navigation=0 \
  --disable-pinch \
  --user-data-dir="$PROFILE_DIR" \
  "$URL"
