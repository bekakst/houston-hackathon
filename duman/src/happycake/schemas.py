"""Pydantic schemas for HappyCake US.

Single source of truth for the data shapes that flow between MCP, agents, the
Telegram owner bot, and the storefront. Every customer-facing reply ends up
serialised through a model defined here so the brand-voice critic and the
audit trail can rely on stable keys.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Channels & roles ─────────────────────────────────────────────────────────


class Channel(str, Enum):
    web = "web"
    whatsapp = "whatsapp"
    instagram = "instagram"
    telegram = "telegram"
    walk_in = "walk-in"
    google_business = "google_business"


class Intent(str, Enum):
    intake = "intake"
    custom = "custom"
    care = "care"
    reporting = "reporting"
    escalate = "escalate"


# ── Catalog ──────────────────────────────────────────────────────────────────


class CakeSize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    weight_g: int = Field(gt=0)
    price_usd: float = Field(gt=0)
    mcp_product_id: str | None = None  # ties to square_list_catalog kitchenProductId


class Cake(BaseModel):
    """A catalog cake. Mirrors data/catalog.yaml entries 1:1."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    short_description: str
    long_description: str
    ingredients: list[str]
    allergens: list[str]
    halal_friendly: bool
    vegan: bool
    photo: str
    sizes: list[CakeSize]
    serves_min: int = Field(ge=1)
    serves_max: int = Field(ge=1)
    lead_time_hours: int = Field(ge=0)
    tier_options: list[int] = Field(min_length=1)
    delivery_zones: list[str]
    available_daily: bool

    @field_validator("serves_max")
    @classmethod
    def _serves_max_ge_min(cls, v: int, info) -> int:
        if "serves_min" in info.data and v < info.data["serves_min"]:
            raise ValueError("serves_max must be >= serves_min")
        return v

    def display_name(self) -> str:
        """Brandbook rule: cake names always quoted after the word 'cake'."""
        if self.slug == "custom":
            return "Custom cake"
        return f'cake "{self.name}"'


# ── Custom cake spec (the contract used across all four channels) ────────────


class CakeSpec(BaseModel):
    """Canonical custom-cake spec. Same JSON across web/WhatsApp/IG/Telegram."""

    model_config = ConfigDict(extra="forbid")

    base_cake_slug: str | None = None
    size_label: str | None = None
    weight_g: int | None = None
    tiers: int = Field(default=1, ge=1, le=3)
    flavor: str | None = None
    filling: str | None = None
    decoration: str | None = None
    inscription: str | None = None
    deadline: datetime | None = None
    fulfillment: Literal["pickup", "delivery"] | None = None
    delivery_zone: str | None = None
    delivery_address: str | None = None
    allergen_constraints: list[str] = Field(default_factory=list)
    notes: str | None = None

    def missing_slots(self) -> list[str]:
        required = ["size_label", "flavor", "deadline", "fulfillment"]
        if self.fulfillment == "delivery":
            required.extend(["delivery_zone", "delivery_address"])
        return [f for f in required if getattr(self, f) in (None, "", [])]

    def is_complete(self) -> bool:
        return not self.missing_slots()


# ── Orders ───────────────────────────────────────────────────────────────────


class OrderStatus(str, Enum):
    draft = "draft"
    pending_owner = "pending_owner"
    confirmed = "confirmed"
    in_kitchen = "in_kitchen"
    ready = "ready"
    delivered = "delivered"
    rejected = "rejected"
    refunded = "refunded"


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    customer_name: str
    customer_phone: str | None = None
    channel: Channel
    cake_slug: str
    cake_spec: CakeSpec | None = None
    size_label: str
    quantity: int = Field(default=1, ge=1)
    price_usd: float = Field(gt=0)
    delivery_fee_usd: float = Field(default=0, ge=0)
    total_usd: float = Field(gt=0)
    margin_usd: float = Field(default=0)
    fulfillment: Literal["pickup", "delivery"]
    delivery_zone: str | None = None
    delivery_address: str | None = None
    deadline: datetime
    status: OrderStatus = OrderStatus.draft
    created_at: datetime
    notes: str | None = None


# ── Messages, decisions, audit ───────────────────────────────────────────────


class Message(BaseModel):
    """An inbound or outbound message on any channel."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    channel: Channel
    direction: Literal["inbound", "outbound"]
    sender: str
    recipient: str | None = None
    text: str
    received_at: datetime
    thread_id: str | None = None
    external_id: str | None = None  # idempotency key from sha256


class Evidence(BaseModel):
    """A single tool call with redacted args + result snippet — the audit trail."""

    tool: str
    args: dict = Field(default_factory=dict)
    result_snippet: str
    at: datetime


class Reply(BaseModel):
    """Output contract from every specialist agent."""

    reply_to_customer: str
    needs_owner_approval: bool
    suggested_action: str | None = None
    draft_order_id: str | None = None
    draft_cake_spec: CakeSpec | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    intent: Intent | None = None


class OwnerDecision(BaseModel):
    """A pending decision the owner sees as an inline-keyboard card."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    kind: Literal["intake", "custom", "care", "marketing", "reporting"]
    channel: Channel
    customer_id: str
    customer_name: str
    thread_id: str
    summary: str  # human-readable card body
    draft_reply: str
    draft_order_id: str | None = None
    cake_spec: CakeSpec | None = None
    total_usd: float | None = None
    margin_usd: float | None = None
    margin_pct: float | None = None
    lead_time_ok: bool | None = None
    allergen_flags: list[str] = Field(default_factory=list)
    requires_kitchen_ack: bool = False
    created_at: datetime
    status: Literal["pending", "approved", "rejected", "edited", "expired"] = "pending"
    rejection_reason: str | None = None


class AuditEvent(BaseModel):
    """Append-only audit row written for every state mutation."""

    event_id: str
    kind: str
    payload: dict = Field(default_factory=dict)
    at: datetime
