"""Mock auth — replace with JWT / OAuth / API-gateway lookup in production."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class User:
    tenant_id: str
    user_id: str


_TOKENS: dict[str, User] = {
    "dev-alice": User(tenant_id="acme", user_id="alice"),
    "dev-bob":   User(tenant_id="acme", user_id="bob"),
    "dev-carol": User(tenant_id="bigco", user_id="carol"),
}


def current_user(authorization: str | None = Header(None)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    user = _TOKENS.get(token)
    if user is None:
        raise HTTPException(401, "unknown token")
    return user
