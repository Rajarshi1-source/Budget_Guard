"""Async engine for the workers and CLI (the API builds its own pool in PgStore)."""
from __future__ import annotations

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .config import get_core_settings


def make_engine(url: str | None = None, *, pool_size: int = 3) -> AsyncEngine:
    return create_async_engine(url or get_core_settings().database_url, pool_size=pool_size,
                               max_overflow=0, pool_pre_ping=True)


# What "the database is unavailable" looks like: asyncpg raises OSError on a refused connect (not
# wrapped by SQLAlchemy), a dropped connection surfaces as OperationalError or InterfaceError, a hung
# one times out. Never DBAPIError as a whole: that also covers SQL bugs, which must fail loudly
# instead of being buffered as if PostgreSQL were down.
DB_DOWN: tuple[type[BaseException], ...] = (OSError, TimeoutError, OperationalError, InterfaceError)
