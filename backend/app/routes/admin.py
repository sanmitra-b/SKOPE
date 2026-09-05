"""Administrator metrics and flagged-response review endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_roles
from ..db import connection
from ..models import FlagStatusUpdate, UserContext
from ..services.evaluation_metrics import load_benchmark_history


router = APIRouter(prefix="/api/admin")
administrator = require_roles("ADMINISTRATOR")


@router.get("/metrics")
def admin_metrics(_: UserContext = Depends(administrator)) -> dict:
    with connection(readonly=True) as conn:
        totals = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM app.conversation) AS conversations,
              (SELECT count(*) FROM app.message WHERE role = 'assistant') AS answers,
              (SELECT count(*) FROM app.flagged_response WHERE status = 'OPEN') AS open_flags,
              (SELECT round(avg(latency_ms), 0)
                 FROM app.message WHERE role = 'assistant') AS average_latency_ms
            """
        ).fetchone()
        ingestion_runs = conn.execute(
            """
            SELECT run_id, started_at, completed_at, status, source_document_count,
                   chunk_count, failed_document_count, summary
            FROM rag.ingestion_run
            ORDER BY started_at DESC
            LIMIT 10
            """
        ).fetchall()
        evaluation_runs = conn.execute(
            """
            SELECT evaluation_run_id, version, status, metrics, configuration,
                   started_at, completed_at
            FROM app.evaluation_run
            ORDER BY started_at DESC
            LIMIT 10
            """
        ).fetchall()
        agent_volumes = conn.execute(
            """
            SELECT agents.agent, count(*)::int AS count
            FROM app.message,
                 jsonb_array_elements_text(
                    coalesce(routing->'agents', '[]'::jsonb)
                 ) AS agents(agent)
            WHERE role = 'assistant'
            GROUP BY agents.agent
            ORDER BY count DESC
            """
        ).fetchall()

    evaluations = [dict(item) for item in evaluation_runs]
    benchmarks = load_benchmark_history()
    return {
        **dict(totals),
        "ingestion_runs": [dict(item) for item in ingestion_runs],
        "latest_evaluation": evaluations[0] if evaluations else None,
        "evaluation_history": evaluations,
        "latest_benchmark": benchmarks[0] if benchmarks else None,
        "benchmark_history": benchmarks,
        "agent_volumes": [dict(item) for item in agent_volumes],
    }


@router.get("/flags")
def admin_flags(_: UserContext = Depends(administrator)) -> list[dict]:
    with connection(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT flag.flag_id, flag.message_id, flag.request_id, flag.reason_code,
                   flag.severity, flag.details, flag.status, flag.reviewed_by,
                   flag.reviewed_at, flag.created_at,
                   message.conversation_id, message.content AS answer,
                   message.routing, message.claims, message.warnings,
                   jsonb_array_length(coalesce(message.evidence, '[]'::jsonb)) AS evidence_count,
                   coalesce(review_evidence.items, '[]'::jsonb) AS review_evidence,
                   message.latency_ms,
                   previous_user.content AS question
            FROM app.flagged_response AS flag
            LEFT JOIN app.message AS message ON message.message_id = flag.message_id
            LEFT JOIN LATERAL (
                SELECT candidate.content
                FROM app.message AS candidate
                WHERE candidate.conversation_id = message.conversation_id
                  AND candidate.role = 'user'
                  AND candidate.created_at <= message.created_at
                ORDER BY candidate.created_at DESC
                LIMIT 1
            ) AS previous_user ON TRUE
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_strip_nulls(
                        jsonb_build_object(
                            'evidence_id', source.item->>'evidence_id',
                            'evidence_type', source.item->>'evidence_type',
                            'title', source.item->>'title',
                            'excerpt', left(source.item->>'excerpt', 900),
                            'document_id', source.item->>'document_id',
                            'page', source.item->'page',
                            'query_id', source.item->>'query_id',
                            'data_as_of', source.item->>'data_as_of'
                        )
                    )
                ) AS items
                FROM jsonb_array_elements(coalesce(message.evidence, '[]'::jsonb)) AS source(item)
                WHERE source.item->>'evidence_id' IN (
                    SELECT cited.value
                    FROM jsonb_array_elements(coalesce(message.claims, '[]'::jsonb)) AS claim(item)
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        coalesce(claim.item->'evidence_ids', '[]'::jsonb)
                    ) AS cited(value)
                    WHERE coalesce((claim.item->>'supported')::boolean, false) IS FALSE
                )
            ) AS review_evidence ON TRUE
            ORDER BY flag.created_at DESC
            LIMIT 200
            """
        ).fetchall()
    return [dict(row) for row in rows]


@router.patch("/flags/{flag_id}")
def update_flag(
    flag_id: UUID,
    update: FlagStatusUpdate,
    user: UserContext = Depends(administrator),
) -> dict:
    with connection() as conn:
        row = conn.execute(
            """
            UPDATE app.flagged_response
            SET status = %s,
                reviewed_by = %s,
                reviewed_at = CASE
                    WHEN %s = 'OPEN' THEN NULL
                    ELSE CURRENT_TIMESTAMP
                END
            WHERE flag_id = %s
            RETURNING *
            """,
            (update.status, user.uid, update.status, flag_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Flag not found.")
    return dict(row)
