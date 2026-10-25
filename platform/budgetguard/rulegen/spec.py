"""ServiceLevel spec model (budgetguard-slo-rules §1). One file per service in slo/ is the source of truth."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DURATION = re.compile(r"^\d+[smhd]$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")        # a typo in a spec fails, never silently drops a key


class Tier(_Strict):
    long: str
    short: str
    burn: float = Field(gt=0)

    @field_validator("long", "short")
    @classmethod
    def _dur(cls, v: str) -> str:
        if not DURATION.match(v):
            raise ValueError(f"bad duration {v}")
        return v


class Alerting(_Strict):
    page: list[Tier] = []
    ticket: list[Tier] = []


class Sli(_Strict):
    type: Literal["ratio", "latency_bucket"]
    total: str
    bad: str | None = None
    good: str | None = None
    self_bad: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Sli:
        if self.type == "ratio" and not self.bad:
            raise ValueError("ratio SLI needs `bad`")
        if self.type == "latency_bucket" and not self.good:
            raise ValueError("latency_bucket SLI needs `good`")
        good = self.good or ""
        if self.type == "latency_bucket" and 'le="' in good and not re.search(r'le="\d+\.\d+"', good):
            raise ValueError('write le in float form (le="1.0"): Prometheus 3 normalises '
                             'classic-histogram le values')
        return self


class Slo(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    objective: float = Field(gt=0, lt=1)
    sli: Sli
    alerting: Alerting

    @model_validator(mode="after")
    def _burns_fit(self) -> Slo:
        for tier in self.alerting.page + self.alerting.ticket:
            if tier.burn * (1 - self.objective) >= 1:
                raise ValueError(f"{self.name}: burn {tier.burn} needs an error ratio >= 100%")
        return self


class Gate(_Strict):
    reserve_pct: float = Field(ge=0, lt=100)
    spend_fraction: float = Field(gt=0, le=1)
    hard_stop_on: list[Literal["page", "ticket"]] = ["page"]


class Spec(_Strict):
    window: Literal["30d", "28d"] = "30d"
    failure_mode: Literal["fail_open", "fail_closed"] = "fail_open"
    min_events_per_hour: int = Field(ge=0)
    job: str
    probe: str | None = None
    slos: list[Slo]
    gate: Gate


class Metadata(_Strict):
    service: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")
    owner: str


class ServiceLevel(_Strict):
    apiVersion: Literal["budgetguard.dev/v1"]
    kind: Literal["ServiceLevel"]
    metadata: Metadata
    spec: Spec


def load(path: Path) -> ServiceLevel:
    return ServiceLevel.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_dir(slo_dir: Path) -> list[ServiceLevel]:
    """Every *.yaml in slo_dir, sorted by service so output order is stable."""
    specs = [load(p) for p in sorted(slo_dir.glob("*.yaml"))]
    names = [s.metadata.service for s in specs]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate service in {slo_dir}: {names}")
    return sorted(specs, key=lambda s: s.metadata.service)
