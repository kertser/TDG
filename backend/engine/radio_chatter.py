"""
Radio chatter engine — generates automatic unit radio messages.

Features:
1. Idle units request orders when their task completes
2. Units under pressure request support from CoC peers (siblings)
3. Post-combat casualty reports
4. Contact detection reports
5. Combat role coordination (suppress/flank/assault)
6. Ceasefire coordination
7. Artillery fire request/response exchanges
8. Coordinated attack planning between infantry units
"""

from __future__ import annotations

import random


def _get_unit_lang(unit, language: str = "ru", side_languages: dict | None = None) -> str:
    """Get the language for a unit based on its side.

    Uses side_languages dict if provided (e.g. {"blue": "en", "red": "ru"}).
    Falls back to the global language parameter.
    """
    if side_languages:
        side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
        return side_languages.get(side, language)
    return language

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
    side_languages: dict | None = None,
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

        # Per-side language
        lang = _get_unit_lang(unit, language, side_languages)
        templates = IDLE_TEMPLATES_RU if lang == "ru" else IDLE_TEMPLATES_EN

        # Resolve grid reference
        grid = "текущем районе" if lang == "ru" else "current position"
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
    side_languages: dict | None = None,
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
        lang = _get_unit_lang(unit, language, side_languages)
        grid = "текущем районе" if lang == "ru" else "current area"
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
        req_templates = SUPPORT_REQUEST_RU if lang == "ru" else SUPPORT_REQUEST_EN
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
        ack_templates = SUPPORT_ACK_RU if lang == "ru" else SUPPORT_ACK_EN
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
    side_languages: dict | None = None,
) -> list[dict]:
    """
    Generate radio messages from units involved in destroying an enemy.
    Each involved unit reports their status and casualties.
    """
    messages = []

    units_by_id = {str(u.id): u for u in all_units}

    for evt in tick_events:
        if evt.get("event_type") != "unit_destroyed":
            continue
        involved_ids = evt.get("payload", {}).get("involved_unit_ids", [])
        if not involved_ids:
            actor = evt.get("actor_unit_id")
            if actor:
                involved_ids = [str(actor)]

        # Get the destroyed enemy's position from event payload for accurate reporting
        target_grid_cache = {}  # cache per event
        target_lat = evt.get("payload", {}).get("target_lat")
        target_lon = evt.get("payload", {}).get("target_lon")
        # Also try to get target unit position directly
        target_uid = evt.get("target_unit_id")
        if target_uid and str(target_uid) in units_by_id:
            target_unit = units_by_id[str(target_uid)]
            if target_unit.position is not None:
                try:
                    from geoalchemy2.shape import to_shape
                    pt = to_shape(target_unit.position)
                    target_lat = pt.y
                    target_lon = pt.x
                except Exception:
                    pass

        for uid_str in involved_ids:
            unit = units_by_id.get(uid_str)
            if not unit or unit.is_destroyed:
                continue

            comms = unit.comms_status
            if hasattr(comms, 'value'):
                comms = comms.value
            if comms == "offline":
                continue

            # Per-side language
            lang = _get_unit_lang(unit, language, side_languages)
            templates = CASUALTY_REPORT_RU if lang == "ru" else CASUALTY_REPORT_EN

            # Use the TARGET's grid (where the enemy was destroyed), not the unit's own grid
            grid = "текущем районе" if lang == "ru" else "current area"
            if grid_service and target_lat is not None and target_lon is not None:
                try:
                    snail = grid_service.point_to_snail(target_lat, target_lon, depth=2)
                    if snail:
                        grid = snail
                except Exception:
                    pass
            elif grid_service:
                # Fallback to unit's own position if target position unknown
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
    side_languages: dict | None = None,
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

    for evt in tick_events:
        etype = evt.get("event_type", "")
        payload = evt.get("payload", {})
        actor_id = evt.get("actor_unit_id")

        if etype == "ceasefire_requested":
            unit = units_by_id.get(str(actor_id)) if actor_id else None
            arty_id = payload.get("artillery_id")
            arty_unit = units_by_id.get(arty_id) if arty_id else None

            if unit and not unit.is_destroyed:
                lang = _get_unit_lang(unit, language, side_languages)
                ceasefire_templates = CEASEFIRE_REQUEST_RU if lang == "ru" else CEASEFIRE_REQUEST_EN
                grid = _resolve_grid(unit, grid_service, lang)
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
                    ceasefire_ack_templates = CEASEFIRE_ACK_RU if lang == "ru" else CEASEFIRE_ACK_EN
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
                lang = _get_unit_lang(unit, language, side_languages)
                ceasefire_clear_templates = CEASEFIRE_CLEAR_RU if lang == "ru" else CEASEFIRE_CLEAR_EN
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
            continue

        uid_str = str(u.id)
        if uid_str in announced_units:
            continue
        announced_units.add(uid_str)

        comms = u.comms_status
        if hasattr(comms, 'value'):
            comms = comms.value
        if comms == "offline":
            continue

        lang = _get_unit_lang(u, language, side_languages)
        suppress_templates = ROLE_SUPPRESS_RU if lang == "ru" else ROLE_SUPPRESS_EN
        flank_templates = ROLE_FLANK_RU if lang == "ru" else ROLE_FLANK_EN
        assault_templates = ROLE_ASSAULT_RU if lang == "ru" else ROLE_ASSAULT_EN

        grid = _resolve_grid(u, grid_service, lang)
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

# Category-level type names for distance-degraded identification
CATEGORY_NAMES_RU = {
    "infantry": "пехота", "armor": "бронетехника", "artillery": "артиллерия",
    "recon": "разведка", "engineer": "инженерные", "support": "тыловые",
    "command": "командный пункт", "unknown": "противник",
}
CATEGORY_NAMES_EN = {
    "infantry": "infantry", "armor": "armor", "artillery": "artillery",
    "recon": "recon", "engineer": "engineers", "support": "support",
    "command": "command", "unknown": "enemy force",
}

# Size-level descriptors
SIZE_NAMES_RU = {
    "battalion": "батальон", "company": "рота", "platoon": "взвод",
    "section": "отделение", "team": "группа",
}
SIZE_NAMES_EN = {
    "battalion": "battalion", "company": "company", "platoon": "platoon",
    "section": "section", "team": "team",
}


def _format_contact_type_desc(contact_type: str, contact_size: str | None, lang: str) -> str:
    """Format a human-readable type description from estimated_type and estimated_size.

    The detection engine returns estimated_type that may be:
    - Full type (e.g. 'infantry_squad') at close range
    - Category only (e.g. 'infantry') at medium/long range
    estimated_size may be 'team', 'platoon', 'company', etc. or None at long range.
    """
    if not contact_type:
        return "противник" if lang == "ru" else "enemy unit"

    if lang == "ru":
        # Try exact match first (close range full type)
        if contact_type in UNIT_TYPE_NAMES_RU:
            return UNIT_TYPE_NAMES_RU[contact_type]
        # Category-level
        cat_name = CATEGORY_NAMES_RU.get(contact_type, contact_type.replace("_", " "))
        if contact_size:
            size_name = SIZE_NAMES_RU.get(contact_size, contact_size)
            return f"{cat_name}, ~{size_name}"
        return cat_name
    else:
        # English
        # Try exact match first (close range full type)
        if "_" in contact_type and contact_type not in CATEGORY_NAMES_EN:
            # Full type like 'infantry_squad' — format nicely
            return contact_type.replace("_", " ")
        cat_name = CATEGORY_NAMES_EN.get(contact_type, contact_type.replace("_", " "))
        if contact_size:
            size_name = SIZE_NAMES_EN.get(contact_size, contact_size)
            return f"{cat_name}, ~{size_name}"
        return cat_name


def generate_contact_radio_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    grid_service=None,
    language: str = "ru",
    side_languages: dict | None = None,
) -> list[dict]:
    """
    Generate radio messages when units detect new enemy contacts.

    Triggered by 'contact_new' events in the tick.
    The observing unit reports the contact over radio.

    Returns list of chat message dicts.
    """
    messages = []

    units_by_id = {str(u.id): u for u in all_units}

    # Track which units already reported this tick (avoid spam)
    reported_units = set()

    # Skip units that have contact_during_advance events — those units will
    # report via generate_contact_halt_messages instead (avoids duplicate messages)
    contact_halt_unit_ids = set()
    for evt in tick_events:
        if evt.get("event_type") == "contact_during_advance":
            actor = evt.get("actor_unit_id")
            if actor:
                contact_halt_unit_ids.add(str(actor))

    for evt in tick_events:
        if evt.get("event_type") not in ("contact_new", "contact_refreshed"):
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
        if observer_id_str in contact_halt_unit_ids:
            continue  # this unit will report via contact_halt_messages instead

        unit = units_by_id.get(observer_id_str)
        if not unit or unit.is_destroyed:
            continue

        comms = unit.comms_status
        if hasattr(comms, 'value'):
            comms = comms.value
        if comms == "offline":
            continue

        # Per-side language
        lang = _get_unit_lang(unit, language, side_languages)
        templates = CONTACT_REPORT_RU if lang == "ru" else CONTACT_REPORT_EN
        type_names = UNIT_TYPE_NAMES_RU if lang == "ru" else {}

        # Get contact info
        contact_type = payload.get("estimated_type", "")
        contact_size = payload.get("estimated_size")
        contact_lat = payload.get("lat")
        contact_lon = payload.get("lon")

        # Resolve grid reference for the contact position
        contact_grid = "неизвестном районе" if lang == "ru" else "unknown area"
        if grid_service and contact_lat is not None and contact_lon is not None:
            try:
                snail = grid_service.point_to_snail(contact_lat, contact_lon, depth=2)
                if snail:
                    contact_grid = snail
            except Exception:
                pass

        # Type description (distance-aware: detection engine provides estimated type)
        type_desc = _format_contact_type_desc(contact_type, contact_size, lang)

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


# ── Templates for artillery fire request/response ──

FIRE_REQUEST_RU = [
    "Здесь {unit}! Обнаружен противник, район {contact_grid}. Прошу огневую поддержку! Координаты цели: {contact_grid}.",
    "{unit}, приём! Наблюдаю противника, квадрат {contact_grid}, дистанция ~{dist}м. Запрашиваю огонь по цели!",
    "Здесь {unit}. Контакт с противником в районе {contact_grid}. Передаю координаты для артиллерии. Прошу подавить!",
]

FIRE_REQUEST_EN = [
    "This is {unit}! Enemy contact, grid {contact_grid}. Requesting fire support! Target coordinates: {contact_grid}.",
    "{unit}, over! Observing enemy at grid {contact_grid}, ~{dist}m. Request fire mission on target!",
    "This is {unit}. Contact at grid {contact_grid}. Passing coordinates for artillery. Request suppression!",
]

FIRE_RESPONSE_RU = [
    "Здесь {arty}. Принял координаты {contact_grid}. Готовлю огонь, {salvos} залпов. Ожидайте.",
    "{arty}, принял. Цель в квадрате {contact_grid}. Открываю огонь по готовности.",
    "Здесь {arty}. Координаты приняты. Район {contact_grid}. Огонь!",
]

FIRE_RESPONSE_EN = [
    "This is {arty}. Copy coordinates {contact_grid}. Preparing fire, {salvos} rounds. Stand by.",
    "{arty}, roger. Target at grid {contact_grid}. Firing when ready.",
    "This is {arty}. Coordinates received. Grid {contact_grid}. Shot, over!",
]

FIRE_SUPPORT_PROGRESS_RU = {
    "closing": "Здесь {unit}. Сближаюсь с целью, до района удара ~{dist}м. Продолжайте подавление.",
    "final_approach": "Здесь {unit}. До цели ~{dist}м. Готовьтесь к переносу или прекращению огня.",
}

FIRE_SUPPORT_PROGRESS_EN = {
    "closing": "This is {unit}. Closing on the target, ~{dist}m from the impact area. Continue suppression.",
    "final_approach": "This is {unit}. ~{dist}m to target. Prepare to shift or cease fire.",
}

FIRE_SUPPORT_PROGRESS_ACK_RU = {
    "closing": "{arty}, принял. Продолжаю огонь по цели.",
    "final_approach": "{arty}, принял. Готовлю перенос или прекращение огня.",
}

FIRE_SUPPORT_PROGRESS_ACK_EN = {
    "closing": "{arty}, copy. Continuing fire on the target.",
    "final_approach": "{arty}, copy. Preparing to shift or cease fire.",
}


def generate_artillery_fire_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    grid_service=None,
    language: str = "ru",
    side_languages: dict | None = None,
) -> list[dict]:
    """
    Generate radio exchanges when artillery is auto-assigned to support a unit.
    """
    messages = []

    units_by_id = {str(u.id): u for u in all_units}

    for evt in tick_events:
        if evt.get("event_type") != "artillery_support":
            continue

        payload = evt.get("payload", {})
        arty_id = payload.get("artillery_id")
        supported_id = payload.get("supported_unit_id")
        target_lat = payload.get("target_lat")
        target_lon = payload.get("target_lon")

        arty_unit = units_by_id.get(arty_id) if arty_id else None
        supported_unit = units_by_id.get(supported_id) if supported_id else None

        if not arty_unit or not supported_unit:
            continue
        if arty_unit.is_destroyed or supported_unit.is_destroyed:
            continue

        # Check comms — skip if either unit is offline
        skip = False
        for u in (supported_unit, arty_unit):
            comms = u.comms_status
            if hasattr(comms, 'value'):
                comms = comms.value
            if comms == "offline":
                skip = True
                break
        if skip:
            continue

        # Per-side language
        lang = _get_unit_lang(supported_unit, language, side_languages)
        req_templates = FIRE_REQUEST_RU if lang == "ru" else FIRE_REQUEST_EN
        resp_templates = FIRE_RESPONSE_RU if lang == "ru" else FIRE_RESPONSE_EN

        # Resolve target grid
        contact_grid = "неизвестном районе" if lang == "ru" else "unknown area"
        if grid_service and target_lat is not None and target_lon is not None:
            try:
                snail = grid_service.point_to_snail(target_lat, target_lon, depth=2)
                if snail:
                    contact_grid = snail
            except Exception:
                pass

        # Distance from supported unit to target
        dist = "?"
        if target_lat is not None and target_lon is not None and supported_unit.position is not None:
            try:
                from geoalchemy2.shape import to_shape
                import math
                pt = to_shape(supported_unit.position)
                dlat = (target_lat - pt.y) * 111320
                dlon = (target_lon - pt.x) * 74000
                dist = str(round(math.sqrt(dlat**2 + dlon**2)))
            except Exception:
                pass

        side = supported_unit.side.value if hasattr(supported_unit.side, 'value') else str(supported_unit.side)

        # 1. Supported unit requests fire
        req_text = random.choice(req_templates).format(
            unit=supported_unit.name, contact_grid=contact_grid, dist=dist,
        )
        messages.append({
            "sender_name": supported_unit.name,
            "side": side,
            "text": req_text,
            "is_unit_response": True,
            "response_type": "fire_request",
        })

        # 2. Artillery responds
        salvos = 3
        arty_task = arty_unit.current_task
        if arty_task:
            salvos = arty_task.get("salvos_remaining", 3)

        resp_text = random.choice(resp_templates).format(
            arty=arty_unit.name, contact_grid=contact_grid, salvos=salvos,
        )
        messages.append({
            "sender_name": arty_unit.name,
            "side": side,
            "text": resp_text,
            "is_unit_response": True,
            "response_type": "fire_response",
        })

    return messages


def generate_fire_support_progress_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    grid_service=None,
    language: str = "ru",
    side_languages: dict | None = None,
) -> list[dict]:
    """Generate radio updates while maneuver units close under friendly fire support."""
    messages = []
    units_by_id = {str(u.id): u for u in all_units}

    for evt in tick_events:
        if evt.get("event_type") != "fire_support_progress":
            continue

        payload = evt.get("payload", {})
        unit = units_by_id.get(str(evt.get("actor_unit_id")))
        arty = units_by_id.get(str(payload.get("artillery_id")))
        if not unit or not arty or unit.is_destroyed or arty.is_destroyed:
            continue

        lang = _get_unit_lang(unit, language, side_languages)
        side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
        dist = int(round(payload.get("distance_to_target_m", 0)))
        stage = payload.get("stage", "closing")

        progress_templates = FIRE_SUPPORT_PROGRESS_RU if lang == "ru" else FIRE_SUPPORT_PROGRESS_EN
        ack_templates = FIRE_SUPPORT_PROGRESS_ACK_RU if lang == "ru" else FIRE_SUPPORT_PROGRESS_ACK_EN

        text = progress_templates.get(stage, progress_templates["closing"]).format(
            unit=unit.name,
            dist=dist,
        )
        ack = ack_templates.get(stage, ack_templates["closing"]).format(arty=arty.name)

        messages.append({
            "sender_name": unit.name,
            "side": side,
            "text": text,
            "is_unit_response": True,
            "response_type": "fire_support_progress",
        })
        messages.append({
            "sender_name": arty.name,
            "side": side,
            "text": ack,
            "is_unit_response": True,
            "response_type": "fire_support_progress_ack",
        })

    return messages


# ── Templates for coordinated attack radio ──

ATTACK_COORD_RU = [
    "Здесь {unit}. Наблюдаю тот же противник, район {grid}. Координируем действия. {role_msg}",
    "{unit}, приём. Противник обнаружен, квадрат {grid}. Предлагаю совместную атаку. {role_msg}",
]

ATTACK_COORD_EN = [
    "This is {unit}. Same enemy contact, grid {grid}. Coordinating. {role_msg}",
    "{unit}, over. Enemy confirmed at grid {grid}. Proposing combined assault. {role_msg}",
]

ATTACK_COORD_ROLE_RU = {
    "suppress": "Обеспечиваю огневое прикрытие.",
    "flank": "Выхожу во фланг.",
    "assault": "Иду на сближение.",
    "default": "Вступаю в бой.",
}

ATTACK_COORD_ROLE_EN = {
    "suppress": "Providing covering fire.",
    "flank": "Moving to flank.",
    "assault": "Closing in.",
    "default": "Engaging.",
}

ATTACK_ACK_RU = [
    "Здесь {ally}. Понял, координируем. {role_msg} Удачи!",
    "{ally}, принял. {role_msg} Работаем.",
]

ATTACK_ACK_EN = [
    "This is {ally}. Copy, coordinating. {role_msg} Good luck!",
    "{ally}, roger. {role_msg} Let's go.",
]


def generate_coordinated_attack_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    grid_service=None,
    language: str = "ru",
    side_languages: dict | None = None,
) -> list[dict]:
    """
    Generate radio messages when multiple units from the same side engage the same target.
    """
    messages = []

    coord_templates_ru = ATTACK_COORD_RU
    coord_templates_en = ATTACK_COORD_EN
    ack_templates_ru = ATTACK_ACK_RU
    ack_templates_en = ATTACK_ACK_EN
    role_msgs_ru = ATTACK_COORD_ROLE_RU
    role_msgs_en = ATTACK_COORD_ROLE_EN

    # Group active engagements by target_unit_id
    target_to_attackers: dict[str, list] = {}
    for u in all_units:
        if u.is_destroyed:
            continue
        task = u.current_task
        if not task:
            continue
        task_type = task.get("type", "")
        if task_type not in ("attack", "engage", "fire"):
            continue
        target_uid = task.get("target_unit_id")
        if not target_uid:
            continue
        target_to_attackers.setdefault(str(target_uid), []).append(u)

    # Only generate for groups of 2+ and only on the tick roles were assigned
    announced_pairs = set()
    for target_id, attackers in target_to_attackers.items():
        if len(attackers) < 2:
            continue

        # Check if any of these units got their combat role THIS tick
        newly_assigned = [
            u for u in attackers
            if u.current_task
            and u.current_task.get("combat_role_assigned_tick") == tick
        ]
        if not newly_assigned:
            continue

        # Find units with different roles for the coordination exchange
        by_role: dict[str, list] = {}
        for u in attackers:
            role = (u.current_task or {}).get("combat_role", "default")
            by_role.setdefault(role, []).append(u)

        # Pick one initiator (prefer newly assigned) and one responder
        initiator = newly_assigned[0]
        responder = None
        for u in attackers:
            if u.id != initiator.id and not u.is_destroyed:
                comms = u.comms_status
                if hasattr(comms, 'value'):
                    comms = comms.value
                if comms != "offline":
                    responder = u
                    break

        if not responder:
            continue

        pair_key = tuple(sorted([str(initiator.id), str(responder.id)]))
        if pair_key in announced_pairs:
            continue
        announced_pairs.add(pair_key)

        # Check initiator comms
        init_comms = initiator.comms_status
        if hasattr(init_comms, 'value'):
            init_comms = init_comms.value
        if init_comms == "offline":
            continue

        # Resolve grid
        lang = _get_unit_lang(initiator, language, side_languages)
        grid = _resolve_grid(initiator, grid_service, lang)
        side = initiator.side.value if hasattr(initiator.side, 'value') else str(initiator.side)

        init_role = (initiator.current_task or {}).get("combat_role", "default")
        resp_role = (responder.current_task or {}).get("combat_role", "default")

        role_msgs = role_msgs_ru if lang == "ru" else role_msgs_en
        init_role_msg = role_msgs.get(init_role, role_msgs["default"])
        resp_role_msg = role_msgs.get(resp_role, role_msgs["default"])

        # 1. Initiator announces coordination
        coord_templates = coord_templates_ru if lang == "ru" else coord_templates_en
        coord_text = random.choice(coord_templates).format(
            unit=initiator.name, grid=grid, role_msg=init_role_msg,
        )
        messages.append({
            "sender_name": initiator.name,
            "side": side,
            "text": coord_text,
            "is_unit_response": True,
            "response_type": "attack_coordination",
        })

        # 2. Responder acknowledges
        ack_templates = ack_templates_ru if lang == "ru" else ack_templates_en
        ack_text = random.choice(ack_templates).format(
            ally=responder.name, role_msg=resp_role_msg,
        )
        messages.append({
            "sender_name": responder.name,
            "side": side,
            "text": ack_text,
            "is_unit_response": True,
            "response_type": "attack_coordination_ack",
        })

    return messages


# ── Contact During Advance: halt and request orders ────────────

CONTACT_HALT_RU = [
    "Здесь {unit}! Обнаружен противник в квадрате {contact_grid}, дистанция {dist}м! Прекратили движение, занимаем позицию в {grid}. Запрашиваю разрешение на вступление в бой, приём!",
    "{unit}, приём! Контакт с противником! {type_desc} в районе {contact_grid}, {dist}м. Остановились в {grid}. Жду указаний — атаковать или обойти? Приём!",
    "Здесь {unit}. Обнаружен противник ({type_desc}) в квадрате {contact_grid}. Движение остановлено, позиция {grid}. Прошу приказа на дальнейшие действия!",
]

CONTACT_HALT_EN = [
    "This is {unit}! Enemy contact at grid {contact_grid}, range {dist}m! Halted, holding position at {grid}. Requesting permission to engage, over!",
    "{unit}, over! Contact! {type_desc} at {contact_grid}, {dist}m. Halted at {grid}. Awaiting orders — engage or bypass? Over!",
    "This is {unit}. Enemy detected ({type_desc}) at grid {contact_grid}. Movement halted, position {grid}. Requesting further orders!",
]

CONTACT_RESUME_RU = [
    "Здесь {unit}. Команд не поступало, возобновляю движение к цели. Приём.",
    "{unit}, ответа нет. Продолжаю выполнение задачи, двигаюсь к цели.",
    "Здесь {unit}. По собственной инициативе продолжаю движение. Приём.",
]

CONTACT_RESUME_EN = [
    "This is {unit}. No orders received, resuming advance to objective. Over.",
    "{unit}, no response. Continuing mission, moving to objective.",
    "This is {unit}. Resuming movement on own initiative. Over.",
]


def generate_contact_halt_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    grid_service=None,
    language: str = "ru",
    side_languages: dict | None = None,
) -> list[dict]:
    """
    Generate radio messages when units halt due to enemy contact during advance,
    and when they resume movement after timeout.

    Triggered by 'contact_during_advance' and 'contact_advance_resumed' events.
    """
    messages = []
    units_by_id = {str(u.id): u for u in all_units}

    for evt in tick_events:
        evt_type = evt.get("event_type")

        if evt_type == "contact_during_advance":
            actor_id = evt.get("actor_unit_id")
            if not actor_id:
                continue
            unit = units_by_id.get(str(actor_id))
            if not unit or unit.is_destroyed:
                continue

            comms = unit.comms_status
            if hasattr(comms, 'value'):
                comms = comms.value
            if comms == "offline":
                continue

            lang = _get_unit_lang(unit, language, side_languages)
            templates = CONTACT_HALT_RU if lang == "ru" else CONTACT_HALT_EN

            payload = evt.get("payload", {})
            grid = payload.get("grid", "?")

            # Find the contact event for this unit to get contact details
            contact_grid = "?"
            contact_dist = "?"
            type_desc = "противник" if lang == "ru" else "enemy"
            for ce in tick_events:
                if ce.get("event_type") in ("contact_new",) and str(ce.get("actor_unit_id")) == str(actor_id):
                    cp = ce.get("payload", {})
                    c_lat = cp.get("lat")
                    c_lon = cp.get("lon")
                    if grid_service and c_lat is not None and c_lon is not None:
                        try:
                            snail = grid_service.point_to_snail(c_lat, c_lon, depth=2)
                            if snail:
                                contact_grid = snail
                        except Exception:
                            pass
                    c_type = cp.get("estimated_type", "")
                    if c_type:
                        type_desc = c_type
                    if c_lat is not None and c_lon is not None and unit.position:
                        try:
                            from geoalchemy2.shape import to_shape
                            import math
                            pt = to_shape(unit.position)
                            dlat = (c_lat - pt.y) * 111320
                            dlon = (c_lon - pt.x) * 74000
                            contact_dist = str(round(math.sqrt(dlat ** 2 + dlon ** 2)))
                        except Exception:
                            pass
                    break

            side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
            text = random.choice(templates).format(
                unit=unit.name, grid=grid,
                contact_grid=contact_grid, dist=contact_dist,
                type_desc=type_desc,
            )
            messages.append({
                "sender_name": unit.name,
                "side": side,
                "text": text,
                "is_unit_response": True,
                "response_type": "contact_halt",
            })

        elif evt_type == "contact_advance_resumed":
            actor_id = evt.get("actor_unit_id")
            if not actor_id:
                continue
            unit = units_by_id.get(str(actor_id))
            if not unit or unit.is_destroyed:
                continue

            comms = unit.comms_status
            if hasattr(comms, 'value'):
                comms = comms.value
            if comms == "offline":
                continue

            lang = _get_unit_lang(unit, language, side_languages)
            templates = CONTACT_RESUME_RU if lang == "ru" else CONTACT_RESUME_EN
            side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)

            text = random.choice(templates).format(unit=unit.name)
            messages.append({
                "sender_name": unit.name,
                "side": side,
                "text": text,
                "is_unit_response": True,
                "response_type": "contact_resume",
            })

    return messages


# ── Templates for out-of-range artillery ──

OUT_OF_RANGE_RU = [
    "Здесь {unit}. Цель за пределами дальности — {dist}м, макс. {max_range}м. Не могу открыть огонь.",
    "{unit}, приём. Дальность цели превышает возможности — {dist}м против {max_range}м. Запрашиваю выдвижение на позицию.",
    "Здесь {unit}. Отказ. Цель {dist}м, дальность орудий {max_range}м. Огонь невозможен.",
]

OUT_OF_RANGE_EN = [
    "This is {unit}. Target out of range — {dist}m, max range {max_range}m. Cannot engage.",
    "{unit}, over. Target distance {dist}m exceeds maximum range {max_range}m. Requesting reposition.",
    "This is {unit}. Negative. Target at {dist}m, weapons max range {max_range}m. Unable to fire.",
]


def generate_out_of_range_messages(
    all_units: list,
    tick_events: list[dict],
    tick: int,
    language: str = "ru",
    side_languages: dict | None = None,
) -> list[dict]:
    """
    Generate radio messages when artillery/mortar cannot reach the target.
    Triggered by 'fire_out_of_range' events in the tick.
    """
    messages = []
    units_by_id = {str(u.id): u for u in all_units}

    # Deduplicate — one message per firing unit per tick
    reported_units: set[str] = set()

    for evt in tick_events:
        if evt.get("event_type") != "fire_out_of_range":
            continue

        actor_id = evt.get("actor_unit_id")
        if not actor_id:
            continue
        actor_id_str = str(actor_id)
        if actor_id_str in reported_units:
            continue

        unit = units_by_id.get(actor_id_str)
        if not unit or unit.is_destroyed:
            continue

        comms = unit.comms_status
        if hasattr(comms, 'value'):
            comms = comms.value
        if comms == "offline":
            continue

        reported_units.add(actor_id_str)

        lang = _get_unit_lang(unit, language, side_languages)
        templates = OUT_OF_RANGE_RU if lang == "ru" else OUT_OF_RANGE_EN
        side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)

        payload = evt.get("payload", {})
        dist = int(round(payload.get("distance_m", 0)))
        max_range = int(round(payload.get("weapon_range_m", 0)))

        text = random.choice(templates).format(
            unit=unit.name, dist=dist, max_range=max_range,
        )
        messages.append({
            "sender_name": unit.name,
            "side": side,
            "text": text,
            "is_unit_response": True,
            "response_type": "fire_out_of_range",
        })

    return messages


