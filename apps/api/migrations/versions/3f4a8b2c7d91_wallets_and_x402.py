"""add wallet payment methods and x402 execution state

Revision ID: 3f4a8b2c7d91
Revises: 7a91c4e2d8b5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f4a8b2c7d91"
down_revision: str | Sequence[str] | None = "7a91c4e2d8b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_CHECK_CONSTRAINT_RENAMES = (
    (
        "cart_items",
        "ck_cart_items_ck_cart_items_checkout_fields",
        "ck_cart_items_checkout_fields",
    ),
    (
        "checkout_events",
        "ck_checkout_events_ck_checkout_events_amount_positive",
        "ck_checkout_events_amount_positive",
    ),
    (
        "checkout_events",
        "ck_checkout_events_ck_checkout_events_currency_length",
        "ck_checkout_events_currency_length",
    ),
    (
        "checkout_events",
        "ck_checkout_events_ck_checkout_events_terminal_status",
        "ck_checkout_events_terminal_status",
    ),
    (
        "checkout_executions",
        "ck_checkout_executions_ck_checkout_executions_approved__b280",
        "ck_checkout_executions_approved_amount_positive",
    ),
    (
        "checkout_executions",
        "ck_checkout_executions_ck_checkout_executions_attempt_c_3cbb",
        "ck_checkout_executions_attempt_count_non_negative",
    ),
    (
        "checkout_executions",
        "ck_checkout_executions_ck_checkout_executions_currency_length",
        "ck_checkout_executions_currency_length",
    ),
    (
        "payment_rule_sets",
        "ck_payment_rule_sets_ck_payment_rule_sets_threshold_cur_dd10",
        "ck_payment_rule_sets_threshold_currency_length",
    ),
    (
        "payment_rule_sets",
        "ck_payment_rule_sets_ck_payment_rule_sets_threshold_fields",
        "ck_payment_rule_sets_threshold_fields",
    ),
    (
        "payment_rule_sets",
        "ck_payment_rule_sets_ck_payment_rule_sets_threshold_non_36c7",
        "ck_payment_rule_sets_threshold_non_negative",
    ),
)


def _rename_legacy_check_constraints(*, reverse: bool) -> None:
    for table_name, old_name, new_name in LEGACY_CHECK_CONSTRAINT_RENAMES:
        source, target = (new_name, old_name) if reverse else (old_name, new_name)
        op.execute(
            sa.text(f'ALTER TABLE "{table_name}" RENAME CONSTRAINT "{source}" TO "{target}"')
        )


def upgrade() -> None:
    # Earlier migrations passed convention-expanded check names back through the
    # naming convention. Normalize those PostgreSQL names so metadata diffing is clean.
    _rename_legacy_check_constraints(reverse=False)
    op.alter_column(
        "checkout_executions",
        "status",
        existing_type=sa.String(length=15),
        type_=sa.String(length=18),
        existing_nullable=False,
    )
    op.alter_column(
        "checkout_status_transitions",
        "status",
        existing_type=sa.String(length=15),
        type_=sa.String(length=18),
        existing_nullable=False,
    )
    op.alter_column(
        "checkout_events",
        "status",
        existing_type=sa.String(length=15),
        type_=sa.String(length=18),
        existing_nullable=False,
    )
    op.add_column(
        "payment_methods",
        sa.Column(
            "kind",
            sa.Enum("card", "wallet", name="paymentmethodkind", native_enum=False),
            server_default=sa.text("'card'"),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_payment_methods_kind"), "payment_methods", ["kind"])
    op.add_column("payment_methods", sa.Column("wallet_address", sa.String(length=42)))
    op.add_column("payment_methods", sa.Column("wallet_address_normalized", sa.String(length=42)))
    op.add_column("payment_methods", sa.Column("wallet_network", sa.String(length=64)))
    op.add_column("payment_methods", sa.Column("wallet_chain_id", sa.Integer()))
    op.add_column("payment_methods", sa.Column("wallet_verified_at", sa.DateTime(timezone=True)))
    for column_name, existing_type in (
        ("card_brand", sa.String(length=32)),
        ("card_last4", sa.String(length=4)),
        ("expiry_month", sa.Integer()),
        ("expiry_year", sa.Integer()),
        (
            "billing_profile_type",
            sa.Enum("personal", "business", name="billingprofiletype", native_enum=False),
        ),
        ("billing_details", sa.JSON()),
    ):
        op.alter_column("payment_methods", column_name, existing_type=existing_type, nullable=True)
    op.create_unique_constraint(
        "wallet_reference",
        "payment_methods",
        ["owner_id", "provider", "wallet_network", "wallet_address_normalized"],
    )
    op.create_check_constraint(
        op.f("ck_payment_methods_kind_fields"),
        "payment_methods",
        "(kind = 'card' AND card_brand IS NOT NULL AND card_last4 IS NOT NULL "
        "AND expiry_month IS NOT NULL AND expiry_year IS NOT NULL "
        "AND billing_profile_type IS NOT NULL AND billing_details IS NOT NULL "
        "AND wallet_address IS NULL AND wallet_address_normalized IS NULL "
        "AND wallet_network IS NULL AND wallet_chain_id IS NULL "
        "AND wallet_verified_at IS NULL) OR "
        "(kind = 'wallet' AND card_brand IS NULL AND card_last4 IS NULL "
        "AND expiry_month IS NULL AND expiry_year IS NULL "
        "AND billing_profile_type IS NULL AND billing_details IS NULL "
        "AND wallet_address IS NOT NULL AND wallet_address_normalized IS NOT NULL "
        "AND wallet_network IS NOT NULL AND wallet_chain_id IS NOT NULL "
        "AND wallet_verified_at IS NOT NULL)",
    )

    op.create_table(
        "wallet_connection_challenges",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("address_normalized", sa.String(length=42), nullable=False),
        sa.Column("network", sa.String(length=64), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
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
            "chain_id > 0", name=op.f("ck_wallet_connection_challenges_chain_id_positive")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_wallet_connection_challenges_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallet_connection_challenges")),
    )
    op.create_index(
        op.f("ix_wallet_connection_challenges_owner_id"),
        "wallet_connection_challenges",
        ["owner_id"],
    )
    op.create_index(
        op.f("ix_wallet_connection_challenges_expires_at"),
        "wallet_connection_challenges",
        ["expires_at"],
    )
    op.create_index(
        "ix_wallet_challenges_owner_expires",
        "wallet_connection_challenges",
        ["owner_id", "expires_at"],
    )

    op.alter_column(
        "cart_items",
        "credential_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    for table_name, column_name in (
        ("cart_items", "unit_price"),
        ("checkout_executions", "approved_amount"),
        ("purchases", "amount"),
        ("checkout_events", "amount"),
    ):
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Numeric(precision=18, scale=2),
            type_=sa.Numeric(precision=38, scale=18),
            existing_nullable=False,
        )

    op.create_table(
        "x402_payments",
        sa.Column("cart_item_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid()),
        sa.Column("resource_url", sa.Text(), nullable=False),
        sa.Column("payment_required", sa.JSON(), nullable=False),
        sa.Column("selected_requirements", sa.JSON(), nullable=False),
        sa.Column("network", sa.String(length=64), nullable=False),
        sa.Column("asset", sa.String(length=42), nullable=False),
        sa.Column("asset_symbol", sa.String(length=16), nullable=False),
        sa.Column("asset_decimals", sa.Integer(), nullable=False),
        sa.Column("amount_atomic", sa.String(length=78), nullable=False),
        sa.Column("pay_to", sa.String(length=42), nullable=False),
        sa.Column("transfer_method", sa.String(length=32), nullable=False),
        sa.Column("payment_signature_hash", sa.String(length=64)),
        sa.Column("response_status_code", sa.Integer()),
        sa.Column("response_mime_type", sa.String(length=255)),
        sa.Column("encrypted_response_body", sa.Text()),
        sa.Column("payment_response", sa.JSON()),
        sa.Column("transaction", sa.String(length=255)),
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
            "asset_decimals >= 0 AND asset_decimals <= 18",
            name=op.f("ck_x402_payments_asset_decimals"),
        ),
        sa.CheckConstraint(
            "length(amount_atomic) > 0",
            name=op.f("ck_x402_payments_amount_atomic_non_empty"),
        ),
        sa.CheckConstraint(
            "transfer_method IN ('eip3009', 'permit2')",
            name=op.f("ck_x402_payments_transfer_method_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["checkout_executions.id"],
            name=op.f("fk_x402_payments_execution_id_checkout_executions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cart_item_id"],
            ["cart_items.id"],
            name=op.f("fk_x402_payments_cart_item_id_cart_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("cart_item_id", name=op.f("pk_x402_payments")),
        sa.UniqueConstraint("execution_id", name=op.f("uq_x402_payments_execution_id")),
        sa.UniqueConstraint(
            "payment_signature_hash", name=op.f("uq_x402_payments_payment_signature_hash")
        ),
    )


def downgrade() -> None:
    # Wallet payment methods cannot be represented by the previous card-only schema.
    op.execute(
        sa.text(
            "DELETE FROM checkout_events WHERE execution_id IN "
            "(SELECT id FROM checkout_executions WHERE adapter_key = 'x402')"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM purchases WHERE cart_item_id IN "
            "(SELECT id FROM cart_items WHERE checkout_adapter = 'x402')"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM checkout_status_transitions WHERE execution_id IN "
            "(SELECT id FROM checkout_executions WHERE adapter_key = 'x402')"
        )
    )
    op.drop_table("x402_payments")
    op.execute(sa.text("DELETE FROM checkout_executions WHERE adapter_key = 'x402'"))
    op.execute(sa.text("DELETE FROM cart_items WHERE checkout_adapter = 'x402'"))
    op.execute(
        sa.text(
            "DELETE FROM agent_payment_methods WHERE payment_method_id IN "
            "(SELECT id FROM payment_methods WHERE kind = 'wallet')"
        )
    )
    op.execute(sa.text("DELETE FROM payment_methods WHERE kind = 'wallet'"))

    op.alter_column(
        "checkout_events",
        "status",
        existing_type=sa.String(length=18),
        type_=sa.String(length=15),
        existing_nullable=False,
    )
    op.alter_column(
        "checkout_status_transitions",
        "status",
        existing_type=sa.String(length=18),
        type_=sa.String(length=15),
        existing_nullable=False,
    )
    op.alter_column(
        "checkout_executions",
        "status",
        existing_type=sa.String(length=18),
        type_=sa.String(length=15),
        existing_nullable=False,
    )

    for table_name, column_name in (
        ("checkout_events", "amount"),
        ("purchases", "amount"),
        ("checkout_executions", "approved_amount"),
        ("cart_items", "unit_price"),
    ):
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Numeric(precision=38, scale=18),
            type_=sa.Numeric(precision=18, scale=2),
            existing_nullable=False,
        )
    op.alter_column("cart_items", "credential_id", existing_type=sa.Uuid(), nullable=False)

    op.drop_index("ix_wallet_challenges_owner_expires", table_name="wallet_connection_challenges")
    op.drop_index(
        op.f("ix_wallet_connection_challenges_expires_at"),
        table_name="wallet_connection_challenges",
    )
    op.drop_index(
        op.f("ix_wallet_connection_challenges_owner_id"),
        table_name="wallet_connection_challenges",
    )
    op.drop_table("wallet_connection_challenges")

    op.drop_constraint(op.f("ck_payment_methods_kind_fields"), "payment_methods", type_="check")
    op.drop_constraint("wallet_reference", "payment_methods", type_="unique")
    for column_name, existing_type in (
        ("billing_details", sa.JSON()),
        (
            "billing_profile_type",
            sa.Enum("personal", "business", name="billingprofiletype", native_enum=False),
        ),
        ("expiry_year", sa.Integer()),
        ("expiry_month", sa.Integer()),
        ("card_last4", sa.String(length=4)),
        ("card_brand", sa.String(length=32)),
    ):
        op.alter_column("payment_methods", column_name, existing_type=existing_type, nullable=False)
    op.drop_column("payment_methods", "wallet_verified_at")
    op.drop_column("payment_methods", "wallet_chain_id")
    op.drop_column("payment_methods", "wallet_network")
    op.drop_column("payment_methods", "wallet_address_normalized")
    op.drop_column("payment_methods", "wallet_address")
    op.drop_index(op.f("ix_payment_methods_kind"), table_name="payment_methods")
    op.drop_column("payment_methods", "kind")
    _rename_legacy_check_constraints(reverse=True)
