"""Chat streaming and conversation-history endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..auth import current_user
from ..models import ChatRequest, ChatResponse, UserContext
from ..persistence import conversation_messages, list_conversations
from ..rate_limit import enforce_chat_rate_limit
from ..services.chat import build_chat_response


router = APIRouter(prefix="/api")
logger = logging.getLogger("skope.api.chat")


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: UserContext = Depends(current_user),
) -> ChatResponse:
    enforce_chat_rate_limit(user.uid)
    return await asyncio.to_thread(build_chat_response, request, user)


async def _chat_events(request: ChatRequest, user: UserContext) -> AsyncIterator[str]:
    yield "event: status\ndata: " + json.dumps(
        {"stage": "routing", "message": "Routing query"}
    ) + "\n\n"
    loop = asyncio.get_running_loop()
    updates: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    def progress(stage: str, message: str) -> None:
        loop.call_soon_threadsafe(updates.put_nowait, (stage, message))

    task = asyncio.create_task(asyncio.to_thread(build_chat_response, request, user, progress))
    try:
        while not task.done() or not updates.empty():
            try:
                stage, message = await asyncio.wait_for(updates.get(), timeout=0.1)
            except TimeoutError:
                continue
            yield "event: status\ndata: " + json.dumps(
                {"stage": stage, "message": message}
            ) + "\n\n"
        response = await task
        yield "event: result\ndata: " + response.model_dump_json() + "\n\n"
    except HTTPException as exc:
        yield "event: error\ndata: " + json.dumps({"message": str(exc.detail)}) + "\n\n"
    except Exception:
        logger.exception("Unexpected chat-stream failure")
        yield "event: error\ndata: " + json.dumps(
            {"message": "SKOPE could not complete this request. Please try again."}
        ) + "\n\n"


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user: UserContext = Depends(current_user),
) -> StreamingResponse:
    enforce_chat_rate_limit(user.uid)
    return StreamingResponse(
        _chat_events(request, user),
        media_type="text/event-stream",
    )


@router.get("/conversations")
def conversations(user: UserContext = Depends(current_user)) -> list[dict]:
    return list_conversations(user)


@router.get("/conversations/{conversation_id}")
def messages(
    conversation_id: UUID,
    user: UserContext = Depends(current_user),
) -> list[dict]:
    try:
        return conversation_messages(user, conversation_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
