"""Plaid integration service for link tokens, token exchange, and transaction sync."""

import os
import logging
import hashlib
from datetime import datetime, timezone
from uuid import UUID
from dotenv import load_dotenv

import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from sqlalchemy import select, and_, or_
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.plaid import PlaidItem, Transaction
from app.core.encryption import encrypt_token, decrypt_token
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

load_dotenv()

# Plaid configuration
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET = os.getenv("PLAID_SECRET")
PLAID_ENV = os.getenv("PLAID_ENV", "sandbox")

# Environment mapping - Plaid API host URLs
PLAID_ENV_MAP = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


def get_plaid_client() -> plaid_api.PlaidApi:
    """Create and return a Plaid API client."""
    if not PLAID_CLIENT_ID or not PLAID_SECRET:
        raise ValueError(
            "PLAID_CLIENT_ID and PLAID_SECRET environment variables are required"
        )

    configuration = plaid.Configuration(
        host=PLAID_ENV_MAP.get(PLAID_ENV, "https://sandbox.plaid.com"),
        api_key={
            "clientId": PLAID_CLIENT_ID,
            "secret": PLAID_SECRET,
        },
    )

    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


def _hash_user_id(user_id: str) -> str:
    """Hash user_id to avoid sending PII (like email) to Plaid."""
    return hashlib.sha256(user_id.encode()).hexdigest()[:32]


async def create_link_token(user_id: str) -> str:
    """
    Create a Plaid Link token for initializing Link in the frontend.

    Args:
        user_id: The user's unique identifier

    Returns:
        The link_token string for Plaid Link initialization
    """
    client = get_plaid_client()

    # Hash the user_id to avoid sending PII to Plaid
    hashed_user_id = _hash_user_id(user_id)

    request = LinkTokenCreateRequest(
        products=[Products("auth"), Products("transactions")],
        client_name="Phase Zero",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id=hashed_user_id),
    )

    response = client.link_token_create(request)
    return response.link_token


async def exchange_public_token(
    db: AsyncSession,
    public_token: str,
    user_id: str,
    tenant_id: UUID,
    institution_id: str | None = None,
    institution_name: str | None = None,
) -> PlaidItem:
    """
    Exchange a public token for an access token and save the PlaidItem.

    Args:
        db: Database session
        public_token: The public token from Plaid Link
        user_id: The user's unique identifier
        tenant_id: The tenant UUID for RLS
        institution_id: Optional institution ID from Plaid
        institution_name: Optional institution name from Plaid

    Returns:
        The created PlaidItem
    """
    client = get_plaid_client()

    # Exchange public token for access token
    exchange_request = ItemPublicTokenExchangeRequest(public_token=public_token)
    exchange_response = client.item_public_token_exchange(exchange_request)

    access_token = exchange_response.access_token
    item_id = exchange_response.item_id

    # Encrypt the access token before storage
    encrypted_token = encrypt_token(access_token)

    # Create PlaidItem
    plaid_item = PlaidItem(
        tenant_id=tenant_id,
        user_id=user_id,
        item_id=item_id,
        encrypted_access_token=encrypted_token,
        institution_id=institution_id,
        institution_name=institution_name,
    )

    db.add(plaid_item)
    await db.flush()
    await db.refresh(plaid_item)

    logger.info(f"Created PlaidItem {plaid_item.id} for user {user_id}")
    return plaid_item


async def sync_transactions() -> dict:
    """
    Batch sync transactions for all active PlaidItems.

    This function iterates through all active PlaidItems, decrypts their
    access tokens, fetches new transactions from Plaid, and upserts them
    to the database.

    Returns:
        Summary dict with counts of items processed, transactions added/modified/removed
    """
    client = get_plaid_client()

    summary = {
        "items_processed": 0,
        "items_failed": 0,
        "transactions_added": 0,
        "transactions_modified": 0,
        "transactions_removed": 0,
    }

    # Use a session without RLS for batch processing
    async with async_session_factory() as db:
        # Get all active PlaidItems
        result = await db.execute(
            select(PlaidItem).where(PlaidItem.is_active == True)
        )
        plaid_items = result.scalars().all()

        for plaid_item in plaid_items:
            try:
                await _sync_item_transactions(db, client, plaid_item, summary)
                summary["items_processed"] += 1
            except Exception as e:
                logger.error(f"Failed to sync PlaidItem {plaid_item.id}: {e}")
                summary["items_failed"] += 1

        await db.commit()

    logger.info(f"Transaction sync completed: {summary}")
    return summary


async def _sync_item_transactions(
    db: AsyncSession,
    client: plaid_api.PlaidApi,
    plaid_item: PlaidItem,
    summary: dict,
) -> None:
    """
    Sync transactions for a single PlaidItem.

    Args:
        db: Database session
        client: Plaid API client
        plaid_item: The PlaidItem to sync
        summary: Summary dict to update with counts
    """
    # Decrypt access token
    access_token = decrypt_token(plaid_item.encrypted_access_token)

    # Use cursor for incremental sync (empty string for initial sync)
    cursor = plaid_item.transaction_cursor or ""
    has_more = True

    while has_more:
        request = TransactionsSyncRequest(
            access_token=access_token,
            cursor=cursor,
        )

        response = client.transactions_sync(request)

        # Process added transactions
        for txn in response.added:
            await _upsert_transaction(db, plaid_item, txn)
            summary["transactions_added"] += 1

        # Process modified transactions
        for txn in response.modified:
            await _upsert_transaction(db, plaid_item, txn)
            summary["transactions_modified"] += 1

        # Process removed transactions
        for removed in response.removed:
            await _remove_transaction(db, removed.transaction_id)
            summary["transactions_removed"] += 1

        cursor = response.next_cursor
        has_more = response.has_more

    # Update cursor and last_synced_at
    plaid_item.transaction_cursor = cursor
    plaid_item.last_synced_at = datetime.now(timezone.utc)
    await db.flush()


async def _upsert_transaction(
    db: AsyncSession,
    plaid_item: PlaidItem,
    txn,
) -> None:
    """
    Upsert a transaction from Plaid response.

    Args:
        db: Database session
        plaid_item: Parent PlaidItem (provides tenant_id)
        txn: Transaction object from Plaid API
    """
    # Extract category information
    category_primary = None
    category_detailed = None
    if hasattr(txn, "personal_finance_category") and txn.personal_finance_category:
        category_primary = txn.personal_finance_category.primary
        category_detailed = txn.personal_finance_category.detailed

    stmt = pg_insert(Transaction).values(
        tenant_id=plaid_item.tenant_id,
        plaid_item_id=plaid_item.id,
        plaid_transaction_id=txn.transaction_id,
        account_id=txn.account_id,
        amount=txn.amount,
        iso_currency_code=txn.iso_currency_code,
        name=txn.name,
        merchant_name=getattr(txn, "merchant_name", None),
        category_primary=category_primary,
        category_detailed=category_detailed,
        transaction_date=txn.date,
        authorized_date=getattr(txn, "authorized_date", None),
        pending=txn.pending,
        payment_channel=getattr(txn, "payment_channel", None),
    )

    # On conflict, update the transaction
    stmt = stmt.on_conflict_do_update(
        index_elements=["plaid_transaction_id"],
        set_={
            "amount": stmt.excluded.amount,
            "name": stmt.excluded.name,
            "merchant_name": stmt.excluded.merchant_name,
            "category_primary": stmt.excluded.category_primary,
            "category_detailed": stmt.excluded.category_detailed,
            "pending": stmt.excluded.pending,
            "payment_channel": stmt.excluded.payment_channel,
            "updated_at": datetime.now(timezone.utc),
        },
    )

    await db.execute(stmt)


async def _remove_transaction(db: AsyncSession, plaid_transaction_id: str) -> None:
    """
    Remove a transaction by its Plaid transaction ID.

    Args:
        db: Database session
        plaid_transaction_id: The Plaid transaction ID to remove
    """
    result = await db.execute(
        select(Transaction).where(
            Transaction.plaid_transaction_id == plaid_transaction_id
        )
    )
    transaction = result.scalar_one_or_none()

    if transaction:
        await db.delete(transaction)


async def sync_transactions_for_item(
    db: AsyncSession,
    item_id: UUID,
    tenant_id: UUID,
) -> dict:
    """
    Sync transactions for a single PlaidItem (manual sync triggered by user).

    Args:
        db: Database session with RLS context
        item_id: The PlaidItem UUID to sync
        tenant_id: The tenant UUID for RLS validation

    Returns:
        Summary dict with added/modified/removed counts and synced_at timestamp

    Raises:
        ValueError: If item not found or doesn't belong to tenant
    """
    client = get_plaid_client()

    # Query item with tenant_id check for RLS safety
    result = await db.execute(
        select(PlaidItem).where(
            and_(
                PlaidItem.id == item_id,
                PlaidItem.tenant_id == tenant_id,
                PlaidItem.is_active == True,
            )
        )
    )
    plaid_item = result.scalar_one_or_none()

    if not plaid_item:
        raise ValueError(f"PlaidItem {item_id} not found or not active")

    summary = {
        "added": 0,
        "modified": 0,
        "removed": 0,
    }

    # Reuse existing sync logic
    internal_summary = {
        "transactions_added": 0,
        "transactions_modified": 0,
        "transactions_removed": 0,
    }

    await _sync_item_transactions(db, client, plaid_item, internal_summary)

    summary["added"] = internal_summary["transactions_added"]
    summary["modified"] = internal_summary["transactions_modified"]
    summary["removed"] = internal_summary["transactions_removed"]
    summary["synced_at"] = datetime.now(timezone.utc).isoformat()

    logger.info(f"Manual sync completed for PlaidItem {item_id}: {summary}")
    return summary


async def sync_due_items() -> dict:
    """
    Sync transactions for all items that are due for sync based on sync_frequency_hours.

    Items are due if:
    - last_synced_at is NULL (never synced), OR
    - (now - last_synced_at) > sync_frequency_hours

    Returns:
        Summary dict with counts of items processed/failed and transactions synced
    """
    client = get_plaid_client()

    summary = {
        "items_processed": 0,
        "items_skipped": 0,
        "items_failed": 0,
        "transactions_added": 0,
        "transactions_modified": 0,
        "transactions_removed": 0,
    }

    async with async_session_factory() as db:
        # Get items that are due for sync
        now = datetime.now(timezone.utc)

        result = await db.execute(
            select(PlaidItem).where(
                and_(
                    PlaidItem.is_active == True,
                    or_(
                        PlaidItem.last_synced_at.is_(None),
                        # Check if enough hours have passed since last sync
                        func.extract("epoch", now - PlaidItem.last_synced_at) / 3600
                        > PlaidItem.sync_frequency_hours,
                    ),
                )
            )
        )
        due_items = result.scalars().all()

        logger.info(f"Found {len(due_items)} items due for sync")

        for plaid_item in due_items:
            try:
                await _sync_item_transactions(db, client, plaid_item, summary)
                summary["items_processed"] += 1
            except Exception as e:
                logger.error(f"Failed to sync PlaidItem {plaid_item.id}: {e}")
                summary["items_failed"] += 1
                # Continue with other items

        await db.commit()

    logger.info(f"Scheduled sync completed: {summary}")
    return summary


async def get_plaid_items_for_user(
    db: AsyncSession,
    tenant_id: UUID,
) -> list[PlaidItem]:
    """
    Get all active PlaidItems for a tenant.

    Args:
        db: Database session
        tenant_id: The tenant UUID

    Returns:
        List of PlaidItem objects
    """
    result = await db.execute(
        select(PlaidItem).where(
            and_(
                PlaidItem.tenant_id == tenant_id,
                PlaidItem.is_active == True,
            )
        ).order_by(PlaidItem.created_at.desc())
    )
    return list(result.scalars().all())
