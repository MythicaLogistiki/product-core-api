"""Transaction API endpoints."""

from datetime import date
from decimal import Decimal
from math import ceil
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.middleware import get_current_user, TokenData
from app.models.plaid import Transaction

router = APIRouter(tags=["transactions"])


class TransactionResponse(BaseModel):
    """Single transaction response."""
    id: UUID
    plaid_item_id: UUID
    amount: Decimal
    iso_currency_code: str | None
    name: str
    merchant_name: str | None
    category_primary: str | None
    category_detailed: str | None
    transaction_date: date
    pending: bool

    class Config:
        from_attributes = True


class PaginatedTransactions(BaseModel):
    """Paginated list of transactions."""
    items: list[TransactionResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get("/transactions", response_model=PaginatedTransactions)
async def get_transactions(
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(get_current_user),
    start_date: date | None = Query(None, description="Filter transactions from this date"),
    end_date: date | None = Query(None, description="Filter transactions until this date"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> PaginatedTransactions:
    """
    Get paginated transactions for the current user's tenant.

    Transactions are filtered by the RLS tenant context and ordered by date descending.
    Optional date range filtering available via start_date and end_date query params.
    """
    tenant_id = UUID(user.tenant_id)

    # Build base query with tenant filter (RLS provides additional safety)
    conditions = [Transaction.tenant_id == tenant_id]

    if start_date:
        conditions.append(Transaction.transaction_date >= start_date)
    if end_date:
        conditions.append(Transaction.transaction_date <= end_date)

    where_clause = and_(*conditions)

    # Get total count
    count_result = await db.execute(
        select(func.count(Transaction.id)).where(where_clause)
    )
    total = count_result.scalar_one()

    # Get paginated transactions
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Transaction)
        .where(where_clause)
        .order_by(Transaction.transaction_date.desc(), Transaction.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    transactions = result.scalars().all()

    total_pages = ceil(total / page_size) if total > 0 else 1

    return PaginatedTransactions(
        items=[TransactionResponse.model_validate(t) for t in transactions],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
