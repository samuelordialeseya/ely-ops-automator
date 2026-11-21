"""HTML + plaintext email renderer and Gmail SMTP sender with CSV ledger attachment."""

from __future__ import annotations

import html
import smtplib
from email.message import EmailMessage
from pathlib import Path

from ely_ops.config import Settings
from ely_ops.models import DailyReport, OpsBriefing

SIGNAL_COLORS = {
    "BUY MORE": ("#9A3412", "#FFEDD5"),
    "NORMAL": ("#166534", "#DCFCE7"),
    "BUY LESS": ("#475569", "#F1F5F9"),
    "NEW ITEM": ("#1D4ED8", "#DBEAFE"),
    # Legacy fallbacks
    "HIGH": ("#9A3412", "#FFEDD5"),
    "LOW": ("#475569", "#F1F5F9"),
    "NEW": ("#1D4ED8", "#DBEAFE"),
}


def render_plaintext_email(report: DailyReport, briefing: OpsBriefing) -> str:
    lines = [
        f"{report.store_name} — End-of-Day Ops Briefing ({report.target_date})",
        "=" * 64,
        briefing.headline,
        "",
        "SUMMARY METRICS",
        f"  Gross Revenue:   PHP {report.gross_revenue:,.2f}",
        f"  Orders:          {report.order_count} ({report.delivery_orders} Delivery / {report.walk_in_orders} Walk-In)",
        f"  Avg Order Value: PHP {report.average_order_value:,.2f}",
        f"  Line Items Sold: {report.total_line_items}",
        f"  Reconcile Diff:  PHP {report.reconciliation_variance:+.2f}",
        "",
        "PERFORMANCE",
        f"  {briefing.performance_summary}",
        "",
        "4:00 AM WHOLESALE MARKET BUYING GUIDE",
    ]
    if briefing.restock_priorities:
        for item in briefing.restock_priorities:
            lines.append(
                f"  - [{item.signal}] {item.item_name} ({item.unit}): {item.suggested_action}"
            )
    else:
        lines.append("  - No market buying recommendations flagged.")

    lines.extend(["", "ANOMALIES & BOOKKEEPER NOTES"])
    for note in briefing.anomalies_to_review:
        lines.append(f"  - {note}")

    lines.extend(
        [
            "",
            "DELIVERY OPS",
            f"  {briefing.delivery_ops_note}",
            "",
            f"Generated automatically by ely-ops-automator ({briefing.generated_by}).",
            "Attached: bookkeeper_ledger CSV.",
        ]
    )
    return "\n".join(lines)


def render_html_email(report: DailyReport, briefing: OpsBriefing) -> str:
    esc = html.escape

    delta_badge = ""
    if report.revenue_delta_pct is not None:
        sign = "+" if report.revenue_delta_pct >= 0 else ""
        color = "#15803D" if report.revenue_delta_pct >= 0 else "#B91C1C"
        delta_badge = (
            f'<div style="font-size:12px;color:{color};margin-top:4px;font-weight:600;">'
            f"{sign}{report.revenue_delta_pct}% vs {report.trailing_7d_active_days}d avg"
            "</div>"
        )

    restock_rows_html = ""
    for item in briefing.restock_priorities:
        fg, bg = SIGNAL_COLORS.get(item.signal, ("#334155", "#F1F5F9"))
        restock_rows_html += (
            "<tr>"
            f'<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;font-weight:600;color:#0F172A;">'
            f"{esc(item.item_name)} "
            f'<span style="font-weight:400;color:#64748B;">({esc(item.unit)})</span></td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;white-space:nowrap;">'
            f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
            f'font-size:11px;font-weight:700;color:{fg};background:{bg};">'
            f"{esc(item.signal)}</span></td>"
            f'<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:#334155;font-size:13px;line-height:1.45;">'
            f"{esc(item.suggested_action)}</td>"
            "</tr>"
        )
    if not restock_rows_html:
        restock_rows_html = (
            '<tr><td colspan="3" style="padding:12px;color:#64748B;font-size:13px;">'
            "No market buying recommendations generated for this date.</td></tr>"
        )

    anomaly_items_html = "".join(
        f'<li style="margin-bottom:6px;color:#334155;font-size:13px;line-height:1.5;">'
        f"{esc(note)}</li>"
        for note in briefing.anomalies_to_review
    )

    zone_chips_html = "".join(
        f'<span style="display:inline-block;margin:0 6px 6px 0;padding:4px 10px;'
        f'background:#F8FAFC;border:1px solid #E2E8F0;border-radius:4px;'
        f'font-size:12px;color:#334155;">'
        f"<strong>{esc(zone)}</strong>: {count}</span>"
        for zone, count in list(report.zone_breakdown.items())[:8]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(report.store_name)} — Daily Ops Briefing ({esc(report.target_date)})</title>
</head>
<body style="margin:0;padding:24px 12px;background-color:#F4F5F7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0F172A;">
  <div style="max-width:680px;margin:0 auto;background:#FFFFFF;border:1px solid #E2E8F0;border-radius:10px;overflow:hidden;">
    <div style="padding:24px 28px;background:#0F172A;color:#FFFFFF;">
      <div style="font-size:12px;letter-spacing:0.06em;text-transform:uppercase;color:#94A3B8;font-weight:600;">
        {esc(report.store_name)}
      </div>
      <h1 style="margin:6px 0 4px 0;font-size:21px;line-height:1.3;font-weight:700;color:#FFFFFF;">
        End-of-Day Bookkeeping &amp; Market Buying Briefing
      </h1>
      <div style="font-size:13px;color:#CBD5E1;">
        Store Date: <strong>{esc(report.target_date)}</strong>
      </div>
    </div>

    <div style="padding:24px 28px;">
      <p style="margin:0 0 20px 0;font-size:15px;line-height:1.55;color:#1E293B;font-weight:500;">
        {esc(briefing.headline)}
      </p>

      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:0 0 22px 0;">
        <tr>
          <td width="32%" style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:14px;vertical-align:top;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.04em;color:#64748B;font-weight:600;">Reconciled Sales</div>
            <div style="font-size:20px;font-weight:700;color:#0F172A;margin-top:4px;">&#8369;{report.gross_revenue:,.2f}</div>
            {delta_badge}
          </td>
          <td width="2%"></td>
          <td width="32%" style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:14px;vertical-align:top;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.04em;color:#64748B;font-weight:600;">Orders</div>
            <div style="font-size:20px;font-weight:700;color:#0F172A;margin-top:4px;">{report.order_count}</div>
            <div style="font-size:12px;color:#475569;margin-top:4px;">{report.delivery_orders} delivery &middot; {report.walk_in_orders} walk-in</div>
          </td>
          <td width="2%"></td>
          <td width="32%" style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:14px;vertical-align:top;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.04em;color:#64748B;font-weight:600;">Avg Order Value</div>
            <div style="font-size:20px;font-weight:700;color:#0F172A;margin-top:4px;">&#8369;{report.average_order_value:,.2f}</div>
            <div style="font-size:12px;color:#475569;margin-top:4px;">{report.total_line_items} line items</div>
          </td>
        </tr>
      </table>

      <h2 style="margin:0 0 8px 0;font-size:14px;text-transform:uppercase;letter-spacing:0.04em;color:#475569;">
        Performance vs. 7-Day Baseline
      </h2>
      <p style="margin:0 0 22px 0;font-size:14px;line-height:1.55;color:#334155;">
        {esc(briefing.performance_summary)}
      </p>

      <h2 style="margin:0 0 10px 0;font-size:14px;text-transform:uppercase;letter-spacing:0.04em;color:#475569;">
        4:00 AM Wholesale Market Buying Guide
      </h2>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #E2E8F0;border-radius:6px;margin-bottom:22px;">
        <thead>
          <tr style="background:#F8FAFC;">
            <th align="left" style="padding:9px 12px;font-size:11px;text-transform:uppercase;color:#64748B;border-bottom:1px solid #E2E8F0;">Item</th>
            <th align="left" style="padding:9px 12px;font-size:11px;text-transform:uppercase;color:#64748B;border-bottom:1px solid #E2E8F0;">Morning Advice</th>
            <th align="left" style="padding:9px 12px;font-size:11px;text-transform:uppercase;color:#64748B;border-bottom:1px solid #E2E8F0;">Action / Recommendation</th>
          </tr>
        </thead>
        <tbody>
          {restock_rows_html}
        </tbody>
      </table>

      <h2 style="margin:0 0 8px 0;font-size:14px;text-transform:uppercase;letter-spacing:0.04em;color:#475569;">
        Bookkeeper Audit &amp; Exceptions
      </h2>
      <div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:12px 16px;margin-bottom:22px;">
        <ul style="margin:0;padding-left:18px;">
          {anomaly_items_html}
        </ul>
      </div>

      <h2 style="margin:0 0 8px 0;font-size:14px;text-transform:uppercase;letter-spacing:0.04em;color:#475569;">
        Delivery Zones
      </h2>
      <p style="margin:0 0 10px 0;font-size:13px;line-height:1.5;color:#334155;">
        {esc(briefing.delivery_ops_note)}
      </p>
      <div style="margin-bottom:18px;">
        {zone_chips_html}
      </div>

      <div style="border-top:1px solid #E2E8F0;padding-top:14px;font-size:12px;color:#64748B;">
        Attached: <strong>bookkeeper_ledger_{esc(report.target_date)}.csv</strong> &middot;
        Variance: <strong>&#8369;{report.reconciliation_variance:+.2f}</strong> &middot;
        Engine: <code>{esc(briefing.generated_by)}</code>
      </div>
    </div>
  </div>
</body>
</html>
"""


def write_email_preview(
    report: DailyReport, briefing: OpsBriefing, output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = output_dir / f"email_preview_{report.target_date}.html"
    preview_path.write_text(render_html_email(report, briefing), encoding="utf-8")
    return preview_path


def send_briefing_email(
    settings: Settings,
    report: DailyReport,
    briefing: OpsBriefing,
    attachments: list[Path],
) -> None:
    if not (
        settings.smtp_user
        and settings.smtp_app_password
        and settings.report_recipient_email
    ):
        raise ValueError(
            "Missing SMTP_USER, SMTP_APP_PASSWORD, or REPORT_RECIPIENT_EMAIL. "
            "Use --dry-run to preview outputs without sending email."
        )

    subject = (
        f"[ely.pos Ops] {report.target_date} — "
        f"PHP {report.gross_revenue:,.2f} ({report.order_count} orders)"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = settings.report_recipient_email
    msg.set_content(render_plaintext_email(report, briefing))
    msg.add_alternative(render_html_email(report, briefing), subtype="html")

    for file_path in attachments:
        data = file_path.read_bytes()
        msg.add_attachment(
            data,
            maintype="text",
            subtype="csv",
            filename=file_path.name,
        )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_app_password)
        server.send_message(msg)
