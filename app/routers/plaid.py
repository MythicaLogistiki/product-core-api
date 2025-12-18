"""Plaid API endpoints for Link flow and transaction sync."""

import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.middleware import get_current_user, TokenData
from app.services import plaid_service
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(tags=["plaid"])

# API key for Cloud Scheduler job authentication
SYNC_API_KEY = os.getenv("SYNC_API_KEY")


class LinkTokenResponse(BaseModel):
    """Response containing Plaid Link token."""
    link_token: str


class ConnectRequest(BaseModel):
    """Request to exchange Plaid public token."""
    public_token: str
    institution_id: str | None = None
    institution_name: str | None = None


class ConnectResponse(BaseModel):
    """Response after successful Plaid connection."""
    item_id: UUID
    institution_name: str | None


class SyncResponse(BaseModel):
    """Response from transaction sync job."""
    items_processed: int
    items_failed: int
    transactions_added: int
    transactions_modified: int
    transactions_removed: int


class ItemSyncResponse(BaseModel):
    """Response from single item sync."""
    added: int
    modified: int
    removed: int
    synced_at: str


class PlaidItemResponse(BaseModel):
    """Plaid item details for frontend."""
    id: UUID
    institution_id: str | None
    institution_name: str | None
    is_active: bool
    last_synced_at: str | None
    sync_frequency_hours: int

    class Config:
        from_attributes = True


@router.post("/plaid/link-token", response_model=LinkTokenResponse)
async def create_link_token(
    user: TokenData = Depends(get_current_user),
) -> LinkTokenResponse:
    """
    Create a Plaid Link token for the authenticated user.

    This token is used to initialize Plaid Link in the frontend.
    """
    try:
        link_token = await plaid_service.create_link_token(user.sub)
        return LinkTokenResponse(link_token=link_token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create link token: {str(e)}",
        )


@router.post("/plaid/connect", response_model=ConnectResponse)
async def connect_plaid_account(
    request: ConnectRequest,
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(get_current_user),
) -> ConnectResponse:
    """
    Exchange a Plaid public token for an access token.

    Called after user completes Plaid Link flow successfully.
    Stores encrypted access token in database.
    """
    try:
        plaid_item = await plaid_service.exchange_public_token(
            db=db,
            public_token=request.public_token,
            user_id=user.sub,
            tenant_id=UUID(user.tenant_id),
            institution_id=request.institution_id,
            institution_name=request.institution_name,
        )
        return ConnectResponse(
            item_id=plaid_item.id,
            institution_name=plaid_item.institution_name,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect account: {str(e)}",
        )


@router.post("/jobs/process-sync-queue", response_model=SyncResponse)
async def process_sync_queue(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> SyncResponse:
    """
    Sync transactions for all items that are due based on sync_frequency_hours.

    This endpoint is intended to be called by Cloud Scheduler.
    Requires X-API-Key header matching SYNC_API_KEY environment variable.
    Only syncs items where (now - last_synced_at) > sync_frequency_hours.
    """
    # Validate API key
    if not SYNC_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SYNC_API_KEY not configured",
        )

    if x_api_key != SYNC_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    try:
        summary = await plaid_service.sync_due_items()
        return SyncResponse(**summary)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transaction sync failed: {str(e)}",
        )


@router.post("/plaid/sync/{item_id}", response_model=ItemSyncResponse)
async def sync_plaid_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(get_current_user),
) -> ItemSyncResponse:
    """
    Manually sync transactions for a specific Plaid item.

    Triggered by user clicking "Sync Now" in the frontend.
    Only syncs the specified item if it belongs to the user's tenant.
    """
    try:
        summary = await plaid_service.sync_transactions_for_item(
            db=db,
            item_id=item_id,
            tenant_id=UUID(user.tenant_id),
        )
        return ItemSyncResponse(**summary)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to sync item: {str(e)}",
        )


@router.get("/plaid/items", response_model=list[PlaidItemResponse])
async def get_plaid_items(
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(get_current_user),
) -> list[PlaidItemResponse]:
    """
    Get all connected Plaid items for the current user's tenant.

    Returns list of connected bank accounts with sync status.
    """
    items = await plaid_service.get_plaid_items_for_user(
        db=db,
        tenant_id=UUID(user.tenant_id),
    )

    return [
        PlaidItemResponse(
            id=item.id,
            institution_id=item.institution_id,
            institution_name=item.institution_name,
            is_active=item.is_active,
            last_synced_at=item.last_synced_at.isoformat() if item.last_synced_at else None,
            sync_frequency_hours=item.sync_frequency_hours,
        )
        for item in items
    ]
