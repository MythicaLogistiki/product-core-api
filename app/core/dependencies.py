"""Dependency injection for multi-tenant resolution and authorization."""

import os
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request, HTTPException, status, Depends, Header
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, current_tenant_id
from app.core.middleware import get_current_user, TokenData
from app.models.tenant import Tenant, UserTenant, UserRole, TenantType, SupportAccessLog
from dotenv import load_dotenv

load_dotenv()

# Internal support token for Business Support Agents
SUPPORT_ACCESS_SECRET = os.getenv("SUPPORT_ACCESS_SECRET")


@dataclass
class TenantContext:
    """Resolved tenant context for the current request."""
    tenant: Tenant
    user_membership: UserTenant | None  # None for platform admins or support access
    is_support_access: bool = False
    is_platform_admin: bool = False


async def get_tenant_by_slug(
    db: AsyncSession,
    slug: str,
) -> Tenant | None:
    """Look up a tenant by its URL slug."""
    result = await db.execute(
        select(Tenant).where(
            and_(
                Tenant.slug == slug,
                Tenant.is_active == True,
            )
        )
    )
    return result.scalar_one_or_none()


async def get_user_tenant_membership(
    db: AsyncSession,
    user_id: str,
    tenant_id: UUID,
) -> UserTenant | None:
    """Check if a user has membership in a tenant."""
    result = await db.execute(
        select(UserTenant).where(
            and_(
                UserTenant.user_id == user_id,
                UserTenant.tenant_id == tenant_id,
                UserTenant.is_active == True,
            )
        )
    )
    return result.scalar_one_or_none()


async def verify_support_access(
    db: AsyncSession,
    user_id: str,
    tenant_id: UUID,
) -> bool:
    """
    Verify if a support agent has valid, non-expired access to a tenant.

    Support access requires:
    1. User has support_agent role in any tenant (platform-level)
    2. support_access_enabled = True for this specific tenant
    3. support_access_expires_at > now (if set)
    """
    result = await db.execute(
        select(UserTenant).where(
            and_(
                UserTenant.user_id == user_id,
                UserTenant.tenant_id == tenant_id,
                UserTenant.support_access_enabled == True,
                UserTenant.is_active == True,
            )
        )
    )
    membership = result.scalar_one_or_none()

    if not membership:
        return False

    # Check expiration if set
    if membership.support_access_expires_at:
        if membership.support_access_expires_at < datetime.now(timezone.utc):
            return False

    return True


async def log_support_access(
    db: AsyncSession,
    support_user_id: str,
    tenant_id: UUID,
    action: str,
    reason: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Log support agent access for audit compliance."""
    log_entry = SupportAccessLog(
        support_user_id=support_user_id,
        tenant_id=tenant_id,
        action=action,
        reason=reason,
        ip_address=ip_address,
    )
    db.add(log_entry)
    await db.flush()


async def get_current_tenant(
    request: Request,
    x_tenant_slug: Optional[str] = Header(None, alias="X-Tenant-Slug"),
    x_support_token: Optional[str] = Header(None, alias="X-Support-Token"),
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(get_current_user),
) -> TenantContext:
    """
    Resolve tenant from X-Tenant-Slug header and verify user access.

    Access Flow:
    1. If no slug provided and user has "individual" tenant, use that
    2. If slug provided, look up tenant by slug
    3. Verify user has membership in UserTenant table
    4. Special case: Platform Admins bypass membership check
    5. Special case: Support Agents with valid support_access can access

    Headers:
    - X-Tenant-Slug: The tenant slug from URL path (e.g., "acme-tax")
    - X-Support-Token: Optional token for support agent impersonation
    """
    is_platform_admin = user.role == "platform_admin"
    is_support_agent = user.role == "support_agent"

    # Handle "personal" tenant (Individual Mode - no slug)
    if not x_tenant_slug or x_tenant_slug == "personal":
        # Look for user's individual tenant
        result = await db.execute(
            select(Tenant).where(
                and_(
                    Tenant.owner_user_id == user.sub,
                    Tenant.type == TenantType.INDIVIDUAL,
                    Tenant.is_active == True,
                )
            )
        )
        tenant = result.scalar_one_or_none()

        if not tenant:
            # Auto-create individual tenant for new users
            tenant = await _create_individual_tenant(db, user.sub)

        # Set tenant context for RLS
        current_tenant_id.set(str(tenant.id))

        # Get or create membership
        membership = await get_user_tenant_membership(db, user.sub, tenant.id)
        if not membership:
            membership = UserTenant(
                user_id=user.sub,
                tenant_id=tenant.id,
                role=UserRole.OWNER,
            )
            db.add(membership)
            await db.flush()

        return TenantContext(
            tenant=tenant,
            user_membership=membership,
            is_support_access=False,
            is_platform_admin=is_platform_admin,
        )

    # Organization Mode - resolve by slug
    tenant = await get_tenant_by_slug(db, x_tenant_slug)

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant '{x_tenant_slug}' not found",
        )

    # Set tenant context for RLS
    current_tenant_id.set(str(tenant.id))

    # Platform Admin bypass
    if is_platform_admin:
        return TenantContext(
            tenant=tenant,
            user_membership=None,
            is_support_access=False,
            is_platform_admin=True,
        )

    # Support Agent access check
    if is_support_agent:
        # Verify support token if configured
        if SUPPORT_ACCESS_SECRET and x_support_token != SUPPORT_ACCESS_SECRET:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid support access token",
            )

        has_support_access = await verify_support_access(db, user.sub, tenant.id)

        if has_support_access:
            # Log the access for audit
            client_ip = request.client.host if request.client else None
            await log_support_access(
                db=db,
                support_user_id=user.sub,
                tenant_id=tenant.id,
                action="data_viewed",
                ip_address=client_ip,
            )

            return TenantContext(
                tenant=tenant,
                user_membership=None,
                is_support_access=True,
                is_platform_admin=False,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Support access not granted for this tenant",
            )

    # Standard user - verify membership
    membership = await get_user_tenant_membership(db, user.sub, tenant.id)

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You do not have access to tenant '{x_tenant_slug}'",
        )

    return TenantContext(
        tenant=tenant,
        user_membership=membership,
        is_support_access=False,
        is_platform_admin=False,
    )


async def _create_individual_tenant(
    db: AsyncSession,
    user_id: str,
) -> Tenant:
    """Create an individual tenant for a new user."""
    # Generate a unique slug from user_id
    slug = f"user-{user_id.replace('@', '-').replace('.', '-')[:50]}"

    tenant = Tenant(
        slug=slug,
        name="Personal",
        type=TenantType.INDIVIDUAL,
        owner_user_id=user_id,
    )
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)

    return tenant


# Convenience dependencies for specific access levels
async def require_tenant_admin(
    context: TenantContext = Depends(get_current_tenant),
) -> TenantContext:
    """Require admin or owner role in the tenant."""
    if context.is_platform_admin:
        return context

    if not context.user_membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    if context.user_membership.role not in [UserRole.OWNER, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return context


async def require_tenant_owner(
    context: TenantContext = Depends(get_current_tenant),
) -> TenantContext:
    """Require owner role in the tenant."""
    if context.is_platform_admin:
        return context

    if not context.user_membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )

    if context.user_membership.role != UserRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )

    return context


async def require_platform_admin(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Require platform admin role (Numbersence staff)."""
    if user.role != "platform_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access required",
        )
    return user


def require_tenant_role(allowed_roles: list[UserRole]):
    """Factory for role-based tenant access."""
    async def dependency(
        context: TenantContext = Depends(get_current_tenant),
    ) -> TenantContext:
        if context.is_platform_admin:
            return context

        if context.is_support_access:
            # Support agents have read-only access
            return context

        if not context.user_membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {[r.value for r in allowed_roles]}",
            )

        if context.user_membership.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {[r.value for r in allowed_roles]}",
            )

        return context

    return dependency
