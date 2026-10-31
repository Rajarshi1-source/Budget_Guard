"""008: freeze_impact view (sql/008_freeze_impact.sql) for the Grafana freeze panel and the backtester."""
from pathlib import Path

from alembic import op

SQL = Path(__file__).resolve().parents[2] / "sql"

revision, down_revision = "008", "007"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    with raw.cursor() as cur:                        # raw cursor: multi-statement, no placeholders
        cur.execute((SQL / "008_freeze_impact.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("forward-only")
