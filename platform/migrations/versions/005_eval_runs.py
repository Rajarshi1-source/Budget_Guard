"""005: eval_runs for the brief and backtest harnesses (budgetguard-mlops-evals §6)."""
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

revision, down_revision = "005", "004"


def upgrade() -> None:
    run_sql("005_eval_runs.sql")
    op.get_bind().exec_driver_sql("GRANT SELECT, INSERT ON eval_runs TO bg_app; "
                                  "GRANT USAGE, SELECT ON SEQUENCE eval_runs_id_seq TO bg_app")


def downgrade() -> None:
    raise NotImplementedError("forward-only")
