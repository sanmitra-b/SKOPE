"""Constrained business query plans and deterministic SQL compilation.

Gemini may select only catalogued datasets, fields, measures, filters, and
result shapes. It never writes SQL. Common operational intents are planned
locally so they require no provider call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .llm import generate_json


DATASET_FIELDS: dict[str, tuple[str, ...]] = {
    "supplier_performance": (
        "supplier_id", "supplier_name", "supplier_type", "risk_tier", "country",
        "purchase_order_count", "shipment_count", "late_shipment_count",
        "on_time_percentage", "open_purchase_order_count", "data_as_of",
    ),
    "purchase_order_lines": (
        "po_id", "supplier_id", "supplier_name", "order_date", "required_date",
        "status", "payment_terms", "ship_to_facility", "po_line_id",
        "source_product_id", "product_name", "category", "supplier_sku",
        "ordered_quantity", "unit_price", "currency_code", "line_value",
        "received_quantity", "rejected_quantity", "open_quantity", "data_as_of",
    ),
    "sales_orders": (
        "source_system", "display_order_id", "order_id", "order_date",
        "source_product_id", "product_name", "category", "quantity",
        "gross_sales", "discount_amount", "net_sales", "currency_code",
        "order_status", "data_as_of",
    ),
    "shipment_operations": (
        "shipment_id", "po_id", "supplier_id", "supplier_name", "carrier_name",
        "origin_facility", "origin_city", "destination_facility", "destination_city",
        "planned_departure", "actual_departure", "planned_arrival", "actual_arrival",
        "status", "is_late", "arrival_variance_days", "data_as_of",
    ),
    "current_inventory": (
        "source_product_id", "product_name", "category", "source_location_id",
        "facility_name", "city", "event_date", "quantity_on_hand",
        "quantity_damaged", "data_as_of",
    ),
    "delivery_performance": (
        "display_order_id", "order_id", "delivery_partner", "origin_facility",
        "promised_time", "actual_time", "delivery_time_minutes", "distance_km",
        "delivery_status", "reason_if_delayed", "delivery_mode", "is_late", "data_as_of",
    ),
    "container_status": (
        "container_number", "vessel_name", "freight_forwarder", "linked_order_id",
        "origin_port", "destination_port", "dispatch_date", "port_loading_date",
        "port_discharge_date", "last_tracked_at", "delivered_flag", "delivered_date",
        "data_as_of",
    ),
    "contract_terms": (
        "contract_id", "contract_type", "supplier_id", "supplier_name", "carrier_name",
        "effective_date", "expiry_date", "status", "clause_id", "clause_type",
        "clause_title", "clause_text", "structured_terms", "data_as_of",
    ),
}


@dataclass(frozen=True)
class MeasureDefinition:
    expression: str
    datasets: frozenset[str]


MEASURES: dict[str, MeasureDefinition] = {
    "row_count": MeasureDefinition("COUNT(*)", frozenset(DATASET_FIELDS)),
    "distinct_purchase_order_count": MeasureDefinition(
        "COUNT(DISTINCT po_id)", frozenset({"purchase_order_lines", "shipment_operations"})
    ),
    "sum_ordered_quantity": MeasureDefinition(
        "SUM(ordered_quantity)", frozenset({"purchase_order_lines"})
    ),
    "sum_received_quantity": MeasureDefinition(
        "SUM(received_quantity)", frozenset({"purchase_order_lines"})
    ),
    "sum_open_quantity": MeasureDefinition(
        "SUM(open_quantity)", frozenset({"purchase_order_lines"})
    ),
    "sum_line_value": MeasureDefinition(
        "SUM(line_value)", frozenset({"purchase_order_lines"})
    ),
    "sum_gross_sales": MeasureDefinition(
        "SUM(gross_sales)", frozenset({"sales_orders"})
    ),
    "sum_net_sales": MeasureDefinition(
        "SUM(net_sales)", frozenset({"sales_orders"})
    ),
    "distinct_sales_order_count": MeasureDefinition(
        "COUNT(DISTINCT display_order_id)", frozenset({"sales_orders"})
    ),
    "maximum_data_as_of": MeasureDefinition(
        "MAX(data_as_of)", frozenset({"sales_orders"})
    ),
    "average_on_time_percentage": MeasureDefinition(
        "AVG(on_time_percentage)", frozenset({"supplier_performance"})
    ),
    "late_delivery_count": MeasureDefinition(
        "COUNT(*) FILTER (WHERE is_late IS TRUE)",
        frozenset({"shipment_operations", "delivery_performance"}),
    ),
    "average_arrival_variance_days": MeasureDefinition(
        "AVG(arrival_variance_days)", frozenset({"shipment_operations"})
    ),
    "maximum_arrival_variance_days": MeasureDefinition(
        "MAX(arrival_variance_days)", frozenset({"shipment_operations"})
    ),
    "sum_quantity_on_hand": MeasureDefinition(
        "SUM(quantity_on_hand)", frozenset({"current_inventory"})
    ),
    "sum_quantity_damaged": MeasureDefinition(
        "SUM(quantity_damaged)", frozenset({"current_inventory"})
    ),
}


ALL_FIELDS = tuple(sorted({field for fields in DATASET_FIELDS.values() for field in fields}))
ALL_MEASURES = tuple(sorted(MEASURES))
DATASETS = tuple(DATASET_FIELDS)
RESULT_SHAPES = ("single_value", "complete_list", "grouped_table", "detail_table")
FILTER_OPERATORS = (
    "equals", "not_equals", "contains", "greater_than", "greater_or_equal",
    "less_than", "less_or_equal", "is_true", "is_false", "is_null", "not_null",
    "is_open", "last_complete_quarter",
)

NUMERIC_FIELDS = frozenset({
    "purchase_order_count", "shipment_count", "late_shipment_count",
    "on_time_percentage", "open_purchase_order_count", "ordered_quantity",
    "unit_price", "line_value", "received_quantity", "rejected_quantity",
    "open_quantity", "arrival_variance_days", "delivery_time_minutes",
    "distance_km", "quantity_on_hand", "quantity_damaged", "quantity",
    "gross_sales", "discount_amount", "net_sales",
})
BOOLEAN_FIELDS = frozenset({"is_late", "delivered_flag"})
DATE_FIELDS = frozenset({
    "order_date", "required_date", "planned_departure", "actual_departure",
    "planned_arrival", "actual_arrival", "event_date", "dispatch_date",
    "port_loading_date", "port_discharge_date", "delivered_date",
    "effective_date", "expiry_date",
})
DATETIME_FIELDS = frozenset({"last_tracked_at", "data_as_of"})


def _field_type(field: str) -> str:
    if field in NUMERIC_FIELDS:
        return "numeric"
    if field in BOOLEAN_FIELDS:
        return "boolean"
    if field in DATE_FIELDS:
        return "date"
    if field in DATETIME_FIELDS:
        return "datetime"
    return "text"


class QueryFilter(BaseModel):
    field: str
    operator: Literal[
        "equals", "not_equals", "contains", "greater_than", "greater_or_equal",
        "less_than", "less_or_equal", "is_true", "is_false", "is_null", "not_null",
        "is_open", "last_complete_quarter",
    ]
    value: str = ""


class QuerySort(BaseModel):
    field: str
    direction: Literal["ascending", "descending"] = "ascending"


class BusinessQueryPlan(BaseModel):
    supported: bool = True
    reason: str = ""
    dataset: str
    dimensions: list[str] = Field(default_factory=list, max_length=10)
    measures: list[str] = Field(default_factory=list, max_length=5)
    filters: list[QueryFilter] = Field(default_factory=list, max_length=8)
    group_by: list[str] = Field(default_factory=list, max_length=10)
    sort: list[QuerySort] = Field(default_factory=list, max_length=5)
    result_shape: Literal["single_value", "complete_list", "grouped_table", "detail_table"]
    completeness: Literal["all_rows", "paginated"] = "all_rows"
    title: str = "Structured result"

    @model_validator(mode="after")
    def validate_catalog(self):
        if not self.supported:
            return self
        if self.dataset not in DATASET_FIELDS:
            raise ValueError(f"Unsupported analytics dataset: {self.dataset}")
        fields = set(DATASET_FIELDS[self.dataset])
        invalid_fields = (
            set(self.dimensions)
            | set(self.group_by)
            | {item.field for item in self.filters}
        ) - fields
        if invalid_fields:
            raise ValueError(
                f"Fields are not available in analytics.{self.dataset}: {sorted(invalid_fields)}"
            )
        invalid_measures = [
            name
            for name in self.measures
            if name not in MEASURES or self.dataset not in MEASURES[name].datasets
        ]
        if invalid_measures:
            raise ValueError(
                f"Measures are not available in analytics.{self.dataset}: {invalid_measures}"
            )
        selected = set(self.dimensions) | set(self.measures)
        invalid_sort = {item.field for item in self.sort} - selected
        if invalid_sort:
            raise ValueError(f"Sort fields must be selected: {sorted(invalid_sort)}")
        if self.measures and set(self.group_by) != set(self.dimensions):
            raise ValueError("Aggregate plans must group by exactly their selected dimensions.")
        if not self.measures and self.group_by:
            raise ValueError("Non-aggregate plans cannot specify group_by fields.")
        if not self.dimensions and not self.measures:
            raise ValueError("A supported plan must select at least one dimension or measure.")
        for item in self.filters:
            field_type = _field_type(item.field)
            if item.operator == "is_open" and item.field != "status":
                raise ValueError("The is_open operator is valid only for a status field.")
            if item.operator == "last_complete_quarter" and field_type not in {"date", "datetime"}:
                raise ValueError(
                    f"last_complete_quarter requires a temporal field, not {item.field}."
                )
            if item.operator in {"is_true", "is_false"} and field_type != "boolean":
                raise ValueError(f"{item.operator} requires a boolean field, not {item.field}.")
            if item.operator == "contains" and field_type != "text":
                raise ValueError(f"contains requires a text field, not {item.field}.")
            if (
                item.operator
                in {"greater_than", "greater_or_equal", "less_than", "less_or_equal"}
                and field_type not in {"numeric", "date", "datetime"}
            ):
                raise ValueError(
                    f"{item.operator} requires a numeric or temporal field, not {item.field}."
                )
        return self


class UnsupportedBusinessQuestion(ValueError):
    pass


def _normalized(question: str) -> str:
    return " ".join(question.lower().replace("-", " ").split())


def _mentions_open_orders(text: str) -> bool:
    open_terms = ("open", "outstanding", "unclosed", "active", "pending")
    has_order = bool(re.search(r"\b(?:purchase orders?|pos?|orders?)\b", text))
    return has_order and any(term in text for term in open_terms)


def local_query_plan(question: str) -> BusinessQueryPlan | None:
    """Plan common business concepts across varied wording without Gemini."""
    text = _normalized(question)
    sales_terms = ("sales", "revenue", "turnover")
    previous_quarter_terms = (
        "last quarter",
        "previous quarter",
        "prior quarter",
        "last completed quarter",
    )
    if any(term in text for term in sales_terms) and any(
        term in text for term in previous_quarter_terms
    ):
        return BusinessQueryPlan(
            dataset="sales_orders",
            dimensions=["currency_code"],
            measures=[
                "sum_net_sales",
                "distinct_sales_order_count",
                "maximum_data_as_of",
            ],
            filters=[
                QueryFilter(field="order_date", operator="last_complete_quarter")
            ],
            group_by=["currency_code"],
            sort=[QuerySort(field="currency_code")],
            result_shape="grouped_table",
            completeness="all_rows",
            title="Net sales for the last completed quarter",
        )

    if not _mentions_open_orders(text):
        return None
    if any(term in text for term in ("scorecard", "supplier performance", "on time percentage")):
        # Broader supplier-performance questions need the dedicated scorecard
        # template rather than an open-order-only projection.
        return None

    supplier_terms = ("supplier", "suppliers", "vendor", "vendors", "who")
    if any(term in text for term in ("payment term", "payment terms")):
        return BusinessQueryPlan(
            dataset="purchase_order_lines",
            dimensions=["supplier_id", "supplier_name", "payment_terms"],
            measures=["distinct_purchase_order_count"],
            filters=[QueryFilter(field="status", operator="is_open")],
            group_by=["supplier_id", "supplier_name", "payment_terms"],
            sort=[QuerySort(field="supplier_name")],
            result_shape="grouped_table",
            completeness="all_rows",
            title="Payment terms for open purchase orders",
        )
    if "product" in text and any(term in text for term in ("quantity", "quantities", "amount")):
        return BusinessQueryPlan(
            dataset="purchase_order_lines",
            dimensions=["source_product_id", "product_name"],
            measures=["sum_open_quantity"],
            filters=[QueryFilter(field="status", operator="is_open")],
            group_by=["source_product_id", "product_name"],
            sort=[QuerySort(field="sum_open_quantity", direction="descending")],
            result_shape="grouped_table",
            completeness="paginated",
            title="Open purchase-order quantities by product",
        )
    if any(term in text for term in supplier_terms):
        return BusinessQueryPlan(
            dataset="purchase_order_lines",
            dimensions=["supplier_id", "supplier_name"],
            measures=["distinct_purchase_order_count"],
            filters=[QueryFilter(field="status", operator="is_open")],
            group_by=["supplier_id", "supplier_name"],
            sort=[QuerySort(field="supplier_name")],
            result_shape="complete_list",
            completeness="all_rows",
            title="Suppliers with open purchase orders",
        )
    return BusinessQueryPlan(
        dataset="purchase_order_lines",
        dimensions=["po_id", "supplier_id", "supplier_name", "status", "required_date"],
        measures=[],
        filters=[QueryFilter(field="status", operator="is_open")],
        group_by=[],
        sort=[QuerySort(field="required_date"), QuerySort(field="po_id")],
        result_shape="detail_table",
        completeness="paginated",
        title="Open purchase orders",
    )


PLAN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "reason": {"type": "string"},
        "dataset": {"type": "string", "enum": [*DATASETS, "unsupported"]},
        "dimensions": {
            "type": "array", "maxItems": 10,
            "items": {"type": "string", "enum": list(ALL_FIELDS)},
        },
        "measures": {
            "type": "array", "maxItems": 5,
            "items": {"type": "string", "enum": list(ALL_MEASURES)},
        },
        "filters": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": list(ALL_FIELDS)},
                    "operator": {"type": "string", "enum": list(FILTER_OPERATORS)},
                    "value": {"type": "string"},
                },
                "required": ["field", "operator", "value"],
                "additionalProperties": False,
            },
        },
        "group_by": {
            "type": "array", "maxItems": 10,
            "items": {"type": "string", "enum": list(ALL_FIELDS)},
        },
        "sort": {
            "type": "array", "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": [*ALL_FIELDS, *ALL_MEASURES]},
                    "direction": {"type": "string", "enum": ["ascending", "descending"]},
                },
                "required": ["field", "direction"],
                "additionalProperties": False,
            },
        },
        "result_shape": {"type": "string", "enum": list(RESULT_SHAPES)},
        "completeness": {"type": "string", "enum": ["all_rows", "paginated"]},
        "title": {"type": "string"},
    },
    "required": [
        "supported", "reason", "dataset", "dimensions", "measures", "filters",
        "group_by", "sort", "result_shape", "completeness", "title",
    ],
    "additionalProperties": False,
}


def _catalog_prompt() -> str:
    datasets = "\n".join(
        f"- {name}: {', '.join(fields)}" for name, fields in DATASET_FIELDS.items()
    )
    measures = "\n".join(
        f"- {name}: available on {', '.join(sorted(definition.datasets))}"
        for name, definition in MEASURES.items()
    )
    return f"""DATASETS AND FIELDS:
{datasets}

MEASURES:
{measures}"""


def plan_with_gemini(
    question: str,
    *,
    provider_deadline: float | None = None,
) -> BusinessQueryPlan:
    """Use Gemini only to choose catalogued semantics; it cannot emit SQL."""
    unsupported_phrases = (
        "air waybill", "air waybills", "awb", "warehouse shortage", "warehouse shortages",
        "safety control", "safety controls", "audit finding", "audit findings",
    )
    text = _normalized(question)
    if any(phrase in text for phrase in unsupported_phrases):
        raise UnsupportedBusinessQuestion(
            "The current analytics catalog does not contain the structured relationship required "
            "for this question; document evidence may still answer it."
        )

    payload = generate_json(
        system_prompt=(
            "You are SKOPE's business query planner. Return a constrained semantic plan, never SQL. "
            "Select only catalogued fields and measures. Do not infer business definitions that are "
            "not explicit in the catalog. If the available datasets cannot answer the question, set "
            "supported=false, dataset=unsupported, use empty arrays, and explain why. Aggregate plans "
            "must group by exactly all selected dimensions. A request for all/list/tell me requires "
            "completeness=all_rows; large operational details may be paginated."
        ),
        user_prompt=f"{_catalog_prompt()}\n\nBUSINESS QUESTION:\n{question}",
        temperature=0.0,
        max_output_tokens=1_000,
        response_schema=PLAN_RESPONSE_SCHEMA,
        provider_deadline=provider_deadline,
    )
    if not payload.get("supported") or payload.get("dataset") == "unsupported":
        raise UnsupportedBusinessQuestion(
            str(payload.get("reason") or "The analytics catalog cannot answer this question.")
        )
    return BusinessQueryPlan.model_validate(payload)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _numeric_literal(value: str) -> str:
    try:
        return str(Decimal(value))
    except InvalidOperation as exc:
        raise ValueError(f"Expected a numeric filter value, received {value!r}.") from exc


def _temporal_literal(value: str, field_type: str) -> str:
    try:
        if field_type == "date":
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Expected an ISO {field_type} filter value, received {value!r}.") from exc
    return _sql_string(value)


def _typed_literal(field: str, value: str) -> str:
    field_type = _field_type(field)
    if field_type == "numeric":
        return _numeric_literal(value)
    if field_type in {"date", "datetime"}:
        return _temporal_literal(value, field_type)
    if field_type == "boolean":
        normalized = value.strip().lower()
        if normalized not in {"true", "false"}:
            raise ValueError(f"Expected a boolean filter value, received {value!r}.")
        return normalized.upper()
    return _sql_string(value)


def _compile_filter(item: QueryFilter, dataset: str) -> str:
    field = item.field
    if item.operator == "is_open":
        return f"upper(coalesce({field}, '')) NOT IN ('CLOSED', 'COMPLETED', 'CANCELLED')"
    if item.operator == "last_complete_quarter":
        snapshot = f"(SELECT MAX(data_as_of) FROM analytics.{dataset})"
        quarter_start = f"date_trunc('quarter', {snapshot})"
        return (
            f"{field} >= ({quarter_start} - INTERVAL '3 months')::date "
            f"AND {field} < {quarter_start}::date"
        )
    if item.operator == "is_true":
        return f"{field} IS TRUE"
    if item.operator == "is_false":
        return f"{field} IS FALSE"
    if item.operator == "is_null":
        return f"{field} IS NULL"
    if item.operator == "not_null":
        return f"{field} IS NOT NULL"
    if item.operator == "contains":
        escaped = item.value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"lower(coalesce({field}::text, '')) LIKE lower({_sql_string('%' + escaped + '%')}) ESCAPE '\\'"
    if item.operator in {"greater_than", "greater_or_equal", "less_than", "less_or_equal"}:
        operators = {
            "greater_than": ">", "greater_or_equal": ">=", "less_than": "<", "less_or_equal": "<=",
        }
        return f"{field} {operators[item.operator]} {_typed_literal(field, item.value)}"
    operator = "=" if item.operator == "equals" else "<>"
    return f"{field} {operator} {_typed_literal(field, item.value)}"


def compile_query_plan(plan: BusinessQueryPlan) -> str:
    """Compile a validated semantic plan into one inspectable SELECT statement."""
    if not plan.supported:
        raise UnsupportedBusinessQuestion(plan.reason or "Unsupported business query plan.")
    select_items = list(plan.dimensions)
    select_items.extend(f"{MEASURES[name].expression} AS {name}" for name in plan.measures)
    sql = f"SELECT {', '.join(select_items)} FROM analytics.{plan.dataset}"
    if plan.filters:
        sql += " WHERE " + " AND ".join(
            _compile_filter(item, plan.dataset) for item in plan.filters
        )
    if plan.group_by:
        sql += " GROUP BY " + ", ".join(plan.group_by)
    if plan.sort:
        order = [
            f"{item.field} {'ASC' if item.direction == 'ascending' else 'DESC'} NULLS LAST"
            for item in plan.sort
        ]
        sql += " ORDER BY " + ", ".join(order)
    return sql


def plan_metadata(plan: BusinessQueryPlan) -> dict[str, Any]:
    return {
        "query_plan": plan.model_dump(mode="json"),
        "result_shape": plan.result_shape,
        "completeness": plan.completeness,
        "render_as_table": plan.result_shape in {"complete_list", "grouped_table", "detail_table"},
        "result_title": plan.title,
        "display_columns": [*plan.dimensions, *plan.measures],
        "deterministic_render": True,
    }


__all__ = [
    "BusinessQueryPlan",
    "PLAN_RESPONSE_SCHEMA",
    "QueryFilter",
    "QuerySort",
    "UnsupportedBusinessQuestion",
    "compile_query_plan",
    "local_query_plan",
    "plan_metadata",
    "plan_with_gemini",
]
