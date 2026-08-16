"""allow a reconciliation event after an unknown checkout outcome

Revision ID: a27c6e91f4b3
Revises: e51a9b7c2d30
Create Date: 2026-08-16 03:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a27c6e91f4b3"
down_revision: str | Sequence[str] | None = "e51a9b7c2d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("uq_checkout_events_execution_id"),
        "checkout_events",
        type_="unique",
    )
    op.create_index(
        op.f("ix_checkout_events_execution_id"),
        "checkout_events",
        ["execution_id"],
    )


def downgrade() -> None:
    checkout_events = sa.table(
        "checkout_events",
        sa.column("cursor", sa.Integer()),
        sa.column("execution_id", sa.Uuid()),
    )
    latest_event_cursors = sa.select(sa.func.max(checkout_events.c.cursor)).group_by(
        checkout_events.c.execution_id
    )
    # The previous schema can represent only one event per execution. Keep the
    # latest event, which preserves the reconciled success when one exists.
    op.execute(
        sa.delete(checkout_events).where(checkout_events.c.cursor.not_in(latest_event_cursors))
    )
    op.drop_index(op.f("ix_checkout_events_execution_id"), table_name="checkout_events")
    op.create_unique_constraint(
        op.f("uq_checkout_events_execution_id"),
        "checkout_events",
        ["execution_id"],
    )
