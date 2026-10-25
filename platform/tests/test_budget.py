"""The interview arithmetic, pinned (budgetguard-slo-math §5). Never mark these xfail."""
import math

import pytest

from budgetguard.budget import (
    Counts,
    budget_minutes,
    budget_remaining,
    budget_remaining_pct,
    budget_spent,
    burn_rate,
    hours_to_exhaustion,
    sum_counts,
)


def test_workbook_ladder() -> None:
    assert budget_spent(14.4, 1) == pytest.approx(0.02)
    assert budget_spent(6, 6) == pytest.approx(0.05)
    assert budget_spent(1, 72) == pytest.approx(0.10)


def test_14_4x_lasts_50_hours_not_one() -> None:
    assert hours_to_exhaustion(1.0, 14.4) == pytest.approx(50.0)


def test_exhaustion_uses_remaining_budget_and_never_negative() -> None:
    assert hours_to_exhaustion(0.5, 14.4) == pytest.approx(25.0)
    assert hours_to_exhaustion(-0.3, 14.4) == 0.0
    assert hours_to_exhaustion(1.0, 0.0) is None


def test_budget_minutes_rolling_window() -> None:
    assert budget_minutes(0.999) == pytest.approx(43.2)
    assert budget_minutes(0.9995) == pytest.approx(21.6)
    assert budget_minutes(0.999, window_hours=30.44 * 24) == pytest.approx(43.83, abs=0.01)


@pytest.mark.parametrize(("objective", "minutes"),
                         [(0.9995, 21.6), (0.999, 43.2), (0.995, 216.0), (0.99, 432.0)])
def test_per_service_reference_table(objective: float, minutes: float) -> None:
    assert budget_minutes(objective) == pytest.approx(minutes)            # Rev 2 §2


def test_overspend_is_negative_not_clipped() -> None:
    assert budget_remaining(Counts(bad=30, total=10_000), 0.999) == pytest.approx(-2.0)
    assert budget_remaining_pct(Counts(bad=30, total=10_000), 0.999) == pytest.approx(-200.0)


def test_remaining_is_one_minus_e_over_a() -> None:
    assert budget_remaining(Counts(bad=3, total=10_000), 0.999) == pytest.approx(0.70)   # not (o-e)/a (B-3)


def test_no_traffic_is_unknown_not_healthy() -> None:
    assert budget_remaining(Counts(bad=0, total=0), 0.999) is None
    assert budget_remaining(Counts(bad=math.nan, total=100), 0.999) is None
    assert budget_remaining_pct(Counts(bad=0, total=0), 0.999) is None


def test_burn_rate() -> None:
    assert burn_rate(0.01, 0.9995) == pytest.approx(20.0)


def test_objective_must_be_a_fraction() -> None:
    with pytest.raises(ValueError):
        burn_rate(0.01, 99.9)


def test_request_weighting_beats_ratio_averaging() -> None:
    quiet, busy = Counts(1, 10), Counts(10, 100_000)
    weighted = sum_counts([quiet, busy]).error_ratio()
    q, b = quiet.error_ratio(), busy.error_ratio()
    assert weighted is not None and q is not None and b is not None
    assert weighted == pytest.approx(11 / 100_010)
    assert (q + b) / 2 > 100 * weighted          # the bug this function exists to prevent
