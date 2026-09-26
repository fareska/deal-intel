"""run tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATES = (
    "'QUEUED', 'AUTHORIZING', 'DENIED', 'RETRIEVING', 'ANALYZING', 'SYNTHESIZING', "
    "'VALIDATING', 'AWAITING_APPROVAL', 'COMPLETED', 'FAILED'"
)
# Shared with 0006: the event tables are audit logs, so the database refuses edits as well.
APPEND_ONLY_FUNCTION = """
CREATE FUNCTION forbid_append_only_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$
"""


def timestamp_column(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def append_only_trigger(table: str) -> str:
    return (
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_append_only_change()"
    )


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("opportunity_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("fresh", sa.Boolean(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=True),
        sa.Column("evidence_hash", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("reused_from_run_id", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        timestamp_column("created_at"),
        timestamp_column("updated_at"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"state IN ({RUN_STATES})", name="ck_runs_state"),
        sa.ForeignKeyConstraint(["reused_from_run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_runs_state", "runs", ["state"])
    op.create_index("ix_runs_idempotency_key", "runs", ["idempotency_key"])
    op.create_table(
        "run_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=True),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        timestamp_column("at"),
        sa.CheckConstraint(f"to_state IN ({RUN_STATES})", name="ck_run_events_to_state"),
        sa.CheckConstraint(f"from_state IN ({RUN_STATES})", name="ck_run_events_from_state"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id", "event_id"])
    op.create_table(
        "stage_outputs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("output_json", postgresql.JSONB(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("prompt_hash", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        timestamp_column("created_at"),
        sa.CheckConstraint(
            "stage IN ('authorize', 'retrieve', 'deal_snapshot', 'conversation_intelligence', "
            "'stakeholder_map', 'negotiation_strategy', 'policy', 'guardrails', 'render')",
            name="ck_stage_outputs_stage",
        ),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_stage_outputs_status"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("run_id", "stage", "attempt"),
    )
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(append_only_trigger("run_events"))


def downgrade() -> None:
    op.drop_table("stage_outputs")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")
    op.execute("DROP FUNCTION forbid_append_only_change()")
    op.drop_index("ix_runs_idempotency_key", table_name="runs")
    op.drop_index("ix_runs_state", table_name="runs")
    op.drop_table("runs")
