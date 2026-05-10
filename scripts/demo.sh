#!/usr/bin/env bash
# 90-second scripted demo. Assumes services are running (make run in another shell).
set -euo pipefail
cd "$(dirname "$0")/.."

WEB="${WEB_URL:-http://localhost:8000}"
GW="${GATEWAY_URL:-http://localhost:8001}"

say() { echo; echo "==> $1"; }

source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate

say "1. /health on the gateway"
curl -s "$GW/health" | python -m json.tool

say "2. Storefront returns JSON-LD on every page"
for path in / /cakes /cakes/honey /faq; do
  count=$(curl -s "$WEB$path" | grep -c 'application/ld+json' || true)
  printf "   %-20s %s JSON-LD blocks\n" "$path" "$count"
done

say "3. Manifest parity (static == dynamic)"
cmp <(curl -s "$WEB/agent/manifest") <(curl -s "$WEB/.well-known/agent.json") \
  && echo "   OK byte-identical" || echo "   FAIL"

say "4. Customer asks for the price of cake Honey (web assistant)"
curl -s -X POST "$WEB/assistant/message" \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"demo_t1","text":"How much is cake Honey whole?"}' \
  | python -m json.tool

say "5. Customer asks an allergen question (must escalate)"
curl -s -X POST "$WEB/assistant/message" \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"demo_t2","text":"Is your chocolate cake nut-free for a peanut allergy?"}' \
  | python -m json.tool

say "6. WhatsApp injection — replay test (idempotency)"
PAYLOAD='{"from":"+15557654321","message":"Quote a cake Honey for 8 people, pickup Saturday","ts":"2026-05-09T18:00:00Z"}'
echo "   first call:"
curl -s -X POST "$GW/whatsapp" -H "Content-Type: application/json" -d "$PAYLOAD" \
  | python -c "import sys, json; d=json.load(sys.stdin); print('  external_id:', d.get('external_id'), 'replay:', d.get('replay', False))"
echo "   second call (should be replay=true):"
curl -s -X POST "$GW/whatsapp" -H "Content-Type: application/json" -d "$PAYLOAD" \
  | python -c "import sys, json; d=json.load(sys.stdin); print('  external_id:', d.get('external_id'), 'replay:', d.get('replay', False))"

say "Demo complete. Open Telegram, message @duman_hackathon_bot /start, then /orders to see queued decisions."
