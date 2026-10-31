"""Freeze Brief grounding checks. Used twice: offline in brief-eval.yml (evals.grounding re-exports this
module) and online as the brief writer's runtime guard, which is why it lives in the package.

Fixes over Rev 2 §10.5:
  * Allowed numbers come from the numeric leaves of the brief view, not str(record) - a hex SHA
    or a URL in the record no longer whitelists arbitrary digits.
  * Dates, clock times, URLs, incident IDs, SHAs and service names are extracted and checked as
    identifiers BEFORE number scanning, so "2026-10-25" is not read as 2026, -10 and -25.
  * Rounding tolerance follows the precision the brief printed: "43" grounds 43.2, "1.3"
    grounds 1.26, but "1.30" does not ground 1.26.
  * Required facts are checked semantically (verdict words, numbers at displayed precision,
    the ETA as a time or as "in N hours"), and a missing value is not counted as a hit.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

URL = re.compile(r"https?://\S+")
DATETIME = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?\b")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
INCIDENT = re.compile(r"\bINC-\d+\b")
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")          # 7+ hex chars (all-digit SHA prefixes too)
SERVICE = re.compile(r"\b[a-z]+(?:-[a-z]+)*-service\b")
NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
FORBIDDEN = re.compile(r"\b(override|overrid\w*|bypass\w*|force[- ]?deploy\w*|skip(?:ping)? the gate|"
                       r"admin[- ]merge|disable the (?:gate|rule))\b", re.I)
CAUSAL = re.compile(r"\b(?:caused by|due to|because of|root cause(?: is| was)?|triggered by)\s+"
                    r"(?:the\s+)?([a-z]+(?:-[a-z]+)*-service)\b", re.I)

VERDICT_WORDS = {
    "admit": ("admitted", "approved", "allowed to deploy", "can deploy"),
    "admit_unverified": ("unverified", "fail-open", "fail open"),
    "block": ("blocked", "rejected", "frozen", "cannot deploy"),
}


def _numeric_leaves(obj: Any) -> Iterable[float]:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _numeric_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _numeric_leaves(v)


def _string_leaves(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _string_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _string_leaves(v)


def _matches_at_precision(text_num: str, value: float) -> bool:
    decimals = len(text_num.split(".")[1]) if "." in text_num else 0
    n = abs(float(text_num))
    return abs(round(abs(value), decimals) - n) < 10 ** -(decimals + 3) or \
        abs(abs(value) - n) <= 0.5 * 10 ** -decimals + 1e-9


@dataclass
class CaseResult:
    grounded: bool
    fact_recall: float
    forbidden: list[str] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.grounded and not self.forbidden


def check(brief: str, view: dict[str, Any]) -> CaseResult:
    strings = " ".join(_string_leaves(view))
    numbers = list(_numeric_leaves(view))
    ungrounded: list[str] = []
    text = brief

    # 1. identifiers, removed from the text once checked
    for url in URL.findall(text):
        if url.rstrip(").,") not in strings:
            ungrounded.append(f"url:{url}")
    text = URL.sub(" ", text)
    for dt in DATETIME.findall(text):
        if not any(s.startswith(dt[:10]) for s in _string_leaves(view)):
            ungrounded.append(f"date:{dt}")
    text = DATETIME.sub(" ", text)
    eta = view.get("eta_utc") or ""
    for clock in CLOCK.findall(text):
        if clock.zfill(5) not in eta:
            ungrounded.append(f"time:{clock}")
    text = CLOCK.sub(" ", text)
    for inc in INCIDENT.findall(text):
        if inc not in strings:
            ungrounded.append(f"incident:{inc}")
    text = INCIDENT.sub(" ", text)
    shas = [s for s in _string_leaves(view) if re.fullmatch(r"[0-9a-f]{40}", s)]
    for h in HEX.findall(text):
        if not any(s.startswith(h) for s in shas):
            ungrounded.append(f"sha:{h}")
    text = HEX.sub(" ", text)
    for svc in SERVICE.findall(text):
        if svc not in strings:
            ungrounded.append(f"service:{svc}")

    # 2. every remaining number must exist in the view at the precision printed
    for n in NUM.findall(text):
        if not any(_matches_at_precision(n, v) for v in numbers):
            ungrounded.append(f"number:{n}")

    # 3. forbidden content
    forbidden = [m.group(0) for m in FORBIDDEN.finditer(brief)]
    culprit = view.get("culprit")
    for m in CAUSAL.finditer(brief):
        if m.group(1) != culprit:
            forbidden.append(f"unsupported cause: {m.group(0)}")

    # 4. required facts
    lower = brief.lower()
    required: list[tuple[str, bool]] = [
        ("verdict", any(w in lower for w in VERDICT_WORDS[view["verdict"]])),
    ]
    for key in ("price_pct", "affordable_pct"):
        if view.get(key) is not None:
            required.append((key, any(_matches_at_precision(n, view[key]) for n in NUM.findall(text))))
    if view.get("eta_utc"):
        hhmm = view["eta_utc"][11:16]
        hours = view.get("eta_in_hours")
        in_hours = hours is not None and any(
            _matches_at_precision(n, hours) for n in re.findall(r"(\d+(?:\.\d+)?)\s*(?:h\b|hours?)", lower))
        required.append(("eta", hhmm in brief or in_hours))
    if view.get("lane"):
        required.append(("lane", view["lane"].replace("_", " ") in lower or view["lane"] in lower))
    if culprit:
        required.append(("culprit", culprit in brief))
    hits = sum(ok for _, ok in required)
    return CaseResult(
        grounded=not ungrounded,
        fact_recall=hits / len(required),
        forbidden=forbidden,
        ungrounded=ungrounded,
        missing_facts=[k for k, ok in required if not ok],
    )
