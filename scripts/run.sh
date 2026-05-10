#!/usr/bin/env bash
# Boot all three services in one foreground process.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .venv/Scripts/activate ]; then
  source .venv/Scripts/activate
elif [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
else
  echo ".venv activation script not found. Run: python3 -m venv .venv && .venv/bin/python -m pip install -e \".[dev]\""
  exit 1
fi

if [ ! -f .env ]; then
  echo ".env missing. Run: cp .env.example .env"
  exit 1
fi

# Trap SIGTERM/SIGINT to bring down all children.
trap 'kill 0' SIGINT SIGTERM EXIT

echo "==> Starting web (8000)..."
PYTHONIOENCODING=utf-8 python -m uvicorn apps.web.main:app \
  --host 0.0.0.0 --port "${WEB_PORT:-8000}" --log-level info &

echo "==> Starting gateway (8001)..."
PYTHONIOENCODING=utf-8 python -m uvicorn apps.gateway.main:app \
  --host 0.0.0.0 --port "${GATEWAY_PORT:-8001}" --log-level info &

echo "==> Starting Telegram owner bot..."
PYTHONIOENCODING=utf-8 python -m apps.owner_bot.main &

wait
