"""Authentication for every caller that is not the GitHub webhook (HMAC lives in github_app.py).
Contract: budgetguard-admission-gate/references/security-and-api.md §3 and §6."""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import Header, HTTPException, Request
from jwt import PyJWKClient, decode
from pydantic import SecretStr

from .config import get_settings

ACTIONS_ISSUER = "https://token.actions.githubusercontent.com"
_jwks = PyJWKClient(f"{ACTIONS_ISSUER}/.well-known/jwks", cache_keys=True, lifespan=3600)


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    return authorization.removeprefix("Bearer ")


def _matches(token: str, *secrets: SecretStr | None) -> bool:
    """Constant-time compare against the current secret and, during a rotation, the next one."""
    return bool(token) and any(s is not None and s.get_secret_value()
                               and hmac.compare_digest(token.encode(), s.get_secret_value().encode())
                               for s in secrets)


def require_dashboard(authorization: str | None = Header(default=None)) -> None:
    s = get_settings()
    if not _matches(_bearer(authorization), s.dashboard_token, s.dashboard_token_next):
        raise HTTPException(401, "bad dashboard token")


def require_alertmanager(authorization: str | None = Header(default=None)) -> None:
    s = get_settings()
    if not _matches(_bearer(authorization), s.alertmanager_token, s.alertmanager_token_next):
        raise HTTPException(401, "bad alertmanager token")


def _jwks_key(token: str) -> Any:
    return _jwks.get_signing_key_from_jwt(token).key


def verify_actions_oidc(token: str, expected_env: str, run_id: int,
                        key_for: Callable[[str], Any] | None = None) -> dict:
    """GitHub Actions OIDC bound to this repo (by name and id), environment, run, branch and workflow."""
    s = get_settings()
    key = (key_for or _jwks_key)(token)
    claims = decode(token, key, algorithms=["RS256"], audience="budgetguard", issuer=ACTIONS_ISSUER,
                    leeway=60, options={"require": ["exp", "iat", "repository", "environment", "run_id"]})
    expected = {
        "repository": s.github_repo,
        "repository_id": str(s.github_repo_id),
        "repository_owner_id": str(s.github_owner_id),
        "environment": expected_env,
        "run_id": str(run_id),
        "ref": "refs/heads/main",
        "workflow_ref": f"{s.github_repo}/.github/workflows/deploy.yml@refs/heads/main",
    }
    if any(str(claims.get(k)) != v for k, v in expected.items()):
        raise PermissionError("token not issued for this repo/environment/run")
    return claims


async def github_user(request: Request, user_token: str) -> tuple[int, str]:
    """Resolve the human behind a dashboard override from their own GitHub token: (numeric id, login).
    Cached 5 minutes under a hash of the token (never the token itself)."""
    r = request.app.state.valkey
    key = "bg:ghuser:" + hashlib.sha256(user_token.encode()).hexdigest()
    if (cached := await r.get(key)) is not None:
        uid, _, login = (cached if isinstance(cached, str) else cached.decode()).partition(":")
        return int(uid), login
    client: httpx.AsyncClient = request.app.state.http
    resp = await client.get("https://api.github.com/user", timeout=5.0,
                            headers={"Authorization": f"Bearer {user_token}",
                                     "Accept": "application/vnd.github+json"})
    if resp.status_code == 401:
        raise HTTPException(401, "GitHub token rejected")
    resp.raise_for_status()
    body = resp.json()
    uid, login = int(body["id"]), body["login"]
    await r.set(key, f"{uid}:{login}", ex=300)
    return uid, login
