"""002: compression, retention, continuous aggregates. CREATE MATERIALIZED VIEW ... WITH
(timescaledb.continuous) cannot run inside a transaction, hence autocommit_block."""
import re
from pathlib import Path

from alembic import op

revision, down_revision = "002", "001"
SQL = Path(__file__).resolve().parents[2] / "sql"


def upgrade() -> None:
    text = (SQL / "002_timeseries.sql").read_text(encoding="utf-8")
    # Split on ';' at end of line, also when a trailing "-- comment" follows it: a multi-statement
    # string runs as one implicit transaction, and continuous aggregates cannot be created in one.
    chunks = ("\n".join(ln for ln in chunk.splitlines() if not ln.lstrip().startswith("--"))
              for chunk in re.split(r";[ \t]*(?:--[^\n]*)?\n", text))
    statements = [c.strip() for c in chunks if c.strip()]          # comment-only chunks dropped, not SQL
    with op.get_context().autocommit_block():
        for stmt in statements:
            op.get_bind().exec_driver_sql(stmt)


def downgrade() -> None:
    raise NotImplementedError("forward-only")
