"""Support API endpoints for Customer Support Console."""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.middleware import get_current_user, TokenData, require_support_agent
from app.models.tenant import Tenant, SupportAccessLog

router = APIRouter(prefix="/support", tags=["support"])


# ============== Request/Response Models ==============

class ImpersonationSessionResponse(BaseModel):
    """Response after starting impersonation."""
    session_id: str
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    expires_at: str


class SessionInfo(BaseModel):
    """Active session information."""
    session_id: str
    tenant_id: str
    tenant_name: str
    tenant_slug: str
    started_at: str
    expires_at: str


class ActiveSessionsResponse(BaseModel):
    """List of active impersonation sessions."""
    sessions: list[SessionInfo]


# ============== In-Memory Session Store ==============
# In production, use Redis or database for persistence

_active_sessions: dict[str, dict] = {}


# ============== Helper Functions ==============

def cleanup_expired_sessions():
    """Remove expired sessions from memory."""
    now = datetime.now(timezone.utc)
    expired = [
        session_id for session_id, session in _active_sessions.items()
        if datetime.fromisoformat(session["expires_at"].replace("Z", "+00:00")) < now
    ]
    for session_id in expired:
        del _active_sessions[session_id]


# ============== Endpoints ==============

@router.post("/impersonate/{tenant_id}", response_model=ImpersonationSessionResponse)
async def start_impersonation(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(require_support_agent),
) -> ImpersonationSessionResponse:
    """
    Start an impersonation session for a tenant.

    Requires support_agent or platform_admin role.
    Creates an audit log entry.
    """
    # Clean up expired sessions first
    cleanup_expired_sessions()

    # Validate tenant exists and is accessible
    result = await db.execute(
        select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    if not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot access inactive tenant",
        )

    # Generate session
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=1)  # 1 hour session

    # Store session
    _active_sessions[session_id] = {
        "user_id": user.sub,
        "user_role": user.role,
        "tenant_id": str(tenant.id),
        "tenant_slug": tenant.slug,
        "tenant_name": tenant.name,
        "started_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }

    # Create audit log entry
    audit_log = SupportAccessLog(
        support_user_id=user.sub,
        tenant_id=tenant.id,
        action="impersonation_started",
        reason=f"Session {session_id} started",
    )
    db.add(audit_log)
    await db.flush()

    return ImpersonationSessionResponse(
        session_id=session_id,
        tenant_id=str(tenant.id),
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        expires_at=expires_at.isoformat(),
    )


@router.delete("/impersonate/{session_id}")
async def end_impersonation(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(require_support_agent),
):
    """
    End an impersonation session.

    Requires support_agent or platform_admin role.
    """
    session = _active_sessions.get(session_id)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or already expired",
        )

    # Verify the user owns this session
    if session["user_id"] != user.sub and user.role != "platform_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot end another user's session",
        )

    # Create audit log entry
    audit_log = SupportAccessLog(
        support_user_id=user.sub,
        tenant_id=uuid.UUID(session["tenant_id"]),
        action="impersonation_ended",
        reason=f"Session {session_id} ended",
    )
    db.add(audit_log)
    await db.flush()

    # Remove session
    del _active_sessions[session_id]

    return {"status": "ended", "session_id": session_id}


@router.get("/sessions", response_model=ActiveSessionsResponse)
async def get_active_sessions(
    user: TokenData = Depends(require_support_agent),
) -> ActiveSessionsResponse:
    """
    Get all active impersonation sessions for the current user.

    Platform admins can see all sessions.
    Support agents can only see their own sessions.
    """
    cleanup_expired_sessions()

    sessions = []
    for session_id, session in _active_sessions.items():
        # Filter by user unless platform admin
        if user.role != "platform_admin" and session["user_id"] != user.sub:
            continue

        sessions.append(SessionInfo(
            session_id=session_id,
            tenant_id=session["tenant_id"],
            tenant_name=session["tenant_name"],
            tenant_slug=session["tenant_slug"],
            started_at=session["started_at"],
            expires_at=session["expires_at"],
        ))

    return ActiveSessionsResponse(sessions=sessions)


@router.get("/audit-log")
async def get_audit_log(
    db: AsyncSession = Depends(get_db),
    user: TokenData = Depends(require_support_agent),
    tenant_id: Optional[str] = None,
    limit: int = 50,
):
    """
    Get support access audit log.

    Platform admins can see all logs.
    Support agents can only see their own logs.
    """
    query = select(SupportAccessLog).order_by(SupportAccessLog.created_at.desc())

    # Filter by user for support agents
    if user.role != "platform_admin":
        query = query.where(SupportAccessLog.support_user_id == user.sub)

    # Filter by tenant if specified
    if tenant_id:
        query = query.where(SupportAccessLog.tenant_id == uuid.UUID(tenant_id))

    query = query.limit(limit)

    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "logs": [
            {
                "id": str(log.id),
                "support_user_id": log.support_user_id,
                "tenant_id": str(log.tenant_id),
                "action": log.action,
                "reason": log.reason,
                "ip_address": log.ip_address,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ]
    }
