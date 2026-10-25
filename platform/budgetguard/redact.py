"""Log redaction (security-and-api.md §7): credentials never reach a log line.

Drops the values of Authorization, X-GitHub-User-Token, X-Hub-Signature-256, X-BG-Fault-Token and
Cookie wherever they are printed (`Header: v`, `header=v`, `'header': 'v'`), and query parameters
named t, token or code (uvicorn access lines, httpx request lines). Installed as the log record
factory, so it covers every logger, including third-party ones that propagate past our handlers.
"""
from __future__ import annotations

import logging
import re
from typing import Any

MASK = "[REDACTED]"
_HEADERS = re.compile(
    r"(?i)((?:authorization|x-github-user-token|x-hub-signature-256|x-bg-fault-token|cookie)"
    r"""["']?\s*[:=]\s*["']?)(?:bearer\s+|token\s+)?[^\s"',;}]+""")
_QUERY = re.compile(r"(?i)([?&](?:t|token|code)=)[^&\s\"'#]+")


def redact(text: str) -> str:
    return _QUERY.sub(rf"\g<1>{MASK}", _HEADERS.sub(rf"\g<1>{MASK}", text))


def install() -> None:
    """Idempotent. Formats each record once, redacts, and freezes it (args cleared)."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_bg_redacting", False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:                                # noqa: BLE001 - a bad format string still gets logged
            return record
        record.msg, record.args = redact(message), None
        return record

    factory._bg_redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)
