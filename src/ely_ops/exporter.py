"""Audit-ready CSV and JSON exporters for bookkeepers and CI archival."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from ely_ops.models import DailyReport, OpsBriefing


def _json_default(obj: object) -> object:
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def export_bookkeeper_ledger(report: DailyReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"bookkeeper_ledger_{report.target_date}.csv"
    fieldnames = [
        "order_id",
        "date",
        "time",
        "customer_name",
        "delivery_zone",
        "channel",
        "status",
        "item_count",
        "computed_total_php",
        "recorded_total_php",
        "variance_php",
        "had_float_noise",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in report.ledger_rows:
            writer.writerow(
                {
                    "order_id": row.order_id,
                    "date": row.raw_date,
                    "time": row.time_str,
                    "customer_name": row.customer_name,
                    "delivery_zone": row.delivery_zone,
                    "channel": row.channel,
                    "status": row.status,
                    "item_count": row.item_count,
                    "computed_total_php": f"{row.computed_total:.2f}",
                    "recorded_total_php": f"{row.recorded_total:.2f}",
                    "variance_php": f"{row.variance:.2f}",
                    "had_float_noise": "YES" if row.had_float_noise else "NO",
                }
            )
        # Summary footer row for quick spreadsheet ingestion
        writer.writerow(
            {
                "order_id": "TOTAL",
                "date": report.target_date,
                "time": "",
                "customer_name": f"{report.order_count} orders",
                "delivery_zone": "",
                "channel": "",
                "status": "",
                "item_count": report.total_line_items,
                "computed_total_php": f"{report.gross_revenue:.2f}",
                "recorded_total_php": f"{report.recorded_revenue:.2f}",
                "variance_php": f"{report.reconciliation_variance:.2f}",
                "had_float_noise": str(report.float_noise_count),
            }
        )
    return path


def export_sku_velocity(report: DailyReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"sku_velocity_{report.target_date}.csv"
    fieldnames = [
        "sku_name",
        "category",
        "unit",
        "today_qty",
        "today_revenue_php",
        "trailing_7d_total_qty",
        "trailing_7d_daily_avg_qty",
        "times_normal_sales",
        "morning_market_advice",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for sku in report.sku_velocities:
            writer.writerow(
                {
                    "sku_name": sku.sku_name,
                    "category": sku.category,
                    "unit": sku.unit,
                    "today_qty": f"{sku.today_qty:.3f}",
                    "today_revenue_php": f"{sku.today_revenue:.2f}",
                    "trailing_7d_total_qty": f"{sku.trailing_7d_total_qty:.3f}",
                    "trailing_7d_daily_avg_qty": f"{sku.trailing_7d_daily_avg:.3f}",
                    "times_normal_sales": (
                        f"{sku.velocity_ratio:.2f}"
                        if sku.velocity_ratio is not None
                        else ""
                    ),
                    "morning_market_advice": sku.restock_signal,
                }
            )
    return path


def export_briefing_json(
    report: DailyReport, briefing: OpsBriefing, output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"briefing_{report.target_date}.json"
    payload = {
        "target_date": report.target_date,
        "store_name": report.store_name,
        "summary_metrics": {
            "gross_revenue_php": report.gross_revenue,
            "recorded_revenue_php": report.recorded_revenue,
            "reconciliation_variance_php": report.reconciliation_variance,
            "order_count": report.order_count,
            "total_line_items": report.total_line_items,
            "average_order_value_php": report.average_order_value,
            "delivery_orders": report.delivery_orders,
            "walk_in_orders": report.walk_in_orders,
            "trailing_7d_active_days": report.trailing_7d_active_days,
            "trailing_7d_avg_revenue_php": report.trailing_7d_avg_revenue,
            "revenue_delta_pct": report.revenue_delta_pct,
            "float_noise_count": report.float_noise_count,
            "pending_order_count": report.pending_order_count,
        },
        "zone_breakdown": report.zone_breakdown,
        "anomalies": [asdict(a) for a in report.anomalies],
        "briefing": briefing.model_dump(),
    }
    path.write_text(
        json.dumps(payload, indent=2, default=_json_default), encoding="utf-8"
    )
    return path
