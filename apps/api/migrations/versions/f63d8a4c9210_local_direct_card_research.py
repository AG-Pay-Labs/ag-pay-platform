"""add local direct-card research storage and resolved form mapping

Revision ID: f63d8a4c9210
Revises: a27c6e91f4b3
Create Date: 2026-08-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f63d8a4c9210"
down_revision: str | Sequence[str] | None = "a27c6e91f4b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stored_card_credentials",
        sa.Column("payment_method_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_pan", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_stored_card_credentials_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["payment_method_id"],
            ["payment_methods.id"],
            name=op.f("fk_stored_card_credentials_payment_method_id_payment_methods"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("payment_method_id", name=op.f("pk_stored_card_credentials")),
    )
    op.create_index(
        op.f("ix_stored_card_credentials_owner_id"),
        "stored_card_credentials",
        ["owner_id"],
    )
    op.add_column(
        "checkout_executions",
        sa.Column("resolved_form_config", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("checkout_executions", "resolved_form_config")
    op.drop_index(
        op.f("ix_stored_card_credentials_owner_id"),
        table_name="stored_card_credentials",
    )
    op.drop_table("stored_card_credentials")
