"""Orders API endpoints – submit, list, get, cancel orders."""

from __future__ import annotations

import asyncio
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db, async_session_factory
from backend.api.deps import get_session_participant
from backend.models.order import Order, OrderStatus

router = APIRouter()
logger = logging.getLogger(__name__)


class OrderSubmit(BaseModel):
    target_unit_ids: list[str] | None = None
    original_text: str = ""
    order_type: str | None = None
    parsed_order: dict | None = None  # Structured order for direct task assignment


async def _run_order_pipeline(
    order_id: uuid.UUID,
    session_id: uuid.UUID,
    issuer_side: str,
):
    """Background task: run LLM order pipeline and broadcast results."""
    from backend.services.order_service import order_service
    from backend.services.ws_manager import ws_manager
    from backend.models.chat_message import ChatMessage
    from datetime import datetime, timezone

    logger.info("Order pipeline STARTING for order=%s", order_id)

    try:
        async with async_session_factory() as db:
            try:
                result = await db.execute(
                    select(Order).where(Order.id == order_id)
                )
                order = result.scalar_one_or_none()
                if order is None:
                    logger.error("Order pipeline: order %s not found", order_id)
                    return

                # Run the full pipeline
                parse_result = await order_service.process(
                    order=order,
                    session_id=session_id,
                    db=db,
                    issuer_side=issuer_side,
                )

                await db.flush()

                logger.info(
                    "Order pipeline: classification=%s status=%s",
                    parse_result.parsed.classification.value,
                    order.status.value,
                )

                # Build order_status broadcast payload
                order_data = {
                    "id": str(order.id),
                    "status": order.status.value,
                    "original_text": order.original_text,
                    "order_type": order.order_type or (
                        parse_result.parsed.order_type.value
                        if parse_result.parsed.order_type else None
                    ),
                    "parsed_order": order.parsed_order,
                    "parsed_intent": order.parsed_intent,
                    "classification": parse_result.parsed.classification.value,
                    "language": parse_result.parsed.language.value,
                    "confidence": parse_result.parsed.confidence,
                    "matched_unit_ids": parse_result.matched_unit_ids,
                    "resolved_locations": [
                        loc.model_dump(mode="json", exclude_none=True)
                        for loc in parse_result.resolved_locations
                    ],
                }

                # Broadcast order status update
                await ws_manager.broadcast(
                    session_id,
                    {"type": "order_status", "data": order_data},
                    only_side=issuer_side,
                )

                # ── Broadcast outgoing order as Radio chat message ──
                # Resolve issuer name and target unit names
                from backend.models.user import User
                from backend.models.unit import Unit

                issuer_name = "Commander"
                if order.issued_by_user_id:
                    usr_result = await db.execute(
                        select(User.display_name).where(User.id == order.issued_by_user_id)
                    )
                    usr_row = usr_result.scalar_one_or_none()
                    if usr_row:
                        issuer_name = usr_row

                target_names = []
                if order.target_unit_ids:
                    for uid in order.target_unit_ids:
                        u_result = await db.execute(
                            select(Unit.name).where(Unit.id == uid)
                        )
                        u_name = u_result.scalar_one_or_none()
                        if u_name:
                            target_names.append(u_name)

                to_str = ", ".join(target_names) if target_names else "all units"
                order_text = order.original_text or order.order_type or "—"
                outgoing_text = f"📋 {issuer_name} → {to_str}: {order_text}"

                now_order = datetime.now(timezone.utc)
                try:
                    order_chat = ChatMessage(
                        session_id=session_id,
                        sender_id=order.issued_by_user_id,
                        sender_name=f"📋 {issuer_name}",
                        side=issuer_side,
                        recipient="all",
                        text=outgoing_text,
                        created_at=now_order,
                    )
                    db.add(order_chat)
                except Exception:
                    pass

                order_chat_data = {
                    "sender_id": str(order.issued_by_user_id) if order.issued_by_user_id else "",
                    "sender_name": f"📋 {issuer_name}",
                    "text": outgoing_text,
                    "recipient": "all",
                    "side": issuer_side,
                    "timestamp": now_order.isoformat(),
                    "is_order": True,
                    "is_unit_response": False,
                }
                await ws_manager.broadcast(
                    session_id,
                    {"type": "chat_message", "data": order_chat_data},
                    only_side=issuer_side,
                )

                # Send unit radio responses as chat messages
                for resp in parse_result.responses:
                    now = datetime.now(timezone.utc)

                    # Persist the unit response as a chat message
                    try:
                        chat_msg = ChatMessage(
                            session_id=session_id,
                            sender_id=None,  # Unit, not a user
                            sender_name=f"📻 {resp.from_unit_name}",
                            side=issuer_side,
                            recipient="all",
                            text=resp.text,
                            created_at=now,
                        )
                        db.add(chat_msg)
                    except Exception:
                        pass  # Chat persistence is best-effort

                    # Broadcast the unit response
                    chat_data = {
                        "sender_id": resp.from_unit_id or "",
                        "sender_name": f"📻 {resp.from_unit_name}",
                        "text": resp.text,
                        "recipient": "all",
                        "side": issuer_side,
                        "timestamp": now.isoformat(),
                        "is_unit_response": True,
                        "response_type": resp.response_type.value,
                    }
                    await ws_manager.broadcast(
                        session_id,
                        {"type": "chat_message", "data": chat_data},
                        only_side=issuer_side,
                    )

                await db.commit()
                logger.info(
                    "Order pipeline completed: order=%s status=%s classification=%s",
                    order_id, order.status.value,
                    parse_result.parsed.classification.value,
                )

            except Exception as e:
                await db.rollback()
                logger.error("Order pipeline failed for order=%s: %s", order_id, e, exc_info=True)
                # Try to mark order as failed
                try:
                    async with async_session_factory() as db2:
                        result = await db2.execute(
                            select(Order).where(Order.id == order_id)
                        )
                        order = result.scalar_one_or_none()
                        if order and order.status == OrderStatus.pending:
                            order.status = OrderStatus.failed
                            order.parsed_order = {"error": str(e)}
                            await db2.commit()
                except Exception:
                    pass

    except Exception as e:
        logger.error("Order pipeline CRASHED for order=%s: %s", order_id, e, exc_info=True)


@router.post("/{session_id}/orders")
async def submit_order(
    session_id: uuid.UUID,
    body: OrderSubmit,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Submit an order for parsing and execution.

    Two modes:
    1. If parsed_order is provided → direct task assignment (fast path, no LLM).
    2. If only original_text → runs LLM pipeline in background (OrderParser → IntentInterpreter → LocationResolver).
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

    order_id = order.id
    has_direct_task = body.parsed_order and body.parsed_order.get("type")
    has_text = bool(body.original_text and body.original_text.strip())

    # If no direct parsed_order but has text → run LLM pipeline in background
    if not has_direct_task and has_text:
        order.status = OrderStatus.pending  # Will be updated by pipeline
        # Commit NOW so the background task can find this order in DB
        await db.commit()
        asyncio.create_task(_run_order_pipeline(order_id, session_id, side_val))
        status_note = "processing"
    else:
        status_note = None

    return {
        "id": str(order.id),
        "status": order.status.value,
        "original_text": order.original_text,
        "issued_at": order.issued_at.isoformat() if order.issued_at else None,
        "issued_by_side": side_val,
        "target_unit_ids": [str(uid) for uid in order.target_unit_ids] if order.target_unit_ids else [],
        "processing": status_note == "processing",
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
            "classification": o.parsed_order.get("classification") if o.parsed_order else None,
            "language": o.parsed_order.get("language") if o.parsed_order else None,
            "confidence": o.parsed_order.get("confidence") if o.parsed_order else None,
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
            "sender_id": str(m.sender_id) if m.sender_id else "",
            "sender_name": m.sender_name,
            "text": m.text,
            "recipient": m.recipient,
            "side": m.side,
            "timestamp": m.created_at.isoformat() if m.created_at else None,
            "own": m.sender_id == my_user_id,
            "is_unit_response": bool(m.sender_name and m.sender_name.startswith("📻")),
            "is_order": bool(m.sender_name and m.sender_name.startswith("📋")),
        }
        for m in messages
        # Include if broadcast (all) or if I'm sender or recipient
        if m.recipient == "all" or m.sender_id == my_user_id or m.recipient == str(my_user_id)
    ]


