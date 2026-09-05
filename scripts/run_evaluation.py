"""Run deterministic routing and retrieval evaluation for the SKOPE corpus."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.db import connection
from backend.app.orchestrator import route_query
from backend.app.retrieval import retrieve
from backend.app.text_to_sql import generate_sql
from psycopg.types.json import Jsonb


def run(args: argparse.Namespace) -> dict[str, Any]:
    questions = json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    if args.limit:
        questions = questions[: args.limit]
    settings = get_settings()
    collection = args.collection or settings.qdrant_collection
    results: list[dict[str, Any]] = []
    started = time.perf_counter()

    for number, item in enumerate(questions, start=1):
        result: dict[str, Any] = {"id": item["id"], "question": item["question"], "errors": []}
        route = route_query(item["question"])
        expected_agents = set(item["expected_agents"])
        result["actual_agents"] = route.agents
        result["actual_requires_documents"] = route.requires_documents
        result["actual_requires_sql"] = route.requires_sql
        result["route_pass"] = expected_agents.issubset(set(route.agents))
        result["tool_route_pass"] = (
            route.requires_documents == item["requires_documents"]
            and route.requires_sql == item["requires_sql"]
        )

        expected_types = set(item.get("expected_document_types", []))
        if expected_types and not args.skip_retrieval:
            try:
                evidence = retrieve(item["question"], top_k=args.top_k, collection=collection)
                returned_types = {
                    str(value.metadata.get("document_type", ""))
                    for value in evidence
                    if value.evidence_type == "document"
                }
                result["retrieval_pass"] = bool(expected_types.intersection(returned_types))
                result["returned_document_types"] = sorted(returned_types)
                result["evidence_ids"] = [value.evidence_id for value in evidence]
            except Exception as exc:
                result["retrieval_pass"] = False
                result["errors"].append(f"retrieval: {exc}")

        expected_view = item.get("expected_sql_view")
        if expected_view and args.include_sql:
            try:
                generated = generate_sql(item["question"])
                result["sql_pass"] = f"analytics.{expected_view}" in generated.sql.lower()
                result["sql"] = generated.sql
            except Exception as exc:
                result["sql_pass"] = False
                result["errors"].append(f"sql: {exc}")
            time.sleep(1.2)

        results.append(result)
        print(f"Evaluated {number}/{len(questions)}: {item['id']}")

    def rate(field: str) -> float | None:
        values = [bool(item[field]) for item in results if field in item]
        return round(sum(values) / len(values), 4) if values else None

    report = {
        "version": "skope-eval-v1",
        "completed_at": datetime.now(UTC).isoformat(),
        "collection": collection,
        "question_count": len(results),
        "top_k": args.top_k,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "metrics": {
            "agent_route_accuracy": rate("route_pass"),
            "tool_route_accuracy": rate("tool_route_pass"),
            "retrieval_type_hit_rate_at_k": rate("retrieval_pass"),
            "sql_view_accuracy": rate("sql_pass"),
        },
        "results": results,
    }
    output_dir = ROOT / "artifacts" / "evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"evaluation_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO app.evaluation_run(
                evaluation_run_id, version, status, metrics, configuration, completed_at
            ) VALUES (%s, %s, 'SUCCEEDED', %s, %s, CURRENT_TIMESTAMP)
            """,
            (
                uuid4(),
                report["version"],
                Jsonb(report["metrics"]),
                Jsonb(
                    {
                        "collection": collection,
                        "question_count": len(results),
                        "top_k": args.top_k,
                        "skip_retrieval": args.skip_retrieval,
                        "include_sql": args.include_sql,
                        "artifact": str(output.relative_to(ROOT)),
                    }
                ),
            ),
        )
    print(json.dumps({"metrics": report["metrics"], "report": str(output)}, indent=2))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--skip-retrieval", action="store_true")
    parser.add_argument("--include-sql", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
