"""Alertmanager webhook (v4 payload) -> incident events. Pure, so it is easy to test."""
from __future__ import annotations

from datetime import datetime

from .store import IncidentEvent

ZERO_TIME = "0001-01-01T00:00:00Z"     # Alertmanager's "not resolved" endsAt


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def events_from_payload(payload: dict) -> list[IncidentEvent]:
    out = []
    for a in payload.get("alerts", []):
        labels = a.get("labels", {})
        if "service" not in labels or "incident_type" not in labels:
            continue                            # not a BudgetGuard alert
        resolved = a.get("status") == "resolved"
        ends = a.get("endsAt") or ZERO_TIME
        out.append(IncidentEvent(
            fingerprint=a["fingerprint"],
            service=labels["service"],
            alertname=labels.get("alertname", "unknown"),
            slo=labels.get("slo"),
            incident_type=labels["incident_type"],
            severity=labels.get("severity", "ticket"),
            started_at=_ts(a["startsAt"]),          # with the fingerprint: identifies one occurrence
            resolved_at=_ts(ends) if resolved and not ends.startswith("0001") else None,
        ))
    return out
