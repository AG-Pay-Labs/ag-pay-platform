"""Rename user identity from username to email.

Revision ID: 4d2a8c7e9f10
Revises: f63d8a4c9210
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d2a8c7e9f10"
down_revision: str | Sequence[str] | None = "f63d8a4c9210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.alter_column(
        "users",
        "username",
        new_column_name="email",
        existing_type=sa.String(length=64),
        type_=sa.String(length=254),
        existing_nullable=False,
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.alter_column(
        "users",
        "email",
        new_column_name="username",
        existing_type=sa.String(length=254),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
