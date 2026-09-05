"""Natural-language to guarded SQL over SKOPE's analytics schema."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .query_planning import (
    compile_query_plan,
    local_query_plan,
    plan_metadata,
    plan_with_gemini,
)
from .retrieval import extract_identifiers
from .sql_tool import SQLResult, execute_readonly_sql, validate_and_rewrite_sql


@dataclass(frozen=True)
class GeneratedSQL:
    sql: str
    reason: str
    source: str = "llm"
    metadata: dict[str, Any] = field(default_factory=dict)

_SAFE_IDENTIFIER = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{1,63}$")


def _literal(identifier: str) -> str:
    """Quote only identifiers already constrained by the business-ID grammar."""
    if not _SAFE_IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"Unsafe business identifier: {identifier!r}")
    return "'" + identifier.replace("'", "''") + "'"


def _exact_template(question: str, identifiers: list[str]) -> GeneratedSQL | None:
    text = question.lower()
    for identifier in identifiers:
        value = _literal(identifier)
        if identifier.startswith("PO-"):
            return GeneratedSQL(
                sql=f"SELECT * FROM analytics.purchase_order_lines WHERE upper(po_id) = {value}",
                reason="Trusted exact purchase-order lookup.",
                source="template:purchase_order_exact",
            )
        if identifier.startswith(("CON-", "CONTRACT-")):
            return GeneratedSQL(
                sql=f"SELECT * FROM analytics.contract_terms WHERE upper(contract_id) = {value}",
                reason="Trusted exact contract and clause lookup.",
                source="template:contract_exact",
            )
        if re.fullmatch(r"[A-Z]{4}\d{7}", identifier):
            return GeneratedSQL(
                sql=f"SELECT * FROM analytics.container_status WHERE upper(container_number) = {value}",
                reason="Trusted exact container lookup.",
                source="template:container_exact",
            )
        if identifier.startswith(("SHIP-", "SHP-")):
            return GeneratedSQL(
                sql=f"SELECT * FROM analytics.shipment_operations WHERE upper(shipment_id) = {value}",
                reason="Trusted exact shipment lookup.",
                source="template:shipment_exact",
            )
        if any(character.isdigit() for character in identifier) and (
            "product" in text or "inventory" in text or "stock" in text
        ):
            return GeneratedSQL(
                sql=f"SELECT * FROM analytics.current_inventory WHERE upper(source_product_id) = {value}",
                reason="Trusted exact product-inventory lookup.",
                source="template:inventory_product_exact",
            )
    return None


def template_sql(question: str) -> GeneratedSQL | None:
    """Match common operational intents to inspectable analytics-view queries."""
    text = " ".join(question.lower().split())
    exact = _exact_template(question, extract_identifiers(question))
    if exact:
        return exact

    semantic_plan = local_query_plan(question)
    if semantic_plan:
        return GeneratedSQL(
            sql=compile_query_plan(semantic_plan),
            reason="Locally planned from the constrained supply-chain semantic catalog.",
            source="plan:local",
            metadata=plan_metadata(semantic_plan),
        )

    if "high risk" in text and any(term in text for term in ("supplier", "vendor")):
        return GeneratedSQL(
            sql=(
                "SELECT * FROM analytics.supplier_performance "
                "WHERE upper(coalesce(risk_tier, '')) = 'HIGH' "
                "ORDER BY supplier_name"
            ),
            reason="Trusted high-risk supplier lookup.",
            source="template:high_risk_suppliers",
        )

    if (
        "purchase order value" in text
        and "received quantity" in text
        and any(term in text for term in ("supplier", "vendor"))
    ):
        return GeneratedSQL(
            sql=(
                "SELECT supplier_id, supplier_name, SUM(line_value) AS purchase_order_value, "
                "SUM(received_quantity) AS received_quantity "
                "FROM analytics.purchase_order_lines "
                "GROUP BY supplier_id, supplier_name "
                "ORDER BY purchase_order_value DESC NULLS LAST, supplier_name"
            ),
            reason="Trusted purchase-order value and receipt summary by supplier.",
            source="template:supplier_po_receipts",
        )

    if "late delivery" in text and any(term in text for term in ("credit", "clause", "penalt")):
        return GeneratedSQL(
            sql=(
                "SELECT contract_id, supplier_id, supplier_name, carrier_name, clause_id, "
                "clause_type, clause_title, clause_text, structured_terms, data_as_of "
                "FROM analytics.contract_terms "
                "WHERE lower(coalesce(clause_type, '')) LIKE '%late%' "
                "OR lower(coalesce(clause_title, '')) LIKE '%late%' "
                "OR lower(coalesce(clause_text, '')) LIKE '%late delivery%' "
                "OR lower(coalesce(clause_text, '')) LIKE '%credit%' "
                "ORDER BY contract_id, clause_id"
            ),
            reason="Trusted late-delivery contract-term lookup.",
            source="template:late_delivery_terms",
        )

    if (
        "late shipment" in text
        or "delayed shipment" in text
        or ("shipment" in text and "arrived late" in text)
    ):
        if "supplier" in text or "vendor" in text:
            return GeneratedSQL(
                sql=(
                    "SELECT supplier_id, supplier_name, risk_tier, shipment_count, "
                    "late_shipment_count, on_time_percentage, open_purchase_order_count, data_as_of "
                    "FROM analytics.supplier_performance WHERE late_shipment_count > 0 "
                    "ORDER BY late_shipment_count DESC, supplier_name"
                ),
                reason="Trusted supplier late-shipment summary.",
                source="template:supplier_late_shipments",
            )
        return GeneratedSQL(
            sql=(
                "SELECT * FROM analytics.shipment_operations WHERE is_late IS TRUE "
                "ORDER BY arrival_variance_days DESC NULLS LAST, shipment_id"
            ),
            reason="Trusted late-shipment detail lookup.",
            source="template:late_shipments",
        )

    if "carrier performance" in text and "late" in text:
        return GeneratedSQL(
            sql=(
                "SELECT carrier_name, COUNT(*) AS shipment_count, "
                "COUNT(*) FILTER (WHERE is_late IS TRUE) AS late_delivery_count, "
                "AVG(arrival_variance_days) FILTER (WHERE is_late IS TRUE) "
                "AS average_late_arrival_variance_days "
                "FROM analytics.shipment_operations "
                "GROUP BY carrier_name "
                "ORDER BY late_delivery_count DESC, carrier_name"
            ),
            reason="Trusted late-delivery carrier summary.",
            source="template:carrier_late_performance",
        )

    if "container" in text and any(term in text for term in ("current status", "status of")):
        return GeneratedSQL(
            sql=(
                "SELECT * FROM analytics.container_status "
                "ORDER BY delivered_flag, last_tracked_at DESC NULLS LAST, container_number"
            ),
            reason="Trusted current container-status lookup.",
            source="template:container_status",
        )

    if "arrival variance" in text and any(term in text for term in ("route", "routes")):
        return GeneratedSQL(
            sql=(
                "SELECT origin_city, destination_city, "
                "AVG(arrival_variance_days) AS average_arrival_variance_days, "
                "MAX(arrival_variance_days) AS maximum_arrival_variance_days, "
                "COUNT(*) AS shipment_count "
                "FROM analytics.shipment_operations "
                "WHERE arrival_variance_days IS NOT NULL "
                "GROUP BY origin_city, destination_city "
                "ORDER BY average_arrival_variance_days DESC NULLS LAST, "
                "origin_city, destination_city"
            ),
            reason="Trusted arrival-variance summary by route.",
            source="template:route_arrival_variance",
        )

    if any(term in text for term in ("supplier performance", "supplier scorecard", "vendor scorecard")):
        return GeneratedSQL(
            sql=(
                "SELECT * FROM analytics.supplier_performance "
                "ORDER BY risk_tier DESC, on_time_percentage ASC NULLS FIRST, supplier_name"
            ),
            reason="Trusted supplier-performance scorecard.",
            source="template:supplier_performance",
        )

    if "open purchase order" in text or "open po" in text:
        return GeneratedSQL(
            sql=(
                "SELECT * FROM analytics.purchase_order_lines "
                "WHERE upper(coalesce(status, '')) NOT IN ('CLOSED', 'COMPLETED', 'CANCELLED') "
                "ORDER BY required_date NULLS LAST, po_id, po_line_id"
            ),
            reason="Trusted open purchase-order lookup.",
            source="template:open_purchase_orders",
        )

    if "goods received" in text and "ordered" in text:
        return GeneratedSQL(
            sql=(
                "SELECT po_id, supplier_id, supplier_name, source_product_id, product_name, "
                "ordered_quantity, received_quantity, rejected_quantity, open_quantity, data_as_of "
                "FROM analytics.purchase_order_lines "
                "ORDER BY open_quantity DESC NULLS LAST, po_id, po_line_id"
            ),
            reason="Trusted ordered-versus-received purchase-order lookup.",
            source="template:ordered_received_quantities",
        )

    if "partially received" in text or "partial receipt" in text:
        return GeneratedSQL(
            sql=(
                "SELECT * FROM analytics.purchase_order_lines "
                "WHERE received_quantity > 0 AND open_quantity > 0 "
                "ORDER BY open_quantity DESC NULLS LAST, po_id, po_line_id"
            ),
            reason="Trusted partially received purchase-order-line lookup.",
            source="template:partially_received_po_lines",
        )

    if any(
        term in text
        for term in ("current inventory", "inventory level", "stock level", "on-hand quantity")
    ):
        return GeneratedSQL(
            sql=(
                "SELECT * FROM analytics.current_inventory "
                "ORDER BY quantity_on_hand ASC NULLS FIRST, facility_name, product_name"
            ),
            reason="Trusted current-inventory lookup.",
            source="template:current_inventory",
        )
    return None


def generate_sql(question: str, *, provider_deadline: float | None = None) -> GeneratedSQL:
    plan = plan_with_gemini(
        question,
        provider_deadline=provider_deadline,
    )
    return GeneratedSQL(
        sql=validate_and_rewrite_sql(compile_query_plan(plan)),
        reason=plan.reason or "Gemini selected a constrained semantic query plan.",
        source="plan:gemini",
        metadata=plan_metadata(plan),
    )


def answer_with_sql(
    question: str,
    *,
    provider_deadline: float | None = None,
) -> tuple[GeneratedSQL, SQLResult]:
    generated = template_sql(question) or generate_sql(
        question,
        provider_deadline=provider_deadline,
    )
    return generated, execute_readonly_sql(
        generated.sql,
        include_total=bool(generated.metadata),
        result_metadata=generated.metadata,
    )
