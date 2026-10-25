"""Prometheus HTTP client for the decision plane: 3 s timeout and a circuit breaker (Rev 2 §8).

Prometheus is the live source of truth. When it cannot be reached, callers get
PrometheusUnavailable and the gate maps that to integrity STALE (the service's failure_mode
applies), never to "healthy". `reachable` feeds the bg_prometheus_reachable gauge that the
BudgetGuardBlind alert watches (exported with the platform's /metrics in W4).
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx


class PrometheusUnavailable(Exception):
    """Prometheus did not answer (timeout, connection error, 5xx) or the breaker is open."""


@dataclass
class Breaker:
    """Opens after `threshold` consecutive failures; lets one probe call through after `cooldown_s`."""
    threshold: int = 3
    cooldown_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    failures: int = 0
    opened_at: float | None = field(default=None)

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        return self.clock() - self.opened_at >= self.cooldown_s       # half-open: one trial call

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()

    @property
    def open(self) -> bool:
        return self.opened_at is not None


class PrometheusClient:
    def __init__(self, base_url: str, http: httpx.AsyncClient, timeout_s: float = 3.0,
                 breaker: Breaker | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.timeout_s = timeout_s
        self.breaker = breaker or Breaker()

    @property
    def reachable(self) -> bool:
        return not self.breaker.open

    async def query(self, expr: str, at: datetime | None = None) -> list[dict[str, Any]]:
        """Instant query; returns the vector result (possibly empty)."""
        if not self.breaker.allow():
            raise PrometheusUnavailable("circuit open")
        params: dict[str, str] = {"query": expr}
        if at is not None:
            if at.tzinfo is None:
                raise ValueError("query time must be timezone-aware")
            params["time"] = f"{at.timestamp():.3f}"
        try:
            r = await self.http.get(f"{self.base_url}/api/v1/query", params=params, timeout=self.timeout_s)
            if r.status_code >= 500:
                raise PrometheusUnavailable(f"HTTP {r.status_code}")
            r.raise_for_status()
            body = r.json()
        except (httpx.TransportError, httpx.TimeoutException) as e:
            self.breaker.failure()
            raise PrometheusUnavailable(type(e).__name__) from e
        except PrometheusUnavailable:
            self.breaker.failure()
            raise
        self.breaker.success()
        if body.get("status") != "success":
            # a rejected query is our bug, not an outage: do not trip the breaker
            raise ValueError(f"query rejected: {body.get('error', body)}")
        data = body["data"]
        if data["resultType"] == "scalar":
            return [{"metric": {}, "value": data["result"]}]
        result: list[dict[str, Any]] = data["result"]
        return result

    async def scalar(self, expr: str, at: datetime | None = None) -> float | None:
        """First sample's value, or None for an empty result. NaN is returned as None too: unknown."""
        result = await self.query(expr, at)
        if not result:
            return None
        value = float(result[0]["value"][1])
        return None if math.isnan(value) else value
