from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from budgetguard.config import get_settings
from budgetguard.workers import reconcilers as rc

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def iso(delta: timedelta) -> str:
    return (NOW - delta).isoformat().replace("+00:00", "Z")


class FakeLedger:
    def __init__(self, deliveries: frozenset[str] = frozenset(),
                 deployments: frozenset[int] = frozenset()) -> None:
        self.deliveries, self.deployments = set(deliveries), set(deployments)
        self.bypasses: list[tuple[str, int]] = []

    async def decided_deliveries(self, guids: list[str]) -> set[str]:
        return self.deliveries & set(guids)

    async def decided_deployments(self, ids: list[int]) -> set[int]:
        return self.deployments & set(ids)

    async def record_bypass(self, service: str, deployment: dict[str, Any], policy_version: str) -> bool:
        self.bypasses.append((service, int(deployment["id"])))
        return True


class FakeGitHub:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._deliveries = [
            {"id": 1, "guid": "g-ok", "event": "deployment_protection_rule", "status_code": 200,
             "delivered_at": iso(timedelta(hours=1))},
            {"id": 2, "guid": "g-fail", "event": "deployment_protection_rule", "status_code": 503,
             "delivered_at": iso(timedelta(hours=1))},
            {"id": 3, "guid": "g-retried", "event": "deployment_protection_rule", "status_code": 502,
             "delivered_at": iso(timedelta(hours=2))},
            {"id": 4, "guid": "g-retried", "event": "deployment_protection_rule", "status_code": 200,
             "delivered_at": iso(timedelta(hours=1))},
            {"id": 5, "guid": "g-decided", "event": "deployment_protection_rule", "status_code": 500,
             "delivered_at": iso(timedelta(hours=1))},
            {"id": 6, "guid": "g-old", "event": "deployment_protection_rule", "status_code": 500,
             "delivered_at": iso(timedelta(days=4))},
            {"id": 7, "guid": "g-status", "event": "deployment_status", "status_code": 500,
             "delivered_at": iso(timedelta(hours=1))},
        ]
        self._deployments = {
            "production-orders-service": [
                {"id": 101, "sha": "a" * 40,
                 "created_at": iso(timedelta(hours=2)), "latest_state": "success"},
                {"id": 102, "sha": "b" * 40,
                 "created_at": iso(timedelta(hours=2)), "latest_state": "success"},
                {"id": 103, "sha": "c" * 40,
                 "created_at": iso(timedelta(minutes=2)), "latest_state": "in_progress"},
                {"id": 104, "sha": "d" * 40,
                 "created_at": iso(timedelta(hours=2)), "latest_state": "failure"},
            ]}

    async def deliveries(self) -> list[dict[str, Any]]:
        return self._deliveries

    async def redeliver(self, delivery_id: int) -> None:
        self.calls.append(f"redeliver {delivery_id}")

    async def deployments(self, environment: str) -> list[dict[str, Any]]:
        return self._deployments.get(environment, [])


async def test_redelivers_only_failed_undecided_recent_protection_rule_deliveries() -> None:
    gh = FakeGitHub()
    done = await rc.redeliver_failed(gh, FakeLedger(deliveries=frozenset({"g-decided"})), NOW)  # type: ignore[arg-type]
    assert done == ["g-fail"] and gh.calls == ["redeliver 2"]


async def test_bypass_records_deployments_that_ran_without_a_decision() -> None:
    ledger = FakeLedger(deployments=frozenset({101}))
    done = await rc.ingest_bypasses(FakeGitHub(), ledger, ["orders-service", "auth-service"],  # type: ignore[arg-type]
                                    "v1", NOW)
    # 101 has a decision, 103 may still be waiting for one, 104 never ran
    assert done == [102] and ledger.bypasses == [("orders-service", 102)]


async def test_github_client_uses_the_app_jwt_and_a_read_only_token() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("Authorization")))
        if request.url.path.endswith("/access_tokens"):
            import json
            assert json.loads(request.content)["permissions"] == {"deployments": "read"}
            return httpx.Response(201, json={"token": "ghs_scoped"})
        if request.url.path.endswith("/statuses"):
            return httpx.Response(200, json=[{"state": "success"}])
        if request.url.path.endswith("/deployments"):
            return httpx.Response(200, json=[{"id": 9, "sha": "e" * 40,
                                              "created_at": iso(timedelta(hours=1))}])
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        gh = rc.GitHub(http, get_settings(), jwt=lambda: "app.jwt")
        await gh.deliveries()
        deps = await gh.deployments("production-orders-service")
    assert deps[0]["latest_state"] == "success"
    assert seen[0] == ("GET", "/app/hook/deliveries", "Bearer app.jwt")
    assert ("GET", "/repos/ricky/budgetguard/deployments", "Bearer ghs_scoped") in seen
    with pytest.raises(AssertionError):
        assert ("GET", "/repos/ricky/budgetguard/deployments", "Bearer app.jwt") in seen
