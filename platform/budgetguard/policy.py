"""The freeze policy (policy/freeze-policy.yaml). One loader for the gate and the backtester, so a
policy PR is scored by exactly the numbers the gate will use (budgetguard-mlops-evals)."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

import yaml

from .pricing import PricingPolicy


@dataclass(frozen=True, slots=True)
class Policy:
    version: str
    reserve_pct: float = 5.0
    spend_fraction: float = 0.5
    attribution: Literal["charge_origin", "charge_victim"] = "charge_origin"
    self_page_share: float = 0.5
    low_traffic_prices_normally: bool = True
    pricing: PricingPolicy = field(default_factory=PricingPolicy)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], version: str) -> Policy:
        raw = dict(raw)
        pr = dict(raw.pop("pricing", {}) or {})
        known = {f.name for f in fields(cls)} - {"version", "pricing"}
        unknown = set(raw) - known | set(pr) - {f.name for f in fields(PricingPolicy)}
        if unknown:                                  # a typo must fail, not silently use a default
            raise ValueError(f"unknown policy keys: {sorted(unknown)}")
        if "size_tiers" in pr:
            pr["size_tiers"] = tuple((int(a), float(b)) for a, b in pr["size_tiers"])
        if raw.get("attribution", "charge_origin") not in ("charge_origin", "charge_victim"):
            raise ValueError(f"attribution must be charge_origin or charge_victim, got {raw['attribution']}")
        return cls(version=version, pricing=PricingPolicy(**pr), **raw)

    @classmethod
    def from_yaml(cls, path: Path, version: str) -> Policy:
        return cls.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, version)
