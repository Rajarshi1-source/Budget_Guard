"""D-4: a dead service has a perfect error rate unless the code knows zero errors from zero data."""
import pytest

from budgetguard.integrity import Integrity, SignalHealth, SliState, classify, sli_state


def test_dead_service_is_unknown() -> None:
    h = SignalHealth(series_present=True, targets_up=False, probe_ok=None,
                     events_last_hour=0, min_events_per_hour=600)
    assert classify(h) is Integrity.STALE
    assert sli_state(Integrity.STALE, 90.0, False, False, 5.0) is SliState.UNKNOWN


def test_low_traffic_can_still_be_healthy() -> None:
    h = SignalHealth(True, True, True, events_last_hour=50, min_events_per_hour=600)
    assert classify(h) is Integrity.LOW_TRAFFIC
    assert sli_state(Integrity.LOW_TRAFFIC, 80.0, False, False, 5.0) is SliState.HEALTHY


@pytest.mark.parametrize(("health", "expected"), [
    (SignalHealth(False, False, False, 0, 600), Integrity.NO_DATA),      # absence beats staleness
    (SignalHealth(True, True, False, 9_000, 600), Integrity.STALE),     # probe failing while metrics flow
    (SignalHealth(True, None, None, 9_000, 600), Integrity.OK),         # no target info is not "down"
    (SignalHealth(True, True, True, 600, 600), Integrity.OK),           # exactly at the floor
])
def test_classification_order(health: SignalHealth, expected: Integrity) -> None:
    assert classify(health) is expected


def test_missing_budget_number_is_unknown_even_when_signal_is_ok() -> None:
    assert sli_state(Integrity.OK, None, False, False, 5.0) is SliState.UNKNOWN


@pytest.mark.parametrize(("page", "ticket", "remaining"),
                         [(True, False, 90.0), (False, True, 90.0), (False, False, 4.9)])
def test_at_risk_on_alerts_or_below_reserve(page: bool, ticket: bool, remaining: float) -> None:
    assert sli_state(Integrity.OK, remaining, page, ticket, 5.0) is SliState.AT_RISK


def test_negative_budget_is_at_risk_not_unknown() -> None:
    assert sli_state(Integrity.OK, -12.5, False, False, 5.0) is SliState.AT_RISK
