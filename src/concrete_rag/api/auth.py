"""API-key authentication with scopes. Keys are random 32-byte tokens; only their SHA-256 is stored."""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from concrete_rag.config import get_settings
from concrete_rag.store import docstore

SCOPES = {"retrieve", "generate", "calc", "estimate", "admin"}
header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(name: str, scopes: list[str]) -> str:
    bad = set(scopes) - SCOPES
    if bad:
        raise ValueError(f"unknown scopes {bad}; valid: {sorted(SCOPES)}")
    key = "crag_" + secrets.token_urlsafe(32)
    docstore.add_api_key(name, _hash(key), key[:10], scopes)
    return key


def principal(request: Request, key: str | None = Depends(header)) -> dict:
    if not key and get_settings().desktop_mode:
        request.state.key_name = "Local user"
        return {"name": "Local user", "scopes": sorted(SCOPES), "desktop": True}
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-API-Key header")
    boot = get_settings().api_bootstrap_admin_key
    if boot and hmac.compare_digest(key, boot):
        p = {"name": "bootstrap-admin", "scopes": sorted(SCOPES)}
    else:
        p = docstore.lookup_api_key(_hash(key))
        if not p:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked API key")
    request.state.key_name = p["name"]
    return p


def require(scope: str):
    def dep(p: dict = Depends(principal)) -> dict:
        if scope not in p["scopes"] and "admin" not in p["scopes"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"API key lacks scope '{scope}'")
        return p
    return dep
