"""Freeze Brief, template form (W5.8). The comment GitHub shows on the waiting run is built only
from the decision record: every number here is a field of the decision or its inputs, never free
text from a person and never an LLM (the LLM brief writer arrives in W7, off the critical path).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .gate import Decision, Verdict

MAX_COMMENT = 1000                      # GitHub's limit for a protection-rule review comment


@dataclass(frozen=True, slots=True)
class Eta:
    """When this deploy becomes affordable (eta.unfreeze_eta to reserve + price / spend fraction),
    if errors stop and if they continue at half the allowed pace. None = no projection (no recent
    traffic, or not within 30 days)."""
    if_clean: datetime | None
    at_half_budget: datetime | None


def _when(t: datetime | None) -> str:
    return "not within 30 days" if t is None else t.strftime("%Y-%m-%d %H:%M UTC")


def template_comment(service: str, d: Decision, decision_url: str, eta: Eta | None,
                     remaining_pct: float | None, note: str = "") -> str:
    verdict = {Verdict.ADMIT: "Admitted", Verdict.ADMIT_UNVERIFIED: "Admitted UNVERIFIED",
               Verdict.BLOCK: "Blocked"}[d.verdict]
    lines = [f"BudgetGuard: {verdict} ({d.reason_code}) for {service}.", d.reason + "."]
    if d.price_pct is not None and d.affordable_pct is not None:
        lines.append(f"Price {d.price_pct:.2f}% of the 30-day budget; "
                     f"affordable now {d.affordable_pct:.2f}%.")
    if remaining_pct is not None:
        lines.append(f"Budget remaining: {remaining_pct:.1f}%.")
    if note:
        lines.append(f"Change: {note}.")
    if d.verdict is not Verdict.BLOCK:
        lines.append(f"Canary: {' -> '.join(f'{w}%' for w in d.canary_profile)}.")
    if eta is not None:
        lines.append(f"This deploy becomes affordable: {_when(eta.if_clean)} if errors stop; "
                     f"{_when(eta.at_half_budget)} at half the allowed error rate.")
    elif d.reason_code == "ACTIVE_PAGE":
        lines.append("It clears when the page-level burn alert resolves.")
    if d.verdict is Verdict.BLOCK:
        lines.append("A proven rollback is always admitted; an approver can override from the decision page.")
    lines.append(f"Decision: {decision_url}")
    text = "\n".join(lines)
    return text if len(text) <= MAX_COMMENT else text[:MAX_COMMENT - 1] + "…"
