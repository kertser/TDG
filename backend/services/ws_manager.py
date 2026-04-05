"""
WebSocket connection manager.

Tracks connected clients per session, handles broadcast with side filtering.
Single-process MVP — uses in-memory dict. Future: Redis pub/sub for multi-worker.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass
class ClientInfo:
    """Metadata about a connected WebSocket client."""
    user_id: uuid.UUID
    display_name: str
    side: str
    websocket: WebSocket


class ConnectionManager:
    """Manages WebSocket connections per session."""

    def __init__(self):
        # session_id → {user_id → ClientInfo}
        self._connections: dict[uuid.UUID, dict[uuid.UUID, ClientInfo]] = {}

    def connect(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        display_name: str,
        side: str,
        websocket: WebSocket,
    ) -> None:
        """Register a new WebSocket connection."""
        if session_id not in self._connections:
            self._connections[session_id] = {}
        self._connections[session_id][user_id] = ClientInfo(
            user_id=user_id,
            display_name=display_name,
            side=side,
            websocket=websocket,
        )

    def disconnect(self, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Remove a WebSocket connection."""
        conns = self._connections.get(session_id, {})
        conns.pop(user_id, None)
        if not conns:
            self._connections.pop(session_id, None)

    def get_clients(self, session_id: uuid.UUID) -> list[ClientInfo]:
        """All connected clients for a session."""
        return list(self._connections.get(session_id, {}).values())

    async def broadcast(
        self,
        session_id: uuid.UUID,
        message: dict,
        exclude_user: uuid.UUID | None = None,
        only_side: str | None = None,
    ) -> None:
        """
        Broadcast a JSON message to all clients in a session.

        Args:
            session_id: Target session
            message: Dict to JSON-encode and send
            exclude_user: Skip this user (e.g., the sender)
            only_side: If set, only send to clients on this side (+ admin/observer)
        """
        conns = self._connections.get(session_id, {})
        data = json.dumps(message)

        for uid, client in list(conns.items()):
            if uid == exclude_user:
                continue
            if only_side and client.side not in (only_side, "admin", "observer"):
                continue
            try:
                await client.websocket.send_text(data)
            except Exception:
                # Client disconnected unexpectedly
                conns.pop(uid, None)

    async def send_to_user(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        message: dict,
    ) -> None:
        """Send a message to a specific user."""
        conns = self._connections.get(session_id, {})
        client = conns.get(user_id)
        if client:
            try:
                await client.websocket.send_text(json.dumps(message))
            except Exception:
                conns.pop(user_id, None)


# Singleton instance
ws_manager = ConnectionManager()


