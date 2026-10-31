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


# ------------------------------------------------------------------ W8.5: environment and cluster drift
APP_ID = "424242"
OUR_RULE = {"id": 1, "enabled": True, "app": {"id": 424242, "slug": "budgetguard"}}


@pytest.mark.parametrize(("env", "rules", "reasons"), [
    ({"can_admins_bypass": False}, [OUR_RULE], []),
    (None, [], ["environment_missing"]),
    ({"can_admins_bypass": True}, [OUR_RULE], ["admin_bypass_enabled"]),
    ({}, [OUR_RULE], ["admin_bypass_enabled"]),                      # GitHub's default is True
    ({"can_admins_bypass": False}, [{**OUR_RULE, "app": {"id": 7}}], ["protection_rule_missing"]),
    ({"can_admins_bypass": False}, [{**OUR_RULE, "enabled": False}], ["protection_rule_disabled"]),
    ({"can_admins_bypass": True}, [], ["admin_bypass_enabled", "protection_rule_missing"]),
])
def test_environment_drift_names_every_gap(env: dict[str, Any] | None, rules: list[dict[str, Any]],
                                           reasons: list[str]) -> None:
    assert rc.environment_drift(env, rules, APP_ID) == reasons


def rollout(name: str, image_digest: str, stamped: str | None, sha: str | None) -> dict[str, Any]:
    ann = {k: v for k, v in ((rc.DIGEST_ANNOTATION, stamped), (rc.SHA_ANNOTATION, sha)) if v}
    return {"metadata": {"name": name},
            "spec": {"template": {"metadata": {"annotations": ann},
                                  "spec": {"containers": [
                                      {"name": "app", "image": f"ns/{name}@{image_digest}"}]}}}}


D1, D2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64


@pytest.mark.parametrize(("ro", "admitted", "reason"), [
    (rollout("orders-service", D1, D1, "a" * 40), {"a" * 40}, None),
    (rollout("orders-service", D2, D1, "a" * 40), {"a" * 40}, "image_changed"),      # kubectl set image
    (rollout("orders-service", D1, D1, "b" * 40), {"a" * 40}, "sha_not_admitted"),   # hand-run helm upgrade
    (rollout("orders-service", D1, None, None), set(), "unannotated"),
])
def test_rollout_drift(ro: dict[str, Any], admitted: set[str], reason: str | None) -> None:
    found = rc.rollout_drift(ro, admitted)
    assert (found.reason if found else None) == reason


class DriftLedger(FakeLedger):
    def __init__(self, admitted: dict[str, set[str]]) -> None:
        super().__init__()
        self.admitted, self.recorded = admitted, []

    async def admitted_shas(self, service: str, shas: list[str]) -> set[str]:
        return self.admitted.get(service, set()) & set(shas)

    async def record_drift(self, finding: rc.DigestDrift, policy_version: str) -> bool:
        first = finding not in self.recorded
        self.recorded.append(finding)
        return first


class FakeKube:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    async def rollouts(self) -> list[dict[str, Any]]:
        return self.items


async def test_cluster_check_records_only_the_drifted_rollout() -> None:
    ledger = DriftLedger({"orders-service": {"a" * 40}, "auth-service": {"c" * 40}})
    kube = FakeKube([rollout("orders-service", D1, D1, "a" * 40), rollout("auth-service", D2, D1, "c" * 40)])
    found = await rc.check_cluster(kube, ledger, "v1")  # type: ignore[arg-type]
    assert [(f.service, f.reason) for f in found] == [("auth-service", "image_changed")]
    assert ledger.recorded[0].live_digest == D2 and ledger.recorded[0].annotated_digest == D1
    rc._publish_cluster(found, ["auth-service", "orders-service"])
    assert rc.DIGEST_DRIFT.labels("auth-service")._value.get() == 1
    assert rc.DIGEST_DRIFT.labels("orders-service")._value.get() == 0


async def test_environment_check_uses_a_read_only_token_and_treats_404_as_missing() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/access_tokens"):
            assert json.loads(request.content)["permissions"] == {"actions": "read"}
            return httpx.Response(201, json={"token": "ghs_scoped"})
        assert request.headers["Authorization"] == "Bearer ghs_scoped"
        if "production-auth-service" in request.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path.endswith("/deployment_protection_rules"):
            return httpx.Response(200, json={"total_count": 1, "custom_deployment_protection_rules": [
                {"id": 3, "enabled": True, "app": {"id": int(get_settings().github_app_id)}}]})
        return httpx.Response(200, json={"name": "production-orders-service", "can_admins_bypass": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        gh = rc.GitHub(http, get_settings(), jwt=lambda: "app.jwt")
        found = await rc.check_environments(gh, ["auth-service", "orders-service"],
                                            get_settings().github_app_id)
    assert found == {"auth-service": ["environment_missing"], "orders-service": []}
    rules = "/repos/ricky/budgetguard/environments/production-orders-service/deployment_protection_rules"
    assert ("GET", rules) in seen
    rc._publish_environments(found)
    assert rc.CONFIG_DRIFT.labels("auth-service", "environment_missing")._value.get() == 1


def test_kube_client_is_off_outside_a_cluster(tmp_path: Any) -> None:
    assert rc.Kube.in_cluster("bg-apps", sa_dir=tmp_path) is None
