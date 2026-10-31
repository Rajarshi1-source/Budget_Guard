from typing import Any

import pytest

from budgetguard.changes import PullRequest, extract, features_from_files
from budgetguard.gate import Lane
from budgetguard.lanes import incident_fix_proof
from budgetguard.pricing import ChangeFeatures, PricingPolicy, risk_multiplier

S = "services/orders-service/"


def f(name: str, adds: int = 10, dels: int = 0) -> dict[str, Any]:
    return {"filename": name, "additions": adds, "deletions": dels}


def test_lines_count_only_this_service() -> None:
    feats, paths = features_from_files([f(S + "src/A.java", 30, 10), f("services/auth-service/B.java", 500),
                                        f("README.md", 99)], "orders-service")
    assert feats.lines_changed == 40 and paths == (S + "src/A.java",)


@pytest.mark.parametrize("path", [S + "src/main/resources/db/migration/V7__add.sql", S + "db/V12__x.sql",
                                  S + "alembic/versions/0007_x.py", S + "migrations/versions/abc.py"])
def test_migration_paths(path: str) -> None:
    assert features_from_files([f(path)], "orders-service")[0].touches_migration


def test_not_a_migration() -> None:
    assert not features_from_files([f(S + "src/MigrationHelper.java")], "orders-service")[0].touches_migration


def test_config_only_and_dependency_bump() -> None:
    cfg = features_from_files([f(S + "src/main/resources/application.yml"), f(S + "x.properties")],
                              "orders-service")[0]
    dep = features_from_files([f(S + "pom.xml")], "orders-service")[0]
    mixed = features_from_files([f(S + "pom.xml"), f(S + "src/A.java")], "orders-service")[0]
    assert (cfg.config_only, cfg.dependency_bump) == (True, False)
    assert (dep.dependency_bump, dep.config_only) == (True, False)
    assert not mixed.dependency_bump and not mixed.config_only


def test_nothing_under_the_service_is_not_config_only() -> None:
    feats = features_from_files([f("docs/x.yml")], "orders-service")[0]
    assert feats == ChangeFeatures(0, False, False, False)


def test_900_line_migration_reproduces_the_worked_example_multiplier() -> None:
    feats = features_from_files([f(S + "src/A.java", 700, 100), f(S + "db/migration/V9__big.sql", 100)],
                                "orders-service")[0]
    assert feats.lines_changed == 900 and risk_multiplier(feats, PricingPolicy()) == pytest.approx(3.0)


class FakeGitHub:
    def __init__(self, pulls: list[dict[str, Any]], files: list[dict[str, Any]]) -> None:
        self.pulls, self.files, self.calls = pulls, files, []

    async def get(self, path: str) -> Any:
        self.calls.append(path)
        if path.endswith("/pulls"):
            return self.pulls
        if "/files" in path:
            page = int(path.rsplit("page=", 1)[1])
            return self.files[(page - 1) * 100: page * 100]
        if "/commits?" in path:
            return [{"commit": {"author": {"date": "2026-10-30T09:00:00Z"}}}]
        raise AssertionError(path)


async def test_extract_picks_the_merged_pr_and_paginates() -> None:
    pulls = [{"number": 1, "merged_at": None, "merge_commit_sha": "x"},
             {"number": 42, "merged_at": "2026-10-31T10:00:00Z", "merge_commit_sha": "abc",
              "body": "Fixes BG-INC-7",
              "user": {"login": "alice"}}]
    files = [f(S + f"src/F{i}.java", 2, 1) for i in range(150)]
    gh = FakeGitHub(pulls, files)
    c = await extract(gh, "o/r", "abc", "orders-service")
    assert c.features.lines_changed == 450 and c.service_files == 150 and c.note == ""
    assert c.pr == PullRequest(42, "Fixes BG-INC-7", "alice", 450, "2026-10-30T09:00:00Z")
    assert sum("/files" in p for p in gh.calls) == 2


async def test_direct_push_is_priced_at_size_max_and_says_so() -> None:
    c = await extract(FakeGitHub([], []), "o/r", "abc", "orders-service")
    assert c.features == ChangeFeatures.unknown() and c.pr is None and "no merged PR" in c.note


class NoDb:
    async def execute(self, *a: Any) -> Any:
        raise AssertionError("this path must not query the database")


class NoProm:
    async def query(self, expr: str) -> list[dict[str, Any]]:
        raise AssertionError("this path must not query Prometheus")


def pr(body: str, author: str = "alice", lines: int = 50) -> PullRequest:
    return PullRequest(9, body, author, lines, None)


async def test_security_lane_for_a_bot_pr_citing_an_advisory() -> None:
    p = await incident_fix_proof(NoDb(), NoProm(), "orders-service",  # type: ignore[arg-type]
                                 pr("Bumps x. Fixes GHSA-jfh8-c2jp-5v3q", "dependabot[bot]", 5000))
    assert p.lane is Lane.INCIDENT_FIX and p.evidence["kind"] == "security"


async def test_an_advisory_from_a_human_is_not_a_lane() -> None:
    p = await incident_fix_proof(NoDb(), NoProm(), "orders-service",  # type: ignore[arg-type]
                                 pr("GHSA-jfh8-c2jp-5v3q"))
    assert p.lane is None


async def test_incident_fix_over_400_lines_is_denied_before_any_lookup() -> None:
    p = await incident_fix_proof(NoDb(), NoProm(), "orders-service",  # type: ignore[arg-type]
                                 pr("Fixes BG-INC-3", lines=401))
    assert p.lane is None and "401 lines" in p.evidence["denied"]


async def test_no_reference_no_lane() -> None:
    assert (await incident_fix_proof(NoDb(), NoProm(), "orders-service",  # type: ignore[arg-type]
                                     pr("tidy up"))).lane is None
    assert (await incident_fix_proof(NoDb(), NoProm(), "orders-service", None)).lane is None  # type: ignore[arg-type]
