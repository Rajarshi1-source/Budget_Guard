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
