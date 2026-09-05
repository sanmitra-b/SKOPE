"""Health, identity, and corpus-status endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from qdrant_client import QdrantClient

from ..auth import current_user
from ..config import get_settings
from ..db import database_health
from ..models import IndexStatus, UserContext
from ..retrieval import onnx_runtime_status
from ..services.indexing import get_index_status


router = APIRouter()


@router.get("/config.js", include_in_schema=False)
def frontend_config() -> Response:
    """Expose only the public browser configuration needed by the static UI."""
    settings = get_settings()
    payload = {
        "apiBaseUrl": "",
        "authMode": settings.auth_mode,
        "firebase": {
            "apiKey": settings.firebase_api_key,
            "authDomain": settings.firebase_auth_domain,
            "projectId": settings.firebase_project_id,
            "appId": settings.firebase_app_id,
        },
    }
    return Response(
        f"window.SKOPE_CONFIG = {json.dumps(payload)};",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    result = database_health()
    try:
        client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
        result["qdrant"] = client.get_collections().model_dump()
    except Exception as exc:
        result["qdrant_error"] = str(exc)
    result["status"] = "ok" if "qdrant_error" not in result else "degraded"
    result["version"] = settings.app_version
    result["inference"] = onnx_runtime_status()
    return result


@router.get("/api/me")
def me(user: UserContext = Depends(current_user)) -> UserContext:
    return user


@router.get("/api/index/status", response_model=IndexStatus)
def index_status(_: UserContext = Depends(current_user)) -> IndexStatus:
    return get_index_status()
