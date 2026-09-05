"""Conversation, message, user, audit, and monitoring persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .db import connection
from .models import ChatResponse, UserContext


def _primary_role(user: UserContext) -> str:
    for role in ("ADMINISTRATOR", "MANAGER", "OPERATIONS_USER"):
        if role in user.roles:
            return role
    return "OPERATIONS_USER"


def upsert_user(user: UserContext) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO app.app_user(firebase_uid, email, display_name, role)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (firebase_uid) DO UPDATE
            SET email=EXCLUDED.email, display_name=EXCLUDED.display_name,
                role=EXCLUDED.role, updated_at=CURRENT_TIMESTAMP
            """,
            (user.uid, user.email, user.display_name, _primary_role(user)),
        )


def get_or_create_conversation(
    user: UserContext,
    conversation_id: UUID | None,
    query: str,
) -> UUID:
    upsert_user(user)
    with connection() as conn:
        if conversation_id:
            row = conn.execute(
                """
                SELECT conversation_id
                FROM app.conversation
                WHERE conversation_id = %s AND firebase_uid = %s
                """,
                (conversation_id, user.uid),
            ).fetchone()
            if not row:
                raise PermissionError("Conversation does not exist or belongs to another user.")
            conn.execute(
                """
                UPDATE app.conversation
                SET updated_at = CURRENT_TIMESTAMP
                WHERE conversation_id = %s
                """,
                (conversation_id,),
            )
            return conversation_id
        new_id = uuid4()
        title = query.strip().replace("\n", " ")[:100]
        conn.execute(
            """
            INSERT INTO app.conversation(conversation_id, firebase_uid, title)
            VALUES (%s, %s, %s)
            """,
            (new_id, user.uid, title),
        )
        return new_id


def save_chat(
    *,
    user: UserContext,
    query: str,
    response: ChatResponse,
) -> None:
    user_message_id = uuid4()
    assistant_message_id = uuid4()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO app.message(message_id, conversation_id, role, content)
            VALUES (%s, %s, 'user', %s)
            """,
            (user_message_id, response.conversation_id, query),
        )
        conn.execute(
            """
            INSERT INTO app.message(
                message_id, conversation_id, role, content, routing, claims,
                evidence, warnings, latency_ms
            ) VALUES (%s, %s, 'assistant', %s, %s, %s, %s, %s, %s)
            """,
            (
                assistant_message_id,
                response.conversation_id,
                response.answer,
                Jsonb(response.routing.model_dump(mode="json")),
                Jsonb([claim.model_dump(mode="json") for claim in response.claims]),
                Jsonb([item.model_dump(mode="json") for item in response.evidence]),
                Jsonb(response.warnings),
                response.latency_ms,
            ),
        )
        conn.execute(
            """
            UPDATE app.conversation
            SET updated_at = CURRENT_TIMESTAMP
            WHERE conversation_id = %s
            """,
            (response.conversation_id,),
        )
        conn.execute(
            """
            INSERT INTO app.audit_event(
                event_id, firebase_uid, request_id, event_type, resource_type, resource_id, details
            ) VALUES (%s, %s, %s, 'CHAT_COMPLETED', 'conversation', %s, %s)
            """,
            (
                uuid4(),
                user.uid,
                response.request_id,
                str(response.conversation_id),
                Jsonb(
                    {
                        "agents": response.routing.agents,
                        "supported_claim_rate": response.supported_claim_rate,
                        "timings_ms": response.timings_ms,
                        "provider_metrics": response.provider_metrics,
                    }
                ),
            ),
        )
        if response.supported_claim_rate < 1.0:
            conn.execute(
                """
                INSERT INTO app.flagged_response(
                    flag_id, message_id, request_id, reason_code, severity, details
                ) VALUES (%s, %s, %s, 'INCOMPLETE_CLAIM_SUPPORT', 'MEDIUM', %s)
                """,
                (
                    uuid4(),
                    assistant_message_id,
                    response.request_id,
                    Jsonb({"supported_claim_rate": response.supported_claim_rate}),
                ),
            )


def list_conversations(user: UserContext, limit: int = 50) -> list[dict[str, Any]]:
    with connection(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT conversation_id, title, created_at, updated_at
            FROM app.conversation
            WHERE firebase_uid=%s
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (user.uid, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def conversation_messages(
    user: UserContext,
    conversation_id: UUID,
) -> list[dict[str, Any]]:
    with connection(readonly=True) as conn:
        allowed = conn.execute(
            """
            SELECT 1
            FROM app.conversation
            WHERE conversation_id = %s AND firebase_uid = %s
            """,
            (conversation_id, user.uid),
        ).fetchone()
        if not allowed:
            raise PermissionError("Conversation does not exist or belongs to another user.")
        rows = conn.execute(
            """
            SELECT message_id, role, content, routing, claims, evidence,
                   warnings, latency_ms, created_at
            FROM app.message
            WHERE conversation_id = %s
            ORDER BY created_at
            """,
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def now_utc() -> datetime:
    return datetime.now(UTC)
