"""Change features for pricing (W6.1, budgetguard-admission-gate §3).

The merged PR for the deploy SHA (`GET /repos/{repo}/commits/{sha}/pulls`), its files (paginated
`GET /pulls/{n}/files`) filtered to services/<service>/**, and from them:

  lines_changed      sum of additions + deletions under the service path
  touches_migration  any path under db/migration/, a Flyway V<n>__*.sql, or alembic/migrations versions/
  config_only        every service path ends in .yml, .yaml or .properties
  dependency_bump    every service path is a build or lock file (pom.xml, package.json, lockfiles)

No PR (a direct push) -> ChangeFeatures.unknown() (priced at size_max) and the reason says so.
The PR body, author and size also feed the incident-fix and security lanes (lanes.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .pricing import ChangeFeatures

MIGRATION = re.compile(r"(^|/)(db/migration/|alembic/versions/|migrations/versions/)|(^|/)V\d+__[^/]*\.sql$")
CONFIG_SUFFIXES = (".yml", ".yaml", ".properties")
DEPENDENCY_FILES = frozenset({"pom.xml", "package.json", "package-lock.json", "pnpm-lock.yaml",
                              "yarn.lock", "pyproject.toml", "uv.lock", "requirements.txt",
                              "build.gradle", "gradle.lockfile"})
MAX_PAGES = 30                          # GitHub lists at most 3,000 files per PR (100 per page)


class Reader(Protocol):
    async def get(self, path: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    body: str
    author: str
    total_lines: int                    # additions + deletions over the whole PR (the incident-fix cap)
    first_commit_at: str | None         # lead time = deploy finished - first commit


@dataclass(frozen=True, slots=True)
class Change:
    features: ChangeFeatures
    pr: PullRequest | None
    service_files: int = 0
    note: str = ""                      # why the price looks the way it does, for the decision reason
    paths: tuple[str, ...] = field(default=())


def features_from_files(files: list[dict[str, Any]], service: str) -> tuple[ChangeFeatures, tuple[str, ...]]:
    """Pure: GitHub's PR file objects -> features for one service."""
    prefix = f"services/{service}/"
    mine = [f for f in files if f["filename"].startswith(prefix)]
    paths = tuple(f["filename"] for f in mine)
    lines = sum(int(f.get("additions", 0)) + int(f.get("deletions", 0)) for f in mine)
    names = [p.rsplit("/", 1)[-1] for p in paths]
    return ChangeFeatures(
        lines_changed=lines,
        touches_migration=any(MIGRATION.search(p) for p in paths),
        config_only=bool(paths) and all(p.endswith(CONFIG_SUFFIXES) for p in paths),
        dependency_bump=bool(paths) and all(n in DEPENDENCY_FILES for n in names),
    ), paths


async def extract(gh: Reader, repo: str, sha: str, service: str) -> Change:
    pulls: list[dict[str, Any]] = await gh.get(f"/repos/{repo}/commits/{sha}/pulls")
    merged = [p for p in pulls if p.get("merged_at")]
    pr = next((p for p in merged if p.get("merge_commit_sha") == sha), merged[0] if merged else None)
    if pr is None:
        return Change(ChangeFeatures.unknown(), None,
                      note="no merged PR for this commit: priced at the largest size")
    n = int(pr["number"])
    files: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        batch: list[dict[str, Any]] = await gh.get(f"/repos/{repo}/pulls/{n}/files?per_page=100&page={page}")
        files.extend(batch)
        if len(batch) < 100:
            break
    commits: list[dict[str, Any]] = await gh.get(f"/repos/{repo}/pulls/{n}/commits?per_page=1")
    first = commits[0]["commit"]["author"]["date"] if commits else None
    features, paths = features_from_files(files, service)
    total = sum(int(f.get("additions", 0)) + int(f.get("deletions", 0)) for f in files)
    author = (pr.get("user") or {}).get("login", "")
    return Change(features, PullRequest(n, pr.get("body") or "", author, total, first),
                  service_files=len(paths), paths=paths,
                  note="" if paths else f"PR #{n} changes nothing under services/{service}/")
