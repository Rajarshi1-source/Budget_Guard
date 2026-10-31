"""Deploy pricing (D-2, budgetguard-admission-gate §3). Pure: the price of a deploy is the posterior
change-failure rate times a change-risk multiplier times what a failure usually costs, in percent
of the 30-day budget. With no history the posterior is the prior, Beta(2, 18) = 10%."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChangeFeatures:
    lines_changed: int
    touches_migration: bool
    config_only: bool
    dependency_bump: bool

    @classmethod
    def unknown(cls) -> ChangeFeatures:
        """No PR found for the SHA (or extraction not yet built, W6.1): priced at size_max."""
        return cls(lines_changed=10**9, touches_migration=False, config_only=False, dependency_bump=False)


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
