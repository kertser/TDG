"""Orders API endpoints – submit, list, get, cancel orders."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.models.order import Order, OrderStatus

router = APIRouter()


class OrderSubmit(BaseModel):
    target_unit_ids: list[str] | None = None
    original_text: str = ""
    order_type: str | None = None
    parsed_order: dict | None = None  # Structured order for direct task assignment


@router.post("/{session_id}/orders")
async def submit_order(
    session_id: uuid.UUID,
    body: OrderSubmit,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Submit an order for parsing and execution.

    Can include a structured parsed_order for direct task assignment:
      {"type": "move", "target_location": {"lat": 48.85, "lon": 2.35}}
    """
    # Observers cannot submit orders (check both side and role)
    if participant.side.value == "observer" or participant.role == "observer":
        raise HTTPException(status_code=403, detail="Observers cannot submit orders")

    # Determine side for the order (admin defaults to blue)
    side_val = participant.side.value
    if side_val not in ("blue", "red"):
        side_val = "blue"

    # Validate target units belong to the issuing side
    if body.target_unit_ids and side_val in ("blue", "red"):
        from backend.models.unit import Unit
        for uid_str in body.target_unit_ids:
            try:
                uid = uuid.UUID(uid_str)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid unit ID: {uid_str}")
            result = await db.execute(
                select(Unit.side).where(Unit.id == uid, Unit.session_id == session_id)
            )
            row = result.first()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Target unit {uid_str} not found")
            unit_side = row[0].value if hasattr(row[0], 'value') else row[0]
            if unit_side != side_val:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot issue orders to units on another side"
                )

    order = Order(
        session_id=session_id,
        issued_by_user_id=participant.user_id,
        issued_by_side=side_val,
        target_unit_ids=[uuid.UUID(uid) for uid in body.target_unit_ids] if body.target_unit_ids else None,
        order_type=body.order_type,
        original_text=body.original_text or "",
        parsed_order=body.parsed_order,
        status=OrderStatus.pending,
    )
    db.add(order)
    await db.flush()
    return {
        "id": str(order.id),
        "status": order.status.value,
        "original_text": order.original_text,
        "issued_at": order.issued_at.isoformat() if order.issued_at else None,
        "issued_by_side": side_val,
        "target_unit_ids": [str(uid) for uid in order.target_unit_ids] if order.target_unit_ids else [],
    }


@router.get("/{session_id}/orders")
async def list_orders(
    session_id: uuid.UUID,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """List orders for a session, optionally filtered by status."""
    query = select(Order).where(Order.session_id == session_id)
    if status:
        query = query.where(Order.status == status)
    result = await db.execute(query)
    orders = result.scalars().all()

    # Batch-load issuer names
    from backend.models.user import User
    issuer_ids = {o.issued_by_user_id for o in orders if o.issued_by_user_id}
    issuer_names = {}
    if issuer_ids:
        user_result = await db.execute(
            select(User.id, User.display_name).where(User.id.in_(issuer_ids))
        )
        for uid, name in user_result.all():
            issuer_names[uid] = name

    return [
        {
            "id": str(o.id),
            "order_type": o.order_type,
            "status": o.status.value,
            "original_text": o.original_text,
            "issued_at": o.issued_at.isoformat() if o.issued_at else None,
            "issued_by_side": o.issued_by_side.value if hasattr(o.issued_by_side, 'value') else o.issued_by_side,
            "issuer_name": issuer_names.get(o.issued_by_user_id, "AI") if o.issued_by_user_id else "AI",
            "target_unit_ids": [str(uid) for uid in o.target_unit_ids] if o.target_unit_ids else [],
        }
        for o in orders
    ]


@router.get("/{session_id}/orders/{order_id}")
async def get_order(
    session_id: uuid.UUID,
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.session_id == session_id)
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "id": str(order.id),
        "order_type": order.order_type,
        "status": order.status.value,
        "original_text": order.original_text,
        "parsed_order": order.parsed_order,
        "parsed_intent": order.parsed_intent,
        "issued_at": order.issued_at.isoformat() if order.issued_at else None,
    }


@router.post("/{session_id}/orders/{order_id}/cancel")
async def cancel_order(
    session_id: uuid.UUID,
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.session_id == session_id)
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = OrderStatus.cancelled
    await db.flush()
    return {"id": str(order.id), "status": order.status.value}


@router.get("/{session_id}/chat")
async def list_chat_messages(
    session_id: uuid.UUID,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Load chat message history for a session."""
    from backend.models.chat_message import ChatMessage

    query = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
    )
    result = await db.execute(query)
    messages = result.scalars().all()

    my_user_id = participant.user_id

    return [
        {
            "sender_id": str(m.sender_id),
            "sender_name": m.sender_name,
            "text": m.text,
            "recipient": m.recipient,
            "side": m.side,
            "timestamp": m.created_at.isoformat() if m.created_at else None,
            "own": m.sender_id == my_user_id,
        }
        for m in messages
        # Include if broadcast (all) or if I'm sender or recipient
        if m.recipient == "all" or m.sender_id == my_user_id or m.recipient == str(my_user_id)
    ]


