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
  

# Get the encryption
```
python -c "from cryptography.fernet import Fernet; 
print(Fernet.generate_key().decode())"
```

# Local Run of the entire solution:
  # Kill existing processes
  lsof -ti:8000 | xargs kill -9 2>/dev/null
  lsof -ti:3000 | xargs kill -9 2>/dev/null

  # Start backend (from product-core-api directory)
  cd /Users/leoceka/repos/phase-zero/product-core-api
  source .venv/bin/activate
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &

  # Start frontend (from product-frontend-web directory)
  cd /Users/leoceka/repos/phase-zero/product-frontend-web
  npm run dev &

  The --reload flag on uvicorn enables hot-reloading so it picks up code changes
  automatically.
  
  Commands to Run the Entire Solution

  # Kill any existing processes on the ports
  lsof -ti:8000 | xargs kill -9 2>/dev/null
  lsof -ti:8001 | xargs kill -9 2>/dev/null
  lsof -ti:3000 | xargs kill -9 2>/dev/null

  # Start identity-service (Auth) on port 8001
  cd /Users/leoceka/repos/phase-zero/identity-service
  source .venv/bin/activate
  uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload &

  # Start product-core-api (API) on port 8000
  cd /Users/leoceka/repos/phase-zero/product-core-api
  source .venv/bin/activate
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &

  # Start product-frontend-web (Frontend) on port 3000
  cd /Users/leoceka/repos/phase-zero/product-frontend-web
  npm run dev &

  ---
  Architecture

  ┌─────────────────┐     /token      ┌──────────────────┐
  │    Frontend     │ ───────────────▶│ identity-service │
  │   (port 3000)   │                 │   (port 8001)    │
  │                 │     /api/v1/*   ├──────────────────┤
  │                 │ ───────────────▶│ product-core-api │
  └─────────────────┘                 │   (port 8000)    │
                                      └──────────────────┘
# multitenant support

⏺ All services are running. Here's the summary of changes:

  Identity Service Updates:

  1. Added new roles in models.py:
    - PLATFORM_ADMIN - internal Numbersence staff for admin console
    - SUPPORT_AGENT - customer support staff
  2. Added test users in main.py:
    - platform@numbersence.com / platform123 → platform_admin role
    - support@numbersence.com / support123 → support_agent role

  Services running:
  - Backend API: http://localhost:8000
  - Identity Service: http://localhost:8001
  - Frontend: http://localhost:3000

  To test the admin console:
  1. Go to http://localhost:3000/login
  2. Login with platform@numbersence.com / platform123
  3. Navigate to http://localhost:3000/admin/orgs

  Support Agent Experience

  Frontend (/support):
  - Dashboard (/support) - Overview with quick actions, stats, and guidelines
  - Customer Lookup (/support/lookup) - Search organizations and start impersonation sessions
  - Active Sessions (/support/sessions) - View and manage your impersonation sessions
  - Cyan/teal color theme to distinguish from admin console

  Backend (/api/v1/support/*):
  - POST /support/impersonate/{tenant_id} - Start impersonation session (creates audit log)
  - DELETE /support/impersonate/{session_id} - End session
  - GET /support/sessions - Get active sessions
  - GET /support/audit-log - View access audit log

  Login Redirects:
  - platform_admin → /admin/orgs
  - support_agent → /support
  - Others → /dashboard

  Test Credentials:
  - Platform Admin: platform@numbersence.com / platform123
  - Support Agent: support@numbersence.com / support123