"""Validate generated SKOPE Phase 2 PDFs and their PostgreSQL index records."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from pypdf import PdfReader

import ingest_to_db as db


BASE = Path(__file__).resolve().parents[1]
DATASET = BASE / "RAG Project Dataset"
DOCS = DATASET / "Documents"
REPORT = BASE / "database" / "phase2_pdf_validation_report.json"
EXPECTED = {"pilot": 32, "full": 13700}
PILOT_LAYOUTS = {
    "INVOICE_QC", "INVOICE_DOMESTIC", "INVOICE_GLOBAL", "PURCHASE_ORDER",
    "HBL", "MANIFEST", "HAWB", "IMPORT_REPORT", "WAREHOUSE_SAFETY_AUDIT",
    "MB51_INVENTORY_REPORT", "WAREHOUSING_AGREEMENT", "VENDOR_SUPPLY_AGREEMENT",
    "CARRIER_MSA", "SHORTAGE_REPORT", "FULL_SOP", "QUICK_REFERENCE_SOP",
}
FULL_LAYOUT_COUNTS = {
    "INVOICE_QC": 4225, "INVOICE_DOMESTIC": 3465, "INVOICE_GLOBAL": 2310,
    "PURCHASE_ORDER": 1600, "HBL": 500, "MANIFEST": 500, "HAWB": 200,
    "IMPORT_REPORT": 200, "WAREHOUSE_SAFETY_AUDIT": 80,
    "MB51_INVENTORY_REPORT": 20, "WAREHOUSING_AGREEMENT": 100,
    "VENDOR_SUPPLY_AGREEMENT": 100, "CARRIER_MSA": 100,
    "SHORTAGE_REPORT": 250, "FULL_SOP": 35, "QUICK_REFERENCE_SOP": 15,
}


def validate(database_url: str, mode: str) -> dict:
    manifest_path = DOCS / f"phase2_pdf_manifest_{mode}.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = payload["documents"]
    failures = []
    page_counts = Counter()
    type_counts = Counter()
    seen_paths = set()

    for item in documents:
        relative = item["path"]
        path = DATASET / relative
        if relative in seen_paths:
            failures.append({"path": relative, "error": "duplicate manifest path"})
            continue
        seen_paths.add(relative)
        try:
            if path.suffix.lower() != ".pdf":
                raise ValueError("generated document is not PDF")
            digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
            if digest != item["sha256"]:
                raise ValueError("SHA-256 mismatch")
            reader = PdfReader(str(path))
            if not reader.pages:
                raise ValueError("PDF has no pages")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            for forbidden_label in ("synthetic", "not legally operative", "demo document"):
                if forbidden_label in text.casefold():
                    raise ValueError(f"visible generation label found: {forbidden_label}")
            if str(item["primary_entity_id"]) not in text:
                raise ValueError("primary entity ID not found in extracted PDF text")
            for forbidden in ("Fresa Demo", "Amazon.com", "Powered By: GoAudits"):
                if forbidden.casefold() in text.casefold():
                    raise ValueError(f"legacy branding found: {forbidden}")
            if reader.metadata and "not legally operative" not in str(reader.metadata.subject or "").casefold():
                raise ValueError("PDF metadata disclosure missing")
            page_counts[item["document_type"]] += len(reader.pages)
            type_counts[item["document_type"]] += 1
        except Exception as exc:
            failures.append({"path": relative, "error": str(exc)})

    with db.psycopg.connect(database_url) as conn:
        indexed = conn.execute("""
            SELECT count(*) FROM skope.document_index
            WHERE metadata->>'phase'='PHASE2' AND metadata->>'mode'=%s
        """, (mode,)).fetchone()[0]
        mismatched_index = conn.execute("""
            SELECT count(*) FROM skope.document_index
            WHERE metadata->>'phase'='PHASE2' AND metadata->>'mode'=%s
              AND (NOT is_synthetic OR NOT rag_index_allowed OR sha256 IS NULL)
        """, (mode,)).fetchone()[0]

    stray_txt = list(DOCS.glob("**/generated/**/*.txt"))
    checks = [
        {"name": "manifest count", "passed": len(documents) == EXPECTED[mode], "actual": len(documents), "expected": EXPECTED[mode]},
        {"name": "unique manifest paths", "passed": len(seen_paths) == len(documents), "actual": len(seen_paths), "expected": len(documents)},
        {"name": "PDF integrity/content checks", "passed": not failures, "actual": len(failures), "expected": 0},
        {"name": "PostgreSQL document index count", "passed": indexed == len(documents), "actual": indexed, "expected": len(documents)},
        {"name": "index provenance flags", "passed": mismatched_index == 0, "actual": mismatched_index, "expected": 0},
        {"name": "no generated TXT documents", "passed": not stray_txt, "actual": len(stray_txt), "expected": 0},
    ]
    if mode == "pilot":
        wrong_layouts = {name: count for name, count in type_counts.items() if name not in PILOT_LAYOUTS or count != 2}
        missing_layouts = sorted(PILOT_LAYOUTS - set(type_counts))
        checks.append({"name": "two PDFs per distinct layout", "passed": not wrong_layouts and not missing_layouts,
                       "actual": {"counts": dict(type_counts), "missing": missing_layouts},
                       "expected": "16 layouts x 2 PDFs"})
    else:
        checks.append({"name": "full layout distribution", "passed": dict(type_counts) == FULL_LAYOUT_COUNTS,
                       "actual": dict(type_counts), "expected": FULL_LAYOUT_COUNTS})
    outcome = {
        "status": "PASSED" if all(c["passed"] for c in checks) else "FAILED",
        "mode": mode, "checks": checks, "type_counts": dict(type_counts),
        "page_counts": dict(page_counts), "failures": failures[:100],
    }
    REPORT.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    for check in checks:
        print(f"[{'PASS' if check['passed'] else 'FAIL'}] {check['name']}: {check['actual']} (expected {check['expected']})")
    print(json.dumps({"status": outcome["status"], "type_counts": outcome["type_counts"],
                      "page_counts": outcome["page_counts"], "report": str(REPORT)}, indent=2))
    return outcome


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=db.DEFAULT_DATABASE_URL)
    parser.add_argument("--mode", choices=("pilot", "full"), default="pilot")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = validate(args.database_url, args.mode)
    raise SystemExit(0 if result["status"] == "PASSED" else 1)
