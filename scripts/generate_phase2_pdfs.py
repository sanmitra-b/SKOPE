"""Generate grounded SKOPE Phase 2 PDF documents from PostgreSQL."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime
from functools import lru_cache
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
import unicodedata

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import ingest_to_db as db


BASE = Path(__file__).resolve().parents[1]
DATASET = BASE / "RAG Project Dataset"
DOCS = DATASET / "Documents"
LOGOS = DOCS / "SKOPE logos"
SIGS = DOCS / "Signatures"
VERSION = "1.3.0-phase2-optimized"
AS_OF = date(2026, 7, 31)
ASSET_DPI = 180

NAVY = colors.HexColor("#173A5E")
BLUE = colors.HexColor("#0B5B8E")
OCEAN = colors.HexColor("#0B3C5D")
YELLOW = colors.HexColor("#F7C948")
ORANGE = colors.HexColor("#FF6F00")
GREEN = colors.HexColor("#1B7F5A")
RED = colors.HexColor("#B42318")
AMBER = colors.HexColor("#D97706")
LIGHT = colors.HexColor("#EEF3F7")
MID = colors.HexColor("#C9D5DF")
INK = colors.HexColor("#15212D")
MUTED = colors.HexColor("#607080")
BORDER_GRAY = colors.HexColor("#D1D5DB")
CARD_BG = colors.HexColor("#F8FAFC")

PILOT_COUNTS = {
    "invoice": 6, "purchase_order": 2, "manifest": 6, "import_report": 2,
    "audit": 4, "contract": 6, "shortage": 2, "sop": 4,
}
FULL_COUNTS = {
    "invoice": 10000, "purchase_order": 1600, "manifest": 1200, "import_report": 200,
    "audit": 100, "contract": 300, "shortage": 250, "sop": 50,
}
FOLDERS = {
    "invoice": "Invoices10000", "purchase_order": "Purchase Order1600",
    "manifest": "Manifest-BoL1200", "import_report": "ImportReport200",
    "audit": "AuditReport100", "contract": "Contract300",
    "shortage": "Shortage Report250", "sop": "SOP50",
}


def safe(value, default="Not recorded") -> str:
    if value is None:
        return default
    text = unicodedata.normalize("NFKD", str(value)).encode("latin-1", "ignore").decode("latin-1")
    text = re.sub(r"\s+", " ", text).strip()
    return text if text and text.lower() != "none" else default


def clean_currency(currency: str | None, default="INR") -> str:
    curr = safe(currency, default)
    if curr in ("Not recorded", "SOURCE_UNSPECIFIED", "None", ""):
        return default
    return curr


def money(value, currency, default_curr="INR") -> str:
    curr = clean_currency(currency, default_curr)
    try:
        val = float(value or 0)
    except (ValueError, TypeError):
        val = 0.0
    return f"{curr} {val:,.2f}"


def slug(value) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", safe(value, "NA")).strip("-")


@lru_cache(maxsize=128)
def optimized_asset(path_text: str, pixel_w: int, pixel_h: int) -> tuple[bytes, int, int]:
    """Return a cached render-size image variant without modifying the source asset."""
    with Image.open(path_text) as source:
        source.load()
        has_alpha = source.mode in {"RGBA", "LA"} or "transparency" in source.info
        image = source.convert("RGBA" if has_alpha else "RGB")
        image.thumbnail((pixel_w, pixel_h), Image.Resampling.LANCZOS)
        stream = BytesIO()
        if has_alpha:
            image.save(stream, format="PNG", optimize=True, compress_level=9)
        else:
            image.save(stream, format="JPEG", quality=88, optimize=True, progressive=False)
        return stream.getvalue(), image.width, image.height


def draw_asset(c: canvas.Canvas, path: Path, x: float, y: float, max_w: float, max_h: float) -> None:
    if not path.exists():
        return
    try:
        pixel_w = max(1, math.ceil(max_w * ASSET_DPI / 72))
        pixel_h = max(1, math.ceil(max_h * ASSET_DPI / 72))
        blob, width, height = optimized_asset(str(path.resolve()), pixel_w, pixel_h)
        image = ImageReader(BytesIO(blob))
        scale = min(max_w / width, max_h / height)
        c.drawImage(image, x, y, width=width * scale, height=height * scale, mask="auto", preserveAspectRatio=True)
    except Exception:
        image = ImageReader(str(path))
        width, height = image.getSize()
        scale = min(max_w / width, max_h / height)
        c.drawImage(image, x, y, width=width * scale, height=height * scale, mask="auto", preserveAspectRatio=True)


def logo_panel(c: canvas.Canvas, path: Path, x: float, y: float, width: float, height: float,
               *, border=colors.HexColor("#D8DEE5")) -> None:
    c.setFillColor(colors.white)
    c.setStrokeColor(border)
    c.roundRect(x, y, width, height, 4, fill=1, stroke=1)
    draw_asset(c, path, x + 6, y + 5, width - 12, height - 10)
    c.setFillColor(INK)
    c.setStrokeColor(colors.black)


def barcode(c: canvas.Canvas, x: float, y: float, width: float, height: float, seed: str) -> None:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    cursor = x
    c.setFillColor(colors.black)
    for value in digest:
        bar = 0.6 + (value % 4) * 0.45
        if cursor + bar > x + width:
            break
        c.rect(cursor, y, bar, height, fill=1, stroke=0)
        cursor += bar + 0.9 + ((value >> 3) % 3) * 0.35
    c.setFillColor(INK)


def qr_code(c: canvas.Canvas, x: float, y: float, size: float, seed: str) -> None:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    grid_n = 17
    mod = size / grid_n
    c.setFillColor(colors.white)
    c.rect(x, y, size, size, fill=1, stroke=0)
    c.setFillColor(colors.black)
    
    for fx, fy in [(0, 0), (grid_n - 5, 0), (0, grid_n - 5)]:
        c.rect(x + fx * mod, y + (grid_n - 5 - fy) * mod, 5 * mod, 5 * mod, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.rect(x + (fx + 1) * mod, y + (grid_n - 4 - fy) * mod, 3 * mod, 3 * mod, fill=1, stroke=0)
        c.setFillColor(colors.black)
        c.rect(x + (fx + 2) * mod, y + (grid_n - 3 - fy) * mod, 1 * mod, 1 * mod, fill=1, stroke=0)

    d_idx = 0
    for row in range(grid_n):
        for col in range(grid_n):
            if (row < 6 and col < 6) or (row < 6 and col >= grid_n - 6) or (row >= grid_n - 6 and col < 6):
                continue
            bit = (digest[d_idx % len(digest)] >> (col % 8)) & 1
            d_idx += 1
            if bit:
                c.rect(x + col * mod, y + (grid_n - 1 - row) * mod, mod, mod, fill=1, stroke=0)
    c.setFillColor(INK)


def badge(c: canvas.Canvas, x: float, y: float, width: float, height: float, text: str,
          bg_color, text_color=colors.white, font_size=6.5) -> None:
    c.setFillColor(bg_color)
    c.roundRect(x, y, width, height, height / 2, fill=1, stroke=0)
    c.setFillColor(text_color)
    c.setFont("Helvetica-Bold", font_size)
    c.drawCentredString(x + width / 2, y + (height - font_size) / 2 + 0.5, text)
    c.setFillColor(INK)


def form_box(c: canvas.Canvas, x: float, y: float, width: float, height: float, *, line=0.6,
             fill=None) -> None:
    if fill is not None:
        c.setFillColor(fill)
        c.rect(x, y, width, height, fill=1, stroke=0)
    c.setStrokeColor(colors.black)
    c.setLineWidth(line)
    c.rect(x, y, width, height, fill=0, stroke=1)
    c.setFillColor(INK)


def wrapped(c: canvas.Canvas, text, x: float, y: float, width: float, *, font="Helvetica", size=8,
            leading=None, color=INK, max_lines=12) -> float:
    c.setFont(font, size)
    c.setFillColor(color)
    leading = leading or size * 1.25
    words = safe(text).split()
    lines = []
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if stringWidth(candidate, font, size) <= width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    for item in lines[:max_lines]:
        c.drawString(x, y, item)
        y -= leading
    c.setFillColor(INK)
    return y


def disclosure(c: canvas.Canvas, page_w: float, page_h: float, source_ids: str, page_no: int, pages: int) -> None:
    c.setStrokeColor(MID)
    c.line(24, 28, page_w - 24, 28)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.3)
    c.drawString(24, 18, "SKOPE | CONFIDENTIAL")
    c.drawRightString(page_w - 24, 18, f"Page {page_no} of {pages}")
    c.setFillColor(INK)


def heading(c: canvas.Canvas, page_w: float, page_h: float, title: str, subtitle: str, logo: str,
            color=NAVY) -> None:
    c.setFillColor(color)
    c.rect(0, page_h - 92, page_w, 92, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.roundRect(24, page_h - 80, 132, 62, 5, fill=1, stroke=0)
    draw_asset(c, LOGOS / logo, 30, page_h - 74, 120, 50)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawRightString(page_w - 28, page_h - 47, safe(title))
    c.setFont("Helvetica", 8)
    c.drawRightString(page_w - 28, page_h - 66, safe(subtitle))
    c.setFillColor(INK)


def label_value(c: canvas.Canvas, x: float, y: float, label: str, value, width=160) -> None:
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 6.5)
    c.drawString(x, y, safe(label).upper())
    c.setFillColor(INK)
    c.setFont("Helvetica", 8.2)
    wrapped(c, value, x, y - 11, width, size=8.2, max_lines=3)


def table(c: canvas.Canvas, x: float, y_top: float, widths: list[float], headers: list[str], rows: list[list],
          *, row_h=28, header_h=24, header_color=NAVY, header_text_color=colors.white, font_size=6.8) -> float:
    total_w = sum(widths)
    c.setFillColor(header_color)
    c.rect(x, y_top - header_h, total_w, header_h, fill=1, stroke=0)
    c.setStrokeColor(MID)
    c.rect(x, y_top - header_h, total_w, header_h, fill=0, stroke=1)
    
    cursor = x
    c.setFillColor(header_text_color)
    c.setFont("Helvetica-Bold", font_size)
    for width, header in zip(widths, headers, strict=True):
        wrapped(c, header, cursor + 4, y_top - 9, width - 8, font="Helvetica-Bold", size=font_size,
                leading=font_size + 1.2, color=header_text_color, max_lines=2)
        cursor += width
        
    y = y_top - header_h
    for row_index, row in enumerate(rows):
        c.setFillColor(colors.white if row_index % 2 == 0 else LIGHT)
        c.rect(x, y - row_h, total_w, row_h, fill=1, stroke=0)
        cursor = x
        for width, value in zip(widths, row, strict=True):
            c.setStrokeColor(MID)
            c.rect(cursor, y - row_h, width, row_h, fill=0, stroke=1)
            wrapped(c, value, cursor + 4, y - 10, width - 8, size=font_size, leading=font_size + 1.2,
                    max_lines=max(1, int(row_h / (font_size + 1.2)) - 1))
            cursor += width
        y -= row_h
    c.setFillColor(INK)
    return y


def signature(c: canvas.Canvas, image_name: str, x: float, y: float, width: float, title: str, signed_date) -> None:
    draw_asset(c, SIGS / image_name, x + 8, y + 23, width - 16, 45)
    c.setStrokeColor(MUTED)
    c.line(x, y + 20, x + width, y + 20)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(x, y + 9, safe(title))
    c.setFont("Helvetica", 6.5)
    c.drawRightString(x + width, y + 9, safe(signed_date))


def pdf_canvas(path: Path, pagesize=A4) -> canvas.Canvas:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=pagesize, pageCompression=1)
    c.setTitle(path.stem)
    c.setAuthor("SKOPE synthetic document generator")
    c.setSubject("Synthetic Phase 2 document for retrieval and evaluation; not legally operative")
    c.setKeywords("SKOPE,synthetic,phase2,RAG")
    return c


def invoice_records(conn, count: int) -> list[dict]:
    if count == 6:
        allocations = {"Blinkit": 2, "IndianEComm": 2, "DataCo": 2}
    elif count == 10000:
        # Blinkit contains 4,225 distinct orders. Redistribute its 775-record
        # shortfall across the two larger sources while retaining a near 50/30/20 mix.
        allocations = {"Blinkit": 4225, "IndianEComm": 3465, "DataCo": 2310}
    else:
        allocations = {
            "Blinkit": count * 5 // 10, "IndianEComm": count * 3 // 10,
            "DataCo": count - (count * 5 // 10) - (count * 3 // 10),
        }
    records = []
    for source, limit in allocations.items():
        rows = conn.execute("""
            SELECT DISTINCT ON (f.order_id)
                   f.source_system,f.source_line_id,f.order_id,f.display_order_id,f.order_date,
                   f.quantity,f.unit_price_original,f.gross_amount_original,f.discount_original,
                   f.net_amount_original,f.currency_code,f.order_status,f.payment_method,
                   c.display_name,c.city,c.state_province,c.country,
                   p.source_product_id,p.product_name,p.category
            FROM skope.fact_order_fulfillment f
            JOIN skope.dim_customer c ON c.customer_key=f.customer_key
            JOIN skope.dim_product p ON p.product_key=f.product_key
            WHERE f.source_system=%s
            ORDER BY f.order_id,f.source_line_id LIMIT %s
        """, (source, limit)).fetchall()
        columns = ["source_system","source_line_id","order_id","display_order_id","order_date","quantity",
                   "unit_price","gross","discount","net","currency","status","payment","customer","city",
                   "state","country","product_id","product_name","category"]
        records.extend(dict(zip(columns, row, strict=True)) for row in rows)
    return records


def render_invoice(record: dict, path: Path) -> None:
    c = pdf_canvas(path)
    w, h = A4
    invoice_id = f"INV-{record['order_id']}"
    city = safe(record.get('city'), "Mumbai")
    state = safe(record.get('state'), "Maharashtra")
    country = safe(record.get('country'), "India")
    location = f"{city}, {state}, {country}"
    
    if record["source_system"] == "Blinkit":
        x, top, box_w = 28, 804, 539
        c.setFillColor(YELLOW)
        c.rect(x, top - 68, box_w, 68, fill=1, stroke=0)
        c.setFillColor(colors.black)
        c.rect(x, top - 68, box_w, 68, fill=0, stroke=1)
        
        logo_panel(c, LOGOS / "SKOPE Express.png", x + 8, top - 58, 135, 48)
        
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 19)
        c.drawRightString(x + box_w - 14, top - 32, "Tax Invoice")
        c.setFont("Helvetica-Bold", 7.5)
        c.drawRightString(x + box_w - 14, top - 50, f"Invoice No: {invoice_id}")
        
        form_box(c, x, 310, box_w, 426)
        
        c.line(x, top - 150, x + box_w, top - 150)
        c.line(x + 350, top - 68, x + 350, top - 150)
        
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 8, top - 82, "Sold By / Seller")
        c.setFont("Helvetica-Bold", 7)
        wrapped(c, "SKOPE Express Commerce Private Limited", x + 8, top - 96, 330, font="Helvetica-Bold", size=7)
        wrapped(c, f"Facility / Dark Store: {location}\nGSTIN: 27AAACS1234F1Z5  |  PAN: AAACS1234F\nFSSAI Lic. No.: 10019022009876",
                x + 8, top - 108, 330, size=6.5, leading=9, max_lines=4)
        
        qr_code(c, x + 365, top - 142, 65, invoice_id)
        c.setFont("Helvetica-Bold", 6.5)
        c.drawString(x + 438, top - 86, "Order ID:")
        c.setFont("Helvetica", 6.5)
        c.drawString(x + 438, top - 98, record["order_id"])
        c.setFont("Helvetica-Bold", 6.5)
        c.drawString(x + 438, top - 114, "Invoice Date:")
        c.setFont("Helvetica", 6.5)
        c.drawString(x + 438, top - 126, safe(record["order_date"]))
        
        c.line(x, top - 225, x + box_w, top - 225)
        c.line(x + 350, top - 150, x + 350, top - 225)
        
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 8, top - 164, "Invoice To / Customer Details")
        c.setFont("Helvetica", 7)
        c.drawString(x + 8, top - 178, f"Name: {record['customer']}")
        wrapped(c, f"Delivery Address: {location}", x + 8, top - 190, 330, size=7, leading=9, max_lines=3)
        
        c.setFont("Helvetica-Bold", 6.5)
        c.drawString(x + 365, top - 166, "Place of Supply:")
        c.setFont("Helvetica", 6.8)
        c.drawString(x + 365, top - 178, state)
        c.setFont("Helvetica-Bold", 6.5)
        c.drawString(x + 365, top - 195, "Reverse Charge:")
        c.setFont("Helvetica", 6.8)
        c.drawString(x + 365, top - 207, "No")
        
        qty = float(record["quantity"] or 1)
        unit_p = float(record["unit_price"] or 0)
        net_val = float(record["net"] or (qty * unit_p))
        taxable = round(net_val / 1.18, 2)
        cgst = round((net_val - taxable) / 2, 2)
        sgst = cgst
        
        rows = [["1", record["product_id"], record["product_name"], f"{unit_p:,.2f}",
                 f"{qty:.0f}", f"{taxable:,.2f}", f"{cgst:,.2f}", f"{sgst:,.2f}", f"{net_val:,.2f}"]]
        
        table(c, x, top - 225, [24, 52, 160, 48, 30, 55, 50, 50, 70],
              ["Sr.", "SKU / HSN", "Item Description", "Unit MRP", "Qty", "Taxable", "CGST 9%", "SGST 9%", "Total INR"],
              rows, row_h=44, header_h=22, header_color=LIGHT, header_text_color=INK, font_size=6.2)
        
        c.setFillColor(CARD_BG)
        c.rect(x, top - 315, box_w, 24, fill=1, stroke=0)
        c.line(x, top - 315, x + box_w, top - 315)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(x + 8, top - 301, "Amount in Words:")
        c.setFont("Helvetica", 7)
        c.drawString(x + 88, top - 301, f"{clean_currency(record['currency'])} {net_val:,.2f} only")
        c.setFont("Helvetica-Bold", 8)
        c.drawRightString(x + box_w - 12, top - 301, f"Grand Total: {clean_currency(record['currency'])} {net_val:,.2f}")
        
        c.line(x, top - 395, x + box_w, top - 395)
        c.line(x + 350, top - 315, x + 350, top - 395)
        
        c.setFont("Helvetica-Bold", 7)
        c.drawString(x + 8, top - 330, "SKOPE Express Commerce Private Limited")
        c.setFont("Helvetica", 6.5)
        wrapped(c, "Certified that the particulars given above are true and correct and the amount indicated represents the price actually charged.",
                x + 8, top - 345, 330, size=6.3, leading=8.5, max_lines=4)
        
        signature(c, "signature-40145.png", x + 365, top - 388, 160, "Authorised Signatory", record["order_date"])
        
        wrapped(c, "Terms & Conditions: Quote invoice number for all support queries. Goods received are subject to quick-commerce return policy within 24 hours.",
                x + 8, top - 410, box_w - 16, size=6.2, leading=8, max_lines=2)
                
    elif record["source_system"] == "IndianEComm":
        x, y, box_w, box_h = 28, 330, 539, 460
        form_box(c, x, y, box_w, box_h, line=0.8)
        
        logo_panel(c, LOGOS / "SKOPE Domestic.png", x + 8, y + box_h - 58, 140, 50)
        c.setFont("Helvetica-Bold", 18)
        c.drawRightString(x + box_w - 12, y + box_h - 32, "TAX INVOICE")
        c.setFont("Helvetica", 7.5)
        c.drawRightString(x + box_w - 12, y + box_h - 48, f"Invoice No: {invoice_id}  |  Date: {record['order_date']}")
        
        barcode(c, x + box_w - 120, y + box_h - 85, 108, 22, invoice_id)
        
        c.line(x, y + box_h - 95, x + box_w, y + box_h - 95)
        c.line(x + 270, y + box_h - 95, x + 270, y + box_h - 175)
        
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x + 8, y + box_h - 110, "Sold By:")
        c.drawString(x + 278, y + box_h - 110, "Billing & Delivery Address:")
        
        c.setFont("Helvetica", 7)
        wrapped(c, f"SKOPE Domestic Retail Pvt Ltd\nFulfillment Hub: {location}\nGSTIN: 33AAACS5678G1Z2",
                x + 8, y + box_h - 124, 250, size=7, leading=9.5, max_lines=4)
        wrapped(c, f"Customer: {record['customer']}\nAddress: {location}\nPlace of Supply: {state}",
                x + 278, y + box_h - 124, 250, size=7, leading=9.5, max_lines=4)
        
        qty = float(record["quantity"] or 1)
        unit_p = float(record["unit_price"] or 0)
        net_val = float(record["net"] or (qty * unit_p))
        taxable = round(net_val / 1.18, 2)
        cgst = round((net_val - taxable) / 2, 2)
        
        table(c, x, y + box_h - 175, [160, 50, 35, 40, 50, 54, 50, 50, 50],
              ["Description of Goods", "HSN / SKU", "Qty", "UOM", "Rate", "Gross", "Taxable", "CGST", "Total INR"],
              [[record["product_name"], record["product_id"], f"{qty:.0f}", "Nos", f"{unit_p:,.2f}",
                f"{net_val:,.2f}", f"{taxable:,.2f}", f"{cgst:,.2f}", f"{net_val:,.2f}"]],
              row_h=55, header_h=24, header_color=NAVY, font_size=6.2)
              
        c.line(x, y + 85, x + box_w, y + 85)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x + 8, y + 96, "Grand Total")
        c.drawRightString(x + box_w - 12, y + 96, money(record["net"], record["currency"], "INR"))
        
        c.setFont("Helvetica", 6.8)
        wrapped(c, f"Amount Chargeable: {money(record['net'], record['currency'], 'INR')}\nPayment Mode: {record['payment']}\nGoods sold are subject to standard warranty.",
                x + 8, y + 68, 300, size=6.8, leading=9, max_lines=3)
        signature(c, "signature-40145.png", x + 370, y + 15, 150, "Authorised Signatory", record["order_date"])
    else:
        logo_panel(c, LOGOS / "SKOPE Global.png", 28, h - 84, 130, 52)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 20)
        c.drawRightString(w - 28, h - 45, "Commercial Invoice")
        c.setFont("Helvetica-Bold", 7.5)
        c.drawRightString(w - 28, h - 62, "International Trade & Logistics Desk")
        
        c.setFont("Helvetica", 7)
        wrapped(c, "SKOPE Global Enterprise Ltd\nGlobal Sourcing & Supply Chain\nTrade Identification Master: GLO-SKP-9988", 170, h - 42, 200, size=7, max_lines=3)
        
        form_box(c, 28, h - 235, 175, 130)
        c.setFillColor(CARD_BG); c.rect(28, h - 125, 175, 20, fill=1, stroke=0); c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 7.5); c.drawString(34, h - 118, "BILL TO (CUSTOMER)")
        wrapped(c, f"{record['customer']}\n{location}", 34, h - 138, 162, size=7.2, leading=9.5, max_lines=5)
        
        form_box(c, 210, h - 235, 175, 130)
        c.setFillColor(CARD_BG); c.rect(210, h - 125, 175, 20, fill=1, stroke=0); c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 7.5); c.drawString(216, h - 118, "SHIP TO (CONSIGNEE)")
        wrapped(c, f"SKOPE Distribution Center\n{location}", 216, h - 138, 162, size=7.2, leading=9.5, max_lines=5)
        
        form_box(c, 395, h - 235, 172, 130)
        meta = [
            ("COMM. INVOICE NO.", invoice_id),
            ("DATE", safe(record["order_date"])),
            ("SHIPPING VIA", "Ocean / Air Freight"),
            ("CURRENCY", clean_currency(record["currency"], "USD")),
            ("PAYMENT TERMS", "Net 30 Days"),
            ("INCOTERMS", "CIF Rotterdam / FOB Origin")
        ]
        my = h - 118
        for label, val in meta:
            c.setFont("Helvetica-Bold", 6); c.drawString(400, my, label)
            c.setFont("Helvetica", 6.5); c.drawRightString(560, my, safe(val))
            my -= 19
            if my > h - 230:
                c.setStrokeColor(MID); c.line(395, my + 6, 567, my + 6)
        
        curr = clean_currency(record["currency"], "USD")
        table(c, 28, h - 260, [35, 85, 175, 45, 55, 75, 70],
              ["ITEM", "MATERIAL SKU", "DESCRIPTION", "QTY", "ORDER ID", "UNIT PRICE", "TOTAL VALUE"],
              [["001", record["product_id"], record["product_name"], str(record["quantity"]), record["order_id"],
                money(record["unit_price"], curr, "USD"), money(record["net"], curr, "USD")]],
              row_h=55, header_h=24, header_color=NAVY, font_size=6.3)
              
        c.setFont("Helvetica-Bold", 9.5)
        c.drawRightString(w - 28, h - 365, f"TOTAL INVOICE VALUE:  {money(record['net'], curr, 'USD')}")
        
        form_box(c, 28, h - 455, 330, 75)
        c.setFont("Helvetica-Bold", 7); c.drawString(34, h - 395, "WIRE TRANSFER / BANKING DETAILS")
        c.setFont("Helvetica", 6.5)
        wrapped(c, "Beneficiary: SKOPE Global Enterprise Ltd\nBank: International Trade & Commerce Bank\nSWIFT: SKOPUS33XXX  |  IBAN: GB29SKOP60161331926819\nPayment Reference: " + invoice_id,
                34, h - 408, 318, size=6.5, leading=9, max_lines=4)
                
        signature(c, "signature-40157.png", 395, h - 460, 165, "Finance Manager", record["order_date"])
        
    disclosure(c, w, h, f"order={record['order_id']}; source_line={record['source_line_id']}", 1, 1)
    c.save()


def po_records(conn, count: int) -> list[dict]:
    headers = conn.execute("""
        SELECT po.po_id,po.order_date,po.required_date,po.currency_code,po.payment_terms,
               s.supplier_id,s.supplier_name,s.supplier_type,s.country,l.facility_name,l.source_location_id
        FROM skope.purchase_order po JOIN skope.dim_supplier s USING(supplier_id)
        LEFT JOIN skope.dim_location l ON l.location_key=po.ship_to_location_key
        ORDER BY po.po_id LIMIT %s
    """, (count,)).fetchall()
    records = []
    for row in headers:
        lines = conn.execute("""
            SELECT pol.supplier_sku,p.product_name,pol.ordered_quantity,pol.unit_price,pol.currency_code
            FROM skope.purchase_order_line pol JOIN skope.dim_product p USING(product_key)
            WHERE pol.po_id=%s ORDER BY pol.po_line_id
        """, (row[0],)).fetchall()
        records.append({"po_id": row[0], "order_date": row[1], "required_date": row[2], "currency": row[3],
                         "payment_terms": row[4], "supplier_id": row[5], "supplier_name": row[6],
                         "supplier_type": row[7], "country": row[8], "facility": row[9], "location_id": row[10],
                         "lines": lines})
    return records


def supplier_logo(supplier_id: str) -> str | None:
    return {"SUP-FARMFRESH": "farmfreshlogo.jpg", "SUP-TECHHUB": "Shenzen TEchHUb.png",
            "SUP-APEX": "Apex.png"}.get(supplier_id)


def render_po(record: dict, path: Path) -> None:
    c = pdf_canvas(path, landscape(A4))
    w, h = landscape(A4)
    x, top, fw = 28, h - 24, w - 56
    
    form_box(c, x, 55, fw, top - 55, line=0.8)
    
    logo_panel(c, LOGOS / "SKOPE Global.png", x + 10, top - 66, 145, 52)
    
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(w / 2, top - 34, "PURCHASE ORDER")
    
    c.setFont("Helvetica-Bold", 8.5)
    c.drawRightString(x + fw - 12, top - 24, f"PO NO. {record['po_id']}")
    c.setFont("Helvetica", 7)
    c.drawRightString(x + fw - 12, top - 40, f"DATE: {safe(record['order_date'])}")
    
    c.line(x, top - 76, x + fw, top - 76)
    
    s_logo = supplier_logo(record["supplier_id"])
    if s_logo:
        draw_asset(c, LOGOS / s_logo, x + 180, top - 150, 70, 50)
    
    c.line(x + 260, top - 76, x + 260, top - 170)
    c.line(x + 520, top - 76, x + 520, top - 170)
    
    col_data = [
        (x + 8, "VENDOR / SUPPLIER", [record['supplier_name'], f"ID: {record['supplier_id']}", f"Origin: {record['country']} | {record['supplier_type']}"]),
        (x + 268, "BILL TO (BUYER)", ["SKOPE Global Enterprise Ltd", "Accounts Payable & Trade Finance", f"Invoice Currency: {clean_currency(record['currency'])}"]),
        (x + 528, "SHIP TO (DELIVERY)", [safe(record['facility'], "SKOPE Central Warehouse"), f"Facility ID: {record['location_id']}", f"Required Delivery: {safe(record['required_date'])}"]),
    ]
    
    c.setFillColor(INK)
    for bx, title, lines in col_data:
        c.setFont("Helvetica-Bold", 7)
        c.drawString(bx, top - 90, title)
        yy = top - 106
        for line in lines:
            c.setFont("Helvetica", 7.2)
            c.drawString(bx, yy, safe(line))
            yy -= 13
            
    c.line(x, top - 170, x + fw, top - 170)
    
    meta = [
        ("PAYMENT TERMS", safe(record['payment_terms'], "Net 30 Days")),
        ("DELIVERY TARGET", safe(record['required_date'])),
        ("BUYER DESK", "SKOPE Global Procurement"),
        ("SHIPPING TERMS", "DDP - Destination Freight Prepaid")
    ]
    cell_w = fw / 4
    for i, (label, val) in enumerate(meta):
        bx = x + i * cell_w
        if i > 0:
            c.line(bx, top - 170, bx, top - 208)
        c.setFont("Helvetica-Bold", 6.2)
        c.drawString(bx + 6, top - 182, label)
        c.setFont("Helvetica", 7)
        c.drawString(bx + 6, top - 198, safe(val))
        
    c.line(x, top - 208, x + fw, top - 208)
    
    curr = clean_currency(record["currency"])
    rows = []
    total = 0.0
    for line_no, (sku, name, qty, price, c_code) in enumerate(record["lines"], start=1):
        line_tot = float(qty or 0) * float(price or 0)
        total += line_tot
        rows.append([str(line_no), sku, name, f"{float(qty):.0f}", "EA",
                     money(price, curr), "0.00", money(line_tot, curr)])
                     
    table_bottom = table(c, x, top - 208, [30, 95, 275, 45, 40, 85, 65, 95],
                         ["SL", "ITEM SKU", "ITEM DESCRIPTION", "QTY", "UOM", "UNIT PRICE", "TAX", "LINE TOTAL"],
                         rows, row_h=32, header_h=22, header_color=NAVY, font_size=6.5)
                         
    c.setFillColor(CARD_BG)
    c.rect(x, 150, fw, 26, fill=1, stroke=0)
    c.line(x, 150, x + fw, 150)
    c.line(x, 176, x + fw, 176)
    
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(x + fw - 12, 158, f"TOTAL PURCHASE ORDER VALUE:  {money(total, curr)}")
    
    c.setFont("Helvetica", 6.5)
    wrapped(c, "Standard Terms: Please quote PO number on all invoices and packaging slips. Material is subject to strict quality and count verification at delivery facility. Discrepancies require written RMA acknowledgment within 48 hours.",
            x + 8, 138, 480, size=6.3, leading=8.5, max_lines=3)
            
    signature(c, "signature-40120.png", x + 30, 65, 170, "Procurement Officer", record["order_date"])
    signature(c, "signature-40140.png", x + 300, 65, 170, "Supply Chain Approver", record["order_date"])
    
    c.setStrokeColor(MUTED)
    c.line(x + 560, 88, x + 720, 88)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(x + 560, 75, "Supplier Authorized Representative")
    
    disclosure(c, w, h, f"po={record['po_id']}; supplier={record['supplier_id']}", 1, 1)
    c.save()


def manifest_records(conn, count: int) -> list[dict]:
    query_limit = 1200 if count == 6 else count
    rows = conn.execute("""
        SELECT sh.shipment_id,sh.po_id,sh.planned_departure,sh.actual_departure,sh.planned_arrival,
               sh.actual_arrival,sh.status,sc.container_number,sc.seal_number,sc.package_count,
               sc.cargo_description,sc.gross_weight_kg,sc.volume_cbm,p.source_partner_id,p.partner_name,
               lo.facility_name,ld.facility_name,s.supplier_id,s.supplier_name
        FROM skope.shipment sh JOIN skope.shipment_container sc USING(shipment_id)
        JOIN skope.purchase_order po USING(po_id) JOIN skope.dim_supplier s USING(supplier_id)
        LEFT JOIN skope.dim_partner p ON p.partner_key=sh.carrier_partner_key
        LEFT JOIN skope.dim_location lo ON lo.location_key=sh.origin_location_key
        LEFT JOIN skope.dim_location ld ON ld.location_key=sh.destination_location_key
        ORDER BY sh.shipment_id LIMIT %s
    """, (query_limit,)).fetchall()
    columns = ["shipment_id","po_id","planned_departure","actual_departure","planned_arrival","actual_arrival",
               "status","container","seal","packages","cargo","weight","cbm","carrier_id","carrier_name",
               "origin","destination","supplier_id","supplier_name"]
    records = [dict(zip(columns, row, strict=True)) for row in rows]
    if count == 6:
        maersk = [row for row in records if row["carrier_id"] == "V44_3"]
        dhl = [row for row in records if row["carrier_id"] == "DHL-AIR-DEMO"]
        other = [row for row in records if row["carrier_id"] not in {"V44_3", "DHL-AIR-DEMO"}]
        if len(maersk) < 2 or len(dhl) < 2 or len(other) < 2:
            raise RuntimeError("Pilot requires two Maersk, two DHL, and two other grounded shipment records")
        return [maersk[0], other[0], dhl[0], maersk[1], other[1], dhl[1]]
    if count == 1200:
        # Preserve the 500 HBL / 500 manifest / 200 HAWB allocation while keeping
        # the two known branded carriers in compatible document families.
        dhl = [row for row in records if row["carrier_id"] == "DHL-AIR-DEMO"]
        maersk = [row for row in records if row["carrier_id"] == "V44_3"]
        other = [row for row in records if row["carrier_id"] not in {"V44_3", "DHL-AIR-DEMO"}]
        hawb = dhl + other[:max(0, 200 - len(dhl))]
        hawb_ids = {row["shipment_id"] for row in hawb}
        remaining = [row for row in records if row["shipment_id"] not in hawb_ids]
        hbl_preferred = [row for row in maersk if row["shipment_id"] not in hawb_ids]
        hbl_other = [row for row in remaining if row["carrier_id"] != "V44_3"]
        hbl = (hbl_preferred + hbl_other)[:500]
        hbl_ids = {row["shipment_id"] for row in hbl}
        manifest = [row for row in remaining if row["shipment_id"] not in hbl_ids]
        if len(hbl) != 500 or len(manifest) != 500 or len(hawb) != 200:
            raise RuntimeError(
                f"Full manifest allocation failed: HBL={len(hbl)}, manifest={len(manifest)}, HAWB={len(hawb)}"
            )
        return hbl + manifest + hawb
    return records


def carrier_logo(record: dict) -> str | None:
    if record.get("carrier_id") == "V44_3" or "MAERSK" in safe(record.get("carrier_name")).upper():
        return "maersk-line.png"
    if record.get("carrier_id") == "DHL-AIR-DEMO" or "DHL" in safe(record.get("carrier_name")).upper():
        return "DHL Global.png"
    return None


def render_manifest(record: dict, path: Path, subtype: str) -> None:
    pagesize = landscape(A4) if subtype == "MANIFEST" else A4
    c = pdf_canvas(path, pagesize)
    w, h = pagesize
    partner_logo = carrier_logo(record)
    issued = safe(record["actual_departure"] or record["planned_departure"])[:10]
    
    if subtype == "MANIFEST":
        logo_panel(c, LOGOS / "SKOPE Ocean.png", 28, h - 80, 145, 52)
        if partner_logo:
            logo_panel(c, LOGOS / partner_logo, w - 175, h - 80, 145, 52)
            
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 19)
        c.drawCentredString(w / 2, h - 38, "OCEAN CARGO MANIFEST")
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(w / 2, h - 54, f"Manifest Ref: {record['shipment_id']}  |  Issued: {issued}")
        
        form_box(c, 28, 65, w - 56, h - 155)
        
        info = [
            ("VESSEL / OCEAN CARRIER", f"{safe(record['carrier_name'])} ({safe(record['carrier_id'])})"),
            ("PORT OF LOADING (POL)", safe(record['origin'], "Shanghai Ocean Terminal")),
            ("PORT OF DISCHARGE (POD)", safe(record['destination'], "Rotterdam Main Port")),
            ("ESTIMATED ARRIVAL (ETA)", safe(record['planned_arrival']))
        ]
        for i, (lab, val) in enumerate(info):
            bx = 36 + i * 190
            c.setFont("Helvetica-Bold", 6.2); c.drawString(bx, h - 105, lab)
            c.setFont("Helvetica", 7.2); wrapped(c, val, bx, h - 118, 175, size=7.2, max_lines=2)
            
        c.line(28, h - 142, w - 28, h - 142)
        
        table(c, 28, h - 142, [85, 85, 75, 115, 115, 195, 55, 60],
              ["B/L NUMBER", "CONTAINER NO.", "SEAL NO.", "SHIPPER", "CONSIGNEE", "DESCRIPTION OF GOODS", "PACKAGES", "GROSS KG"],
              [[record['shipment_id'], record['container'], record['seal'], record['supplier_name'], "SKOPE Global Enterprise",
                record['cargo'], str(record['packages']), f"{float(record['weight'] or 0):,.1f}"]],
              row_h=75, header_h=26, header_color=OCEAN, font_size=6.2)
              
        c.setFont("Helvetica-Bold", 7); c.drawString(36, 120, "CARGO & STOWAGE DECLARATION")
        c.setFont("Helvetica", 6.5)
        wrapped(c, "The master and manifesting agent certify that the particulars furnished above are true and complete according to onboard container declarations. Goods are stowed in accordance with international maritime regulations.",
                36, 106, 520, size=6.5, leading=8.5, max_lines=3)
                
        signature(c, "signature-40126.png", w - 210, 68, 160, "Vessel Master / Agent", issued)
        
    else:
        x, top, fw = 28, h - 22, w - 56
        form_box(c, x, 55, fw, top - 55, line=0.8)
        
        title_text = "HOUSE BILL OF LADING" if subtype == "HBL" else "HOUSE AIR WAYBILL"
        logo_file = "SKOPE Ocean.png" if subtype == "HBL" else "SKOPE Global.png"
        partner_file = partner_logo
        
        logo_panel(c, LOGOS / logo_file, x + 8, top - 64, 125, 48)
        if partner_file:
            logo_panel(c, LOGOS / partner_file, x + fw - 132, top - 64, 124, 48)
        
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(w / 2, top - 26, title_text)
        c.setFont("Helvetica-Bold", 6)
        c.drawCentredString(w / 2, top - 38, "NON-NEGOTIABLE UNLESS CONSIGNED TO ORDER")
        c.setFont("Helvetica", 7)
        c.drawCentredString(w / 2, top - 52, f"REF NO: {record['shipment_id']}")
        
        c.line(x, top - 76, x + fw, top - 76)
        
        split = x + fw / 2
        c.line(split, top - 76, split, top - 250)
        c.line(x, top - 163, x + fw, top - 163)
        c.line(x, top - 250, x + fw, top - 250)
        
        party_boxes = [
            (x + 8, top - 88, "SHIPPER / EXPORTER", f"{record['supplier_name']}\nSupplier ID: {record['supplier_id']}"),
            (split + 8, top - 88, "CARRIER / AGENT", f"{safe(record['carrier_name'])}\nCarrier ID: {safe(record['carrier_id'])}"),
            (x + 8, top - 175, "CONSIGNEE", "SKOPE Global Enterprise Ltd\nLogistics Control Tower & Receiving"),
            (split + 8, top - 175, "NOTIFY PARTY", f"SKOPE Operations Control\nCross-Ref Purchase Order: {record['po_id']}")
        ]
        for bx, by, lab, val in party_boxes:
            c.setFont("Helvetica-Bold", 6.2); c.drawString(bx, by, lab)
            wrapped(c, val, bx, by - 14, fw / 2 - 16, size=6.8, leading=9.5, max_lines=4)
            
        routing = [
            ("PORT OF RECEIPT / LOADING", safe(record['origin'], "Shanghai Port")),
            ("PORT OF DELIVERY / DISCHARGE", safe(record['destination'], "Rotterdam Port")),
            ("DEPARTURE DATE", safe(record['actual_departure'] or record['planned_departure'])),
            ("EXPECTED ARRIVAL (ETA)", safe(record['planned_arrival']))
        ]
        rw = fw / 4
        for i, (lab, val) in enumerate(routing):
            bx = x + i * rw
            if i > 0:
                c.line(bx, top - 250, bx, top - 295)
            c.setFont("Helvetica-Bold", 5.5); c.drawString(bx + 5, top - 262, lab)
            c.setFont("Helvetica", 6.5); wrapped(c, val, bx + 5, top - 275, rw - 10, size=6.5, max_lines=2)
            
        c.line(x, top - 295, x + fw, top - 295)
        
        table(c, x, top - 295, [85, 75, 45, 185, 75, 74],
              ["CONTAINER NO.", "SEAL NO.", "PKGS", "DESCRIPTION OF GOODS", "GROSS WEIGHT", "MEASUREMENT"],
              [[record['container'], record['seal'], str(record['packages']), record['cargo'],
                f"{float(record['weight'] or 0):,.1f} KG", f"{float(record['cbm'] or 0):,.2f} CBM"]],
              row_h=85, header_h=24, header_color=NAVY, font_size=6.2)
              
        c.line(x, 170, x + fw, 170)
        c.line(split, 55, split, 170)
        
        c.setFont("Helvetica-Bold", 6.5); c.drawString(x + 8, 156, "FREIGHT & CHARGES")
        c.setFont("Helvetica", 6.3)
        wrapped(c, "Freight Payable: PREPAID\nDeclared Value: As per Commercial Invoice\nNumber of Original Bills: THREE (3)\nSubject to Hague-Visby rules and standard bill of lading conditions.",
                x + 8, 142, fw / 2 - 16, size=6.3, leading=8.5, max_lines=5)
                
        c.setFont("Helvetica-Bold", 6.5); c.drawString(split + 8, 156, "CARRIER RECEIPT & EXECUTION")
        sig_file = "signature-40126.png" if subtype == "HBL" else "signature-40166.png"
        signature(c, sig_file, split + 15, 68, 180, "Carrier Authorized Master / Agent", issued)
        
    disclosure(c, w, h, f"shipment={record['shipment_id']}; container={record['container']}; po={record['po_id']}", 1, 1)
    c.save()


def import_records(conn, count: int) -> list[dict]:
    rows = conn.execute("""
        SELECT ce.customs_entry_id,ce.entry_date,ce.tariff_code,ce.customs_value,ce.duty_amount,
               ce.currency_code,ce.clearance_status,sh.shipment_id,sh.po_id,sc.container_number,
               s.supplier_name,s.country
        FROM skope.customs_entry ce JOIN skope.shipment sh USING(shipment_id)
        JOIN skope.shipment_container sc USING(shipment_id)
        JOIN skope.purchase_order po USING(po_id) JOIN skope.dim_supplier s USING(supplier_id)
        ORDER BY ce.customs_entry_id LIMIT %s
    """, (count,)).fetchall()
    columns = ["entry","date","tariff","value","duty","currency","status","shipment","po","container","supplier","country"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def render_import(record: dict, path: Path) -> None:
    pagesize = landscape(A4)
    c = pdf_canvas(path, pagesize)
    w, h = pagesize
    curr = clean_currency(record["currency"], "INR")
    
    heading(c, w, h, "CUSTOMS IMPORT REGISTER", f"Report ID: {record['entry']}", "SKOPE Ocean.png", OCEAN)
    
    cards = [
        ("CUSTOMS ENTRIES", "1 Entry (Batch Ref)"),
        ("CONTAINER REF", record["container"]),
        ("CUSTOMS VALUE", money(record["value"], curr)),
        ("ASSESSED DUTY", money(record["duty"], curr)),
        ("COUNTRY OF ORIGIN", safe(record["country"], "Global"))
    ]
    card_w = (w - 64) / 5
    for index, (label, value) in enumerate(cards):
        cx = 32 + index * card_w
        c.setFillColor(CARD_BG)
        c.roundRect(cx, h - 175, card_w - 6, 52, 3, fill=1, stroke=0)
        c.setStrokeColor(BORDER_GRAY)
        c.roundRect(cx, h - 175, card_w - 6, 52, 3, fill=0, stroke=1)
        label_value(c, cx + 8, h - 138, label, value, card_w - 20)
        
    table_rows = [
        [record["entry"], str(record["date"]), record["shipment"], record["container"],
         record["po"], str(record["tariff"]), money(record["value"], curr), money(record["duty"], curr), record["status"]],
        ["SUMMARY TOTAL", "--", "1 Shipment", "1 FCL", record["po"], "Standard", money(record["value"], curr), money(record["duty"], curr), "VERIFIED"]
    ]
    
    table(c, 32, h - 210, [95, 75, 85, 85, 80, 70, 95, 95, 94],
          ["CUSTOMS ENTRY", "ENTRY DATE", "SHIPMENT REF", "CONTAINER NO.", "PO NUMBER", "TARIFF CODE", "CUSTOMS VALUE", "DUTY AMOUNT", "CLEARANCE STATUS"],
          table_rows, row_h=38, header_h=24, header_color=OCEAN, font_size=6.5)
          
    c.setFont("Helvetica-Bold", 7)
    c.drawString(32, h - 315, "STATUTORY DECLARATION & AUDIT PROVENANCE")
    c.setFont("Helvetica", 6.8)
    wrapped(c, f"Declarant / Importer: SKOPE Global Enterprise Ltd. Supplier of Record: {record['supplier']}. Assessed customs duty and tariff classification code {record['tariff']} have been calculated in compliance with customs valuation regulations and linked to source purchase order {record['po']}.",
            32, h - 328, 520, size=6.8, leading=9.5, max_lines=3)
            
    signature(c, "signature-40124.png", 560, 60, 225, "Customs Officer / Importer Representative", record["date"])
    
    disclosure(c, w, h, f"customs={record['entry']}; shipment={record['shipment']}", 1, 1)
    c.save()


def audit_records(conn, count: int) -> list[dict]:
    if count == 4:
        headers = conn.execute("""
            (SELECT a.audit_id,a.audit_date,a.audit_type,a.auditor_reference,a.status,l.facility_name,l.source_location_id,l.location_type
             FROM skope.warehouse_audit a JOIN skope.dim_location l USING(location_key)
             WHERE a.audit_type='SAFETY_RISK' ORDER BY a.audit_id LIMIT 2)
            UNION ALL
            (SELECT a.audit_id,a.audit_date,a.audit_type,a.auditor_reference,a.status,l.facility_name,l.source_location_id,l.location_type
             FROM skope.warehouse_audit a JOIN skope.dim_location l USING(location_key)
             WHERE a.audit_type='INVENTORY_CONTROL' ORDER BY a.audit_id LIMIT 2)
        """).fetchall()
    else:
        headers = conn.execute("""
            SELECT a.audit_id,a.audit_date,a.audit_type,a.auditor_reference,a.status,l.facility_name,l.source_location_id,l.location_type
            FROM skope.warehouse_audit a JOIN skope.dim_location l USING(location_key)
            ORDER BY a.audit_id LIMIT %s
        """, (count,)).fetchall()
    records = []
    for row in headers:
        findings = conn.execute("""
            SELECT finding_text,priority,required_action,due_date FROM skope.audit_finding
            WHERE audit_id=%s ORDER BY finding_id
        """, (row[0],)).fetchall()
        records.append({"audit_id": row[0], "date": row[1], "type": row[2], "auditor": row[3], "status": row[4],
                        "facility": row[5], "location_id": row[6], "location_type": row[7], "findings": findings})
    return records


def render_audit(record: dict, path: Path) -> None:
    c = pdf_canvas(path)
    w, h = A4
    logo = "SKOPE Express.png" if record["location_type"] == "DARK_STORE" else "SKOPE Domestic.png"
    
    if record["type"] == "INVENTORY_CONTROL":
        for page_no in range(1, 4):
            logo_panel(c, LOGOS / logo, 38, h - 82, 130, 50)
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 15); c.drawRightString(w - 38, h - 45, "MB51 INVENTORY MOVEMENT REVIEW")
            c.setFont("Helvetica", 7); c.drawRightString(w - 38, h - 62, f"Doc ID: {record['audit_id']}  |  Date: {safe(record['date'])}")
            c.setStrokeColor(colors.black); c.setLineWidth(0.8); c.line(38, h - 94, w - 38, h - 94)
            
            if page_no == 1:
                c.setFont("Times-Bold", 16); c.drawString(48, h - 130, "Inventory Movement Analysis & Audit")
                wrapped(c, f"Facility Reviewed: {record['facility']} (Site ID: {record['location_id']}). This audit report examines SAP MB51 material movement postings, stock ledger integrity, physical receiving variance tolerances, and required reconciliation adjustments.",
                        48, h - 155, w - 96, font="Times-Roman", size=10, leading=15, max_lines=5)
                        
                c.setFont("Times-Bold", 12); c.drawString(48, h - 230, "1. Scope & Verification Methodology")
                wrapped(c, "All material movements (Types 101 Goods Receipt, 102 Reversal, 261 Goods Issue, and 311 Plant Transfer) posted during the audit window were cross-verified against purchase orders, container packing lists, and warehouse goods receipt logs.",
                        48, h - 252, w - 96, font="Times-Roman", size=9.8, leading=14.5, max_lines=6)
                        
                c.setFont("Times-Bold", 12); c.drawString(48, h - 350, "2. Executive Control Observations")
                bullets = [
                    "Movement timestamps reconcile accurately with dark-store inbound scan sequences.",
                    "Quarantined and damaged stock lines require mandatory disposition sign-off within 24 hours.",
                    "Cycle-count discrepancy adjustments exceeding tolerance threshold require plant controller approval."
                ]
                yy = h - 375
                for bullet in bullets:
                    c.setFont("Times-Bold", 10); c.drawString(50, yy, "•")
                    yy = wrapped(c, bullet, 65, yy, w - 120, font="Times-Roman", size=9.8, leading=14.5, max_lines=3) - 8
                    
            elif page_no == 2:
                c.setFont("Times-Bold", 14); c.drawString(48, h - 130, "Detailed Exception Items & Corrective Plans")
                yy = h - 165
                for idx, (finding, priority, action, due) in enumerate(record["findings"], 1):
                    c.setFont("Times-Bold", 10.5)
                    c.drawString(48, yy, f"{idx}. Observation [{safe(priority)} Priority]")
                    yy = wrapped(c, finding, 65, yy - 20, w - 120, font="Times-Roman", size=9.5, leading=14, max_lines=4) - 8
                    
                    c.setFont("Times-Bold", 9); c.drawString(65, yy, "Required Corrective Action:")
                    yy = wrapped(c, action, 65, yy - 16, w - 120, font="Times-Roman", size=9.2, leading=13.5, max_lines=4) - 6
                    
                    c.setFont("Times-Italic", 8); c.drawString(65, yy, f"Target Due Date: {safe(due)}")
                    yy -= 30
                    if yy < 120:
                        break
            else:
                c.setFont("Times-Bold", 14); c.drawString(48, h - 130, "Conclusion & Executive Sign-off")
                wrapped(c, "The inventory movement controls for this facility have been reviewed. Action items listed in Section 2 must be tracked to completion before formal audit closure. Historical movement records remain permanently filed in the central data warehouse.",
                        48, h - 160, w - 96, font="Times-Roman", size=10, leading=15, max_lines=6)
                        
                signature(c, "signature-40135.png", 62, 170, 190, "Inventory Control Reviewer", record["date"])
                signature(c, "signature-40133.png", 340, 170, 190, "Facility Operations Lead", record["date"])
                
            disclosure(c, w, h, f"audit={record['audit_id']}; facility={record['location_id']}", page_no, 3)
            c.showPage()
        c.save()
        return

    # Warehouse Safety & Risk Audit
    for page_no in range(1, 4):
        logo_panel(c, LOGOS / logo, 34, h - 80, 130, 50)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 15); c.drawRightString(w - 34, h - 45, "WAREHOUSE SAFETY & RISK AUDIT")
        c.setFont("Helvetica", 7.5); c.drawRightString(w - 34, h - 62, f"Audit ID: {record['audit_id']}")
        c.setStrokeColor(colors.black); c.line(34, h - 94, w - 34, h - 94)
        
        label_value(c, 38, h - 112, "Facility", f"{record['facility']} ({record['location_id']})", 220)
        label_value(c, 285, h - 112, "Audit Date", record["date"], 100)
        label_value(c, 405, h - 112, "Lead Auditor", record["auditor"], 140)
        
        if page_no == 1:
            c.setFillColor(NAVY); c.rect(34, h - 180, w - 68, 24, fill=1, stroke=0)
            c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 9.5); c.drawString(43, h - 166, "1. AUDIT EXECUTIVE SUMMARY")
            c.setFillColor(INK)
            wrapped(c, "This comprehensive safety and risk evaluation assesses physical perimeter security, material handling compliance, emergency egress readiness, fire prevention, and hazardous spill protocols. Non-compliant items require mandatory tracked action.",
                    36, h - 200, w - 72, size=8, leading=11, max_lines=4)
                    
            checklist = [
                ("1", "Emergency exit routes and fire doors are completely unobstructed", "YES"),
                ("2", "Electronic badge access logs and visitor escort registers are maintained", "YES"),
                ("3", "High-value and regulated inventory lines are physically segregated", "YES"),
                ("4", "Aisle safety floor markings and pedestrian walkways are clear", "NO"),
                ("5", "Fire extinguishers and sprinkler heads have valid inspection tags", "YES"),
                ("6", "Loading dock safety interlocks and vehicle chocks are operational", "YES"),
                ("7", "Material handling equipment (forklifts/reach trucks) daily pre-check is logged", "NO"),
                ("8", "Damaged goods and chemical quarantine zones are strictly enforced", "YES"),
                ("9", "Inbound container bolt seal integrity verification is recorded", "YES"),
                ("10", "Facility safety incident escalation procedure is visibly posted", "NO")
            ]
            
            table_y = h - 250
            c.setFillColor(NAVY); c.rect(34, table_y, w - 68, 20, fill=1, stroke=0)
            c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 7)
            c.drawString(40, table_y + 6, "#")
            c.drawString(75, table_y + 6, "CONTROL QUESTION / SAFETY STANDARD")
            c.drawRightString(w - 55, table_y + 6, "RESULT")
            
            row_y = table_y
            for q_num, question, res in checklist:
                row_y -= 25
                c.setFillColor(colors.white if int(q_num) % 2 != 0 else LIGHT)
                c.rect(34, row_y, w - 68, 25, fill=1, stroke=0)
                c.setStrokeColor(MID)
                c.rect(34, row_y, w - 68, 25, fill=0, stroke=1)
                
                c.setFillColor(INK); c.setFont("Helvetica-Bold", 7); c.drawString(40, row_y + 8, q_num)
                c.setFont("Helvetica", 6.8); wrapped(c, question, 75, row_y + 15, w - 190, size=6.8, leading=8.5, max_lines=2)
                
                badge_bg = GREEN if res == "YES" else RED
                badge(c, w - 85, row_y + 5, 42, 14, f"{res} {'✓' if res=='YES' else '✗'}", badge_bg, font_size=6.2)
                
        elif page_no == 2:
            c.setFillColor(NAVY); c.rect(34, h - 180, w - 68, 24, fill=1, stroke=0)
            c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 9.5); c.drawString(43, h - 166, "2. DEFICIENCY FINDINGS & ACTION PLAN")
            
            rows = []
            for index, item in enumerate(record["findings"], 1):
                rows.append([str(index), item[0], item[1], item[2], safe(item[3])])
                
            table(c, 34, h - 210, [25, 185, 55, 195, 68],
                  ["#", "IDENTIFIED FINDING", "PRIORITY", "REQUIRED CORRECTIVE ACTION", "DUE DATE"],
                  rows, row_h=65, header_h=22, header_color=NAVY, font_size=6.5)
                  
        else:
            c.setFillColor(NAVY); c.rect(34, h - 180, w - 68, 24, fill=1, stroke=0)
            c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 9.5); c.drawString(43, h - 166, "3. CLOSEOUT & AUDITOR ACCOUNTABILITY")
            c.setFillColor(INK)
            wrapped(c, "All findings documented in Section 2 have been reviewed with site leadership. Mandatory corrective actions must be closed with photographic proof and work orders prior to the next scheduled assessment. Formal sign-off below verifies the accuracy of this inspection.",
                    36, h - 205, w - 72, size=8.5, leading=12, max_lines=5)
                    
            signature(c, "signature-40135.png", 65, 170, 180, "Lead Safety Auditor", record["date"])
            signature(c, "—Pngtree—beautiful signature handwritten with pen_22544925.png", 340, 170, 180,
                      "Quality & Safety Inspector", record["date"])
                      
        disclosure(c, w, h, f"audit={record['audit_id']}; facility={record['location_id']}", page_no, 3)
        c.showPage()
    c.save()


def contract_records(conn, count: int) -> list[dict]:
    if count == 6:
        headers = conn.execute("""
            (SELECT c.contract_id,c.contract_type,c.effective_date,c.expiry_date,c.currency_code,c.status,
                    s.supplier_id,s.supplier_name,p.source_partner_id,p.partner_name
             FROM skope.contract c LEFT JOIN skope.dim_supplier s USING(supplier_id)
             LEFT JOIN skope.dim_partner p ON p.partner_key=c.carrier_partner_key
             WHERE c.contract_type='3PL_WAREHOUSING' ORDER BY c.contract_id LIMIT 2)
            UNION ALL
            (SELECT c.contract_id,c.contract_type,c.effective_date,c.expiry_date,c.currency_code,c.status,
                    s.supplier_id,s.supplier_name,p.source_partner_id,p.partner_name
             FROM skope.contract c LEFT JOIN skope.dim_supplier s USING(supplier_id)
             LEFT JOIN skope.dim_partner p ON p.partner_key=c.carrier_partner_key
             WHERE c.contract_type='VENDOR_SUPPLY' ORDER BY c.contract_id LIMIT 2)
            UNION ALL
            (SELECT c.contract_id,c.contract_type,c.effective_date,c.expiry_date,c.currency_code,c.status,
                    s.supplier_id,s.supplier_name,p.source_partner_id,p.partner_name
             FROM skope.contract c LEFT JOIN skope.dim_supplier s USING(supplier_id)
             LEFT JOIN skope.dim_partner p ON p.partner_key=c.carrier_partner_key
             WHERE c.contract_type='CARRIER_MSA' ORDER BY c.contract_id LIMIT 2)
        """).fetchall()
    else:
        headers = conn.execute("""
            SELECT c.contract_id,c.contract_type,c.effective_date,c.expiry_date,c.currency_code,c.status,
                   s.supplier_id,s.supplier_name,p.source_partner_id,p.partner_name
            FROM skope.contract c LEFT JOIN skope.dim_supplier s USING(supplier_id)
            LEFT JOIN skope.dim_partner p ON p.partner_key=c.carrier_partner_key
            ORDER BY c.contract_id LIMIT %s
        """, (count,)).fetchall()
    records = []
    for row in headers:
        clauses = conn.execute("""
            SELECT clause_type,clause_title,clause_text,structured_terms FROM skope.contract_clause
            WHERE contract_id=%s ORDER BY clause_id
        """, (row[0],)).fetchall()
        records.append({"contract_id": row[0], "type": row[1], "effective": row[2], "expiry": row[3],
                        "currency": row[4], "status": row[5], "supplier_id": row[6], "supplier": row[7],
                        "carrier_id": row[8], "carrier": row[9], "clauses": clauses})
    return records


def render_contract(record: dict, path: Path) -> None:
    c = pdf_canvas(path)
    w, h = A4
    
    if record["type"] == "CARRIER_MSA":
        party = "Maersk Line" if record.get("carrier_id") == "V44_3" else (record.get("carrier") or "Maersk Line")
        party_id = record.get("carrier_id") or "V44_3"
        counterparty_display = f"{party} ({party_id})"
    else:
        party = record["supplier"] or record["carrier"] or "Contracted Partner"
        party_id = record["supplier_id"] or record["carrier_id"] or "PARTNER-ID"
        counterparty_display = f"{party} ({party_id})"
    
    title_map = {
        "3PL_WAREHOUSING": "3PL WAREHOUSING & LOGISTICS AGREEMENT",
        "VENDOR_SUPPLY": "MASTER VENDOR SUPPLY AGREEMENT",
        "CARRIER_MSA": "OCEAN CARRIER MASTER SERVICE CONTRACT"
    }
    title = title_map.get(record["type"], "COMMERCIAL SERVICE AGREEMENT")
    
    for page_no in range(1, 5):
        if record["type"] == "3PL_WAREHOUSING":
            c.setFont("Times-Bold", 14); c.drawCentredString(w / 2, h - 45, title)
            c.setLineWidth(0.8); c.line(60, h - 52, w - 60, h - 52)
            c.setFont("Times-Roman", 7.5); c.drawCentredString(w / 2, h - 65, f"Contract Identifier: {record['contract_id']}  |  Classification: Controlled Legal Agreement")
        elif record["type"] == "VENDOR_SUPPLY":
            c.setFillColor(NAVY); c.rect(0, h - 78, w, 78, fill=1, stroke=0)
            logo_panel(c, LOGOS / "SKOPE Global.png", 28, h - 68, 120, 48)
            c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 15); c.drawRightString(w - 28, h - 35, title)
            c.setFont("Helvetica", 7.5); c.drawRightString(w - 28, h - 52, f"Agreement Ref: {record['contract_id']}")
        else:
            c.setFillColor(OCEAN); c.rect(0, h - 88, w, 88, fill=1, stroke=0)
            logo_panel(c, LOGOS / "SKOPE Ocean.png", 20, h - 74, 115, 48)
            partner_logo = carrier_logo({"carrier_id": record.get("carrier_id"), "carrier_name": record.get("carrier")})
            if partner_logo:
                logo_panel(c, LOGOS / partner_logo, w - 135, h - 74, 115, 48)
            
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 10.5)
            carrier_mode = "AIR CARRIER" if record.get("carrier_id") == "DHL-AIR-DEMO" else "OCEAN CARRIER"
            c.drawCentredString(w / 2, h - 34, carrier_mode)
            c.drawCentredString(w / 2, h - 48, "MASTER SERVICE CONTRACT")
            c.setFont("Helvetica", 7)
            c.drawCentredString(w / 2, h - 64, f"Contract ID: {record['contract_id']}")
            
        c.setFillColor(INK)
        
        if page_no == 1:
            base_y = h - (115 if record["type"] != "3PL_WAREHOUSING" else 95)
            if record["type"] != "3PL_WAREHOUSING":
                label_value(c, 38, base_y, "Effective Date", record["effective"], 110)
                label_value(c, 175, base_y, "Expiration Date", record["expiry"], 110)
                label_value(c, 320, base_y, "Contracting Counterparty", counterparty_display, 230)
                
            c.setFont("Times-Bold", 13); c.setFillColor(INK)
            c.drawCentredString(w / 2, base_y - 65, "PREAMBLE & RECITALS")
            
            intro = (f"This Master Agreement (the 'Agreement') is entered into and made effective as of {safe(record['effective'])} "
                     f"by and between SKOPE Global Enterprise Ltd (the 'Company') and {counterparty_display}, "
                     f"individually a 'Party' and collectively the 'Parties'.")
            wrapped(c, intro, 55, base_y - 95, w - 110, font="Times-Roman", size=10, leading=15, max_lines=6)
            
            c.setFont("Times-Bold", 11); c.drawString(55, base_y - 170, "WITNESSETH THAT:")
            recitals = (
                "WHEREAS, the Company engages in global enterprise supply chain operations, logistics fulfillment, and retail distribution; and\n\n"
                "WHEREAS, the Counterparty provides certified commercial transportation, containerized shipping, and specialized freight services meeting Company operational benchmarks; and\n\n"
                "NOW, THEREFORE, in consideration of the mutual covenants, performance obligations, freight rate schedules, and agreed service level agreements (SLAs) herein contained, the Parties agree as set forth in the following Articles."
            )
            wrapped(c, recitals, 55, base_y - 195, w - 110, font="Times-Roman", size=9.8, leading=14.5, max_lines=12)
            
            if record["type"] == "3PL_WAREHOUSING":
                wrapped(c, "The Warehouse Operator shall receive, inspect, securely store, and dispatch goods solely against authenticated dispatch authorizations, maintaining full digital audit traceability at all times.",
                        55, base_y - 340, w - 110, font="Times-Roman", size=9.5, leading=14, max_lines=6)
            elif record["type"] == "CARRIER_MSA":
                wrapped(c, "The Carrier shall allocate transport capacity, maintain published transit schedules, provide electronic shipment status events, and adhere to the service commitments applicable across designated trade lanes.",
                        55, base_y - 340, w - 110, font="Times-Roman", size=9.5, leading=14, max_lines=6)
                        
        elif page_no in (2, 3):
            clause_slice = record["clauses"][(page_no - 2) * 3:(page_no - 1) * 3]
            y = h - 130
            for index, (kind, clause_title, clause_text, terms) in enumerate(clause_slice, start=(page_no - 2) * 3 + 1):
                c.setFont("Times-Bold", 11.5)
                c.setFillColor(INK)
                c.drawString(45, y, f"Article {index}. {safe(clause_title)} [{safe(kind)}]")
                
                y = wrapped(c, clause_text, 60, y - 20, w - 115, font="Times-Roman", size=9.5,
                            leading=14, max_lines=6) - 12
                            
                if terms:
                    readable = "  |  ".join(f"{safe(k).replace('_', ' ').title()}: {safe(v)}" for k, v in terms.items())
                    c.setFillColor(CARD_BG); c.rect(60, y - 18, w - 120, 22, fill=1, stroke=0)
                    c.setStrokeColor(BORDER_GRAY); c.rect(60, y - 18, w - 120, 22, fill=0, stroke=1)
                    c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 7)
                    c.drawString(68, y - 7, "ENFORCEABLE PARAMETERS:")
                    c.setFillColor(INK); c.setFont("Helvetica", 7)
                    c.drawString(200, y - 7, readable[:85])
                    y -= 30
                else:
                    y -= 15
                    
                if y < 110:
                    break
        else:
            c.setFont("Times-Bold", 13)
            c.setFillColor(INK)
            c.drawString(45, h - 140, "SIGNATURES & EXECUTION")
            wrapped(c, "IN WITNESS WHEREOF, the Parties hereto have caused this Agreement to be executed by their duly authorized corporate officers as of the Effective Date first written above. Each signatory warrants authority to bind their respective entity.",
                    45, h - 165, w - 90, font="Times-Roman", size=10, leading=15, max_lines=5)
                    
            sig_img = "signature-40120.png" if record["type"] == "VENDOR_SUPPLY" else "signature-40140.png"
            signature(c, sig_img, 55, 190, 180, "SKOPE Authorized Signatory", record["effective"])
            
            short_party = safe(party)[:30]
            signature(c, "—Pngtree—signature isolated the signature is_15785835.png", 340, 190, 195,
                      f"Representative — {short_party}", record["effective"])
                      
        disclosure(c, w, h, f"contract={record['contract_id']}; counterparty={party_id}", page_no, 4)
        c.showPage()
    c.save()


def shortage_records(conn, count: int) -> list[dict]:
    po_ids = conn.execute("""
        SELECT DISTINCT po.po_id FROM skope.purchase_order po
        JOIN skope.purchase_order_line pol USING(po_id) JOIN skope.goods_receipt gr USING(po_line_id)
        WHERE gr.received_quantity + coalesce(gr.rejected_quantity,0) < pol.ordered_quantity
        ORDER BY po.po_id LIMIT %s
    """, (count,)).fetchall()
    records = []
    for (po_id,) in po_ids:
        head = conn.execute("""
            SELECT po.required_date,s.supplier_id,s.supplier_name,l.facility_name,l.source_location_id
            FROM skope.purchase_order po JOIN skope.dim_supplier s USING(supplier_id)
            LEFT JOIN skope.dim_location l ON l.location_key=po.ship_to_location_key WHERE po.po_id=%s
        """, (po_id,)).fetchone()
        lines = conn.execute("""
            SELECT pol.supplier_sku,p.product_name,pol.ordered_quantity,gr.received_quantity,gr.rejected_quantity,
                   gr.provenance->>'root_cause'
            FROM skope.purchase_order_line pol JOIN skope.dim_product p USING(product_key)
            JOIN skope.goods_receipt gr USING(po_line_id)
            WHERE pol.po_id=%s AND gr.received_quantity+coalesce(gr.rejected_quantity,0)<pol.ordered_quantity
            ORDER BY pol.po_line_id
        """, (po_id,)).fetchall()
        records.append({"po_id": po_id, "required": head[0], "supplier_id": head[1], "supplier": head[2],
                        "facility": head[3], "location_id": head[4], "lines": lines})
    return records


def render_shortage(record: dict, path: Path) -> None:
    pagesize = landscape(A4)
    c = pdf_canvas(path, pagesize)
    w, h = pagesize
    report_id = record["po_id"].replace("PO-", "SSR-")
    
    heading(c, w, h, "INTERNAL SHORTAGE REPORT", f"Report Ref: {report_id}", "SKOPE Global.png", NAVY)
    
    c.setFillColor(MUTED); c.setFont("Helvetica-Bold", 7)
    c.drawRightString(w - 32, h - 105, f"LINKED PURCHASE ORDER: {record['po_id']}")
    
    total_gap = sum(float(line[2]) - float(line[3]) for line in record["lines"])
    crit_count = sum(1 for line in record["lines"] if (float(line[2]) - float(line[3])) >= 5)
    
    cards = [
        ("OPEN SHORTAGE LINES", str(len(record["lines"]))),
        ("CRITICAL / RED FLAGS", str(crit_count)),
        ("TOTAL QUANTITY GAP", f"{total_gap:,.0f} Units"),
        ("RECEIVING FACILITY", safe(record["location_id"], "DC-01")),
        ("PRIMARY SUPPLIER", safe(record["supplier"]))
    ]
    card_w = (w - 64) / 5
    for index, (label, value) in enumerate(cards):
        cx = 32 + index * card_w
        c.setFillColor(CARD_BG)
        c.roundRect(cx, h - 175, card_w - 6, 52, 3, fill=1, stroke=0)
        c.setStrokeColor(BORDER_GRAY)
        c.roundRect(cx, h - 175, card_w - 6, 52, 3, fill=0, stroke=1)
        label_value(c, cx + 8, h - 138, label, value, card_w - 18)
        
    rows = []
    for sku, product, ordered, received, rejected, cause in record["lines"]:
        gap = float(ordered) - float(received)
        flag = "CRITICAL" if gap >= 5 else "AT RISK"
        rows.append([sku, product, safe(record["location_id"]), flag,
                     f"{float(ordered):.0f}", f"{float(received):.0f}", f"{gap:.0f}", safe(cause, "Supplier quantity variance")])
                     
    table_y = h - 210
    total_w = 780
    c.setFillColor(NAVY); c.rect(32, table_y - 24, total_w, 24, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 6.5)
    
    headers = ["ITEM SKU", "PRODUCT DESCRIPTION", "FACILITY", "FLAG", "EXPECTED", "RECEIVED", "GAP", "RECORDED ROOT CAUSE"]
    widths = [95, 185, 65, 70, 60, 60, 50, 195]
    
    cursor = 32
    for wd, hd in zip(widths, headers):
        c.drawString(cursor + 4, table_y - 16, hd)
        cursor += wd
        
    y = table_y - 24
    for r_idx, row in enumerate(rows):
        c.setFillColor(colors.white if r_idx % 2 == 0 else LIGHT)
        c.rect(32, y - 36, total_w, 36, fill=1, stroke=0)
        
        cursor = 32
        for col_idx, (wd, val) in enumerate(zip(widths, row)):
            c.setStrokeColor(MID)
            c.rect(cursor, y - 36, wd, 36, fill=0, stroke=1)
            
            if col_idx == 3:
                b_color = RED if val == "CRITICAL" else AMBER
                badge(c, cursor + 6, y - 24, 52, 14, val, b_color, font_size=6)
            else:
                c.setFillColor(INK)
                wrapped(c, val, cursor + 4, y - 12, wd - 8, size=6.5, leading=8.5, max_lines=2)
            cursor += wd
        y -= 36
        
    c.setFont("Helvetica", 6.5)
    wrapped(c, "Material Shortage Protocol: Critical flags must be escalated to the category procurement manager immediately. Expedited replenishment POs require control tower approval.",
            32, 120, 520, size=6.5, leading=8.5, max_lines=2)
            
    signature(c, "output-onlinepngtools.png", 460, 55, 140, "Receiving Officer", record["required"])
    signature(c, "signature-40133.png", 640, 55, 140, "Operations Lead", record["required"])
    
    disclosure(c, w, h, f"po={record['po_id']}; supplier={record['supplier_id']}", 1, 1)
    c.save()


def sop_records(conn, count: int) -> list[dict]:
    if count == 4:
        rows = conn.execute("""
            (SELECT policy_id,policy_type,title,body_text,effective_date,review_date,version
             FROM skope.enterprise_policy WHERE policy_type='FULL_SOP' ORDER BY policy_id LIMIT 2)
            UNION ALL
            (SELECT policy_id,policy_type,title,body_text,effective_date,review_date,version
             FROM skope.enterprise_policy WHERE policy_type='QUICK_REFERENCE' ORDER BY policy_id LIMIT 2)
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT policy_id,policy_type,title,body_text,effective_date,review_date,version
            FROM skope.enterprise_policy ORDER BY policy_id LIMIT %s
        """, (count,)).fetchall()
    columns = ["policy_id","type","title","body","effective","review","version"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def get_sop_domain_content(title: str, policy_id: str) -> dict:
    """Generate in-depth, authentic procedural instructions tailored to each of the 50 SOP domains."""
    t_lower = title.lower()
    
    if any(k in t_lower for k in ("cold", "temp", "refrigerat", "chilled", "freezer")):
        return {
            "benchmarks": "Maintain strict temperature integrity (-18.0C to -22.0C for frozen; +2.0C to +4.0C for chilled). Log data logger curves upon dock entry and guarantee core-temperature probing within 15 minutes of door opening.",
            "scope": "Applies to all cold-storage dark stores, temperature-controlled cross-dock bays, reefer container staging berths, and refrigerated last-mile transport fleet assets.",
            "roles": [
                ("Cold Chain Quality Lead", "Validates continuous temperature data logs, conducts infrared core probe audits, and signs cold-chain release forms."),
                ("Dock Receiver / Handler", "Executes rapid pallet unloading within 15-minute SLA and operates high-speed insulated roll-up dock curtains."),
                ("Refrigeration Tech", "Performs bi-hourly glycol chiller inspections, defrost cycle monitoring, and backup generator transfer tests."),
                ("Inventory Controller", "Posts ERP movement 101 with mandatory lot-expiry dates and enforces Strict First-Expired First-Out (FEFO) rules.")
            ],
            "safety": "Thermal insulated freezer suits, anti-slip studded boots, and dual-redundant internal emergency escape release mechanisms are mandatory in sub-zero chambers.",
            "steps": [
                ("4.1 Cold Dock Pre-Cooling & Staging", "Verify the cold receiving anteroom is stabilized at +2C to +4C prior to opening reefer container doors."),
                ("4.2 Logger Extraction & Thermal Probing", "Retrieve USB/Bluetooth temperature data loggers from top, middle, and bottom pallet layers. Probe 5 sample cartons per SKU."),
                ("4.3 Rapid Transfer SLA (<15 Minutes)", "Transfer palletized stock into the deep-freeze chamber immediately. Maximum allowable ambient exposure is strictly 15 minutes."),
                ("4.4 Excursion Quarantine Protocol", "If core temperature exceeds +6.0C, instantly divert pallets to the Red Quarantine Chiller and trigger CAPA investigation."),
                ("4.5 ERP Batch & FEFO Expiry Recording", "Input manufacturing batch numbers and verified shelf-life into WMS to lock automated FEFO picking logic."),
                ("4.6 High-Bay Racking & Thermal Seal Check", "Store pallets in designated thermal rack locations. Verify high-speed automated freezer doors seal completely.")
            ],
            "sla": [
                ("Tier 1 (Minor)", "Temperature drift between +0.5C and +1.0C", "Shift QA Lead", "Resolve within 30 min"),
                ("Tier 2 (Moderate)", "Logger gap or drift +1.0C to +2.5C (>20 min)", "Cold Chain Manager", "Resolve within 2 hours"),
                ("Tier 3 (Critical)", "Compressor failure or temp > +4.0C (Chilled)", "Operations VP & Safety Head", "Immediate (< 1 hour)")
            ]
        }
    elif any(k in t_lower for k in ("hazard", "fire", "theft", "safety", "forklift", "housekeep", "dock safety", "shutdown", "incident")):
        return {
            "benchmarks": "Ensure zero Lost Time Incidents (LTI), 100% compliance with OSHA/GHS standards, complete daily pre-operational equipment safety checks, and maintain unobstructed emergency routes at all times.",
            "scope": "Mandatory across all fulfillment centers, dark store backrooms, material handling equipment (MHE) charging bays, hazardous waste bunds, and trailer yards.",
            "roles": [
                ("EHS & Safety Manager", "Administers facility safety protocols, conducts monthly mock evacuations, and leads Root Cause Analysis investigations."),
                ("Certified MHE Operator", "Executes daily 22-point forklift pre-checks, observes pedestrian zebra clearance zones, and enforces load capacity limits."),
                ("Facility Maintenance Lead", "Maintains sprinkler system hydrostatic pressure, inspects fire extinguishers, and manages eye-wash stations."),
                ("Emergency Floor Warden", "Leads section evacuation during alarms, secures hazardous chemical valves, and accounts for personnel at muster points.")
            ],
            "safety": "High-visibility safety vests, ANSI-rated steel-toed footwear, impact eye protection, and safety harnesses for work above 2 meters are strictly enforced.",
            "steps": [
                ("4.1 Daily Pre-Operational Equipment Inspection", "Inspect hydraulic lines, mast chains, brake interlocks, strobe lights, and dead-man safety switches before activating MHE."),
                ("4.2 Hazard Classification & Secondary Containment", "Verify Safety Data Sheets (SDS) and store Class 3/8 chemicals on secondary containment spill bunds with 110% capacity."),
                ("4.3 Pedestrian & Vehicle Traffic Segregation", "Maintain minimum 1.5-meter safety clearance between moving reach trucks and pedestrian safety walkways at all times."),
                ("4.4 Hazardous Spill Containment & Neutralization", "Deploy universal chemical absorbent booms, isolate contaminated drainage lines, and dispose of residue in Hazmat drums."),
                ("4.5 Emergency Alarm & Evacuation Protocol", "In the event of fire, spill, or structural breach, sound the general alarm and direct personnel to designated exterior muster stations."),
                ("4.6 Incident Investigation & CAPA Reporting", "File formal Safety Incident Report (SIR) in EHS software within 4 hours, documenting root cause and preventive barriers.")
            ],
            "sla": [
                ("Tier 1 (Minor)", "Near-miss event or minor floor oil seep", "Shift Safety Warden", "Resolve within 2 hours"),
                ("Tier 2 (Moderate)", "Equipment collision with rack or minor injury", "Facility EHS Lead", "Resolve within 4 hours"),
                ("Tier 3 (Critical)", "Chemical spill >5L, structural damage, fire", "General Manager & EHS VP", "Immediate (< 30 min)")
            ]
        }
    elif any(k in t_lower for k in ("cycle", "count", "inventory", "damage", "return", "quality", "recall", "shortage", "bin")):
        return {
            "benchmarks": "Achieve net inventory record accuracy (IRA) >= 99.85%, resolve cycle count discrepancies within 24 hours, and maintain strict quarantine control over defective or recalled stock.",
            "scope": "Governs all stockrooms, automated bin carousels, damaged goods staging bays, high-value security cages, and customer return triage areas.",
            "roles": [
                ("Inventory Control Manager", "Audits weekly cycle-count schedules, reviews stock adjustment reconciliations, and enforces ABC inventory stratification."),
                ("Quality Assurance Inspector", "Evaluates damaged stock, applies physical red hold tags, and signs off on supplier Return-to-Vendor (RTV) authorizations."),
                ("Dedicated Cycle Counter", "Executes blind inventory counts using RF scanning terminals and performs second-pass counts on all flagged variances."),
                ("SAP Inventory Controller", "Processes Movement Type 101, 102, 261, 311, and 551 scrap postings in ERP systems with authorized sign-off.")
            ],
            "safety": "Ensure stable pallet stacking (max height 1.6m), utilize safety harness in narrow-aisle man-up pickers, and maintain clear access to count ladders.",
            "steps": [
                ("4.1 ABC Velocity Schedule Generation", "Generate automated cycle-count sheets in WMS: Class A SKUs monthly, Class B quarterly, and Class C semi-annually."),
                ("4.2 Blind Physical Count Execution", "Perform physical piece-count of target storage bins without displaying system quantity to prevent confirmation bias."),
                ("4.3 Discrepancy Isolation & Re-Count", "If physical count deviates by >= 1 unit, trigger automated recount by an independent second auditor within 2 hours."),
                ("4.4 Transaction History Investigation", "Trace SAP MB51 transaction history (receipts, picks, transfers) to isolate misplacement, scanning errors, or shrinkage."),
                ("4.5 Defect Quarantine & Digital Freeze", "Immediately apply WMS digital lock 'Q-HOLD' and affix physical red quarantine stickers to damaged or suspect batches."),
                ("4.6 ERP Inventory Reconciliation Posting", "Post approved inventory adjustments (Movement 701/702) supported by signed variance justification and supervisor authorization.")
            ],
            "sla": [
                ("Tier 1 (Minor)", "Variance value < $100 (non-critical item)", "Inventory Lead", "Resolve within 24 hours"),
                ("Tier 2 (Moderate)", "Variance value $100 - $1,000 or damaged carton", "Inventory Manager", "Resolve within 8 hours"),
                ("Tier 3 (Critical)", "Discrepancy > $1,000, high-value theft, recall", "Plant Director & Finance Lead", "Immediate (< 2 hours)")
            ]
        }
    elif any(k in t_lower for k in ("outbound", "dispatch", "carrier", "proof", "pod", "export", "loading")):
        return {
            "benchmarks": "Attain on-time truck dispatch >= 98.5%, order pick accuracy >= 99.92%, zero shipping manifest discrepancies, and 100% digital proof of delivery capture.",
            "scope": "Encompasses order batching, wave picking, pack-and-weigh stations, shipping staging lanes, trailer loading docks, and carrier driver handovers.",
            "roles": [
                ("Outbound Dispatch Lead", "Directs wave pick sequencing, allocates dock staging doors, and verifies carrier arrival against pickup appointments."),
                ("RF Wave Picker", "Executes directed RF scan picking, verifies item UPC codes, and transits picked totes to assigned packing benches."),
                ("Pack & Weigh Specialist", "Applies standardized void fill, verifies package gross weight against theoretical tolerance (+-2%), and prints shipping labels."),
                ("Carrier Driver / Agent", "Inspects trailer load distribution, verifies pallet count, and signs physical/digital shipping manifest upon dispatch.")
            ],
            "safety": "Place wheel chocks under rear trailer tires, engage dock vehicle restraint locks, and observe maximum manual lifting limit of 25 kg per box.",
            "steps": [
                ("4.1 Order Wave Release & Pick Routing", "Release customer order waves through WMS, generating optimized pick paths for RF handheld scanning terminals."),
                ("4.2 Item Pick Verification & Tote Scanning", "Scan origin bin barcode, scan item SKU barcode, and confirm quantity before placing item into designated order tote."),
                ("4.3 Packing, Dunnage & Carton Sealing", "Pack items in heavy-duty corrugated cartons, apply bubble dunnage, and seal top/bottom seams with reinforced security tape."),
                ("4.4 Weight Check & SSCC-18 Labeling", "Pass sealed cartons over automated conveyor scales. Print and affix SSCC-18 shipping label to the upper right corner."),
                ("4.5 Pallet Staging & Stretch-Wrapping", "Stack shipping cartons in interlocking pattern up to 1.6m. Apply 4 layers of stretch film including pallet base overlap."),
                ("4.6 Vehicle Loading & Manifest Execution", "Scan pallet barcodes into carrier trailer. Confirm trailer load restraint bars are secured and obtain driver signature.")
            ],
            "sla": [
                ("Tier 1 (Minor)", "Pick variance or damaged packaging on bench", "Shift Dispatch Lead", "Resolve within 30 min"),
                ("Tier 2 (Moderate)", "Carrier delay >45 min or missing carton", "Outbound Manager", "Resolve within 2 hours"),
                ("Tier 3 (Critical)", "Truck departure without signed manifest", "Logistics Control Tower", "Immediate (< 1 hour)")
            ]
        }
    else:
        # General Inbound / Administrative / Governance
        return {
            "benchmarks": "Achieve 100% document audit compliance, 3-way matching accuracy between PO, GR, and Invoice, and complete dock turnaround within standard 90-minute appointment windows.",
            "scope": "Applies across all supply chain facilities, procurement desks, inventory control towers, customs clearance offices, and 3PL partner nodes.",
            "roles": [
                ("Operations Excellence Lead", "Formulates standard operating procedures, conducts quarterly compliance audits, and tracks operational KPIs."),
                ("Inbound Dock Supervisor", "Oversees trailer staging, verifies Advance Shipping Notices (ASN), and monitors unloading throughput."),
                ("Quality Audit Inspector", "Inspects outer packaging integrity, verifies seal numbers, and issues discrepancy non-conformance reports."),
                ("ERP Systems Controller", "Executes SAP MB51 Movement 101 Goods Receipt postings, closes purchase order lines, and files digital audit records.")
            ],
            "safety": "All warehouse and dock personnel must wear mandatory PPE, maintain clean aisles free of debris, and obey all facility speed limits.",
            "steps": [
                ("4.1 Pre-Arrival Notification & Dock Allocation", "Verify vendor Advance Shipping Notice (ASN) in WMS 24 hours prior to scheduled truck delivery appointment."),
                ("4.2 Security Inspection & Seal Verification", "Inspect container bolt seal against physical bill of lading. Record high-resolution photo in WMS prior to cutting."),
                ("4.3 Physical Unload & Barcode Scan Validation", "Unload pallets using electric pallet jacks. Scan master carton barcodes and verify quantities against PO lines."),
                ("4.4 Defect Logging & Quarantine Staging", "Identify short, damaged, or unmanifested inventory. Immediately move affected items to the Yellow Quarantine Bay."),
                ("4.5 ERP Goods Receipt Posting (Movement 101)", "Execute SAP/ERP Movement 101 transaction within 45 minutes of physical unloading to reflect real-time inventory."),
                ("4.6 Directed Put-Away & Bin Confirmation", "Transport approved inventory to assigned warehouse high-bay racking and scan bin barcode to confirm final placement.")
            ],
            "sla": [
                ("Tier 1 (Minor)", "Discrepancy <= 2 units or paperwork typo", "Shift Team Lead", "Resolve within 60 min"),
                ("Tier 2 (Moderate)", "Damaged freight or missing ASN documentation", "Warehouse Operations Head", "Resolve within 4 hours"),
                ("Tier 3 (Critical)", "Tampered container seal or unmanifested cargo", "Control Tower VP & Legal", "Immediate (< 2 hours)")
            ]
        }


def render_sop(record: dict, path: Path) -> None:
    pages = 3 if record["type"] == "FULL_SOP" else 2
    c = pdf_canvas(path)
    w, h = A4
    
    topic = record["title"].title()
    policy_id = record["policy_id"]
    content = get_sop_domain_content(record["title"], policy_id)
    
    for page_no in range(1, pages + 1):
        if record["type"] == "FULL_SOP":
            form_box(c, 30, h - 118, w - 60, 88, line=0.8)
            logo_panel(c, LOGOS / "SKOPE Global.png", 38, h - 105, 112, 60)
            
            c.line(160, h - 118, 160, h - 30)
            c.line(430, h - 118, 430, h - 30)
            
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 10)
            c.drawCentredString(295, h - 52, "STANDARD OPERATING PROCEDURE")
            wrapped(c, record["title"], 172, h - 72, 245, font="Helvetica-Bold", size=8.5, leading=11, max_lines=3)
            
            c.setFont("Helvetica-Bold", 6)
            c.drawString(438, h - 48, f"DOCUMENT: {policy_id}")
            c.drawString(438, h - 64, f"VERSION: {safe(record['version'])}")
            c.drawString(438, h - 80, f"EFFECTIVE: {safe(record['effective'])}")
            c.drawString(438, h - 96, f"REVIEW: {safe(record['review'])}")
            
        else:
            c.setFillColor(BLUE); c.rect(0, h - 105, w, 105, fill=1, stroke=0)
            logo_panel(c, LOGOS / "SKOPE Global.png", 28, h - 88, 130, 60)
            c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 17); c.drawRightString(w - 30, h - 48, "QUICK REFERENCE SOP")
            c.setFont("Helvetica", 7.5); c.drawRightString(w - 30, h - 68, f"Doc ID: {policy_id}  |  Version: {safe(record['version'])}")
            c.setFillColor(INK)
            
        if page_no == 1 and record["type"] == "FULL_SOP":
            c.setFont("Helvetica-Bold", 15); c.drawCentredString(w / 2, h - 160, "DOCUMENT APPROVAL & CONTROL")
            
            table(c, 48, h - 185, [125, 160, 115, 105],
                  ["ROLE", "NAME / FUNCTION", "DATE", "STATUS"],
                  [["Prepared By", "Operations Excellence & QA Lead", safe(record['effective']), "PREPARED"],
                   ["Reviewed By", "Regional Logistics & Warehouse Head", safe(record['effective']), "REVIEWED"],
                   ["Approved By", "Global Supply Chain VP", safe(record['effective']), "APPROVED"]],
                  row_h=28, header_color=NAVY, font_size=6.8)
                  
            c.setFont("Helvetica-Bold", 13); c.drawString(48, h - 315, "TABLE OF CONTENTS")
            toc = [
                ("1.0 Purpose & Operational Benchmarks", "2"),
                ("2.0 Operational Scope & Facility Applicability", "2"),
                ("3.0 Roles, Responsibilities & Authority Matrix", "2"),
                ("4.0 Step-by-Step Operating Work Instructions", "3"),
                ("5.0 Quality, Safety & Environmental Safeguards", "3"),
                ("6.0 Discrepancy Protocol & Escalation SLA Matrix", "3")
            ]
            yy = h - 345
            for label, p_target in toc:
                c.setFont("Helvetica", 9); c.drawString(58, yy, label)
                c.setStrokeColor(MID); c.setDash(1, 2); c.line(280, yy + 2, 485, yy + 2); c.setDash()
                c.drawRightString(515, yy, p_target)
                yy -= 24
                
            form_box(c, 48, h - 575, w - 96, 75, fill=CARD_BG)
            c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 7.5); c.drawString(58, h - 515, "POLICY STATEMENT & AUDIT MANDATE:")
            c.setFont("Helvetica", 7.2)
            wrapped(c, f"This Standard Operating Procedure governs all operational activities related to '{topic}' across all SKOPE facilities. Adherence to these work instructions is mandatory for all warehouse personnel, dark store operators, and 3PL contractors. Compliance is audited quarterly against internal logistics performance metrics.",
                    58, h - 530, w - 120, size=7.2, leading=10, max_lines=4)
                
        elif page_no == 1:
            c.setFont("Helvetica-Bold", 18); c.setFillColor(INK)
            wrapped(c, f"Standard Work Instructions: {record['title']}", 48, h - 145, w - 96, font="Helvetica-Bold", size=16, leading=20, max_lines=3)
            
            form_box(c, 48, h - 290, w - 96, 95, line=0.8)
            c.setFillColor(CARD_BG); c.rect(48, h - 225, w - 96, 30, fill=1, stroke=0)
            c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 8.5); c.drawString(60, h - 212, "CONTROL & AUDIT ATTRIBUTES")
            
            label_value(c, 60, h - 250, "Document Version", record["version"], 90)
            label_value(c, 190, h - 250, "Effective Date", record["effective"], 110)
            label_value(c, 345, h - 250, "Next Review Cycle", record["review"], 120)
            
            c.setFont("Helvetica-Bold", 11); c.setFillColor(NAVY); c.drawString(48, h - 325, "CRITICAL EXECUTION STEPS (PART 1)")
            
            steps_p1 = content["steps"][:3]
            y = h - 350
            for idx, (stitle, sbody) in enumerate(steps_p1, 1):
                c.setFillColor(BLUE); c.circle(60, y + 2, 10, fill=1, stroke=0)
                c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 7.5); c.drawCentredString(60, y - 1, str(idx))
                c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 8.5); c.drawString(80, y, stitle)
                y = wrapped(c, sbody, 80, y - 14, w - 130, size=7.8, leading=10.5, max_lines=4) - 18
                
            signature(c, "signature-40133.png", 330, h - 565, 180, "Operations Lead", record["effective"])
            
        elif page_no == 2 and record["type"] == "FULL_SOP":
            c.setFont("Helvetica-Bold", 12); c.setFillColor(NAVY)
            c.drawString(40, h - 145, "1.0 PURPOSE & OPERATIONAL BENCHMARKS")
            c.setFont("Helvetica", 8)
            wrapped(c, f"1.1 This Standard Operating Procedure defines the mandatory standards for executing '{topic}' across all network fulfillment nodes. The primary operational objective is: {content['benchmarks']}\n\n1.2 Compliance ensures full digital audit traceability from supplier dispatch through warehouse storage location assignment and final delivery.",
                    40, h - 162, w - 80, size=8, leading=11, max_lines=8)
                    
            c.setFont("Helvetica-Bold", 12); c.setFillColor(NAVY)
            c.drawString(40, h - 260, "2.0 OPERATIONAL SCOPE & FACILITY APPLICABILITY")
            c.setFont("Helvetica", 8)
            wrapped(c, f"2.1 {content['scope']}\n\n2.2 All shifts, full-time material handlers, operations leads, and quality audit inspectors must adhere strictly to the protocols set forth herein.",
                    40, h - 277, w - 80, size=8, leading=11, max_lines=7)
                    
            c.setFont("Helvetica-Bold", 12); c.setFillColor(NAVY)
            c.drawString(40, h - 375, "3.0 ROLES, RESPONSIBILITIES & AUTHORITY MATRIX")
            
            y = h - 395
            for r_title, r_desc in content["roles"]:
                c.setFont("Helvetica-Bold", 8); c.setFillColor(INK); c.drawString(50, y, f"•  {r_title}:")
                c.setFont("Helvetica", 7.8); wrapped(c, r_desc, 185, y, w - 235, size=7.8, leading=10, max_lines=2)
                y -= 22
                
            c.setFillColor(CARD_BG); c.rect(40, h - 565, w - 80, 50, fill=1, stroke=0)
            c.setStrokeColor(BORDER_GRAY); c.rect(40, h - 565, w - 80, 50, fill=0, stroke=1)
            c.setFillColor(RED); c.setFont("Helvetica-Bold", 8); c.drawString(50, h - 528, "MANDATORY SAFETY REQUIREMENT:")
            c.setFillColor(INK); c.setFont("Helvetica", 7.2)
            wrapped(c, content["safety"], 50, h - 542, w - 100, size=7.2, leading=9.5, max_lines=2)
                    
        elif page_no == 3 and record["type"] == "FULL_SOP":
            c.setFont("Helvetica-Bold", 12); c.setFillColor(NAVY)
            c.drawString(40, h - 145, "4.0 STEP-BY-STEP OPERATIONAL WORK INSTRUCTIONS")
            
            y = h - 165
            for stitle, sdesc in content["steps"]:
                c.setFont("Helvetica-Bold", 8); c.setFillColor(INK); c.drawString(48, y, stitle)
                y = wrapped(c, sdesc, 65, y - 13, w - 115, size=7.5, leading=9.5, max_lines=2) - 10
                
            c.setFont("Helvetica-Bold", 12); c.setFillColor(NAVY)
            c.drawString(40, h - 355, "5.0 DISCREPANCY MANAGEMENT & ESCALATION SLA")
            
            table(c, 40, h - 375, [85, 175, 125, 130],
                  ["SEVERITY TIER", "TRIGGER EVENT / CONDITION", "ESCALATION TARGET", "RESOLUTION SLA"],
                  content["sla"],
                  row_h=24, header_h=18, header_color=NAVY, font_size=6.2)
                  
            signature(c, "signature-40122.png", 340, 68, 180, "Warehouse Operations Manager", record["effective"])
            
        else:
            c.setFont("Helvetica-Bold", 14); c.setFillColor(NAVY)
            c.drawString(48, h - 145, "CRITICAL EXECUTION STEPS (PART 2)")
            
            steps_p2 = content["steps"][3:]
            y = h - 170
            for idx, (stitle, sbody) in enumerate(steps_p2, 4):
                c.setFillColor(BLUE); c.circle(60, y + 2, 10, fill=1, stroke=0)
                c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 7.5); c.drawCentredString(60, y - 1, str(idx))
                c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 8.5); c.drawString(80, y, stitle)
                y = wrapped(c, sbody, 80, y - 14, w - 130, size=7.8, leading=10.5, max_lines=4) - 20
                
            form_box(c, 48, h - 420, w - 96, 75, fill=CARD_BG)
            c.setFillColor(RED); c.setFont("Helvetica-Bold", 8); c.drawString(58, h - 360, "EMERGENCY & ESCALATION CONTACTS:")
            c.setFont("Helvetica", 7.2)
            wrapped(c, f"For broken seals, temperature excursions, hazardous spills, or security breaches:\n• SKOPE Logistics Control Tower Hotline: +1 (800) 555-SKOPE-OPS\n• Quality & Escalation Desk: qa-escalation@skope-logistics.com\n• On-Duty Lead for {policy_id}: Direct Radio Channel 4",
                    58, h - 375, w - 120, size=7.2, leading=10, max_lines=4)
                    
            signature(c, "signature-40133.png", 330, 68, 180, "Operations Lead", record["effective"])
            
        disclosure(c, w, h, f"policy={policy_id}; version={record['version']}", page_no, pages)
        c.showPage()
    c.save()


def index_document(conn, path: Path, doc_type: str, entity_type: str, entity_id: str, source_system: str,
                   metadata: dict) -> dict:
    relative = path.relative_to(DATASET).as_posix()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    document_id = db.stable_key("Phase2PDF", "document", relative)
    payload = {"phase": "PHASE2", "is_synthetic_document": True, "transformation_version": VERSION, **metadata}
    conn.execute("""
        INSERT INTO skope.document_index(document_id,document_type,source_path,source_system,
          primary_entity_type,primary_entity_id,sha256,byte_size,is_synthetic,rag_index_allowed,indexed_at,metadata,dataset_as_of)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE,TRUE,NULL,%s::jsonb,%s)
        ON CONFLICT(source_path) DO UPDATE SET document_type=excluded.document_type,
          source_system=excluded.source_system,primary_entity_type=excluded.primary_entity_type,
          primary_entity_id=excluded.primary_entity_id,sha256=excluded.sha256,byte_size=excluded.byte_size,
          is_synthetic=TRUE,rag_index_allowed=TRUE,indexed_at=NULL,metadata=excluded.metadata,dataset_as_of=excluded.dataset_as_of
    """, (document_id, doc_type, relative, source_system, entity_type, entity_id, digest,
          path.stat().st_size, json.dumps(payload), AS_OF))
    return {"path": relative, "document_type": doc_type, "primary_entity_type": entity_type,
            "primary_entity_id": entity_id, "sha256": digest, "byte_size": path.stat().st_size,
            "is_synthetic_document": True, "transformation_version": VERSION, **metadata}


def generate(database_url: str, mode: str, force: bool) -> list[dict]:
    counts = PILOT_COUNTS if mode == "pilot" else FULL_COUNTS
    manifest = []
    with db.psycopg.connect(database_url) as conn:
        if force:
            conn.execute("DELETE FROM skope.document_index WHERE metadata->>'phase'='PHASE2' AND metadata->>'mode'=%s", (mode,))
        builders = {
            "invoice": invoice_records(conn, counts["invoice"]),
            "purchase_order": po_records(conn, counts["purchase_order"]),
            "manifest": manifest_records(conn, counts["manifest"]),
            "import_report": import_records(conn, counts["import_report"]),
            "audit": audit_records(conn, counts["audit"]),
            "contract": contract_records(conn, counts["contract"]),
            "shortage": shortage_records(conn, counts["shortage"]),
            "sop": sop_records(conn, counts["sop"]),
        }
        for category, records in builders.items():
            if len(records) != counts[category]:
                raise RuntimeError(f"{category}: expected {counts[category]} records, received {len(records)}")
            output_dir = DOCS / FOLDERS[category] / "generated" / mode
            output_dir.mkdir(parents=True, exist_ok=True)
            expected_paths: set[Path] = set()
            for index, record in enumerate(records):
                if category == "invoice":
                    name = f"SKOPE_INV_{slug(record['order_id'])}.pdf"
                    path = output_dir / name
                    renderer = lambda record=record, path=path: render_invoice(record, path)
                    invoice_types = {"Blinkit": "INVOICE_QC", "IndianEComm": "INVOICE_DOMESTIC", "DataCo": "INVOICE_GLOBAL"}
                    dtype, etype, eid, source = invoice_types[record["source_system"]], "CUSTOMER_ORDER", record["order_id"], record["source_system"]
                    meta = {"source_records": [record["source_line_id"], record["order_id"]], "layout": dtype}
                elif category == "purchase_order":
                    path = output_dir / f"SKOPE_{record['po_id']}.pdf"
                    renderer = lambda record=record, path=path: render_po(record, path)
                    dtype, etype, eid, source = "PURCHASE_ORDER", "PURCHASE_ORDER", record["po_id"], "Phase2Synthetic"
                    meta = {"source_records": [record["po_id"], record["supplier_id"]]}
                elif category == "manifest":
                    if mode == "full":
                        subtype = "HBL" if index < 500 else "MANIFEST" if index < 1000 else "HAWB"
                    else:
                        subtype = ("HBL", "MANIFEST", "HAWB")[index % 3]
                    path = output_dir / f"SKOPE_{subtype}_{slug(record['container'])}.pdf"
                    renderer = lambda record=record, path=path, subtype=subtype: render_manifest(record, path, subtype)
                    dtype, etype, eid, source = subtype, "SHIPMENT", record["shipment_id"], "Phase2Synthetic"
                    meta = {"source_records": [record["shipment_id"], record["container"], record["po_id"]], "layout": subtype}
                elif category == "import_report":
                    path = output_dir / f"SKOPE_IMPORT_{record['entry']}.pdf"
                    renderer = lambda record=record, path=path: render_import(record, path)
                    dtype, etype, eid, source = "IMPORT_REPORT", "CUSTOMS_ENTRY", record["entry"], "Phase2Synthetic"
                    meta = {"source_records": [record["entry"], record["shipment"]]}
                elif category == "audit":
                    path = output_dir / f"SKOPE_AUDIT_{record['audit_id']}.pdf"
                    renderer = lambda record=record, path=path: render_audit(record, path)
                    dtype = "WAREHOUSE_SAFETY_AUDIT" if record["type"] == "SAFETY_RISK" else "MB51_INVENTORY_REPORT"
                    etype, eid, source = "WAREHOUSE_AUDIT", record["audit_id"], "Phase2Synthetic"
                    meta = {"source_records": [record["audit_id"], record["location_id"]], "layout": dtype}
                elif category == "contract":
                    path = output_dir / f"SKOPE_CONTRACT_{record['contract_id']}.pdf"
                    renderer = lambda record=record, path=path: render_contract(record, path)
                    contract_types = {"3PL_WAREHOUSING": "WAREHOUSING_AGREEMENT", "VENDOR_SUPPLY": "VENDOR_SUPPLY_AGREEMENT", "CARRIER_MSA": "CARRIER_MSA"}
                    dtype, etype, eid, source = contract_types[record["type"]], "CONTRACT", record["contract_id"], "Phase2Synthetic"
                    meta = {"source_records": [record["contract_id"], record["supplier_id"] or record["carrier_id"]], "layout": dtype}
                elif category == "shortage":
                    path = output_dir / f"SKOPE_SHORTAGE_{record['po_id']}.pdf"
                    renderer = lambda record=record, path=path: render_shortage(record, path)
                    dtype, etype, eid, source = "SHORTAGE_REPORT", "PURCHASE_ORDER", record["po_id"], "Phase2Synthetic"
                    meta = {"source_records": [record["po_id"], record["supplier_id"]]}
                else:
                    path = output_dir / f"SKOPE_{record['policy_id']}.pdf"
                    renderer = lambda record=record, path=path: render_sop(record, path)
                    dtype = "FULL_SOP" if record["type"] == "FULL_SOP" else "QUICK_REFERENCE_SOP"
                    etype, eid, source = "ENTERPRISE_POLICY", record["policy_id"], "Phase2Synthetic"
                    meta = {"source_records": [record["policy_id"]], "layout": dtype}
                expected_paths.add(path)
                if force or not path.exists():
                    renderer()
                manifest.append(index_document(conn, path, dtype, etype, eid, source, {"mode": mode, **meta}))
                if mode == "full" and ((index + 1) % 250 == 0 or index + 1 == len(records)):
                    print(f"  {category}: {index + 1:,}/{len(records):,}", flush=True)
            if force:
                for stale_path in output_dir.glob("*.pdf"):
                    if stale_path not in expected_paths:
                        stale_path.unlink()
            print(f"Generated/indexed {len(records):>5} PDFs -> {output_dir}")
        conn.commit()
    manifest_path = DOCS / f"phase2_pdf_manifest_{mode}.json"
    manifest_path.write_text(json.dumps({"mode": mode, "count": len(manifest), "documents": manifest}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SUCCEEDED", "mode": mode, "count": len(manifest), "manifest": str(manifest_path)}, indent=2))
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=db.DEFAULT_DATABASE_URL)
    parser.add_argument("--mode", choices=("pilot", "full"), default="pilot")
    parser.add_argument("--force", action="store_true", help="Overwrite generated outputs only; samples are never targeted")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    generate(args.database_url, args.mode, args.force)
