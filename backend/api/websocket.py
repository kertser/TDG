"""WebSocket endpoint – real-time session communication hub."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import JWTError, jwt
from sqlalchemy import select

from backend.config import settings
from backend.database import async_session_factory
from backend.models.user import User
from backend.models.session import SessionParticipant
from backend.services.ws_manager import ws_manager

router = APIRouter()


async def _authenticate(token: str) -> User | None:
    """Validate JWT and return User."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            return None
    except JWTError:
        return None

    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        return result.scalar_one_or_none()


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(...),
):
    user = await _authenticate(token)
    if user is None:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # Verify participant
    async with async_session_factory() as db:
        result = await db.execute(
            select(SessionParticipant).where(
                SessionParticipant.session_id == session_id,
                SessionParticipant.user_id == user.id,
            )
        )
        participant = result.scalar_one_or_none()
        if participant is None:
            await websocket.close(code=4003, reason="Not a participant")
            return
        side = participant.side.value

    await websocket.accept()

    # Register with connection manager
    ws_manager.connect(
        session_id=session_id,
        user_id=user.id,
        display_name=user.display_name,
        side=side,
        websocket=websocket,
    )

    # Notify others
    await ws_manager.broadcast(
        session_id,
        {
            "type": "participant_joined",
            "data": {
                "user_id": str(user.id),
                "display_name": user.display_name,
                "side": side,
            },
        },
        exclude_user=user.id,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "data": {"message": "Invalid JSON"}})
                )
                continue

            msg_type = msg.get("type", "")
            msg_data = msg.get("data", {})

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif msg_type == "cursor_position":
                await ws_manager.broadcast(
                    session_id,
                    {
                        "type": "cursor_positions",
                        "data": [{"user_id": str(user.id), **msg_data}],
                    },
                    exclude_user=user.id,
                )

            elif msg_type == "overlay_create":
                # Map 'admin' side to 'blue' for overlay storage (OverlaySide enum)
                overlay_side = side if side in ("blue", "red", "observer") else "blue"
                await _handle_overlay_create(session_id, user.id, overlay_side, msg_data, websocket)

            elif msg_type == "overlay_update":
                await _handle_overlay_update(session_id, msg_data, websocket)

            elif msg_type == "overlay_delete":
                await _handle_overlay_delete(session_id, msg_data, websocket)

            elif msg_type == "order_submit":
                await _handle_order_submit(session_id, user.id, side, msg_data, websocket)

            else:
                await websocket.send_text(
                    json.dumps({
                        "type": "error",
                        "data": {"message": f"Unhandled message type: {msg_type}"},
                    })
                )

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(session_id, user.id)
        await ws_manager.broadcast(
            session_id,
            {"type": "participant_left", "data": {"user_id": str(user.id)}},
        )


async def _handle_overlay_create(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    side: str,
    data: dict,
    websocket: WebSocket,
):
    """Handle overlay_create WS message."""
    from backend.services import overlay_service

    required = ["overlay_type", "geometry"]
    if not all(k in data for k in required):
        await websocket.send_text(
            json.dumps({"type": "error", "data": {"message": "overlay_create requires overlay_type and geometry"}})
        )
        return

    async with async_session_factory() as db:
        try:
            result = await overlay_service.create_overlay(
                session_id=session_id,
                user_id=user_id,
                side=side,
                overlay_type=data["overlay_type"],
                geometry=data["geometry"],
                style_json=data.get("style_json"),
                label=data.get("label"),
                properties=data.get("properties"),
                db=db,
            )
            await db.commit()
            # overlay_service.create_overlay already broadcasts to all clients
        except Exception as e:
            await db.rollback()
            await websocket.send_text(
                json.dumps({"type": "error", "data": {"message": f"Overlay create failed: {str(e)}"}})
            )


async def _handle_overlay_update(
    session_id: uuid.UUID,
    data: dict,
    websocket: WebSocket,
):
    """Handle overlay_update WS message."""
    from backend.services import overlay_service

    overlay_id = data.get("overlay_id")
    if not overlay_id:
        await websocket.send_text(
            json.dumps({"type": "error", "data": {"message": "overlay_update requires overlay_id"}})
        )
        return

    async with async_session_factory() as db:
        try:
            update_kwargs = dict(
                session_id=session_id,
                overlay_id=uuid.UUID(overlay_id),
                geometry=data.get("geometry"),
                style_json=data.get("style_json"),
                properties=data.get("properties"),
                db=db,
            )
            # Only pass label if explicitly included in the message
            if "label" in data:
                update_kwargs["label"] = data["label"]

            result = await overlay_service.update_overlay(**update_kwargs)
            await db.commit()
            if result is None:
                await websocket.send_text(
                    json.dumps({"type": "error", "data": {"message": "Overlay not found"}})
                )
        except Exception as e:
            await db.rollback()
            await websocket.send_text(
                json.dumps({"type": "error", "data": {"message": f"Overlay update failed: {str(e)}"}})
            )


async def _handle_overlay_delete(
    session_id: uuid.UUID,
    data: dict,
    websocket: WebSocket,
):
    """Handle overlay_delete WS message."""
    from backend.services import overlay_service

    overlay_id = data.get("overlay_id")
    if not overlay_id:
        await websocket.send_text(
            json.dumps({"type": "error", "data": {"message": "overlay_delete requires overlay_id"}})
        )
        return

    async with async_session_factory() as db:
        try:
            success = await overlay_service.delete_overlay(
                session_id=session_id,
                overlay_id=uuid.UUID(overlay_id),
                db=db,
            )
            await db.commit()
            if not success:
                await websocket.send_text(
                    json.dumps({"type": "error", "data": {"message": "Overlay not found"}})
                )
        except Exception as e:
            await db.rollback()
            await websocket.send_text(
                json.dumps({"type": "error", "data": {"message": f"Overlay delete failed: {str(e)}"}})
            )


async def _handle_order_submit(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    side: str,
    data: dict,
    websocket: WebSocket,
):
    """Handle order_submit WS message – creates an order."""
    from backend.models.order import Order, OrderStatus

    original_text = data.get("original_text", "").strip()
    if not original_text:
        await websocket.send_text(
            json.dumps({"type": "error", "data": {"message": "Order text is required"}})
        )
        return

    target_unit_ids = data.get("target_unit_ids")

    async with async_session_factory() as db:
        try:
            order = Order(
                session_id=session_id,
                issued_by_user_id=user_id,
                issued_by_side=side,
                target_unit_ids=[uuid.UUID(uid) for uid in target_unit_ids] if target_unit_ids else None,
                original_text=original_text,
                status=OrderStatus.pending,
            )
            db.add(order)
            await db.flush()

            order_data = {
                "id": str(order.id),
                "status": order.status.value,
                "original_text": order.original_text,
            }
            await db.commit()

            # Send confirmation to sender
            await websocket.send_text(
                json.dumps({"type": "order_status", "data": order_data})
            )

            # Broadcast to side
            await ws_manager.broadcast(
                session_id,
                {"type": "order_status", "data": order_data},
                exclude_user=user_id,
                only_side=side,
            )
        except Exception as e:
            await db.rollback()
            await websocket.send_text(
                json.dumps({"type": "error", "data": {"message": f"Order submit failed: {str(e)}"}})
            )

