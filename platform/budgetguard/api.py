"""HTTP surface other than the GitHub webhook. Auth per endpoint follows budgetguard-admission-gate §6."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import BaseModel, Field

from .auth import github_user, require_alertmanager, require_dashboard, verify_actions_oidc
from .config import get_settings
from .incidents import events_from_payload
from .integrations.prometheus import PrometheusClient, PrometheusUnavailable
from .queue import invalidate_verdicts
from .records import replay
from .rulegen.spec import ServiceLevel
from .signals import slo_status
from .store import DecisionRow, Store
from .summary import SloInputs, Window, gate_remaining, service_card

router = APIRouter()

BAKE = {(100,): "0s", (25, 100): "2m", (10, 50, 100): "5m", (5, 25, 50, 100): "10m"}
CONSERVATIVE_PROFILE = (5, 25, 50, 100)
PAGE_SIZE = 50

PROM_REACHABLE = Gauge("bg_prometheus_reachable",
                       "1 if this replica can query Prometheus (the BudgetGuardBlind alert watches it)")


def store(request: Request) -> Store:
    return request.app.state.store


@router.get("/livez")
async def livez() -> dict:
    return {"ok": True}                                  # event loop alive; no dependencies


@router.get("/readyz")
async def readyz(request: Request) -> dict:
    try:
        await request.app.state.store.ping()
        await request.app.state.valkey.ping()
    except Exception as exc:                             # noqa: BLE001 - readiness reports, never raises
        raise HTTPException(503, f"not ready: {type(exc).__name__}") from exc
    return {"ok": True}


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Internal scrape only (the edge guard 404s it from the tunnel). Probes Prometheus on every
    scrape: the breaker alone would only notice an outage when a decision happened to need it."""
    prom: PrometheusClient = request.app.state.prom
    try:
        PROM_REACHABLE.set(1 if await prom.scalar("vector(1)") == 1 else 0)
    except (PrometheusUnavailable, ValueError):
        PROM_REACHABLE.set(0)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------- deploy job (GitHub OIDC)
@router.get("/v1/decisions")
async def decision_for_run(service: Annotated[str, Query(pattern=r"^[a-z][a-z0-9-]{1,40}$")],
                           run_id: Annotated[int, Query(gt=0)],
                           authorization: Annotated[str | None, Header()] = None,
                           s: Store = Depends(store)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "OIDC token required")
    try:
        verify_actions_oidc(authorization.removeprefix("Bearer "), f"production-{service}", run_id)
    except Exception as exc:                             # bad signature, audience, repo or environment
        raise HTTPException(403, "token not valid for this environment") from exc
    d = await s.decision_for_run(service, run_id)
    if d is None:
        raise HTTPException(404, "no decision for this run")
    if d.verdict == "block":
        raise HTTPException(409, "this run was rejected")   # the job should never be running
    profile = tuple(d.canary_profile) or CONSERVATIVE_PROFILE
    return {"decision_id": d.id, "verdict": d.verdict, "canary_profile": list(profile),
            "bake": BAKE.get(profile, "10m")}


# ---------------------------------------------------------------- Alertmanager (Flow 3)
@router.post("/v1/webhooks/alertmanager", dependencies=[Depends(require_alertmanager)])
async def alertmanager(payload: dict, request: Request, s: Store = Depends(store)) -> dict:
    events = events_from_payload(payload)
    for e in events:
        await s.upsert_incident(e)                      # keyed (fingerprint, started_at): replays are no-ops
        await invalidate_verdicts(request.app.state.valkey, e.service)
    return {"incidents": len(events)}


# ---------------------------------------------------------------- dashboard (BFF)
class OverrideIn(BaseModel):
    decision_id: int = Field(gt=0)
    reason: str = Field(min_length=20, max_length=500)


@router.post("/v1/overrides", dependencies=[Depends(require_dashboard)])
async def create_override(body: OverrideIn, request: Request,
                          x_github_user_token: Annotated[str | None, Header()] = None,
                          s: Store = Depends(store)) -> dict:
    if not x_github_user_token:
        raise HTTPException(401, "user token required")
    user_id, login = await github_user(request, x_github_user_token)   # the API decides who this is
    if user_id not in get_settings().approver_ids:                      # by id: logins can be renamed
        raise HTTPException(403, f"{login} is not an approver")
    d = await s.decision(body.decision_id)
    if d is None:
        raise HTTPException(404, "no such decision")
    if not d.overridable or d.github_run_id is None or d.installation_id is None:
        raise HTTPException(409, "nothing to override: not blocked, already overridden, or too old")
    override_id = await s.create_override(d.id, user_id, login, body.reason.strip())   # single use, 30 min
    # The old callback was spent when the gate rejected; a re-run raises a fresh protection-rule
    # request, which the override lane admits (budgetguard-admission-gate, fourth flag).
    try:
        await request.app.state.rerun(d)
    except Exception as exc:                             # never leave an armed override behind
        await s.delete_override(override_id)
        raise HTTPException(503, "re-run failed; nothing was approved") from exc
    return {"override_id": override_id}


@router.get("/v1/services/summary", dependencies=[Depends(require_dashboard)])
async def services_summary(request: Request, s: Store = Depends(store)) -> list[dict[str, Any]]:
    specs: list[ServiceLevel] = request.app.state.specs
    prom: PrometheusClient = request.app.state.prom
    attribution = getattr(request.app.state, "attribution", "charge_origin")
    now = datetime.now(UTC)
    windows, daily, incidents, decisions = await asyncio.gather(
        s.slo_windows(now), s.slo_daily(now, 59), s.open_incidents(), s.last_decisions())
    statuses = await asyncio.gather(*(slo_status(prom, sl, slo) for sl in specs for slo in sl.spec.slos))
    by_key = {(st.service, st.slo): st for st in statuses}
    cards = []
    for sl in specs:
        svc, inputs = sl.metadata.service, {}
        for slo in sl.spec.slos:
            raw = windows.get((svc, slo.name))
            w = Window(*raw) if raw else None
            rem = gate_remaining(slo, w, attribution)
            frozen_by_budget = rem is not None and rem < sl.spec.gate.reserve_pct
            self_only = attribution == "charge_origin" and bool(slo.sli.self_bad)
            inputs[slo.name] = SloInputs(
                status=by_key[(svc, slo.name)], window=w, daily=daily.get((svc, slo.name), []),
                eta_buckets=await s.eta_buckets(svc, slo.name, now, self_only) if frozen_by_budget else ())
        cards.append(service_card(sl, inputs, now=now, open_incident=incidents.get(svc),
                                  last_decision=decisions.get(svc), attribution=attribution))
    return cards


@router.get("/v1/decisions/page", dependencies=[Depends(require_dashboard)])   # before /{decision_id}
async def decision_page(service: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9-]{1,40}$")] = None,
                        cursor: Annotated[str | None, Query(pattern=r"^[1-9][0-9]{0,18}$")] = None,
                        s: Store = Depends(store)) -> dict[str, Any]:
    rows = await s.decision_page(service, int(cursor) if cursor else None, PAGE_SIZE + 1)
    items = [{k: v for k, v in _decision_json(d).items() if k not in ("inputs", "brief")}
             for d in rows[:PAGE_SIZE]]
    return {"items": items, "next_cursor": str(rows[PAGE_SIZE - 1].id) if len(rows) > PAGE_SIZE else None}


@router.get("/v1/policy/versions", dependencies=[Depends(require_dashboard)])
async def policy_versions(s: Store = Depends(store)) -> list[dict[str, Any]]:
    running = get_settings().policy_version
    return [{**v, "active": v["version"] == running} for v in await s.policy_versions()]


@router.get("/v1/decisions/{decision_id}", dependencies=[Depends(require_dashboard)])
async def decision_detail(decision_id: int, s: Store = Depends(store)) -> dict:
    d = await s.decision(decision_id)
    if d is None:
        raise HTTPException(404, "no such decision")
    return _decision_json(d)


@router.post("/v1/decisions/{decision_id}/replay", dependencies=[Depends(require_dashboard)])
async def decision_replay(decision_id: int, s: Store = Depends(store)) -> dict:
    """Recompute the verdict from the stored GateInput with the running code. Writes nothing."""
    d = await s.decision(decision_id)
    if d is None:
        raise HTTPException(404, "no such decision")
    if "gate_input" not in d.inputs:
        raise HTTPException(409, "decision has no stored gate input")
    r = replay(d.inputs, d.inputs_hash, d.verdict, d.reason_code)
    return {"verdict": str(r.decision.verdict), "reason_code": r.decision.reason_code,
            "inputs_hash": r.inputs_hash, "matches": r.matches}


def _brief(d: DecisionRow) -> dict | None:
    """The template comment GitHub showed (W5); the LLM brief replaces it from W7."""
    text = d.inputs.get("comment")
    return {"text": text, "source": "template", "fallback_reason": None} if text else None


def _decision_json(d: DecisionRow) -> dict:
    inputs = {k: v for k, v in d.inputs.items() if k != "comment"}
    return {"id": d.id, "service": d.service, "sha": d.sha, "verdict": d.verdict,
            "reason_code": d.reason_code, "price_pct": d.price_pct, "affordable_pct": d.affordable_pct,
            "canary_profile": list(d.canary_profile), "policy_version": d.policy_version,
            "inputs_hash": d.inputs_hash, "decided_at": d.decided_at.isoformat(),
            "overridable": d.overridable, "brief": _brief(d), "override": d.override, "inputs": inputs}
