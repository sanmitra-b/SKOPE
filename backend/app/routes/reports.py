"""Supplier scorecard and export endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..auth import current_user
from ..models import UserContext
from ..services.supplier_reports import (
    fetch_supplier_scorecard,
    render_scorecard_csv,
    render_scorecard_pdf,
)


router = APIRouter(prefix="/api/reports")
EXPORT_LIMIT = 500


@router.get("/suppliers")
def supplier_report(
    risk_tier: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=EXPORT_LIMIT),
    _: UserContext = Depends(current_user),
) -> list[dict]:
    return fetch_supplier_scorecard(risk_tier, limit=limit)


@router.get("/suppliers.csv")
def supplier_report_csv(
    risk_tier: str | None = Query(default=None),
    _: UserContext = Depends(current_user),
) -> Response:
    rows = fetch_supplier_scorecard(risk_tier, limit=EXPORT_LIMIT)
    return Response(
        content=render_scorecard_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=skope_supplier_scorecard.csv"},
    )


@router.get("/suppliers.pdf")
def supplier_report_pdf(
    risk_tier: str | None = Query(default=None),
    _: UserContext = Depends(current_user),
) -> Response:
    rows = fetch_supplier_scorecard(risk_tier, limit=EXPORT_LIMIT)
    return Response(
        content=render_scorecard_pdf(rows),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=skope_supplier_scorecard.pdf"},
    )
