import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from budgetguard import auth
from budgetguard.incidents import events_from_payload
from budgetguard.integrations.prometheus import PrometheusClient
from budgetguard.main import create_app
from budgetguard.queue import ENQUEUE_ONCE
from budgetguard.rulegen.spec import load_dir
from budgetguard.store import DecisionRow

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
SLO_DIR = Path(__file__).resolve().parents[2] / "slo"


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
        self.decisions = {4812: decision(), 4813: decision(id=4813, verdict="admit",
                                                           reason_code="WITHIN_BUDGET",
                                                           canary_profile=(10, 50, 100), github_run_id=778)}
        self.incidents, self.overrides = {}, []

    async def decision(self, i):
        return self.decisions.get(i)

    async def decision_for_run(self, service, run_id):
        return next((d for d in self.decisions.values()
                     if d.service == service and d.github_run_id == run_id), None)

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

    async def ensure_policy_version(self, version):
        return None

    async def slo_windows(self, now):
        # orders availability: 1,000,000 requests, 300 bad (200 self) -> 70% left, 80% self-only
        return {("orders-service", "availability"): (1_000_000.0, 300.0, 200.0)}

    async def slo_daily(self, now, days):
        return {("orders-service", "availability"): [(now.date() - timedelta(days=1), 300.0, 1_000_000.0)]}

    async def eta_buckets(self, service, slo, now):
        return []

    async def open_incidents(self):
        return {"auth-service": {"id": 9, "alertname": "ErrorBudgetBurnPage",
                                 "started_at": NOW.isoformat()}}

    async def last_decisions(self):
        return {"orders-service": {"id": 4813, "verdict": "admit", "decided_at": NOW.isoformat()}}

    async def decision_page(self, service, before_id, limit):
        rows = sorted(self.decisions.values(), key=lambda d: d.id, reverse=True)
        rows = [d for d in rows if (service is None or d.service == service)
                and (before_id is None or d.id < before_id)]
        return rows[:limit]

    async def policy_versions(self):
        return [{"version": "p" * 40, "created_at": NOW.isoformat(), "backtest_markdown": None},
                {"version": "o" * 40, "created_at": NOW.isoformat(), "backtest_markdown": "| a |"}]


def prometheus_api(request: httpx.Request) -> httpx.Response:
    """vector(1) answers; every SLI query is empty, so every service reads NO_DATA -> unknown."""
    if request.url.params.get("query") == "vector(1)":
        return httpx.Response(200, json={"status": "success",
                                         "data": {"resultType": "vector",
                                                  "result": [{"metric": {}, "value": [0, "1"]}]}})
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": []}})


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
        app.state.specs = load_dir(SLO_DIR)

        def prom_or_down(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503) if app.state.flags.get("prom_down") else prometheus_api(request)
        app.state.prom = PrometheusClient("http://prometheus:9090",
                                          httpx.AsyncClient(transport=httpx.MockTransport(prom_or_down)))

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
    r = client.post("/v1/github/webhook", content=body, headers=signed(body))
    assert r.json() == {"ignored": "staging"}


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


# ---------------------------------------------------------------- W4: dashboard reads
DASH = {"Authorization": "Bearer dashtoken"}


def test_summary_needs_the_dashboard_token(client):
    assert client.get("/v1/services/summary").status_code == 401


def test_summary_has_one_card_per_spec_matching_the_zod_contract(client):
    cards = client.get("/v1/services/summary", headers=DASH).json()
    assert [c["service"] for c in cards] == ["auth-service", "inventory-service",
                                             "notifications-service", "orders-service"]
    keys = {"service", "state", "integrity", "objective_pct", "remaining_pct", "remaining_self_pct",
            "burn_rate_1h", "frozen", "unfreeze_eta", "open_incident", "last_decision", "daily_remaining"}
    assert all(set(c) == keys for c in cards)
    orders = cards[-1]
    assert orders["objective_pct"] == 99.9
    assert orders["remaining_pct"] == pytest.approx(70.0)          # from buckets, not Prometheus
    assert orders["remaining_self_pct"] == pytest.approx(80.0)
    assert orders["last_decision"]["id"] == 4813
    assert len(orders["daily_remaining"]) == 30
    assert orders["daily_remaining"][-3] is None                    # before any history
    assert orders["daily_remaining"][-2:] == [pytest.approx(70.0)] * 2   # yesterday rolls into today
    assert cards[0]["open_incident"]["alertname"] == "ErrorBudgetBurnPage"


def test_summary_without_signals_is_unknown_never_healthy(client):
    cards = client.get("/v1/services/summary", headers=DASH).json()
    assert {c["state"] for c in cards} == {"unknown"}
    assert {c["integrity"] for c in cards} == {"no_data"}


def test_summary_survives_prometheus_down(client):
    client.app.state.flags["prom_down"] = True
    cards = client.get("/v1/services/summary", headers=DASH).json()
    assert {c["integrity"] for c in cards} == {"stale"}
    assert cards[-1]["remaining_pct"] == pytest.approx(70.0)       # history still answers


def test_decisions_page_is_not_swallowed_by_the_id_route(client):
    r = client.get("/v1/decisions/page", headers=DASH)
    assert r.status_code == 200
    body = r.json()
    assert [d["id"] for d in body["items"]] == [4813, 4812]
    assert body["next_cursor"] is None
    assert "inputs" not in body["items"][0] and "brief" not in body["items"][0]


def test_decisions_page_cursor_and_filter(client):
    page = client.get("/v1/decisions/page?cursor=4813", headers=DASH).json()
    assert [d["id"] for d in page["items"]] == [4812]
    assert client.get("/v1/decisions/page?service=auth-service", headers=DASH).json()["items"] == []
    assert client.get("/v1/decisions/page?cursor=abc", headers=DASH).status_code == 422


def test_policy_versions_mark_the_running_one(client):
    rows = client.get("/v1/policy/versions", headers=DASH).json()
    assert [r["active"] for r in rows] == [False, False]                # BG_POLICY_VERSION unset
    assert set(rows[0]) == {"version", "created_at", "backtest_markdown", "active"}


def test_metrics_reports_prometheus_reachability(client):
    assert "bg_prometheus_reachable 1.0" in client.get("/metrics").text
    client.app.state.flags["prom_down"] = True
    assert "bg_prometheus_reachable 0.0" in client.get("/metrics").text


def test_metrics_is_not_public(client):
    assert client.get("/metrics", headers={"CF-Connecting-IP": "203.0.113.9"}).status_code == 404
