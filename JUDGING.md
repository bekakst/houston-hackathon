# HappyCake US — Judging Report (`duman/`)

**Final score: 78 / 115** (Core 73 + Bonus 5)
*Revised after re-scoring against the Happy Cake Business Simulator rubric. See [Revisions vs first pass](#revisions-vs-first-pass) at the bottom.*

The codebase is genuinely strong. It's organized around a single dispatcher with four defense rings, has a real notebook-grounded business hypothesis, exposes a first-class agent manifest, and uses `claude -p` exclusively. The dominant gap is between *documented* MCP behavior and *wired* MCP behavior: the only hosted-MCP tools actually invoked at runtime are `whatsapp_send`, `instagram_send_dm`, `whatsapp_register_webhook`, `instagram_register_webhook`, and `tools/list` for the `/health` probe. Everything POS, kitchen, marketing, Google Business, and world-engine is documented but never called, so `mcp_audit_log` evidence will be thin where the rubric expects it densest.

## Pass-by-pass scores

| Pass | Awarded / Max |
|---|---|
| Functional tester | 12 / 20 |
| Agent-friendliness | 13 / 15 |
| On-site assistant | 10 / 15 |
| Code review | 8 / 10 |
| Operator simulator | 10 / 15 |
| Business analyst | 13 / 15 |
| Innovation & depth | 7 / 10 |
| **Core total** | **73 / 100** |
| Bonus | +5 |
| **Final** | **78 / 115** |

---

## Functional tester — 12 / 20

6 public + 5 adversarial YAML scenarios cover peanut allergy, prompt injection, non-English, replay idempotency, quote-vs-custom collision, damaged-on-delivery, custom slot-fill start, phone-last-4 challenge. Substring assertions tolerate LLM variability. Deterministic safety pre-filter (`src/happycake/agents/safety.py`) catches allergen / handoff / injection before any LLM. Webhook receivers parse both Meta and sandbox shapes (`apps/gateway/routes/whatsapp.py`, `instagram.py`) with sha256 idempotency. `/admin/register-webhooks` (in `apps/gateway/routes/health.py:45`) does call `whatsapp_register_webhook` + `instagram_register_webhook` against the hosted MCP at startup time.

**Risks:**
- Approval flow only calls `whatsapp_send` / `instagram_send_dm` — the documented `square_create_order → square_update_order_status → kitchen_create_ticket` chain in `ops/mcp_tools.md` §4 is never invoked. Secret POS/kitchen scenarios will find no evidence.
- The smoke runner is offline: it drives the local dispatcher directly, never `world_start_scenario` / `world_next_event`. So the `evaluator_score_world_scenario` pass has nothing to score.

## Agent-friendliness — 13 / 15

`/.well-known/agent.json` static-mounted *and* `/agent/manifest` dynamic, refreshed byte-identical at import (`apps/web/routes/manifest.py:114-122`). `/agent/catalog.json` and `/agent.txt` hint file present. Bakery JSON-LD on every page (`_base.html`), Product + Offer on cake detail, FAQPage on `/faq` and `/policies/allergens`. Every cake card carries `data-*` attributes (slug, price, lead-time, allergens, serves) so agents can extract without HTML scraping. Manifest exposes a full `cake_configuration_schema`. Product photos and storefront imagery now resolve (logo + 9 cake `.webp` + storefront hero). Mobile breakpoint at 800px collapses the major grids — basic responsiveness rather than mobile-first.

**Gap:** no campaign landing pages with attribution capture (`/lp/<campaign>` + UTM persistence) — the simulator rubric explicitly calls these out for routing Marketing-Simulator traffic into the site.

## On-site assistant — 10 / 15

Same dispatcher as WhatsApp/IG (`apps/web/routes/assistant.py`), so brand-voice critic, allergen escalation, and phone-last-4 challenge all apply on-site. Greets in HappyCake voice. Widget includes a sensible failure fallback. Owner escalation queues an `OwnerDecision`, surfaces it in Telegram, and on Approve sends back through `whatsapp_send` / `instagram_send_dm`.

**Gaps:**
- The "MCP-backed facts" rubric criterion is technically not met. `agents/grounding.py` builds the evidence dict from local YAML mirrors (`mcp/local_data.py`) — the seed step pulled from the hosted MCP into JSON, but at runtime no `square_*`, `kitchen_*`, or `marketing_*` tool is called.
- Order-status path stops at "ask for last 4". The verified=True branch never fetches via `square_recent_orders`.
- Custom-cake consultation can slot-fill but never produces a real `square_create_order` even after owner approval.
- Complaint flow queues an `OwnerDecision` but the kitchen-ack button is a placeholder ("Wired in T16-T17").

## Code review — 8 / 10

Clean src-layout (`pyproject.toml [tool.setuptools.packages.find] where=["src"]`), Pydantic v2 schemas, `pydantic-settings` with `SecretStr` for tokens, `.env.example` with placeholders only, comprehensive `.gitignore`. The four-ring defense (`safety → router → specialists → brand_critic`) is genuinely well-decomposed and each module is small + focused. `claude -p` is the only LLM bridge (`src/happycake/agents/cli.py`) with a retry-and-strict-suffix on JSON parse failure. SQLite schema is three tables (events / decisions / audit) — no over-engineering. Fresh-clone reproducibility verified against a clean venv: `pip install -e ".[dev]"` and `uvicorn apps.web.main:app` both work first time.

**Issues:** `_write_static_mirror()` runs at import time (filesystem side effect on every router import); brand audit allowlist hides every prompt file from scanning so real wordmark drift in prompts would slip through; the `evaluator_*` self-judge loop documented in `ops/mcp_tools.md` was not used pre-submission — running it would have surfaced the missing POS / kitchen / world evidence before the deadline.

## Operator simulator — 10 / 15

11 commands, 2×3 inline-keyboard grid (`apps/owner_bot/cards.py:approval_keyboard`), six one-tap reject reasons (`REJECT_REASONS`), sent-keyboard replacement on approve, long-poll (no tunnel for owner). `/replay <thread_id>` reads the audit log and prints the agent's reasoning trace — a real trust-building feature. `/audit` shows the last 20 events.

**Gaps:** no operator authorization — `TELEGRAM_OWNER_CHAT_ID` exists in settings but is never checked; `✏️ Edit`, `🚨 Kitchen`, and report-period buttons all reply "coming" instead of doing the action; `/marketing` lists pending decisions but no flow ever generates one (no caller invokes `run_marketing` / `marketing_create_campaign` / `marketing_launch_simulated_campaign`); on Approve, the system sends the customer reply but does not create a POS order or kitchen ticket. A non-technical operator following the help text will hit dead ends on three of the six menu items.

## Business analyst — 13 / 15

README is rendered from `README.md.tmpl` against `analysis/_metrics.json`, eliminating cross-document number drift. Baseline numbers reconcile exactly to seeded MCP data: avg revenue $17,003.33 = sum/6 of `mcp_sales_history.json`, avg margin 62.4% = mean of five `estimatedMarginPct` in `mcp_margins.json`, avg orders 675.7. Cost breakdown is explicit ($200 Claude Max + $30 VPS + $20 tunnel + $250 marketing across five channels). Two value streams — loss recovery (20% of 290 lost orders → $907 contribution) and marketing leverage ($523 contribution) — sum to $1,431 incremental, plus replacement-labour $4,902 from BLS Houston-Sugar Land wages × burden/overhead. Verdict $12.67/dollar beats the 10× target.

**Soft spots:** the 20% loss-recovery rate and the 8% retention-SMS conversion rate are stipulated, not derived. The hypothesis is solid on paper; the simulator rubric's "Marketing as a closed loop" criterion (plan → launch → leads → metrics → adjust) is *not* exercised in code — see Operator simulator above.

## Innovation & depth — 7 / 10

Real original moves: brand-voice critic loop as a second `claude -p` pass for defense-in-depth; `/replay` reasoning-trace command; static/dynamic manifest byte-identical at import; lead-time-aware substitution in `mcp/inventory.py:alternatives` returning serves-matched in-stock options instead of refusing; `agent.txt` hint file; sha256 idempotency keys; one-bot/four-commands UX explicitly justified. Edge-case depth uneven — many ideas land, but several (kitchen ack, marketing publish, edit reply) are stubs.

---

## Simulator-rubric coverage

Mapping the simulator's own scoring dimensions onto where they show up in this report:

| Simulator dimension | Score evidence | Status |
|---|---|---|
| Website / Storefront | Functional + Agent-friendliness | **partial** — sells, but no campaign LPs / attribution |
| Agent-friendly site | Agent-friendliness | **strong** — manifest, JSON-LD, `data-*`, agent.txt |
| On-site assistant | On-site assistant | **partial** — UX clean; runtime not actually MCP-backed |
| Square / POS simulator | Functional + Operator | **gap** — no `square_create_order` on Approve |
| Kitchen / Production | Operator | **gap** — no `kitchen_create_ticket` on Approve |
| WhatsApp simulator | Functional | **wired** — webhook in, `whatsapp_send` out, idempotency |
| Instagram simulator | Functional | **wired** — webhook in, `instagram_send_dm` out |
| Marketing Simulator | Business analyst | **partial** — math is excellent; closed-loop wiring missing |
| Analytics / Evaluator | Code review | **gap** — `evaluator_*` self-judge never run |
| World / Scenario engine | Functional | **gap** — smoke runner is offline-only |
| Google Business / Local | — | **gap** — no `gb_*` calls anywhere |

## Bonus — +5 (capped at +5 because core is in the 60–79 band)

- Real business pain — allergens, complaints, capacity, custom intake, repeat customers via retention SMS: **+2**
- Production readiness — audit trail and idempotency are real, photos + logo + storefront imagery now wired, but operator auth missing and marketing/kitchen flows are stubs: **+2**
- Growth upside — channel-specific budget logic, review generation, retention SMS, halal-friendly tagging for Sugar Land diaspora: **+2**

(Capped at +5; sums to +6 before cap.)

---

## Top three things that would raise the score most

1. **Wire the POS + kitchen MCP chain on Approve.** `apps/owner_bot/handlers.py:_handle_approve` should call `square_create_order` → `square_update_order_status("confirmed")` → `kitchen_create_ticket` after `send_to_customer`. This single change populates the `mcp_audit_log` rows that `evaluator_score_pos_kitchen_flow` looks for and converts the on-site assistant from "MCP-backed in name" to "MCP-backed in fact."
2. **Drive a world scenario end-to-end.** Add a `scripts/run_world.py` that calls `world_start_scenario` → loops on `world_next_event` → routes events through the dispatcher → calls `evaluator_score_world_scenario` and writes the result to `analysis/_world_score.json`. The smoke runner stays for unit-style coverage.
3. **Marketing as a closed loop.** Owner taps `/marketing` → bot calls `run_marketing` → on Approve invokes `marketing_create_campaign` → `marketing_launch_simulated_campaign` → `marketing_generate_leads` → reports back via `marketing_report_to_owner`. This is the rubric's "$500 → $5K loop" turned from spreadsheet into runtime.

Two cheaper follow-ups: gate every Telegram handler on `update.effective_chat.id == int(settings.telegram_owner_chat_id)`, and add `/lp/<campaign>` routes that capture `?utm_*` into the events table for marketing attribution.

---

## Revisions vs first pass

The first pass put this submission at **81 / 115** (Core 76 + Bonus 5). Re-scoring against the Happy Cake Business Simulator rubric pulled four points off:

- **Functional tester −1** (was 13 → 12). Strict reading: the smoke runner is offline; `world_*` engine is never driven; `mcp_audit_log` evidence will be thin where the rubric expects it dense.
- **On-site assistant −1** (was 11 → 10). The "MCP-backed facts" criterion is not literally met — runtime grounding reads local YAML mirrors, not the hosted MCP. Order-status doesn't actually fetch `square_recent_orders`.
- **Operator simulator −1** (was 11 → 10). `/marketing` shows pending decisions but nothing produces them; report-period buttons are stubs. A non-technical operator hits dead ends on multiple menu items.
- **Code review** unchanged at 8/10, but the underlying notes shifted: the prior `/admin/register-webhooks` "missing" claim was wrong (route exists at `apps/gateway/routes/health.py:45`); replaced by a fresh `evaluator_*` self-judge ding instead.

Bonus stayed at +5 (capped). Photos + logo are now wired, which lifted the Production-readiness pillar from +1 to +2 — but the bonus band cap absorbs that improvement.

**Net: 78 / 115.**
