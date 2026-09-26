"""strip denial reason codes from stored trace spans

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE trace_spans SET attributes = attributes - 'reason_code' "
        "WHERE attributes ->> 'reason_code' IS NOT NULL"
    )


def downgrade() -> None:
    """The removed values were a leak; they are intentionally not restorable."""
