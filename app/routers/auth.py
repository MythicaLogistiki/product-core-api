"""Authentication endpoints for token generation."""

import os
from datetime import datetime, timezone, timedelta
from enum import Enum

from fastapi import APIRouter, HTTPException, status, Form
from pydantic import BaseModel
from jose import jwt

router = APIRouter(tags=["auth"])

# Config
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "60"))
ISSUER = os.getenv("ISSUER", "http://localhost:8000")


class Role(str, Enum):
    ADMIN = "admin"
    STANDARD = "standard"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# Mock user store (replace with DB in production)
MOCK_USERS = {
    "admin@example.com": {
        "password": "admin123",
        "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
        "role": Role.ADMIN,
    },
    "user@example.com": {
        "password": "user123",
        "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
        "role": Role.STANDARD,
    },
}


@router.post("/token", response_model=TokenResponse)
async def token(
    username: str = Form(...),
    password: str = Form(...),
):
    """
    OAuth2 token endpoint.
    Accepts username/password, returns JWT with tenant_id and role.
    """
    user = MOCK_USERS.get(username)
    if not user or user["password"] != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": username,
        "tenant_id": user["tenant_id"],
        "role": user["role"].value,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": ISSUER,
    }

    access_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    return TokenResponse(
        access_token=access_token,
        expires_in=TOKEN_EXPIRE_MINUTES * 60,
    )
