"""Read-only data adapters for live ely.pos Firestore and local CSV/JSON snapshots.

IMPORTANT: This module intentionally exposes ONLY read operations (.stream / .get).
No write, update, or delete methods exist against the live POS database.
"""

from __future__ import annotations

import base64
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ely_ops.models import OrderItem, OrderRecord, ProductRecord


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _parse_items(raw_items: Any) -> list[OrderItem]:
    if isinstance(raw_items, str):
        try:
            raw_items = json.loads(raw_items)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw_items, list):
        return []

    parsed: list[OrderItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        price = _to_decimal(entry.get("price"), "0")
        qty = _to_decimal(entry.get("quantity"), "1")
        subtotal = (
            _to_decimal(entry.get("subtotal"), "0")
            if entry.get("subtotal") is not None
            else price * qty
        )
        parsed.append(
            OrderItem(
                name=str(entry.get("name") or "Unknown").strip(),
                unit=str(entry.get("unit") or "pc").strip(),
                price=price,
                quantity=qty,
                recorded_subtotal=subtotal,
            )
        )
    return parsed


def _normalize_order_doc(doc_id: str, data: dict[str, Any]) -> OrderRecord:
    raw_total_value = data.get("total", 0)
    raw_total_str = "" if raw_total_value is None else str(raw_total_value).strip()
    created_at = data.get("created_at", "")
    if hasattr(created_at, "isoformat"):
        created_at_str = created_at.isoformat()
    else:
        created_at_str = str(created_at or "")

    return OrderRecord(
        id=str(doc_id),
        created_at=created_at_str,
        raw_date=str(data.get("raw_date") or "").strip(),
        display_date=str(data.get("display_date") or "").strip(),
        time_str=str(data.get("time") or "").strip(),
        customer_name=str(data.get("customer_name") or "Walk-in Customer").strip(),
        address=str(data.get("address") or "").strip(),
        status=str(data.get("status") or "Pending").strip(),
        order_type=str(data.get("order_type") or "pos").strip().lower(),
        recorded_total=_to_decimal(raw_total_value, "0"),
        raw_total_str=raw_total_str,
        items=_parse_items(data.get("items")),
    )


def _normalize_product_doc(doc_id: str, data: dict[str, Any]) -> ProductRecord:
    return ProductRecord(
        id=str(doc_id),
        name=str(data.get("name") or "").strip(),
        unit=str(data.get("unit") or "pc").strip(),
        category=str(data.get("category") or "Uncategorized").strip(),
        price=_to_decimal(data.get("price"), "0"),
    )


class ReadOnlyFirestoreClient:
    """Strictly read-only Firestore reader for the freshbyely POS database."""

    def __init__(
        self,
        service_account_path: str | None = None,
        service_account_b64: str | None = None,
    ) -> None:
        import firebase_admin
        from firebase_admin import credentials, firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        self._FieldFilter = FieldFilter

        if service_account_b64:
            decoded = base64.b64decode(service_account_b64).decode("utf-8")
            cert_dict = json.loads(decoded)
            cred = credentials.Certificate(cert_dict)
        elif service_account_path:
            cred_path = Path(service_account_path)
            if not cred_path.exists():
                raise FileNotFoundError(
                    f"Firebase service account JSON not found: {cred_path}"
                )
            cred = credentials.Certificate(str(cred_path))
        else:
            raise ValueError(
                "Provide FIREBASE_SERVICE_ACCOUNT_PATH or FIREBASE_SERVICE_ACCOUNT_B64."
            )

        app_name = "ely_ops_readonly"
        try:
            self._app = firebase_admin.get_app(app_name)
        except ValueError:
            self._app = firebase_admin.initialize_app(cred, name=app_name)

        self._db = firestore.client(app=self._app)

    def fetch_orders_for_range(self, start_date: str, end_date: str) -> list[OrderRecord]:
        """Read-only range query on root `orders` collection by `raw_date`."""
        query = (
            self._db.collection("orders")
            .where(filter=self._FieldFilter("raw_date", ">=", start_date))
            .where(filter=self._FieldFilter("raw_date", "<=", end_date))
        )
        orders: list[OrderRecord] = []
        for snap in query.stream():
            data = snap.to_dict() or {}
            orders.append(_normalize_order_doc(snap.id, data))
        orders.sort(key=lambda o: (o.raw_date, o.created_at))
        return orders

    def fetch_products(self) -> list[ProductRecord]:
        """Read-only fetch of root `products` catalog."""
        products: list[ProductRecord] = []
        for snap in self._db.collection("products").stream():
            data = snap.to_dict() or {}
            products.append(_normalize_product_doc(snap.id, data))
        products.sort(key=lambda p: p.name.lower())
        return products


def load_orders_from_csv(
    csv_path: str | Path,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[OrderRecord]:
    """Load orders from an ely.pos CSV export (useful for offline runs & tests)."""
    path = Path(csv_path)
    orders: list[OrderRecord] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_date = (row.get("raw_date") or "").strip()
            if start_date and raw_date < start_date:
                continue
            if end_date and raw_date > end_date:
                continue
            orders.append(_normalize_order_doc(row.get("id") or "", row))
    orders.sort(key=lambda o: (o.raw_date, o.created_at))
    return orders


def load_products_from_json(json_path: str | Path) -> list[ProductRecord]:
    """Load product catalog from a local JSON backup."""
    path = Path(json_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    products: list[ProductRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        products.append(_normalize_product_doc(item.get("id") or "", item))
    products.sort(key=lambda p: p.name.lower())
    return products
