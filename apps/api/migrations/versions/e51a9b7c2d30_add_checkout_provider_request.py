"""add checkout provider request reference

Revision ID: e51a9b7c2d30
Revises: d44e6f5a8b12
Create Date: 2026-08-12 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e51a9b7c2d30"
down_revision: str | Sequence[str] | None = "d44e6f5a8b12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "checkout_executions",
        sa.Column("provider_request_id", sa.String(length=255)),
    )


def downgrade() -> None:
    op.drop_column("checkout_executions", "provider_request_id")
