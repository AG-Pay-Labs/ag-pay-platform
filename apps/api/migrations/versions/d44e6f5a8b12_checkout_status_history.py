"""add durable checkout status history

Revision ID: d44e6f5a8b12
Revises: c82d91ae6410
Create Date: 2026-08-10 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d44e6f5a8b12"
down_revision: str | Sequence[str] | None = "c82d91ae6410"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SAFE_ERROR_CODES = (
    "checkout_disabled",
    "execution_invalid",
    "cart_not_approved",
    "agent_inactive",
    "payment_method_unavailable",
    "payment_method_unassigned",
    "amount_mismatch",
    "currency_mismatch",
    "currency_unsupported",
    "currency_precision_invalid",
    "provider_unsupported",
    "recurring_unsupported",
    "authorization_snapshot_failed",
    "card_reference_invalid",
    "card_unavailable",
    "card_reconciliation_required",
    "adapter_invalid",
    "origin_blocked",
    "item_mismatch",
    "quantity_mismatch",
    "total_not_found",
    "total_mismatch",
    "payment_form_not_found",
    "browser_session_failed",
    "browser_navigation_failed",
    "checkout_action_required",
    "payment_declined",
    "payment_outcome_unknown",
    "checkout_failed",
)


def upgrade() -> None:
    op.create_table(
        "checkout_status_transitions",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
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
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["checkout_executions.id"],
            name=op.f("fk_checkout_status_transitions_execution_id_checkout_executions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sequence", name=op.f("pk_checkout_status_transitions")),
    )
    op.create_index(
        op.f("ix_checkout_status_transitions_execution_id"),
        "checkout_status_transitions",
        ["execution_id"],
    )
    op.create_index(
        "ix_checkout_status_transitions_execution_sequence",
        "checkout_status_transitions",
        ["execution_id", "sequence"],
    )
    executions = sa.table(
        "checkout_executions",
        sa.column("id", sa.Uuid()),
        sa.column("status", sa.String(length=32)),
        sa.column("attempt_count", sa.Integer()),
        sa.column("error_code", sa.String(length=64)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("completed_at", sa.DateTime(timezone=True)),
    )
    transitions = sa.table(
        "checkout_status_transitions",
        sa.column("execution_id", sa.Uuid()),
        sa.column("status", sa.String(length=32)),
        sa.column("attempt_count", sa.Integer()),
        sa.column("error_code", sa.String(length=64)),
        sa.column("occurred_at", sa.DateTime(timezone=True)),
    )
    safe_error_code = sa.case(
        (executions.c.error_code.is_(None), None),
        (executions.c.error_code.in_(SAFE_ERROR_CODES), executions.c.error_code),
        else_="checkout_failed",
    )
    op.execute(
        transitions.insert().from_select(
            (
                "execution_id",
                "status",
                "attempt_count",
                "error_code",
                "occurred_at",
            ),
            sa.select(
                executions.c.id,
                executions.c.status,
                executions.c.attempt_count,
                safe_error_code,
                sa.func.coalesce(
                    executions.c.completed_at,
                    executions.c.updated_at,
                    executions.c.created_at,
                    sa.func.now(),
                ),
            ),
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_checkout_status_transitions_execution_sequence",
        table_name="checkout_status_transitions",
    )
    op.drop_index(
        op.f("ix_checkout_status_transitions_execution_id"),
        table_name="checkout_status_transitions",
    )
    op.drop_table("checkout_status_transitions")
