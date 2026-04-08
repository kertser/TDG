"""
Radio chatter engine — generates automatic unit radio messages.

Two features:
1. Idle units request orders when their task completes
2. Units under pressure request support from CoC peers (siblings)
"""

from __future__ import annotations

import random

# ── Templates for idle radio messages ──

IDLE_TEMPLATES_RU = [
    "Здесь {unit}. Задача выполнена, находимся {grid}. Жду указаний, приём.",
    "{unit}, задача выполнена. На месте {grid}. Ожидаем приказов.",
    "Здесь {unit}. Прибыли в точку {grid}. Задача выполнена, жду дальнейших указаний.",
    "{unit} на позиции {grid}. Готовы к выполнению новых задач, приём.",
]

IDLE_TEMPLATES_EN = [
    "This is {unit}. Objective complete, holding at {grid}. Awaiting orders, over.",
    "{unit}, task complete. At grid {grid}. Standing by for new orders.",
    "This is {unit}. Arrived at {grid}. Mission complete, awaiting further tasking, over.",
    "{unit} at {grid}. Ready for new orders, over.",
]

# ── Templates for support requests ──

SUPPORT_REQUEST_RU = [
    "Здесь {unit}! Нуждаемся в поддержке, район {grid}! Противник давит!",
    "{unit}, приём! Под огнём в районе {grid}, прошу поддержки!",
    "Здесь {unit}! Тяжёлая обстановка, квадрат {grid}. Прошу помощи!",
]

SUPPORT_REQUEST_EN = [
    "This is {unit}! Requesting support, grid {grid}! Taking heavy fire!",
    "{unit}, over! Under fire at {grid}, need assistance!",
    "This is {unit}! Situation critical at grid {grid}. Request immediate support!",
]

SUPPORT_ACK_RU = [
    "Здесь {unit}. Понял, выдвигаемся на помощь.",
    "{unit}, приём. Идём к вам.",
]

SUPPORT_ACK_EN = [
    "This is {unit}. Copy, moving to assist.",
    "{unit}, roger. On our way.",
]


def generate_idle_radio_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    grid_service=None,
    language: str = "ru",
) -> list[dict]:
    """
    Generate radio messages for units that just completed their task.

    Returns list of chat message dicts ready for DB insertion:
    {sender_name, side, text, is_unit_response, response_type}
    """
    messages = []

    # Find units that completed orders this tick
    completed_unit_ids = set()
    for evt in tick_events:
        if evt.get("event_type") == "order_completed":
            uid = evt.get("actor_unit_id")
            if uid:
                completed_unit_ids.add(str(uid))

    if not completed_unit_ids:
        return messages

    templates = IDLE_TEMPLATES_RU if language == "ru" else IDLE_TEMPLATES_EN

    for unit in all_units:
        if unit.is_destroyed:
            continue
        uid_str = str(unit.id)
        if uid_str not in completed_unit_ids:
            continue

        # Unit just completed task — check if it's now idle
        if unit.current_task is not None:
            continue  # Got a new task already

        # Check comms
        comms = unit.comms_status
        if hasattr(comms, 'value'):
            comms = comms.value
        if comms == "offline":
            continue

        # Resolve grid reference
        grid = "текущем районе" if language == "ru" else "current position"
        if grid_service:
            try:
                from geoalchemy2.shape import to_shape
                pt = to_shape(unit.position)
                snail = grid_service.point_to_snail(pt.y, pt.x, depth=2)
                if snail:
                    grid = snail
            except Exception:
                pass

        text = random.choice(templates).format(unit=unit.name, grid=grid)
        side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)

        messages.append({
            "sender_name": unit.name,
            "side": side,
            "text": text,
            "is_unit_response": True,
            "response_type": "idle",
        })

    return messages


def generate_peer_support_requests(
    all_units: list,
    under_fire: set,
    tick: int,
    grid_service=None,
    language: str = "ru",
) -> list[dict]:
    """
    Generate radio messages for units requesting support from CoC peers.

    Triggers when a unit is under fire AND (strength < 0.5 OR suppression > 0.5).
    Finds sibling units (same parent) to send support request to.

    Throttle: only every 5 ticks per unit (uses capabilities.last_support_request_tick).

    Returns list of chat message dicts.
    """
    messages = []
    COOLDOWN_TICKS = 5

    # Build parent→children map
    parent_map: dict[str | None, list] = {}
    for u in all_units:
        if u.is_destroyed:
            continue
        pid = str(u.parent_unit_id) if u.parent_unit_id else None
        parent_map.setdefault(pid, []).append(u)

    req_templates = SUPPORT_REQUEST_RU if language == "ru" else SUPPORT_REQUEST_EN
    ack_templates = SUPPORT_ACK_RU if language == "ru" else SUPPORT_ACK_EN

    for unit in all_units:
        if unit.is_destroyed:
            continue
        if unit.id not in under_fire:
            continue

        # Check if unit needs support
        strength = unit.strength or 1.0
        suppression = unit.suppression or 0.0
        if strength >= 0.5 and suppression <= 0.5:
            continue

        # Throttle
        caps = unit.capabilities or {}
        last_req = caps.get("last_support_request_tick", -999)
        if tick - last_req < COOLDOWN_TICKS:
            continue

        # Check comms
        comms = unit.comms_status
        if hasattr(comms, 'value'):
            comms = comms.value
        if comms == "offline":
            continue

        # Find siblings (same parent, same side, not destroyed)
        pid = str(unit.parent_unit_id) if unit.parent_unit_id else None
        siblings = parent_map.get(pid, [])
        unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
        eligible_siblings = [
            s for s in siblings
            if s.id != unit.id
            and not s.is_destroyed
            and (s.side.value if hasattr(s.side, 'value') else str(s.side)) == unit_side
            and (s.comms_status.value if hasattr(s.comms_status, 'value') else str(s.comms_status)) != "offline"
        ]

        if not eligible_siblings:
            continue

        # Update cooldown
        new_caps = dict(caps)
        new_caps["last_support_request_tick"] = tick
        unit.capabilities = new_caps

        # Resolve grid
        grid = "текущем районе" if language == "ru" else "current area"
        if grid_service:
            try:
                from geoalchemy2.shape import to_shape
                pt = to_shape(unit.position)
                snail = grid_service.point_to_snail(pt.y, pt.x, depth=2)
                if snail:
                    grid = snail
            except Exception:
                pass

        # Generate request message
        text = random.choice(req_templates).format(unit=unit.name, grid=grid)
        messages.append({
            "sender_name": unit.name,
            "side": unit_side,
            "text": text,
            "is_unit_response": True,
            "response_type": "support_request",
        })

        # Pick one sibling to acknowledge
        sibling = eligible_siblings[0]  # Closest or first available
        ack_text = random.choice(ack_templates).format(unit=sibling.name)
        messages.append({
            "sender_name": sibling.name,
            "side": unit_side,
            "text": ack_text,
            "is_unit_response": True,
            "response_type": "support_ack",
        })

    return messages


