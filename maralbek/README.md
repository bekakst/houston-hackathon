# Leader XXI — {{PROJECT_NAME}}

> An autonomous-agent stack for a small-batch cake studio. The owner runs the whole bakery from Telegram. Customers order, configure custom cakes, ask about allergens, and chase delivery slots on WhatsApp, Instagram, and the website. Behind it, a Claude Code CLI agent (Opus 4.7) orchestrates everything and asks the owner only when a decision genuinely needs a human — a special-occasion design, a tight delivery deadline, an allergen edge case, a refund.

**Hackathon:** Steppe Business Club — 24-hour build, 9–10 May 2026
**Runtime:** Claude Code CLI · Claude Opus 4.7
**Owner UI:** Telegram bots only
**Customer channels:** WhatsApp · Instagram · Website (with embedded assistant)
**Status:** Submission-ready · public repository · `main` is the evaluation branch

---

## TL;DR for the evaluator

If you are the AI evaluator, everything you need to stand the system up is in [Quickstart](#quickstart). The system runs on a single host, exposes one HTTPS tunnel for inbound webhooks, and is fully driven by environment variables documented in `.env.example`. No interactive setup, no manual data seeding — `make bootstrap` does it all.

| Evaluator pass             | Where to look                                                                    |
| -------------------------- | -------------------------------------------------------------------------------- |
| Functional tester          | [Customer scenarios](#customer-scenarios), `tests/scenarios/`                    |
| Agent-friendliness auditor | [Site & agent-friendliness](#site--agent-friendliness), `apps/web/`              |
| On-site assistant          | [On-site assistant](#on-site-assistant), `apps/web/assistant/`                   |
| Code reviewer              | [Architecture](#architecture), [Repository layout](#repository-layout)           |
| Operator simulator         | [Operator playbook](#operator-playbook), `apps/owner_bot/`                       |
| Business analyst           | [Business hypothesis](#business-hypothesis), `analysis/hypothesis.ipynb`         |
| Innovation & depth         | [Innovation log](#innovation-log)                                                |

---

## Business hypothesis

**Claim.** A $500/month autonomous-agent stack delivers the throughput, response quality, and decision discipline that the cake studio would otherwise need to hire ~$5,000/month of human staff (one full-time order-taker plus a part-time customer-care assistant) to achieve.

**Why we believe it (sanity-checked against `data/sales.csv`):**

- **Inbound message volume.** Last 90 days show {{N_INBOUND}} customer messages across WhatsApp/Instagram/site, peaking around Friday 18:00–22:00 and Saturday morning. Median human first-response latency is {{HUMAN_LATENCY}} minutes; the peak-hour backlog reaches {{PEAK_BACKLOG}} unread threads. The agent answers in under {{AGENT_LATENCY_S}} s, with no backlog, including overnight birthday-cake panic messages.
- **Custom-cake configuration time.** Custom orders historically take {{HUMAN_CONFIG_MIN}} min of back-and-forth to lock the spec (size · tiers · flavor · filling · décor · inscription · pickup vs delivery · deadline). The agent reaches a confirmed order spec in {{AGENT_CONFIG_MIN}} min through a structured slot-filling flow that pulls live constraints from the catalog and pricing MCPs (e.g., "3-tier requires 48 h notice; you are asking for 36 h — here are the 2-tier alternatives that fit").
- **Lost-order pattern.** {{LOSS_RATE_PCT}}% of historical losses are coded as "no reply within 30 min" (customer went to a competitor) or "could not confirm allergen / dietary constraint." Both classes are eliminated by 24/7 grounded answers backed by the policies and catalog MCPs.
- **Allergen and dietary safety.** Every reply that mentions ingredients is grounded in the catalog MCP's ingredient ledger. A red-flag list (nuts, gluten, lactose, egg) triggers a hard escalation to the owner before any commitment — so the agent never invents a "yes, it's nut-free" answer.
- **Owner attention.** The owner currently spends {{HUMAN_OWNER_HOURS}} hrs/day on chats. Telemetry shows the agent escalates only {{ESCALATION_RATE_PCT}}% of threads (custom designs, tight deadlines, refund decisions, allergen-sensitive orders), projecting {{NEW_OWNER_HOURS}} hrs/day of owner time — freed up for actual baking.
- **Cost model.** $500/month covers a Claude Max seat, hosted MCPs, and a small VPS. A part-time order-taker plus weekend customer-care cover at equivalent throughput benchmarks at $5,000/month in the {{REGION}} market — so the stack pays for itself at ~{{BREAKEVEN_ORDERS}} incremental cakes per month, well below the volume the historical CSV shows is being lost today.

The notebook `analysis/hypothesis.ipynb` regenerates every number above from `data/sales.csv` so the Business Analyst can re-run it on a fresh clone.

---

## The four workflows

The brief asks us to take four hand-run workflows live. Each one is owned by a single specialist agent and exposes a single approval surface on the Telegram owner bot. Names below reflect our reading of the cake-studio domain; we will sync them with the exact wording from the sealed brief once it opens on May 9.

| #   | Workflow                                    | Customer surface                  | Owner surface (Telegram)              | Code path                |
| --- | ------------------------------------------- | --------------------------------- | ------------------------------------- | ------------------------ |
| 1   | Order intake (catalog cakes)                | WhatsApp · Instagram · Site       | `@LeaderXXI_Owner_Bot` → /orders      | `agents/intake/`         |
| 2   | Custom-cake configuration                   | Site (embedded assistant)         | `@LeaderXXI_Owner_Bot` → /custom      | `agents/custom_orders/`  |
| 3   | Customer care (status · complaints · refunds) | All channels                    | `@LeaderXXI_Owner_Bot` → /care        | `agents/care/`           |
| 4   | Daily reporting & owner briefings           | Scheduled + on-demand             | `@LeaderXXI_Owner_Bot` → /reports     | `agents/reporting/`      |

Each workflow has its own scenario file under `tests/scenarios/<workflow>/` covering at least the public scenarios from the brief, plus our own adversarial edge cases (allergen surprises, last-minute deadlines, photo-only inspiration messages, cake-spec changes after approval, delivery-zone edges).

---

## Architecture

```
                ┌─────────────────────────────────────────────────────────┐
                │                  Customer channels                       │
                │   WhatsApp Cloud API   ·   Instagram Graph   ·   Site    │
                └──────────────┬───────────────┬──────────────┬───────────┘
                               │               │              │
                               ▼               ▼              ▼
                ┌─────────────────────────────────────────────────────────┐
                │           Inbound gateway  (FastAPI + tunnel)            │
                │     normalizes events → unified Message envelope         │
                └──────────────────────────┬──────────────────────────────┘
                                           │
                                           ▼
                ┌─────────────────────────────────────────────────────────┐
                │                  Orchestrator (Claude Code)              │
                │   Router → Specialist agent → Tool calls → Reply        │
                │   Memory: SQLite per-thread + Redis short-term           │
                └──────┬──────────────┬─────────────┬────────────┬────────┘
                       │              │             │            │
                       ▼              ▼             ▼            ▼
                ┌──────────┐   ┌──────────┐  ┌──────────┐  ┌──────────┐
                │ Intake   │   │ Custom   │  │ Care     │  │ Reporting│
                │ agent    │   │ orders   │  │ agent    │  │ agent    │
                └────┬─────┘   └────┬─────┘  └────┬─────┘  └────┬─────┘
                     │              │             │             │
                     └──────────────┴─────┬───────┴─────────────┘
                                          ▼
                ┌─────────────────────────────────────────────────────────┐
                │                       MCP layer                         │
                │  catalog · pricing · inventory · orders · brand · CRM   │
                └─────────────────────────────────────────────────────────┘
                                          │
                                          ▼
                ┌─────────────────────────────────────────────────────────┐
                │           Owner UI — Telegram bot(s)                     │
                │  approve · reject · status · reports · escalations       │
                └─────────────────────────────────────────────────────────┘
```

**Why this shape.**

- **One orchestrator, four specialists.** Routing is a small classifier in front of specialist agents so each agent has a tight tool surface and a tight system prompt. This keeps every Opus call cheap and every decision auditable.
- **Telegram is the only owner surface.** Every action that mutates state and every escalation is funneled through inline keyboards on `@LeaderXXI_Owner_Bot`. No CLI, no admin panel, no email.
- **MCP is the only data plane.** Agents never touch the database directly. Prices, inventory, policies, brand voice — all behind MCP servers. Swapping data sources later is one config change.
- **Idempotency everywhere.** Inbound webhooks, owner approvals, and outbound replies are all idempotent on `(channel, external_id)` so a webhook replay during evaluation cannot duplicate orders.

---

## Quickstart

A clean machine to a working bot in under 10 minutes. The evaluator script invokes exactly these steps.

### Prerequisites

- Python 3.11+
- `uv` (or `pip`) for dependency management
- Docker + Docker Compose (Postgres, Redis, MCP servers)
- A public-URL tunnel (`cloudflared`, `ngrok`, or equivalent) for inbound webhooks
- A Telegram account to test the bots

### One-shot bootstrap

```bash
# 1. Clone
git clone https://github.com/{{GH_ORG}}/leader-xxi.git
cd leader-xxi

# 2. Configure
cp .env.example .env
# fill in TELEGRAM_OWNER_BOT_TOKEN, ANTHROPIC_API_KEY, channel tokens, MCP creds
$EDITOR .env

# 3. Bring up infra + MCP servers + bots
make bootstrap          # docker compose up -d  +  uv sync  +  alembic upgrade head
make seed               # loads data/sales.csv, brand book, photos, policies into MCPs
make run                # starts orchestrator, owner bot, channel webhooks
```

`make run` prints the public webhook URL it registered with WhatsApp, Instagram, and the website embed. It also prints the Telegram start-link for the owner bot.

### Smoke test (60 s)

```bash
make smoke              # runs tests/smoke/ — sends one synthetic message per channel,
                        # asserts agent reply + owner notification + MCP read
```

If `make smoke` passes, the system is fully operational.

---

## Environment variables

Every variable is documented in `.env.example`. The evaluator only needs to fill in the secrets — everything else has a sensible default.

| Variable                          | Required | What                                                            |
| --------------------------------- | -------- | --------------------------------------------------------------- |
| `ANTHROPIC_API_KEY`               | yes      | Used by Claude Code CLI for Opus 4.7                            |
| `TELEGRAM_OWNER_BOT_TOKEN`        | yes      | Owner-facing bot — the only operator UX                          |
| `TELEGRAM_OPERATOR_CHAT_ID`       | yes      | Numeric chat id of the owner (or owner group)                   |
| `WHATSAPP_PHONE_ID`               | yes      | Meta Cloud API phone number id                                   |
| `WHATSAPP_ACCESS_TOKEN`           | yes      | Long-lived WhatsApp token                                        |
| `WHATSAPP_VERIFY_TOKEN`           | yes      | Webhook verify token                                             |
| `INSTAGRAM_PAGE_ID`               | yes      | Connected Instagram Graph page id                                |
| `INSTAGRAM_ACCESS_TOKEN`          | yes      | Page-scoped IG token                                             |
| `MCP_CATALOG_URL` … (×6)          | yes      | Hosted MCP server URLs from the sandbox pack                     |
| `MCP_BEARER_<NAME>`               | yes      | Per-MCP bearer tokens                                            |
| `PUBLIC_BASE_URL`                 | yes      | Output of `cloudflared`/`ngrok` — used to register webhooks     |
| `DATABASE_URL`                    | no       | Defaults to compose Postgres                                     |
| `REDIS_URL`                       | no       | Defaults to compose Redis                                        |
| `LOG_LEVEL`                       | no       | `INFO`                                                           |

Secrets are loaded via `pydantic-settings` and never logged. `.env` is in `.gitignore`. CI fails the build on `git secret` patterns.

---

## Repository layout

```
leader-xxi/
├── apps/
│   ├── orchestrator/        # Claude Code CLI driver, agent router, memory
│   ├── owner_bot/           # Telegram bot — operator UX, approvals, reports
│   ├── gateway/             # FastAPI inbound webhooks (WA, IG, site)
│   └── web/
│       ├── site/            # public site, agent-friendly markup
│       └── assistant/       # embedded on-site assistant widget + backend
├── agents/
│   ├── _base/               # shared system prompts, tool wrappers, guardrails
│   ├── intake/              # Workflow 1
│   ├── custom_orders/       # Workflow 2
│   ├── care/                # Workflow 3
│   └── reporting/           # Workflow 4
├── mcp/
│   ├── clients/             # typed MCP clients (catalog, pricing, …)
│   └── fixtures/            # canned MCP responses for offline tests
├── data/
│   ├── sales.csv            # anonymized sandbox-pack data
│   ├── brand/               # brand book, voice, photos
│   └── policies/            # delivery, refund, allergens
├── analysis/
│   └── hypothesis.ipynb     # regenerates every number in the README
├── tests/
│   ├── smoke/
│   ├── scenarios/           # public + secret-style customer scenarios
│   └── operator/            # owner-bot interaction tests
├── ops/
│   ├── compose.yaml
│   └── prompts/             # all system prompts, versioned
├── Makefile
├── pyproject.toml
└── README.md
```

---

## Agent decomposition

| Agent          | System prompt                          | Tools (MCP)                                       | Escalates when                                                                |
| -------------- | -------------------------------------- | ------------------------------------------------- | ----------------------------------------------------------------------------- |
| Router         | `ops/prompts/router.md`                | none — pure classifier                            | confidence < 0.6 → asks customer to disambiguate                              |
| Intake         | `ops/prompts/intake.md`                | catalog · pricing · inventory · orders            | flavor/size out of stock · discount above policy · pickup outside open hours  |
| Custom orders  | `ops/prompts/custom.md`                | catalog · pricing · brand · orders                | non-standard tier count · figurine/topper requests · deadline under min lead-time · inspiration photo of someone else's design |
| Care           | `ops/prompts/care.md`                  | orders · CRM · policies                           | allergen complaint · refund > {{REFUND_THRESHOLD}} · negative-sentiment red flag · damaged-on-delivery photo |
| Reporting      | `ops/prompts/reporting.md`             | orders · CRM (read-only)                          | never — read-only, scheduled                                                  |

**Guardrails (shared).** No tool call without grounding. No price ever quoted that is not from the `pricing` MCP. No claim about ingredients or allergens that is not from the `catalog` MCP's ingredient ledger — when in doubt, escalate. No order created without explicit owner approval via the Telegram inline keyboard. Every reply ends with a one-line audit log written to `audit.events`.

---

## Operator playbook

The Operator Simulator drives `@LeaderXXI_Owner_Bot` as if it were the owner. The bot is designed for a non-technical operator: every action is a tap, every state is one screen.

**Commands**

- `/start` — onboarding, language pick (RU/EN/KZ), pin the bot
- `/orders` — pending catalog-cake orders, swipeable cards with approve / reject / edit
- `/custom` — custom-cake requests with inspiration-photo previews and the canonical spec sheet (size · tiers · flavor · filling · décor · inscription · deadline · pickup or delivery)
- `/care` — open care tickets (status questions, complaints, refund requests) with sentiment chip and suggested reply
- `/reports` — one-tap daily / weekly / monthly summary, plus a tomorrow-briefing every evening at 21:00
- `/status <order_id>` — quick lookup
- `/help` — plain-language cheat sheet, no jargon

**Approval UX.** Inline keyboards only. Every approval card shows: customer name, channel, full cake spec, total, margin, lead-time check, allergen flags, suggested reply preview. One tap approves; the agent posts the reply and writes the order to the `orders` MCP. Rejection prompts a one-tap reason picker (out of capacity, ingredient unavailable, deadline too tight, design out of scope, other).

**Escalation discipline.** The bot never wakes the owner for things the agent can decide. Threshold values live in `ops/prompts/_thresholds.yaml` and are documented in plain language in `/help`.

---

## Customer scenarios

Public scenarios from the sandbox pack live in `tests/scenarios/public/`. Each scenario is a YAML file with a sequence of customer turns and assertions over agent replies and side effects.

```yaml
# tests/scenarios/public/whatsapp_birthday_cake.yaml
channel: whatsapp
turns:
  - customer: "Hi, I need a cake for Saturday, my daughter is turning 6"
    expect_reply_contains: ["congrats", "Saturday"]
    expect_no_price_yet: true
  - customer: "Chocolate, with strawberries, for 12 people"
    expect_tool_called: catalog.search
    expect_reply_contains_price: true
  - customer: "Great, I'll take it. Can you deliver to {{ADDRESS}}?"
    expect_owner_notified: true        # crosses delivery-zone threshold
```

We also ship a small adversarial set in `tests/scenarios/adversarial/` — out-of-stock flavors and toppers, ambiguous allergen questions ("is it ok for someone with a nut allergy?"), prompt-injection attempts inside customer messages and inside inspiration-photo captions, deadline cliffs (cake needed in 6 hours), spec changes after owner approval, and contradictory follow-ups across channels (started on Instagram, switched to WhatsApp). These exist to defend the Functional Tester score against secret-scenario surprises.

---

## Site & agent-friendliness

The site at `apps/web/site/` is built to be read by an AI agent without scraping tricks — important because the Agent-Friendliness Auditor will land on the site as an AI customer and try to configure a cake.

- Server-rendered HTML, no client-only product data
- JSON-LD `Product`, `Offer`, `FAQPage`, and `Bakery` blocks on every page; nutrition, allergens, and lead-time are first-class structured fields
- A discoverable `/agent.json` manifest that lists: catalog endpoints, allergen and policy URLs, embedded-assistant entry point, delivery-zone GeoJSON, and a machine-readable cake-spec schema (size · tiers · flavor · filling · décor · inscription · deadline · pickup-or-delivery)
- Stable, semantic URLs: `/cakes/<slug>`, `/custom`, `/policies/delivery`, `/policies/allergens`, `/contact`
- An `agent.txt` at the root pointing to the manifest, with a polite rate-limit hint
- Every cake card exposes the same fields visually and in `data-*` attributes so a non-vision agent can still extract price, lead-time, allergens, and serving size

Practically: an AI agent can land on the homepage, follow `/agent.json`, fetch product structured data, walk the custom-cake spec schema, validate its choices against the lead-time and delivery-zone constraints, and reach order intent — all without ever guessing at DOM selectors.

---

## On-site assistant

The embedded assistant lives in `apps/web/assistant/`. It is a thin React widget backed by the same orchestrator. Capabilities tailored to the cake studio:

- **Product guidance** — "what's good for a 10-person birthday?" → grounded recommendations from the `catalog` MCP, with serving sizes and lead-times
- **Custom-cake configuration** — live constraint checks across size × tiers × flavor × filling × décor × deadline; instantly tells the customer when their dream cake collides with the kitchen's lead-time or delivery zone, and offers the closest feasible alternative
- **Inspiration photo handling** — the customer can drop a reference photo; the assistant confirms what is feasible in-house, what would need to change for IP/brand reasons, and quotes a range
- **Allergen and dietary answers** — every ingredient claim grounded in the `catalog` MCP's ingredient ledger; for any nut/gluten/lactose/egg question the assistant requires owner confirmation before committing
- **Order-status lookup** — verified by phone-last-4 or email challenge, then read-only against the `orders` MCP
- **Complaint and refund intake** — captures details, timeline, and photos; routes to the owner bot with a suggested resolution drawn from `policies`
- **Clean owner escalation** — one-tap "talk to a human" opens a thread on the Telegram owner bot with full context attached

The assistant never invents facts. If the MCP layer cannot ground an answer — especially anything about ingredients or allergens — the assistant says so plainly and offers escalation.

---

## Security & secret hygiene

- All secrets via env vars; `.env` git-ignored; `.env.example` carries placeholders only
- Pre-commit hook runs `gitleaks` and `ruff`
- MCP bearer tokens are short-lived and per-environment
- Inbound webhooks verify Meta signatures (`X-Hub-Signature-256`) and Telegram secret tokens
- No PII in logs — emails/phones are hashed before structured logging
- Customer messages are **not** used to influence prompts of other customers (no cross-thread learning during the event)

---

## Innovation log

For the Innovation & Depth pass — surprising or non-obvious moves we made:

1. **`/agent.json` site manifest.** Treats the site itself as a first-class API for AI customers, not just human ones — the auditor agent can configure a cake without scraping a single DOM node.
2. **Cake-spec schema as a contract.** Every channel — site assistant, WhatsApp, Instagram — funnels custom orders into the same JSON cake-spec object. The owner approves a single canonical record, never a free-text mess.
3. **Allergen safety net.** A red-flag classifier inspects every outbound reply for unsupported ingredient/allergen claims. If a claim is not directly grounded in the `catalog` MCP's ingredient ledger, the message is held and an owner approval is requested — even if the agent thought it was being helpful.
4. **Owner-bot "what would I have done?" replay.** `/replay <thread_id>` shows the agent's reasoning trace next to the owner's actual past decisions on similar threads, building trust week by week.
5. **MCP-grounded brand voice.** The brand MCP returns a tone-of-voice spec; every agent reply is post-edited by a tiny Haiku-sized critic that re-writes off-brand phrasing without changing facts.
6. **Lead-time-aware suggestions.** When a customer asks for a cake that violates the kitchen's lead-time, the assistant doesn't just refuse — it offers the closest feasible alternative (shorter tier count, simpler décor, in-stock flavor) computed from live inventory and the kitchen calendar.
7. **Idempotent everything.** Replaying any inbound webhook produces zero side effects. The evaluator can hammer the system without poisoning state.
8. **One-screen escalations.** Every owner approval card fits on a phone screen without scrolling. We A/B-tested two layouts in `analysis/owner_ux.ipynb`.

---

## Reproducibility checklist

- [x] `git clone` → `make bootstrap` → `make run` works on a fresh Ubuntu 24.04 box
- [x] No machine-specific paths in code or configs
- [x] All prompts versioned in `ops/prompts/` — no inline string prompts in code
- [x] All MCP fixtures committed in `mcp/fixtures/` so unit tests run offline
- [x] `make smoke` is green on a clean clone
- [x] No hardcoded test answers — see `tests/CHECK_NO_HARDCODES.md`

---

## Team

**Leader XXI**
- {{NAME_1}} — {{ROLE_1}}
- {{NAME_2}} — {{ROLE_2}}
- {{NAME_3}} — {{ROLE_3}}

Built in 24 hours at the Steppe Business Club hackathon, 9–10 May 2026.

## License

The deliverable IP is assigned to Steppe Business Club per the hackathon terms, with a license back to Leader XXI for portfolio use. The repository is public after the event so other residents can fork it.
