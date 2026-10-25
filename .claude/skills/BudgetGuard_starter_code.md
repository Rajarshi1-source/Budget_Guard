# BudgetGuard — starter code (aligned with Rev 2 and the eight BudgetGuard skills)

This is the scaffold to start week 1 from: the repository layout, the platform's FastAPI
skeleton (config, persistence boundary, Valkey helpers, auth, Alertmanager intake, the
deploy-job decision read, overrides by re-run, the gate worker loop, migrations, the `bg`
CLI), the root files, and the build order mapped to the skills.

**Verified on 25 Oct 2026** (Python 3.14.6, FastAPI 0.143.0): the platform's 20 API tests pass
(`pytest`), covering webhook HMAC and redelivery, Alertmanager auth and idempotent incident
upserts, verdict-cache invalidation, GitHub OIDC checks on `/v1/decisions` (wrong environment,
forked repo, another run's token, re-created repo id, foreign workflow, blocked run), bake
derivation, the override flow (API-side user resolution, approver by numeric id, single use,
re-run, no armed override after a failed re-run), the dashboard token rotation window and the
public-path guard. The 24 Oct version had 14 tests; the additions implement
`budgetguard-admission-gate/references/security-and-api.md` §11. Worker, PostgreSQL store, callbacks and migrations are
compile-checked only; they need TimescaleDB and GitHub, so cover them with a
testcontainers integration test in week 4–5. The tests ran on Python 3.13 with FastAPI
0.162; the 25 Oct re-run used the target Python 3.14.6 and the 0.143.0 pin.

Where the rest of the code lives (do not duplicate it here):

| Area | Complete code in |
| --- | --- |
| Budget math, integrity, ETA | `budgetguard-slo-math` |
| Gate chain, pricing, lanes, GitHub App | `budgetguard-admission-gate` + `references/github-app.md` |
| Spec, rule generator, promtool tests, Alertmanager | `budgetguard-slo-rules` |
| Spring Boot services, error_origin, faults, k6 | `budgetguard-spring-services` |
| DDL, queries, snapshotter, Valkey keys | `budgetguard-data-layer` + `references/schema-and-queries.md` |
| Backtest, brief writer, grounding eval, workflows | `budgetguard-mlops-evals` + `references/eval-harness.md` |
| Compose, Helm, k3s, CI/CD, tunnel | `budgetguard-delivery-platform` + `references/manifests-and-workflows.md` |
| Next.js dashboard | `budgetguard-nextjs-dashboard` + `references/dashboard-code.md` |

---

## 1. Kickoff checklist (versions moved since Rev 2 was written)

| Item | Rev 2 | Seen on 24 Oct 2026 | Action |
| --- | --- | --- | --- |
| FastAPI | 0.128.x | 0.162.0 latest; ~20 minor releases between 18 and 23 Oct | Pin `0.143.0` (8 Oct, newest older than two weeks) and move up once the burst settles |
| Next.js | 16.3.x | 16.4.0 (6 Oct) … 16.4.3 (23 Oct) | Build on 16.3.8; trial 16.4 on a branch |
| Better Auth | — (Rev 2 names no auth library) | 1.7.12 | Use it; Auth.js is now maintained by the Better Auth team |
| Prometheus, Alertmanager, Grafana, Argo Rollouts, Helm, k3s, TimescaleDB, Valkey | §C | not re-checked (GitHub API blocked in this session) | Re-check each tag in `.env.versions` and chart pins |
| LLM prices | §E | not re-checked | Re-check and update `config/llm.yaml` `pricing` |

---

## 2. Repository layout

```text
budgetguard/
├── services/                      # budgetguard-spring-services (Maven reactor, bg-common + 4 services)
├── slo/                           # one ServiceLevel spec per service
├── policy/freeze-policy.yaml      # changes trigger policy-backtest.yml
├── platform/
│   ├── pyproject.toml
│   ├── budgetguard/
│   │   ├── config.py  store.py  pgstore.py  queue.py  auth.py  edge.py  incidents.py  api.py  main.py
│   │   ├── budget.py  integrity.py  eta.py               # budgetguard-slo-math
│   │   ├── gate.py  pricing.py  lanes.py  changes.py     # budgetguard-admission-gate
│   │   ├── briefs.py                                     # budgetguard-mlops-evals
│   │   ├── rulegen/{spec,generate,sloth,cli}.py          # budgetguard-slo-rules
│   │   ├── integrations/{github_app,callbacks,prometheus,slack}.py
│   │   └── workers/{gate_worker,snapshotter,deploy_events,brief_writer}.py
│   ├── backtest/  evals/  prompts/  config/llm.yaml      # budgetguard-mlops-evals
│   ├── migrations/versions/       # Alembic, forward-only
│   ├── sql/                       # 001_schema.sql, 002_timeseries.sql, 003_seed.sql
│   └── tests/
├── rules/{generated,tests,parity}/
├── dashboard/                     # budgetguard-nextjs-dashboard
├── grafana/                       # provisioned datasources + dashboards JSON
├── load/                          # k6 scripts
├── deploy/{compose,helm,k3s}/
├── .github/{workflows,CODEOWNERS}       # CODEOWNERS also covers workflows/ and deploy/
└── Makefile
```

---

## 3. Root files

### `.env.example` (compose; never commit `.env`)

```bash
PG_PASSWORD=change-me
BG_DATABASE_URL=postgresql+asyncpg://bg:change-me@timescaledb:5432/budgetguard
BG_VALKEY_URL=redis://valkey:6379/0
BG_PROMETHEUS_URL=http://prometheus:9090
BG_GITHUB_REPO=yourname/budgetguard
BG_GITHUB_REPO_ID=                  # gh api repos/yourname/budgetguard --jq .id
BG_GITHUB_OWNER_ID=                 # gh api users/yourname --jq .id
BG_GITHUB_INSTALLATION_ID=          # App settings -> Install App -> the number in the URL
BG_GITHUB_APP_ID=
BG_GITHUB_APP_PRIVATE_KEY=          # PEM, newlines as \n
BG_GITHUB_WEBHOOK_SECRET=
BG_GITHUB_WEBHOOK_SECRET_PREVIOUS=  # only during a rotation (security-and-api.md §6.1)
BG_ALERTMANAGER_TOKEN=              # same value in deploy/compose/bg_token
BG_DASHBOARD_TOKEN=
BG_APPROVER_IDS=[12345678]          # numeric GitHub user ids: gh api users/<login> --jq .id
BG_FAULT_TOKEN=                     # services' /admin/faults
OPENAI_API_KEY=                     # brief writer only; empty = template briefs
```

### `.github/CODEOWNERS`

```text
# The gate's behaviour changes only with a platform review and a passing backtest.
/policy/                      @yourname
/platform/budgetguard/gate.py @yourname
/platform/budgetguard/pricing.py @yourname
/slo/                         @yourname
/rules/generated/             @yourname
/.github/workflows/           @yourname
/deploy/                      @yourname
```

### `.gitignore`

```text
.env
.venv/
__pycache__/
.pytest_cache/
node_modules/
.next/
target/
reports/
*.kubeconfig
kubeconfig*
```

---

## 4. Platform skeleton

### `platform/pyproject.toml`

```toml
[project]
name = "budgetguard"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
  "fastapi==0.143.0",            # newest release older than two weeks on 24 Oct 2026 (Rev 2 said 0.128); re-check
  "uvicorn[standard]>=0.38",
  "pydantic>=2.12",
  "pydantic-settings>=2.11",
  "httpx>=0.28",
  "PyJWT[crypto]>=2.10",
  "redis>=6.4",                  # Valkey 9.1 speaks the same protocol
  "sqlalchemy[asyncio]>=2.0.44",
  "asyncpg>=0.30",
  "alembic>=1.17",
  "pyyaml>=6.0",
  "numpy>=2.3",
  "scipy>=1.16",
]

[project.optional-dependencies]
llm = ["openai>=2.0", "anthropic>=0.70"]   # only the brief-writer image installs these

[project.scripts]
bg = "budgetguard.rulegen.cli:main"

[dependency-groups]
dev = ["pytest>=8.4", "pytest-asyncio>=1.2", "ruff>=0.14", "mypy>=1.18"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 110
target-version = "py314"
```

### `platform/budgetguard/config.py`

```python
"""All configuration from the environment. No defaults for secrets: a missing secret fails at boot."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BG_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bg:bg@timescaledb:5432/budgetguard"
    valkey_url: str = "redis://valkey:6379/0"
    prometheus_url: str = "http://prometheus:9090"

    github_repo: str = Field(description="owner/name the App guards")
    github_repo_id: int                              # OIDC repository_id: survives renames, not re-creation
    github_owner_id: int                             # OIDC repository_owner_id
    github_installation_id: int                      # the only installation the App acts for
    github_app_id: str
    github_app_private_key: SecretStr
    github_webhook_secret: SecretStr
    github_webhook_secret_previous: SecretStr | None = None   # set only during a rotation
    alertmanager_token: SecretStr
    alertmanager_token_next: SecretStr | None = None          # set only during a rotation
    dashboard_token: SecretStr
    dashboard_token_next: SecretStr | None = None             # set only during a rotation
    approver_ids: tuple[int, ...] = ()               # numeric GitHub user ids; logins can be renamed
    policy_version: str = "unknown"                  # baked into the image: git log -1 --format=%H -- policy/

    prometheus_timeout_s: float = 3.0
    public_url: str = "https://budgetguard.example.dev"

    def webhook_secrets(self) -> list[str]:
        """Current secret first, then the previous one while a rotation is in progress."""
        secrets = [self.github_webhook_secret, self.github_webhook_secret_previous]
        return [s.get_secret_value() for s in secrets if s is not None]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def __getattr__(name: str) -> Settings:
    """`from budgetguard.config import settings` works (PEP 562) without reading the
    environment at import time, so tests can set env vars first."""
    if name == "settings":
        return get_settings()
    raise AttributeError(name)
```

### `platform/budgetguard/store.py`

```python
"""Persistence boundary. The API depends on this Protocol; PgStore implements it with the SQL in
budgetguard-data-layer/references/schema-and-queries.md, tests use an in-memory fake."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class DecisionRow:
    id: int
    service: str
    sha: str
    github_run_id: int | None
    verdict: str
    reason_code: str
    price_pct: float | None
    affordable_pct: float | None
    canary_profile: tuple[int, ...]
    policy_version: str
    inputs_hash: str
    inputs: dict[str, Any]
    decided_at: datetime
    repo: str
    installation_id: int | None
    overridable: bool               # verdict block, no override yet, run younger than 30 days


@dataclass(frozen=True, slots=True)
class IncidentEvent:
    fingerprint: str
    service: str
    alertname: str
    slo: str | None
    incident_type: str
    severity: str
    started_at: datetime
    resolved_at: datetime | None


class Store(Protocol):
    async def decision_for_run(self, service: str, run_id: int) -> DecisionRow | None: ...
    async def decision(self, decision_id: int) -> DecisionRow | None: ...
    async def upsert_incident(self, e: IncidentEvent) -> None: ...
    async def create_override(self, decision_id: int, approver_id: int, approver: str, reason: str) -> int: ...
    async def delete_override(self, override_id: int) -> None: ...
    async def open_override(self, service: str, sha: str) -> int | None: ...   # lane proof (query 4.10a)
    async def ping(self) -> None: ...
```

### `platform/budgetguard/queue.py`

```python
"""Valkey helpers. Key names follow the catalog in budgetguard-data-layer."""
from __future__ import annotations

import json
from typing import Any

ENQUEUE_ONCE = """
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', 604800) then
  redis.call('XADD', KEYS[2], 'MAXLEN', '~', 10000, '*', 'job', ARGV[1])
  return 1
end
return 0
"""


async def enqueue_once(r: Any, stream: str, key: str, job: dict) -> bool:
    """Delivery-ID dedupe and stream append in one round trip (GitHub redelivers webhooks)."""
    return bool(await r.eval(ENQUEUE_ONCE, 2, f"bg:delivery:{key}", stream, json.dumps(job)))


async def invalidate_verdicts(r: Any, service: str) -> int:
    """O(1) cache invalidation: verdict keys embed a per-service generation number
    (bg:verdict:{service}:{gen}:{sha}); bumping the generation orphans every cached verdict,
    which then expire on their 30 s TTL. No SCAN, no key set to maintain."""
    return int(await r.incr(f"bg:verdict-gen:{service}"))
```

### `platform/budgetguard/auth.py`

```python
"""Authentication for every caller that is not the GitHub webhook (HMAC lives in github_app.py).
Contract: budgetguard-admission-gate/references/security-and-api.md §3 and §6."""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable

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
    return any(s is not None and hmac.compare_digest(token.encode(), s.get_secret_value().encode())
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
                            headers={"Authorization": f"Bearer {user_token}", "Accept": "application/vnd.github+json"})
    if resp.status_code == 401:
        raise HTTPException(401, "GitHub token rejected")
    resp.raise_for_status()
    body = resp.json()
    uid, login = int(body["id"]), body["login"]
    await r.set(key, f"{uid}:{login}", ex=300)
    return uid, login
```

### `platform/budgetguard/edge.py`

```python
"""Public-path guard (security-and-api.md §2). The tunnel's path regex is the first filter; this
repeats it so a tunnel misconfiguration cannot expose internal routes. Cloudflare always sets
CF-Connecting-IP on requests that came through the edge, and nothing inside the cluster does."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PUBLIC = {("POST", "/v1/github/webhook"), ("GET", "/v1/decisions")}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def public_path_guard(request: Request, call_next):
        if "cf-connecting-ip" in request.headers and (request.method, request.url.path) not in PUBLIC:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return await call_next(request)
```

### `platform/budgetguard/incidents.py`

```python
"""Alertmanager webhook (v4 payload) -> incident events. Pure, so it is easy to test."""
from __future__ import annotations

from datetime import datetime

from .store import IncidentEvent

ZERO_TIME = "0001-01-01T00:00:00Z"     # Alertmanager's "not resolved" endsAt


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def events_from_payload(payload: dict) -> list[IncidentEvent]:
    out = []
    for a in payload.get("alerts", []):
        labels = a.get("labels", {})
        if "service" not in labels or "incident_type" not in labels:
            continue                            # not a BudgetGuard alert
        resolved = a.get("status") == "resolved"
        ends = a.get("endsAt") or ZERO_TIME
        out.append(IncidentEvent(
            fingerprint=a["fingerprint"],
            service=labels["service"],
            alertname=labels.get("alertname", "unknown"),
            slo=labels.get("slo"),
            incident_type=labels["incident_type"],
            severity=labels.get("severity", "ticket"),
            started_at=_ts(a["startsAt"]),          # with the fingerprint: identifies one occurrence
            resolved_at=_ts(ends) if resolved and not ends.startswith("0001") else None,
        ))
    return out
```

### `platform/budgetguard/api.py`

```python
"""HTTP surface other than the GitHub webhook. Auth per endpoint follows budgetguard-admission-gate §6."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import github_user, require_alertmanager, require_dashboard, verify_actions_oidc
from .config import get_settings
from .incidents import events_from_payload
from .queue import invalidate_verdicts
from .store import DecisionRow, Store

router = APIRouter()

BAKE = {(100,): "0s", (25, 100): "2m", (10, 50, 100): "5m", (5, 25, 50, 100): "10m"}
CONSERVATIVE = {"canary_profile": [5, 25, 50, 100], "bake": "10m"}


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
    profile = tuple(d.canary_profile) or tuple(CONSERVATIVE["canary_profile"])
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


@router.get("/v1/decisions/{decision_id}", dependencies=[Depends(require_dashboard)])
async def decision_detail(decision_id: int, s: Store = Depends(store)) -> dict:
    d = await s.decision(decision_id)
    if d is None:
        raise HTTPException(404, "no such decision")
    return _decision_json(d)


def _decision_json(d: DecisionRow) -> dict:
    return {"id": d.id, "service": d.service, "sha": d.sha, "verdict": d.verdict,
            "reason_code": d.reason_code, "price_pct": d.price_pct, "affordable_pct": d.affordable_pct,
            "canary_profile": list(d.canary_profile), "policy_version": d.policy_version,
            "inputs_hash": d.inputs_hash, "decided_at": d.decided_at.isoformat(),
            "overridable": d.overridable, "brief": None, "override": None, "inputs": d.inputs}
```

### `platform/budgetguard/main.py`

```python
"""App factory. Workers run the same image with a different command (see compose)."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from . import edge
from .api import router as api_router
from .config import get_settings
from .integrations.github_app import router as github_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.valkey = Redis.from_url(settings.valkey_url, decode_responses=True)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))
    from .pgstore import PgStore                         # real store; tests inject a fake instead
    app.state.store = await PgStore.connect(settings.database_url)
    from .integrations.callbacks import rerun_failed_jobs
    app.state.rerun = lambda d: rerun_failed_jobs(app.state.http, d)
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.valkey.aclose()
        await app.state.store.close()


def create_app(*, lifespan_override=None) -> FastAPI:
    app = FastAPI(title="BudgetGuard", version="rev2", lifespan=lifespan_override or lifespan,
                  docs_url=None, redoc_url=None)        # no public docs on the internet-facing app
    edge.install(app)                                    # public-path guard first
    app.include_router(github_router)
    app.include_router(api_router)
    return app


app = create_app()
```

`budgetguard/integrations/github_app.py` is `budgetguard-admission-gate/references/github-app.md`
(webhook route with size cap, dual secret and repo/installation check, `validate_job`, `app_jwt`,
down-scoped `installation_token`, `post_review`), unchanged. The gate worker below must call
`validate_job(job)` on every job it reads and skip a job whose `deployment_id` already has a
decision.

### `platform/budgetguard/integrations/callbacks.py`

```python
"""GitHub calls made after a decision. Uses the App installation token from github_app.py."""
from __future__ import annotations

import httpx

from ..store import DecisionRow
from ..config import get_settings
from .github_app import JobRejected, installation_token


async def rerun_failed_jobs(client: httpx.AsyncClient, d: DecisionRow) -> None:
    """Needs the App permission Actions: read & write. GitHub allows re-runs for 30 days."""
    assert d.installation_id is not None and d.github_run_id is not None
    if d.repo != get_settings().github_repo:            # stored inputs are re-checked before any token
        raise JobRejected("foreign repo")
    token = await installation_token(client, d.installation_id, {"actions": "write"})
    r = await client.post(f"https://api.github.com/repos/{d.repo}/actions/runs/{d.github_run_id}/rerun-failed-jobs",
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    r.raise_for_status()
```

### `platform/budgetguard/pgstore.py`

```python
"""PostgreSQL implementation of Store. SQL mirrors budgetguard-data-layer/references/schema-and-queries.md.
Covered by an integration test against the TimescaleDB image (testcontainers), not by unit tests."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .store import DecisionRow, IncidentEvent

DECISION_SQL = """
SELECT d.id, s.name AS service, d.sha, d.github_run_id, d.verdict, d.reason_code,
       d.price_pct::float8 AS price_pct, d.affordable_pct::float8 AS affordable_pct,
       d.canary_profile, d.policy_version, d.inputs_hash, d.inputs, d.decided_at,
       d.inputs -> 'github' ->> 'repo' AS repo,
       (d.inputs -> 'github' ->> 'installation_id')::bigint AS installation_id,
       (d.verdict = 'block'
        AND d.decided_at > now() - INTERVAL '30 days'
        AND NOT EXISTS (SELECT 1 FROM overrides o WHERE o.decision_id = d.id)) AS overridable
FROM decisions d JOIN services s ON s.id = d.service_id
"""


class PgStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @classmethod
    async def connect(cls, url: str) -> "PgStore":
        return cls(create_async_engine(url, pool_size=10, max_overflow=5, pool_pre_ping=True))

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> None:
        async with self.engine.connect() as c:
            await c.execute(text("SELECT 1"))

    @staticmethod
    def _row(m) -> DecisionRow:
        return DecisionRow(id=m["id"], service=m["service"], sha=m["sha"], github_run_id=m["github_run_id"],
                           verdict=m["verdict"], reason_code=m["reason_code"], price_pct=m["price_pct"],
                           affordable_pct=m["affordable_pct"], canary_profile=tuple(m["canary_profile"]),
                           policy_version=m["policy_version"], inputs_hash=m["inputs_hash"], inputs=m["inputs"],
                           decided_at=m["decided_at"], repo=m["repo"] or "",
                           installation_id=m["installation_id"], overridable=m["overridable"])

    async def decision(self, decision_id: int) -> DecisionRow | None:
        async with self.engine.connect() as c:
            m = (await c.execute(text(DECISION_SQL + " WHERE d.id = :id"), {"id": decision_id})).mappings().first()
        return self._row(m) if m else None

    async def decision_for_run(self, service: str, run_id: int) -> DecisionRow | None:
        q = DECISION_SQL + " WHERE s.name = :svc AND d.github_run_id = :run ORDER BY d.decided_at DESC LIMIT 1"
        async with self.engine.connect() as c:
            m = (await c.execute(text(q), {"svc": service, "run": run_id})).mappings().first()
        return self._row(m) if m else None

    async def upsert_incident(self, e: IncidentEvent) -> None:
        async with self.engine.begin() as c:
            await c.execute(text("""
                INSERT INTO incidents (service_id, fingerprint, alertname, slo, incident_type, severity,
                                       started_at, resolved_at)
                SELECT s.id, :fp, :alertname, :slo, :itype, :sev, :started, :resolved
                FROM services s WHERE s.name = :svc
                ON CONFLICT (fingerprint, started_at)
                DO UPDATE SET resolved_at = COALESCE(EXCLUDED.resolved_at, incidents.resolved_at)
            """), {"fp": e.fingerprint, "alertname": e.alertname, "slo": e.slo, "itype": e.incident_type,
                   "sev": e.severity, "started": e.started_at, "resolved": e.resolved_at, "svc": e.service})

    async def create_override(self, decision_id: int, approver_id: int, approver: str, reason: str) -> int:
        async with self.engine.begin() as c:
            return (await c.execute(text("""
                INSERT INTO overrides (decision_id, approver_id, approver, reason)
                VALUES (:d, :i, :a, :r) RETURNING id
            """), {"d": decision_id, "i": approver_id, "a": approver, "r": reason})).scalar_one()

    async def delete_override(self, override_id: int) -> None:
        async with self.engine.begin() as c:
            await c.execute(text("DELETE FROM overrides WHERE id = :id AND consumed_by_decision_id IS NULL"),
                            {"id": override_id})

    async def open_override(self, service: str, sha: str) -> int | None:
        async with self.engine.connect() as c:
            return (await c.execute(text("""
                SELECT o.id FROM overrides o
                JOIN decisions b ON b.id = o.decision_id JOIN services s ON s.id = b.service_id
                WHERE s.name = :svc AND b.sha = :sha
                  AND o.expires_at > now() AND o.consumed_by_decision_id IS NULL
                ORDER BY o.created_at DESC LIMIT 1
            """), {"svc": service, "sha": sha})).scalar_one_or_none()
```

### `platform/budgetguard/workers/gate_worker.py`

```python
"""Gate worker: the 7-step contract from budgetguard-admission-gate §5. Skeleton with the
order, locks, idempotency and failure handling in place; the input gatherers are the
functions named in the gate, data-layer and slo-math skills."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket

import httpx
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from ..config import get_settings
from ..integrations.github_app import JobRejected, validate_job

log = logging.getLogger("gate_worker")
STREAM, GROUP = "bg:gate-evaluations", "gate-workers"
CONSUMER = f"{socket.gethostname()}-{os.getpid()}"


async def ensure_group(r: Redis) -> None:
    try:
        await r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def handle(r: Redis, http: httpx.AsyncClient, msg_id: str, job: dict) -> None:
    service, sha = job["service"], job["sha"]
    # 2. single-flight per service: one Prometheus fan-out for N concurrent merges
    if not await r.set(f"bg:lock:eval:{service}", CONSUMER, nx=True, px=15_000):
        await asyncio.sleep(2)
        return                                    # not ACKed: XAUTOCLAIM or the next read retries it
    try:
        # 3. verdict cache, keyed by the per-service generation (bumped on every alert)
        gen = await r.get(f"bg:verdict-gen:{service}") or "0"
        cache_key = f"bg:verdict:{service}:{gen}:{sha}"
        cached = await r.get(cache_key)
        if cached:
            decision, facts = json.loads(cached), None
        else:
            # 4. gather inputs concurrently, 3 s each (integrity, page, remaining, history,
            #    change features, lane proof incl. override) -> DeployFacts -> GateInput
            facts = await gather_facts(http, job)                       # noqa: F821 - see skills
            decision = evaluate_facts(facts)                             # noqa: F821
            await r.set(cache_key, json.dumps(decision), ex=30)
        # 5. ONE transaction: decision row (ON CONFLICT github_delivery DO NOTHING),
        #    deployments row in_progress, override consumed if the lane was OVERRIDE
        decision_id = await record_decision(job, decision, facts)        # noqa: F821
        # 6. answer GitHub with the template brief; retried with jitter, decision already durable
        await post_with_retry(http, job, decision, decision_id)
        # 7. brief writer runs later; then ACK
        await r.xadd("bg:briefs", {"decision_id": str(decision_id)}, maxlen=10_000, approximate=True)
        await r.xack(STREAM, GROUP, msg_id)
    finally:
        await r.delete(f"bg:lock:eval:{service}")


async def post_with_retry(http: httpx.AsyncClient, job: dict, decision: dict, decision_id: int) -> None:
    from ..integrations.github_app import post_review
    comment = template_comment(decision, decision_id)                    # noqa: F821
    for attempt in range(12):                                           # ~30 minutes with backoff
        try:
            await post_review(http, job, decision["verdict"] != "block", comment)
            return
        except httpx.HTTPError as e:
            delay = min(300, 2 ** attempt) * random.uniform(0.5, 1.0)
            log.warning("callback failed (%s); retry in %.0fs", e, delay)
            await asyncio.sleep(delay)
    log.error("callback never delivered for decision %s; GitHub run stays waiting", decision_id)


async def run() -> None:
    s = get_settings()
    r = Redis.from_url(s.valkey_url, decode_responses=True)
    await ensure_group(r)
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as http:
        while True:
            # adopt jobs from crashed workers first, then read new ones
            _, claimed, _ = await r.xautoclaim(STREAM, GROUP, CONSUMER, min_idle_time=60_000, count=10)
            batches = [(STREAM, claimed)] if claimed else await r.xreadgroup(
                GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5_000) or []
            for _, messages in batches:
                for msg_id, fields in messages:
                    job = json.loads(fields["job"])
                    try:
                        validate_job(job)            # anything in Valkey may not come from GitHub (T-8)
                    except JobRejected as e:
                        log.error("job rejected, no token minted: %s", e)
                        await r.xadd("bg:gate-dead", {"job": fields["job"], "why": str(e)}, maxlen=10_000)
                        await r.xack(STREAM, GROUP, msg_id)
                        continue
                    try:
                        await handle(r, http, msg_id, job)   # first step inside: skip if deployment_id decided
                    except Exception:                                    # noqa: BLE001
                        log.exception("evaluation failed; left pending for XAUTOCLAIM")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
```

### `platform/budgetguard/rulegen/cli.py`

```python
"""`bg` (pyproject: bg = "budgetguard.rulegen.cli:main"). Interface fixed by budgetguard-slo-rules:

  bg generate ../slo -o ../rules/generated [--check] [--format prometheus|prometheus-operator]
  bg export sloth ../slo/orders-service.yaml > orders.sloth.yaml
  bg sync-slos                      # upsert slos rows; spec_hash detects drift
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(prog="bg")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="slo specs -> Prometheus rule files")
    g.add_argument("slo_dir", type=Path)
    g.add_argument("-o", "--out", type=Path, required=True)
    g.add_argument("--format", choices=("prometheus", "prometheus-operator"), default="prometheus")
    g.add_argument("--check", action="store_true", help="fail if committed rules differ")
    e = sub.add_parser("export", help="interop exports")
    e.add_argument("target", choices=("sloth",))
    e.add_argument("spec", type=Path)
    sub.add_parser("sync-slos", help="upsert slos rows from the specs")
    a = ap.parse_args()

    if a.cmd == "generate":
        from .generate import write_all          # wraps recording_groups/alert_groups/dump_rules
        return write_all(a.slo_dir, a.out, fmt=a.format, check=a.check)
    if a.cmd == "export":
        from .sloth import export_sloth
        sys.stdout.write(export_sloth(a.spec))
        return 0
    from ..slo_registry import sync_slos
    return sync_slos()


if __name__ == "__main__":
    sys.exit(main())
```

### `platform/migrations/versions/001_schema.py`

```python
"""001: relational schema + sli_buckets hypertable (DDL in sql/001_schema.sql, from
budgetguard-data-layer/references/schema-and-queries.md section 1)."""
from pathlib import Path

from alembic import op

revision, down_revision = "001", None
SQL = Path(__file__).resolve().parents[2] / "sql"


def upgrade() -> None:
    op.execute((SQL / "001_schema.sql").read_text())


def downgrade() -> None:
    raise NotImplementedError("forward-only: restore from the nightly pg_dump instead")
```

### `platform/migrations/versions/002_timeseries.py`

```python
"""002: compression, retention, continuous aggregates. CREATE MATERIALIZED VIEW ... WITH
(timescaledb.continuous) cannot run inside a transaction, hence autocommit_block."""
from pathlib import Path

from alembic import op

revision, down_revision = "002", "001"
SQL = Path(__file__).resolve().parents[2] / "sql"


def upgrade() -> None:
    statements = [s.strip() for s in (SQL / "002_timeseries.sql").read_text().split(";\n") if s.strip()]
    with op.get_context().autocommit_block():
        for stmt in statements:
            op.execute(stmt)


def downgrade() -> None:
    raise NotImplementedError("forward-only")
```

---

## 5. Tests

### `platform/tests/conftest.py`

```python
import os

# Settings are read lazily; set the environment before any budgetguard import.
os.environ.update({
    "BG_GITHUB_REPO": "ricky/budgetguard",
    "BG_GITHUB_REPO_ID": "1001",
    "BG_GITHUB_OWNER_ID": "2002",
    "BG_GITHUB_INSTALLATION_ID": "99",
    "BG_GITHUB_APP_ID": "123",
    "BG_GITHUB_APP_PRIVATE_KEY": "unused-in-tests",
    "BG_GITHUB_WEBHOOK_SECRET": "whsec",
    "BG_ALERTMANAGER_TOKEN": "amtoken",
    "BG_DASHBOARD_TOKEN": "dashtoken",
    "BG_DASHBOARD_TOKEN_NEXT": "dashtoken-next",
    "BG_APPROVER_IDS": "[501]",
})
```

### `platform/tests/test_api.py`

```python
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from budgetguard import auth
from budgetguard.incidents import events_from_payload
from budgetguard.main import create_app
from budgetguard.queue import ENQUEUE_ONCE
from budgetguard.store import DecisionRow

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)


class FakeValkey:
    def __init__(self):
        self.kv, self.streams = {}, {}

    async def eval(self, script, numkeys, *args):
        assert script == ENQUEUE_ONCE
        key, stream, job = args
        if key in self.kv:
            return 0
        self.kv[key] = "1"
        self.streams.setdefault(stream, []).append(json.loads(job))
        return 1

    async def incr(self, key):
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return self.kv[key]

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def ping(self):
        return True


def decision(**kw) -> DecisionRow:
    base = dict(id=4812, service="orders-service", sha="9f3c2ab" + "0" * 33, github_run_id=777,
                verdict="block", reason_code="OVER_BUDGET", price_pct=1.26, affordable_pct=1.0,
                canary_profile=(), policy_version="p" * 40, inputs_hash="h" * 64, inputs={},
                decided_at=NOW, repo="ricky/budgetguard", installation_id=99, overridable=True)
    return DecisionRow(**{**base, **kw})


class FakeStore:
    def __init__(self):
        self.decisions = {4812: decision(), 4813: decision(id=4813, verdict="admit", reason_code="WITHIN_BUDGET",
                                                           canary_profile=(10, 50, 100), github_run_id=778)}
        self.incidents, self.overrides = {}, []

    async def decision(self, i):
        return self.decisions.get(i)

    async def decision_for_run(self, service, run_id):
        return next((d for d in self.decisions.values() if d.service == service and d.github_run_id == run_id), None)

    async def upsert_incident(self, e):
        key = (e.fingerprint, e.started_at)
        prev = self.incidents.get(key)
        self.incidents[key] = replace(e, resolved_at=e.resolved_at or (prev.resolved_at if prev else None))

    async def create_override(self, decision_id, approver_id, approver, reason):
        self.overrides.append((decision_id, approver, reason))
        self.decisions[decision_id] = replace(self.decisions[decision_id], overridable=False)
        return len(self.overrides)

    async def delete_override(self, override_id):
        decision_id = self.overrides.pop(override_id - 1)[0]
        self.decisions[decision_id] = replace(self.decisions[decision_id], overridable=True)

    async def open_override(self, service, sha):
        return None

    async def ping(self):
        return None


def github_api(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/user":
        user = {"Bearer tok-alice": (501, "alice"), "Bearer tok-bob": (502, "bob"),
                "Bearer tok-renamed": (501, "alice-new-name"),                # same person, new login
                "Bearer tok-squatter": (777, "alice")}.get(request.headers["Authorization"])   # reused login
        return httpx.Response(200, json={"id": user[0], "login": user[1]}) if user else httpx.Response(401)
    return httpx.Response(404)


@pytest.fixture
def client():
    reruns = []

    @asynccontextmanager
    async def lifespan(app):
        app.state.valkey = FakeValkey()
        app.state.store = FakeStore()
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(github_api))
        app.state.flags = {}

        async def rerun(d):
            if app.state.flags.get("rerun_fails"):
                raise httpx.ConnectError("github down")
            reruns.append(d.github_run_id)
        app.state.rerun = rerun
        yield

    with TestClient(create_app(lifespan_override=lifespan)) as c:
        c.reruns = reruns
        yield c


# ---------------------------------------------------------------- GitHub webhook
def signed(body: bytes) -> dict:
    sig = "sha256=" + hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": sig, "X-GitHub-Event": "deployment_protection_rule",
            "X-GitHub-Delivery": "d-1", "Content-Type": "application/json"}


PROTECTION = {
    "environment": "production-orders-service",
    "deployment_callback_url": "https://api.github.com/repos/ricky/budgetguard/actions/runs/777/deployment_protection_rule",
    "installation": {"id": 99}, "deployment": {"sha": "9f3c2ab" + "0" * 33},
    "repository": {"full_name": "ricky/budgetguard"},
}


def test_webhook_rejects_bad_signature(client):
    body = json.dumps(PROTECTION).encode()
    h = signed(body) | {"X-Hub-Signature-256": "sha256=" + "0" * 64}
    assert client.post("/v1/github/webhook", content=body, headers=h).status_code == 401


def test_webhook_enqueues_once_per_delivery(client):
    body = json.dumps(PROTECTION).encode()
    assert client.post("/v1/github/webhook", content=body, headers=signed(body)).json() == {"accepted": True}
    assert client.post("/v1/github/webhook", content=body, headers=signed(body)).json() == {"accepted": False}
    job = client.app.state.valkey.streams["bg:gate-evaluations"][0]
    assert job["service"] == "orders-service" and job["run_id"] == 777


def test_webhook_ignores_non_production(client):
    body = json.dumps(PROTECTION | {"environment": "staging"}).encode()
    assert client.post("/v1/github/webhook", content=body, headers=signed(body)).json() == {"ignored": "staging"}


# ---------------------------------------------------------------- Alertmanager
AM = {"version": "4", "status": "firing", "alerts": [{
    "status": "firing", "fingerprint": "abc123", "startsAt": "2026-10-24T08:50:00Z",
    "endsAt": "0001-01-01T00:00:00Z",
    "labels": {"alertname": "ErrorBudgetBurnPage", "service": "orders-service", "slo": "availability",
               "severity": "page", "incident_type": "availability_burn"}}]}


def test_alertmanager_needs_token(client):
    assert client.post("/v1/webhooks/alertmanager", json=AM).status_code == 401


def test_alertmanager_upserts_and_invalidates_verdicts(client):
    h = {"Authorization": "Bearer amtoken"}
    assert client.post("/v1/webhooks/alertmanager", json=AM, headers=h).json() == {"incidents": 1}
    assert client.post("/v1/webhooks/alertmanager", json=AM, headers=h).json() == {"incidents": 1}
    assert len(client.app.state.store.incidents) == 1                   # redelivery is a no-op
    assert client.app.state.valkey.kv["bg:verdict-gen:orders-service"] == 2
    resolved = json.loads(json.dumps(AM))
    resolved["alerts"][0] |= {"status": "resolved", "endsAt": "2026-10-24T09:20:00Z"}
    client.post("/v1/webhooks/alertmanager", json=resolved, headers=h)
    (inc,) = client.app.state.store.incidents.values()
    assert inc.resolved_at is not None


def test_same_alert_recurring_is_a_new_incident():
    later = json.loads(json.dumps(AM))
    later["alerts"][0]["startsAt"] = "2026-10-31T08:50:00Z"
    a, b = events_from_payload(AM)[0], events_from_payload(later)[0]
    assert a.fingerprint == b.fingerprint and a.started_at != b.started_at


# ---------------------------------------------------------------- deploy job (OIDC)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def oidc(environment: str, repo: str = "ricky/budgetguard", run_id: int = 778, **claims) -> str:
    now = int(time.time())
    return jwt.encode({"iss": auth.ACTIONS_ISSUER, "aud": "budgetguard", "iat": now, "exp": now + 300,
                       "repository": repo, "repository_id": "1001", "repository_owner_id": "2002",
                       "environment": environment, "run_id": str(run_id), "ref": "refs/heads/main",
                       "workflow_ref": f"{repo}/.github/workflows/deploy.yml@refs/heads/main"} | claims,
                      KEY, algorithm="RS256")


@pytest.fixture
def jwks(monkeypatch):
    monkeypatch.setattr(auth, "_jwks_key", lambda token: KEY.public_key())


def test_decision_read_returns_profile_and_bake(client, jwks):
    r = client.get("/v1/decisions", params={"service": "orders-service", "run_id": 778},
                   headers={"Authorization": f"Bearer {oidc('production-orders-service')}"})
    assert r.status_code == 200 and r.json()["bake"] == "5m" and r.json()["canary_profile"] == [10, 50, 100]


@pytest.mark.parametrize("env, repo, claims", [
    ("production-auth-service", "ricky/budgetguard", {}),
    ("production-orders-service", "mallory/fork", {}),
    ("production-orders-service", "ricky/budgetguard", {"run_id": "999"}),        # another run's token
    ("production-orders-service", "ricky/budgetguard", {"repository_id": "9"}),   # re-created repo
    ("production-orders-service", "ricky/budgetguard",
     {"workflow_ref": "ricky/budgetguard/.github/workflows/evil.yml@refs/heads/main"}),
])
def test_decision_read_rejects_wrong_scope(client, jwks, env, repo, claims):
    r = client.get("/v1/decisions", params={"service": "orders-service", "run_id": 778},
                   headers={"Authorization": f"Bearer {oidc(env, repo, **claims)}"})
    assert r.status_code == 403


def test_blocked_run_never_gets_a_profile(client, jwks):
    r = client.get("/v1/decisions", params={"service": "orders-service", "run_id": 777},
                   headers={"Authorization": f"Bearer {oidc('production-orders-service', run_id=777)}"})
    assert r.status_code == 409


# ---------------------------------------------------------------- overrides
def override(client, token, reason="payments webhook broken; fix ships in this deploy"):
    return client.post("/v1/overrides", json={"decision_id": 4812, "reason": reason},
                       headers={"Authorization": "Bearer dashtoken", "X-GitHub-User-Token": token})


def test_override_requires_an_approver_resolved_by_the_api(client):
    assert override(client, "tok-bob").status_code == 403
    assert override(client, "tok-nobody").status_code == 401
    assert client.reruns == []


def test_override_records_and_reruns_once(client):
    r = override(client, "tok-alice")
    assert r.status_code == 200 and client.reruns == [777]
    assert client.app.state.store.overrides[0][:2] == (4812, "alice")
    assert override(client, "tok-alice").status_code == 409            # single use
    assert any(k.startswith("bg:ghuser:") and v == "501:alice" for k, v in client.app.state.valkey.kv.items())


def test_override_authorizes_by_user_id_not_login(client):
    assert override(client, "tok-squatter").status_code == 403        # someone else registered "alice"
    assert override(client, "tok-renamed").status_code == 200         # alice renamed her account


def test_failed_rerun_leaves_no_armed_override(client):
    client.app.state.flags["rerun_fails"] = True
    assert override(client, "tok-alice").status_code == 503
    store = client.app.state.store
    assert store.overrides == [] and store.decisions[4812].overridable


def test_public_path_guard_hides_internal_routes(client):
    h = {"Authorization": "Bearer dashtoken", "CF-Connecting-IP": "203.0.113.9"}
    assert client.get("/v1/decisions/4812", headers=h).status_code == 404
    assert client.get("/livez", headers=h).status_code == 404


def test_override_reason_too_short(client):
    assert override(client, "tok-alice", "urgent").status_code == 422


def test_dashboard_reads_need_the_service_token(client):
    assert client.get("/v1/decisions/4812").status_code == 401
    r = client.get("/v1/decisions/4812", headers={"Authorization": "Bearer dashtoken"})
    assert r.status_code == 200 and r.json()["overridable"] is True
    nxt = client.get("/v1/decisions/4812", headers={"Authorization": "Bearer dashtoken-next"})
    assert nxt.status_code == 200                                     # rotation window
```

---

## 6. Build order (Rev 2 §2 weeks, with the skill to open first)

| Week | Build | Open first | Exit test |
| --- | --- | --- | --- |
| 1 | Services, metrics, faults | `budgetguard-spring-services` | Every SLI series in Prometheus; `MetricsContractTest` green |
| 2 | Specs, generator, promtool tests, 35 d retention | `budgetguard-slo-rules` | `bg generate --check` + `promtool test rules` in CI |
| 3 | Tri-state SLIs, floors, probes, Alertmanager tree | `budgetguard-slo-math`, `budgetguard-slo-rules` | Killing a pod gives UNKNOWN, never HEALTHY |
| 4 | Schema, snapshotter, caggs, Grafana, dashboard cards | `budgetguard-data-layer`, `budgetguard-nextjs-dashboard` | TimescaleDB 30 d availability within 0.01 pp of Prometheus |
| 5 | Gate, App, OIDC, decision records, ETA, overrides by re-run, public deploy | `budgetguard-admission-gate`, `budgetguard-delivery-platform` | Demo freeze end to end on the public URL (MVP) |
| 6 | Pricing, lanes, `charge_origin` attribution, chargeback | `budgetguard-admission-gate` | §D worked example reproduces; dependency-outage demo does not freeze orders |
| 7 | Backtester, synthetic library, Freeze Brief, grounding eval | `budgetguard-mlops-evals` | Policy PR shows a backtest comment; brief eval gates CI |
| 8 | Budget-proportional canary with Traefik weights, Sloth parity, chaos drill, README | `budgetguard-delivery-platform` | README checklist (Rev 2 §11.3) complete |

---

## 7. README skeleton

```markdown
# BudgetGuard — error-budget admission control

[3-minute demo GIF: freeze → priced block → rollback admitted → unfreeze ETA]

Live: dashboard · Grafana (read-only) · a sample decision record. Demo numbers are synthetic.

## Why not Sloth or Pyrra?
They generate rules and show budgets; neither decides whether a deploy may proceed. …

## How it works (four planes, two enforcement points)
## SLOs and the arithmetic
## Quick start: `make up`, `make demo-fast-burn`, `make demo-dependency-outage`
## Kubernetes install
## Decisions log
- Protection rule instead of a curl step · TimescaleDB buckets instead of 30 d PromQL (measured: …)
- Fail-open vs fail-closed · charge_origin attribution · overrides by re-run
## Testing: rule tests · backtest report · grounding eval
## Known limits: single PostgreSQL · priors in pricing · …
```
