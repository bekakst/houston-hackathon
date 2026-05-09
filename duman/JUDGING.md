# HappyCake US — Judging Report (`duman/`)

**Final score: 81 / 115** (Core 76 + Bonus 5)

The codebase is genuinely strong. It's organized around a single dispatcher with four defense rings, has a real notebook-grounded business hypothesis, exposes a first-class agent manifest, and uses `claude -p` exclusively. The largest risks are gaps between *documented* behavior and *wired* behavior — particularly POS/kitchen handoff and several Telegram buttons.

## Pass-by-pass scores

| Pass | Awarded / Max |
|---|---|
| Functional tester | 13 / 20 |
| Agent-friendliness | 13 / 15 |
| On-site assistant | 11 / 15 |
| Code review | 8 / 10 |
| Operator simulator | 11 / 15 |
| Business analyst | 13 / 15 |
| Innovation & depth | 7 / 10 |
| **Core total** | **76 / 100** |
| Bonus | +5 |
| **Final** | **81 / 115** |

---

## Functional tester — 13 / 20

6 public + 5 adversarial YAML scenarios cover peanut allergy, prompt injection, non-English, replay idempotency, quote-vs-custom collision, damaged-on-delivery, custom slot-fill start, phone-last-4 challenge. Substring assertions tolerate LLM variability. Deterministic safety pre-filter (`src/happycake/agents/safety.py`) catches allergen / handoff / injection before any LLM. Webhook receivers parse both Meta and sandbox shapes (`apps/gateway/routes/whatsapp.py`, `instagram.py`) with sha256 idempotency.

**Risk:** approval flow only calls `whatsapp_send` / `instagram_send_dm` — the documented `square_create_order → square_update_order_status → kitchen_create_ticket` chain in `ops/mcp_tools.md` §4 is never invoked, so secret POS/kitchen scenarios will find no evidence.

## Agent-friendliness — 13 / 15

`/.well-known/agent.json` static-mounted *and* `/agent/manifest` dynamic, refreshed byte-identical at import (`apps/web/routes/manifest.py:114-122`). `/agent/catalog.json` and `/agent.txt` hint file present. Bakery JSON-LD on every page (`_base.html`), Product + Offer on cake detail, FAQPage on `/faq` and `/policies/allergens`. Every cake card carries `data-*` attributes (slug, price, lead-time, allergens, serves) so agents can extract without HTML scraping. Manifest exposes a full `cake_configuration_schema` so AI agents can configure a cake without a single round trip.

**Minor:** `apps/web/static/photos/` is missing — every `<img>` 404s, which an agent-friendliness pass that follows image URLs would notice.

## On-site assistant — 11 / 15

Same dispatcher as WhatsApp/IG (`apps/web/routes/assistant.py`), so brand-voice critic, allergen escalation, and phone-last-4 challenge all apply on-site. Greets correctly in HappyCake voice. Widget includes a sensible failure fallback.

**Gaps:** the order-status path stops at "ask for last 4" (no actual MCP `square_recent_orders` lookup wired through `agents/grounding.py:_ground_care`); the custom-cake consultation can slot-fill but never produces a real `square_create_order`. The complaint flow queues an `OwnerDecision` but the kitchen-ack button is a placeholder ("Wired in T16-T17").

## Code review — 8 / 10

Clean src-layout (`pyproject.toml [tool.setuptools.packages.find] where=["src"]`), Pydantic v2 schemas, `pydantic-settings` with `SecretStr` for tokens, `.env.example` with placeholders only, comprehensive `.gitignore`. The four-ring defense (`safety → router → specialists → brand_critic`) is genuinely well-decomposed and each module is small + focused. `claude -p` is the only LLM bridge (`src/happycake/agents/cli.py`) with a retry-and-strict-suffix on JSON parse failure. SQLite schema is three tables (events / decisions / audit) — no over-engineering.

**Issues:** `_write_static_mirror()` runs at import time (filesystem side effect on every router import); `/admin/register-webhooks` is referenced in README but no admin router exists; brand audit allowlist hides every prompt file from scanning so real wordmark drift in prompts would slip through.

## Operator simulator — 11 / 15

11 commands, 2×3 inline-keyboard grid (`apps/owner_bot/cards.py:approval_keyboard`), six one-tap reject reasons (`REJECT_REASONS`), sent-keyboard replacement on approve, long-poll (no tunnel for owner). `/replay <thread_id>` reads the audit log and prints the agent's reasoning trace — a real trust-building feature.

**Gaps:** no operator authorization — `TELEGRAM_OWNER_CHAT_ID` exists in settings but is never checked, so any user who finds the bot can `/approve`; `✏️ Edit`, `🚨 Kitchen`, and report-period buttons all reply "coming" instead of doing the action; on approve, the system sends the reply but does not create a POS order or kitchen ticket.

## Business analyst — 13 / 15

README is rendered from `README.md.tmpl` against `analysis/_metrics.json`, eliminating cross-document number drift. Baseline numbers reconcile exactly to seeded MCP data: avg revenue $17,003.33 = sum/6 of `mcp_sales_history.json`, avg margin 62.4% = mean of five `estimatedMarginPct` in `mcp_margins.json`, avg orders 675.7. Cost breakdown is explicit ($200 Claude Max + $30 VPS + $20 tunnel + $250 marketing across five channels). Two value streams — loss recovery (20% of 290 lost orders → $907 contribution) and marketing leverage ($523 contribution) — sum to $1,431 incremental, plus replacement-labour $4,902 from BLS Houston-Sugar Land wages × burden/overhead. Verdict $12.67/dollar beats the 10× target.

**Soft spots:** the 20% loss-recovery rate and the 8% retention-SMS conversion rate are stipulated, not derived.

## Innovation & depth — 7 / 10

Real original moves: brand-voice critic loop as a second `claude -p` pass for defense-in-depth; `/replay` reasoning-trace command; static/dynamic manifest byte-identical at import; lead-time-aware substitution in `mcp/inventory.py:alternatives` returning serves-matched in-stock options instead of refusing; `agent.txt` hint file; sha256 idempotency keys; one-bot/four-commands UX explicitly justified. Edge-case depth uneven — many ideas land, but several (kitchen ack, marketing publish, edit reply) are stubs.

---

## Bonus — +5 (capped at +5 because core is in the 60–79 band)

- Real business pain — allergens, complaints, capacity, custom intake, repeat customers via retention SMS: **+2**
- Production readiness — audit trail and idempotency are real, but missing operator auth, missing product photos, multiple stub buttons: **+1**
- Growth upside — channel-specific budget logic, review generation, retention SMS, halal-friendly tagging for Sugar Land diaspora: **+2**

---

## Top three things that would raise the score most

1. **Wire the POS+kitchen chain on approve.** `apps/owner_bot/handlers.py:_handle_approve` should call `square_create_order` → `square_update_order_status("confirmed")` → `kitchen_create_ticket` after `send_to_customer`. Without it, `evaluator_score_pos_kitchen_flow` finds nothing.
2. **Gate the bot on `TELEGRAM_OWNER_CHAT_ID`.** A simple `update.effective_chat.id != int(settings.telegram_owner_chat_id)` early-return at the top of every handler closes the auth hole and is one commit.
3. **Ship the `/static/photos/` directory** referenced in `data/catalog.yaml`. Without it the storefront renders broken, which hurts both the agent-friendliness and assistant passes.
