"""Slack delivery for Freeze Briefs (#bg-briefs, W7.6). An incoming-webhook URL is a secret
(security-and-api.md §7); unset means briefs are stored on the decision and not posted."""
from __future__ import annotations

import httpx
from pydantic import SecretStr


async def post(http: httpx.AsyncClient, url: SecretStr | None, text: str) -> bool:
    """True when Slack accepted the message; False when no URL is configured."""
    if url is None:
        return False
    r = await http.post(url.get_secret_value(), json={"text": text}, timeout=5.0)
    r.raise_for_status()
    return True
