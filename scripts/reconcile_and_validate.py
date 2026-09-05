"""Validate the rebuilt SKOPE PostgreSQL database against Phase 1 contracts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]

import psycopg
from psycopg import sql


DEFAULT_DATABASE_URL = "postgresql://skope_admin:skope_pass_secure@localhost:5432/skope_db"
REPORT_PATH = BASE / "database" / "postgres_validation_report.json"

EXPECTED_COUNTS = {
    "skope.dim_date": 943,
    "skope.dim_product": 34581,
    "skope.dim_customer": 54792,
    "skope.fact_order_fulfillment": 415693,
    "skope.fact_delivery": 308083,
    "skope.fact_inventory_event": 65192,
    "skope.fact_freight_order": 9215,
    "skope.fact_container_tracking": 1545,
    "skope.fact_shipment_record": 263821,
    "skope.fact_customer_feedback": 4225,
    "skope.customer_communication": 22876,
    "skope.email_thread": 2500,
    "skope.email_message": 5000,
    "skope.document_index": 2500,
    "skope.sys_rejected_record": 34416,
    "restricted.customer_contact": 54792,
}

PHASE2_TABLES = (
    "dim_supplier", "supplier_product", "purchase_order", "purchase_order_line",
    "shipment", "shipment_container", "goods_receipt", "contract", "contract_clause",
    "warehouse_audit", "audit_finding", "customs_entry", "enterprise_policy",
)


def scalar(conn: psycopg.Connection, statement: str, params: tuple = ()):
    return conn.execute(statement, params).fetchone()[0]


def table_count(conn: psycopg.Connection, qualified_name: str) -> int:
    schema_name, table_name = qualified_name.split(".", 1)
    statement = sql.SQL("SELECT count(*) FROM {}.{}").format(
        sql.Identifier(schema_name), sql.Identifier(table_name)
    )
    return conn.execute(statement).fetchone()[0]


def validate(database_url: str) -> dict:
    checks: list[dict] = []

    def record(name: str, passed: bool, actual=None, expected=None) -> None:
        item = {"name": name, "passed": bool(passed)}
        if actual is not None:
            item["actual"] = actual
        if expected is not None:
            item["expected"] = expected
        checks.append(item)
        suffix = f" (actual={actual}, expected={expected})" if expected is not None else ""
        print(f"[{'PASS' if passed else 'FAIL'}] {name}{suffix}")

    with psycopg.connect(database_url) as conn:
        for table, expected in EXPECTED_COUNTS.items():
            actual = table_count(conn, table)
            record(f"row count: {table}", actual == expected, actual, expected)

        for table in PHASE2_TABLES:
            actual = table_count(conn, f"skope.{table}")
            record(f"Phase 2 remains unloaded: skope.{table}", actual == 0, actual, 0)

        dataco_rows = scalar(conn, "SELECT count(*) FROM skope.fact_order_fulfillment WHERE source_system='DataCo'")
        dataco_lines = scalar(conn, "SELECT count(DISTINCT source_line_id) FROM skope.fact_order_fulfillment WHERE source_system='DataCo'")
        record("DataCo grain uses unique Order Item Id", dataco_rows == dataco_lines == 161468,
               {"rows": dataco_rows, "distinct_source_line_ids": dataco_lines},
               {"rows": 161468, "distinct_source_line_ids": 161468})

        duplicate_source_lines = scalar(conn, """
            SELECT count(*) FROM (
              SELECT source_system, source_line_id FROM skope.fact_order_fulfillment
              GROUP BY source_system, source_line_id HAVING count(*) > 1
            ) duplicate_lines
        """)
        record("no duplicate canonical order-line identifiers", duplicate_source_lines == 0, duplicate_source_lines, 0)

        fabricated_ids = scalar(
            conn,
            "SELECT count(*) FROM skope.fact_order_fulfillment WHERE display_order_id LIKE %s",
            ("GLO-%",),
        )
        record("no legacy GLO identifiers", fabricated_ids == 0, fabricated_ids, 0)

        linked_containers = scalar(conn, "SELECT count(*) FROM skope.fact_container_tracking WHERE linked_order_id IS NOT NULL")
        synthetic_containers = scalar(conn, "SELECT count(*) FROM skope.fact_container_tracking WHERE is_synthetic")
        record("containers are not linked without source evidence", linked_containers == 0, linked_containers, 0)
        record("container facts retain source provenance", synthetic_containers == 0, synthetic_containers, 0)

        pii_columns = scalar(conn, """
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema='skope' AND table_name='dim_customer'
              AND column_name IN ('email','phone','street_address','postal_code')
        """)
        record("operational PII is absent from RAG-facing dim_customer", pii_columns == 0, pii_columns, 0)

        rag_enabled_contacts = scalar(conn, "SELECT count(*) FROM restricted.customer_contact WHERE rag_index_allowed")
        record("restricted customer contacts are excluded from RAG", rag_enabled_contacts == 0, rag_enabled_contacts, 0)

        rag_restricted_privileges = scalar(conn, """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee='skope_rag_reader' AND table_schema='restricted'
        """)
        rag_write_privileges = scalar(conn, """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee='skope_rag_reader' AND table_schema='skope'
              AND privilege_type <> 'SELECT'
        """)
        record("RAG reader has no restricted-schema privileges", rag_restricted_privileges == 0,
               rag_restricted_privileges, 0)
        record("RAG reader is read-only", rag_write_privileges == 0, rag_write_privileges, 0)
        rag_can_read = scalar(conn, "SELECT has_table_privilege('skope_rag_reader', 'skope.fact_order_fulfillment', 'SELECT')")
        pii_can_read = scalar(conn, "SELECT has_table_privilege('skope_pii_operator', 'restricted.customer_contact', 'SELECT')")
        record("RAG reader can query canonical facts", rag_can_read is True, rag_can_read, True)
        record("PII operator can query restricted contacts", pii_can_read is True, pii_can_read, True)

        contact_gaps = scalar(conn, """
            SELECT count(*) FROM skope.dim_customer c
            LEFT JOIN restricted.customer_contact p ON p.contact_id=c.contact_id
            WHERE c.contact_id IS NOT NULL AND p.contact_id IS NULL
        """)
        record("dim_customer contact references resolve in restricted schema", contact_gaps == 0, contact_gaps, 0)

        invalid_email_threads = scalar(conn, """
            SELECT count(*) FROM skope.email_thread t
            LEFT JOIN (SELECT thread_id, count(*) AS messages FROM skope.email_message GROUP BY thread_id) m USING (thread_id)
            WHERE coalesce(m.messages, 0) <> 2
               OR jsonb_array_length(t.source_records) = 0
               OR t.primary_entity_id IS NULL
        """)
        record("email threads have two messages and source-backed metadata", invalid_email_threads == 0,
               invalid_email_threads, 0)

        email_domains = scalar(conn, "SELECT count(DISTINCT business_domain) FROM skope.email_thread")
        email_sources = scalar(conn, "SELECT count(DISTINCT source_system) FROM skope.email_thread")
        record("email corpus spans multiple business domains", email_domains >= 4, email_domains, ">=4")
        record("email corpus spans multiple source systems", email_sources >= 4, email_sources, ">=4")

        manifest_missing = scalar(conn, """
            SELECT count(*) FROM skope.email_thread t
            LEFT JOIN skope.source_file_manifest f ON f.source_path=t.source_path
            WHERE f.source_path IS NULL
        """)
        record("every loaded email is present in source manifest", manifest_missing == 0, manifest_missing, 0)

        future_events = scalar(conn, """
            SELECT
              (SELECT count(*) FROM skope.fact_order_fulfillment WHERE order_date > DATE '2026-07-31') +
              (SELECT count(*) FROM skope.fact_inventory_event WHERE event_date > DATE '2026-07-31') +
              (SELECT count(*) FROM skope.fact_freight_order WHERE order_date > DATE '2026-07-31') +
              (SELECT count(*) FROM skope.fact_shipment_record WHERE shipment_date > DATE '2026-07-31') +
              (SELECT count(*) FROM skope.fact_customer_feedback WHERE feedback_date > DATE '2026-07-31')
        """)
        record("no occurred Phase 1 facts exceed dataset cutoff", future_events == 0, future_events, 0)

        latest_etl = conn.execute("""
            SELECT status, dataset_version, transformation_version
            FROM skope.etl_run ORDER BY run_id DESC LIMIT 1
        """).fetchone()
        actual_etl = list(latest_etl) if latest_etl else None
        expected_etl = ["SUCCEEDED", "phase1-v3", "3.0.0-db"]
        record("latest ETL run succeeded", actual_etl == expected_etl, actual_etl, expected_etl)

    passed = all(item["passed"] for item in checks)
    report = {"status": "PASSED" if passed else "FAILED", "checks": checks}
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nValidation {report['status']}; report: {REPORT_PATH}")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    return parser.parse_args()


if __name__ == "__main__":
    outcome = validate(parse_args().database_url)
    raise SystemExit(0 if outcome["status"] == "PASSED" else 1)
