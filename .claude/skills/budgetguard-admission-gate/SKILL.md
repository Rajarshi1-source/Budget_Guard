---
name: budgetguard-admission-gate
description: Build and review BudgetGuard's deploy admission controller in FastAPI on Python 3.14 — the GitHub App used as a custom deployment protection rule (webhook HMAC, idempotent deliveries, installation-token callback that approves or rejects the job), the chain-of-responsibility gate (rollback lane, override lane, SLI integrity, incident-fix lane, active-page hard stop, priced admission), Beta-Binomial deploy pricing with change-risk multipliers, provable lanes, replayable decision records, GitHub OIDC auth, overrides by re-run, charge-origin attribution, Valkey single-flight and verdict cache, fail-open versus fail-closed. Use for ANY work on gate.py, pricing.py, lanes, github_app.py, gate_worker.py, decision records, overrides, endpoint auth, secrets or freeze logic, including why a deploy was blocked or whether a curl step is enough, even if the user just says freeze or gate. MANDATE - the verdict is deterministic and recorded before GitHub is answered; a rollback is always admitted; the LLM never decides.
---

# BudgetGuard — admission gate (Rev 2, D-1 / D-2 / D-8)

The gate answers one question per deploy: **can this service afford this change right
now?** It replaces Rev 1's `curl` step (B-6, B-7, B-8) with a server-side GitHub
deployment protection rule, and its binary 10% freeze with a priced admission.

Stack: Python 3.14, FastAPI (Rev 2 says 0.128.x; pin 0.143.0 or newer per the starter-code kickoff table), Pydantic 2, httpx, PyJWT[crypto], redis-py (Valkey 9.1
speaks the same protocol), SQLAlchemy 2 async + asyncpg. Lives in
`platform/budgetguard/{gate,pricing,lanes,records}.py`,
`platform/budgetguard/integrations/github_app.py`, `platform/budgetguard/auth.py`,
`platform/budgetguard/workers/gate_worker.py`.

Pairs with: `budgetguard-slo-math` (budget numbers, integrity), `budgetguard-data-layer`
(decisions, deployments, Valkey keys), `budgetguard-mlops-evals` (backtest replays this exact
`evaluate()`; Freeze Brief writer), `budgetguard-delivery-platform` (environments, workflow).

Reference files:

| File | Read it when |
| --- | --- |
| `references/github-app.md` | Editing the webhook route, job validation, App JWT, installation tokens or the callback |
| `references/security-and-api.md` | Adding or changing any endpoint, credential or secret; reviewing an auth PR; answering "what is public / who can call this / what if X leaks". It has the full endpoint contract, the secrets inventory with rotation steps, what the internet can reach, a 12-threat model and the security test list |

---

## 1. Invariants (why each exists)

| Invariant | Why |
| --- | --- |
| Verdict = pure function of a frozen `GateInput` | D-8: any decision recomputes identically from its stored inputs; the backtester replays the same code |
| Decision row committed **before** the GitHub callback | The audit trail can never disagree with what GitHub was told (strong consistency path, §8 of the plan) |
| Rollback lane runs **before** integrity | A team may choose fail-closed for auth without ever blocking its own rollback |
| An override is a lane on a **re-run**, never a late callback | GitHub consumes the callback when the gate answers `rejected`; see "Fourth flag" |
| Lanes are *proven*, not *claimed* | A label anyone can add is not a security boundary (see §4) |
| Service identity comes from the environment name (`production-<service>`) | Nothing in the workflow file is trusted |
| LLM output never reaches the verdict | Briefs are written after the commit, asynchronously |
| One evaluation per service at a time | 10 merges = 1 Prometheus fan-out (single-flight lock) |

---

## 2. Chain order — and a Rev 2 inconsistency to flag

The code in Rev 2 §4.11 runs `rollback → integrity → incident_fix → hard_stop → price`.
The prose in §3 Flow 1 step 4 says `integrity → lanes → hard stops → price`. **The code is
right** (§8's mitigation table depends on rollback-before-integrity); fix the prose in the
plan and say so in the README decisions log. This skill adds an `OVERRIDE` lane right after
rollback (fourth flag, §5), so the full chain is
`rollback → override → integrity → incident_fix → hard_stop → price`.

Second flag (shared with `budgetguard-slo-math`): Rev 2 treats `LOW_TRAFFIC` like `STALE`,
so a quiet service pages on-call for every night-time deploy. Recommended: only `NO_DATA`
and `STALE` short-circuit; `LOW_TRAFFIC` prices against the 30-day budget, skips the
short-window hard stop, and forces the conservative canary. The file below implements the
recommendation behind a policy flag so the user can choose.

```python
"""Chain-of-responsibility gate. Deterministic: same GateInput -> same Decision (D-8)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Callable, Literal

from .integrity import Integrity
from .pricing import affordable_pct

CONSERVATIVE = (5, 25, 50, 100)


class Verdict(StrEnum):
    ADMIT = "admit"
    ADMIT_UNVERIFIED = "admit_unverified"   # fail-open: admitted, audited, on-call paged
    BLOCK = "block"


class Lane(StrEnum):
    ROLLBACK = "rollback"
    INCIDENT_FIX = "incident_fix"
    OVERRIDE = "override"          # an unexpired, unused override row for this service and SHA


@dataclass(frozen=True, slots=True)
class GateInput:
    service: str
    policy_version: str
    integrity: Integrity
    remaining_pct: float | None            # 30-day, from buckets; may be negative
    active_page: bool                      # ALERTS{severity="page",service=...} firing
    lane: Lane | None                      # already PROVEN by lanes.py, never a raw label
    price_pct: float
    reserve_pct: float
    spend_fraction: float
    failure_mode: Literal["fail_open", "fail_closed"]
    low_traffic_prices_normally: bool = True   # the flagged recommendation; False = Rev 2 behaviour

    def digest(self) -> str:
        canonical = json.dumps(asdict(self), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    reason_code: str
    reason: str
    price_pct: float | None = None
    affordable_pct: float | None = None
    canary_profile: tuple[int, ...] = (25, 100)


Rule = Callable[[GateInput], Decision | None]


def canary_profile(remaining_pct: float | None) -> tuple[int, ...]:
    if remaining_pct is None or remaining_pct < 20:
        return CONSERVATIVE
    if remaining_pct < 50:
        return (10, 50, 100)
    return (25, 100)


def rollback_lane(i: GateInput) -> Decision | None:
    if i.lane is Lane.ROLLBACK:
        return Decision(Verdict.ADMIT, "ROLLBACK_LANE",
                        "Rollback to a previously healthy build is free", price_pct=0.0,
                        canary_profile=(100,))
    return None


def override_lane(i: GateInput) -> Decision | None:
    if i.lane is Lane.OVERRIDE:
        return Decision(Verdict.ADMIT, "OVERRIDE",
                        "Approved by a signed-in approver with a recorded reason; forced 5% canary",
                        price_pct=i.price_pct, canary_profile=CONSERVATIVE)
    return None


def integrity_check(i: GateInput) -> Decision | None:
    unknown = {Integrity.NO_DATA, Integrity.STALE}
    if not i.low_traffic_prices_normally:
        unknown.add(Integrity.LOW_TRAFFIC)
    if i.integrity not in unknown and i.remaining_pct is not None:
        return None
    if i.failure_mode == "fail_closed":
        return Decision(Verdict.BLOCK, "SLI_UNKNOWN",
                        f"SLI integrity is {i.integrity}; service is fail-closed")
    return Decision(Verdict.ADMIT_UNVERIFIED, "SLI_UNKNOWN",
                    f"SLI integrity is {i.integrity}; admitted under fail-open, on-call paged",
                    canary_profile=CONSERVATIVE)


def incident_fix_lane(i: GateInput) -> Decision | None:
    if i.lane is Lane.INCIDENT_FIX:
        return Decision(Verdict.ADMIT, "INCIDENT_FIX_LANE",
                        "Proven fix for the active incident; forced 5% canary",
                        price_pct=i.price_pct, canary_profile=CONSERVATIVE)
    return None


def hard_stop(i: GateInput) -> Decision | None:
    if i.integrity is Integrity.LOW_TRAFFIC:
        return None                         # short windows are not meaningful here
    if i.active_page:
        return Decision(Verdict.BLOCK, "ACTIVE_PAGE",
                        f"{i.service} has a firing page-level burn alert")
    return None


def priced_admission(i: GateInput) -> Decision:
    assert i.remaining_pct is not None
    afford = affordable_pct(i.remaining_pct, i.reserve_pct, i.spend_fraction)
    profile = CONSERVATIVE if i.integrity is Integrity.LOW_TRAFFIC else canary_profile(i.remaining_pct)
    if i.price_pct <= afford:
        return Decision(Verdict.ADMIT, "WITHIN_BUDGET",
                        f"Price {i.price_pct:.2f}% <= affordable {afford:.2f}%",
                        price_pct=i.price_pct, affordable_pct=afford, canary_profile=profile)
    return Decision(Verdict.BLOCK, "OVER_BUDGET",
                    f"Price {i.price_pct:.2f}% > affordable {afford:.2f}%",
                    price_pct=i.price_pct, affordable_pct=afford)


CHAIN: tuple[Rule, ...] = (rollback_lane, override_lane, integrity_check, incident_fix_lane, hard_stop)


def evaluate(i: GateInput) -> Decision:
    for rule in CHAIN:
        decision = rule(i)
        if decision is not None:
            return decision
    return priced_admission(i)
```

---

## 3. Pricing (D-2) — `pricing.py`

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChangeFeatures:
    lines_changed: int
    touches_migration: bool
    config_only: bool
    dependency_bump: bool


@dataclass(frozen=True, slots=True)
class PricingPolicy:
    prior_alpha: float = 2.0          # Beta(2, 18): prior mean change-failure rate 10%
    prior_beta: float = 18.0
    default_failure_cost_pct: float = 5.0
    w_config_only: float = 0.5
    w_migration: float = 2.0
    w_dependency: float = 1.5
    size_tiers: tuple[tuple[int, float], ...] = ((200, 1.0), (500, 1.25))
    size_max: float = 1.5
    multiplier_cap: float = 5.0


def posterior_cfr(failures: int, deploys: int, p: PricingPolicy) -> float:
    return (p.prior_alpha + failures) / (p.prior_alpha + p.prior_beta + deploys)


def size_factor(lines: int, p: PricingPolicy) -> float:
    for limit, factor in p.size_tiers:
        if lines <= limit:
            return factor
    return p.size_max


def risk_multiplier(f: ChangeFeatures, p: PricingPolicy) -> float:
    m = size_factor(f.lines_changed, p)
    if f.config_only:
        m *= p.w_config_only
    if f.touches_migration:
        m *= p.w_migration
    if f.dependency_bump:
        m *= p.w_dependency
    return min(m, p.multiplier_cap)


def price_pct(f: ChangeFeatures, failures: int, deploys: int,
              median_failure_cost_pct: float | None, p: PricingPolicy) -> float:
    """Expected budget cost of this deploy, in percent of the 30-day budget."""
    cost = median_failure_cost_pct if median_failure_cost_pct is not None else p.default_failure_cost_pct
    return posterior_cfr(failures, deploys, p) * risk_multiplier(f, p) * cost


def affordable_pct(remaining_pct: float, reserve_pct: float, spend_fraction: float) -> float:
    return max(0.0, spend_fraction * (remaining_pct - reserve_pct))
```

Worked example to keep in tests (Rev 2 §D): orders-service, n = 30, f = 3, prior Beta(2,18)
→ CFR = 5/50 = 10%; median failure cost 4.2%; reserve 5%; k = 0.5.

| Remaining | Change | m | Price | Affordable | Verdict |
| --- | --- | --- | --- | --- | --- |
| 18% | 40 lines | 1.0 | 0.42% | 6.5% | admit |
| 7% | 40 lines | 1.0 | 0.42% | 1.0% | admit (Rev 1 froze) |
| 7% | 900 lines + migration | 1.5 × 2.0 = 3.0 | 1.26% | 1.0% | block |
| 7% | rollback (proven) | — | 0 | — | admit |

Note the 900-line row: the multiplier is `size_max (1.5) × w_migration (2.0) = 3.0`, which is
how the plan's table gets m = 3.0. Keep that derivation in a test so a tier change can't
silently move the example.

Feature extraction (`changes.py`): list the merged PR's files for the deploy SHA
(`GET /repos/{repo}/commits/{sha}/pulls`, then paginated `GET /pulls/{n}/files`), filter to
`services/<service>/**`, and set:

- `lines_changed` = Σ(additions + deletions) under the service path
- `touches_migration` = any path matching `db/migration/`, `V\d+__.*\.sql`, `alembic/versions/`
- `config_only` = every path ends in `.yml`, `.yaml` or `.properties`
- `dependency_bump` = every path is `pom.xml`, `package.json`, lockfiles

No PR (direct push) → price with `size_max` and say so in the decision reason.

---

## 4. Lanes must be proven (gap in Rev 2, filled here)

Rev 2 names the lanes but not how they are verified. A label is self-asserted by the person
who benefits from it, so the lane proofs are:

| Lane | Proof | Stored as |
| --- | --- | --- |
| Rollback | The deploy's **service subtree SHA** (git tree object for `services/<service>`) equals the subtree SHA of an earlier deployment with `status = success` and `is_change_failure = false`. A `git revert` or a re-run of an old commit both produce a matching tree; a "rollback" that sneaks in new code cannot. | `deployments.service_tree_sha` (new column — flag to `budgetguard-data-layer`) |
| Incident fix | PR body references an **open** BudgetGuard incident (`Fixes BG-INC-<id>`) whose `service_id` is this service, its `alertname` is **firing for this service in Prometheus `ALERTS`** at decision time (an incident row alone can be forged through the Alertmanager webhook; `security-and-api.md` T-6), and the PR diff is ≤ 400 lines | `decisions.inputs.lane_evidence` |
| Security (optional) | Author is `dependabot[bot]` or `renovate[bot]` and the PR links a GitHub security advisory | treated as incident-fix (forced canary) |
| Override | An `overrides` row for this service and SHA with `expires_at > now()` and `consumed_by_decision_id IS NULL`, written by the API after it resolved the approver's GitHub login | `overrides.consumed_by_decision_id` set in the same transaction as the new decision |

```python
# lanes.py — subtree SHA via the git trees API (no checkout needed)
async def service_tree_sha(gh: GitHubClient, repo: str, sha: str, service: str) -> str | None:
    commit = await gh.get(f"/repos/{repo}/git/commits/{sha}")
    tree_sha = commit["tree"]["sha"]
    for part in ("services", service):
        tree = await gh.get(f"/repos/{repo}/git/trees/{tree_sha}")
        entry = next((e for e in tree["tree"] if e["path"] == part and e["type"] == "tree"), None)
        if entry is None:
            return None
        tree_sha = entry["sha"]
    return tree_sha
```

---

## 5. GitHub App protection rule — `integrations/github_app.py`

App settings: register the App as **private** ("Only on this account"); permissions
**Deployments: read & write**, **Actions: read & write** (re-run
after an override), **Contents: read**,
**Pull requests: read & write** (PR file list; Freeze Brief comments); subscribe to
**Deployment protection rule**, **Deployment status** (DORA, Flow 5) and **Pull request**. Enable the App on each
`production-<service>` environment, and untick "allow administrators to bypass" if you want
the break-glass to be visible as an override rather than silent.

The complete module (webhook route, `verify_signature`, `validate_job`, `app_jwt`,
`installation_token`, `post_review`) is in **`references/github-app.md`**. The rules it follows:

| Step | Rule |
| --- | --- |
| Size | Body over 2 MiB → 413 before reading further |
| Verify | HMAC-SHA256 of the **raw body** with `compare_digest`, before any JSON parsing; current and previous secret accepted during a rotation |
| Scope | `installation.id` and `repository.full_name` must be the configured ones, else `{"ignored": "repository"}` |
| Filter | Only `deployment_protection_rule` events for environments named `production-<service>`; the service comes from the environment name, never from the workflow |
| Identify | `run_id` parsed from `deployment_callback_url` (`/actions/runs/<id>/deployment_protection_rule`); `deployment.id` carried as the worker's second dedupe key (the delivery header is not signed) |
| Enqueue | `enqueue_once(valkey, "bg:gate-evaluations", key=X-GitHub-Delivery, job)`: redeliveries are no-ops |
| Answer | 200 within about a second; the worker decides and calls back |
| Callback | `validate_job` again → App JWT (RS256, `iat` 60 s in the past, 9-minute expiry) → installation token **down-scoped** to this repo and `deployments: write` → `POST callback_url` with `environment_name`, `state` approved or rejected, `comment` ≤ 1000 chars |

### The worker — `workers/gate_worker.py` (outline the order, it is the contract)

1. `XREADGROUP bg:gate-evaluations gate-workers` → job; `XAUTOCLAIM` anything idle > 60 s.
   `validate_job(job)` first: repo, installation, environment/service and the exact
   callback URL for this run. A rejected job is dead-lettered and **no token is minted**, so
   whoever can write to Valkey cannot steer an installation token to their own URL. Skip the
   job (record nothing, post nothing) if a decision already exists for its `deployment_id`.
2. `SET bg:lock:eval:{service} NX PX 15000`; if held, re-queue with 2 s delay.
3. Cache check `bg:verdict:{service}:{gen}:{sha}` (30 s), where `gen` is
   `bg:verdict-gen:{service}`, bumped by every Alertmanager delivery (O(1) invalidation). Hit → reuse the *decision*, still write a
   new decision row for this delivery (one row per GitHub request).
4. Gather inputs concurrently with `asyncio.TaskGroup` and 3 s Prometheus timeouts:
   integrity, active page, 30-day remaining (buckets; fallback
   `bg:error_ratio:increase30d`), deploy history, change features, lane proof. Under
   `attribution: charge_origin` (below), "active page" and "remaining" are the
   self-inflicted versions.

### Third flag — D-3 is promised but not wired into the gate

Rev 2 §D says "when auth fails, orders is not frozen for auth's outage". But the gate's
inputs in Rev 2 are `remaining_pct` from **all** bad events and `active_page` from **any**
firing page on the service, and the burn alerts are built on `bg:error_ratio` (all 5xx).
During an auth outage, orders pages and the `ACTIVE_PAGE` hard stop freezes orders: the
exact victim-freeze D-3 claims to remove. The backtester's "dependency outage" scenario
fails against Rev 2 as written, which is how you would discover it.

Fix behind a policy switch in `policy/freeze-policy.yaml`:

```yaml
attribution: charge_origin      # charge_origin (D-3, recommended) | charge_victim (Rev 2 as written)
self_page_share: 0.5            # a page is "self" when >= 50% of the last hour's bad events are self
```

| Input | `charge_victim` | `charge_origin` |
| --- | --- | --- |
| `active_page` | `ALERTS{alertstate="firing",severity="page",service=s}` | that, **and** `bg:self_error_ratio:rate1h / bg:error_ratio:rate1h >= self_page_share` for the same `service, slo` |
| `remaining_pct` | `1 - sum(bad)/sum(total) / allowed` | `1 - sum(self_bad)/sum(total) / allowed` |
| Culprit's budget | unchanged | unchanged; the culprit's own self-inflicted errors already burn its budget |

Users still felt every error, so the dashboard keeps showing the all-errors budget next to
the self-only one (`budgetguard-data-layer` query 4.1). The gate decides on the self-only
number because a freeze is a tool for stopping the team that *caused* the burn. Store
`attribution` in the decision inputs so a replay knows which number was used.
5. Build `DeployFacts` (raw facts) and from them `GateInput`, `evaluate()`; store
   `inputs = {facts, gate_input, lane_evidence, github: {repo, installation_id, environment,
   callback_url}}` so the backtester can replay leak-free and an override can re-run the job
   (`budgetguard-mlops-evals`). Then **one transaction**: insert `decisions`
   (`ON CONFLICT (github_delivery) DO NOTHING`), insert `deployments` row `in_progress`.
6. `post_review()` with retries (jittered backoff, up to 30 min). Comment = template brief
   (price, affordable, culprit, ETA, decision URL) — never wait for the LLM here.
7. `XADD bg:briefs` with the decision id; `XACK`; release lock.

---

## 6. Auth for everything that is not the webhook (fixes B-8)

Summary only. Request and response shapes, status codes, the public-path list, secrets
rotation and the threat model are in **`references/security-and-api.md`**; change that file
in the same PR as any endpoint or credential change.

| Endpoint | Caller | Auth | Public? |
| --- | --- | --- | --- |
| `POST /v1/github/webhook` | GitHub | HMAC-SHA256 on the raw body (dual secret while rotating); repo + installation check | yes (tunnel) |
| `GET /v1/decisions?service=&run_id=` | deploy job | GitHub OIDC JWT: `iss`, `aud=budgetguard`, `repository` + `repository_id` + `repository_owner_id`, `environment` = `production-<service>`, `run_id` = the query's run, `ref` = main, `workflow_ref` = `deploy.yml@main` | yes (tunnel) |
| `POST /v1/webhooks/alertmanager` | Alertmanager | shared bearer secret; NetworkPolicy restricts the source | no |
| `POST /v1/overrides` | human, via dashboard (BFF) | dashboard service bearer **and** `X-GitHub-User-Token` (the user's OAuth token); the API resolves the user itself with `GET https://api.github.com/user` (cached 5 min under a SHA-256 of the token), so a compromised dashboard cannot name an arbitrary approver; the user's **numeric id** must be in `approver_ids` (logins can be renamed and re-registered); reason ≥ 20 chars | no |
| `GET /v1/services/summary`, `/v1/decisions/page`, `/v1/decisions/{id}`, `POST /v1/decisions/{id}/replay`, `GET /v1/policy/versions` | dashboard (BFF) | dashboard service bearer; read-only except replay, which recomputes and writes nothing | no |

A public-path guard middleware returns 404 for any other path that arrives through the
Cloudflare edge (`CF-Connecting-IP` present), so a tunnel misconfiguration cannot expose
internal routes.

`verify_actions_oidc(token, expected_env, run_id)` checks exactly the claims in the table
above with `algorithms=["RS256"]` fixed and 60 s leeway; the tested code is in
`security-and-api.md` §6.2. The starter code's `auth.py` and its test helper `oidc()` predate
the extra claims; update both in week 5 (`security-and-api.md` §11 lists every such change).

Overrides never post to the old callback (see the fourth flag): the API writes an
`overrides` row (approver resolved from their own GitHub token, reason ≥ 20 chars, expires in
30 minutes, single use), then calls `POST /repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs`
with the installation token. The re-run raises a fresh protection-rule request; `lanes.py`
finds the unexpired, unconsumed override for this service and SHA and sets
`lane = Lane.OVERRIDE`; the decision row marks the override consumed. If the re-run call
fails, the API deletes the override row and returns 503, so no armed override is left behind.
Break-glass: on
recovery a reconciler lists GitHub deployments per environment and records any deployment
without a matching decision as an override with `approver = 'github-admin-bypass'`. Three
more reconcilers run in the same worker (`security-and-api.md` T-1, T-4): failed-webhook
redelivery (GitHub never redelivers on its own), environment drift (App removed from a
protection rule, admin bypass re-enabled → page `GateConfigDrift`), and cluster digest drift
(a Rollout running a digest no decision admitted → recorded as `cluster-admin-drift`).

### Fourth flag — an override cannot use a callback that was already answered

Rev 2 §8 says an override "posts the callback, so the approver sees the deploy proceed
immediately". But the gate answers `rejected` within seconds of a block, and GitHub then
fails the job; that callback is spent. Holding every blocked run open instead (to keep the
callback usable) would leave jobs waiting for days and hide the rejection from the PR.
Fix: override = time-boxed override row + **re-run failed jobs**, admitted through an
`OVERRIDE` lane placed right after the rollback lane (an approver can break glass even when
the SLI is UNKNOWN and the service is fail-closed). The App needs **Actions: read & write**
for the re-run. Every override is still a decision row, so it is replayable and shows up in
the backtest as a lane case.

---

## 7. Failure behaviour (say both cases in interviews)

| Situation | Behaviour |
| --- | --- |
| Prometheus unreachable | integrity `STALE` → service `failure_mode`: fail-open = `admit_unverified` + page `BudgetGuardBlind`; rollback still admitted first |
| BudgetGuard down | GitHub keeps the job waiting (an unanswered rule is fail-closed). Admin bypass is the audited break-glass |
| Valkey down | evaluate inline in the request handler (< 10 s); PostgreSQL `UNIQUE(github_delivery)` dedupes |
| GitHub callback fails | decision already committed; retry with backoff |

### As built in W5 (31 Oct 2026, verified against TimescaleDB 2.31 + Valkey 9.1 in CI's `integration` job)

Files: `gate.py`, `pricing.py`, `policy.py`, `records.py` (`DeployFacts`, `to_gate_input`, JSON round
trip, `replay`), `lanes.py`, `gather.py`, `briefs.py` (template), `workers/gate_worker.py`,
`workers/reconcilers.py`. What the sketches above do not say:

| Point | Rule |
| --- | --- |
| One GateInput per service | worst integrity across its SLOs; the most constrained SLO's budget; any SLO's page. Under `charge_origin` a page counts when that SLO's self share >= `self_page_share`; an SLI with no origin label (latency) counts as self |
| Policy vs spec | reserve and spend fraction come from `policy/freeze-policy.yaml`; a test fails if a spec's `gate:` block disagrees |
| PostgreSQL down | record -> `bg:records-pending` **before** the callback, replayed later; both stores down -> no answer (503 inline) |
| What counts as "down" | `OSError, TimeoutError, OperationalError, InterfaceError` (`db.DB_DOWN`). Never `DBAPIError`: it also covers SQL bugs, and the first integration run buffered a type bug as an outage |
| Crash between commit and callback | the redelivered job finds the decision; if `callback_state != 'sent'` it re-sends `inputs.comment`, never re-decides |
| Bad App key | `AppMisconfigured`: callback fails once, `failed` recorded, no retry loop |
| Cache | never stores an override decision (single use) |
| Deployments | a row only for admitted decisions (a blocked deploy never ran) |
| asyncpg arrays | pass `canary_profile` as a Python list; a `'{25,100}'` string is rejected |
| Admin bypass | synthetic `ADMIN_BYPASS` decision keyed `bypass:<deployment id>` + overrides row (`decision_id` is NOT NULL) |

### As built in W6 (31 Oct 2026)

| Point | Rule |
| --- | --- |
| Features | `changes.py`: merged PR for the SHA, paginated files, filtered to `services/<service>/`; no PR or a malformed answer -> `size_max`, with the note in the comment and `inputs.change` |
| Token | one per job, `contents: read` + `pull_requests: read` |
| Lane order | rollback > override > incident fix; the security lane is recorded as `INCIDENT_FIX` with `kind: security` |
| Incident fix | `Fixes BG-INC-<id>` + open incident on THIS service + its alertname firing in `ALERTS` for this service + PR <= 400 lines; the alertname is validated as a metric name before it enters PromQL |
| History | query 4.7 with the leak rule (`finished_at + 60 min <= decided_at`); the §D table reproduces from 30 fixture rows (integration test) |
| Change failure | `workers/deploy_events.py`: page incident in the window AND `self_bad / bad >= self_page_share`; the window's self-inflicted cost equals `100 * budget_spent(burn, 1 h)` |

## 8. Tests to keep

- Every row of the §3 worked example as a parametrized test.
- `evaluate(i) == evaluate(i)` and `i.digest()` stable across processes (no dict-order leaks).
- Rollback with `STALE` + `fail_closed` → admit. Active page + incident-fix lane → admit.
- `LOW_TRAFFIC` with the flag on → priced, conservative canary; flag off → Rev 2 behaviour.
- Webhook: bad signature 401; redelivery returns `accepted: false`; non-production env ignored.
- Override lane with `NO_DATA` + `fail_closed` + active page → admit, conservative canary.
- An override row is single-use: a second re-run of the same SHA is priced normally.
- Incident-fix lane with an open incident row but no firing `ALERTS` series → priced normally.
- `validate_job` rejects a foreign callback host, repo, installation or run id, and no token
  is requested. The full security test list is `security-and-api.md` §9.

## 9. Interview soundbites

- "The gate isn't a step in the YAML — it's a deployment protection rule in repo settings,
  and the kubeconfig only exists inside that environment. Delete the line and you lose the keys."
- "Every deploy has a price: posterior change-failure rate times what a failure usually costs.
  Small safe changes ship at 7% budget; a big migration waits."
- "Rollbacks are free, and *provably* rollbacks: the service's git subtree must match a build
  that was healthy before."
- "The decision is written to Postgres before GitHub hears it, and it recomputes byte-for-byte
  from its inputs."
- "Freeze the culprit, not the victim: the gate reads the self-inflicted budget, so an auth
  outage freezes auth, not orders. The backtester has a scenario that fails if that regresses."
- "An override can't approve a job GitHub already failed, so it's a single-use, 30-minute
  record plus a re-run; the re-run is admitted on an override lane and is itself a decision row."
- "Only two API paths are on the internet, and each one proves its caller: GitHub signs the
  webhook, and the deploy job brings an OIDC token bound to its repo, environment and run.
  Everything else answers 404 at the edge and again inside the app."
- "Nothing from the queue is trusted with a credential: the worker re-checks that the
  callback URL is GitHub's URL for this repo and this run before it mints a token."
