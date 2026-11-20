"""CLI entrypoint for ely-ops-automator."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ely_ops.ai_briefing import generate_ops_briefing
from ely_ops.analytics import build_daily_report
from ely_ops.config import Settings
from ely_ops.emailer import send_briefing_email, write_email_preview
from ely_ops.exporter import (
    export_bookkeeper_ledger,
    export_briefing_json,
    export_sku_velocity,
)
from ely_ops.firestore_client import (
    ReadOnlyFirestoreClient,
    load_orders_from_csv,
    load_products_from_json,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ely_ops")


def _default_store_date(tz_name: str) -> str:
    return datetime.now(ZoneInfo(tz_name)).date().isoformat()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ely-ops",
        description="Read-only daily bookkeeping, anomaly audit, and AI restock pipeline for ely.pos.",
    )
    parser.add_argument(
        "--date",
        dest="target_date",
        default=None,
        help="Target store date (YYYY-MM-DD). Defaults to today in configured timezone.",
    )
    parser.add_argument(
        "--source",
        choices=["firestore", "csv"],
        default="firestore",
        help="Data source: live read-only Firestore (default) or local CSV export.",
    )
    parser.add_argument(
        "--csv-orders",
        default=None,
        help="Path to orders CSV export (required when --source csv).",
    )
    parser.add_argument(
        "--products-json",
        default=None,
        help="Optional path to products JSON backup (used with --source csv).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for generated CSV/JSON/HTML artifacts (defaults to OUTPUT_DIR).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate CSVs, JSON, and HTML email preview without sending SMTP email.",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip Gemini API and use the deterministic rule-based briefing fallback.",
    )
    return parser.parse_args(argv)


def run_pipeline(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    settings = Settings.from_env()
    target_date = args.target_date or _default_store_date(settings.timezone)
    target_dt = date.fromisoformat(target_date)
    window_start = (target_dt - timedelta(days=7)).isoformat()
    output_dir = Path(args.output_dir) if args.output_dir else settings.output_dir

    logger.info(
        "Starting ely-ops-automator | date=%s | window=%s..%s | source=%s | dry_run=%s",
        target_date,
        window_start,
        target_date,
        args.source,
        args.dry_run,
    )

    if args.source == "csv":
        if not args.csv_orders:
            raise ValueError("--csv-orders is required when --source csv is selected.")
        orders_window = load_orders_from_csv(
            args.csv_orders, start_date=window_start, end_date=target_date
        )
        products = (
            load_products_from_json(args.products_json) if args.products_json else []
        )
    else:
        client = ReadOnlyFirestoreClient(
            service_account_path=settings.firebase_service_account_path,
            service_account_b64=settings.firebase_service_account_b64,
        )
        orders_window = client.fetch_orders_for_range(window_start, target_date)
        products = client.fetch_products()

    logger.info(
        "Loaded %d orders across 8-day window and %d catalog products (read-only).",
        len(orders_window),
        len(products),
    )

    report = build_daily_report(
        target_date=target_date,
        orders_window=orders_window,
        products=products,
        store_name=settings.store_name,
    )

    briefing = generate_ops_briefing(
        report=report,
        api_key=settings.gemini_api_key,
        model_name=settings.gemini_model,
        use_ai=not args.no_ai,
    )

    ledger_path = export_bookkeeper_ledger(report, output_dir)
    velocity_path = export_sku_velocity(report, output_dir)
    json_path = export_briefing_json(report, briefing, output_dir)
    preview_path = write_email_preview(report, briefing, output_dir)

    logger.info("Exported ledger:   %s", ledger_path)
    logger.info("Exported velocity: %s", velocity_path)
    logger.info("Exported JSON:     %s", json_path)
    logger.info("Exported preview:  %s", preview_path)

    if args.dry_run:
        logger.info("Dry-run enabled; skipping SMTP delivery.")
    else:
        send_briefing_email(
            settings=settings,
            report=report,
            briefing=briefing,
            attachments=[ledger_path, velocity_path],
        )
        logger.info("Sent daily briefing email to %s", settings.report_recipient_email)

    elapsed = time.perf_counter() - started
    logger.info(
        "Completed in %.2fs | date=%s | orders=%d | revenue=PHP %.2f | anomalies=%d | engine=%s",
        elapsed,
        report.target_date,
        report.order_count,
        report.gross_revenue,
        len(report.anomalies),
        briefing.generated_by,
    )
    return 0


def cli() -> None:
    args = parse_args()
    sys.exit(run_pipeline(args))


if __name__ == "__main__":
    cli()
