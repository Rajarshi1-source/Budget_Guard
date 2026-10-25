"""Security contract (security-and-api.md §9): webhook HMAC with rotation, repo/installation scope,
size cap, job re-validation before any token is minted, down-scoped tokens, and every OIDC claim."""
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from budgetguard import auth
from budgetguard.config import get_settings
from budgetguard.integrations import github_app as gh

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()
NEW, OLD = b"whsec", b"whsec-old"                         # conftest: current and previous webhook secret


@pytest.fixture(autouse=True)
def app_key_and_previous_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "github_app_private_key", SecretStr(PEM))
    monkeypatch.setattr(s, "github_webhook_secret_previous", SecretStr(OLD.decode()))


CALLBACK = "https://api.github.com/repos/ricky/budgetguard/actions/runs/777/deployment_protection_rule"
PAYLOAD = {"action": "requested", "environment": "production-orders-service",
           "deployment_callback_url": CALLBACK,
           "installation": {"id": 99}, "deployment": {"id": 555, "sha": "9f3c2ab" + "0" * 33},
           "repository": {"full_name": "ricky/budgetguard"}}


class FakeValkey:
    def __init__(self):
        self.keys, self.stream = set(), []

    async def eval(self, script, n, key, stream, job):
        if key in self.keys:
            return 0
        self.keys.add(key)
        self.stream.append(json.loads(job))
        return 1


@pytest.fixture
def client():
    @asynccontextmanager
    async def lifespan(app):
        app.state.valkey = FakeValkey()
        yield
    app = FastAPI(lifespan=lifespan)
    app.include_router(gh.router)
    with TestClient(app) as c:
        yield c


def post(client, payload, secret=NEW, delivery="d-1", event="deployment_protection_rule", body=None):
    body = body if body is not None else json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return client.post("/v1/github/webhook", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json"})


def test_current_and_previous_secret_accepted_unknown_rejected(client):
    assert post(client, PAYLOAD).json() == {"accepted": True}
    assert post(client, PAYLOAD, secret=OLD, delivery="d-2").json() == {"accepted": True}
    assert post(client, PAYLOAD, secret=b"nope", delivery="d-3").status_code == 401


def test_redelivery_is_a_noop_and_job_carries_deployment_id(client):
    post(client, PAYLOAD)
    assert post(client, PAYLOAD).json() == {"accepted": False}
    (job,) = client.app.state.valkey.stream
    assert job["deployment_id"] == 555 and job["run_id"] == 777 and job["service"] == "orders-service"


def test_foreign_repo_or_installation_ignored(client):
    foreign_repo = PAYLOAD | {"repository": {"full_name": "mallory/x"}}
    assert post(client, foreign_repo).json() == {"ignored": "repository"}
    foreign_install = PAYLOAD | {"installation": {"id": 1}}
    assert post(client, foreign_install, delivery="d-9").json() == {"ignored": "repository"}
    assert client.app.state.valkey.stream == []


def test_non_production_env_and_other_events_ignored(client):
    assert post(client, PAYLOAD | {"environment": "staging"}).json() == {"ignored": "staging"}
    assert post(client, PAYLOAD, event="ping", delivery="d-4").json() == {"ignored": "ping"}


def test_oversized_body_413(client):
    big = json.dumps(PAYLOAD | {"pad": "x" * (3 * 1024 * 1024)}).encode()
    assert post(client, None, body=big).status_code == 413


def test_unexpected_callback_shape_is_a_failed_delivery(client):
    bad = PAYLOAD | {"deployment_callback_url": "https://api.github.com/repos/ricky/budgetguard/other/777"}
    assert post(client, bad).status_code == 422


def job(**kw):
    base = {"service": "orders-service", "environment": "production-orders-service", "callback_url": CALLBACK,
            "run_id": 777, "installation_id": 99, "repo": "ricky/budgetguard", "sha": "a" * 40,
            "deployment_id": 555, "delivery": "d"}
    return base | kw


@pytest.mark.parametrize("bad", [
    {"callback_url": "https://attacker.example/repos/ricky/budgetguard/actions/runs/777/deployment_protection_rule"},
    {"callback_url": "https://api.github.com.attacker.example/repos/ricky/budgetguard/actions/runs/777/deployment_protection_rule"},
    {"callback_url": CALLBACK.replace("777", "778")},
    {"repo": "mallory/budgetguard"},
    {"installation_id": 1},
    {"service": "auth-service"},
])
def test_validate_job_rejects_injected_jobs(bad):
    with pytest.raises(gh.JobRejected):
        gh.validate_job(job(**bad))


def test_validate_job_accepts_the_real_one():
    gh.validate_job(job())


async def test_post_review_never_mints_a_token_for_a_foreign_url():
    calls = []
    def record(r: httpx.Request) -> httpx.Response:
        calls.append(r)
        return httpx.Response(201, json={"token": "t"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    with pytest.raises(gh.JobRejected):
        await gh.post_review(client, job(callback_url="https://attacker.example/x"), True, "ok")
    assert calls == []


async def test_installation_token_is_down_scoped():
    seen = []

    def handler(r: httpx.Request):
        seen.append(r)
        if "access_tokens" in r.url.path:
            return httpx.Response(201, json={"token": "t"})
        return httpx.Response(200)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await gh.post_review(client, job(), False, "blocked")
    body = json.loads(seen[0].content)
    assert body == {"repositories": ["budgetguard"], "permissions": {"deployments": "write"}}
    claims = jwt.decode(seen[0].headers["Authorization"].removeprefix("Bearer "), KEY.public_key(),
                        algorithms=["RS256"])
    assert claims["iss"] == "123" and claims["exp"] - claims["iat"] == 600
    assert json.loads(seen[1].content)["state"] == "rejected"


# ------------------------------------------------------------------ OIDC
OK = {"repository": "ricky/budgetguard", "repository_id": "1001", "repository_owner_id": "2002",
      "environment": "production-orders-service", "run_id": "777", "ref": "refs/heads/main",
      "workflow_ref": "ricky/budgetguard/.github/workflows/deploy.yml@refs/heads/main"}


def token(alg="RS256", key=KEY, **claims):
    now = int(time.time())
    base = {"iss": auth.ACTIONS_ISSUER, "aud": "budgetguard", "iat": now, "exp": now + 300} | OK | claims
    return jwt.encode({k: v for k, v in base.items() if v is not None}, key, algorithm=alg)


def verify(t, env="production-orders-service", run_id=777):
    return auth.verify_actions_oidc(t, env, run_id, key_for=lambda _: KEY.public_key())


def test_oidc_valid():
    assert verify(token())["run_id"] == "777"


@pytest.mark.parametrize("claims", [
    {"repository": "mallory/fork"}, {"repository_id": "9"}, {"repository_owner_id": "9"},
    {"environment": "production-auth-service"}, {"run_id": "778"}, {"ref": "refs/heads/feature"},
    {"workflow_ref": "ricky/budgetguard/.github/workflows/evil.yml@refs/heads/main"},
])
def test_oidc_rejects_wrong_scope(claims):
    with pytest.raises(PermissionError):
        verify(token(**claims))


@pytest.mark.parametrize("claims", [{"aud": "other"}, {"iss": "https://evil"},
                                    {"exp": int(time.time()) - 120},
                                    {"run_id": None}])
def test_oidc_rejects_bad_token(claims):
    with pytest.raises(jwt.PyJWTError):
        verify(token(**claims))


def test_oidc_rejects_hs256_signed_with_public_key():
    pem = KEY.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    forged = jwt.encode({"iss": auth.ACTIONS_ISSUER, "aud": "budgetguard", "exp": int(time.time()) + 300,
                         "iat": int(time.time())} | OK, "x" * 32, algorithm="HS256")
    with pytest.raises(jwt.PyJWTError):
        auth.verify_actions_oidc(forged, "production-orders-service", 777, key_for=lambda _: pem)
