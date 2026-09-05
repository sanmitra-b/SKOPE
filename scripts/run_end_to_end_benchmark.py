"""Run SKOPE's real pipeline and retain answers, SQL, quality checks, and latency.

This benchmark measures deterministic properties for which the curated question
set contains ground truth: routing, tool selection, expected document types,
and expected SQL views. It also records SQL execution, supported-claim and
citation checks, response completion, and timings. It deliberately does not
call these checks semantic answer correctness because the current benchmark
does not contain reference answers or expected SQL result rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.agent_reasoning import NO_SUPPORTED_ANSWER
from backend.app.config import get_settings
from backend.app.orchestrator import route_query, run_orchestrator
from backend.app.retrieval import prewarm_models


def _rate(values: Iterable[bool]) -> float | None:
    observed = list(values)
    return round(sum(observed) / len(observed), 4) if observed else None


def _percentile(values: list[int], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _latency_summary(values: list[int]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "minimum_ms": min(values) if values else None,
        "median_ms": round(_percentile(values, 0.50) or 0, 1) if values else None,
        "average_ms": round(sum(values) / len(values), 1) if values else None,
        "p95_ms": round(_percentile(values, 0.95) or 0, 1) if values else None,
        "maximum_ms": max(values) if values else None,
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _evaluate_question(item: dict[str, Any], *, top_k: int, collection: str) -> dict[str, Any]:
    question = item["question"]
    expected_agents = set(item["expected_agents"])
    expected_types = set(item.get("expected_document_types", []))
    expected_view = item.get("expected_sql_view")

    route = route_query(question)
    route_pass = expected_agents.issubset(set(route.agents))
    tool_route_pass = (
        route.requires_documents == item["requires_documents"]
        and route.requires_sql == item["requires_sql"]
    )

    started = time.perf_counter()
    state = run_orchestrator(question, top_k=top_k, collection=collection)
    measured_latency_ms = int((time.perf_counter() - started) * 1_000)

    documents = list(state.get("document_evidence", []))
    sql_evidence = list(state.get("sql_evidence", []))
    all_evidence = [*documents, *sql_evidence]
    claims = list(state.get("verified_claims", []))
    answer = str(state.get("answer", "")).strip()
    warnings = [str(value) for value in state.get("warnings", [])]
    timings = dict(state.get("timings_ms", {}))
    provider_metrics = list(state.get("provider_metrics", []))

    returned_types = {
        str(evidence.metadata.get("document_type", ""))
        for evidence in documents
        if evidence.metadata.get("document_type")
    }
    matched_types = expected_types.intersection(returned_types)
    retrieval_type_recall = (
        round(len(matched_types) / len(expected_types), 4) if expected_types else None
    )
    retrieval_pass = bool(matched_types) if expected_types else None

    executed_sql = "\n\n".join(evidence.sql or "" for evidence in sql_evidence).strip()
    sql_execution_pass = bool(sql_evidence) if item["requires_sql"] else None
    sql_view_pass = (
        f"analytics.{expected_view}" in executed_sql.lower() if expected_view else None
    )
    sql_sources = sorted(
        {
            str(evidence.metadata.get("sql_source"))
            for evidence in sql_evidence
            if evidence.metadata.get("sql_source")
        }
    )
    sql_row_count = sum(len(evidence.rows or []) for evidence in sql_evidence)

    evidence_ids = {evidence.evidence_id for evidence in all_evidence}
    supported_claims = [claim for claim in claims if claim.supported]
    citation_integrity_pass = bool(supported_claims) and all(
        claim.evidence_ids
        and all(evidence_id in evidence_ids for evidence_id in claim.evidence_ids)
        for claim in supported_claims
    )
    claim_support_rate = round(len(supported_claims) / len(claims), 4) if claims else 0.0
    answer_success = bool(answer) and answer != NO_SUPPORTED_ANSWER and bool(supported_claims)

    required_checks = [route_pass, tool_route_pass, answer_success, citation_integrity_pass]
    if expected_types:
        required_checks.append(bool(retrieval_pass))
    if item["requires_sql"]:
        required_checks.append(bool(sql_execution_pass))
    if expected_view:
        required_checks.append(bool(sql_view_pass))
    operational_pass = all(required_checks)

    return {
        "id": item["id"],
        "question": question,
        "answer": answer,
        "expected_agents": sorted(expected_agents),
        "actual_agents": route.agents,
        "route_pass": route_pass,
        "expected_requires_documents": item["requires_documents"],
        "actual_requires_documents": route.requires_documents,
        "expected_requires_sql": item["requires_sql"],
        "actual_requires_sql": route.requires_sql,
        "tool_route_pass": tool_route_pass,
        "expected_document_types": sorted(expected_types),
        "returned_document_types": sorted(returned_types),
        "matched_document_types": sorted(matched_types),
        "retrieval_pass": retrieval_pass,
        "retrieval_type_recall": retrieval_type_recall,
        "document_evidence_count": len(documents),
        "document_evidence_ids": [evidence.evidence_id for evidence in documents],
        "expected_sql_view": expected_view,
        "sql_execution_pass": sql_execution_pass,
        "sql_view_pass": sql_view_pass,
        "sql_sources": sql_sources,
        "sql": executed_sql,
        "sql_row_count": sql_row_count,
        "sql_evidence_ids": [evidence.evidence_id for evidence in sql_evidence],
        "claim_count": len(claims),
        "supported_claim_count": len(supported_claims),
        "claim_support_rate": claim_support_rate,
        "citation_integrity_pass": citation_integrity_pass,
        "answer_success": answer_success,
        "operational_pass": operational_pass,
        "claims": [claim.model_dump() for claim in claims],
        "warnings": warnings,
        "timings_ms": timings,
        "provider_metrics": provider_metrics,
        "latency_ms": int(timings.get("orchestrator_total", measured_latency_ms)),
        "wall_clock_latency_ms": measured_latency_ms,
        "error": None,
    }


def _failed_result(item: dict[str, Any], exc: Exception, elapsed_ms: int) -> dict[str, Any]:
    return {
        "id": item["id"],
        "question": item["question"],
        "answer": "",
        "expected_agents": item["expected_agents"],
        "actual_agents": [],
        "route_pass": False,
        "expected_requires_documents": item["requires_documents"],
        "actual_requires_documents": None,
        "expected_requires_sql": item["requires_sql"],
        "actual_requires_sql": None,
        "tool_route_pass": False,
        "expected_document_types": item.get("expected_document_types", []),
        "returned_document_types": [],
        "matched_document_types": [],
        "retrieval_pass": False if item.get("expected_document_types") else None,
        "retrieval_type_recall": 0.0 if item.get("expected_document_types") else None,
        "document_evidence_count": 0,
        "document_evidence_ids": [],
        "expected_sql_view": item.get("expected_sql_view"),
        "sql_execution_pass": False if item["requires_sql"] else None,
        "sql_view_pass": False if item.get("expected_sql_view") else None,
        "sql_sources": [],
        "sql": "",
        "sql_row_count": 0,
        "sql_evidence_ids": [],
        "claim_count": 0,
        "supported_claim_count": 0,
        "claim_support_rate": 0.0,
        "citation_integrity_pass": False,
        "answer_success": False,
        "operational_pass": False,
        "claims": [],
        "warnings": [],
        "timings_ms": {},
        "provider_metrics": [],
        "latency_ms": elapsed_ms,
        "wall_clock_latency_ms": elapsed_ms,
        "error": f"{type(exc).__name__}: {exc}",
    }


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    supported = sum(result["supported_claim_count"] for result in results)
    proposed = sum(result["claim_count"] for result in results)
    return {
        "agent_route_accuracy": _rate(result["route_pass"] for result in results),
        "tool_route_accuracy": _rate(result["tool_route_pass"] for result in results),
        "retrieval_type_hit_rate_at_k": _rate(
            result["retrieval_pass"]
            for result in results
            if result["retrieval_pass"] is not None
        ),
        "mean_retrieval_type_recall_at_k": round(
            sum(result["retrieval_type_recall"] for result in results if result["retrieval_type_recall"] is not None)
            / sum(result["retrieval_type_recall"] is not None for result in results),
            4,
        ) if any(result["retrieval_type_recall"] is not None for result in results) else None,
        "sql_execution_success_rate": _rate(
            result["sql_execution_pass"]
            for result in results
            if result["sql_execution_pass"] is not None
        ),
        "sql_expected_view_accuracy": _rate(
            result["sql_view_pass"]
            for result in results
            if result["sql_view_pass"] is not None
        ),
        "answer_success_rate": _rate(result["answer_success"] for result in results),
        "aggregate_claim_support_rate": round(supported / proposed, 4) if proposed else 0.0,
        "citation_integrity_rate": _rate(
            result["citation_integrity_pass"] for result in results
        ),
        "strict_operational_pass_rate": _rate(
            result["operational_pass"] for result in results
        ),
        "error_rate": _rate(bool(result["error"]) for result in results),
        "latency": _latency_summary([result["latency_ms"] for result in results]),
        "latency_sql_questions": _latency_summary(
            [
                result["latency_ms"]
                for result in results
                if result["expected_requires_sql"]
            ]
        ),
        "latency_document_only_questions": _latency_summary(
            [
                result["latency_ms"]
                for result in results
                if not result["expected_requires_sql"]
            ]
        ),
    }


def _write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    columns = [
        "id", "question", "answer", "operational_pass", "answer_success",
        "route_pass", "tool_route_pass", "expected_agents", "actual_agents",
        "expected_requires_documents", "actual_requires_documents",
        "expected_requires_sql", "actual_requires_sql",
        "expected_document_types", "returned_document_types", "matched_document_types",
        "retrieval_pass", "retrieval_type_recall", "document_evidence_count",
        "document_evidence_ids", "expected_sql_view", "sql_execution_pass",
        "sql_view_pass", "sql_sources", "sql", "sql_row_count", "sql_evidence_ids",
        "claim_count", "supported_claim_count", "claim_support_rate",
        "citation_integrity_pass", "claims", "warnings", "timings_ms",
        "provider_metrics", "latency_ms", "wall_clock_latency_ms", "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            row = dict(result)
            for field in (
                "expected_agents", "actual_agents", "expected_document_types",
                "returned_document_types", "matched_document_types", "document_evidence_ids",
                "sql_sources", "sql_evidence_ids", "claims", "warnings", "timings_ms",
                "provider_metrics",
            ):
                row[field] = _json(row[field])
            writer.writerow(row)


def run(args: argparse.Namespace) -> dict[str, Any]:
    questions = json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    questions = questions[args.offset : args.offset + args.limit]
    settings = get_settings()
    collection = args.collection or settings.qdrant_collection

    print("Prewarming local embedding and reranker models...", flush=True)
    model_status = prewarm_models() if not args.skip_prewarm else {}
    print(f"Running {len(questions)} questions against {collection}...", flush=True)

    results: list[dict[str, Any]] = []
    benchmark_started = time.perf_counter()
    for number, item in enumerate(questions, start=1):
        started = time.perf_counter()
        try:
            result = _evaluate_question(item, top_k=args.top_k, collection=collection)
        except Exception as exc:
            result = _failed_result(item, exc, int((time.perf_counter() - started) * 1_000))
        results.append(result)
        print(
            f"[{number:02d}/{len(questions):02d}] {result['id']} "
            f"pass={result['operational_pass']} sql={result['sql_execution_pass']} "
            f"answer={result['answer_success']} latency={result['latency_ms']}ms",
            flush=True,
        )
        if args.delay_seconds and number < len(questions):
            time.sleep(args.delay_seconds)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = ROOT / "artifacts" / "evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"end_to_end_{len(results)}_{stamp}.json"
    csv_path = output_dir / f"end_to_end_{len(results)}_{stamp}.csv"
    report = {
        "version": "skope-end-to-end-v1",
        "completed_at": datetime.now(UTC).isoformat(),
        "collection": collection,
        "question_count": len(results),
        "question_offset": args.offset,
        "top_k": args.top_k,
        "delay_seconds": args.delay_seconds,
        "elapsed_seconds": round(time.perf_counter() - benchmark_started, 2),
        "model_status": model_status,
        "metric_scope": {
            "objective_ground_truth": [
                "agent routing", "tool routing", "expected document types", "expected SQL view"
            ],
            "observed_runtime_checks": [
                "SQL execution", "response success", "claim support", "citation integrity", "latency"
            ],
            "not_measured": [
                "semantic answer correctness", "SQL result/denotation accuracy"
            ],
            "reason_not_measured": (
                "evaluation/questions.json has no reference answers or expected SQL result rows."
            ),
        },
        "metrics": _metrics(results),
        "results": results,
    }
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    _write_csv(csv_path, results)
    report["artifacts"] = {"json": str(json_path), "csv": str(csv_path)}
    print(json.dumps({"metrics": report["metrics"], "artifacts": report["artifacts"]}, indent=2), flush=True)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--delay-seconds", type=float, default=2.5)
    parser.add_argument("--skip-prewarm", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
