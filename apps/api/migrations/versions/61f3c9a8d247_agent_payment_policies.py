"""add agent payment policies

Revision ID: 61f3c9a8d247
Revises: 92e90796459c
Create Date: 2026-08-04 20:30:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "61f3c9a8d247"
down_revision: str | Sequence[str] | None = "92e90796459c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_payment_policies",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "always",
                "subscriptions_only",
                "above_amount",
                "subscriptions_or_above_amount",
                "never",
                name="paymentapprovalmode",
                native_enum=False,
            ),
            server_default=sa.text("'always'"),
            nullable=False,
        ),
        sa.Column("threshold_amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("threshold_currency", sa.String(length=3), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            "(mode IN ('above_amount', 'subscriptions_or_above_amount') "
            "AND threshold_amount IS NOT NULL AND threshold_currency IS NOT NULL) OR "
            "(mode IN ('always', 'subscriptions_only', 'never') "
            "AND threshold_amount IS NULL AND threshold_currency IS NULL)",
            name="ck_agent_payment_policies_threshold_fields",
        ),
        sa.CheckConstraint(
            "threshold_amount IS NULL OR threshold_amount >= 0",
            name="ck_agent_payment_policies_threshold_non_negative",
        ),
        sa.CheckConstraint(
            "threshold_currency IS NULL OR length(threshold_currency) = 3",
            name="ck_agent_payment_policies_threshold_currency_length",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_payment_policies_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_agent_payment_policies_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_payment_policies")),
        sa.UniqueConstraint("agent_id", name=op.f("uq_agent_payment_policies_agent_id")),
    )
    op.create_index(
        op.f("ix_agent_payment_policies_owner_id"),
        "agent_payment_policies",
        ["owner_id"],
        unique=False,
    )

    agents = list(op.get_bind().execute(sa.text("SELECT id, owner_id FROM agents")).mappings())
    if agents:
        policy_table = sa.table(
            "agent_payment_policies",
            sa.column("id", sa.Uuid()),
            sa.column("owner_id", sa.Uuid()),
            sa.column("agent_id", sa.Uuid()),
            sa.column("mode", sa.String()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        migrated_at = datetime.now(UTC)
        op.bulk_insert(
            policy_table,
            [
                {
                    "id": uuid4(),
                    "owner_id": UUID(str(agent["owner_id"])),
                    "agent_id": UUID(str(agent["id"])),
                    "mode": "always",
                    "created_at": migrated_at,
                    "updated_at": migrated_at,
                }
                for agent in agents
            ],
        )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_agent_payment_policies_owner_id"),
        table_name="agent_payment_policies",
    )
    op.drop_table("agent_payment_policies")
