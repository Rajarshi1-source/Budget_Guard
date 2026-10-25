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
