"""Auth endpoints – register and login with callsign + password."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, HTTPException
from jose import jwt
from pydantic import BaseModel
from sqlalchemy import select

from backend.api.deps import DB
from backend.config import settings
from backend.models.user import User

router = APIRouter()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


class RegisterRequest(BaseModel):
    display_name: str
    password: str


class LoginRequest(BaseModel):
    display_name: str
    password: str


class TokenResponse(BaseModel):
    user_id: str
    display_name: str
    token: str


def _create_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "exp": expire},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, db: DB):
    if not body.display_name.strip():
        raise HTTPException(status_code=400, detail="Callsign required")
    if not body.password or len(body.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    # Check if callsign already taken
    existing = await db.execute(
        select(User).where(User.display_name == body.display_name.strip())
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Callsign already taken")

    user = User(
        display_name=body.display_name.strip(),
        password_hash=_hash_password(body.password),
    )
    db.add(user)
    await db.flush()
    token = _create_token(str(user.id))
    return TokenResponse(user_id=str(user.id), display_name=user.display_name, token=token)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DB):
    result = await db.execute(
        select(User).where(User.display_name == body.display_name.strip())
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Legacy users without password_hash can set one on first login
    if user.password_hash is None:
        if not body.password or len(body.password) < 4:
            raise HTTPException(status_code=400, detail="Set a password (at least 4 characters) to secure your account")
        user.password_hash = _hash_password(body.password)
        await db.flush()
    else:
        # Verify password
        if not body.password or not _verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid password")

    token = _create_token(str(user.id))
    return TokenResponse(user_id=str(user.id), display_name=user.display_name, token=token)

