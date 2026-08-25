"""replace per-agent policies with reusable payment rule sets

Revision ID: 7a91c4e2d8b5
Revises: 4d2a8c7e9f10
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "7a91c4e2d8b5"
down_revision: str | Sequence[str] | None = "4d2a8c7e9f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rule_set_table() -> sa.TableClause:
    return sa.table(
        "payment_rule_sets",
        sa.column("id", sa.Uuid()),
        sa.column("owner_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("mode", sa.String()),
        sa.column("threshold_amount", sa.Numeric()),
        sa.column("threshold_currency", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    op.create_table(
        "payment_rule_sets",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
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
            name="ck_payment_rule_sets_threshold_fields",
        ),
        sa.CheckConstraint(
            "threshold_amount IS NULL OR threshold_amount >= 0",
            name="ck_payment_rule_sets_threshold_non_negative",
        ),
        sa.CheckConstraint(
            "threshold_currency IS NULL OR length(threshold_currency) = 3",
            name="ck_payment_rule_sets_threshold_currency_length",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_payment_rule_sets_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_rule_sets")),
        sa.UniqueConstraint("owner_id", "name", name="uq_payment_rule_sets_owner_name"),
    )
    op.create_index(
        op.f("ix_payment_rule_sets_owner_id"),
        "payment_rule_sets",
        ["owner_id"],
        unique=False,
    )
    op.add_column("agents", sa.Column("payment_rule_set_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_agents_payment_rule_set_id"),
        "agents",
        ["payment_rule_set_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_agents_payment_rule_set_id_payment_rule_sets"),
        "agents",
        "payment_rule_sets",
        ["payment_rule_set_id"],
        ["id"],
        ondelete="SET NULL",
    )

    policies = list(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT p.id, p.owner_id, p.agent_id, p.mode, p.threshold_amount, "
                "p.threshold_currency, p.created_at, p.updated_at, a.name AS agent_name "
                "FROM agent_payment_policies p JOIN agents a ON a.id = p.agent_id"
            )
        )
        .mappings()
    )
    used_names: dict[UUID, set[str]] = defaultdict(set)
    migrated: list[dict[str, object]] = []
    for policy in policies:
        owner_id = UUID(str(policy["owner_id"]))
        base_name = f"{policy['agent_name']} rules"[:80]
        name = base_name
        suffix = 2
        while name.lower() in used_names[owner_id]:
            ending = f" ({suffix})"
            name = f"{base_name[: 80 - len(ending)]}{ending}"
            suffix += 1
        used_names[owner_id].add(name.lower())
        migrated.append(
            {
                "id": UUID(str(policy["id"])),
                "owner_id": owner_id,
                "name": name,
                "mode": policy["mode"],
                "threshold_amount": policy["threshold_amount"],
                "threshold_currency": policy["threshold_currency"],
                "created_at": policy["created_at"],
                "updated_at": policy["updated_at"],
            }
        )
    if migrated:
        op.bulk_insert(_rule_set_table(), migrated)
        for policy in policies:
            op.execute(
                sa.text(
                    "UPDATE agents SET payment_rule_set_id = :rule_set_id WHERE id = :agent_id"
                ).bindparams(
                    rule_set_id=UUID(str(policy["id"])),
                    agent_id=UUID(str(policy["agent_id"])),
                )
            )

    op.drop_index(
        op.f("ix_agent_payment_policies_owner_id"),
        table_name="agent_payment_policies",
    )
    op.drop_table("agent_payment_policies")


def downgrade() -> None:
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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

    rows = list(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT a.id AS agent_id, a.owner_id, r.mode, r.threshold_amount, "
                "r.threshold_currency, r.created_at, r.updated_at "
                "FROM agents a LEFT JOIN payment_rule_sets r ON r.id = a.payment_rule_set_id"
            )
        )
        .mappings()
    )
    now = datetime.now(UTC)
    if rows:
        policy_table = sa.table(
            "agent_payment_policies",
            sa.column("id", sa.Uuid()),
            sa.column("owner_id", sa.Uuid()),
            sa.column("agent_id", sa.Uuid()),
            sa.column("mode", sa.String()),
            sa.column("threshold_amount", sa.Numeric()),
            sa.column("threshold_currency", sa.String()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(
            policy_table,
            [
                {
                    "id": uuid4(),
                    "owner_id": UUID(str(row["owner_id"])),
                    "agent_id": UUID(str(row["agent_id"])),
                    "mode": row["mode"] or "always",
                    "threshold_amount": row["threshold_amount"],
                    "threshold_currency": row["threshold_currency"],
                    "created_at": row["created_at"] or now,
                    "updated_at": row["updated_at"] or now,
                }
                for row in rows
            ],
        )

    op.drop_constraint(
        op.f("fk_agents_payment_rule_set_id_payment_rule_sets"),
        "agents",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_agents_payment_rule_set_id"), table_name="agents")
    op.drop_column("agents", "payment_rule_set_id")
    op.drop_index(op.f("ix_payment_rule_sets_owner_id"), table_name="payment_rule_sets")
    op.drop_table("payment_rule_sets")
