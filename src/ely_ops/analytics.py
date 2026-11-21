"""Deterministic retail bookkeeping reconciliation, SKU velocity, and anomaly detection.

All financial math uses decimal.Decimal (centavo exact, ROUND_HALF_UP) so the LLM layer
never performs arithmetic on raw transaction records.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from ely_ops.models import (
    AnomalyFlag,
    DailyReport,
    LedgerRow,
    OrderRecord,
    ProductRecord,
    SkuVelocity,
)

CENTAVO = Decimal("0.01")
GRAM = Decimal("0.001")
MATH_DRIFT_THRESHOLD = Decimal("0.05")
BULK_ORDER_MULTIPLIER = Decimal("2.5")


def round_centavos(value: Decimal) -> Decimal:
    return value.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def round_weight(value: Decimal) -> Decimal:
    return value.quantize(GRAM, rounding=ROUND_HALF_UP)


def is_delivery_order(order: OrderRecord) -> bool:
    """Parity with ely.pos moneyUtils.isDeliveryOrder (handles v1 order_type='pos')."""
    if order.order_type == "delivery":
        return True
    addr = order.address.strip().lower()
    return bool(addr and addr not in {"walk-in", "walk in", "in-store"})


def normalize_delivery_zone(address: str, is_delivery: bool) -> str:
    """Normalize shorthand subdivision phases (e.g., '4', 'P4', 'Phase 4') for reporting."""
    if not is_delivery:
        return "Walk-In"
    cleaned = re.sub(r"\s+", " ", address.strip())
    if not cleaned:
        return "Unspecified Delivery"

    # Match bare digits like "2", "4" or "P1".."P9" -> "Phase X"
    phase_match = re.fullmatch(r"(?:p(?:hase)?\s*)?([1-9])", cleaned, flags=re.IGNORECASE)
    if phase_match:
        return f"Phase {phase_match.group(1)}"

    # Normalize common Nuvali / nearby village names (including common cashier typos)
    lower = cleaned.lower()
    if "venare" in lower:
        return "Venare Nuvali"
    if "trev" in lower or "triv" in lower:
        return "Treveia Nuvali"
    if lower in {"m.f.", "mf"}:
        return "M.F."
    return cleaned.title() if cleaned.islower() else cleaned


def has_float_precision_noise(raw_total_str: str) -> bool:
    """Detect IEEE-754 float artifacts (e.g., '908.4599999999999') in stored order totals."""
    if "." not in raw_total_str:
        return False
    fractional = raw_total_str.split(".", 1)[1].rstrip("0")
    return len(fractional) > 2


def compute_order_total(order: OrderRecord) -> Decimal:
    """Recompute exact centavo order total from line items."""
    total = Decimal("0.00")
    for item in order.items:
        line = round_centavos(item.price * round_weight(item.quantity))
        total += line
    return round_centavos(total)


def _normalize_sku_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def build_daily_report(
    target_date: str,
    orders_window: list[OrderRecord],
    products: list[ProductRecord],
    store_name: str,
) -> DailyReport:
    """Build a verified DailyReport comparing target_date against its trailing 7-day window."""
    target_dt = date.fromisoformat(target_date)
    trailing_start = (target_dt - timedelta(days=7)).isoformat()
    trailing_end = (target_dt - timedelta(days=1)).isoformat()

    today_orders = [o for o in orders_window if o.raw_date == target_date]
    trailing_orders = [
        o for o in orders_window if trailing_start <= o.raw_date <= trailing_end
    ]

    catalog_by_key: dict[str, ProductRecord] = {
        _normalize_sku_key(p.name): p for p in products if p.name
    }

    # --- 1. Trailing 7-Day Baseline ---
    trailing_days_set = {o.raw_date for o in trailing_orders}
    trailing_active_days = len(trailing_days_set)
    trailing_total_rev = sum(
        (compute_order_total(o) for o in trailing_orders), Decimal("0.00")
    )
    trailing_order_count = len(trailing_orders)

    if trailing_active_days > 0:
        trailing_avg_rev = round_centavos(
            trailing_total_rev / Decimal(trailing_active_days)
        )
        trailing_avg_orders = (
            Decimal(trailing_order_count) / Decimal(trailing_active_days)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    else:
        trailing_avg_rev = Decimal("0.00")
        trailing_avg_orders = Decimal("0.0")

    trailing_aov = (
        round_centavos(trailing_total_rev / Decimal(trailing_order_count))
        if trailing_order_count > 0
        else Decimal("0.00")
    )

    # Trailing 7-day SKU quantities
    trailing_sku_qty: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for order in trailing_orders:
        for item in order.items:
            key = _normalize_sku_key(item.name)
            trailing_sku_qty[key] += round_weight(item.quantity)

    # --- 2. Target Date Reconciliation & Ledger ---
    ledger_rows: list[LedgerRow] = []
    anomalies: list[AnomalyFlag] = []
    zone_breakdown: dict[str, int] = defaultdict(int)

    gross_revenue = Decimal("0.00")
    recorded_revenue = Decimal("0.00")
    total_line_items = 0
    total_units_qty = Decimal("0")
    delivery_orders = 0
    walk_in_orders = 0
    delivery_revenue = Decimal("0.00")
    walk_in_revenue = Decimal("0.00")
    float_noise_count = 0
    pending_order_count = 0

    today_sku_stats: dict[str, dict[str, object]] = {}
    catalog_mismatch_seen: set[str] = set()

    for order in today_orders:
        computed = compute_order_total(order)
        recorded = round_centavos(order.recorded_total)
        variance = round_centavos(computed - recorded)
        float_noise = has_float_precision_noise(order.raw_total_str)

        delivery = is_delivery_order(order)
        zone = normalize_delivery_zone(order.address, delivery)
        channel = "Delivery" if delivery else "Walk-In"

        gross_revenue += computed
        recorded_revenue += recorded
        total_line_items += len(order.items)

        if delivery:
            delivery_orders += 1
            delivery_revenue += computed
        else:
            walk_in_orders += 1
            walk_in_revenue += computed

        zone_breakdown[zone] += 1

        if float_noise:
            float_noise_count += 1

        if order.status.strip().lower() == "pending":
            pending_order_count += 1

        # Anomaly 1: True line-item math drift > ₱0.05
        if abs(variance) > MATH_DRIFT_THRESHOLD:
            anomalies.append(
                AnomalyFlag(
                    severity="HIGH",
                    category="MATH_DRIFT",
                    order_id=order.id,
                    summary=f"Order total variance of ₱{variance:+.2f} for {order.customer_name}",
                    detail=(
                        f"Recomputed line items = ₱{computed:.2f}, "
                        f"recorded POS total = ₱{recorded:.2f}."
                    ),
                )
            )

        # Anomaly 2: Bulk/high-value spike (> 2.5x trailing AOV when baseline exists)
        if trailing_aov > Decimal("0") and computed >= round_centavos(
            trailing_aov * BULK_ORDER_MULTIPLIER
        ):
            anomalies.append(
                AnomalyFlag(
                    severity="MEDIUM",
                    category="BULK_ORDER_SPIKE",
                    order_id=order.id,
                    summary=(
                        f"High-value order ₱{computed:,.2f} from {order.customer_name} "
                        f"({zone})"
                    ),
                    detail=(
                        f"Exceeds 2.5x trailing 7-day AOV (₱{trailing_aov:,.2f}); "
                        "check stock impact on top line items."
                    ),
                )
            )

        for item in order.items:
            qty = round_weight(item.quantity)
            line_rev = round_centavos(item.price * qty)
            total_units_qty += qty

            key = _normalize_sku_key(item.name)
            cat_product = catalog_by_key.get(key)

            if key not in today_sku_stats:
                today_sku_stats[key] = {
                    "name": cat_product.name if cat_product else item.name,
                    "unit": item.unit or (cat_product.unit if cat_product else "pc"),
                    "category": cat_product.category if cat_product else "Uncategorized",
                    "qty": Decimal("0"),
                    "revenue": Decimal("0.00"),
                }
            today_sku_stats[key]["qty"] = (
                today_sku_stats[key]["qty"] + qty  # type: ignore[operator]
            )
            today_sku_stats[key]["revenue"] = (
                today_sku_stats[key]["revenue"] + line_rev  # type: ignore[operator]
            )

            # Anomaly 3: Catalog mismatch (only report once per SKU per day)
            if products and key not in catalog_mismatch_seen:
                if cat_product is None:
                    catalog_mismatch_seen.add(key)
                    anomalies.append(
                        AnomalyFlag(
                            severity="LOW",
                            category="CATALOG_MISMATCH",
                            order_id=order.id,
                            summary=f"Sold SKU '{item.name}' not found in active catalog",
                            detail=(
                                f"Sold at ₱{item.price:.2f}/{item.unit}; verify spelling or "
                                "archived product name in ely.pos."
                            ),
                        )
                    )
                elif (
                    cat_product.price > Decimal("0")
                    and abs(item.price - cat_product.price) >= Decimal("10")
                ):
                    catalog_mismatch_seen.add(key)
                    anomalies.append(
                        AnomalyFlag(
                            severity="LOW",
                            category="CATALOG_MISMATCH",
                            order_id=order.id,
                            summary=(
                                f"Price delta on '{cat_product.name}': sold at "
                                f"₱{item.price:.2f} vs catalog ₱{cat_product.price:.2f}"
                            ),
                            detail=(
                                "Wholesale price may have shifted between order date "
                                "and current catalog snapshot."
                            ),
                        )
                    )

        ledger_rows.append(
            LedgerRow(
                order_id=order.id,
                raw_date=order.raw_date,
                time_str=order.time_str,
                customer_name=order.customer_name,
                delivery_zone=zone,
                channel=channel,
                status=order.status,
                item_count=len(order.items),
                computed_total=computed,
                recorded_total=recorded,
                variance=variance,
                had_float_noise=float_noise,
            )
        )

    # Aggregate operational anomalies (keep list concise and high-signal)
    if float_noise_count > 0:
        anomalies.append(
            AnomalyFlag(
                severity="LOW",
                category="FLOAT_PRECISION_NOISE",
                order_id=None,
                summary=(
                    f"Cleaned floating-point precision noise on {float_noise_count} of "
                    f"{len(today_orders)} orders"
                ),
                detail=(
                    "Raw POS totals contained >2 decimal places (IEEE-754 artifacts); "
                    "normalized to exact centavos in bookkeeper ledger."
                ),
            )
        )

    if pending_order_count > 0:
        anomalies.append(
            AnomalyFlag(
                severity="MEDIUM",
                category="UNCLOSED_PENDING",
                order_id=None,
                summary=(
                    f"{pending_order_count} of {len(today_orders)} orders remain marked "
                    "'Pending' at end of day"
                ),
                detail=(
                    "Confirm rider dispatch/payment collection and mark settled tickets "
                    "as Delivered in ely.pos."
                ),
            )
        )

    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    category_rank = {
        "MATH_DRIFT": 0,
        "UNCLOSED_PENDING": 1,
        "BULK_ORDER_SPIKE": 2,
        "FLOAT_PRECISION_NOISE": 3,
        "CATALOG_MISMATCH": 4,
    }
    anomalies.sort(
        key=lambda a: (severity_rank.get(a.severity, 9), category_rank.get(a.category, 9))
    )

    # --- 3. SKU Velocity & Restock Signals ---
    sku_velocities: list[SkuVelocity] = []
    denom_days = Decimal(max(trailing_active_days, 1))
    for key, stats in today_sku_stats.items():
        today_qty: Decimal = stats["qty"]  # type: ignore[assignment]
        today_rev: Decimal = round_centavos(stats["revenue"])  # type: ignore[arg-type]
        trail_total = round_weight(trailing_sku_qty.get(key, Decimal("0")))
        trail_daily = (
            round_weight(trail_total / denom_days)
            if trailing_active_days > 0
            else Decimal("0.000")
        )

        if trail_daily > Decimal("0"):
            ratio = (today_qty / trail_daily).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if ratio >= Decimal("1.35"):
                signal = "BUY MORE"
            elif ratio <= Decimal("0.60"):
                signal = "BUY LESS"
            else:
                signal = "NORMAL"
        else:
            ratio = None
            signal = "NEW ITEM"

        sku_velocities.append(
            SkuVelocity(
                sku_name=str(stats["name"]),
                unit=str(stats["unit"]),
                category=str(stats["category"]),
                today_qty=round_weight(today_qty),
                today_revenue=today_rev,
                trailing_7d_total_qty=trail_total,
                trailing_7d_daily_avg=trail_daily,
                velocity_ratio=ratio,
                restock_signal=signal,
            )
        )

    sku_velocities.sort(key=lambda s: (s.today_revenue, s.today_qty), reverse=True)

    # --- 4. Daily KPIs ---
    order_count = len(today_orders)
    aov = (
        round_centavos(gross_revenue / Decimal(order_count))
        if order_count > 0
        else Decimal("0.00")
    )
    revenue_delta_pct: Decimal | None = None
    if trailing_avg_rev > Decimal("0"):
        revenue_delta_pct = (
            ((gross_revenue - trailing_avg_rev) / trailing_avg_rev) * Decimal("100")
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    reconciliation_variance = round_centavos(gross_revenue - recorded_revenue)
    sorted_zones = dict(sorted(zone_breakdown.items(), key=lambda kv: kv[1], reverse=True))

    return DailyReport(
        target_date=target_date,
        store_name=store_name,
        gross_revenue=round_centavos(gross_revenue),
        recorded_revenue=round_centavos(recorded_revenue),
        reconciliation_variance=reconciliation_variance,
        order_count=order_count,
        total_line_items=total_line_items,
        total_units_qty=round_weight(total_units_qty),
        average_order_value=aov,
        delivery_orders=delivery_orders,
        walk_in_orders=walk_in_orders,
        delivery_revenue=round_centavos(delivery_revenue),
        walk_in_revenue=round_centavos(walk_in_revenue),
        zone_breakdown=sorted_zones,
        trailing_7d_active_days=trailing_active_days,
        trailing_7d_avg_revenue=trailing_avg_rev,
        trailing_7d_avg_orders=trailing_avg_orders,
        trailing_7d_aov=trailing_aov,
        revenue_delta_pct=revenue_delta_pct,
        float_noise_count=float_noise_count,
        pending_order_count=pending_order_count,
        ledger_rows=ledger_rows,
        sku_velocities=sku_velocities,
        anomalies=anomalies,
    )
