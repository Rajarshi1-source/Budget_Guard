from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from budgetguard.slo_registry import SloQueries, spec_hash
from budgetguard.workers.snapshotter import BUCKET, aligned_end, bucket_row, snapshot_range

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def slo(type_: str = "ratio", floor: float = 600) -> SloQueries:
    return SloQueries(id=7, service="orders-service", name="availability", type=type_,  # type: ignore[arg-type]
                      objective=0.999, total="total", bad="bad" if type_ == "ratio" else None,
                      good="good" if type_ != "ratio" else None, self_bad="self", min_events_per_hour=floor)


class FakeProm:
    def __init__(self, series: dict[str, dict[float, float]]) -> None:
        self.series = series
        self.calls: list[tuple[str, datetime, datetime, int]] = []

    async def range_values(self, expr: str, start: datetime, end: datetime,
                           step_s: int) -> dict[float, float]:
        self.calls.append((expr, start, end, step_s))
        name = expr.removeprefix("sum(increase(").removesuffix("[5m]))")
        return self.series.get(name, {})


@pytest.mark.parametrize(("now", "end"), [
    (T0 + timedelta(minutes=5, seconds=50), T0 + timedelta(minutes=5)),   # settled
    (T0 + timedelta(minutes=5, seconds=30), T0),                          # last bucket not settled yet
    (T0 + timedelta(minutes=9, seconds=59), T0 + timedelta(minutes=5)),
])
def test_aligned_end_waits_for_the_bucket_to_settle(now: datetime, end: datetime) -> None:
    assert aligned_end(now) == end


def test_ratio_bucket() -> None:
    r = bucket_row(slo(), T0, total=6000, bad=12, good=None, self_bad=3)
    assert (r.total, r.bad, r.self_bad, r.integrity) == (6000, 12, 3, "ok")


def test_absent_error_series_means_zero_errors_when_traffic_exists() -> None:
    r = bucket_row(slo(), T0, total=6000, bad=None, good=None, self_bad=None)
    assert (r.bad, r.self_bad, r.integrity) == (0, 0, "ok")


def test_absent_total_is_no_data_not_zero_errors() -> None:
    r = bucket_row(slo(), T0, total=None, bad=None, good=None, self_bad=None)
    assert (r.total, r.bad, r.integrity) == (0, 0, "no_data")


def test_latency_without_good_series_counts_every_event_bad() -> None:
    # e.g. notifications: every delivery failed, so the outcome="delivered" bucket series never appeared
    r = bucket_row(slo("latency_bucket"), T0, total=100, bad=None, good=None, self_bad=None)
    assert r.bad == 100


def test_latency_bad_is_total_minus_good() -> None:
    r = bucket_row(slo("latency_bucket"), T0, total=6000, bad=None, good=5940, self_bad=None)
    assert r.bad == pytest.approx(60)


def test_extrapolation_noise_is_clamped() -> None:
    r = bucket_row(slo(), T0, total=10, bad=10.4, good=None, self_bad=11)
    assert (r.bad, r.self_bad) == (10, 10)
    r = bucket_row(slo("latency_bucket"), T0, total=10, bad=None, good=10.3, self_bad=None)
    assert r.bad == 0


def test_low_traffic_uses_the_hourly_floor() -> None:
    assert bucket_row(slo(floor=600), T0, 49, 0, None, 0).integrity == "low_traffic"   # 588/h
    assert bucket_row(slo(floor=600), T0, 50, 0, None, 0).integrity == "ok"            # 600/h
    assert bucket_row(slo(floor=0), T0, 0, 0, None, 0).integrity == "low_traffic"      # zero traffic


async def test_snapshot_range_reads_one_range_per_series() -> None:
    starts = [T0 + i * BUCKET for i in range(3)]
    ts = [(s + BUCKET).timestamp() for s in starts]
    prom = FakeProm({"total": {ts[0]: 6000, ts[2]: 6000}, "bad": {ts[0]: 6}, "self": {}})
    rows = await snapshot_range(prom, slo(), starts)  # type: ignore[arg-type]
    assert [r.integrity for r in rows] == ["ok", "no_data", "ok"]
    assert [r.bad for r in rows] == [6, 0, 0]
    assert len(prom.calls) == 3                                        # total, bad, self_bad
    assert {(c[1], c[2], c[3]) for c in prom.calls} == {(starts[0] + BUCKET, starts[-1] + BUCKET, 300)}


async def test_snapshot_range_with_nothing_missing_queries_nothing() -> None:
    prom = FakeProm({})
    assert await snapshot_range(prom, slo(), []) == []  # type: ignore[arg-type]
    assert prom.calls == []


def test_spec_hash_tracks_objective_and_queries_only() -> None:
    from pathlib import Path

    from budgetguard.rulegen.spec import load

    sl = load(Path(__file__).resolve().parents[2] / "slo" / "orders-service.yaml")
    base = spec_hash(sl, sl.spec.slos[0])
    assert spec_hash(sl, sl.spec.slos[0]) == base
    changed = sl.spec.slos[0].model_copy(update={"objective": 0.995})
    assert spec_hash(sl, changed) != base
    retuned: Any = sl.spec.slos[0].model_copy(deep=True)
    retuned.alerting.page[0].burn = 10.0                               # alert tuning is not SLO drift
    assert spec_hash(sl, retuned) == base
