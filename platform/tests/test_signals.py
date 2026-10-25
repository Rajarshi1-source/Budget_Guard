"""Prometheus client and live verdicts against a fake Prometheus: silence is never health (D-4)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from budgetguard.integrations.prometheus import Breaker, PrometheusClient, PrometheusUnavailable
from budgetguard.integrity import Integrity, SliState
from budgetguard.rulegen.spec import load
from budgetguard.signals import slo_status

ORDERS = load(Path(__file__).resolve().parents[2] / "slo" / "orders-service.yaml")
AVAIL = ORDERS.spec.slos[0]


def vector(value: float | str, **labels: str) -> list[dict]:
    return [{"metric": labels, "value": [1_700_000_000, str(value)]}]


def fake(answers: Callable[[str], list[dict]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        return httpx.Response(200, json={"status": "success",
                                         "data": {"resultType": "vector", "result": answers(q)}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def healthy(q: str, overrides: dict[str, list[dict]] | None = None) -> list[dict]:
    for key, val in (overrides or {}).items():
        if key in q:
            return val
    if q.startswith("count("):
        return vector(4)
    if q.startswith("min(up"):
        return vector(1)
    if q.startswith("min(probe_success"):
        return vector(1)
    if q.startswith("sum(increase("):
        return vector(72_000)
    if "increase30d" in q:
        return vector(0.0003)                              # 30% of the 0.1% budget spent
    if "rate1h" in q:
        return vector(0.0001)
    if q.startswith("ALERTS"):
        return []
    raise AssertionError(f"unexpected query {q}")


async def status(overrides: dict[str, list[dict]] | None = None):
    async with fake(lambda q: healthy(q, overrides)) as http:
        return await slo_status(PrometheusClient("http://prom:9090", http), ORDERS, AVAIL)


async def test_healthy_service() -> None:
    s = await status()
    assert (s.integrity, s.state) == (Integrity.OK, SliState.HEALTHY)
    assert s.remaining_pct == pytest.approx(70.0) and s.burn_rate_1h == pytest.approx(0.1)


async def test_dead_target_is_unknown_not_healthy() -> None:
    s = await status({"min(up": vector(0)})
    assert (s.integrity, s.state) == (Integrity.STALE, SliState.UNKNOWN)


async def test_no_series_at_all_is_no_data() -> None:
    s = await status({"count(": [], "sum(increase(": []})
    assert (s.integrity, s.state) == (Integrity.NO_DATA, SliState.UNKNOWN)


async def test_failing_probe_is_stale_even_while_metrics_flow() -> None:
    s = await status({"min(probe_success": vector(0)})
    assert s.integrity is Integrity.STALE and s.state is SliState.UNKNOWN


async def test_low_traffic_with_a_budget_number_is_healthy() -> None:
    s = await status({"sum(increase(": vector(50)})
    assert (s.integrity, s.state) == (Integrity.LOW_TRAFFIC, SliState.HEALTHY)


async def test_overspent_budget_is_negative_and_at_risk() -> None:
    s = await status({"increase30d": vector(0.003)})
    assert s.remaining_pct == pytest.approx(-200.0) and s.state is SliState.AT_RISK


async def test_firing_page_for_this_slo_is_at_risk_but_another_slos_is_not() -> None:
    own = await status({"ALERTS": vector(1, severity="page", slo="availability", service="orders-service")})
    other = await status({"ALERTS": vector(1, severity="page", slo="latency", service="orders-service")})
    assert own.state is SliState.AT_RISK and own.page_firing
    assert other.state is SliState.HEALTHY and not other.page_firing


async def test_missing_30d_number_is_unknown() -> None:
    s = await status({"increase30d": []})
    assert s.state is SliState.UNKNOWN and s.remaining_pct is None


async def test_unreachable_prometheus_is_stale_never_healthy() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")
    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        s = await slo_status(PrometheusClient("http://prom:9090", http), ORDERS, AVAIL)
    assert (s.integrity, s.state) == (Integrity.STALE, SliState.UNKNOWN)


async def test_breaker_opens_after_three_failures_and_half_opens_after_cooldown() -> None:
    now = [0.0]
    calls = [0]

    def down(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        prom = PrometheusClient("http://prom:9090", http, breaker=Breaker(clock=lambda: now[0]))
        for _ in range(3):
            with pytest.raises(PrometheusUnavailable):
                await prom.query("up")
        assert not prom.reachable
        with pytest.raises(PrometheusUnavailable, match="circuit open"):
            await prom.query("up")
        assert calls[0] == 3                                   # open: no request sent
        now[0] = 31.0
        with pytest.raises(PrometheusUnavailable):
            await prom.query("up")                             # half-open trial goes out
        assert calls[0] == 4


async def test_nan_and_empty_are_none() -> None:
    async with fake(lambda q: vector("NaN") if "nan" in q else []) as http:
        prom = PrometheusClient("http://prom:9090", http)
        assert await prom.scalar("nan_query") is None
        assert await prom.scalar("empty_query") is None


async def test_bad_query_is_a_bug_not_an_outage() -> None:
    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "error": "parse error"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(bad)) as http:
        prom = PrometheusClient("http://prom:9090", http)
        with pytest.raises(ValueError, match="parse error"):
            await prom.query("sum(")
        assert prom.reachable


async def test_query_time_must_be_timezone_aware() -> None:
    async with fake(lambda q: []) as http:
        with pytest.raises(ValueError, match="timezone"):
            await PrometheusClient("http://prom:9090", http).query("up", at=datetime(2026, 10, 26))
