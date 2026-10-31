import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from budgetguard.briefs import MAX_COMMENT, Eta, template_comment
from budgetguard.config import get_settings
from budgetguard.gate import CONSERVATIVE, Decision, Verdict
from budgetguard.gather import combine
from budgetguard.integrations.github_app import JobRejected
from budgetguard.integrations.prometheus import PrometheusClient
from budgetguard.integrity import Integrity, SliState
from budgetguard.policy import Policy
from budgetguard.rulegen.spec import load_dir
from budgetguard.signals import SloStatus
from budgetguard.workers import gate_worker as gw

ROOT = Path(__file__).resolve().parents[2]
SPECS = {sl.metadata.service: sl for sl in load_dir(ROOT / "slo")}
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
CALLBACK = "https://api.github.com/repos/ricky/budgetguard/actions/runs/777/deployment_protection_rule"


def job(**kw: Any) -> dict[str, Any]:
    base = {"service": "orders-service", "environment": "production-orders-service", "callback_url": CALLBACK,
            "run_id": 777, "installation_id": 99, "sha": "9f3c2ab" + "0" * 33, "deployment_id": 5001,
            "repo": "ricky/budgetguard", "delivery": "d-1"}
    return {**base, **kw}


class FakeValkey:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.acked: list[str] = []
        self.log: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, nx: bool = False, px: int | None = None,
                  ex: int | None = None) -> bool:
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    async def xadd(self, stream: str, fields: dict[str, str], maxlen: int | None = None,
                   approximate: bool = True) -> str:
        self.log.append(f"xadd {stream}")
        entries = self.streams.setdefault(stream, [])
        entries.append((f"{len(entries)}-0", fields))
        return entries[-1][0]

    async def xack(self, stream: str, group: str, entry_id: str) -> int:
        self.acked.append(entry_id)
        return 1

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        key, token = args
        if self.kv.get(key) == token:
            del self.kv[key]
            return 1
        return 0


def healthy_prometheus(request: httpx.Request) -> httpx.Response:
    q = request.url.params.get("query", "")
    if q.startswith("ALERTS"):
        result: list[Any] = []
    elif "increase30d" in q or "rate1h" in q:
        result = [{"metric": {}, "value": [0, "0.0001"]}]          # 10% of a 99.9% budget spent
    elif "increase(" in q:
        result = [{"metric": {}, "value": [0, "36000"]}]           # traffic well above the floor
    else:
        result = [{"metric": {}, "value": [0, "1"]}]               # series present, up, probe ok
    return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": result}})


def ctx(valkey: FakeValkey | None, posts: list[tuple[dict[str, Any], bool, str]],
        log: list[str] | None = None) -> gw.Context:
    http = httpx.AsyncClient(transport=httpx.MockTransport(healthy_prometheus))

    async def post(_: httpx.AsyncClient, j: dict[str, Any], approved: bool, comment: str) -> None:
        from budgetguard.integrations.github_app import validate_job
        validate_job(j)                                            # post_review's own first step
        if log is not None:
            log.append("callback")
        posts.append((j, approved, comment))

    return gw.Context(settings=get_settings(), policy=Policy("v1"), specs=SPECS,
                      prom=PrometheusClient("http://prometheus:9090", http), engine=None, valkey=valkey,
                      http=http, post=post, clock=lambda: NOW)


async def test_forged_callback_is_rejected_before_any_token_or_record() -> None:
    v, posts = FakeValkey(), []
    for bad in (job(callback_url="https://evil.example/repos/ricky/budgetguard/actions/runs/777/"
                                 "deployment_protection_rule"),
                job(repo="mallory/budgetguard"), job(installation_id=1), job(run_id=778),
                job(service="auth-service")):
        with pytest.raises(JobRejected):
            await gw.evaluate_job(ctx(v, posts), bad)
    assert posts == [] and v.streams == {}


async def test_postgres_down_buffers_the_record_before_answering_github() -> None:
    v, posts = FakeValkey(), []
    out = await gw.evaluate_job(ctx(v, posts, v.log), job())
    assert out.status == "buffered" and out.decision is not None
    assert v.log.index(f"xadd {gw.PENDING}") < v.log.index("callback")      # recorded first
    record = json.loads(v.streams[gw.PENDING][0][1]["record"])
    inputs = json.loads(record["inputs"])
    assert record["inputs_hash"] and inputs["facts"]["case_id"] == "d-1"
    assert inputs["gate_input"]["remaining_pct"] == pytest.approx(90.0)     # the increase30d fallback
    assert record["verdict"] == "admit" and posts[0][1] is True             # no history: prior 0.75% < 42.5%


async def test_both_stores_down_leaves_the_run_waiting() -> None:
    posts: list[tuple[dict[str, Any], bool, str]] = []
    with pytest.raises(OSError):
        await gw.evaluate_job(ctx(None, posts), job())
    assert posts == []                                                      # GitHub keeps it waiting


async def test_handle_dead_letters_a_forged_job_and_acks_it() -> None:
    v, posts = FakeValkey(), []
    await gw.handle(ctx(v, posts), "7-0", {"job": json.dumps(job(repo="mallory/x"))})
    assert v.streams[gw.DEAD][0][1]["reason"] == "foreign repo or installation"
    assert v.acked == ["7-0"] and posts == []


async def test_handle_releases_the_single_flight_lock() -> None:
    v, posts = FakeValkey(), []
    await gw.handle(ctx(v, posts), "8-0", {"job": json.dumps(job())})
    assert "bg:lock:eval:orders-service" not in v.kv and v.acked == ["8-0"]


# ---------------------------------------------------------------- combine()
def status(slo: str, integrity: Integrity = Integrity.OK, page: bool = False,
           rule: float | None = 90.0) -> SloStatus:
    return SloStatus("orders-service", slo, integrity, SliState.HEALTHY, rule, 0.1, page, False, 9000)


def test_combine_takes_the_worst_integrity_and_lowest_budget() -> None:
    sl = SPECS["orders-service"]
    s = combine(sl, {"availability": status("availability"), "latency": status("latency", Integrity.STALE)},
                {"availability": (1e6, 900, 100), "latency": (1e6, 2000, 0)}, {})
    assert s.integrity is Integrity.STALE
    assert s.remaining_all_pct == pytest.approx(10.0)          # availability: 900/1e6 of 0.1%
    assert s.remaining_self_pct == pytest.approx(80.0)         # latency has no origin label: all bad = self
    assert s.constraining_slo == "latency"


def test_combine_falls_back_to_the_rule_without_buckets() -> None:
    sl = SPECS["orders-service"]
    s = combine(sl, {"availability": status("availability", rule=42.0),
                     "latency": status("latency", rule=60.0)},
                None, {})
    assert (s.remaining_all_pct, s.remaining_self_pct) == (42.0, 42.0)


def test_combine_unknown_when_any_slo_has_no_number() -> None:
    sl = SPECS["orders-service"]
    s = combine(sl, {"availability": status("availability"), "latency": status("latency", rule=None)},
                None, {})
    assert s.remaining_all_pct is None and s.remaining_self_pct is None


def test_combine_latency_page_counts_as_self() -> None:
    sl = SPECS["orders-service"]
    s = combine(sl, {"availability": status("availability"), "latency": status("latency", page=True)},
                None, {"latency": 1.0})
    assert (s.page_firing, s.self_share_1h) == (True, 1.0)


# ---------------------------------------------------------------- template comment
def test_block_comment_carries_price_affordable_and_both_etas() -> None:
    d = Decision(Verdict.BLOCK, "OVER_BUDGET", "Price 1.26% > affordable 1.00%", price_pct=1.26,
                 affordable_pct=1.0)
    c = template_comment("orders-service", d, "https://bg.example/decisions/9",
                         Eta(datetime(2026, 11, 2, 14, 20, tzinfo=UTC), None), 7.0)
    assert "Price 1.26%" in c and "affordable now 1.00%" in c
    assert "becomes affordable: 2026-11-02 14:20 UTC if errors stop" in c
    assert "not within 30 days at half" in c and "rollback is always admitted" in c
    assert c.endswith("https://bg.example/decisions/9")


def test_admit_comment_shows_the_canary_and_is_capped() -> None:
    d = Decision(Verdict.ADMIT, "OVERRIDE", "x" * 2000, canary_profile=CONSERVATIVE)
    c = template_comment("orders-service", d, "u", None, None)
    assert len(c) <= MAX_COMMENT and c.startswith("BudgetGuard: Admitted (OVERRIDE)")


async def test_a_misconfigured_app_key_fails_the_callback_once_instead_of_retrying() -> None:
    from budgetguard.integrations.github_app import AppMisconfigured
    calls: list[int] = []

    async def broken(*_: Any) -> None:
        calls.append(1)
        raise AppMisconfigured("cannot sign the App JWT: InvalidKeyError")

    c = ctx(FakeValkey(), [])
    c.post = broken
    out = await gw.evaluate_job(c, job())
    assert out.status == "buffered" and calls == [1]                 # one attempt, no backoff loop


def test_only_an_over_budget_block_has_an_affordability_eta() -> None:
    p = Policy("v1")                                             # reserve 5, spend fraction 0.5
    over = Decision(Verdict.BLOCK, "OVER_BUDGET", "x", price_pct=1.26, affordable_pct=1.0)
    assert gw.affordable_threshold(over, p) == pytest.approx(5.0 + 1.26 / 0.5)
    assert gw.affordable_threshold(Decision(Verdict.BLOCK, "ACTIVE_PAGE", "x"), p) is None
    assert gw.affordable_threshold(Decision(Verdict.BLOCK, "SLI_UNKNOWN", "x"), p) is None
    page = Decision(Verdict.BLOCK, "ACTIVE_PAGE", "page firing")
    c = template_comment("orders-service", page, "u", None, 87.7)
    assert "clears when the page-level burn alert resolves" in c and "affordable:" not in c
