"""D-5: rolling windows recover when old bad buckets age out, not when errors stop."""
from datetime import UTC, datetime, timedelta

import pytest

from budgetguard.eta import BUCKET, Bucket, unfreeze_eta


def _window(bad_days: int, bad_ratio: float, traffic: float = 100.0, at_end: bool = False) -> list[Bucket]:
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


def test_eta_short_when_bad_minutes_are_oldest() -> None:
    w = _window(bad_days=3, bad_ratio=0.01)
    eta = unfreeze_eta(w, objective=0.999, threshold_pct=10.0)
    assert eta is not None and eta - w[-1].start == timedelta(minutes=5 * 88)          # 7 h 20 m


def test_eta_long_when_bad_minutes_are_recent() -> None:
    # Same budget, opposite timing: clean buckets age out first, so nothing improves for 27 days.
    w = _window(bad_days=3, bad_ratio=0.01, at_end=True)
    eta = unfreeze_eta(w, objective=0.999, threshold_pct=10.0)
    assert eta is not None and timedelta(days=27) < eta - w[-1].start < timedelta(days=28)


def test_eta_none_without_traffic() -> None:
    w = _window(bad_days=3, bad_ratio=0.01, traffic=0.0)
    assert unfreeze_eta(w, 0.999, 10.0) is None


def test_eta_now_when_already_above_threshold() -> None:
    w = _window(bad_days=0, bad_ratio=0.0)
    assert unfreeze_eta(w, 0.999, 10.0) == w[-1].start + BUCKET


def test_eta_none_when_future_errors_keep_it_frozen() -> None:
    w = _window(bad_days=3, bad_ratio=0.01, at_end=True)
    assert unfreeze_eta(w, 0.999, 10.0, future_error_ratio=0.002) is None    # 2x burn forever: never


def test_half_budget_pace_is_later_than_zero_errors() -> None:
    w = _window(bad_days=3, bad_ratio=0.01)
    zero = unfreeze_eta(w, 0.999, 10.0)
    half = unfreeze_eta(w, 0.999, 10.0, future_error_ratio=0.0005)
    assert zero is not None and half is not None and half > zero


def test_naive_timestamps_are_rejected() -> None:
    w = [Bucket(datetime(2026, 10, 1), 0.0, 100.0)]
    with pytest.raises(ValueError, match="timezone"):
        unfreeze_eta(w, 0.999, 10.0)


def test_empty_window_is_unknown() -> None:
    assert unfreeze_eta([], 0.999, 10.0) is None
