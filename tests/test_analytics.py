"""Unit tests for deterministic bookkeeping math, anomaly detection, exports, and fallback."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from ely_ops.ai_briefing import (
    _check_and_record_ai_quota,
    build_fallback_briefing,
    generate_ops_briefing,
)
from ely_ops.analytics import (
    build_daily_report,
    has_float_precision_noise,
    is_delivery_order,
    normalize_delivery_zone,
)
from ely_ops.emailer import render_html_email, render_plaintext_email
from ely_ops.exporter import (
    export_bookkeeper_ledger,
    export_briefing_json,
    export_sku_velocity,
)
from ely_ops.models import OrderItem, OrderRecord, ProductRecord


def _sample_products() -> list[ProductRecord]:
    return [
        ProductRecord("p1", "Lacatan (Davao)", "kg", "Fruits", Decimal("155")),
        ProductRecord("p2", "Kamatis", "kg", "Vegetables", Decimal("85")),
        ProductRecord("p3", "Kang Kong", "tali", "Vegetables", Decimal("25")),
    ]


def _sample_orders() -> list[OrderRecord]:
    return [
        # Trailing 7-day baseline order (day -2)
        OrderRecord(
            id="ord-hist-1",
            created_at="2026-05-04T02:00:00Z",
            raw_date="2026-05-04",
            display_date="5/4/2026",
            time_str="10:00 AM",
            customer_name="Ana R.",
            address="P4",
            status="Delivered",
            order_type="pos",
            recorded_total=Decimal("310.00"),
            raw_total_str="310.00",
            items=[
                OrderItem(
                    name="Lacatan (Davao)",
                    unit="kg",
                    price=Decimal("155"),
                    quantity=Decimal("2.000"),
                    recorded_subtotal=Decimal("310.00"),
                )
            ],
        ),
        # Target date order 1: float noise + delivery shorthand "4"
        OrderRecord(
            id="ord-today-1",
            created_at="2026-05-06T03:00:00Z",
            raw_date="2026-05-06",
            display_date="5/6/2026",
            time_str="11:00 AM",
            customer_name="Donna N.",
            address="4",
            status="Pending",
            order_type="pos",
            recorded_total=Decimal("642.2699999999999"),
            raw_total_str="642.2699999999999",
            items=[
                OrderItem(
                    name="Lacatan (Davao)",
                    unit="kg",
                    price=Decimal("155"),
                    quantity=Decimal("3.500"),
                    recorded_subtotal=Decimal("542.50"),
                ),
                OrderItem(
                    name="Kamatis",
                    unit="kg",
                    price=Decimal("85"),
                    quantity=Decimal("1.174"),
                    recorded_subtotal=Decimal("99.79"),
                ),
            ],
        ),
        # Target date order 2: walk-in + true math drift + bulk spike
        OrderRecord(
            id="ord-today-2",
            created_at="2026-05-06T05:00:00Z",
            raw_date="2026-05-06",
            display_date="5/6/2026",
            time_str="01:00 PM",
            customer_name="Walk-in Customer",
            address="Walk-in",
            status="Delivered",
            order_type="pos",
            recorded_total=Decimal("1000.00"),
            raw_total_str="1000.00",
            items=[
                OrderItem(
                    name="Lacatan (Davao)",
                    unit="kg",
                    price=Decimal("155"),
                    quantity=Decimal("6.000"),
                    recorded_subtotal=Decimal("930.00"),
                ),
                OrderItem(
                    name="Kang Kong",
                    unit="tali",
                    price=Decimal("25"),
                    quantity=Decimal("2"),
                    recorded_subtotal=Decimal("50.00"),
                ),
            ],
        ),
    ]


def test_delivery_and_zone_normalization() -> None:
    orders = _sample_orders()
    assert is_delivery_order(orders[1]) is True
    assert is_delivery_order(orders[2]) is False
    assert normalize_delivery_zone("4", True) == "Phase 4"
    assert normalize_delivery_zone("P2", True) == "Phase 2"
    assert normalize_delivery_zone("Venare nuvali", True) == "Venare Nuvali"
    assert normalize_delivery_zone("Walk-in", False) == "Walk-In"


def test_float_noise_detection() -> None:
    assert has_float_precision_noise("908.4599999999999") is True
    assert has_float_precision_noise("908.46") is False
    assert has_float_precision_noise("150") is False


def test_build_daily_report_reconciliation_and_anomalies() -> None:
    report = build_daily_report(
        target_date="2026-05-06",
        orders_window=_sample_orders(),
        products=_sample_products(),
        store_name="Ely's Fresh Fruits & Veggies",
    )

    # Order 1 exact: 542.50 + 99.79 = 642.29; Order 2 exact: 930.00 + 50.00 = 980.00
    assert report.order_count == 2
    assert report.gross_revenue == Decimal("1622.29")
    assert report.recorded_revenue == Decimal("1642.27")
    assert report.reconciliation_variance == Decimal("-19.98")
    assert report.delivery_orders == 1
    assert report.walk_in_orders == 1
    assert report.float_noise_count == 1
    assert report.pending_order_count == 1

    categories = {a.category for a in report.anomalies}
    assert "MATH_DRIFT" in categories
    assert "BULK_ORDER_SPIKE" in categories
    assert "FLOAT_PRECISION_NOISE" in categories
    assert "UNCLOSED_PENDING" in categories

    # Lacatan sold 9.5 kg vs 2.0 kg/day trailing -> 4.75x -> HIGH signal
    lacatan = next(s for s in report.sku_velocities if s.sku_name == "Lacatan (Davao)")
    assert lacatan.today_qty == Decimal("9.500")
    assert lacatan.velocity_ratio == Decimal("4.75")
    assert lacatan.restock_signal == "HIGH"


def test_exports_and_fallback_email_render(tmp_path: Path) -> None:
    report = build_daily_report(
        target_date="2026-05-06",
        orders_window=_sample_orders(),
        products=_sample_products(),
        store_name="Ely's Fresh Fruits & Veggies",
    )
    briefing = build_fallback_briefing(report)
    assert briefing.generated_by == "deterministic-fallback"
    assert len(briefing.restock_priorities) >= 2

    ledger_path = export_bookkeeper_ledger(report, tmp_path)
    velocity_path = export_sku_velocity(report, tmp_path)
    json_path = export_briefing_json(report, briefing, tmp_path)

    assert ledger_path.exists()
    assert velocity_path.exists()
    assert json_path.exists()

    rows = list(csv.DictReader(ledger_path.open("r", encoding="utf-8")))
    assert len(rows) == 3  # 2 orders + TOTAL footer
    assert rows[-1]["order_id"] == "TOTAL"
    assert rows[-1]["computed_total_php"] == "1622.29"

    html_body = render_html_email(report, briefing)
    text_body = render_plaintext_email(report, briefing)
    assert "1,622.29" in html_body
    assert "Lacatan (Davao)" in html_body
    assert "1,622.29" in text_body


def test_ai_free_tier_safety_walls(tmp_path: Path) -> None:
    report = build_daily_report(
        target_date="2026-05-06",
        orders_window=_sample_orders(),
        products=_sample_products(),
        store_name="Ely's Fresh Fruits & Veggies",
    )

    # 1. Non-free-tier model is blocked and falls back safely without calling Gemini
    blocked = generate_ops_briefing(
        report=report,
        api_key="dummy-key-never-called",
        model_name="gemini-2.5-pro",
        use_ai=True,
        allow_non_free_model=False,
    )
    assert blocked.generated_by == "deterministic-fallback"

    # 2. Daily rate limit + cooldown guard blocks burst loops
    state_file = tmp_path / ".ai_rate_limit.json"
    ok1, _ = _check_and_record_ai_quota(state_file, max_calls_per_day=1, min_cooldown_seconds=15)
    ok2, reason2 = _check_and_record_ai_quota(state_file, max_calls_per_day=1, min_cooldown_seconds=15)
    assert ok1 is True
    assert ok2 is False
    assert reason2 is not None and "cap reached" in reason2

    # 3. Cooldown guard blocks rapid consecutive calls even when daily cap > 1
    state_file2 = tmp_path / ".ai_rate_limit_burst.json"
    ok3, _ = _check_and_record_ai_quota(state_file2, max_calls_per_day=5, min_cooldown_seconds=60)
    ok4, reason4 = _check_and_record_ai_quota(state_file2, max_calls_per_day=5, min_cooldown_seconds=60)
    assert ok3 is True
    assert ok4 is False
    assert reason4 is not None and "Cooldown" in reason4


def test_repo_contains_no_leaked_secrets() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for folder in ("src", "tests", ".github"):
        for file_path in (repo_root / folder).rglob("*"):
            if file_path.is_file() and file_path.suffix in {".py", ".yml", ".yaml", ".md"}:
                content = file_path.read_text(encoding="utf-8")
                assert "AIza" not in content, f"Possible Google API key in {file_path}"
                assert "BEGIN PRIVATE KEY" not in content, f"Possible private key in {file_path}"
