"""Database configuration with RLS tenant isolation."""

import os
from contextvars import ContextVar
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

# Context variable to hold current tenant_id
current_tenant_id: ContextVar[str | None] = ContextVar("current_tenant_id", default=None)

# Public tenant fallback
PUBLIC_TENANT = "00000000-0000-0000-0000-000000000000"

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/phase_zero"
)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("DB_ECHO", "false").lower() == "true",
    pool_size=5,
    max_overflow=10,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency that provides a database session with RLS tenant context.

    Sets `app.current_tenant` PostgreSQL session variable for RLS policies.
    """
    async with async_session_factory() as session:
        tenant_id = current_tenant_id.get() or PUBLIC_TENANT

        # Set tenant context for RLS
        await session.execute(
            text("SET app.current_tenant = :tenant_id"),
            {"tenant_id": tenant_id},
        )

        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            # Reset tenant context
            await session.execute(text("RESET app.current_tenant"))


async def get_db_no_rls() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for admin operations that bypass RLS.
    Use sparingly - only for cross-tenant admin queries.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
