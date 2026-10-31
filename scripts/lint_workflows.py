"""CI lint for the workflows (security-and-api.md §9, threats T-3 and T-5):

1. every third-party action is pinned to a full commit SHA (a moved tag cannot change what runs);
2. only a job with `environment:` may reference secrets.KUBECONFIG (an environment secret is not
   readable without the environment, but a repo secret of the same name would be: catch the slip).

    uv run --project platform python scripts/lint_workflows.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def problems(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for job_name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            uses = step.get("uses")
            if uses and not uses.startswith("./") and not SHA.match(uses):
                out.append(f"{path.name}:{job_name}: action not pinned by commit SHA: {uses}")
        if "secrets.KUBECONFIG" in yaml.safe_dump(job) and "environment" not in job:
            out.append(f"{path.name}:{job_name}: references secrets.KUBECONFIG without an environment")
    return out


def main() -> int:
    found = [p for f in sorted((ROOT / ".github" / "workflows").glob("*.yml")) for p in problems(f)]
    for p in found:
        print(p)
    print(f"{len(found)} workflow problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
