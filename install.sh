#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
mkdir -p data

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo
  echo "Created .env. Put your existing GOVEE_API_KEY in it before starting."
else
  chmod 600 .env
  echo "Existing .env preserved."
fi

echo
echo "Install complete. Run:"
echo "  ./start.sh"
echo "Then in another terminal:"
echo "  ./kiosk.sh"
