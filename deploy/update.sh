#!/usr/bin/env bash
#
# Pull the latest code and restart the bot. Run on the droplet:
#
#   bash deploy/update.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="scout-bot"
cd "$APP_DIR"

echo "==> Pulling latest code…"
git pull --ff-only

echo "==> Syncing dependencies…"
./venv/bin/pip install -r requirements.txt

echo "==> Restarting ${SERVICE_NAME}…"
sudo systemctl restart "${SERVICE_NAME}"
sleep 1
sudo systemctl --no-pager status "${SERVICE_NAME}" || true
