"""Multi-tenant models with Organization and Individual support."""

import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, Boolean, ForeignKey, Index, Enum, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class TenantType(str, enum.Enum):
    """Type of tenant."""
    ORGANIZATION = "organization"
    INDIVIDUAL = "individual"


class UserRole(str, enum.Enum):
    """User roles within a tenant or platform."""
    # Tenant-level roles
    OWNER = "owner"           # Org owner, full access
    ADMIN = "admin"           # Org admin
    MEMBER = "member"         # Standard org member
    VIEWER = "viewer"         # Read-only access

    # Platform-level roles (Numbersence staff)
    PLATFORM_ADMIN = "platform_admin"       # Internal: create orgs, manage billing
    SUPPORT_AGENT = "support_agent"         # Internal: view-only support access


class Tenant(Base):
    """
    Represents an Organization or Individual tenant.

    - ORGANIZATION: A company/business with multiple users (e.g., acme-tax)
    - INDIVIDUAL: A solo user acting as their own entity (personal tenant)
    """
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # URL-safe slug for path-based routing (e.g., "acme-tax")
    slug: Mapped[str] = mapped_column(
        String(63),
        unique=True,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    type: Mapped[TenantType] = mapped_column(
        Enum(TenantType, name="tenant_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=TenantType.ORGANIZATION,
    )
    # For individual tenants, this links to the owning user
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    # Metadata
    settings: Mapped[str | None] = mapped_column(
        Text,  # JSON string for flexible tenant settings
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

    # Relationships
    user_memberships: Mapped[list["UserTenant"]] = relationship(
        "UserTenant",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )


class UserTenant(Base):
    """
    Many-to-many mapping between Users and Tenants.

    A user can belong to multiple organizations and have different roles in each.
    This table also tracks platform-level staff with special access.
    """
    __tablename__ = "user_tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # User identifier (from auth service, e.g., email or user_id)
    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=UserRole.MEMBER,
    )
    # For support agents: whether they have active support access to this tenant
    support_access_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    # Support access expiration (temporary access window)
    support_access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Audit: who granted support access
    support_access_granted_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
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

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="user_memberships",
    )

    __table_args__ = (
        # A user can only have one membership per tenant
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
        Index("ix_user_tenants_user_tenant", "user_id", "tenant_id"),
    )


class SupportAccessLog(Base):
    """
    Audit log for support agent access to tenants.

    Tracks when support agents access customer data for compliance.
    """
    __tablename__ = "support_access_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    support_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(
        String(50),  # "access_granted", "access_revoked", "data_viewed"
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(
        Text,  # Support ticket ID or reason for access
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(45),  # IPv6 max length
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_support_access_logs_tenant_time", "tenant_id", "created_at"),
    )
