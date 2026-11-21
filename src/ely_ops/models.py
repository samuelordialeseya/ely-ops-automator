"""Typed domain models for orders, bookkeeping reconciliation, SKU velocity, and AI briefings."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class OrderItem:
    name: str
    unit: str
    price: Decimal
    quantity: Decimal
    recorded_subtotal: Decimal


@dataclass(frozen=True)
class OrderRecord:
    id: str
    created_at: str
    raw_date: str
    display_date: str
    time_str: str
    customer_name: str
    address: str
    status: str
    order_type: str
    recorded_total: Decimal
    raw_total_str: str
    items: list[OrderItem] = field(default_factory=list)


@dataclass(frozen=True)
class ProductRecord:
    id: str
    name: str
    unit: str
    category: str
    price: Decimal


@dataclass(frozen=True)
class LedgerRow:
    order_id: str
    raw_date: str
    time_str: str
    customer_name: str
    delivery_zone: str
    channel: Literal["Delivery", "Walk-In"]
    status: str
    item_count: int
    computed_total: Decimal
    recorded_total: Decimal
    variance: Decimal
    had_float_noise: bool


@dataclass(frozen=True)
class SkuVelocity:
    sku_name: str
    unit: str
    category: str
    today_qty: Decimal
    today_revenue: Decimal
    trailing_7d_total_qty: Decimal
    trailing_7d_daily_avg: Decimal
    velocity_ratio: Decimal | None
    restock_signal: Literal["BUY MORE", "NORMAL", "BUY LESS", "NEW ITEM"]


@dataclass(frozen=True)
class AnomalyFlag:
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    category: Literal[
        "MATH_DRIFT",
        "FLOAT_PRECISION_NOISE",
        "UNCLOSED_PENDING",
        "BULK_ORDER_SPIKE",
        "CATALOG_MISMATCH",
    ]
    order_id: str | None
    summary: str
    detail: str


@dataclass(frozen=True)
class DailyReport:
    target_date: str
    store_name: str
    gross_revenue: Decimal
    recorded_revenue: Decimal
    reconciliation_variance: Decimal
    order_count: int
    total_line_items: int
    total_units_qty: Decimal
    average_order_value: Decimal
    delivery_orders: int
    walk_in_orders: int
    delivery_revenue: Decimal
    walk_in_revenue: Decimal
    zone_breakdown: dict[str, int]
    trailing_7d_active_days: int
    trailing_7d_avg_revenue: Decimal
    trailing_7d_avg_orders: Decimal
    trailing_7d_aov: Decimal
    revenue_delta_pct: Decimal | None
    float_noise_count: int
    pending_order_count: int
    ledger_rows: list[LedgerRow]
    sku_velocities: list[SkuVelocity]
    anomalies: list[AnomalyFlag]


class RestockPriority(BaseModel):
    item_name: str = Field(description="Produce SKU name from the verified velocity table")
    unit: str = Field(description="Unit of measure (kg, tali, pc, pack, tray, etc.)")
    signal: Literal["BUY MORE", "NORMAL", "BUY LESS", "NEW ITEM"] = Field(
        description="Morning market advice for tomorrow's 4:00 AM wholesale buy"
    )
    suggested_action: str = Field(
        description="Specific, practical wholesale buying note for the 4:00 AM market run"
    )


class OpsBriefing(BaseModel):
    headline: str = Field(
        description="One-sentence end-of-day summary citing verified peso revenue and order volume."
    )
    performance_summary: str = Field(
        description="2-3 sentences comparing today's sales and basket size against the 7-day baseline."
    )
    restock_priorities: list[RestockPriority] = Field(
        default_factory=list,
        description="Top 5-8 produce SKUs to prioritize for tomorrow's morning market purchase.",
    )
    anomalies_to_review: list[str] = Field(
        default_factory=list,
        description="Short actionable bullets for the bookkeeper or owner to verify.",
    )
    delivery_ops_note: str = Field(
        description="Brief operational observation on walk-in vs. neighborhood delivery demand."
    )
    generated_by: str = Field(
        default="gemini",
        description="Identifier of whether Gemini or deterministic fallback produced this briefing.",
    )
