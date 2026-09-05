from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings
from .models import UserContext


@lru_cache
def _firebase_app(service_account_json: str | None):
    """Return the process-wide Firebase app using a path, JSON, or ADC."""
    import firebase_admin
    from firebase_admin import credentials

    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    if service_account_json:
        service_account_path = Path(service_account_json)
        credential_source = (
            str(service_account_path)
            if service_account_path.is_file()
            else json.loads(service_account_json)
        )
        credential = credentials.Certificate(credential_source)
        return firebase_admin.initialize_app(credential)
    return firebase_admin.initialize_app()


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A bearer token is required.",
        )
    return authorization[7:].strip()


def _normalized_roles(decoded_token: dict[str, Any]) -> list[str]:
    """Normalize Firebase's optional singular or plural role claims."""
    claimed_roles = decoded_token.get("roles")
    if claimed_roles is None:
        claimed_roles = [decoded_token.get("role", "OPERATIONS_USER")]
    elif isinstance(claimed_roles, str):
        claimed_roles = [claimed_roles]
    elif not isinstance(claimed_roles, (list, tuple, set)):
        claimed_roles = ["OPERATIONS_USER"]
    return [str(role).upper() for role in claimed_roles]


async def current_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> UserContext:
    token = _bearer_token(authorization)
    auth_mode = settings.auth_mode.lower()
    if auth_mode == "dev":
        if token != settings.dev_auth_token:
            raise HTTPException(status_code=401, detail="Invalid local development token.")
        return UserContext(
            uid="local-admin",
            email="admin@skope.local",
            display_name="Local SKOPE Administrator",
            roles=["ADMINISTRATOR", "MANAGER", "OPERATIONS_USER", "PII_OPERATOR"],
            auth_mode="dev",
        )

    if auth_mode != "firebase":
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported authentication mode: {settings.auth_mode}",
        )

    try:
        from firebase_admin import auth

        _firebase_app(settings.firebase_service_account_json)
        decoded = auth.verify_id_token(token, check_revoked=True)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired Firebase token.") from exc

    return UserContext(
        uid=decoded["uid"],
        email=decoded.get("email", "unknown"),
        display_name=decoded.get("name"),
        roles=_normalized_roles(decoded),
        auth_mode="firebase",
    )


def require_roles(*allowed_roles: str):
    """Create a FastAPI dependency that accepts any configured role."""
    allowed = {role.upper() for role in allowed_roles}

    async def dependency(user: UserContext = Depends(current_user)) -> UserContext:
        if not allowed.intersection(user.roles):
            raise HTTPException(status_code=403, detail="This role is not authorized.")
        return user

    return dependency
