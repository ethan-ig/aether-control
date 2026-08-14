#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
USER_NAME="$(id -un)"
SERVICE_NAME="aether-control.service"
TMP="$(mktemp)"

cat > "$TMP" <<EOF
[Unit]
Description=Aether Control
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$ROOT/.venv/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:5000 --access-logfile - app:app
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 0644 "$TMP" "/etc/systemd/system/$SERVICE_NAME"
rm -f "$TMP"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
sudo systemctl --no-pager --full status "$SERVICE_NAME"
