"""Supplier scorecard data and export renderers."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from ..db import connection


CSV_FIELDS = [
    "supplier_id",
    "supplier_name",
    "supplier_type",
    "risk_tier",
    "country",
    "purchase_order_count",
    "shipment_count",
    "late_shipment_count",
    "on_time_percentage",
    "open_purchase_order_count",
    "data_as_of",
]


def fetch_supplier_scorecard(risk_tier: str | None, *, limit: int) -> list[dict[str, Any]]:
    """Read supplier metrics from the guarded analytics boundary."""
    with connection(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM analytics.supplier_performance
            WHERE (%s::text IS NULL OR upper(risk_tier) = upper(%s::text))
            ORDER BY risk_tier DESC NULLS LAST,
                     on_time_percentage ASC NULLS LAST,
                     supplier_name
            LIMIT %s
            """,
            (risk_tier, risk_tier, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def render_scorecard_csv(rows: Sequence[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _scorecard_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    return {
        "title": ParagraphStyle(
            "DocTitle",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#173A5E"),
            spaceAfter=6,
        ),
        "meta": ParagraphStyle(
            "DocMeta",
            parent=base,
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#607080"),
        ),
        "limitations": ParagraphStyle(
            "DocLimitations",
            parent=base,
            fontName="Helvetica-Oblique",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#607080"),
            spaceAfter=15,
        ),
        "cell": ParagraphStyle(
            "TableCell",
            parent=base,
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#15212D"),
        ),
        "cell_bold": ParagraphStyle(
            "TableCellBold",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#15212D"),
        ),
        "header": ParagraphStyle(
            "TableHeader",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.white,
        ),
    }


def _latest_data_date(rows: Sequence[dict[str, Any]]) -> str:
    dates: list[datetime] = []
    for row in rows:
        if not row.get("data_as_of"):
            continue
        try:
            dates.append(datetime.strptime(str(row["data_as_of"]).split()[0], "%Y-%m-%d"))
        except ValueError:
            continue
    return max(dates).strftime("%Y-%m-%d") if dates else "N/A"


def _table_rows(
    rows: Sequence[dict[str, Any]],
    styles: dict[str, ParagraphStyle],
) -> list[list[Paragraph]]:
    headings = [
        "Supplier ID",
        "Supplier Name",
        "Type",
        "Risk Tier",
        "Country",
        "POs",
        "Shipments",
        "Late Shipments",
        "On-Time %",
        "Open POs",
    ]
    output = [[Paragraph(value, styles["header"]) for value in headings]]
    for row in rows:
        on_time = (
            f"{row['on_time_percentage']}%"
            if row.get("on_time_percentage") is not None
            else "Unknown"
        )
        values = [
            row.get("supplier_id") or "",
            row.get("supplier_name") or "",
            row.get("supplier_type") or "",
            row.get("risk_tier") or "Unrated",
            row.get("country") or "",
            row.get("purchase_order_count") or 0,
            row.get("shipment_count") or 0,
            row.get("late_shipment_count") or 0,
            on_time,
            row.get("open_purchase_order_count") or 0,
        ]
        output.append(
            [
                Paragraph(str(value), styles["cell_bold"] if index == 1 else styles["cell"])
                for index, value in enumerate(values)
            ]
        )
    return output


def render_scorecard_pdf(rows: Sequence[dict[str, Any]]) -> bytes:
    """Render the supplier scorecard as a self-contained PDF document."""
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = _scorecard_styles()
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    story = [
        Paragraph("SKOPE Supplier Performance Scorecard", styles["title"]),
        Paragraph(
            f"<b>Generated:</b> {generated_at} UTC &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Data Cutoff:</b> {_latest_data_date(rows)} &nbsp;&nbsp;|&nbsp;&nbsp; "
            "<b>Data Sources:</b> MB51 Inventory, Carrier MSAs, Global Supplier Invoices",
            styles["meta"],
        ),
        Paragraph(
            "<b>Limitations:</b> CONFIDENTIAL - FOR INTERNAL USE ONLY. Performance metrics "
            "are derived from operational datasets and transaction logs. Metrics are subject "
            "to ingestion latency, incomplete records, and automated extraction tolerances.",
            styles["limitations"],
        ),
    ]

    table_data = _table_rows(rows, styles)
    table = Table(
        table_data,
        colWidths=[55, 170, 85, 60, 75, 50, 70, 75, 70, 60],
        repeatRows=1,
    )
    table_style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173A5E")),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
        ]
    )
    for row_index in range(2, len(table_data), 2):
        table_style.add(
            "BACKGROUND",
            (0, row_index),
            (-1, row_index),
            colors.HexColor("#EEF3F7"),
        )
    table.setStyle(table_style)
    story.append(table)

    document.build(story)
    return buffer.getvalue()
