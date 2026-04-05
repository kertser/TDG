"""FastAPI dependencies: DB session, current user, participant lookup."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Header
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models.user import User
from backend.models.session import SessionParticipant

DB = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DB,
    authorization: str | None = Header(None),
) -> User:
    """Extract and validate JWT from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_session_participant(
    db: DB,
    session_id: uuid.UUID,
    current_user: CurrentUser,
) -> SessionParticipant:
    """Ensure the current user is a participant in the given session."""
    result = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.user_id == current_user.id,
        )
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status_code=403, detail="Not a participant in this session")
    return participant

