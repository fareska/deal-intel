"""llm and tracing tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def created_at_column() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "trace_spans",
        sa.Column("span_id", sa.Text(), nullable=False),
        sa.Column("parent_span_id", sa.Text(), nullable=True),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('run', 'stage', 'agent_call', 'llm_request', 'retrieval', 'tool', "
            "'guardrail', 'approval')",
            name="ck_trace_spans_kind",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'ok', 'error')",
            name="ck_trace_spans_status",
        ),
        sa.PrimaryKeyConstraint("span_id"),
    )
    op.create_index("ix_trace_spans_run_started", "trace_spans", ["run_id", "started_at"])
    op.create_table(
        "llm_calls",
        sa.Column("call_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("span_id", sa.Text(), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_role", sa.Text(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        created_at_column(),
        sa.CheckConstraint(
            "model_role IN ('extraction', 'strategy')",
            name="ck_llm_calls_model_role",
        ),
        sa.CheckConstraint(
            "stop_reason IN ('end_turn', 'tool_use', 'max_tokens', 'stop_sequence', "
            "'pause_turn', 'refusal', 'model_context_window_exceeded', 'cached', 'other')",
            name="ck_llm_calls_stop_reason",
        ),
        sa.PrimaryKeyConstraint("call_id"),
    )
    op.create_index("ix_llm_calls_run_id", "llm_calls", ["run_id"])
    op.create_table(
        "agent_output_cache",
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("output_json", postgresql.JSONB(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("guardrail_results", postgresql.JSONB(), nullable=False),
        sa.Column("tool_evidence_ids", postgresql.JSONB(), nullable=False),
        created_at_column(),
        sa.PrimaryKeyConstraint("cache_key"),
    )


def downgrade() -> None:
    op.drop_table("agent_output_cache")
    op.drop_index("ix_llm_calls_run_id", table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index("ix_trace_spans_run_started", table_name="trace_spans")
    op.drop_table("trace_spans")
