"""Valkey helpers. Key names follow the catalog in budgetguard-data-layer."""
from __future__ import annotations

import json
from typing import Any

ENQUEUE_ONCE = """
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', 604800) then
  redis.call('XADD', KEYS[2], 'MAXLEN', '~', 10000, '*', 'job', ARGV[1])
  return 1
end
return 0
"""


async def enqueue_once(r: Any, stream: str, key: str, job: dict) -> bool:
    """Delivery-ID dedupe and stream append in one round trip (GitHub redelivers webhooks)."""
    return bool(await r.eval(ENQUEUE_ONCE, 2, f"bg:delivery:{key}", stream, json.dumps(job)))


async def invalidate_verdicts(r: Any, service: str) -> int:
    """O(1) cache invalidation: verdict keys embed a per-service generation number
    (bg:verdict:{service}:{gen}:{sha}); bumping the generation orphans every cached verdict,
    which then expire on their 30 s TTL. No SCAN, no key set to maintain."""
    return int(await r.incr(f"bg:verdict-gen:{service}"))
