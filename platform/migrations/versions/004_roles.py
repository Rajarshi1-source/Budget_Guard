"""004: least-privilege roles (sql/004_roles.sql). LOGIN + password only for roles whose
BG_DB_<ROLE>_PASSWORD is set; the password is quoted by PostgreSQL's format(%L), never by us."""
import os
from pathlib import Path

from alembic import op
from sqlalchemy import text

SQL = Path(__file__).resolve().parents[2] / "sql"


def run_sql(name: str) -> None:
    """Send the file to psycopg as-is on Alembic's own connection. A cursor with no parameters runs a
    multi-statement string and parses no placeholders: SQLAlchemy's text() would treat :words as binds,
    and exec_driver_sql passes an empty tuple, which makes psycopg choke on a '%H' in a comment."""
    raw = op.get_bind().connection.driver_connection
    with raw.cursor() as cur:
        cur.execute((SQL / name).read_text(encoding="utf-8"))

revision, down_revision = "004", "003"
ROLES = ("bg_app", "bg_migrator", "bg_grafana", "bg_auth")


def upgrade() -> None:
    run_sql("004_roles.sql")
    bind = op.get_bind()
    for role in ROLES:
        password = os.environ.get(f"BG_DB_{role.removeprefix('bg_').upper()}_PASSWORD")
        if password:
            fmt = text("SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', CAST(:r AS text), CAST(:p AS text))")
            stmt = bind.execute(fmt, {"r": role, "p": password}).scalar_one()
            bind.exec_driver_sql(stmt)


def downgrade() -> None:
    raise NotImplementedError("forward-only")
