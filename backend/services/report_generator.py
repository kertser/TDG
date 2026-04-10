"""
Report Generator — auto-generates tactical reports after each tick.

Report types (channels):
  - spotrep   : Immediate enemy contact report (on new contact detection)
  - shelrep   : Incoming fire/combat report (when units take fire)
  - sitrep    : Periodic situation report (every SITREP_INTERVAL ticks)
  - intsum    : Intelligence summary (every INTSUM_INTERVAL ticks)
  - casrep    : Casualty report (when unit is destroyed)

All reports are deterministic — no LLM involvement.
Reports are persisted in the Report table and broadcast via WebSocket.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

from geoalchemy2.shape import to_shape

logger = logging.getLogger(__name__)

# ── Intervals ──
SITREP_INTERVAL = 5   # ticks between periodic SITREPs
INTSUM_INTERVAL = 10  # ticks between intelligence summaries

# ── Translations ──
TERRAIN_NAMES_RU = {
    "road": "дорога", "open": "открытая местность", "forest": "лес",
    "urban": "городская застройка", "water": "водная преграда",
    "fields": "поля", "marsh": "болото", "desert": "пустыня",
    "scrub": "кустарник", "bridge": "мост", "mountain": "горы",
    "orchard": "сад",
}

UNIT_TYPES_RU = {
    "infantry_platoon": "пех. взвод", "infantry_company": "пех. рота",
    "infantry_section": "пех. отд.", "infantry_squad": "пех. отд.",
    "infantry_team": "пех. группа", "infantry_battalion": "пех. батальон",
    "mech_platoon": "мех. взвод", "mech_company": "мех. рота",
    "tank_platoon": "танк. взвод", "tank_company": "танк. рота",
    "artillery_battery": "арт. батарея", "artillery_platoon": "арт. взвод",
    "mortar_section": "мин. отд.", "mortar_team": "мин. расчёт",
    "at_team": "птрк расчёт", "recon_team": "разведгруппа",
    "recon_section": "разведотд.", "observation_post": "НП",
    "sniper_team": "снайп. пара", "headquarters": "штаб",
    "logistics_unit": "тыловое подр.",
}

STRENGTH_LABELS_RU = {
    "full": "полная", "good": "боеспособен", "reduced": "потери",
    "heavy_losses": "тяжёлые потери", "critical": "критические потери",
}
STRENGTH_LABELS_EN = {
    "full": "full strength", "good": "combat effective", "reduced": "reduced",
    "heavy_losses": "heavy losses", "critical": "critical losses",
}

TASK_NAMES_RU = {
    "move": "марш", "attack": "атака", "engage": "бой",
    "fire": "огонь", "defend": "оборона", "observe": "наблюдение",
    "halt": "стоп", "dig_in": "окопаться",
}
TASK_NAMES_EN = {
    "move": "moving", "attack": "attacking", "engage": "engaging",
    "fire": "firing", "defend": "defending", "observe": "observing",
    "halt": "halted", "dig_in": "digging in",
}


METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0


def _distance_m(lat1, lon1, lat2, lon2):
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON_AT_48
    return math.sqrt(dlat * dlat + dlon * dlon)


def _bearing_deg(lat1, lon1, lat2, lon2):
    dy = (lat2 - lat1) * METERS_PER_DEG_LAT
    dx = (lon2 - lon1) * METERS_PER_DEG_LON_AT_48
    return math.degrees(math.atan2(dx, dy)) % 360


def _bearing_to_compass(deg, lang="en"):
    dirs_en = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    dirs_ru = ["С", "ССВ", "СВ", "ВСВ", "В", "ВЮВ", "ЮВ", "ЮЮВ",
               "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗСЗ", "СЗ", "ССЗ"]
    dirs = dirs_ru if lang == "ru" else dirs_en
    idx = round(deg / 22.5) % 16
    return dirs[idx]


def _strength_label(s, lang="en"):
    if s > 0.85:
        return STRENGTH_LABELS_RU["full"] if lang == "ru" else STRENGTH_LABELS_EN["full"]
    elif s > 0.65:
        return STRENGTH_LABELS_RU["good"] if lang == "ru" else STRENGTH_LABELS_EN["good"]
    elif s > 0.45:
        return STRENGTH_LABELS_RU["reduced"] if lang == "ru" else STRENGTH_LABELS_EN["reduced"]
    elif s > 0.25:
        return STRENGTH_LABELS_RU["heavy_losses"] if lang == "ru" else STRENGTH_LABELS_EN["heavy_losses"]
    else:
        return STRENGTH_LABELS_RU["critical"] if lang == "ru" else STRENGTH_LABELS_EN["critical"]


def _unit_type_display(unit_type, lang="en"):
    if lang == "ru":
        return UNIT_TYPES_RU.get(unit_type, unit_type.replace("_", " "))
    return unit_type.replace("_", " ")


def _get_unit_pos(unit):
    """Extract (lat, lon) from unit.position."""
    if unit.position is None:
        return None
    try:
        pt = to_shape(unit.position)
        return pt.y, pt.x
    except Exception:
        return None


def _get_grid_ref(unit, grid_service):
    """Get snail grid reference for a unit's position."""
    if not grid_service:
        return None
    pos = _get_unit_pos(unit)
    if not pos:
        return None
    try:
        return grid_service.point_to_snail(pos[0], pos[1], depth=2)
    except Exception:
        return None


def _task_display(task, lang="en"):
    if not task:
        return "ожидание" if lang == "ru" else "idle"
    t = task.get("type", "")
    names = TASK_NAMES_RU if lang == "ru" else TASK_NAMES_EN
    return names.get(t, t)


# ═══════════════════════════════════════════════════════════
#  SPOTREP — new contact detected
# ═══════════════════════════════════════════════════════════

def generate_spotreps(
    tick_events: list[dict],
    all_units: list,
    contacts: list,
    tick: int,
    grid_service=None,
    lang: str = "ru",
) -> list[dict]:
    """
    Generate SPOTREP for each new contact detected this tick.

    Returns list of report dicts ready for DB insertion.
    """
    reports = []

    # Find contact_new events
    for evt in tick_events:
        if evt.get("event_type") != "contact_new":
            continue

        payload = evt.get("payload", {})
        obs_side = payload.get("observing_side", "blue")
        est_type = payload.get("estimated_type", "unknown")
        lat = payload.get("lat")
        lon = payload.get("lon")
        confidence = payload.get("confidence", 0.5)

        if lat is None or lon is None:
            continue

        # Find the observer unit name
        observer_name = None
        observer_uid = evt.get("actor_unit_id")
        if observer_uid:
            for u in all_units:
                if str(u.id) == str(observer_uid):
                    observer_name = u.name
                    break

        # Grid reference
        grid_ref = None
        if grid_service:
            try:
                grid_ref = grid_service.point_to_snail(lat, lon, depth=2)
            except Exception:
                pass

        type_display = _unit_type_display(est_type, lang)
        conf_pct = int(confidence * 100)

        # Compute bearing from observer to contact
        bearing_str = ""
        if observer_uid:
            for u in all_units:
                if str(u.id) == str(observer_uid):
                    obs_pos = _get_unit_pos(u)
                    if obs_pos:
                        brg = _bearing_deg(obs_pos[0], obs_pos[1], lat, lon)
                        compass = _bearing_to_compass(brg, lang)
                        dist_m = _distance_m(obs_pos[0], obs_pos[1], lat, lon)
                        if lang == "ru":
                            bearing_str = f", направление {compass} ({int(brg)}°), дальность ~{int(dist_m)}м"
                        else:
                            bearing_str = f", bearing {compass} ({int(brg)}°), range ~{int(dist_m)}m"
                    break

        if lang == "ru":
            text = f"РАЗВЕДДОНЕСЕНИЕ. "
            if observer_name:
                text += f"Докладывает {observer_name}. "
            text += f"Обнаружен противник: {type_display}"
            if grid_ref:
                text += f", район {grid_ref}"
            text += bearing_str
            text += f". Достоверность: {conf_pct}%. Приём."
        else:
            text = f"SPOTREP. "
            if observer_name:
                text += f"From: {observer_name}. "
            text += f"Enemy spotted: {type_display}"
            if grid_ref:
                text += f", grid {grid_ref}"
            text += bearing_str
            text += f". Confidence: {conf_pct}%. Over."

        reports.append({
            "channel": "spotrep",
            "to_side": obs_side,
            "from_unit_id": observer_uid,
            "text": text,
            "structured_data": {
                "type": "spotrep",
                "estimated_type": est_type,
                "lat": lat,
                "lon": lon,
                "grid_ref": grid_ref,
                "confidence": confidence,
                "observer": observer_name,
            },
        })

    return reports


# ═══════════════════════════════════════════════════════════
#  SHELREP / Combat Report — units taking fire
# ═══════════════════════════════════════════════════════════

def generate_shelreps(
    tick_events: list[dict],
    all_units: list,
    under_fire: set,
    tick: int,
    grid_service=None,
    lang: str = "ru",
) -> list[dict]:
    """
    Generate SHELREP for units that took significant fire this tick.
    Only report if damage is non-trivial (> 0.01).
    """
    reports = []
    reported_targets = set()

    for evt in tick_events:
        if evt.get("event_type") != "combat":
            continue

        payload = evt.get("payload", {})
        target_id = payload.get("target")
        damage = payload.get("damage", 0)
        distance = payload.get("distance_m", 0)

        if not target_id or damage < 0.01:
            continue
        if target_id in reported_targets:
            continue
        reported_targets.add(target_id)

        # Find target unit
        target_unit = None
        for u in all_units:
            if str(u.id) == str(target_id):
                target_unit = u
                break
        if not target_unit:
            continue

        side = target_unit.side.value if hasattr(target_unit.side, 'value') else str(target_unit.side)
        grid_ref = _get_grid_ref(target_unit, grid_service)
        pos = _get_unit_pos(target_unit)
        strength = target_unit.strength or 1.0
        strPct = int(strength * 100)
        s_label = _strength_label(strength, lang)

        if lang == "ru":
            text = f"ДОНЕСЕНИЕ ОБ ОБСТРЕЛЕ. {target_unit.name} ведёт бой"
            if grid_ref:
                text += f", район {grid_ref}"
            text += f". Боеспособность: {s_label} ({strPct}%)"
            if (target_unit.ammo or 1.0) < 0.3:
                text += f". Боеприпасы на исходе"
            text += f". Приём."
        else:
            text = f"SHELREP. {target_unit.name} under fire"
            if grid_ref:
                text += f", grid {grid_ref}"
            text += f". Status: {s_label} ({strPct}%)"
            if (target_unit.ammo or 1.0) < 0.3:
                text += ". Low ammunition"
            text += f". Over."

        reports.append({
            "channel": "shelrep",
            "to_side": side,
            "from_unit_id": target_unit.id,
            "text": text,
            "structured_data": {
                "type": "shelrep",
                "unit_name": target_unit.name,
                "unit_id": str(target_unit.id),
                "grid_ref": grid_ref,
                "lat": pos[0] if pos else None,
                "lon": pos[1] if pos else None,
                "strength": round(strength, 2),
                "damage_this_tick": round(damage, 4),
            },
        })

    return reports


# ═══════════════════════════════════════════════════════════
#  CASREP — unit destroyed
# ═══════════════════════════════════════════════════════════

def generate_casreps(
    tick_events: list[dict],
    all_units: list,
    tick: int,
    grid_service=None,
    lang: str = "ru",
) -> list[dict]:
    """Generate casualty report when a unit is destroyed."""
    reports = []

    for evt in tick_events:
        if evt.get("event_type") != "unit_destroyed":
            continue

        payload = evt.get("payload", {})
        target_id = payload.get("target")
        attacker_id = payload.get("attacker")

        # Find destroyed unit
        destroyed = None
        for u in all_units:
            if str(u.id) == str(target_id):
                destroyed = u
                break
        if not destroyed:
            continue

        side = destroyed.side.value if hasattr(destroyed.side, 'value') else str(destroyed.side)
        grid_ref = _get_grid_ref(destroyed, grid_service)

        if lang == "ru":
            text = f"ДОНЕСЕНИЕ О ПОТЕРЯХ. {destroyed.name} уничтожен"
            if grid_ref:
                text += f", район {grid_ref}"
            text += ". Подразделение потеряно. Приём."
        else:
            text = f"CASREP. {destroyed.name} destroyed"
            if grid_ref:
                text += f", grid {grid_ref}"
            text += ". Unit lost. Over."

        reports.append({
            "channel": "casrep",
            "to_side": side,
            "from_unit_id": None,
            "text": text,
            "structured_data": {
                "type": "casrep",
                "unit_name": destroyed.name,
                "unit_id": str(destroyed.id),
                "grid_ref": grid_ref,
            },
        })

    return reports


# ═══════════════════════════════════════════════════════════
#  SITREP — periodic situation report (per side)
# ═══════════════════════════════════════════════════════════

def generate_sitreps(
    all_units: list,
    contacts: list,
    tick: int,
    tick_events: list[dict],
    grid_service=None,
    lang: str = "ru",
) -> list[dict]:
    """
    Generate periodic SITREP for each side.
    Only generated every SITREP_INTERVAL ticks.
    """
    if tick % SITREP_INTERVAL != 0 or tick == 0:
        return []

    reports = []

    for side in ("blue", "red"):
        side_units = [
            u for u in all_units
            if not u.is_destroyed
            and (u.side.value if hasattr(u.side, 'value') else str(u.side)) == side
        ]
        if not side_units:
            continue

        # Count stats
        total = len(side_units)
        moving = sum(1 for u in side_units if u.current_task and u.current_task.get("type") in ("move", "advance"))
        fighting = sum(1 for u in side_units if u.current_task and u.current_task.get("type") in ("attack", "engage", "fire"))
        defending = sum(1 for u in side_units if u.current_task and u.current_task.get("type") in ("defend", "dig_in"))
        idle = total - moving - fighting - defending

        # Average strength and morale
        avg_strength = sum(u.strength or 1.0 for u in side_units) / total if total else 1.0
        avg_morale = sum(u.morale or 1.0 for u in side_units) / total if total else 1.0

        # Low ammo units
        low_ammo = sum(1 for u in side_units if (u.ammo or 1.0) < 0.3)

        # Count contacts for this side
        side_contacts = [
            c for c in contacts
            if (c.observing_side.value if hasattr(c.observing_side, 'value') else str(c.observing_side)) == side
            and not c.is_stale
        ]

        # Destroyed this tick
        destroyed_this_tick = [
            evt for evt in tick_events
            if evt.get("event_type") == "unit_destroyed"
        ]
        our_losses = 0
        enemy_losses = 0
        for evt in destroyed_this_tick:
            target_id = evt.get("payload", {}).get("target")
            for u in all_units:
                if str(u.id) == str(target_id):
                    u_side = u.side.value if hasattr(u.side, 'value') else str(u.side)
                    if u_side == side:
                        our_losses += 1
                    else:
                        enemy_losses += 1
                    break

        # Build per-unit detail lines for more informative report
        unit_details_lines = []
        for u in side_units:
            u_type = _unit_type_display(u.unit_type, lang)
            u_grid = _get_grid_ref(u, grid_service) or "?"
            u_str = _strength_label(u.strength or 1.0, lang)
            u_task = _task_display(u.current_task, lang)
            if lang == "ru":
                line = f"  • {u.name} ({u_type}) — {u_grid}, {u_str}, {u_task}"
            else:
                line = f"  • {u.name} ({u_type}) — {u_grid}, {u_str}, {u_task}"
            if (u.ammo or 1.0) < 0.3:
                line += " ⚠БК" if lang == "ru" else " ⚠AMMO"
            unit_details_lines.append(line)

        if lang == "ru":
            text = f"ДОКЛАД ОБ ОБСТАНОВКЕ, ход {tick}.\n"
            text += f"В строю: {total} подразд. (на марше: {moving}, в бою: {fighting}, в обороне: {defending}, ожидание: {idle}).\n"
            text += f"Средняя боеспособность: {avg_strength:.0%}, боевой дух: {avg_morale:.0%}."
            if low_ammo:
                text += f" Низкий боекомплект: {low_ammo} подр."
            text += f"\nИзвестные позиции противника: {len(side_contacts)}."
            if our_losses:
                text += f" Потери за ход: {our_losses}."
            if enemy_losses:
                text += f" Уничтожено противника: {enemy_losses}."
            if unit_details_lines:
                text += "\nСостав и положение:\n" + "\n".join(unit_details_lines)
            text += "\nПриём."
        else:
            text = f"SITREP, turn {tick}.\n"
            text += f"Units: {total} (moving: {moving}, combat: {fighting}, defending: {defending}, idle: {idle}).\n"
            text += f"Avg strength: {avg_strength:.0%}, morale: {avg_morale:.0%}."
            if low_ammo:
                text += f" Low ammo: {low_ammo} units."
            text += f"\nEnemy contacts: {len(side_contacts)}."
            if our_losses:
                text += f" Own losses this turn: {our_losses}."
            if enemy_losses:
                text += f" Enemy losses: {enemy_losses}."
            if unit_details_lines:
                text += "\nUnit disposition:\n" + "\n".join(unit_details_lines)
            text += "\nOver."

        reports.append({
            "channel": "sitrep",
            "to_side": side,
            "from_unit_id": None,
            "text": text,
            "structured_data": {
                "type": "sitrep",
                "tick": tick,
                "total_units": total,
                "moving": moving,
                "fighting": fighting,
                "defending": defending,
                "idle": idle,
                "avg_strength": round(avg_strength, 2),
                "avg_morale": round(avg_morale, 2),
                "low_ammo_count": low_ammo,
                "contacts": len(side_contacts),
                "own_losses": our_losses,
                "enemy_losses": enemy_losses,
            },
        })

    return reports


# ═══════════════════════════════════════════════════════════
#  INTSUM — intelligence summary (per side)
# ═══════════════════════════════════════════════════════════

def generate_intsums(
    all_units: list,
    contacts: list,
    tick: int,
    grid_service=None,
    lang: str = "ru",
) -> list[dict]:
    """
    Generate intelligence summary — all known contacts with assessment.
    Only generated every INTSUM_INTERVAL ticks.
    """
    if tick % INTSUM_INTERVAL != 0 or tick == 0:
        return []

    reports = []

    for side in ("blue", "red"):
        side_contacts = [
            c for c in contacts
            if (c.observing_side.value if hasattr(c.observing_side, 'value') else str(c.observing_side)) == side
            and not c.is_stale
        ]
        if not side_contacts:
            continue

        # Group by estimated type
        type_counts = {}
        contact_details = []

        for c in side_contacts:
            est_type = c.estimated_type or "unknown"
            type_counts[est_type] = type_counts.get(est_type, 0) + 1

            # Get contact position
            try:
                cpt = to_shape(c.location_estimate)
                c_lat, c_lon = cpt.y, cpt.x
            except Exception:
                continue

            grid_ref = None
            if grid_service:
                try:
                    grid_ref = grid_service.point_to_snail(c_lat, c_lon, depth=2)
                except Exception:
                    pass

            type_disp = _unit_type_display(est_type, lang)
            conf = int((c.confidence or 0.5) * 100)
            stale_str = ""
            if c.is_stale:
                stale_str = " [устаревший]" if lang == "ru" else " [stale]"

            if lang == "ru":
                detail = f"  • {type_disp}"
                if grid_ref:
                    detail += f" — {grid_ref}"
                detail += f" ({c_lat:.4f}, {c_lon:.4f})"
                detail += f", достоверность {conf}%{stale_str}"
            else:
                detail = f"  • {type_disp}"
                if grid_ref:
                    detail += f" — {grid_ref}"
                detail += f" ({c_lat:.4f}, {c_lon:.4f})"
                detail += f", confidence {conf}%{stale_str}"

            contact_details.append(detail)

        # Build summary
        type_summary_parts = []
        for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            disp = _unit_type_display(t, lang)
            type_summary_parts.append(f"{disp}: {cnt}")

        if lang == "ru":
            text = f"РАЗВЕДСВОДКА, ход {tick}.\n"
            text += f"Всего контактов: {len(side_contacts)}."
            if type_summary_parts:
                text += f" Состав: {', '.join(type_summary_parts)}.\n"
            text += "Детали:\n"
            text += "\n".join(contact_details)
            text += "\nПриём."
        else:
            text = f"INTSUM, turn {tick}.\n"
            text += f"Total contacts: {len(side_contacts)}."
            if type_summary_parts:
                text += f" Composition: {', '.join(type_summary_parts)}.\n"
            text += "Details:\n"
            text += "\n".join(contact_details)
            text += "\nOver."

        reports.append({
            "channel": "intsum",
            "to_side": side,
            "from_unit_id": None,
            "text": text,
            "structured_data": {
                "type": "intsum",
                "tick": tick,
                "total_contacts": len(side_contacts),
                "type_counts": type_counts,
                "contacts": [
                    {
                        "estimated_type": c.estimated_type,
                        "confidence": c.confidence,
                        "is_stale": c.is_stale,
                    }
                    for c in side_contacts
                ],
            },
        })

    return reports


# ═══════════════════════════════════════════════════════════
#  Master generator — called from tick.py
# ═══════════════════════════════════════════════════════════

def generate_tick_reports(
    all_units: list,
    contacts: list,
    tick: int,
    game_time: datetime,
    tick_events: list[dict],
    under_fire: set,
    grid_service=None,
    lang: str = "ru",
    side_languages: dict | None = None,
) -> list[dict]:
    """
    Generate all applicable reports for the current tick.

    Returns list of report dicts with keys:
        channel, to_side, from_unit_id, text, structured_data

    Caller (tick.py) is responsible for creating Report model rows
    and broadcasting via WebSocket.
    """
    reports = []

    # 1. SPOTREPs — new enemy contacts
    reports.extend(generate_spotreps(tick_events, all_units, contacts, tick, grid_service, lang))

    # 2. SHELREPs — units under fire
    reports.extend(generate_shelreps(tick_events, all_units, under_fire, tick, grid_service, lang))

    # 3. CASREPs — units destroyed
    reports.extend(generate_casreps(tick_events, all_units, tick, grid_service, lang))

    # 4. SITREPs — periodic
    reports.extend(generate_sitreps(all_units, contacts, tick, tick_events, grid_service, lang))

    # 5. INTSUMs — periodic intelligence
    reports.extend(generate_intsums(all_units, contacts, tick, grid_service, lang))

    return reports


