"""Authentication middleware with tenant extraction and role-based access."""

import os
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from app.core.database import current_tenant_id, PUBLIC_TENANT
from dotenv import load_dotenv

load_dotenv()

# Config
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").lower() == "true"

security = HTTPBearer(auto_error=False)


class PlatformRole(str, Enum):
    """Platform-level roles for Numbersence users."""
    # Standard tenant users
    STANDARD = "standard"
    ADMIN = "admin"

    # Platform staff (Numbersence internal)
    PLATFORM_ADMIN = "platform_admin"  # Create orgs, manage billing
    SUPPORT_AGENT = "support_agent"    # View-only support access


@dataclass
class TokenData:
    """Decoded JWT payload."""
    sub: str
    tenant_id: str
    role: str

    @property
    def is_platform_staff(self) -> bool:
        """Check if user is Numbersence internal staff."""
        return self.role in [PlatformRole.PLATFORM_ADMIN.value, PlatformRole.SUPPORT_AGENT.value]

    @property
    def is_platform_admin(self) -> bool:
        """Check if user is platform admin."""
        return self.role == PlatformRole.PLATFORM_ADMIN.value

    @property
    def is_support_agent(self) -> bool:
        """Check if user is support agent."""
        return self.role == PlatformRole.SUPPORT_AGENT.value


def decode_token(token: str) -> TokenData:
    """Decode and validate JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return TokenData(
            sub=payload.get("sub", ""),
            tenant_id=payload.get("tenant_id", PUBLIC_TENANT),
            role=payload.get("role", "standard"),
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> TokenData:
    """
    Dependency to extract and validate JWT from Authorization header.
    Sets tenant context for RLS.
    """
    if credentials is None:
        if REQUIRE_AUTH:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Public access - set public tenant
        current_tenant_id.set(PUBLIC_TENANT)
        return TokenData(sub="anonymous", tenant_id=PUBLIC_TENANT, role="standard")

    token_data = decode_token(credentials.credentials)

    # Set tenant context for RLS
    current_tenant_id.set(token_data.tenant_id)

    return token_data


async def get_current_admin(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Dependency that requires admin role."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


class RoleChecker:
    """Dependency class for flexible role checking."""

    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: TokenData = Depends(get_current_user)) -> TokenData:
        if user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' not authorized. Required: {self.allowed_roles}",
            )
        return user


# Pre-configured role checkers
require_admin = RoleChecker(["admin"])
require_standard = RoleChecker(["admin", "standard"])

# Platform staff role checkers
require_platform_admin = RoleChecker(["platform_admin"])
require_support_agent = RoleChecker(["platform_admin", "support_agent"])
require_platform_staff = RoleChecker(["platform_admin", "support_agent"])
