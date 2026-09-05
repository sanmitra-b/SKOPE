import pytest

from backend.app.agent_reasoning import synthesize_answer
from backend.app.query_planning import (
    BusinessQueryPlan,
    QueryFilter,
    QuerySort,
    UnsupportedBusinessQuestion,
    compile_query_plan,
    local_query_plan,
    plan_with_gemini,
)
from backend.app.text_to_sql import answer_with_sql, template_sql


OPEN_SUPPLIER_PARAPHRASES = (
    "Tell me the suppliers who have open purchase orders.",
    "Which vendors have outstanding POs?",
    "Who currently has unclosed purchase orders?",
    "List vendors against whom we have pending orders.",
    "How many active POs does each supplier have?",
)


def test_open_supplier_paraphrases_compile_to_one_query_shape():
    sql_statements = set()
    for question in OPEN_SUPPLIER_PARAPHRASES:
        plan = local_query_plan(question)
        assert plan is not None
        assert plan.dataset == "purchase_order_lines"
        assert plan.dimensions == ["supplier_id", "supplier_name"]
        assert plan.measures == ["distinct_purchase_order_count"]
        assert plan.completeness == "all_rows"
        sql_statements.add(compile_query_plan(plan))
    assert len(sql_statements) == 1


def test_open_po_intents_have_different_aggregation_grains():
    suppliers = local_query_plan("Which suppliers have open purchase orders?")
    payments = local_query_plan("What payment terms apply to open purchase orders?")
    products = local_query_plan("List open purchase order quantities by product.")
    assert suppliers and payments and products
    assert suppliers.dimensions == ["supplier_id", "supplier_name"]
    assert "payment_terms" in payments.dimensions
    assert products.dimensions == ["source_product_id", "product_name"]
    assert products.measures == ["sum_open_quantity"]


def test_template_entrypoint_uses_local_plan_not_generic_po_lines():
    generated = template_sql("Tell me the suppliers who have open purchase orders.")
    assert generated is not None
    assert generated.source == "plan:local"
    assert "COUNT(DISTINCT po_id)" in generated.sql
    assert generated.metadata["deterministic_render"] is True


@pytest.mark.parametrize(
    "question",
    (
        "What is the sales for last quarter?",
        "How much revenue did we make in the previous quarter?",
        "Show turnover for the prior quarter.",
    ),
)
def test_last_quarter_sales_is_planned_locally(question):
    plan = local_query_plan(question)
    assert plan is not None
    assert plan.dataset == "sales_orders"
    assert plan.dimensions == ["currency_code"]
    assert "sum_net_sales" in plan.measures
    assert plan.filters[0].operator == "last_complete_quarter"
    sql = compile_query_plan(plan)
    assert "analytics.sales_orders" in sql
    assert "date_trunc('quarter'" in sql
    assert "INTERVAL '3 months'" in sql


def test_supplier_scorecard_keeps_the_broader_performance_template():
    generated = template_sql(
        "Create a supplier scorecard with on-time percentage and open purchase orders."
    )
    assert generated is not None
    assert generated.source == "template:supplier_performance"


def test_plan_rejects_cross_dataset_fields():
    with pytest.raises(ValueError, match="not available"):
        BusinessQueryPlan(
            dataset="current_inventory",
            dimensions=["supplier_name"],
            measures=[],
            filters=[],
            group_by=[],
            sort=[],
            result_shape="detail_table",
            completeness="paginated",
        )


def test_compiler_escapes_catalogued_string_filter_values():
    plan = BusinessQueryPlan(
        dataset="supplier_performance",
        dimensions=["supplier_id", "supplier_name"],
        measures=[],
        filters=[QueryFilter(field="supplier_name", operator="equals", value="O'Brien")],
        group_by=[],
        sort=[QuerySort(field="supplier_name")],
        result_shape="detail_table",
        completeness="paginated",
    )
    assert "O''Brien" in compile_query_plan(plan)


def test_compiler_validates_and_compiles_date_range_filters():
    plan = BusinessQueryPlan(
        dataset="purchase_order_lines",
        dimensions=["po_id", "order_date"],
        measures=[],
        filters=[
            QueryFilter(field="order_date", operator="greater_or_equal", value="2026-01-01")
        ],
        group_by=[],
        sort=[QuerySort(field="order_date")],
        result_shape="detail_table",
        completeness="paginated",
    )
    assert "order_date >= '2026-01-01'" in compile_query_plan(plan)


def test_plan_rejects_numeric_comparison_on_text_field():
    with pytest.raises(ValueError, match="requires a numeric or temporal field"):
        BusinessQueryPlan(
            dataset="supplier_performance",
            dimensions=["supplier_name"],
            measures=[],
            filters=[
                QueryFilter(field="supplier_name", operator="greater_than", value="10")
            ],
            group_by=[],
            sort=[],
            result_shape="detail_table",
            completeness="paginated",
        )


def test_missing_semantic_relationship_is_rejected_before_gemini(monkeypatch):
    monkeypatch.setattr(
        "backend.app.query_planning.generate_json",
        lambda **kwargs: pytest.fail("Gemini should not plan a known unsupported relationship"),
    )
    with pytest.raises(UnsupportedBusinessQuestion, match="does not contain"):
        plan_with_gemini("Find air waybills and their linked purchase orders.")


def test_gemini_selects_a_plan_and_never_returns_sql(monkeypatch):
    monkeypatch.setattr(
        "backend.app.query_planning.generate_json",
        lambda **kwargs: {
            "supported": True,
            "reason": "Use the curated supplier performance view.",
            "dataset": "supplier_performance",
            "dimensions": ["risk_tier"],
            "measures": ["row_count"],
            "filters": [],
            "group_by": ["risk_tier"],
            "sort": [{"field": "row_count", "direction": "descending"}],
            "result_shape": "grouped_table",
            "completeness": "all_rows",
            "title": "Suppliers by risk tier",
        },
    )
    plan = plan_with_gemini("Break down suppliers by tier.")
    sql = compile_query_plan(plan)
    assert "analytics.supplier_performance" in sql
    assert "GROUP BY risk_tier" in sql


def test_open_supplier_result_is_complete_and_rendered_without_gemini():
    generated, result = answer_with_sql(
        "Tell me the suppliers who have open purchase orders."
    )
    assert generated.source == "plan:local"
    assert result.total_rows == 30
    assert len(result.rows) == 30
    assert not result.truncated
    assert sum(row["distinct_purchase_order_count"] for row in result.rows) == 200
    assert result.evidence.metadata["total_rows"] == 30
    assert result.evidence.metadata["render_as_table"] is True

    synthesis = synthesize_answer(
        "Tell me the suppliers who have open purchase orders.",
        ["supplier", "analytics"],
        [result.evidence],
    )
    assert synthesis.provider_metrics == []
    assert synthesis.claims[0].supported
    assert synthesis.answer.startswith("30 suppliers have 200 distinct open purchase orders")


def test_payment_term_result_is_not_mislabeled_as_a_supplier_list():
    _, result = answer_with_sql("What payment terms apply to open purchase orders?")
    synthesis = synthesize_answer(
        "What payment terms apply to open purchase orders?",
        ["supplier", "analytics"],
        [result.evidence],
    )
    assert synthesis.provider_metrics == []
    assert synthesis.answer.startswith("The open-purchase-order payment-term breakdown")
    assert "distinct purchase orders" in synthesis.answer


def test_last_quarter_sales_executes_without_gemini_and_states_its_period():
    generated, result = answer_with_sql("What is the sales for last quarter?")
    assert generated.source == "plan:local"
    assert {row["currency_code"] for row in result.rows} == {"INR", "SOURCE_UNSPECIFIED"}
    assert all(row["sum_net_sales"] > 0 for row in result.rows)
    assert result.evidence.data_as_of == "2026-07-31"

    synthesis = synthesize_answer(
        "What is the sales for last quarter?",
        ["analytics"],
        [result.evidence],
    )
    assert synthesis.provider_metrics == []
    assert "2026 Q2 (2026-04-01 to 2026-06-30)" in synthesis.answer
    assert "Net sales were" in synthesis.answer
    assert "no currency conversion was applied" in synthesis.answer
