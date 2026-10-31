import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from budgetguard.briefs import (
    BriefWriter,
    Completion,
    LlmConfig,
    ValkeySpendLedger,
    brief_view,
    load_prompt,
    recovered_brief,
    template_brief,
)
from evals.grounding import check
from evals.make_golden import build
from evals.run_brief_eval import run, wilson_lower

ROOT = Path(__file__).parent.parent
VIEW = brief_view(
    {"id": 4812, "service": "orders-service", "sha": "9f3c2ab" + "0" * 33, "verdict": "block",
     "reason_code": "OVER_BUDGET", "price_pct": 1.26, "affordable_pct": 1.0, "canary_profile": []},
    {"remaining_pct": 7.0, "reserve_pct": 5.0, "objective_pct": 99.9, "burn_rate_1h": 3.2,
     "culprit": "orders-service", "incident_id": "INC-1042", "eta_utc": "2026-10-25T07:30Z",
     "eta_in_hours": 22.5, "still_allowed": ["rollback to a previously healthy build"],
     "decision_url": "https://budgetguard.example.dev/decisions/4812"})


def test_template_is_grounded_on_every_golden_case():
    for case in build():
        r = check(template_brief(case["view"]), case["view"])
        assert r.passed, (case["id"], r.ungrounded, r.forbidden)
        assert r.fact_recall == 1.0, (case["id"], r.missing_facts)


def test_golden_composition():
    kinds = [c["kind"] for c in build()]
    counts = (kinds.count("admit"), kinds.count("block"), kinds.count("lane"), kinds.count("unknown"))
    assert counts == (40, 40, 20, 20)
    assert build() == build()


GOOD = ("orders-service deploy 9f3c2ab is blocked: the change costs 1.26% of the 30-day budget "
        "but only 1.0% is affordable with 7% remaining. The burn comes from orders-service (INC-1042). "
        "It should fit again at 07:30 UTC on 2026-10-25, in about 22.5 hours. "
        "A rollback to a previously healthy build is still allowed.\n"
        "https://budgetguard.example.dev/decisions/4812")


def test_good_brief_passes():
    r = check(GOOD, VIEW)
    assert r.passed and r.fact_recall == 1.0, (r.ungrounded, r.missing_facts)


@pytest.mark.parametrize("bad, reason", [
    (GOOD.replace("1.26%", "1.62%"), "number:1.62"),
    (GOOD.replace("22.5 hours", "23.4 hours"), "number:23.4"),
    (GOOD.replace("07:30", "08:30"), "time:08:30"),
    (GOOD.replace("INC-1042", "INC-1043"), "incident:INC-1043"),
    (GOOD.replace("9f3c2ab", "9f3c2ac"), "sha:9f3c2ac"),
    (GOOD + " The burn comes from payments-service.", "service:payments-service"),
    (GOOD.replace("2026-10-25", "2026-10-26"), "date:2026-10-26"),
])
def test_hallucinations_are_caught(bad, reason):
    assert reason in check(bad, VIEW).ungrounded


def test_dates_are_not_read_as_negative_numbers():
    """Rev 2's regex would read 2026-10-25 as 2026, -10, -25 and fail a correct brief."""
    r = check(GOOD, VIEW)
    assert not any(u.startswith("number:-") for u in r.ungrounded)


def test_rounding_follows_printed_precision():
    assert check(GOOD.replace("1.26%", "1.3%"), VIEW).grounded
    assert not check(GOOD.replace("1.26%", "1.30%"), VIEW).grounded


def test_override_suggestion_is_forbidden():
    r = check(GOOD + " If urgent, ask an admin to override the gate.", VIEW)
    assert not r.passed and r.forbidden


def test_invented_cause_is_forbidden():
    view = dict(VIEW, culprit=None)
    r = check(GOOD.replace("The burn comes from orders-service (INC-1042). ", "") +
              " This is due to auth-service.", view)
    assert any("unsupported cause" in f for f in r.forbidden)


def test_missing_eta_is_a_missing_fact():
    r = check(GOOD.replace("It should fit again at 07:30 UTC on 2026-10-25, in about 22.5 hours. ", ""),
              VIEW)
    assert "eta" in r.missing_facts and r.fact_recall < 1.0


class FakeModel:
    def __init__(self, text: str, delay: float = 0.0, fail: bool = False):
        self.model, self.text, self.delay, self.fail = "fake-model", text, delay, fail

    async def complete(self, system, user, *, max_output_tokens):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("503")
        return Completion(self.text, 1500, 250, self.delay * 1000, self.model)


class Ledger:
    def __init__(self, spent=0.0):
        self.spent = spent

    async def month_spend(self):
        return self.spent

    async def add(self, usd):
        self.spent += usd


CFG = LlmConfig("openai", "fake-model", None, 200, 300, 5.0, {"fake-model": (0.75, 4.50)})


def _writer(model, ledger=None):
    return BriefWriter(CFG, model, ledger or Ledger(), load_prompt(ROOT / "prompts"), check)


def test_writer_serves_grounded_llm_brief_and_prices_it():
    r = asyncio.run(_writer(FakeModel(GOOD)).write(VIEW))
    assert r.source == "llm" and abs(r.cost_usd - 0.00225) < 1e-9


@pytest.mark.parametrize("model, ledger, why", [
    (FakeModel(GOOD.replace("1.26", "1.62")), None, "grounding_failed"),
    (FakeModel(GOOD, delay=0.5), None, "error:TimeoutError"),
    (FakeModel(GOOD, fail=True), None, "error:RuntimeError"),
    (FakeModel(GOOD), Ledger(5.0), "monthly_budget_exhausted"),
    (None, None, "provider=template"),
])
def test_writer_falls_back_to_template(model, ledger, why):
    r = asyncio.run(_writer(model, ledger).write(VIEW))
    assert r.source == "template" and r.fallback_reason.startswith(why)
    assert check(r.text, VIEW).passed


def test_circuit_opens_after_three_failures():
    w = _writer(FakeModel(GOOD, fail=True))
    for _ in range(3):
        asyncio.run(w.write(VIEW))
    assert asyncio.run(w.write(VIEW)).fallback_reason == "circuit_open"


def test_eval_runner_template_mode(tmp_path):
    golden = tmp_path / "g.jsonl"
    golden.write_text("\n".join(json.dumps(c) for c in build()))
    out = asyncio.run(run("template", None, golden, ROOT / "config/llm.yaml", prompt_dir=ROOT / "prompts"))
    s = out["summary"]
    assert s["failures"] == [] and s["grounding"] == 1.0 and s["cases"] == 120


def test_wilson_bound_on_119_of_120():
    assert 0.95 < wilson_lower(119, 120) < 0.96


def test_committed_golden_set_is_what_make_golden_builds() -> None:
    committed = [json.loads(line) for line in (ROOT / "evals/golden/briefs.jsonl").read_text().splitlines()]
    assert committed == build()


def test_still_allowed_never_suggests_an_override() -> None:
    """FORBIDDEN catches override wording, so what the brief lists as still allowed must avoid it."""
    for case in build():
        assert check(" ".join(case["view"]["still_allowed"]), case["view"]).forbidden == []


def test_budget_recovered_variant_is_grounded() -> None:
    view = brief_view({"id": 7, "service": "orders-service", "sha": "a" * 40, "verdict": "block",
                       "reason_code": "OVER_BUDGET", "price_pct": None, "affordable_pct": None,
                       "canary_profile": []},
                      {"remaining_pct": 12.34, "reserve_pct": 5.0,
                       "decision_url": "https://bg.example/decisions/7"})
    text = recovered_brief("orders-service", 12.34, 5.0, "https://bg.example/decisions/7")
    assert check(text, view).grounded and "recovered" in text


class FakeValkey:
    def __init__(self) -> None:
        self.kv: dict[str, float] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.kv[key] = self.kv.get(key, 0.0) + amount
        return self.kv[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.ttl[key] = seconds


def test_spend_ledger_is_monthly_in_valkey() -> None:
    v = FakeValkey()
    led = ValkeySpendLedger(v, clock=lambda: datetime(2026, 10, 31, tzinfo=UTC))
    asyncio.run(led.add(0.002))
    asyncio.run(led.add(0.0))
    assert asyncio.run(led.month_spend()) == 0.002 and v.ttl == {"bg:llm:spend:202610": 40 * 86400}
    nov = ValkeySpendLedger(v, clock=lambda: datetime(2026, 11, 1, tzinfo=UTC))
    assert asyncio.run(nov.month_spend()) == 0.0
