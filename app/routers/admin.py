"""Admin API endpoints for Platform Admin Console."""

import uuid
import secrets
from math import ceil
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.middleware import get_current_user, TokenData, require_platform_admin
from app.models.tenant import Tenant, UserTenant, TenantType, UserRole

router = APIRouter(prefix="/admin", tags=["admin"])


# ============== Request/Response Models ==============

class CreateTenantRequest(BaseModel):
    """Request to create a new organization."""
    name: str
    slug: str
    owner_email: EmailStr
    subscription_tier: str = "pro"  # free, pro, enterprise


class TenantResponse(BaseModel):
    """Tenant details response."""
    id: str
    slug: str
    name: str
    type: str
    owner_user_id: Optional[str]
    owner_email: Optional[str]
    subscription_tier: str
    is_active: bool
    created_at: str
    updated_at: Optional[str]
    user_count: int = 0

    class Config:
        from_attributes = True


class CreateTenantResponse(BaseModel):
    """Response after creating a tenant."""
    tenant: TenantResponse
    invitation_link: str


class TenantListResponse(BaseModel):
    """Paginated list of tenants."""
    items: list[TenantResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class UpdateTenantRequest(BaseModel):
    """Request to update a tenant."""
    name: Optional[str] = None
    subscription_tier: Optional[str] = None
    is_active: Optional[bool] = None


# ============== Helper Functions ==============

async def get_tenant_user_count(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Get the number of users in a tenant."""
    result = await db.execute(
        select(func.count(UserTenant.id)).where(
            and_(
                UserTenant.tenant_id == tenant_id,
                UserTenant.is_active == True,
            )
        )
    )
    return result.scalar_one()


def tenant_to_response(tenant: Tenant, user_count: int = 0) -> TenantResponse:
    """Convert Tenant model to response."""
    return TenantResponse(
        id=str(tenant.id),
        slug=tenant.slug,
        name=tenant.name,
        type=tenant.type.value,
        owner_user_id=tenant.owner_user_id,
        owner_email=tenant.owner_user_id,  # For now, owner_user_id is the email
        subscription_tier=tenant.settings or "pro",  # Store tier in settings for now
        is_active=tenant.is_active,
        created_at=tenant.created_at.isoformat(),
        updated_at=tenant.updated_at.isoformat() if tenant.updated_at else None,
        user_count=user_count,
    )


# ============== Endpoints ==============

@router.get("/tenants", response_model=TenantListResponse)
async def list_tenants(
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(require_platform_admin),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
) -> TenantListResponse:
    """
    List all tenants (organizations) in the platform.

    Requires platform_admin role.
    """
    # Build query
    conditions = [Tenant.type == TenantType.ORGANIZATION]

    if search:
        conditions.append(
            Tenant.name.ilike(f"%{search}%") | Tenant.slug.ilike(f"%{search}%")
        )

    where_clause = and_(*conditions)

    # Get total count
    count_result = await db.execute(
        select(func.count(Tenant.id)).where(where_clause)
    )
    total = count_result.scalar_one()

    # Get paginated tenants
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Tenant)
        .where(where_clause)
        .order_by(Tenant.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    tenants = result.scalars().all()

    # Get user counts for each tenant
    items = []
    for tenant in tenants:
        user_count = await get_tenant_user_count(db, tenant.id)
        items.append(tenant_to_response(tenant, user_count))

    total_pages = ceil(total / page_size) if total > 0 else 1

    return TenantListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/tenants", response_model=CreateTenantResponse)
async def create_tenant(
    request: CreateTenantRequest,
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(require_platform_admin),
) -> CreateTenantResponse:
    """
    Create a new organization tenant.

    Requires platform_admin role.
    """
    # Check if slug is already taken
    existing = await db.execute(
        select(Tenant).where(Tenant.slug == request.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Slug '{request.slug}' is already taken",
        )

    # Create tenant
    tenant = Tenant(
        slug=request.slug,
        name=request.name,
        type=TenantType.ORGANIZATION,
        owner_user_id=request.owner_email,
        settings=request.subscription_tier,  # Store tier in settings for now
        is_active=True,
    )
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)

    # Create owner membership (will be activated when owner accepts invitation)
    owner_membership = UserTenant(
        user_id=request.owner_email,
        tenant_id=tenant.id,
        role=UserRole.OWNER,
        is_active=True,
    )
    db.add(owner_membership)
    await db.flush()

    # Generate invitation token
    invitation_token = secrets.token_urlsafe(32)

    # In production, you would:
    # 1. Store the invitation token with expiration
    # 2. Send email to owner_email with the invitation link
    # For now, we return a mock invitation link
    invitation_link = f"https://numbersence.com/invite/{invitation_token}?org={tenant.slug}"

    return CreateTenantResponse(
        tenant=tenant_to_response(tenant, user_count=1),
        invitation_link=invitation_link,
    )


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(require_platform_admin),
) -> TenantResponse:
    """
    Get details of a specific tenant.

    Requires platform_admin role.
    """
    result = await db.execute(
        select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    user_count = await get_tenant_user_count(db, tenant.id)
    return tenant_to_response(tenant, user_count)


@router.patch("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: str,
    request: UpdateTenantRequest,
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(require_platform_admin),
) -> TenantResponse:
    """
    Update a tenant's details.

    Requires platform_admin role.
    """
    result = await db.execute(
        select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    if request.name is not None:
        tenant.name = request.name
    if request.subscription_tier is not None:
        tenant.settings = request.subscription_tier
    if request.is_active is not None:
        tenant.is_active = request.is_active

    tenant.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(tenant)

    user_count = await get_tenant_user_count(db, tenant.id)
    return tenant_to_response(tenant, user_count)


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(require_platform_admin),
):
    """
    Soft-delete a tenant (mark as inactive).

    Requires platform_admin role.
    """
    result = await db.execute(
        select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    tenant.is_active = False
    tenant.updated_at = datetime.now(timezone.utc)
    await db.flush()

    return {"status": "deleted", "tenant_id": tenant_id}
