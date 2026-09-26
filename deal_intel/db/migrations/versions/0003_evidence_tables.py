"""evidence tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Left in place on downgrade: other databases or later revisions may rely on the extension.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "ingest_snapshots",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("file_manifest", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_table(
        "evidence_chunks",
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("opportunity_id", sa.Text(), nullable=True),
        sa.Column("account_id", sa.Text(), nullable=True),
        sa.Column("access_level", sa.Text(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("author_or_speakers", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('salesforce', 'gong', 'slack', 'pricing', 'policies')",
            name="ck_evidence_chunks_source_type",
        ),
        sa.CheckConstraint(
            "access_level IN ('standard', 'restricted', 'sensitive_pricing')",
            name="ck_evidence_chunks_access_level",
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["ingest_snapshots.snapshot_id"]),
        sa.PrimaryKeyConstraint("chunk_id"),
    )
    op.create_index(
        "ix_evidence_chunks_scope",
        "evidence_chunks",
        ["account_id", "opportunity_id", "source_type", "access_level"],
    )
    op.create_index("ix_evidence_chunks_tsv", "evidence_chunks", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_evidence_chunks_tsv", table_name="evidence_chunks")
    op.drop_index("ix_evidence_chunks_scope", table_name="evidence_chunks")
    op.drop_table("evidence_chunks")
    op.drop_table("ingest_snapshots")
