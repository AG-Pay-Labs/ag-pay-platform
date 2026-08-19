import enum
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ag_platform_api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentStatus(enum.StrEnum):
    pending = "pending"
    active = "active"
    revoked = "revoked"


class BillingProfileType(enum.StrEnum):
    personal = "personal"
    business = "business"


class PaymentMethodStatus(enum.StrEnum):
    active = "active"
    disabled = "disabled"


class PaymentApprovalMode(enum.StrEnum):
    always = "always"
    subscriptions_only = "subscriptions_only"
    above_amount = "above_amount"
    subscriptions_or_above_amount = "subscriptions_or_above_amount"
    never = "never"


class CartItemStatus(enum.StrEnum):
    proposed = "proposed"
    approved = "approved"
    cancelled = "cancelled"
    purchased = "purchased"


class CheckoutExecutionStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    action_required = "action_required"
    outcome_unknown = "outcome_unknown"


class PurchaseStatus(enum.StrEnum):
    completed = "completed"
    failed = "failed"
    refunded = "refunded"


class BillingPeriod(enum.StrEnum):
    monthly = "monthly"
    yearly = "yearly"


class SubscriptionStatus(enum.StrEnum):
    active = "active"
    cancelled = "cancelled"
    paused = "paused"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    agents: Mapped[list["Agent"]] = relationship(back_populates="owner")
    payment_methods: Mapped[list["PaymentMethod"]] = relationship(back_populates="owner")


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, native_enum=False), nullable=False, default=AgentStatus.pending
    )
    pairing_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    api_key_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    api_key_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    instance_id: Mapped[str | None] = mapped_column(String(255))
    software_version: Mapped[str | None] = mapped_column(String(100))
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[User] = relationship(back_populates="agents")
    assigned_payment_methods: Mapped[list["AgentPaymentMethod"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    payment_policy: Mapped["AgentPaymentPolicy | None"] = relationship(
        back_populates="agent", cascade="all, delete-orphan", uselist=False
    )


class AgentPaymentPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_payment_policies"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    mode: Mapped[PaymentApprovalMode] = mapped_column(
        Enum(PaymentApprovalMode, native_enum=False),
        nullable=False,
        default=PaymentApprovalMode.always,
        server_default=PaymentApprovalMode.always.value,
    )
    threshold_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    threshold_currency: Mapped[str | None] = mapped_column(String(3))

    agent: Mapped[Agent] = relationship(back_populates="payment_policy")

    __table_args__ = (
        CheckConstraint(
            "(mode IN ('above_amount', 'subscriptions_or_above_amount') "
            "AND threshold_amount IS NOT NULL AND threshold_currency IS NOT NULL) OR "
            "(mode IN ('always', 'subscriptions_only', 'never') "
            "AND threshold_amount IS NULL AND threshold_currency IS NULL)",
            name="threshold_fields",
        ),
        CheckConstraint(
            "threshold_amount IS NULL OR threshold_amount >= 0",
            name="threshold_non_negative",
        ),
        CheckConstraint(
            "threshold_currency IS NULL OR length(threshold_currency) = 3",
            name="threshold_currency_length",
        ),
    )


class PaymentMethod(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payment_methods"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[PaymentMethodStatus] = mapped_column(
        Enum(PaymentMethodStatus, native_enum=False),
        nullable=False,
        default=PaymentMethodStatus.active,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_payment_method_id: Mapped[str] = mapped_column(String(255), nullable=False)
    card_brand: Mapped[str] = mapped_column(String(32), nullable=False)
    card_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    expiry_month: Mapped[int] = mapped_column(Integer, nullable=False)
    expiry_year: Mapped[int] = mapped_column(Integer, nullable=False)
    billing_profile_type: Mapped[BillingProfileType] = mapped_column(
        Enum(BillingProfileType, native_enum=False), nullable=False
    )
    billing_details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    owner: Mapped[User] = relationship(back_populates="payment_methods")
    assigned_agents: Mapped[list["AgentPaymentMethod"]] = relationship(
        back_populates="payment_method", cascade="all, delete-orphan"
    )
    stored_card_credential: Mapped["StoredCardCredential | None"] = relationship(
        back_populates="payment_method", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "provider", "provider_payment_method_id", name="provider_reference"
        ),
    )


class StoredCardCredential(TimestampMixin, Base):
    __tablename__ = "stored_card_credentials"

    payment_method_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_methods.id", ondelete="CASCADE"), primary_key=True
    )
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    encrypted_pan: Mapped[str] = mapped_column(Text, nullable=False)

    payment_method: Mapped[PaymentMethod] = relationship(back_populates="stored_card_credential")


class AgentPaymentMethod(TimestampMixin, Base):
    __tablename__ = "agent_payment_methods"

    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    payment_method_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_methods.id", ondelete="CASCADE"), primary_key=True
    )

    agent: Mapped[Agent] = relationship(back_populates="assigned_payment_methods")
    payment_method: Mapped[PaymentMethod] = relationship(back_populates="assigned_agents")


class PurchaseCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "purchase_credentials"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    login_url: Mapped[str | None] = mapped_column(Text)


class CartItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cart_items"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    credential_id: Mapped[UUID] = mapped_column(
        ForeignKey("purchase_credentials.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    selected_payment_method_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_methods.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    product_url: Mapped[str] = mapped_column(Text, nullable=False)
    checkout_adapter: Mapped[str | None] = mapped_column(String(64))
    checkout_url: Mapped[str | None] = mapped_column(Text)
    merchant: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    billing_period: Mapped[BillingPeriod | None] = mapped_column(
        Enum(BillingPeriod, native_enum=False)
    )
    status: Mapped[CartItemStatus] = mapped_column(
        Enum(CartItemStatus, native_enum=False),
        nullable=False,
        default=CartItemStatus.proposed,
        index=True,
    )
    decision_note: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    credential: Mapped[PurchaseCredential] = relationship()
    purchase: Mapped["Purchase | None"] = relationship(back_populates="cart_item", uselist=False)
    checkout_execution: Mapped["CheckoutExecution | None"] = relationship(
        back_populates="cart_item", uselist=False
    )

    __table_args__ = (
        CheckConstraint(
            "(checkout_adapter IS NULL AND checkout_url IS NULL) OR "
            "(checkout_adapter IS NOT NULL AND checkout_url IS NOT NULL)",
            name="checkout_fields",
        ),
        Index("ix_cart_items_owner_status", "owner_id", "status"),
    )


class CheckoutExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "checkout_executions"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    payment_method_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_methods.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    cart_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("cart_items.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    adapter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resolved_form_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    approved_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    checkout_origin: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[CheckoutExecutionStatus] = mapped_column(
        Enum(CheckoutExecutionStatus, native_enum=False),
        nullable=False,
        default=CheckoutExecutionStatus.queued,
        server_default=CheckoutExecutionStatus.queued.value,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    browserbase_session_id: Mapped[str | None] = mapped_column(String(255))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    merchant_order_reference: Mapped[str | None] = mapped_column(String(128))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    cart_item: Mapped[CartItem] = relationship(back_populates="checkout_execution")
    events: Mapped[list["CheckoutEvent"]] = relationship(back_populates="execution")
    status_transitions: Mapped[list["CheckoutStatusTransition"]] = relationship(
        order_by=lambda: CheckoutStatusTransition.sequence,
        viewonly=True,
    )

    __table_args__ = (
        CheckConstraint("approved_amount > 0", name="approved_amount_positive"),
        CheckConstraint("length(currency) = 3", name="currency_length"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        Index("ix_checkout_executions_owner_status", "owner_id", "status"),
    )


class CheckoutStatusTransition(Base):
    __tablename__ = "checkout_status_transitions"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("checkout_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[CheckoutExecutionStatus] = mapped_column(
        Enum(CheckoutExecutionStatus, native_enum=False), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        Index(
            "ix_checkout_status_transitions_execution_sequence",
            "execution_id",
            "sequence",
        ),
        {"sqlite_autoincrement": True},
    )


class Purchase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "purchases"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    payment_method_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_methods.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    cart_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("cart_items.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    status: Mapped[PurchaseStatus] = mapped_column(
        Enum(PurchaseStatus, native_enum=False),
        nullable=False,
        default=PurchaseStatus.completed,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant_order_reference: Mapped[str | None] = mapped_column(String(128))
    receipt_url: Mapped[str | None] = mapped_column(Text)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    cart_item: Mapped[CartItem] = relationship(back_populates="purchase")
    subscription: Mapped["Subscription | None"] = relationship(
        back_populates="purchase", uselist=False
    )

    __table_args__ = (
        UniqueConstraint(
            "payment_method_id", "provider_reference", name="payment_provider_reference"
        ),
        Index("ix_purchases_owner_purchased_at", "owner_id", "purchased_at"),
    )


class CheckoutEvent(Base):
    __tablename__ = "checkout_events"

    cursor: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(default=uuid4, nullable=False, unique=True)
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("checkout_executions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    cart_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("cart_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purchase_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchases.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[CheckoutExecutionStatus] = mapped_column(
        Enum(CheckoutExecutionStatus, native_enum=False), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution: Mapped[CheckoutExecution] = relationship(back_populates="events")

    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'action_required', 'outcome_unknown')",
            name="terminal_status",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("length(currency) = 3", name="currency_length"),
        Index("ix_checkout_events_agent_cursor", "agent_id", "cursor"),
        {"sqlite_autoincrement": True},
    )


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purchase_id: Mapped[UUID] = mapped_column(
        ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    billing_period: Mapped[BillingPeriod] = mapped_column(
        Enum(BillingPeriod, native_enum=False), nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False),
        nullable=False,
        default=SubscriptionStatus.active,
    )
    next_billing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    purchase: Mapped[Purchase] = relationship(back_populates="subscription")
