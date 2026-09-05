"""Validate canonical Phase 2 rows before any PDF generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import ingest_to_db as db


BASE = Path(__file__).resolve().parents[1]
REPORT = BASE / "database" / "phase2_data_validation_report.json"
EXPECTED = {
    "dim_supplier": 30,
    "supplier_product": 600,
    "purchase_order": 1600,
    "purchase_order_line": 4800,
    "shipment": 1200,
    "shipment_container": 1200,
    "goods_receipt": 4800,
    "contract": 300,
    "contract_clause": 1500,
    "warehouse_audit": 100,
    "audit_finding": 300,
    "customs_entry": 200,
    "enterprise_policy": 50,
}


def validate(database_url: str) -> dict:
    checks = []

    def check(name: str, passed: bool, actual=None, expected=None):
        item = {"name": name, "passed": bool(passed), "actual": actual, "expected": expected}
        checks.append(item)
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {actual} (expected {expected})")

    with db.psycopg.connect(database_url) as conn:
        for table, expected in EXPECTED.items():
            actual = conn.execute(f"SELECT count(*) FROM skope.{table}").fetchone()[0]
            check(f"row count skope.{table}", actual == expected, actual, expected)

        non_synthetic = conn.execute("""
            SELECT
              (SELECT count(*) FROM skope.dim_supplier WHERE NOT is_synthetic) +
              (SELECT count(*) FROM skope.purchase_order WHERE NOT is_synthetic) +
              (SELECT count(*) FROM skope.shipment WHERE NOT is_synthetic) +
              (SELECT count(*) FROM skope.contract WHERE NOT is_synthetic) +
              (SELECT count(*) FROM skope.warehouse_audit WHERE NOT is_synthetic) +
              (SELECT count(*) FROM skope.enterprise_policy WHERE NOT is_synthetic)
        """).fetchone()[0]
        check("all Phase 2 business entities are disclosed as synthetic", non_synthetic == 0, non_synthetic, 0)

        unsupported_ids = conn.execute("""
            SELECT
              (SELECT count(*) FROM skope.purchase_order WHERE po_id LIKE %s) +
              (SELECT count(*) FROM skope.shipment_container WHERE container_number='FCIU8573349'
                 AND shipment_id IN (SELECT shipment_id FROM skope.shipment WHERE po_id LIKE %s))
        """, ("%GLO%", "%GLO%" )).fetchone()[0]
        check("unsupported GLO crosswalk is absent", unsupported_ids == 0, unsupported_ids, 0)

        broken_shipment_chain = conn.execute("""
            SELECT count(*) FROM skope.shipment s
            LEFT JOIN skope.purchase_order po ON po.po_id=s.po_id
            LEFT JOIN skope.shipment_container sc ON sc.shipment_id=s.shipment_id
            LEFT JOIN skope.fact_container_tracking ct ON ct.container_tracking_key=sc.container_tracking_key
            WHERE po.po_id IS NULL OR sc.shipment_id IS NULL OR ct.container_tracking_key IS NULL
        """).fetchone()[0]
        check("shipment -> PO -> container source chain resolves", broken_shipment_chain == 0, broken_shipment_chain, 0)

        broken_procurement_chain = conn.execute("""
            SELECT count(*) FROM skope.purchase_order_line pol
            LEFT JOIN skope.purchase_order po ON po.po_id=pol.po_id
            LEFT JOIN skope.dim_supplier s ON s.supplier_id=po.supplier_id
            LEFT JOIN skope.dim_product p ON p.product_key=pol.product_key
            LEFT JOIN skope.goods_receipt gr ON gr.po_line_id=pol.po_line_id
            WHERE po.po_id IS NULL OR s.supplier_id IS NULL OR p.product_key IS NULL OR gr.receipt_id IS NULL
        """).fetchone()[0]
        check("PO -> supplier/product -> receipt chain resolves", broken_procurement_chain == 0, broken_procurement_chain, 0)

        broken_customs = conn.execute("""
            SELECT count(*) FROM skope.customs_entry c
            LEFT JOIN skope.shipment s ON s.shipment_id=c.shipment_id
            WHERE s.shipment_id IS NULL OR c.customs_value IS NULL OR c.duty_amount IS NULL
        """).fetchone()[0]
        check("customs entries resolve to valued shipments", broken_customs == 0, broken_customs, 0)

        missing_clauses = conn.execute("""
            SELECT count(*) FROM skope.contract c
            LEFT JOIN (SELECT contract_id,count(*) n FROM skope.contract_clause GROUP BY contract_id) x USING(contract_id)
            WHERE coalesce(x.n,0) <> 5
        """).fetchone()[0]
        check("every contract has five stored clauses", missing_clauses == 0, missing_clauses, 0)

        shortage_lines = conn.execute("""
            SELECT count(*) FROM skope.purchase_order_line pol
            JOIN skope.goods_receipt gr USING(po_line_id)
            WHERE gr.received_quantity + coalesce(gr.rejected_quantity,0) < pol.ordered_quantity
        """).fetchone()[0]
        check("enough recorded shortage lines for 250 reports", shortage_lines >= 250, shortage_lines, ">=250")

        logo_entities = conn.execute("""
            SELECT count(*) FROM skope.dim_partner
            WHERE source_partner_id IN ('V44_3','DHL-AIR-DEMO')
        """).fetchone()[0]
        check("authorized Maersk/DHL demo carrier mappings resolve", logo_entities == 2, logo_entities, 2)

        successful_run = conn.execute("""
            SELECT count(*) FROM skope.etl_run
            WHERE dataset_version='phase2-synthetic-v1' AND status='SUCCEEDED'
        """).fetchone()[0]
        check("Phase 2 ETL recorded a successful run", successful_run >= 1, successful_run, ">=1")

    outcome = {"status": "PASSED" if all(c["passed"] for c in checks) else "FAILED", "checks": checks}
    REPORT.write_text(json.dumps(outcome, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Validation {outcome['status']}; report: {REPORT}")
    return outcome


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=db.DEFAULT_DATABASE_URL)
    return parser.parse_args()


if __name__ == "__main__":
    result = validate(parse_args().database_url)
    raise SystemExit(0 if result["status"] == "PASSED" else 1)
