"""
Event generation helper – creates Event DB rows for all notable state changes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from backend.models.event import Event


def create_event(
    session_id: uuid.UUID,
    tick: int,
    game_time: datetime | None,
    event_dict: dict,
    visibility: str = "all",
) -> Event:
    """
    Create an Event ORM instance from a dict produced by engine sub-modules.

    event_dict keys:
      event_type, text_summary, payload,
      actor_unit_id (optional), target_unit_id (optional)
    """
    return Event(
        session_id=session_id,
        tick=tick,
        game_timestamp=game_time,
        event_type=event_dict.get("event_type", "unknown"),
        visibility=visibility,
        actor_unit_id=event_dict.get("actor_unit_id"),
        target_unit_id=event_dict.get("target_unit_id"),
        payload=event_dict.get("payload"),
        text_summary=event_dict.get("text_summary", ""),
    )

