"""
Communications engine.

AGENTS.MD Section 8.7:
  operational: orders reach instantly
  degraded: order delivery delayed by 2 ticks
  offline: orders do not reach; unit continues last task

Degradation triggers:
  - suppression > 0.7 → probabilistic degrade to 'degraded'
  - no relay unit (HQ / parent) within comms range → degrade to 'degraded'
  - comms unit destroyed → 'offline' for subordinates
"""

from __future__ import annotations

import uuid

from geoalchemy2.shape import to_shape

# ── Comms range constants ────────────────────────────────────────────────────
COMMS_RANGE_DEFAULT_M = 8_000.0    # standard infantry / light units
COMMS_RANGE_HQ_M      = 25_000.0   # HQ / command post — long-range radios
COMMS_RANGE_LONG_M    = 15_000.0   # artillery, recon, aviation

# HQ / command post types — act as relay nodes and are never range-limited themselves
RELAY_UNIT_TYPES = {"headquarters", "command_post"}

# Unit types with longer built-in radios (don't need a relay as early)
LONG_RANGE_UNIT_TYPES = {
    "artillery_battery", "artillery_platoon",
    "recon_team", "recon_section",
    "attack_helicopter", "transport_helicopter", "recon_uav",
}


def _find_nearest_relay_dist(unit, all_units: list) -> float:
    """
    Return the distance (m) to the nearest relay unit on the same side.
    A relay = parent unit OR any RELAY_UNIT_TYPES unit on the same side.
    Returns float('inf') if none found.
    """
    if unit.position is None:
        return float("inf")

    from backend.engine.geo_utils import planar_offset_m

    try:
        u_pt = to_shape(unit.position)
    except Exception:
        return float("inf")

    unit_side = unit.side.value if hasattr(unit.side, "value") else str(unit.side)
    best = float("inf")

    for other in all_units:
        if other.is_destroyed or other.id == unit.id:
            continue
        other_side = other.side.value if hasattr(other.side, "value") else str(other.side)
        if other_side != unit_side:
            continue
        is_relay = (str(other.id) == str(unit.parent_unit_id)) or (other.unit_type in RELAY_UNIT_TYPES)
        if not is_relay or other.position is None:
            continue
        try:
            o_pt = to_shape(other.position)
            _, _, dist = planar_offset_m(u_pt, o_pt)
            if dist < best:
                best = dist
        except Exception:
            continue

    return best


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
        current_val = current_status.value if hasattr(current_status, "value") else str(current_status)

        # ── Suppression-based degradation (existing logic) ──
        if current_val == "operational" and suppression > 0.7:
            unit.comms_status = "degraded"
            events.append({
                "event_type": "comms_change",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} comms degraded due to heavy suppression",
                "payload": {"from": "operational", "to": "degraded", "reason": "suppression"},
                "visibility": unit.side.value if hasattr(unit.side, "value") else str(unit.side),
            })
            continue  # already degraded this tick

        # ── Range-based degradation ──
        # HQ / relay types are never range-limited themselves
        if unit.unit_type not in RELAY_UNIT_TYPES:
            unit_range = (
                COMMS_RANGE_LONG_M if unit.unit_type in LONG_RANGE_UNIT_TYPES
                else COMMS_RANGE_DEFAULT_M
            )
            relay_dist = _find_nearest_relay_dist(unit, all_units)
            too_far = relay_dist > unit_range

            if too_far and current_val == "operational":
                unit.comms_status = "degraded"
                events.append({
                    "event_type": "comms_change",
                    "actor_unit_id": unit.id,
                    "text_summary": (
                        f"{unit.name} comms degraded — out of relay range "
                        f"({relay_dist / 1000:.1f} km)"
                    ),
                    "payload": {
                        "from": "operational",
                        "to": "degraded",
                        "reason": "range",
                        "relay_dist_m": round(relay_dist, 0),
                    },
                    "visibility": unit.side.value if hasattr(unit.side, "value") else str(unit.side),
                })

            elif not too_far and current_val == "degraded" and suppression <= 0.3:
                unit.comms_status = "operational"
                events.append({
                    "event_type": "comms_change",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} comms restored — relay in range",
                    "payload": {
                        "from": "degraded",
                        "to": "operational",
                        "reason": "range_restored",
                    },
                    "visibility": unit.side.value if hasattr(unit.side, "value") else str(unit.side),
                })

        # ── Recovery from suppression when suppression drops (still in range) ──
        elif current_val == "degraded" and suppression <= 0.3:
            unit.comms_status = "operational"
            events.append({
                "event_type": "comms_change",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} comms restored",
                "payload": {"from": "degraded", "to": "operational"},
                "visibility": unit.side.value if hasattr(unit.side, "value") else str(unit.side),
            })

    return events
