#!/usr/bin/env bash
# Open a Cloudflare tunnel to the gateway port (8001) so the hackathon MCP
# can forward inbound WhatsApp/Instagram events to it.
set -euo pipefail
PORT="${1:-8001}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install from https://github.com/cloudflare/cloudflared/releases"
  echo "Or use ngrok: ngrok http $PORT"
  exit 1
fi

echo "==> Opening Cloudflare tunnel to localhost:$PORT"
echo "After it prints the public URL, register webhooks with the MCP:"
echo "  curl -X POST http://localhost:$PORT/admin/register-webhooks?public_base_url=<tunnel-url>"
echo ""
exec cloudflared tunnel --url "http://localhost:$PORT"
