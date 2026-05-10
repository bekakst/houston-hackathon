"""Generate data/sales.csv — 90 days of seeded operational data.

Drives the business-analyst evaluator pass. The shape mirrors what a Square POS
export would look like, plus a `channel` column with the realistic distribution
the prior submission's data was missing (40% walk-in, 25% WhatsApp, 20% IG, 15%
website). Friday evening + Saturday morning peaks. Mother's Day spike on
2026-05-10.

Reproducible: deterministic seed = 20260509.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEED = 20260509
random.seed(SEED)

CHANNELS = [
    ("walk-in",   0.40),
    ("whatsapp",  0.25),
    ("instagram", 0.20),
    ("website",   0.15),
]

LOSS_REASONS = [
    ("no_reply_30min",    0.45),
    ("allergen_unclear",  0.18),
    ("price_concern",     0.12),
    ("date_unavailable",  0.10),
    ("custom_too_complex", 0.08),
    ("other",             0.07),
]

CARE_TYPES = [
    "status",
    "complaint_late",
    "complaint_damaged",
    "complaint_taste",
    "refund_request",
    "general_question",
]


def weighted_choice(pairs: list[tuple]) -> str:
    r = random.random()
    cum = 0.0
    for value, weight in pairs:
        cum += weight
        if r < cum:
            return value
    return pairs[-1][0]


def hour_distribution() -> int:
    """Hourly demand profile: peaks at 18-22 Fri/Sat, midday otherwise."""
    return random.choices(
        population=list(range(8, 22)),
        weights=[1, 2, 3, 4, 5, 6, 7, 6, 5, 5, 6, 7, 6, 5],
        k=1,
    )[0]


def day_multiplier(d: datetime) -> float:
    weekday = d.weekday()  # Mon=0
    if weekday == 0:
        return 0.2  # closed
    if weekday in (4, 5):
        return 1.6  # Fri/Sat
    if weekday == 6:
        return 1.2  # Sun
    if d.date().isoformat() == "2026-05-10":
        return 3.0  # Mother's Day
    return 1.0


def main() -> None:
    catalog = yaml.safe_load((ROOT / "data" / "catalog.yaml").read_text())["cakes"]
    catalog_by_slug = {c["slug"]: c for c in catalog}
    sellable = [c for c in catalog if c["slug"] != "custom"]

    rows: list[dict] = []
    inbound_msgs: list[dict] = []
    end = datetime(2026, 5, 9, tzinfo=timezone.utc)
    start = end - timedelta(days=90)

    cur = start
    order_counter = 0
    msg_counter = 0
    while cur <= end:
        mult = day_multiplier(cur)
        base_orders = max(int(random.gauss(6, 2) * mult), 0)
        for _ in range(base_orders):
            cake = random.choices(
                sellable,
                weights=[
                    8 if c["slug"] == "honey" else
                    5 if c["slug"] in ("milk-maiden", "napoleon") else
                    3 if c["slug"] in ("tiramisu", "pistachio-roll", "cloud") else 2
                    for c in sellable
                ],
                k=1,
            )[0]
            size = random.choice(cake["sizes"])
            qty = 1 if size["label"] != "slice" else random.choice([1, 1, 2, 3])
            price = round(size["price_usd"] * qty, 2)
            channel = weighted_choice(CHANNELS)
            hour = hour_distribution()
            ts = cur.replace(hour=hour, minute=random.randint(0, 59))
            order_counter += 1
            order_id = f"ord_{cur.strftime('%Y%m%d')}_{order_counter:04d}"
            rows.append({
                "order_id": order_id,
                "ts": ts.isoformat(),
                "channel": channel,
                "cake_slug": cake["slug"],
                "cake_name": cake["name"],
                "size": size["label"],
                "quantity": qty,
                "price_usd": price,
                "fulfillment": random.choices(["pickup", "delivery"], weights=[7, 3], k=1)[0],
                "status": random.choices(
                    ["completed", "completed", "completed", "completed", "refunded"],
                    weights=[80, 80, 80, 80, 5], k=1,
                )[0],
            })

        # Inbound messages (with some unanswered to feed the loss model)
        msg_count = max(int(random.gauss(14, 4) * mult), 0)
        for _ in range(msg_count):
            channel = weighted_choice([c for c in CHANNELS if c[0] != "walk-in"])
            hour = hour_distribution()
            ts = cur.replace(hour=hour, minute=random.randint(0, 59))
            answered = random.random() < 0.78
            response_minutes = random.choices(
                [3, 5, 8, 12, 20, 35, 60, 120],
                weights=[5, 8, 10, 12, 14, 12, 10, 6], k=1,
            )[0] if answered else None
            msg_counter += 1
            inbound_msgs.append({
                "msg_id": f"msg_{cur.strftime('%Y%m%d')}_{msg_counter:05d}",
                "ts": ts.isoformat(),
                "channel": channel,
                "answered": answered,
                "response_minutes": response_minutes if response_minutes else "",
                "loss_reason": weighted_choice(LOSS_REASONS) if not answered else "",
                "intent": random.choices(
                    ["intake", "custom", "care", "general"],
                    weights=[50, 15, 20, 15], k=1,
                )[0],
                "care_type": random.choice(CARE_TYPES),
            })
        cur += timedelta(days=1)

    sales_csv = ROOT / "data" / "sales.csv"
    with sales_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    msgs_csv = ROOT / "data" / "messages.csv"
    with msgs_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(inbound_msgs[0].keys()))
        writer.writeheader()
        writer.writerows(inbound_msgs)

    print(f"Wrote {len(rows)} orders to {sales_csv.name}")
    print(f"Wrote {len(inbound_msgs)} messages to {msgs_csv.name}")
    by_channel = {}
    for r in rows:
        by_channel[r["channel"]] = by_channel.get(r["channel"], 0) + 1
    print("Order distribution by channel:", by_channel)
    by_msg_channel = {}
    for m in inbound_msgs:
        by_msg_channel[m["channel"]] = by_msg_channel.get(m["channel"], 0) + 1
    print("Message distribution by channel:", by_msg_channel)


if __name__ == "__main__":
    main()
