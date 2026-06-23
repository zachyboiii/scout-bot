#!/usr/bin/env bash
#
# One-time setup for an Ubuntu droplet (DigitalOcean, etc.).
# Run from the repo root after cloning:
#
#   bash deploy/setup.sh
#
# Idempotent: safe to re-run. The systemd unit is generated from the current
# user and directory, so it works wherever you cloned the repo, as whatever
# user you run it as.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="$(id -un)"
SERVICE_NAME="scout-bot"
cd "$APP_DIR"

echo "==> Installing system packages (sudo required)…"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip

echo "==> Creating virtualenv and installing dependencies…"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "==> Created .env from the template. Add your tokens now:"
  echo "      nano $APP_DIR/.env"
  echo "    Then re-run:  bash deploy/setup.sh"
  exit 0
fi

if ! grep -q '^TELEGRAM_BOT_TOKEN=.\+' .env || ! grep -q '^ANTHROPIC_API_KEY=.\+' .env; then
  echo "!! .env is missing TELEGRAM_BOT_TOKEN or ANTHROPIC_API_KEY."
  echo "   Edit $APP_DIR/.env and re-run: bash deploy/setup.sh"
  exit 1
fi

echo "==> Writing systemd unit to /etc/systemd/system/${SERVICE_NAME}.service…"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<UNIT
[Unit]
Description=Scout Telegram sourcing bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

echo "==> Enabling and starting the service…"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"
sleep 1
sudo systemctl --no-pager status "${SERVICE_NAME}" || true

echo
echo "==> Done. Scout is running. Follow logs with:"
echo "      journalctl -u ${SERVICE_NAME} -f"
