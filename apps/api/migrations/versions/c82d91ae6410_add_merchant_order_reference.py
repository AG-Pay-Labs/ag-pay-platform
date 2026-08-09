"""add merchant order reference for checkout reconciliation

Revision ID: c82d91ae6410
Revises: b71c2f4a8e90
Create Date: 2026-08-09 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c82d91ae6410"
down_revision: str | Sequence[str] | None = "b71c2f4a8e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "checkout_executions",
        sa.Column("merchant_order_reference", sa.String(length=128)),
    )
    op.add_column(
        "purchases",
        sa.Column("merchant_order_reference", sa.String(length=128)),
    )


def downgrade() -> None:
    op.drop_column("purchases", "merchant_order_reference")
    op.drop_column("checkout_executions", "merchant_order_reference")
