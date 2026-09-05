import pytest

from backend.app.sql_tool import UnsafeSQLError, validate_and_rewrite_sql


def test_guard_accepts_curated_select_and_adds_limit():
    sql = validate_and_rewrite_sql("SELECT supplier_name FROM analytics.supplier_performance")
    assert "analytics.supplier_performance" in sql
    assert "LIMIT 201" in sql


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM analytics.supplier_performance",
        "SELECT * FROM skope.dim_customer",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM analytics.supplier_performance; SELECT 1",
        "UPDATE skope.purchase_order SET status='CLOSED'",
    ],
)
def test_guard_rejects_unsafe_or_out_of_boundary_sql(sql):
    with pytest.raises(UnsafeSQLError):
        validate_and_rewrite_sql(sql)


def test_guard_accepts_cte_over_curated_view():
    sql = validate_and_rewrite_sql(
        "WITH ranked AS (SELECT supplier_name FROM analytics.supplier_performance) SELECT * FROM ranked"
    )
    assert "WITH ranked" in sql


def test_guard_rejects_tableless_placeholder_query():
    with pytest.raises(UnsafeSQLError, match="must read"):
        validate_and_rewrite_sql("SELECT NULL AS warehouse_safety_controls")
