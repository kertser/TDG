"""Auth endpoints – register and login (simplified for MVP)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from jose import jwt
from pydantic import BaseModel
from sqlalchemy import select

from backend.api.deps import DB
from backend.config import settings
from backend.models.user import User

router = APIRouter()


class RegisterRequest(BaseModel):
    display_name: str


class LoginRequest(BaseModel):
    display_name: str


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
        raise HTTPException(status_code=400, detail="display_name required")

    user = User(display_name=body.display_name.strip())
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
        raise HTTPException(status_code=404, detail="User not found. Register first.")
    token = _create_token(str(user.id))
    return TokenResponse(user_id=str(user.id), display_name=user.display_name, token=token)

