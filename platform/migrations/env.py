"""Alembic environment: synchronous psycopg (asyncpg prepares every statement, so it cannot run
the multi-statement SQL files). The URL comes from BG_DATABASE_URL with the driver swapped."""
from alembic import context
from sqlalchemy import create_engine, pool

from budgetguard.config import get_core_settings


def url() -> str:
    return get_core_settings().database_url.replace("+asyncpg", "+psycopg")


def run() -> None:
    engine = create_engine(url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


run()
