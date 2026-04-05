"""
Communications engine.

AGENTS.MD Section 8.7:
  operational: orders reach instantly
  degraded: order delivery delayed by 2 ticks
  offline: orders do not reach; unit continues last task

Degradation triggers:
  - suppression > 0.7 → probabilistic degrade to 'degraded'
  - comms unit destroyed → 'offline' for subordinates
"""

from __future__ import annotations

import uuid


def process_comms(
    all_units: list,
    under_fire: set[uuid.UUID],
) -> list[dict]:
    """
    Update communications status for all units.

    Returns list of comms-related event dicts.
    """
    events = []

    for unit in all_units:
        if unit.is_destroyed:
            continue

        suppression = unit.suppression if unit.suppression is not None else 0.0
        current_status = unit.comms_status
        current_val = current_status.value if hasattr(current_status, 'value') else str(current_status)

        if current_val == "operational" and suppression > 0.7:
            # Degrade comms under heavy suppression
            unit.comms_status = "degraded"
            events.append({
                "event_type": "comms_change",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} comms degraded due to heavy suppression",
                "payload": {"from": "operational", "to": "degraded"},
            })
        elif current_val == "degraded" and suppression <= 0.3:
            # Recover comms when suppression drops
            unit.comms_status = "operational"
            events.append({
                "event_type": "comms_change",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} comms restored",
                "payload": {"from": "degraded", "to": "operational"},
            })

    return events

