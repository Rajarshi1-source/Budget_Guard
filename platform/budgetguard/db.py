"""Async engine for the workers and CLI (the API builds its own pool in PgStore)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .config import get_core_settings


def make_engine(url: str | None = None, *, pool_size: int = 3) -> AsyncEngine:
    return create_async_engine(url or get_core_settings().database_url, pool_size=pool_size,
                               max_overflow=0, pool_pre_ping=True)
