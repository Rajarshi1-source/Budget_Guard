"""007: real-time aggregation on sli_hourly and sli_daily. Off by default since TimescaleDB 2.13, so the
newest hour was missing from Grafana's 30-day panel until the policy's next refresh (data-layer §6).
With materialized_only = false the views append not-yet-materialized buckets at query time."""
from alembic import op

revision, down_revision = "007", "006"


def upgrade() -> None:
    for view in ("sli_hourly", "sli_daily"):
        op.get_bind().exec_driver_sql(
            f"ALTER MATERIALIZED VIEW {view} SET (timescaledb.materialized_only = false)")


def downgrade() -> None:
    raise NotImplementedError("forward-only")
