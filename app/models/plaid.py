"""Plaid database models with RLS support."""

import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from sqlalchemy import String, Text, DateTime, Date, Numeric, Boolean, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class PlaidItem(Base):
    """
    Represents a Plaid Item (a connection to a financial institution).
    Stores encrypted access tokens for security.
    """
    __tablename__ = "plaid_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    item_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    # Encrypted using Fernet - NEVER store in plain text
    encrypted_access_token: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    institution_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    institution_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    # Cursor for transaction sync pagination
    transaction_cursor: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    # Relationship to transactions
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="plaid_item",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_plaid_items_tenant_user", "tenant_id", "user_id"),
    )


class Transaction(Base):
    """
    Stores transaction data from Plaid.
    Inherits tenant_id from parent PlaidItem for RLS.
    """
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    plaid_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plaid_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Plaid's unique transaction ID
    plaid_transaction_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    account_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    # Transaction details
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    iso_currency_code: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    merchant_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    # Categorization
    category_primary: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    category_detailed: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    # Dates
    transaction_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )
    authorized_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    # Status
    pending: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    # Payment channel: online, in store, other
    payment_channel: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    # Relationship back to PlaidItem
    plaid_item: Mapped["PlaidItem"] = relationship(
        "PlaidItem",
        back_populates="transactions",
    )

    __table_args__ = (
        Index("ix_transactions_tenant_date", "tenant_id", "transaction_date"),
        Index("ix_transactions_account_date", "account_id", "transaction_date"),
    )
