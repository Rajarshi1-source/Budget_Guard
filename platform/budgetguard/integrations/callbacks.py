"""GitHub calls made after a decision. Uses the App installation token from github_app.py."""
from __future__ import annotations

import httpx

from ..config import get_settings
from ..store import DecisionRow
from .github_app import JobRejected, installation_token


async def rerun_failed_jobs(client: httpx.AsyncClient, d: DecisionRow) -> None:
    """Needs the App permission Actions: read & write. GitHub allows re-runs for 30 days."""
    assert d.installation_id is not None and d.github_run_id is not None
    if d.repo != get_settings().github_repo:            # stored inputs are re-checked before any token
        raise JobRejected("foreign repo")
    token = await installation_token(client, d.installation_id, {"actions": "write"})
    r = await client.post(f"https://api.github.com/repos/{d.repo}/actions/runs/{d.github_run_id}/rerun-failed-jobs",
                          headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/vnd.github+json"})
    r.raise_for_status()
