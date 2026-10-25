"""Error-budget arithmetic. Pure functions, no I/O: every number in a decision record is recomputable."""
from __future__ import annotations

import math
from dataclasses import dataclass

WINDOW_HOURS_30D = 720.0


@dataclass(frozen=True, slots=True)
class Counts:
    """Request-weighted counts over one window. Floats because Prometheus increase() extrapolates."""
    bad: float
    total: float

    def error_ratio(self) -> float | None:
        if math.isnan(self.total) or math.isnan(self.bad) or self.total <= 0:
            return None                      # unknown — never "0% errors"
        return min(max(self.bad / self.total, 0.0), 1.0)


def allowed_error_ratio(objective: float) -> float:
    if not 0.0 < objective < 1.0:
        raise ValueError(f"objective must be in (0, 1), got {objective}")
    return 1.0 - objective


def burn_rate(error_ratio: float, objective: float) -> float:
    """1.0 = spending exactly on plan; 14.4 = 14.4x faster than plan."""
    return error_ratio / allowed_error_ratio(objective)


def budget_remaining(counts: Counts, objective: float) -> float | None:
    """Fraction of the window's budget left. Negative = overspent; kept, not clipped (fixes B-3)."""
    ratio = counts.error_ratio()
    if ratio is None:
        return None
    return 1.0 - ratio / allowed_error_ratio(objective)


def budget_remaining_pct(counts: Counts, objective: float) -> float | None:
    r = budget_remaining(counts, objective)
    return None if r is None else 100.0 * r


def budget_spent(burn: float, hours: float, window_hours: float = WINDOW_HOURS_30D) -> float:
    """Fraction of the window's budget spent by `burn` sustained for `hours`. 14.4x for 1h -> 0.02."""
    return burn * hours / window_hours


def hours_to_exhaustion(remaining: float, burn: float,
                        window_hours: float = WINDOW_HOURS_30D) -> float | None:
    """At constant burn, hours until remaining budget reaches zero. 14.4x from full -> 50h."""
    if burn <= 0:
        return None
    return max(remaining, 0.0) * window_hours / burn


def budget_minutes(objective: float, window_hours: float = WINDOW_HOURS_30D) -> float:
    """Time-equivalent budget for a full outage at constant traffic. 0.999 over 30d -> 43.2."""
    return allowed_error_ratio(objective) * window_hours * 60.0


def sum_counts(buckets: list[Counts]) -> Counts:
    """Request-weighted aggregation. Never average ratios: a quiet hour must not weigh like a busy one."""
    return Counts(bad=sum(b.bad for b in buckets), total=sum(b.total for b in buckets))
