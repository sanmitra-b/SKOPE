"""Guarded read-only SQL execution over curated SKOPE analytics views."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import sqlglot
from psycopg.rows import dict_row
from sqlglot import exp

from .config import get_settings
from .db import connection
from .models import Evidence


ALLOWED_VIEWS = {
    "supplier_performance",
    "shipment_operations",
    "purchase_order_lines",
    "sales_orders",
    "current_inventory",
    "delivery_performance",
    "container_status",
    "contract_terms",
}

FORBIDDEN_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Transaction,
    exp.Merge,
)


class UnsafeSQLError(ValueError):
    pass


@dataclass(frozen=True)
class SQLResult:
    query_id: str
    sql: str
    rows: list[dict[str, Any]]
    total_rows: int
    truncated: bool
    evidence: Evidence


@lru_cache(maxsize=1)
def analytics_schema() -> str:
    """Return a compact live schema description for text-to-SQL prompting."""
    with connection(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'analytics'
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row["table_name"], []).append(f"{row['column_name']} {row['data_type']}")
    return "\n".join(
        f"analytics.{table}({', '.join(columns)})"
        for table, columns in grouped.items()
        if table in ALLOWED_VIEWS
    )


def validate_and_rewrite_sql(sql: str, *, row_limit: int | None = None) -> str:
    settings = get_settings()
    safe_limit = row_limit or settings.sql_row_limit
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise UnsafeSQLError(f"SQL could not be parsed: {exc}") from exc
    if len(statements) != 1 or statements[0] is None:
        raise UnsafeSQLError("Exactly one SQL statement is allowed.")

    parsed = statements[0]
    if not isinstance(parsed, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise UnsafeSQLError("Only SELECT queries are allowed.")
    if any(parsed.find(kind) is not None for kind in FORBIDDEN_EXPRESSIONS):
        raise UnsafeSQLError("Mutating or administrative SQL is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in parsed.find_all(exp.CTE)}
    physical_tables = []
    for table in parsed.find_all(exp.Table):
        name = table.name.lower()
        schema = table.db.lower() if table.db else ""
        if name in cte_names:
            continue
        physical_tables.append(table)
        if schema != "analytics" or name not in ALLOWED_VIEWS:
            raise UnsafeSQLError(
                f"Only curated analytics views are queryable; rejected {table.sql(dialect='postgres')}."
            )
    if not physical_tables:
        raise UnsafeSQLError("A business SQL query must read at least one curated analytics view.")

    existing_limit = parsed.args.get("limit")
    if existing_limit is None:
        parsed = parsed.limit(safe_limit + 1)
    else:
        limit_expression = existing_limit.expression
        if not isinstance(limit_expression, exp.Literal) or not limit_expression.is_int:
            raise UnsafeSQLError("LIMIT must be a fixed integer.")
        if int(limit_expression.this) > safe_limit + 1:
            parsed.set("limit", exp.Limit(expression=exp.Literal.number(safe_limit + 1)))
    return parsed.sql(dialect="postgres")


def execute_readonly_sql(
    sql: str,
    *,
    include_total: bool = False,
    result_metadata: dict[str, Any] | None = None,
) -> SQLResult:
    settings = get_settings()
    safe_sql = validate_and_rewrite_sql(sql)
    query_id = hashlib.sha256(safe_sql.encode("utf-8")).hexdigest()[:16]
    with connection(readonly=True) as conn:
        conn.execute("SELECT set_config('statement_timeout', %s, true)", (f"{settings.sql_timeout_ms}ms",))
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(safe_sql)
            fetched = [dict(row) for row in cursor.fetchall()]
            if include_total:
                count_source = sqlglot.parse_one(safe_sql, read="postgres")
                count_source.set("limit", None)
                count_source.set("offset", None)
                count_source.set("order", None)
                count_sql = (
                    "SELECT COUNT(*) AS total_rows FROM ("
                    + count_source.sql(dialect="postgres")
                    + ") AS skope_complete_result"
                )
                cursor.execute(count_sql)
                total_rows = int(cursor.fetchone()["total_rows"])
            else:
                total_rows = len(fetched)

    truncated = total_rows > settings.sql_row_limit
    rows = fetched[: settings.sql_row_limit]
    data_dates = [
        str(row.get("data_as_of") or row.get("maximum_data_as_of"))
        for row in rows
        if row.get("data_as_of") is not None or row.get("maximum_data_as_of") is not None
    ]
    evidence = Evidence(
        evidence_id=f"SQL-{query_id}",
        evidence_type="sql",
        title="Structured ERP query",
        excerpt=(
            f"Read-only query returned {len(rows)} of {total_rows} result row(s)."
            if truncated
            else f"Read-only query returned {total_rows} result row(s)."
        ),
        query_id=query_id,
        sql=safe_sql,
        rows=rows,
        data_as_of=max(data_dates) if data_dates else None,
        metadata={
            **(result_metadata or {}),
            "truncated": truncated,
            "row_count": len(rows),
            "returned_rows": len(rows),
            "total_rows": total_rows,
            "page": 1,
            "page_size": settings.sql_row_limit,
        },
    )
    return SQLResult(
        query_id=query_id,
        sql=safe_sql,
        rows=rows,
        total_rows=total_rows,
        truncated=truncated,
        evidence=evidence,
    )
