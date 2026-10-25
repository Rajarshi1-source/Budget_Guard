"""001: relational schema + sli_buckets hypertable (sql/001_schema.sql, from
budgetguard-data-layer/references/schema-and-queries.md section 1)."""
from pathlib import Path

from alembic import op

SQL = Path(__file__).resolve().parents[2] / "sql"


def run_sql(name: str) -> None:
    """Send the file to psycopg as-is on Alembic's own connection. A cursor with no parameters runs a
    multi-statement string and parses no placeholders: SQLAlchemy's text() would treat :words as binds,
    and exec_driver_sql passes an empty tuple, which makes psycopg choke on a '%H' in a comment."""
    raw = op.get_bind().connection.driver_connection
    with raw.cursor() as cur:
        cur.execute((SQL / name).read_text(encoding="utf-8"))

revision, down_revision = "001", None


def upgrade() -> None:
    run_sql("001_schema.sql")


def downgrade() -> None:
    raise NotImplementedError("forward-only: restore from the nightly pg_dump instead")
