"""add managed checkout execution state

Revision ID: b71c2f4a8e90
Revises: 61f3c9a8d247
Create Date: 2026-08-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b71c2f4a8e90"
down_revision: str | Sequence[str] | None = "61f3c9a8d247"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cart_items", sa.Column("checkout_adapter", sa.String(length=64)))
    op.add_column("cart_items", sa.Column("checkout_url", sa.Text()))
    op.create_check_constraint(
        "ck_cart_items_checkout_fields",
        "cart_items",
        "(checkout_adapter IS NULL AND checkout_url IS NULL) OR "
        "(checkout_adapter IS NOT NULL AND checkout_url IS NOT NULL)",
    )

    op.create_table(
        "checkout_executions",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("payment_method_id", sa.Uuid(), nullable=False),
        sa.Column("cart_item_id", sa.Uuid(), nullable=False),
        sa.Column("adapter_key", sa.String(length=64), nullable=False),
        sa.Column("adapter_config", sa.JSON(), nullable=False),
        sa.Column("approved_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("checkout_origin", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "succeeded",
                "failed",
                "action_required",
                "outcome_unknown",
                name="checkoutexecutionstatus",
                native_enum=False,
            ),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("browserbase_session_id", sa.String(length=255)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_message", sa.Text()),
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
            "approved_amount > 0", name="ck_checkout_executions_approved_amount_positive"
        ),
        sa.CheckConstraint("length(currency) = 3", name="ck_checkout_executions_currency_length"),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_checkout_executions_attempt_count_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_checkout_executions_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cart_item_id"],
            ["cart_items.id"],
            name=op.f("fk_checkout_executions_cart_item_id_cart_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_checkout_executions_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["payment_method_id"],
            ["payment_methods.id"],
            name=op.f("fk_checkout_executions_payment_method_id_payment_methods"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checkout_executions")),
        sa.UniqueConstraint("cart_item_id", name=op.f("uq_checkout_executions_cart_item_id")),
    )
    op.create_index(
        op.f("ix_checkout_executions_agent_id"),
        "checkout_executions",
        ["agent_id"],
    )
    op.create_index(
        op.f("ix_checkout_executions_owner_id"),
        "checkout_executions",
        ["owner_id"],
    )
    op.create_index(
        op.f("ix_checkout_executions_payment_method_id"),
        "checkout_executions",
        ["payment_method_id"],
    )
    op.create_index(
        op.f("ix_checkout_executions_status"),
        "checkout_executions",
        ["status"],
    )
    op.create_index(
        "ix_checkout_executions_owner_status",
        "checkout_executions",
        ["owner_id", "status"],
    )

    op.create_table(
        "checkout_events",
        sa.Column("cursor", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("cart_item_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_id", sa.Uuid()),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "succeeded",
                "failed",
                "action_required",
                "outcome_unknown",
                name="checkoutexecutionstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'action_required', 'outcome_unknown')",
            name="ck_checkout_events_terminal_status",
        ),
        sa.CheckConstraint("amount > 0", name="ck_checkout_events_amount_positive"),
        sa.CheckConstraint("length(currency) = 3", name="ck_checkout_events_currency_length"),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_checkout_events_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cart_item_id"],
            ["cart_items.id"],
            name=op.f("fk_checkout_events_cart_item_id_cart_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["checkout_executions.id"],
            name=op.f("fk_checkout_events_execution_id_checkout_executions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_checkout_events_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_id"],
            ["purchases.id"],
            name=op.f("fk_checkout_events_purchase_id_purchases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("cursor", name=op.f("pk_checkout_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_checkout_events_event_id")),
        sa.UniqueConstraint("execution_id", name=op.f("uq_checkout_events_execution_id")),
        sa.UniqueConstraint("purchase_id", name=op.f("uq_checkout_events_purchase_id")),
    )
    op.create_index(op.f("ix_checkout_events_agent_id"), "checkout_events", ["agent_id"])
    op.create_index(op.f("ix_checkout_events_cart_item_id"), "checkout_events", ["cart_item_id"])
    op.create_index(op.f("ix_checkout_events_owner_id"), "checkout_events", ["owner_id"])
    op.create_index("ix_checkout_events_agent_cursor", "checkout_events", ["agent_id", "cursor"])


def downgrade() -> None:
    op.drop_index("ix_checkout_events_agent_cursor", table_name="checkout_events")
    op.drop_index(op.f("ix_checkout_events_owner_id"), table_name="checkout_events")
    op.drop_index(op.f("ix_checkout_events_cart_item_id"), table_name="checkout_events")
    op.drop_index(op.f("ix_checkout_events_agent_id"), table_name="checkout_events")
    op.drop_table("checkout_events")

    op.drop_index("ix_checkout_executions_owner_status", table_name="checkout_executions")
    op.drop_index(op.f("ix_checkout_executions_status"), table_name="checkout_executions")
    op.drop_index(
        op.f("ix_checkout_executions_payment_method_id"), table_name="checkout_executions"
    )
    op.drop_index(op.f("ix_checkout_executions_owner_id"), table_name="checkout_executions")
    op.drop_index(op.f("ix_checkout_executions_agent_id"), table_name="checkout_executions")
    op.drop_table("checkout_executions")

    op.drop_constraint("ck_cart_items_checkout_fields", "cart_items", type_="check")
    op.drop_column("cart_items", "checkout_url")
    op.drop_column("cart_items", "checkout_adapter")
