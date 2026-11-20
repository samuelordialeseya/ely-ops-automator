"""AI briefing synthesis using google-genai + Pydantic structured outputs, with safe fallback.

The LLM receives ONLY pre-verified deterministic Python aggregates—never raw order totals
to sum itself—eliminating arithmetic hallucinations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ely_ops.models import DailyReport, OpsBriefing, RestockPriority

logger = logging.getLogger(__name__)

# Hard allowlist of low-cost / free-tier Flash models so paid Pro models cannot run accidentally
FREE_TIER_MODELS = frozenset(
    {
        "gemini-3.8-flash",
        "gemini-3.8-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    }
)
MAX_PROMPT_CHARS = 12_000
MIN_COOLDOWN_SECONDS = 15

PERISHABLE_KEYWORDS = (
    "kang kong",
    "pechay",
    "petchay",
    "mustasa",
    "alogbati",
    "malunggay",
    "sitaw",
    "talbos",
    "lettuce",
    "broccoli",
    "brocolli",
    "spinach",
    "cilantro",
    "wansoy",
    "celery",
    "leeks",
    "mango",
    "mangga",
    "avocado",
    "banana",
    "lacatan",
    "papaya",
)


def _build_verified_payload(report: DailyReport) -> dict:
    top_skus = [
        {
            "sku": s.sku_name,
            "category": s.category,
            "unit": s.unit,
            "today_qty": float(s.today_qty),
            "today_revenue_php": float(s.today_revenue),
            "trailing_7d_daily_avg_qty": float(s.trailing_7d_daily_avg),
            "velocity_ratio": float(s.velocity_ratio) if s.velocity_ratio is not None else None,
            "restock_signal": s.restock_signal,
        }
        for s in report.sku_velocities[:15]
    ]
    anomalies = [
        {"severity": a.severity, "category": a.category, "summary": a.summary}
        for a in report.anomalies[:8]
    ]
    return {
        "store_name": report.store_name,
        "date": report.target_date,
        "kpis": {
            "gross_revenue_php": float(report.gross_revenue),
            "order_count": report.order_count,
            "total_line_items": report.total_line_items,
            "average_order_value_php": float(report.average_order_value),
            "delivery_orders": report.delivery_orders,
            "walk_in_orders": report.walk_in_orders,
            "delivery_revenue_php": float(report.delivery_revenue),
            "walk_in_revenue_php": float(report.walk_in_revenue),
            "top_delivery_zones": dict(list(report.zone_breakdown.items())[:6]),
            "trailing_7d_active_days": report.trailing_7d_active_days,
            "trailing_7d_avg_revenue_php": float(report.trailing_7d_avg_revenue),
            "trailing_7d_avg_orders": float(report.trailing_7d_avg_orders),
            "revenue_delta_vs_7d_pct": (
                float(report.revenue_delta_pct)
                if report.revenue_delta_pct is not None
                else None
            ),
        },
        "top_sku_velocities": top_skus,
        "detected_anomalies": anomalies,
    }


def build_fallback_briefing(report: DailyReport) -> OpsBriefing:
    """Deterministic rule-based briefing used when Gemini is disabled or unavailable."""
    if report.order_count == 0:
        return OpsBriefing(
            headline=f"No POS transactions recorded for {report.target_date}.",
            performance_summary=(
                "Zero orders were logged in ely.pos on this date. Confirm whether the "
                "store was closed or if offline tablet sync is still pending."
            ),
            restock_priorities=[],
            anomalies_to_review=[
                "Verify iPad POS internet sync if the store operated today."
            ],
            delivery_ops_note="No deliveries logged.",
            generated_by="deterministic-fallback",
        )

    delta_phrase = ""
    if report.revenue_delta_pct is not None:
        sign = "+" if report.revenue_delta_pct >= 0 else ""
        delta_phrase = (
            f" ({sign}{report.revenue_delta_pct}% vs. {report.trailing_7d_active_days}-day "
            f"active average of ₱{report.trailing_7d_avg_revenue:,.2f})"
        )

    headline = (
        f"Closed {report.target_date} at ₱{report.gross_revenue:,.2f} across "
        f"{report.order_count} orders{delta_phrase}."
    )
    performance_summary = (
        f"Average order value finished at ₱{report.average_order_value:,.2f} across "
        f"{report.total_line_items} line items. Delivery accounted for "
        f"{report.delivery_orders} orders (₱{report.delivery_revenue:,.2f}) and walk-in "
        f"accounted for {report.walk_in_orders} orders (₱{report.walk_in_revenue:,.2f})."
    )

    def _fmt_qty(val: object) -> str:
        s = f"{val:.3f}".rstrip("0").rstrip(".")
        return s or "0"

    restock: list[RestockPriority] = []
    for sku in report.sku_velocities[:6]:
        lower_name = sku.sku_name.lower()
        is_perishable = any(k in lower_name for k in PERISHABLE_KEYWORDS)
        qty_str = _fmt_qty(sku.today_qty)
        if sku.restock_signal == "HIGH":
            if is_perishable:
                action = (
                    f"Moved {qty_str} {sku.unit} ({sku.velocity_ratio}x 7d avg). "
                    "Increase tomorrow's buy by 15-20%, but cap buffer to avoid spoilage."
                )
            else:
                action = (
                    f"Moved {qty_str} {sku.unit} ({sku.velocity_ratio}x 7d avg). "
                    "Restock aggressively (+25% buffer) for tomorrow's 4 AM market run."
                )
        elif sku.restock_signal == "NEW":
            action = (
                f"Sold {qty_str} {sku.unit} today with no prior 7-day movement. "
                "Keep a modest replenishment batch until repeat demand confirms."
            )
        else:
            action = (
                f"Steady volume at {qty_str} {sku.unit} "
                f"(₱{sku.today_revenue:,.2f}); maintain standard daily par level."
            )

        restock.append(
            RestockPriority(
                item_name=sku.sku_name,
                unit=sku.unit,
                signal=sku.restock_signal,
                suggested_action=action,
            )
        )

    anomaly_notes = [f"[{a.severity}] {a.summary}" for a in report.anomalies[:5]]
    if not anomaly_notes:
        anomaly_notes = ["All order totals reconciled cleanly with zero variance flags."]

    top_zones = ", ".join(
        f"{zone} ({cnt})" for zone, cnt in list(report.zone_breakdown.items())[:3]
    )
    delivery_note = (
        f"Top order channels/zones: {top_zones}."
        if top_zones
        else "No delivery zone concentration recorded."
    )

    return OpsBriefing(
        headline=headline,
        performance_summary=performance_summary,
        restock_priorities=restock,
        anomalies_to_review=anomaly_notes,
        delivery_ops_note=delivery_note,
        generated_by="deterministic-fallback",
    )


def _check_and_record_ai_quota(
    state_file: Path,
    max_calls_per_day: int,
    min_cooldown_seconds: int = MIN_COOLDOWN_SECONDS,
) -> tuple[bool, str | None]:
    """Enforce a hard daily AI call ceiling & cooldown to prevent runaway quota burn."""
    today = datetime.now(timezone.utc).date().isoformat()
    now_ts = datetime.now(timezone.utc).timestamp()

    state = {"date": today, "count": 0, "last_call_ts": 0.0}
    if state_file.exists():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("date") == today:
                state["count"] = int(loaded.get("count", 0))
                state["last_call_ts"] = float(loaded.get("last_call_ts", 0.0))
        except (ValueError, OSError):
            pass

    if state["count"] >= max_calls_per_day:
        return False, f"Daily AI safety cap reached ({state['count']}/{max_calls_per_day})."

    elapsed = now_ts - state["last_call_ts"]
    if state["last_call_ts"] > 0 and elapsed < min_cooldown_seconds:
        return (
            False,
            f"Cooldown active ({elapsed:.1f}s < {min_cooldown_seconds}s) to prevent accidental burst.",
        )

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state["count"] += 1
    state["last_call_ts"] = now_ts
    state_file.write_text(json.dumps(state), encoding="utf-8")
    return True, None


def generate_ops_briefing(
    report: DailyReport,
    api_key: str | None,
    model_name: str = "gemini-3.5-flash-lite",
    use_ai: bool = True,
    *,
    allow_non_free_model: bool = False,
    max_output_tokens: int = 1200,
    max_calls_per_day: int = 10,
    rate_limit_file: Path | None = None,
) -> OpsBriefing:
    """Generate a structured owner briefing via Gemini with strict free-tier safety walls."""
    if not use_ai or not api_key or report.order_count == 0:
        return build_fallback_briefing(report)

    if not allow_non_free_model and model_name not in FREE_TIER_MODELS:
        logger.warning(
            "Model '%s' is outside FREE_TIER_MODELS %s; falling back to deterministic briefing.",
            model_name,
            sorted(FREE_TIER_MODELS),
        )
        return build_fallback_briefing(report)

    payload_text = json.dumps(_build_verified_payload(report), indent=2)
    if len(payload_text) > MAX_PROMPT_CHARS:
        logger.warning(
            "Prompt payload (%d chars) exceeds safety limit (%d); using deterministic fallback.",
            len(payload_text),
            MAX_PROMPT_CHARS,
        )
        return build_fallback_briefing(report)

    if rate_limit_file is not None:
        allowed, reason = _check_and_record_ai_quota(rate_limit_file, max_calls_per_day)
        if not allowed:
            logger.warning("AI safety wall triggered: %s Using deterministic fallback.", reason)
            return build_fallback_briefing(report)

    try:
        from google import genai
        from google.genai import types
        import time

        system_instruction = (
            "You are an experienced Philippine fresh-produce retail operations lead and "
            "bookkeeper advising the owner of Ely's Fresh Fruits & Veggies. "
            "Use ONLY the verified numbers provided in the JSON payload. NEVER invent "
            "prices, totals, or SKUs not present in the input. "
            "Apply practical fresh-produce buying heuristics: leafy greens and ripe "
            "fruits spoil within 1-2 days (keep buffers tight even on spikes), whereas "
            "hardy produce (onions, garlic, potatoes, squash, eggs) can carry higher "
            "restock buffers. Be concise, practical, and zero fluff."
        )
        prompt = (
            "Generate the End-of-Day Owner & Bookkeeper Ops Briefing strictly matching "
            "the JSON schema from this verified POS analytics snapshot:\n\n"
            f"{payload_text}"
        )

        client = genai.Client(api_key=api_key)

        models_to_try = [model_name]
        for fallback_candidate in ("gemini-3.5-flash-lite", "gemini-3.8-flash-lite", "gemini-2.0-flash"):
            if fallback_candidate in FREE_TIER_MODELS and fallback_candidate not in models_to_try:
                models_to_try.append(fallback_candidate)

        last_exc: Exception | None = None
        for target_model in models_to_try:
            for attempt in range(2):
                try:
                    response = client.models.generate_content(
                        model=target_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.2,
                            max_output_tokens=max_output_tokens,
                            response_mime_type="application/json",
                            response_schema=OpsBriefing,
                        ),
                    )

                    if response.parsed is not None and isinstance(response.parsed, OpsBriefing):
                        briefing = response.parsed
                        briefing.generated_by = f"gemini:{target_model}"
                        return briefing

                    raw_text = response.text or ""
                    briefing = OpsBriefing.model_validate_json(raw_text)
                    briefing.generated_by = f"gemini:{target_model}"
                    return briefing
                except Exception as exc:
                    last_exc = exc
                    err_msg = str(exc)
                    if any(term in err_msg for term in ("503", "UNAVAILABLE", "high demand")):
                        logger.warning(
                            "Model %s attempt %d hit high demand (503); backing off 3s...",
                            target_model,
                            attempt + 1,
                        )
                        time.sleep(3)
                        continue
                    break

        if last_exc:
            logger.warning("Gemini briefing failed (%s); using deterministic fallback.", last_exc)
        return build_fallback_briefing(report)
    except Exception as exc:  # pragma: no cover - defensive runtime safeguard
        logger.warning("Gemini briefing failed (%s); using deterministic fallback.", exc)
        return build_fallback_briefing(report)
