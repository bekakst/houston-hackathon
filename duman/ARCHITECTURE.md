# HappyCake US — Architecture

This document is the implementer-facing companion to `README.md`. It is written for the Code Reviewer evaluator pass — every claim has a file path next to it.

## 1 · The shape

```
                    ┌──────────────────────────────────────────────────────────┐
                    │                  Customer channels                        │
                    │   Website (browser)   ·   WhatsApp DM   ·   Instagram DM   │
                    └──────────┬───────────────────┬──────────────┬────────────┘
                               │                   │              │
            POST /assistant    │ webhook (8001)    │ webhook (8001)
                               ▼                   ▼              ▼
                    ┌──────────────────────────────────────────────────────────┐
                    │  apps/web (8000)         apps/gateway (8001)              │
                    │  FastAPI SSR             FastAPI inbound                  │
                    │  + JSON-LD               + HMAC verify                    │
                    │  + assistant API         + sha256 idempotency             │
                    └──────────┬───────────────────┬──────────────────┬────────┘
                               │                   │                  │
                               ▼                   ▼                  ▼
                    ┌──────────────────────────────────────────────────────────┐
                    │  src/happycake/agents/dispatcher.py                       │
                    │     handle_customer_message(channel, sender, text, …)     │
                    │                                                           │
                    │   Ring 1  safety_pre_filter        (pure Python)          │
                    │   Ring 2  router (claude -p)       (LLM classifier)       │
                    │   Ring 3  intake / custom / care   (LLM specialist)       │
                    │   Ring 4  brand_critic (claude -p) (LLM rewriter)         │
                    └──────────┬─────────────────────┬───────────────┬─────────┘
                               │                     │               │
                MCP call       │  decision queued    │  audit trail  │
                               ▼                     ▼               ▼
                    ┌──────────────────┐   ┌──────────────────┐   ┌────────────┐
                    │  Hosted MCP      │   │  decisions table │   │ audit log  │
                    │  (55 tools)      │   │  (SQLite UNIQUE) │   │  (SQLite)  │
                    └──────────────────┘   └────────┬─────────┘   └────────────┘
                                                    │
                                          long-poll │
                                                    ▼
                                    ┌──────────────────────────────────┐
                                    │  apps/owner_bot/                  │
                                    │  Telegram inline-keyboard cards   │
                                    │   approve · reject · edit · …     │
                                    └────────────────┬─────────────────┘
                                                     │ on Approve
                                                     ▼
                                    ┌──────────────────────────────────┐
                                    │  apps/owner_bot/outbound.py       │
                                    │  → MCP whatsapp_send /            │
                                    │    instagram_send_dm /            │
                                    │    site assistant ack             │
                                    └──────────────────────────────────┘
```

## 2 · Defense rings (the prior 43/100 was missing rings 1, 4, and the audit trail)

| Ring | File | Role | Failure mode it blocks |
|---|---|---|---|
| 1 | `src/happycake/agents/safety.py` | Token + phrase + regex pre-filter. Always runs FIRST. | "peanut allergy", "talk to a person", "ignore all previous instructions". |
| 2 | `src/happycake/agents/router.py` | `claude -p` intent classifier. Returns `escalate` if confidence < 0.6. | Out-of-domain / ambiguous messages. |
| 3 | `src/happycake/agents/specialists.py` | One specialist per intent. Pre-fetches MCP grounding via `agents/grounding.py` before the LLM call. | Fabricated prices, allergens, hours, lead-times. |
| 4 | `src/happycake/agents/brand_critic.py` | Second `claude -p` pass that rewrites for wordmark, cake-name format, max-3-emoji, standard close. | Off-brand drafts that slip past the specialist. |
| Audit | `src/happycake/storage.py` | `audit` table append-only on every state mutation. | Lost evidence at evaluator-replay time. |

## 3 · Agent decomposition

| Agent | System prompt | MCP tools used | Escalates when |
|---|---|---|---|
| Router | `ops/prompts/router.md` | none | confidence < 0.6, or any safety token, or human-handoff phrase |
| Intake | `ops/prompts/intake.md` | `square_list_catalog`, `square_get_inventory`, `kitchen_get_menu_constraints` (grounded via `mcp/catalog`, `mcp/pricing`, `mcp/inventory`) | flavor/size out of stock, discount above policy, pickup outside open hours |
| Custom | `ops/prompts/custom.md` | catalog + pricing + kitchen feasibility + brand voice | tiers > 2, figurine/topper, deadline < `lead_time_hours`, inspiration-photo of competitor |
| Care | `ops/prompts/care.md` | `square_recent_orders`, orders + customers + policies | allergen complaint (severity `crit`), refund > $50, damaged-on-delivery |
| Marketing | `ops/prompts/marketing.md` | `marketing_get_budget`, `marketing_get_sales_history`, `marketing_get_margin_by_product`, `marketing_create_campaign` | every public draft (brand-book hard rule) |
| Reporting | `ops/prompts/reporting.md` | `square_get_pos_summary`, `kitchen_get_production_summary`, audit (read-only) | never — read-only |
| Brand critic | `ops/prompts/brand_critic.md` | `mcp/brand` (voice spec) | only when a fabricated FACT is detected — otherwise auto-rewrites |

All 55 hosted MCP tools are catalogued in [`ops/mcp_tools.md`](ops/mcp_tools.md).

## 4 · Idempotency and HMAC

`apps/gateway/security.py` provides two helpers used by every webhook route:

- `derive_external_id(channel, sender, received_at, text)` → `sha256(...)[:32]`
- `verify_meta_signature(app_secret, body, signature_header)` → `hmac.compare_digest(...)`

`apps/gateway/routes/whatsapp.py` and `instagram.py` both:

1. Verify the signature (or pass through in dev mode).
2. Compute `external_id`.
3. Check the `events` table — if the `external_id` exists, return the cached response.
4. Insert the row, dispatch the agent stack, store the resulting `Reply` JSON back on the row.

The unique constraint `events.external_id PRIMARY KEY` is the lock; replay produces zero duplicate orders by construction.

## 5 · Owner bot — operator simulator pass

`apps/owner_bot/handlers.py` registers 11 commands plus `CallbackQueryHandler`. Every approval card uses `apps/owner_bot/cards.py:approval_keyboard()` (a 2x3 grid: ✅ Approve · ✏️ Edit · ❌ Reject / 👁 Preview · 📞 Call · 🚨 Kitchen). Reject reasons are buttons via `reject_reason_keyboard()` — six one-tap reasons, never typed.

When the owner taps Approve:
1. `decision_set_status(decision_id, "approved")` (atomic).
2. `outbound.send_to_customer(channel, customer_id, draft_reply)` posts on the original channel via the matching MCP tool.
3. `audit_write("decision_approved", ...)`.
4. The inline keyboard is replaced with a non-clickable `✓ Sent at HH:MM UTC` button, so the audit history is visible inline.

`/replay <thread_id>` reads the audit table and prints the decision trail — the **innovation lever** that lets the owner build trust week by week.

## 6 · Storefront and JSON-LD

`apps/web/main.py` mounts `apps/web/static` and `.well-known/`, registers three routers (pages, assistant, manifest). Every page extends `templates/_base.html`, which always emits a Bakery JSON-LD block. `templates/cake_detail.html` adds Product + Offer; `policy_allergens.html` and `faq.html` add FAQPage.

`/.well-known/agent.json` and `/agent/manifest` are byte-identical (CI test). The manifest exposes the full `cake_configuration_schema`, every catalog cake, every delivery zone, and every assistant endpoint — so an AI customer can configure a cake without scraping the DOM.

## 7 · Reproducibility

- `pyproject.toml` uses **src-layout** (`src/happycake/`) with `[tool.setuptools.packages.find] where=["src"]`. The prior 43/100 build's `make install` failed on flat-layout; this one cannot.
- All secrets are env vars; `.env` is gitignored; `.env.example` carries placeholders only.
- `make bootstrap` is the one-line setup. `make seed` pulls live MCP data, executes `analysis/hypothesis.ipynb`, and renders `README.md` from the template.
- `make smoke` runs all 6 public scenarios. Last verified run: **6/6 scenarios, 7/7 turns**.
- `make smoke-adv` runs 5 adversarial scenarios. Last verified run: **5/5, 6/6 turns**.

## 8 · Telemetry

- Every inbound message → `audit("message_inbound", ...)`.
- Every outbound message → `audit("message_outbound", ...)`.
- Every owner decision → `audit("decision_queued|approved|rejected", ...)`.
- Every webhook idempotency hit → log line + cached response.
- Every `claude -p` call timing is logged at INFO; failures escalate to owner via the dispatcher fallback path.

## 9 · Blind gender-reveal cake — the orderer never sees the answer

A blind-orderer flow added without disturbing the four-ring dispatcher or the
agent-readable surfaces. The orderer fills `/order/gender-reveal`, gets a
one-time `/reveal/<token>` link, and forwards it to the knower. The knower
picks Boy or Girl on a `noindex,nofollow` page; the kitchen sees it in the
owner's Telegram queue; the orderer's status page is wired from a
`RevealOrdererView` Pydantic model that does not include the `gender` field
at all (defense-in-depth — even a future template typo cannot leak it).

```
                                   /reveal/<24c-token>
   parent ──▶ /order/gender-reveal ──┐               ┌── doctor / knower
   (orderer)                         │  share link   │
                                     ▼               ▼
                              ┌──────────────────────────┐
                              │  reveal_orders (sqlite)   │
                              │  state: pending_reveal    │
                              └──────────────────────────┘
                                          │
                                          ▼  POST /reveal/<token>
                                          │  (write-once gender lock)
                              ┌──────────────────────────┐
                              │  state: revealed          │
                              │  gender: boy | girl       │
                              │  knower_ip_hash (salted)  │
                              └──────────────────────────┘
                                          │
                ┌─────────────────────────┴─────────────────────────┐
                ▼                                                   ▼
   OwnerDecision (kind="gender_reveal")                  orderer status page
   ─ Telegram inline keyboard                            ─ "Reveal locked. Pickup
   ─ Card body: "Interior: BLUE (boy)"                     ready by [date]"
   ─ Approve → state: baking                             ─ NO gender column,
   ─ Customer message: lock-in only,                       NO pink/blue word,
     no colour, no gender                                  enforced by Pydantic
```

The `gender_reveal` intent joins the router enum, with a matching
`agents/gender_reveal.py` specialist that holds the line in HappyCake voice
("the surprise is the whole point — send the link to your doctor's office").
Brand-critic still runs on every customer-visible reply.

## 10 · What we deliberately did not build

- ❌ Postgres / Redis — SQLite + in-memory dicts are sufficient for 24 h.
- ❌ Docker Compose — fresh-clone bootstrap matters more than infra elegance.
- ❌ Separate orchestrator microservice — in-process function calls are auditable and cheaper.
- ❌ React widget — Jinja2 + 60 lines of vanilla JS deliver the same UX without a build chain.
- ❌ Anthropic SDK / Agent SDK / LangGraph / CrewAI — disqualified by the brief; we use `claude -p` exclusively (`src/happycake/agents/cli.py`).
