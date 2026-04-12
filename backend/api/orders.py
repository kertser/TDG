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

    import time as _pipeline_time
    _pipeline_t0 = _pipeline_time.monotonic()

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

                # Get game time from session for display
                _game_time_str = None
                _game_time_dt = None
                try:
                    from backend.models.session import Session as _Sess
                    _sess_r = await db.execute(select(_Sess.current_time).where(_Sess.id == session_id))
                    _game_t = _sess_r.scalar_one_or_none()
                    if _game_t:
                        _game_time_dt = _game_t
                        # Ensure UTC timezone suffix for consistent frontend parsing
                        _game_time_str = _game_t.isoformat()
                        if not _game_time_str.endswith('Z') and '+' not in _game_time_str:
                            _game_time_str += 'Z'
                except Exception:
                    pass

                try:
                    order_chat = ChatMessage(
                        session_id=session_id,
                        sender_id=order.issued_by_user_id,
                        sender_name=f"📋 {issuer_name}",
                        side=issuer_side,
                        recipient="all",
                        text=outgoing_text,
                        game_time=_game_time_dt,
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
                    "game_time": _game_time_str,
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
                            game_time=_game_time_dt,
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
                        "game_time": _game_time_str,
                        "is_unit_response": True,
                        "response_type": resp.response_type.value,
                    }
                    await ws_manager.broadcast(
                        session_id,
                        {"type": "chat_message", "data": chat_data},
                        only_side=issuer_side,
                    )

                # ── Immediately assign task to units (don't wait for tick) ──
                # This makes units show "moving" right away instead of staying
                # "idle" until "Execute Orders" is pressed.
                _should_broadcast_units = False
                if (
                    order.status == OrderStatus.validated
                    and parse_result.engine_task
                    and order.target_unit_ids
                ):
                    await _assign_task_to_units_immediately(
                        order, parse_result.engine_task,
                        session_id, issuer_side, db,
                    )
                    _should_broadcast_units = True

                await db.commit()

                # Broadcast updated unit state so frontend sees "moving" immediately
                if _should_broadcast_units:
                    try:
                        from backend.services.visibility_service import get_visible_units, get_visible_contacts
                        units = await get_visible_units(session_id, issuer_side, db)
                        contacts = await get_visible_contacts(session_id, issuer_side, db)
                        from backend.models.session import Session as _Sess2
                        _sess_r2 = await db.execute(select(_Sess2.tick, _Sess2.current_time).where(_Sess2.id == session_id))
                        _sess_row = _sess_r2.first()
                        _tick = _sess_row[0] if _sess_row else 0
                        _gt = _sess_row[1].isoformat() + 'Z' if _sess_row and _sess_row[1] else None
                        await ws_manager.broadcast(
                            session_id,
                            {
                                "type": "state_update",
                                "data": {
                                    "units": units,
                                    "contacts": contacts,
                                    "tick": _tick,
                                    "game_time": _gt,
                                },
                            },
                            only_side=issuer_side,
                        )
                    except Exception as e2:
                        logger.warning("Failed to broadcast immediate state update: %s", e2)

                _pipeline_elapsed = _pipeline_time.monotonic() - _pipeline_t0
                logger.info(
                    "Order pipeline completed: order=%s status=%s classification=%s total=%.1fs",
                    order_id, order.status.value,
                    parse_result.parsed.classification.value,
                    _pipeline_elapsed,
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


async def _assign_task_to_units_immediately(
    order: Order,
    task: dict,
    session_id: uuid.UUID,
    issuer_side: str,
    db: AsyncSession,
):
    """
    Immediately assign the validated order's task to target units.

    Mirrors the logic in tick.py's _process_orders() so units become
    "moving"/"defending"/etc. right away instead of waiting for the next tick.
    Sets order.status = executing to prevent tick from re-processing.
    """
    from backend.models.unit import Unit
    from backend.api.units import UNIT_TYPE_SPEEDS, DEFAULT_SPEEDS

    if not order.target_unit_ids:
        return

    task_type = task.get("type", "")

    for unit_id in order.target_unit_ids:
        result = await db.execute(
            select(Unit).where(
                Unit.id == unit_id,
                Unit.session_id == session_id,
                Unit.is_destroyed == False,
            )
        )
        unit = result.scalar_one_or_none()
        if not unit:
            continue

        if task_type == "halt":
            unit.current_task = None
        elif task_type == "disengage":
            speeds = UNIT_TYPE_SPEEDS.get(unit.unit_type, DEFAULT_SPEEDS)
            unit.move_speed_mps = speeds.get("fast", speeds.get("slow", 3.0))
            unit.current_task = {
                "type": "disengage",
                "order_id": str(order.id),
                "disengaging": True,
            }
        else:
            unit.current_task = dict(task)

            # Apply move speed from order
            speed_label = task.get("speed")
            if speed_label and task_type in ("move", "attack", "advance", "resupply"):
                speeds = UNIT_TYPE_SPEEDS.get(unit.unit_type, DEFAULT_SPEEDS)
                if speed_label in speeds:
                    unit.move_speed_mps = speeds[speed_label]
            elif task_type == "resupply" and not speed_label:
                speeds = UNIT_TYPE_SPEEDS.get(unit.unit_type, DEFAULT_SPEEDS)
                unit.move_speed_mps = speeds.get("fast", speeds.get("slow", 3.0))

            # Apply formation if specified
            formation = task.get("formation")
            if formation:
                caps = dict(unit.capabilities or {})
                caps["formation"] = formation
                unit.capabilities = caps

            # Handle phased/conditional orders
            phases = task.get("phases") or (order.parsed_order or {}).get("phases")
            if phases and isinstance(phases, list) and len(phases) > 1:
                unit.order_queue = phases[1:]

    # Mark order as executing so tick.py doesn't re-process it
    order.status = OrderStatus.executing
    await db.flush()
    logger.info(
        "Immediate task assignment: order=%s task=%s units=%d",
        order.id, task_type, len(order.target_unit_ids),
    )


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


class CancelUnitsRequest(BaseModel):
    unit_ids: list[str] | None = None  # None = all units on this side


@router.post("/{session_id}/cancel-unit-orders")
async def cancel_unit_orders(
    session_id: uuid.UUID,
    body: CancelUnitsRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """
    Cancel all active orders and clear current tasks for specified units
    (or all units on the requester's side). Units report 'awaiting orders'
    via radio.
    """
    from backend.models.unit import Unit
    from backend.models.chat_message import ChatMessage
    from backend.services.ws_manager import ws_manager
    from datetime import datetime, timezone

    side_val = participant.side.value
    if side_val == "observer" or participant.role == "observer":
        raise HTTPException(status_code=403, detail="Observers cannot cancel orders")

    if side_val not in ("blue", "red"):
        side_val = "blue"

    # Load target units
    query = select(Unit).where(
        Unit.session_id == session_id,
        Unit.is_destroyed == False,
        Unit.side == side_val,
    )
    if body.unit_ids:
        target_uuids = []
        for uid_str in body.unit_ids:
            try:
                target_uuids.append(uuid.UUID(uid_str))
            except ValueError:
                continue
        if target_uuids:
            query = query.where(Unit.id.in_(target_uuids))

    result = await db.execute(query)
    units = list(result.scalars().all())

    if not units:
        return {"cancelled_units": 0, "cancelled_orders": 0}

    unit_ids_set = {u.id for u in units}

    # 1. Cancel all pending/validated/executing orders targeting these units
    result_orders = await db.execute(
        select(Order).where(
            Order.session_id == session_id,
            Order.status.in_([
                OrderStatus.pending,
                OrderStatus.validated,
                OrderStatus.executing,
            ]),
            Order.issued_by_side == side_val,
        )
    )
    orders = list(result_orders.scalars().all())

    cancelled_count = 0
    for order in orders:
        if order.target_unit_ids:
            # Cancel if any of the order's target units are in our set
            order_targets = {uid for uid in order.target_unit_ids}
            if order_targets & unit_ids_set:
                order.status = OrderStatus.cancelled
                cancelled_count += 1
        elif not body.unit_ids:
            # If cancelling all units and order has no specific targets, cancel it too
            order.status = OrderStatus.cancelled
            cancelled_count += 1

    # 2. Clear current_task on all target units
    cleared_units = []
    for unit in units:
        if unit.current_task is not None:
            unit.current_task = None
            cleared_units.append(unit)

    # 3. Generate radio messages — units report "awaiting orders"
    now = datetime.now(timezone.utc)
    radio_broadcast = []

    # Get game time from session
    _cancel_game_time_dt = None
    try:
        from backend.models.session import Session as _Sess
        _sess_r = await db.execute(select(_Sess.current_time).where(_Sess.id == session_id))
        _cancel_gt = _sess_r.scalar_one_or_none()
        if _cancel_gt:
            _cancel_game_time_dt = _cancel_gt
    except Exception:
        pass

    # Resolve grid references for unit positions
    grid_service = None
    try:
        from backend.models.grid import GridDefinition
        from backend.services.grid_service import GridService
        gd_result = await db.execute(
            select(GridDefinition).where(GridDefinition.session_id == session_id)
        )
        gd = gd_result.scalar_one_or_none()
        if gd:
            grid_service = GridService(gd)
    except Exception:
        pass

    for unit in cleared_units:
        # Get grid reference
        grid_ref = ""
        if grid_service and unit.position:
            try:
                from geoalchemy2.shape import to_shape
                pt = to_shape(unit.position)
                snail = grid_service.point_to_snail(pt.y, pt.x, depth=2)
                if snail:
                    grid_ref = f", кв. {snail}" if True else f", grid {snail}"
            except Exception:
                pass

        msg_text = f"📻 {unit.name}: приказы отменены, ожидаю указаний{grid_ref}"

        chat = ChatMessage(
            session_id=session_id,
            sender_name=f"📻 {unit.name}",
            side=side_val,
            recipient="all",
            text=msg_text,
            game_time=_cancel_game_time_dt,
            created_at=now,
        )
        db.add(chat)

        _cancel_gt_str = None
        if _cancel_game_time_dt:
            _cancel_gt_str = _cancel_game_time_dt.isoformat()
            if not _cancel_gt_str.endswith('Z') and '+' not in _cancel_gt_str:
                _cancel_gt_str += 'Z'

        radio_broadcast.append({
            "sender_id": "",
            "sender_name": f"📻 {unit.name}",
            "text": msg_text,
            "recipient": "all",
            "side": side_val,
            "timestamp": now.isoformat(),
            "game_time": _cancel_gt_str,
            "is_unit_response": True,
            "response_type": "sitrep",
        })

    await db.flush()

    # 4. Broadcast radio messages via WebSocket
    for msg in radio_broadcast:
        await ws_manager.broadcast(
            session_id,
            {"type": "chat_message", "data": msg},
            only_side=side_val,
        )

    return {
        "cancelled_units": len(cleared_units),
        "cancelled_orders": cancelled_count,
        "total_units": len(units),
    }


@router.get("/{session_id}/chat")
async def list_chat_messages(
    session_id: uuid.UUID,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Load chat message history for a session.

    Side-filtering: unit radio messages (📻 prefix) and outgoing order
    messages (📋 prefix) are only returned to the matching side.
    Human chat messages are visible to all participants.
    Admin/observer see everything.
    """
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
    my_side = participant.side.value if hasattr(participant.side, 'value') else str(participant.side)
    is_privileged = my_side in ("admin", "observer")

    out = []
    for m in messages:
        # Basic recipient check: broadcast or I'm sender/recipient
        if not (m.recipient == "all" or m.sender_id == my_user_id or m.recipient == str(my_user_id)):
            continue

        is_unit_msg = bool(m.sender_name and m.sender_name.startswith("📻"))
        is_order_msg = bool(m.sender_name and m.sender_name.startswith("📋"))

        # Side-filter unit radio and outgoing order messages:
        # these are side-specific — Red unit reports must not leak to Blue and vice versa.
        if (is_unit_msg or is_order_msg) and not is_privileged:
            msg_side = m.side or ""
            if msg_side and msg_side != my_side:
                continue  # Skip enemy-side unit/order messages

        out.append({
            "sender_id": str(m.sender_id) if m.sender_id else "",
            "sender_name": m.sender_name,
            "text": m.text,
            "recipient": m.recipient,
            "side": m.side,
            "timestamp": m.created_at.isoformat() if m.created_at else None,
            "game_time": m.game_time.isoformat() if m.game_time else None,
            "own": m.sender_id == my_user_id,
            "is_unit_response": is_unit_msg,
            "is_order": is_order_msg,
        })

    return out


