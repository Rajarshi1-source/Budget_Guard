---
name: budgetguard-slo-math
description: Implement, review and explain BudgetGuard's error-budget arithmetic in Python — error budget, burn rate, budget remaining (negative when overspent), budget spent per window, hours to exhaustion, the 2/5/10 percent Workbook ladder behind the 14.4x, 6x and 1x thresholds, request-based versus time-equivalent budgets (99.9 percent over 30 days is 43.2 minutes), the tri-state SLI verdict (HEALTHY, AT_RISK, UNKNOWN for missing data, dead targets or low traffic) and the exact unfreeze ETA that replays 5-minute buckets. Use this skill whenever writing or reviewing budget.py, integrity.py, eta.py or their tests, answering an SLO-math interview question, or sanity-checking any number about burn rates, error budgets or exhaustion time in BudgetGuard, even if the user only asks whether the math is right. MANDATE - pure functions with no I/O; 14.4x spends 2 percent of a 30-day budget per hour and lasts 50 hours, never one hour; missing data is UNKNOWN, never zero errors.
---

# BudgetGuard — SLO math core (Rev 2)

This is the arithmetic the whole project stands on. Rev 1 got it wrong in three places
(B-1, B-2, B-3) and an SRE panel can disprove each with a whiteboard, so this core is
**pure, deterministic, and pinned by unit tests**. Every number that later appears in a
decision record, a Freeze Brief or an interview answer must be recomputable from these
functions.

Location in the repo: `platform/budgetguard/{budget,integrity,eta}.py` with tests in
`platform/tests/test_{budget,integrity,eta}.py`. Python 3.14, standard library only
(NumPy is allowed in the backtester, not here — the gate must import this module in
under a millisecond).

Pairs with: `budgetguard-admission-gate` (consumes these functions),
`budgetguard-data-layer` (supplies the 5-minute buckets), `budgetguard-slo-rules`
(the PromQL that must agree with these numbers).

---

## 1. The formulas, once, correctly

Let `T` be the SLO window in hours (720 for 30 days), `o` the objective (0.999),
`a = 1 − o` the allowed error ratio, `e` the observed error ratio over some window.

| Quantity | Formula | Example (o = 0.999) |
| --- | --- | --- |
| Allowed error ratio | `a = 1 − o` | 0.001 (1 request in 1,000) |
| Burn rate | `b = e / a` | e = 1% → b = 10x |
| Budget remaining (fraction) | `1 − e₃₀ / a` | e₃₀ = 0.0003 → 0.70 |
| Budget spent at burn b for t hours | `b · t / T` | 14.4 · 1 / 720 = **2%** |
| Hours to exhaust a full budget | `T / b` | 720 / 14.4 = **50 h** |
| Time-equivalent budget | `a · T · 60` minutes | 0.001 · 720 · 60 = **43.2 min** |

The Workbook ladder is just the "budget spent" row solved for `b`:

| Tier | Window | Burn | Budget spent in window |
| --- | --- | --- | --- |
| Page | 1 h (short 5 m) | 14.4x | 2% |
| Page | 6 h (short 30 m) | 6x | 5% |
| Ticket | 3 d (short 6 h) | 1x | 10% |

Two framing rules to hold in every answer:

1. **Request-based SLIs have a request budget.** "43.2 minutes" is only the equivalent of a
   *full outage at constant traffic*. Say both: "1 in 1,000 requests may fail — that's 43.2
   minutes if it were one complete outage."
2. **Say which window.** 43.2 min is a 30-day rolling window (720 h). 43.8 min is the
   calendar-month average (30.44 days). Rev 1 mixed them (B-2).

### Per-service reference (Rev 2 §2)

| Service | Objective | Bad requests allowed | Time-equivalent / 30 d |
| --- | --- | --- | --- |
| auth-service | 0.9995 | 1 in 2,000 | 21.6 min |
| orders-service | 0.999 | 1 in 1,000 | 43.2 min |
| inventory-service | 0.995 | 1 in 200 | 3.6 h |
| notifications-service | 0.99 | 1 in 100 | 7.2 h |

---

## 2. `budget.py` — complete file

```python
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
```

`sum_counts` exists because the most common silent bug after B-3 is **averaging per-bucket
availability**. A 3 a.m. bucket with 10 requests and 1 error (90%) would drag a mean down as
much as a peak bucket with 100,000 requests. Always sum bad and total, then divide once.

---

## 3. `integrity.py` — the D-4 fix ("a dead service has a perfect error rate")

A crashed or unscraped service emits no samples; the ratio is NaN or absent; every `> 14.4`
comparison is false; a naive gate says healthy. BudgetGuard classifies the *signal* before it
trusts the *value*.

```python
"""SLI integrity and the tri-state verdict. Pure; inputs are gathered by the Prometheus client."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Integrity(StrEnum):
    OK = "ok"
    LOW_TRAFFIC = "low_traffic"   # events below the spec floor: short windows not meaningful
    STALE = "stale"               # up == 0, scrape failing, or synthetic probe failing
    NO_DATA = "no_data"           # series absent: never scraped or fully gone


class SliState(StrEnum):
    HEALTHY = "healthy"
    AT_RISK = "at_risk"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SignalHealth:
    series_present: bool          # NOT absent(metric{service=...})
    targets_up: bool | None       # min(up{job=...}) == 1 ; None if no target info
    probe_ok: bool | None         # probe_success from blackbox exporter ; None if not probed
    events_last_hour: float       # sum(increase(total[1h]))
    min_events_per_hour: float    # from the SLO spec


def classify(h: SignalHealth) -> Integrity:
    # Order matters: absence beats staleness beats low traffic.
    if not h.series_present:
        return Integrity.NO_DATA
    if h.targets_up is False or h.probe_ok is False:
        return Integrity.STALE
    if h.events_last_hour < h.min_events_per_hour:
        return Integrity.LOW_TRAFFIC
    return Integrity.OK


def sli_state(integrity: Integrity, remaining_pct: float | None,
              page_firing: bool, ticket_firing: bool, reserve_pct: float) -> SliState:
    if integrity in (Integrity.NO_DATA, Integrity.STALE) or remaining_pct is None:
        return SliState.UNKNOWN
    if page_firing or ticket_firing or remaining_pct < reserve_pct:
        return SliState.AT_RISK
    return SliState.HEALTHY            # LOW_TRAFFIC with a valid 30-day number can be healthy
```

### Design decision to flag: LOW_TRAFFIC is not the same as STALE

Rev 2's gate (`integrity_check` in §4.11) short-circuits on *any* non-OK integrity, which
means a quiet service at 3 a.m. gets `admit_unverified` and **pages on-call** for every deploy.
That contradicts Rev 2's own low-traffic answer ("the 30-day window still counts every real
request"). Recommended fix, mirrored in `budgetguard-admission-gate`:

- `NO_DATA`, `STALE` → unknown → service `failure_mode` applies (fail-open audit + page).
- `LOW_TRAFFIC` → trust the 30-day remaining from buckets, ignore short-window hard stops,
  price normally, and force the conservative canary profile.

Raise this with the user rather than silently changing the plan's gate code.

---

## 4. `eta.py` — exact unfreeze ETA (D-5)

Rolling windows recover when *old bad buckets age out*, not when errors stop. The ETA
replays the stored 5-minute buckets forward, dropping the oldest and appending a projected
bucket built from recent traffic and an assumed future error ratio.

```python
"""D-5: when does the rolling 30-day budget climb back above the threshold?

O(n) with running sums over 8,640 buckets per 30 days. Returns None rather than guessing.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

BUCKET = timedelta(minutes=5)
BUCKETS_PER_DAY = 288


@dataclass(frozen=True, slots=True)
class Bucket:
    start: datetime          # timezone-aware UTC (fixes B-18)
    bad: float
    total: float


def unfreeze_eta(window: list[Bucket], objective: float, threshold_pct: float,
                 future_error_ratio: float = 0.0,
                 horizon_buckets: int = 30 * BUCKETS_PER_DAY) -> datetime | None:
    """`window` = last 30 days of buckets, oldest first, ending at now."""
    if not window:
        return None
    if window[-1].start.tzinfo is None:
        raise ValueError("bucket timestamps must be timezone-aware")
    allowed = 1.0 - objective
    recent = window[-BUCKETS_PER_DAY:]
    traffic = sum(b.total for b in recent) / len(recent)
    if traffic <= 0:
        return None          # no recent traffic: a projection would be fiction (Rev 2 gap, fixed here)

    q = deque(window)
    bad = sum(b.bad for b in q)
    total = sum(b.total for b in q)
    t = window[-1].start

    def remaining_pct() -> float:
        return 100.0 * (1.0 - (bad / total) / allowed) if total > 0 else 100.0

    if remaining_pct() >= threshold_pct:
        return t + BUCKET
    for _ in range(horizon_buckets):
        old = q.popleft()
        bad -= old.bad
        total -= old.total
        t += BUCKET
        q.append(Bucket(t, future_error_ratio * traffic, traffic))
        bad += future_error_ratio * traffic
        total += traffic
        if remaining_pct() >= threshold_pct:
            return t + BUCKET
    return None
```

Why `traffic <= 0` returns None: Rev 2's version would project zero-traffic buckets, total
would fall toward the remaining history, and the function could report an optimistic ETA for
a service that is actually down. When there is nothing to project from, say "unknown".

Present the ETA with its assumption: "budget returns above 10% at 14:20 IST Thursday
**if the error ratio stays at 0**". Offer the `future_error_ratio = allowed / 2` variant
(half-budget pace) as the realistic second number.

---

## 5. Tests that pin the interview arithmetic

```python
# platform/tests/test_budget.py
import math
from datetime import UTC, datetime, timedelta

import pytest
from budgetguard.budget import (Counts, budget_minutes, budget_remaining, budget_spent,
                                burn_rate, hours_to_exhaustion, sum_counts)
from budgetguard.eta import BUCKET, Bucket, unfreeze_eta
from budgetguard.integrity import Integrity, SignalHealth, SliState, classify, sli_state


def test_workbook_ladder():
    assert budget_spent(14.4, 1) == pytest.approx(0.02)
    assert budget_spent(6, 6) == pytest.approx(0.05)
    assert budget_spent(1, 72) == pytest.approx(0.10)

def test_14_4x_lasts_50_hours_not_one():
    assert hours_to_exhaustion(1.0, 14.4) == pytest.approx(50.0)

def test_budget_minutes_rolling_window():
    assert budget_minutes(0.999) == pytest.approx(43.2)
    assert budget_minutes(0.9995) == pytest.approx(21.6)
    assert budget_minutes(0.999, window_hours=30.44 * 24) == pytest.approx(43.83, abs=0.01)

def test_overspend_is_negative_not_clipped():
    assert budget_remaining(Counts(bad=30, total=10_000), 0.999) == pytest.approx(-2.0)

def test_no_traffic_is_unknown_not_healthy():
    assert budget_remaining(Counts(bad=0, total=0), 0.999) is None
    assert budget_remaining(Counts(bad=math.nan, total=100), 0.999) is None

def test_burn_rate():
    assert burn_rate(0.01, 0.9995) == pytest.approx(20.0)

def test_request_weighting_beats_ratio_averaging():
    quiet, busy = Counts(1, 10), Counts(10, 100_000)
    weighted = sum_counts([quiet, busy]).error_ratio()
    averaged = (quiet.error_ratio() + busy.error_ratio()) / 2
    assert weighted == pytest.approx(11 / 100_010)
    assert averaged > 100 * weighted          # the bug this function exists to prevent

def test_dead_service_is_unknown():
    h = SignalHealth(series_present=True, targets_up=False, probe_ok=None,
                     events_last_hour=0, min_events_per_hour=600)
    assert classify(h) is Integrity.STALE
    assert sli_state(Integrity.STALE, 90.0, False, False, 5.0) is SliState.UNKNOWN

def test_low_traffic_can_still_be_healthy():
    h = SignalHealth(True, True, True, events_last_hour=50, min_events_per_hour=600)
    assert classify(h) is Integrity.LOW_TRAFFIC
    assert sli_state(Integrity.LOW_TRAFFIC, 80.0, False, False, 5.0) is SliState.HEALTHY

def _window(bad_days: int, bad_ratio: float, traffic: float = 100.0,
            at_end: bool = False) -> list[Bucket]:
    """30 days of buckets; `bad_days` of them at `bad_ratio`, at the oldest or newest end."""
    start = datetime(2026, 10, 1, tzinfo=UTC)
    n, k = 30 * 288, bad_days * 288
    out = []
    for i in range(n):
        bad = (i >= n - k) if at_end else (i < k)
        out.append(Bucket(start + i * BUCKET, (bad_ratio if bad else 0.0) * traffic, traffic))
    return out

# 3 bad days at 1% on a 99.9% SLO: 30-day error ratio = 3/30 * 1% = 0.1% -> exactly 0% remaining.
# Reaching 10% remaining means dropping 86.4 bad requests' worth of buckets (87 buckets).

def test_eta_short_when_bad_minutes_are_oldest():
    w = _window(bad_days=3, bad_ratio=0.01)
    eta = unfreeze_eta(w, objective=0.999, threshold_pct=10.0)
    assert eta - w[-1].start == timedelta(minutes=5 * 88)          # 7 h 20 m

def test_eta_long_when_bad_minutes_are_recent():
    # Same budget, opposite timing: clean buckets age out first, so nothing improves for 27 days.
    w = _window(bad_days=3, bad_ratio=0.01, at_end=True)
    eta = unfreeze_eta(w, objective=0.999, threshold_pct=10.0)
    assert timedelta(days=27) < eta - w[-1].start < timedelta(days=28)

def test_eta_none_without_traffic():
    w = _window(bad_days=3, bad_ratio=0.01, traffic=0.0)
    assert unfreeze_eta(w, 0.999, 10.0) is None
```

Run with `uv run pytest -q platform/tests/test_budget.py`. These tests are part of `ci.yml`
and must never be marked xfail — if a refactor breaks one, the refactor is wrong.

---

## 6. Review checklist (use when reviewing anyone's SLO math)

- [ ] Budget remaining computed as `1 − e/a`, not `(o − e)/a` or `(a − e)/a`.
- [ ] Overspend allowed to go negative; consumed budget not clipped at 100%.
- [ ] Ratios aggregated request-weighted (sum bad, sum total), never averaged.
- [ ] Missing, NaN or zero-traffic windows return `None` and map to UNKNOWN.
- [ ] Window length stated (720 h rolling vs 730.5 h calendar average).
- [ ] Exhaustion time uses *remaining* budget, not a full budget.
- [ ] Timestamps timezone-aware UTC; displayed to the user in their zone (IST for Ricky).
- [ ] No I/O, no clock calls inside the math — `now` is passed in.

## 7. Interview soundbites

- "14.4x doesn't burn the month in an hour. It burns **2%** in an hour; at that pace the budget
  lasts 50 hours. That 2/5/10 ladder is where 14.4, 6 and 1 come from."
- "With a request-based SLI my budget is a count: 1 bad request in 1,000. Forty-three minutes is
  just what that would be as one full outage."
- "A dead service has a perfect error rate — unless your code knows the difference between
  zero errors and zero data. Mine returns *unknown*."
- "Rolling windows don't recover when you fix the bug; they recover when the bad minutes age
  out. Same 0% budget, two answers: 7 hours if the errors were 30 days ago, 27 days if they
  were yesterday. So my unfreeze ETA replays the buckets instead of guessing."
