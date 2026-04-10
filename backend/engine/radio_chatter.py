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


# ── Templates for post-combat casualty reports ──

CASUALTY_REPORT_RU = [
    "Здесь {unit}. Цель уничтожена. Личный состав: {strength}%. Боеприпасы: {ammo}%. Ожидаем указаний, приём.",
    "{unit}, приём. Противник уничтожен в районе {grid}. Потери: {strength}% боеспособности. Жду дальнейших указаний.",
    "Здесь {unit}. Доклад: цель поражена. Состояние: {strength}%, БК: {ammo}%. Жду приказов, приём.",
]

CASUALTY_REPORT_EN = [
    "This is {unit}. Target destroyed. Strength at {strength}%. Ammo: {ammo}%. Awaiting orders, over.",
    "{unit}, over. Enemy eliminated at grid {grid}. Status: {strength}% combat effective. Standing by for orders.",
    "This is {unit}. Report: target neutralized. Strength {strength}%, ammo {ammo}%. Awaiting further tasking, over.",
]


def generate_casualty_radio_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    grid_service=None,
    language: str = "ru",
) -> list[dict]:
    """
    Generate radio messages from units involved in destroying an enemy.
    Each involved unit reports their status and casualties.
    """
    messages = []
    templates = CASUALTY_REPORT_RU if language == "ru" else CASUALTY_REPORT_EN

    units_by_id = {str(u.id): u for u in all_units}

    for evt in tick_events:
        if evt.get("event_type") != "unit_destroyed":
            continue
        involved_ids = evt.get("payload", {}).get("involved_unit_ids", [])
        if not involved_ids:
            actor = evt.get("actor_unit_id")
            if actor:
                involved_ids = [str(actor)]

        for uid_str in involved_ids:
            unit = units_by_id.get(uid_str)
            if not unit or unit.is_destroyed:
                continue

            comms = unit.comms_status
            if hasattr(comms, 'value'):
                comms = comms.value
            if comms == "offline":
                continue

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

            strength_pct = round((unit.strength or 1.0) * 100)
            ammo_pct = round((unit.ammo or 1.0) * 100)
            side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)

            text = random.choice(templates).format(
                unit=unit.name, grid=grid,
                strength=strength_pct, ammo=ammo_pct,
            )
            messages.append({
                "sender_name": unit.name,
                "side": side,
                "text": text,
                "is_unit_response": True,
                "response_type": "casualty_report",
            })

    return messages


# ── Templates for combat role coordination ──

ROLE_SUPPRESS_RU = [
    "Здесь {unit}. Обеспечиваю подавление, район {grid}. Прикрываю {ally}.",
    "{unit}, приём. Веду огонь на подавление, квадрат {grid}.",
]

ROLE_SUPPRESS_EN = [
    "This is {unit}. Providing suppressive fire at grid {grid}. Covering {ally}.",
    "{unit}, over. Suppressing target at {grid}.",
]

ROLE_FLANK_RU = [
    "Здесь {unit}. Выхожу на фланг, район {grid}. Прошу продолжать подавление.",
    "{unit}, приём. Обхожу позицию противника с фланга. Не прекращайте огонь!",
]

ROLE_FLANK_EN = [
    "This is {unit}. Moving to flank position, grid {grid}. Continue suppression.",
    "{unit}, over. Flanking enemy position. Maintain covering fire!",
]

ROLE_ASSAULT_RU = [
    "Здесь {unit}. Выдвигаюсь на штурм, район {grid}. Прикройте!",
    "{unit}, приём. Иду на сближение с противником. Огнём поддержите!",
]

ROLE_ASSAULT_EN = [
    "This is {unit}. Moving to assault position at grid {grid}. Cover me!",
    "{unit}, over. Closing in on enemy position. Keep them suppressed!",
]

CEASEFIRE_REQUEST_RU = [
    "Здесь {unit}! Прошу прекратить огонь по цели, пехота выходит на штурм! Район {grid}.",
    "{unit}, приём! Прекратите огонь! Мы выходим на рубеж атаки, район {grid}!",
]

CEASEFIRE_REQUEST_EN = [
    "This is {unit}! Requesting cease fire on target — infantry moving to assault! Grid {grid}.",
    "{unit}, over! Check fire, check fire! We are approaching the target area, grid {grid}!",
]

CEASEFIRE_ACK_RU = [
    "Здесь {unit}. Принял, прекращаю огонь. Последний залп — отбой.",
    "{unit}, приём. Огонь прекращаю, подтверждаю. Удачи на штурме.",
]

CEASEFIRE_ACK_EN = [
    "This is {unit}. Copy, ceasing fire. Last round — check fire.",
    "{unit}, roger. Cease fire confirmed. Good luck on the assault.",
]

CEASEFIRE_CLEAR_RU = [
    "Здесь {unit}. Артиллерия прекратила огонь, продолжаю выдвижение.",
    "{unit}, приём. Огонь прекращён, возобновляем движение.",
]

CEASEFIRE_CLEAR_EN = [
    "This is {unit}. Artillery cease-fire confirmed, resuming advance.",
    "{unit}, over. Fire cleared, continuing movement.",
]


def generate_combat_coordination_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    grid_service=None,
    language: str = "ru",
) -> list[dict]:
    """
    Generate radio messages for combat coordination events:
    - Role assignments (suppress/flank/assault)
    - Cease-fire requests and acknowledgments
    - Cease-fire cleared messages

    Returns list of chat message dicts.
    """
    messages = []

    units_by_id = {str(u.id): u for u in all_units}

    # ── Cease-fire requests ──
    ceasefire_templates = CEASEFIRE_REQUEST_RU if language == "ru" else CEASEFIRE_REQUEST_EN
    ceasefire_ack_templates = CEASEFIRE_ACK_RU if language == "ru" else CEASEFIRE_ACK_EN
    ceasefire_clear_templates = CEASEFIRE_CLEAR_RU if language == "ru" else CEASEFIRE_CLEAR_EN

    for evt in tick_events:
        etype = evt.get("event_type", "")
        payload = evt.get("payload", {})
        actor_id = evt.get("actor_unit_id")

        if etype == "ceasefire_requested":
            unit = units_by_id.get(str(actor_id)) if actor_id else None
            arty_id = payload.get("artillery_id")
            arty_unit = units_by_id.get(arty_id) if arty_id else None

            if unit and not unit.is_destroyed:
                grid = _resolve_grid(unit, grid_service, language)
                side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
                ally = arty_unit.name if arty_unit else "artillery"
                text = random.choice(ceasefire_templates).format(
                    unit=unit.name, grid=grid, ally=ally
                )
                messages.append({
                    "sender_name": unit.name,
                    "side": side,
                    "text": text,
                    "is_unit_response": True,
                    "response_type": "ceasefire_request",
                })

                # Artillery acknowledges
                if arty_unit and not arty_unit.is_destroyed:
                    ack_text = random.choice(ceasefire_ack_templates).format(unit=arty_unit.name)
                    messages.append({
                        "sender_name": arty_unit.name,
                        "side": side,
                        "text": ack_text,
                        "is_unit_response": True,
                        "response_type": "ceasefire_ack",
                    })

        elif etype == "ceasefire_cleared":
            unit = units_by_id.get(str(actor_id)) if actor_id else None
            if unit and not unit.is_destroyed:
                side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
                text = random.choice(ceasefire_clear_templates).format(unit=unit.name)
                messages.append({
                    "sender_name": unit.name,
                    "side": side,
                    "text": text,
                    "is_unit_response": True,
                    "response_type": "ceasefire_clear",
                })

    # ── Combat role assignment announcements ──
    # Only announce when roles are newly assigned this tick
    suppress_templates = ROLE_SUPPRESS_RU if language == "ru" else ROLE_SUPPRESS_EN
    flank_templates = ROLE_FLANK_RU if language == "ru" else ROLE_FLANK_EN
    assault_templates = ROLE_ASSAULT_RU if language == "ru" else ROLE_ASSAULT_EN

    announced_units = set()
    for u in all_units:
        if u.is_destroyed:
            continue
        task = u.current_task
        if not task:
            continue
        role = task.get("combat_role")
        assign_tick = task.get("combat_role_assigned_tick", 0)
        if not role or assign_tick != tick:
            continue  # Only announce on the tick roles are assigned

        uid_str = str(u.id)
        if uid_str in announced_units:
            continue
        announced_units.add(uid_str)

        comms = u.comms_status
        if hasattr(comms, 'value'):
            comms = comms.value
        if comms == "offline":
            continue

        grid = _resolve_grid(u, grid_service, language)
        side = u.side.value if hasattr(u.side, 'value') else str(u.side)

        # Find an ally name for context
        target_id = task.get("target_unit_id")
        ally_name = ""
        if target_id:
            for other in all_units:
                if other.is_destroyed or str(other.id) == uid_str:
                    continue
                other_task = other.current_task
                if other_task and other_task.get("target_unit_id") == target_id:
                    ally_name = other.name
                    break

        if role == "suppress":
            text = random.choice(suppress_templates).format(
                unit=u.name, grid=grid, ally=ally_name or "assault team"
            )
        elif role == "flank":
            text = random.choice(flank_templates).format(unit=u.name, grid=grid)
        elif role == "assault":
            text = random.choice(assault_templates).format(unit=u.name, grid=grid)
        else:
            continue

        messages.append({
            "sender_name": u.name,
            "side": side,
            "text": text,
            "is_unit_response": True,
            "response_type": f"combat_role_{role}",
        })

    return messages


def _resolve_grid(unit, grid_service, language: str) -> str:
    """Helper to resolve a unit's position to a grid reference."""
    grid = "текущем районе" if language == "ru" else "current area"
    if grid_service and unit.position is not None:
        try:
            from geoalchemy2.shape import to_shape
            pt = to_shape(unit.position)
            snail = grid_service.point_to_snail(pt.y, pt.x, depth=2)
            if snail:
                grid = snail
        except Exception:
            pass
    return grid


# ── Templates for contact detection radio messages ──

CONTACT_REPORT_RU = [
    "Здесь {unit}! Наблюдаем противника, район {grid}. {type_desc}, дистанция ~{dist}м. Приём.",
    "{unit}, приём! Обнаружен противник: {type_desc}, квадрат {grid}, ~{dist}м. Продолжаю наблюдение.",
    "Здесь {unit}. Контакт! {type_desc} замечен в районе {grid}, ~{dist}м.",
]

CONTACT_REPORT_EN = [
    "This is {unit}! Contact — enemy spotted at grid {grid}. {type_desc}, distance ~{dist}m. Over.",
    "{unit}, over! Enemy detected: {type_desc}, grid {grid}, ~{dist}m. Continuing observation.",
    "This is {unit}. Contact! {type_desc} observed at grid {grid}, ~{dist}m.",
]

UNIT_TYPE_NAMES_RU = {
    "infantry_platoon": "пехотный взвод", "infantry_company": "пехотная рота",
    "infantry_section": "пехотное отделение", "infantry_squad": "пехотное отделение",
    "infantry_team": "пехотная группа", "infantry_battalion": "пехотный батальон",
    "mech_platoon": "мех. взвод", "mech_company": "мех. рота",
    "tank_platoon": "танковый взвод", "tank_company": "танковая рота",
    "artillery_battery": "арт. батарея", "artillery_platoon": "арт. взвод",
    "mortar_section": "миномётная секция", "mortar_team": "миномётная группа",
    "at_team": "ПТ группа", "recon_team": "разведгруппа",
    "recon_section": "разведотделение", "observation_post": "наблюдательный пост",
    "sniper_team": "снайперская пара", "headquarters": "штаб",
}


def generate_contact_radio_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    grid_service=None,
    language: str = "ru",
) -> list[dict]:
    """
    Generate radio messages when units detect new enemy contacts.

    Triggered by 'contact_new' events in the tick.
    The observing unit reports the contact over radio.

    Returns list of chat message dicts.
    """
    messages = []

    templates = CONTACT_REPORT_RU if language == "ru" else CONTACT_REPORT_EN
    type_names = UNIT_TYPE_NAMES_RU if language == "ru" else {}

    units_by_id = {str(u.id): u for u in all_units}

    # Track which units already reported this tick (avoid spam)
    reported_units = set()

    for evt in tick_events:
        if evt.get("event_type") != "contact_new":
            continue

        payload = evt.get("payload", {})
        observer_id = evt.get("actor_unit_id")
        if not observer_id:
            observer_id = payload.get("observing_unit_id")
        if not observer_id:
            continue

        observer_id_str = str(observer_id)
        if observer_id_str in reported_units:
            continue  # one report per unit per tick

        unit = units_by_id.get(observer_id_str)
        if not unit or unit.is_destroyed:
            continue

        comms = unit.comms_status
        if hasattr(comms, 'value'):
            comms = comms.value
        if comms == "offline":
            continue

        # Get contact info
        contact_type = payload.get("estimated_type", "")
        contact_lat = payload.get("lat")
        contact_lon = payload.get("lon")

        # Resolve grid reference for the contact position
        contact_grid = "неизвестном районе" if language == "ru" else "unknown area"
        if grid_service and contact_lat is not None and contact_lon is not None:
            try:
                snail = grid_service.point_to_snail(contact_lat, contact_lon, depth=2)
                if snail:
                    contact_grid = snail
            except Exception:
                pass

        # Type description
        if language == "ru":
            type_desc = type_names.get(contact_type, contact_type.replace("_", " ") if contact_type else "противник")
        else:
            type_desc = contact_type.replace("_", " ") if contact_type else "enemy unit"

        # Distance
        dist = "?"
        if contact_lat is not None and contact_lon is not None and unit.position is not None:
            try:
                from geoalchemy2.shape import to_shape
                import math
                pt = to_shape(unit.position)
                dlat = (contact_lat - pt.y) * 111320
                dlon = (contact_lon - pt.x) * 74000
                dist = str(round(math.sqrt(dlat**2 + dlon**2)))
            except Exception:
                pass

        side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
        text = random.choice(templates).format(
            unit=unit.name, grid=contact_grid,
            type_desc=type_desc, dist=dist,
        )
        messages.append({
            "sender_name": unit.name,
            "side": side,
            "text": text,
            "is_unit_response": True,
            "response_type": "contact_report",
        })
        reported_units.add(observer_id_str)

    return messages

