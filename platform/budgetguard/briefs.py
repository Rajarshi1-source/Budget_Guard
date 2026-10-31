"""Freeze Brief (W5.8 template comment, W7.4 writer).

Two products, one rule: every number comes from the decision record.
  * template_comment(): the comment GitHub shows on the waiting run, written by the gate worker
    before it answers GitHub. Never an LLM.
  * BriefWriter: the Freeze Brief for Slack and the PR, written after the decision is committed
    (workers/brief_writer.py). brief_view() precomputes every number; the model formats, never
    computes; every LLM brief passes grounding.check at runtime or the template serves.
No model name in code: config/llm.yaml is the only place one appears.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from .gate import Decision, Verdict
from .grounding import CaseResult
from .integrations.llm import BriefModel, Completion, build_model

__all__ = ["BriefResult", "BriefWriter", "Completion", "Eta", "LlmConfig", "Prompt", "ValkeySpendLedger",
           "brief_view", "build_model", "load_prompt", "recovered_brief", "template_brief",
           "template_comment", "user_message"]

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


# ---------------------------------------------------------------- the view the model sees (W7.4)
def brief_view(decision: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """decision: a decisions row; ctx: context gathered by the worker. Pure; easy to golden-test."""
    return {
        "decision_id": decision["id"],
        "service": decision["service"],
        "environment": f"production-{decision['service']}",
        "sha": decision["sha"],
        "verdict": decision["verdict"],
        "reason_code": decision["reason_code"],
        "lane": ctx.get("lane"),
        "price_pct": _r(decision.get("price_pct")),
        "affordable_pct": _r(decision.get("affordable_pct")),
        "remaining_pct": _r(ctx.get("remaining_pct")),
        "reserve_pct": _r(ctx.get("reserve_pct")),
        "objective_pct": _r(ctx.get("objective_pct"), 3),
        "window_days": 30,
        "burn_rate_1h": _r(ctx.get("burn_rate_1h"), 1),
        "culprit": ctx.get("culprit"),
        "incident_id": ctx.get("incident_id"),
        "eta_utc": ctx.get("eta_utc"),                    # ISO "2026-10-25T14:35Z" or None
        "eta_in_hours": _r(ctx.get("eta_in_hours"), 1),
        "canary_profile": list(decision.get("canary_profile") or []),
        "still_allowed": ctx.get("still_allowed", []),
        "decision_url": ctx["decision_url"],
    }


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


VERB = {"admit": "admitted", "admit_unverified": "admitted unverified (fail-open)", "block": "blocked"}


def template_brief(v: dict[str, Any]) -> str:
    """Deterministic fallback; must score 100% on the golden set (stricter than the model's bar)."""
    lines = [f"{v['service']} deploy {v['sha'][:7]} {VERB[v['verdict']]} ({v['reason_code']})."]
    if v["lane"]:
        lines.append(f"Lane: {v['lane'].replace('_', ' ')}.")
    if v["price_pct"] is not None:
        afford = (f"; affordable {v['affordable_pct']:.2f}%" if v["affordable_pct"] is not None else "")
        lines.append(f"Price {v['price_pct']:.2f}% of the {v['window_days']}-day budget{afford}.")
    if v["remaining_pct"] is not None:
        lines.append(f"Budget remaining: {v['remaining_pct']:.2f}%.")
    if v["culprit"]:
        inc = f" ({v['incident_id']})" if v["incident_id"] else ""
        lines.append(f"Burn is attributed to {v['culprit']}{inc}.")
    if v["eta_utc"]:
        lines.append(f"Budget is projected to allow this change at {v['eta_utc']} "
                     f"(in {v['eta_in_hours']:.1f} hours) if traffic holds.")
    if v["still_allowed"]:
        lines.append("Still allowed: " + "; ".join(v["still_allowed"]) + ".")
    lines.append(f"Decision record: {v['decision_url']}")
    return "\n".join(lines)


def recovered_brief(service: str, remaining_pct: float, reserve_pct: float, decision_url: str) -> str:
    """'Budget recovered' variant [Rev 1 idea, W7.6]: the service that was blocked can afford deploys
    again. Numbers come from the gate's own reading at the time of the message."""
    return (f"{service}: budget recovered. Remaining {remaining_pct:.2f}% is above the {reserve_pct:.2f}% "
            f"reserve and no self-inflicted page is firing, so the next deploy is priced normally.\n"
            f"Last blocked decision: {decision_url}")


# ---------------------------------------------------------------- prompt registry
@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    system: str
    sha: str                        # versions the prompt exactly like a model change


def load_prompt(prompt_dir: Path, name: str = "freeze_brief") -> Prompt:
    text = (prompt_dir / f"{name}.md").read_text(encoding="utf-8")
    return Prompt(name, text, hashlib.sha256(text.encode()).hexdigest()[:12])


def user_message(view: dict[str, Any]) -> str:
    return "Decision record (the only facts you may use):\n" + json.dumps(view, indent=2, sort_keys=True)


# ---------------------------------------------------------------- the writer
@dataclass(frozen=True, slots=True)
class LlmConfig:
    provider: str
    model: str
    challenger: str | None
    timeout_ms: int
    max_output_tokens: int
    monthly_budget_usd: float
    price_per_mtok: dict[str, tuple[float, float]]          # model -> (input, output) USD

    @classmethod
    def load(cls, path: Path) -> LlmConfig:
        raw = dict(yaml.safe_load(path.read_text(encoding="utf-8"))["llm"])
        prices = {m: (float(p["input"]), float(p["output"])) for m, p in raw.pop("pricing", {}).items()}
        raw.pop("on_failure", None)
        return cls(price_per_mtok=prices, **raw)

    def cost_usd(self, c: Completion) -> float:
        pin, pout = self.price_per_mtok.get(c.model, (0.0, 0.0))
        return (c.input_tokens * pin + c.output_tokens * pout) / 1_000_000


class SpendLedger(Protocol):
    async def month_spend(self) -> float: ...
    async def add(self, usd: float) -> None: ...


class ValkeySpendLedger:
    """bg:llm:spend:{YYYYMM} (INCRBYFLOAT, 40-day TTL), the key the data-layer catalog names."""

    def __init__(self, valkey: Any, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.valkey, self.clock = valkey, clock

    def _key(self) -> str:
        return f"bg:llm:spend:{self.clock():%Y%m}"

    async def month_spend(self) -> float:
        return float(await self.valkey.get(self._key()) or 0.0)

    async def add(self, usd: float) -> None:
        if usd > 0:
            await self.valkey.incrbyfloat(self._key(), usd)
            await self.valkey.expire(self._key(), 40 * 86400)


@dataclass
class BriefResult:
    text: str
    source: str                     # "llm" | "template"
    fallback_reason: str | None
    model: str | None
    prompt_sha: str
    latency_ms: float
    cost_usd: float


class BriefWriter:
    """Template fallback on: provider template, open circuit (3 failures -> 5 minutes), monthly budget
    spent, timeout / provider error, or a brief that fails the runtime grounding check."""

    def __init__(self, cfg: LlmConfig, model: BriefModel | None, ledger: SpendLedger, prompt: Prompt,
                 guard: Callable[[str, dict[str, Any]], CaseResult]) -> None:
        self.cfg, self.model, self.ledger, self.prompt, self.guard = cfg, model, ledger, prompt, guard
        self._failures, self._open_until = 0, 0.0

    def _fallback(self, view: dict[str, Any], why: str, ms: float = 0.0, cost: float = 0.0) -> BriefResult:
        return BriefResult(template_brief(view), "template", why, None, self.prompt.sha, ms, cost)

    async def write(self, view: dict[str, Any]) -> BriefResult:
        if self.model is None:
            return self._fallback(view, "provider=template")
        if time.monotonic() < self._open_until:
            return self._fallback(view, "circuit_open")
        if await self.ledger.month_spend() >= self.cfg.monthly_budget_usd:
            return self._fallback(view, "monthly_budget_exhausted")
        try:
            c = await asyncio.wait_for(
                self.model.complete(self.prompt.system, user_message(view),
                                    max_output_tokens=self.cfg.max_output_tokens),
                timeout=self.cfg.timeout_ms / 1000)
        except Exception as exc:                        # noqa: BLE001 - timeout, 429, 5xx, network: all fall back
            self._failures += 1
            if self._failures >= 3:
                self._open_until, self._failures = time.monotonic() + 300, 0
            return self._fallback(view, f"error:{type(exc).__name__}")
        self._failures = 0
        cost = self.cfg.cost_usd(c)
        await self.ledger.add(cost)
        result = self.guard(c.text, view)
        if not result.passed:
            why = "grounding_failed:" + ",".join(result.ungrounded + result.forbidden)[:200]
            return self._fallback(view, why,
                                  c.latency_ms, cost)
        return BriefResult(c.text.strip(), "llm", None, c.model, self.prompt.sha, c.latency_ms, cost)
