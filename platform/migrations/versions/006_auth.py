"""006: Better Auth's tables (sql/006_auth.sql), created as bg_auth in schema auth so that role owns
them and no BudgetGuard role can read them.
The migrating role must be able to SET ROLE bg_auth: the compose owner is a superuser; in k3s grant
bg_auth to bg_migrator first."""
from pathlib import Path

from alembic import op

SQL = Path(__file__).resolve().parents[2] / "sql"

revision, down_revision = "006", "005"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    with raw.cursor() as cur:
        cur.execute("SET LOCAL ROLE bg_auth; SET LOCAL search_path = auth")
        cur.execute((SQL / "006_auth.sql").read_text(encoding="utf-8"))
        cur.execute("RESET ROLE; RESET search_path")   # Alembic records the revision in public next


def downgrade() -> None:
    raise NotImplementedError("forward-only")
