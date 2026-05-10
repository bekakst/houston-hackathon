# Steppe Business Club Hackathon MCP — Tool Reference

**Endpoint:** `https://www.steppebusinessclub.com/api/mcp`
**Auth:** `X-Team-Token: <token>` header (NOT Authorization: Bearer)
**Transport:** HTTP + JSON-RPC 2.0
**Server:** `steppebusinessclub-hackathon-mcp v1.0.0`
**Discovered:** 2026-05-09 via `tools/list`

55 tools across 8 categories. This file is the agent's source of truth — every
ops/prompts/*.md references the tools it's allowed to call. Only the local
`load_catalog` / `load_policies` are used as fallback when MCP is unreachable.

---

## square_* — POS catalog, orders, inventory (7 tools)

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `square_list_catalog` | — | `limit` | Simulator-first Happy Cake POS catalog |
| `square_get_inventory` | `variationIds` | — | Inventory for variation IDs |
| `square_recent_orders` | — | `sinceISO`, `limit` | Recent simulator POS orders |
| `square_create_order` | `items` | `source`, `customerName`, `customerNote` | Creates POS order from website / whatsapp / instagram / walk-in / agent. **Follow with kitchen_create_ticket.** |
| `square_update_order_status` | `orderId`, `status` | `note` | Update after approval / kitchen / ready / completion / cancel |
| `square_get_pos_summary` | — | — | Per-team simulator POS summary for evaluator |
| `square_recent_sales_csv` | — | — | **Canonical 6-month seeded sales CSV.** Read-only. Drives the marketing-budget reasoning. |

## whatsapp_* — message I/O + webhook (4 tools)

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `whatsapp_send` | `to`, `message` | — | Send to whitelisted simulated customer |
| `whatsapp_list_threads` | — | — | Recent WhatsApp conversations |
| `whatsapp_register_webhook` | `url` | — | **Register our public tunnel URL.** MCP forwards inbound events here. |
| `whatsapp_inject_inbound` | `from`, `message` | — | **Test-only.** Inject a fake inbound for dry-run. Used by evaluator AND by us. |

## instagram_* — DMs, comments, posts with approval queue (7 tools)

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `instagram_list_dm_threads` | — | — | Recent IG DM conversations |
| `instagram_send_dm` | `threadId`, `message` | — | Reply on a thread |
| `instagram_reply_to_comment` | `commentId`, `message` | — | Reply under a post |
| `instagram_schedule_post` | `imageUrl`, `caption` | `scheduledFor` | Queue post for owner approval. Returns `scheduledPostId`. **Posts NEVER publish until** `instagram_publish_post` is called. |
| `instagram_publish_post` | `scheduledPostId` | — | Publish approved post. Errors if not approved. |
| `instagram_approve_post` | `scheduledPostId` | — | Owner-side approve via Telegram bot. |
| `instagram_register_webhook` | `url` | — | Register our public tunnel URL for inbound DM/comment events. |
| `instagram_inject_dm` | `threadId`, `from`, `message` | — | Test-only inbound injection. |

## gb_* — Google Business reviews + simulated posts (5 tools)

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `gb_list_reviews` | — | — | Recent reviews |
| `gb_simulate_reply` | `reviewId`, `reply` | — | Record proposed reply. Simulated only. Evaluator checks existence + wording. |
| `gb_simulate_post` | `content` | `callToAction`, `photoUrl` | Record proposed GMB post. Simulated. |
| `gb_get_metrics` | — | `period` | Views, calls, direction requests |
| `gb_list_simulated_actions` | — | — | All recorded GMB actions for this team |

## marketing_* — $500 → $5,000 loop (10 tools)

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `marketing_get_budget` | — | — | **Monthly constraint + target.** $500 → $5,000. |
| `marketing_get_sales_history` | — | — | Anonymized monthly sales for planning |
| `marketing_get_margin_by_product` | — | — | Seeded pricing + margin for budget allocation |
| `marketing_create_campaign` | `name`, `channel`, `objective`, `budgetUsd`, `targetAudience`, `offer` | `landingPath` | Records campaign plan |
| `marketing_launch_simulated_campaign` | `campaignId` | `approvalNote` | Launch + record impressions/clicks/leads/orders estimates |
| `marketing_get_campaign_metrics` | — | `campaignId` | Campaign metrics |
| `marketing_generate_leads` | `campaignId` | — | Generate simulated leads |
| `marketing_route_lead` | `leadId`, `routeTo`, `reason` | — | Record routing into a sales channel |
| `marketing_adjust_campaign` | `campaignId`, `adjustment` | `expectedImpact` | Record agent adjustment |
| `marketing_report_to_owner` | — | — | Summary for owner |

## kitchen_* — production tickets (6 tools)

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `kitchen_get_capacity` | — | — | Capacity, lead time defaults, current load |
| `kitchen_get_menu_constraints` | — | — | Per-item prep/lead-time/capacity/custom constraints |
| `kitchen_create_ticket` | `orderId`, `customerName`, `items` | `requestedPickupAt`, `notes` | Production ticket from order intent |
| `kitchen_list_tickets` | — | `status` | All tickets, optionally filtered |
| `kitchen_accept_ticket` | `ticketId` | `note` | Accept queued ticket |
| `kitchen_reject_ticket` | `ticketId`, `reason` | — | Reject when not feasible |
| `kitchen_mark_ready` | `ticketId` | `pickupNote` | Ready for pickup |
| `kitchen_get_production_summary` | — | — | For evaluator: counts, capacity, rejections, readiness |

## world_* — deterministic scenarios (the evaluator's playground) (7 tools)

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `world_get_scenarios` | — | — | Available time-compressed scenarios |
| `world_start_scenario` | `scenarioId` | `seed` | Start scenario, reset team timeline |
| `world_next_event` | — | — | Deliver next deterministic event |
| `world_inject_event` | `channel`, `type`, `payload` | `priority` | Custom evaluator/test event |
| `world_advance_time` | `minutes` | — | Advance scenario clock |
| `world_get_timeline` | — | — | Per-team world timeline (debug/scoring) |
| `world_get_scenario_summary` | — | — | Progress: delivered events, channel mix, minute, remaining |

## evaluator_* — self-scoring (USE THESE BEFORE SUBMISSION) (5 tools)

| Tool | Required | Optional | Returns |
|---|---|---|---|
| `evaluator_get_evidence_summary` | — | — | Per-team evidence across world / marketing / POS / kitchen / channels / mcp_audit_log |
| `evaluator_score_marketing_loop` | — | — | Score $500 → $5,000 loop |
| `evaluator_score_pos_kitchen_flow` | — | — | Score POS + kitchen handoff |
| `evaluator_score_channel_response` | — | — | Score WhatsApp + IG + GMB response |
| `evaluator_score_world_scenario` | — | — | Score deterministic world execution |
| `evaluator_generate_team_report` | — | `repoUrl`, `websiteUrl`, `notes` | Combined evidence report for judges |

---

## Architectural implications

1. **Replace local `data/sales.csv`** with a `make seed` step that calls
   `square_recent_sales_csv`. The notebook uses the canonical seed.

2. **Replace local kitchen calendar** with calls to
   `kitchen_get_capacity` + `kitchen_get_menu_constraints`. Local YAML stays
   as fallback only.

3. **Webhook gateway** must call `whatsapp_register_webhook(public_base_url + "/whatsapp")`
   and `instagram_register_webhook(public_base_url + "/instagram")` at
   startup. Inbound payloads come via the registered URL.

4. **Order placement flow** for the agents:
   - `square_create_order(items, source, customerName)` →
   - `square_update_order_status(orderId, "pending_owner")` →
   - owner approves on Telegram →
   - `square_update_order_status(orderId, "confirmed")` →
   - `kitchen_create_ticket(orderId, customerName, items, requestedPickupAt)` →
   - `kitchen_accept_ticket(ticketId)` (or `kitchen_reject_ticket` with reason).

5. **Instagram post pipeline** for marketing:
   - `instagram_schedule_post(imageUrl, caption)` returns `scheduledPostId` →
   - owner sees inline keyboard in Telegram →
   - on approve: `instagram_approve_post(scheduledPostId)` then
     `instagram_publish_post(scheduledPostId)`.

6. **Self-judge dry run before submission:**
   - `world_start_scenario(...)` → drive scenario via our agents →
   - `evaluator_get_evidence_summary` →
   - `evaluator_score_*` → fix the lowest two.
