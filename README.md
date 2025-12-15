# product-core-api

Core business logic API for the Phase Zero SaaS platform.

## Purpose

Handles all domain-specific business operations. Implements multi-tenant data isolation using PostgreSQL Row Level Security (RLS).

## Multi-Tenancy

1. JWT middleware extracts `tenant_id` from token claims
2. Sets PostgreSQL session variable: `SET app.current_tenant = '<tenant_id>'`
3. RLS policies enforce row-level isolation automatically

## Stack

- Python 3.11
- FastAPI
- SQLAlchemy (async) + PostgreSQL with RLS
- Deployed on GCP Cloud Run

## Auth

- Validates JWTs against `identity-service` JWKS
- Supports Admin and Standard user roles via claims

## Structure

```
app/
├── api/           # Route handlers
├── core/          # Auth, config, middleware
├── db/            # SQLAlchemy models, RLS setup
├── models/        # Pydantic schemas
└── services/      # Business logic
```

## Response notes

  product-core-api structure:
  product-core-api/
  ├── app/
  │   ├── __init__.py
  │   └── core/
  │       ├── __init__.py
  │       ├── database.py    # Async SQLAlchemy + RLS
  │       └── middleware.py  # JWT auth + tenant extraction
  ├── .gitignore
  └── README.md

  database.py
  - ContextVar holds current_tenant_id per-request
  - get_db() — Sets app.current_tenant before yielding session, resets after
  - get_db_no_rls() — Admin bypass for cross-tenant queries
  - Public tenant fallback: 00000000-0000-0000-0000-000000000000

  middleware.py
  - get_current_user() — Extracts JWT, sets current_tenant_id context var
  - get_current_admin() — Requires role: admin
  - RoleChecker — Flexible role-based access
  - REQUIRE_AUTH env toggle (default: true)

  RLS Flow:
  Request → get_current_user() → decode JWT → set current_tenant_id ContextVar
                                                      ↓
                                            get_db() reads ContextVar
                                                      ↓
                                     SET app.current_tenant = '<tenant_id>'
                                                      ↓
                                          RLS policies enforce isolation

  Usage in routes:
  from fastapi import Depends
  from app.core.database import get_db, AsyncSession
  from app.core.middleware import get_current_user, require_admin, TokenData

  @router.get("/items")
  async def list_items(
      db: AsyncSession = Depends(get_db),
      user: TokenData = Depends(get_current_user),
  ):
      # RLS automatically filters by tenant_id
      ...

  @router.delete("/items/{id}")
  async def delete_item(
      id: int,
      db: AsyncSession = Depends(get_db),
      user: TokenData = Depends(require_admin),
  ):
      # Admin only
      ...