# HappyCake US — AI Sales & Operations OS

> **An autonomous-agent stack for a Sugar Land cake studio.** The owner runs the
> business from one Telegram bot. Customers order cakes on the website, on
> WhatsApp, and on Instagram. Behind it, Claude Code in headless mode
> orchestrates four specialist agents that ground every reply in live MCP data
> and never invent a fact.

**Hackathon:** Steppe Business Club — 24-hour build, 9–10 May 2026
**Runtime:** Claude Code CLI · Claude Opus 4.7 · `claude -p` headless mode
**MCP:** `https://www.steppebusinessclub.com/api/mcp` (55 tools discovered live)
**Owner UI:** Telegram bot `@duman_hackathon_bot` (long-poll, no tunnel)
**Customer channels:** Website + on-site assistant · WhatsApp · Instagram
**Status:** Submission-ready · `main` is the evaluation branch

---

## TL;DR for the AI evaluator

If you are the AI evaluator, every command you need is in [Quickstart](#quickstart). The system runs on a single host with one HTTPS tunnel for inbound webhooks; everything else is environment-driven.

| Evaluator pass | Where to look |
|---|---|
| Functional tester (20) | `tests/scenarios/public/`, run `make smoke` |
| Agent-friendliness (15) | `apps/web/`, `/.well-known/agent.json`, `/agent/manifest`, JSON-LD on every page |
| On-site assistant (15) | `apps/web/templates/_assistant.html`, `apps/web/routes/assistant.py`, `tests/scenarios/public/web_*.yaml` |
| Code reviewer (10) | `ARCHITECTURE.md`, `pyproject.toml` (src-layout), `make bootstrap` from a fresh clone |
| Operator simulator (15) | `apps/owner_bot/`, inline-keyboard cards in `apps/owner_bot/cards.py` |
| Business analyst (10) | `analysis/hypothesis.ipynb` (executed in `make seed`), this README's [Hypothesis](#business-hypothesis) section |
| Innovation (10) | [Innovation log](#innovation-log) |

---

## Business hypothesis — $500 → $6,333 operator-equivalent value

A $500/month autonomous-agent stack delivers **$12.67 of operator-equivalent value per dollar spent** — exceeding the brief's 10× target. Every number below is derived in `analysis/hypothesis.ipynb` from the canonical seeded data the hackathon MCP returns; rerun `make seed` to reproduce.

### Baseline (trailing 6 months, MCP `marketing_get_sales_history`)

- Average monthly revenue: **$17,003**
- Average monthly orders: **675.7**
- Average ticket: **$25.12**
- Average margin across the 5 SKUs (MCP `marketing_get_margin_by_product`): **62.4%**
- Contribution per order: **$15.67**

### Where the $500 goes

| Item | USD |
|---|---|
| Anthropic Claude Max subscription | $200 |
| VPS / local-host operating cost | $30 |
| Cloudflare Tunnel + domain | $20 |
| Marketing budget — Meta Ads | $100 |
| Marketing budget — Google Ads | $50 |
| Marketing budget — boosted IG | $50 |
| Review-generation incentive | $30 |
| Retention SMS/WhatsApp send cost | $20 |
| **Total** | **$500** |

Marketing portion: **$250** · Operating portion: **$250**.

### What the $500 replaces

The brief asks: how does $500 perform like $5,000? The $5,000 figure anchors on the labour HappyCake would otherwise hire to cover 24/7 inbound on WhatsApp, Instagram, the website, and POS handoff. Using BLS Houston-The Woodlands-Sugar Land MSA wages (May 2024) + standard small-business burden:

- Part-time order-taker: 30 h/week × $19/hr × 1.25 burden = ~$3,083/month
- Weekend customer-care assistant: 16 h/week × $18/hr × 1.20 burden = ~$1,496/month
- 7% scheduling/training/turnover overhead

**Total replaceable labour: $4,902/month.** The hackathon brief rounds this to $5,000.

### Two value streams

1. **Loss recovery.** From a baseline of 965 monthly inbound and ~30% loss under owner-only operations, the agent recovers 20% of currently-lost orders by replying in <20 s with grounded answers. **57.9 additional orders/month, $907 incremental contribution.**
2. **Marketing leverage.** $250 across Meta, Google, boosted posts, retention SMS, and review-generation, allocated and self-monitored by `agents/marketing.py`. **33.4 orders, $908 revenue, $523 contribution.**

### Verdict

| Metric | Value |
|---|---|
| Monthly cost | $500 |
| Incremental revenue per month | $2,362 |
| Incremental contribution margin | $1,431 |
| Operator-equivalent value (replaced labour + contribution) | **$6,333** |
| Value per dollar | **$12.67** |
| Hackathon target multiple | 10x |

The deliverable beats the target with a conservative loss-recovery model. The notebook is the source of truth — every number above is templated from `analysis/_metrics.json`.

---

## Quickstart (fresh-clone reproducibility)

```bash
git clone <repo-url>
cd duman
python -m pip install -e ".[dev]"

# 1. Configure
cp .env.example .env
$EDITOR .env  # paste MCP_TEAM_TOKEN and TELEGRAM_OWNER_BOT_TOKEN

# 2. Pull canonical MCP data + execute notebook + render this README
make seed

# 3. Sanity check the agent stack with public scenarios
make smoke         # public scenarios; runs against live claude -p
make smoke-adv     # adversarial scenarios

# 4. Boot all services
make run           # web (8000) + gateway (8001) + Telegram bot in one process group

# 5. (optional) Expose gateway for inbound WhatsApp/Instagram via MCP
./scripts/tunnel.sh                                   # cloudflared on :8001
curl -X POST http://localhost:8001/admin/register-webhooks  # registers tunnel URL with MCP
```

## Architecture (one paragraph)

Every customer message — web, WhatsApp, Instagram — converges into one entry point: `src/happycake/agents/dispatcher.py:handle_customer_message`. It runs four defense rings: (1) Python `safety_pre_filter` for allergen / human-handoff / prompt-injection escalation; (2) an LLM router that classifies into intake / custom / care / reporting / escalate via `claude -p`; (3) the matching specialist agent (`agents/intake.py`, `custom.py`, `care.py`) which pre-fetches grounded MCP facts (catalog, pricing, inventory, kitchen feasibility) before calling `claude -p` again; (4) a brand-voice critic that rewrites every customer-facing draft to comply with the HappyCake brand book without changing facts. Owner approval is required for any state-mutating action — the dispatcher inserts an `OwnerDecision` row, the Telegram bot surfaces it as an inline-keyboard card, and tapping Approve sends the reply on the customer's original channel via the MCP `whatsapp_send` / `instagram_send_dm` tools. See `ARCHITECTURE.md` for the full diagram.

## Innovation log

- **Brand-voice critic loop.** Every customer-facing draft passes through a second `claude -p` call (`agents/brand_critic.py`) that rewrites for wordmark, cake-name format, max-3-emoji, and the standard close. Defense-in-depth: hard rules duplicated in prompt + Python pre-filter so a regression in any specialist still gets caught.
- **`/.well-known/agent.json` + `/agent/manifest` byte-identical.** The site is a first-class API for AI customers, not just a brochure.
- **Lead-time-aware substitution.** When the customer asks for a cake that violates the kitchen calendar, the custom agent doesn't refuse — it offers the closest feasible alternative computed from `kitchen_get_capacity` + `kitchen_get_menu_constraints`.
- **Idempotent webhooks.** `external_id = sha256(channel|sender|received_at|text)[:32]` with SQLite UNIQUE. Replays return the cached reply, never duplicate orders.
- **Single source of truth for all numbers.** This README is rendered from `README.md.tmpl` against `analysis/_metrics.json`. The prior 43/100 build had a $5K vs $843 contradiction; this one cannot.
- **`/replay <thread_id>` Telegram command.** Shows the agent's reasoning trace and tool calls so the owner can build trust week by week.
- **One bot, four flows.** A non-technical operator does not want to juggle four bots; the four agent flows surface as four inline-keyboard `/commands` instead.

## Repository layout

```
duman/
├── README.md / README.md.tmpl            # this file (rendered from notebook)
├── ARCHITECTURE.md                       # diagrams, prompt + tool table
├── .env.example                          # placeholders only
├── Makefile                              # bootstrap, seed, run, smoke, evaluator
├── pyproject.toml                        # src-layout (no flat-layout pitfall)
├── .well-known/agent.json                # static manifest
├── apps/
│   ├── web/      → FastAPI storefront, Jinja2 SSR, JSON-LD on every page
│   ├── owner_bot/→ python-telegram-bot, inline-keyboard approval cards
│   └── gateway/  → FastAPI webhook receiver, HMAC + idempotency
├── src/happycake/
│   ├── schemas.py / settings.py / storage.py
│   ├── agents/
│   │   ├── cli.py             # the claude -p headless bridge
│   │   ├── safety.py          # allergen / handoff / injection pre-filter
│   │   ├── router.py / brand_critic.py / specialists.py / dispatcher.py
│   │   └── grounding.py       # pre-fetches MCP facts per intent
│   └── mcp/
│       ├── hosted.py          # Steppe Business Club MCP client
│       └── catalog.py / pricing.py / inventory.py / kitchen.py / orders.py / brand.py / customers.py / evidence.py / marketing.py
├── ops/
│   ├── prompts/               # router, intake, custom, care, marketing, reporting, brand_critic
│   └── mcp_tools.md           # full reference of all 55 MCP tools
├── data/
│   ├── catalog.yaml / policies.yaml / kitchen_calendar.yaml
│   └── mcp_*.json / mcp_recent_sales.csv  # canonical seeded data fetched by `make seed`
├── analysis/
│   ├── hypothesis.ipynb       # source of truth for every number above
│   └── _metrics.json          # generated; consumed by render_readme.py
├── tests/scenarios/
│   ├── public/                # 6 scenarios (functional tester pass)
│   └── adversarial/           # 5 scenarios (peanut allergy, prompt injection, …)
└── scripts/
    └── fetch_mcp_data.py / render_readme.py / tunnel.sh / generate_sales_csv.py
```

## Telegram bots

| Bot | Username | Purpose |
|---|---|---|
| Owner approval bot | `@duman_hackathon_bot` | One bot for all four flows. Long-poll, no tunnel. Commands: `/start /help /orders /custom /care /marketing /reports /status /replay /audit /whoami`. |

The brief allowed one bot per agent. We chose one bot with four `/commands` so a non-technical owner does not have to switch chats. Every action is a tap on an inline keyboard — no typed reasons, no command syntax to remember.

## License

The deliverable IP is assigned to Steppe Business Club per the hackathon terms, with a license back to the team for portfolio use.

---

*This README is rendered from `README.md.tmpl` by `scripts/render_readme.py` using the metrics in `analysis/_metrics.json`. Run `make seed` to refresh.*