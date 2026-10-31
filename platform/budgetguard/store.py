"""Persistence boundary. The API depends on this Protocol; PgStore implements it with the SQL in
budgetguard-data-layer/references/schema-and-queries.md, tests use an in-memory fake."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
    override: dict[str, Any] | None = None   # {approver, reason, created_at} when overridden


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


SloKey = tuple[str, str]            # (service, slo)


class Store(Protocol):
    async def decision_for_run(self, service: str, run_id: int) -> DecisionRow | None: ...
    async def decision(self, decision_id: int) -> DecisionRow | None: ...
    async def upsert_incident(self, e: IncidentEvent) -> None: ...
    async def create_override(self, decision_id: int, approver_id: int, approver: str,
                              reason: str) -> int: ...
    async def delete_override(self, override_id: int) -> None: ...
    async def open_override(self, service: str, sha: str) -> int | None: ...   # lane proof (query 4.10a)
    async def ping(self) -> None: ...
    # W4: history and dashboard reads
    async def ensure_policy_version(self, version: str) -> None: ...
    async def slo_windows(self, now: datetime) -> dict[SloKey, tuple[float, float, float]]: ...
    async def slo_daily(self, now: datetime, days: int) -> dict[SloKey, list[tuple[date, float, float]]]: ...
    async def eta_buckets(self, service: str, slo: str, now: datetime,
                          self_only: bool = False) -> list[Any]: ...
    async def open_incidents(self) -> dict[str, dict[str, Any]]: ...
    async def last_decisions(self) -> dict[str, dict[str, Any]]: ...
    async def decision_page(self, service: str | None, before_id: int | None,
                            limit: int) -> list[DecisionRow]: ...
    async def policy_versions(self) -> list[dict[str, Any]]: ...
