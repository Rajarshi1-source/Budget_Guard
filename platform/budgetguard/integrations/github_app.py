from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, Header, HTTPException, Request
from redis.exceptions import ConnectionError as ValkeyDown
from redis.exceptions import TimeoutError as ValkeyTimeout

from ..config import settings
from ..queue import enqueue_once

router = APIRouter()
ENV = re.compile(r"^production-([a-z][a-z0-9-]{1,40})$")
RUN_ID = re.compile(r"/actions/runs/(\d+)/deployment_protection_rule$")
MAX_BODY = 2 * 1024 * 1024
API = "https://api.github.com"


def verify_signature(body: bytes, signature: str | None) -> None:
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(401, "missing signature")
    for secret in settings.webhook_secrets():                # current, then previous during rotation
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            return
    raise HTTPException(401, "bad signature")


def ours(payload: dict) -> bool:
    """The App is private, but a misconfiguration must not let another repo drive the gate."""
    return (payload.get("installation", {}).get("id") == settings.github_installation_id
            and payload.get("repository", {}).get("full_name") == settings.github_repo)


@router.post("/v1/github/webhook")
async def github_webhook(request: Request,
                         x_hub_signature_256: str | None = Header(default=None),
                         x_github_event: str = Header(...),
                         x_github_delivery: str = Header(...)) -> dict:
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise HTTPException(413, "body too large")
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "body too large")
    verify_signature(body, x_hub_signature_256)          # before parsing anything
    payload = json.loads(body)
    if not ours(payload):
        return {"ignored": "repository"}
    if x_github_event in ("deployment_status", "pull_request"):
        return await _deploy_event(request, x_github_event, x_github_delivery, payload)
    if x_github_event != "deployment_protection_rule":
        return {"ignored": x_github_event}
    m_env = ENV.match(payload.get("environment", ""))
    if m_env is None:
        return {"ignored": payload.get("environment")}
    callback = payload["deployment_callback_url"]
    m = RUN_ID.search(callback)
    job = {
        "service": m_env.group(1),
        "environment": payload["environment"],
        "callback_url": callback,
        "run_id": int(m.group(1)) if m else None,
        "installation_id": payload["installation"]["id"],
        "sha": payload["deployment"]["sha"],
        "deployment_id": payload["deployment"].get("id"),   # signed; the worker's second dedupe key
        "repo": payload["repository"]["full_name"],
        "delivery": x_github_delivery,
    }
    try:
        validate_job(job)                                # same rule the worker applies after the queue
    except JobRejected as exc:                           # e.g. GitHub changed the callback URL shape:
        raise HTTPException(422, f"unexpected job: {exc}") from exc   # a failed, redeliverable delivery
    try:
        accepted = await enqueue_once(request.app.state.valkey, "bg:gate-evaluations",
                                      key=x_github_delivery, job=job)  # signature: budgetguard-data-layer §4
    except (ValkeyDown, ValkeyTimeout, OSError):
        return await _inline(request, job)               # Valkey down: decide now, Postgres dedupes
    return {"accepted": accepted}                        # 200 within ~1 s; work happens in the worker


async def _inline(request: Request, job: dict[str, Any]) -> dict[str, Any]:
    """Valkey unavailable (budgetguard-admission-gate §7): evaluate in the request, without the
    single-flight lock or verdict cache; UNIQUE(github_delivery, github_deployment_id) dedupes.
    Two callback attempts keep the answer inside GitHub's 10 s webhook timeout."""
    from dataclasses import replace

    from ..workers.gate_worker import evaluate_job
    gate = getattr(request.app.state, "gate", None)
    if gate is None:
        raise HTTPException(503, "queue unavailable")    # GitHub marks the delivery failed; redeliverable
    from ..db import DB_DOWN
    try:
        out = await evaluate_job(replace(gate, valkey=None, callback_attempts=2), job)
    except DB_DOWN as exc:                               # nothing recorded or buffered: never answer
        raise HTTPException(503, "cannot record a decision") from exc   # redelivery reconciler retries
    return {"accepted": True, "inline": out.status}


async def _deploy_event(request: Request, event: str, delivery: str,
                        payload: dict[str, Any]) -> dict[str, Any]:
    """deployment_status and merged pull_request events for the deploy recorder (W6.3): only the
    identifiers it needs, never the full payload, onto bg:deploy-events."""
    if event == "deployment_status":
        m = ENV.match(payload.get("deployment", {}).get("environment", ""))
        if m is None:
            return {"ignored": payload.get("deployment", {}).get("environment")}
        dep, st = payload["deployment"], payload["deployment_status"]
        rec = {"event": event, "service": m.group(1), "deployment_id": dep["id"], "sha": dep["sha"],
               "state": st["state"], "at": st.get("updated_at") or st.get("created_at"),
               "run_id": _run_id(st.get("target_url") or st.get("log_url") or "")}
    else:
        pr = payload.get("pull_request", {})
        if payload.get("action") != "closed" or not pr.get("merged"):
            return {"ignored": f"pull_request {payload.get('action')}"}
        rec = {"event": event, "number": pr["number"], "merge_commit_sha": pr.get("merge_commit_sha"),
               "merged_at": pr.get("merged_at"), "created_at": pr.get("created_at"),
               "base": pr.get("base", {}).get("ref")}
    try:
        accepted = await enqueue_once(request.app.state.valkey, "bg:deploy-events", key=delivery, job=rec)
    except (ValkeyDown, ValkeyTimeout, OSError) as exc:
        raise HTTPException(503, "queue unavailable") from exc   # redeliverable; the reconciler retries
    return {"accepted": accepted}


def _run_id(url: str) -> int | None:
    m = re.search(r"/actions/runs/(\d+)", url)
    return int(m.group(1)) if m else None


class AppMisconfigured(RuntimeError):
    """The App credentials cannot sign a JWT (missing or malformed key): permanent until an operator
    fixes the secret, so callers record the failure instead of retrying."""


class JobRejected(ValueError):
    """A job that must never cause a token to be minted (security-and-api.md T-8)."""


def validate_job(job: dict[str, Any]) -> None:
    """Re-run in the gate worker on every job read from Valkey, and before any re-run call:
    anything in the queue may have been written by someone who is not GitHub."""
    repo = settings.github_repo
    if job.get("repo") != repo or job.get("installation_id") != settings.github_installation_id:
        raise JobRejected("foreign repo or installation")
    env = ENV.match(job.get("environment", ""))
    if env is None or job.get("service") != env.group(1):
        raise JobRejected("environment and service disagree")
    expected = re.compile(
        rf"^{re.escape(API)}/repos/{re.escape(repo)}/actions/runs/(\d+)/deployment_protection_rule$")
    m = expected.match(job.get("callback_url", ""))
    if m is None or int(m.group(1)) != job.get("run_id"):
        raise JobRejected("callback_url is not this repo's protection-rule URL for this run")


def app_jwt() -> str:
    now = int(time.time())
    try:
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": settings.github_app_id},
                          settings.github_app_private_key.get_secret_value(), algorithm="RS256")
    except (jwt.PyJWTError, ValueError, TypeError) as e:
        raise AppMisconfigured(f"cannot sign the App JWT: {type(e).__name__}") from e


async def installation_token(client: httpx.AsyncClient, installation_id: int,
                             permissions: dict[str, str]) -> str:
    """Down-scoped to this repository and the one permission the call needs (§6.7)."""
    if installation_id != settings.github_installation_id:
        raise JobRejected("foreign installation")
    r = await client.post(f"{API}/app/installations/{installation_id}/access_tokens",
                          headers={"Authorization": f"Bearer {app_jwt()}",
                                   "Accept": "application/vnd.github+json"},
                          json={"repositories": [settings.github_repo.split("/", 1)[1]],
                                "permissions": permissions})
    r.raise_for_status()
    return r.json()["token"]


async def post_review(client: httpx.AsyncClient, job: dict, approved: bool, comment: str) -> None:
    validate_job(job)                                    # never send a token to a URL we did not check
    token = await installation_token(client, job["installation_id"], {"deployments": "write"})
    r = await client.post(job["callback_url"],
                          headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/vnd.github+json"},
                          json={"environment_name": job["environment"],
                                "state": "approved" if approved else "rejected",
                                "comment": comment[:1000]})
    r.raise_for_status()


class GitHubApi:
    """Read-only REST calls for one job (lane proofs). One installation token per instance, minted
    lazily and down-scoped to contents: read on this repository."""

    def __init__(self, client: httpx.AsyncClient, installation_id: int) -> None:
        self.client, self.installation_id = client, installation_id
        self._token: str | None = None

    async def get(self, path: str) -> dict[str, Any]:
        if self._token is None:
            self._token = await installation_token(self.client, self.installation_id, {"contents": "read"})
        r = await self.client.get(f"{API}{path}", timeout=5.0,
                                  headers={"Authorization": f"Bearer {self._token}",
                                           "Accept": "application/vnd.github+json"})
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        return body
