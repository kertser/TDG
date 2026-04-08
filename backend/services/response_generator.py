"""
ResponseGenerator – generates radio-style unit responses.

Two modes:
1. Template-based (instant, no LLM) — standard acks/nacks based on unit state
2. LLM-enhanced (future) — richer status reports

Responses are delivered as chat messages from units.
"""

from __future__ import annotations

import logging

from backend.schemas.order import (
    UnitRadioResponse,
    ResponseType,
    MessageClassification,
    ParsedOrderData,
)
from backend.prompts.response_generator import get_template_response

logger = logging.getLogger(__name__)


class ResponseGenerator:
    """Generates radio-style responses from units."""

    def generate_response(
        self,
        parsed: ParsedOrderData,
        unit: dict,
        response_type: ResponseType,
        reason_key: str | None = None,
        status_text: str = "",
    ) -> UnitRadioResponse | None:
        """
        Generate a template-based radio response from a unit.

        Args:
            parsed: The parsed order/message.
            unit: Unit dict (name, id, unit_type, strength, morale, comms_status, etc.).
            response_type: Type of response to generate.
            reason_key: Key for inability reason (destroyed, morale_broken, no_ammo, etc.).
            status_text: Status text to include in status responses.

        Returns:
            UnitRadioResponse or None (for no_response).
        """
        language = parsed.language.value
        unit_name = unit.get("name", "Unknown Unit")

        text = get_template_response(
            unit_name=unit_name,
            response_type=response_type.value,
            language=language,
            reason_key=reason_key,
            status_text=status_text,
        )

        if text is None:
            return None

        return UnitRadioResponse(
            from_unit_name=unit_name,
            from_unit_id=str(unit.get("id", "")),
            text=text,
            language=parsed.language,
            response_type=response_type,
        )

    def determine_response_type(
        self,
        parsed: ParsedOrderData,
        unit: dict,
    ) -> tuple[ResponseType, str | None]:
        """
        Determine what kind of response a unit should give based on its state.

        Returns:
            (ResponseType, reason_key or None)
        """
        # Check comms first
        comms = unit.get("comms_status", "operational")
        if comms == "offline":
            return ResponseType.no_response, None

        # Check if destroyed
        if unit.get("is_destroyed", False):
            return ResponseType.no_response, None

        # Check morale
        morale = unit.get("morale", 1.0)
        if morale < 0.15:
            return ResponseType.unable, "morale_broken"

        # Degraded comms — delayed/garbled response
        if comms == "degraded":
            return ResponseType.ack, None  # Will use comms_degraded template

        # Check if the message was unclear
        if parsed.classification == MessageClassification.unclear:
            return ResponseType.clarify, None

        # Check if the order is a status request
        if parsed.classification == MessageClassification.status_request:
            return ResponseType.status, None

        # Check ammo
        ammo = unit.get("ammo", 1.0)
        if ammo <= 0 and parsed.order_type and parsed.order_type.value in ("attack",):
            return ResponseType.unable, "no_ammo"

        # Heavy casualties
        strength = unit.get("strength", 1.0)
        if strength < 0.25 and parsed.order_type and parsed.order_type.value in ("attack", "move"):
            return ResponseType.unable, "heavy_casualties"

        # Normal acknowledgment
        if parsed.classification == MessageClassification.command:
            return ResponseType.wilco, None

        return ResponseType.ack, None

    def generate_status_report(
        self,
        unit: dict,
        language: str = "en",
        situation: dict | None = None,
    ) -> str:
        """Build a status text string for a unit, including rich situational awareness."""
        name = unit.get("name", "Unknown")
        strength = unit.get("strength", 1.0)
        morale = unit.get("morale", 1.0)
        ammo = unit.get("ammo", 1.0)
        suppression = unit.get("suppression", 0.0)
        task = unit.get("current_task")

        if language == "ru":
            # ── Basic status ──
            task_text = "без задачи" if not task else f"выполняем: {task.get('type', '?')}"
            if task and task.get("target_snail"):
                task_text += f", цель: {task['target_snail']}"
            status = (
                f"Личный состав: {strength:.0%}, "
                f"боеприпасы: {ammo:.0%}, "
                f"подавление: {suppression:.0%}. "
                f"Состояние: {task_text}."
            )
            if morale < 0.3:
                status += " Моральный дух критически низкий!"
            elif morale < 0.5:
                status += " Моральный дух снижен."

            # Situational awareness
            if situation:
                parts = []

                # Position: coordinates + grid ref
                coords = situation.get("coordinates")
                grid_ref = situation.get("grid_ref")
                if coords and grid_ref:
                    parts.append(f"Позиция: {grid_ref} (координаты {coords['lat']:.4f}, {coords['lon']:.4f})")
                elif grid_ref:
                    parts.append(f"Позиция: {grid_ref}")
                elif coords:
                    parts.append(f"Позиция: координаты {coords['lat']:.4f}, {coords['lon']:.4f}")

                # Heading
                heading_compass = situation.get("heading_compass")
                if heading_compass:
                    compass_ru = {
                        "N": "С", "NNE": "ССВ", "NE": "СВ", "ENE": "ВСВ",
                        "E": "В", "ESE": "ВЮВ", "SE": "ЮВ", "SSE": "ЮЮВ",
                        "S": "Ю", "SSW": "ЮЮЗ", "SW": "ЮЗ", "WSW": "ЗЮЗ",
                        "W": "З", "WNW": "ЗСЗ", "NW": "СЗ", "NNW": "ССЗ",
                    }
                    parts.append(f"Курс: {compass_ru.get(heading_compass, heading_compass)} ({situation.get('heading_deg', '?')}°)")

                # Terrain + elevation
                t = situation.get("terrain")
                if t:
                    terrain_names_ru = {
                        "road": "дорога", "open": "открытая местность",
                        "forest": "лес", "urban": "город", "water": "вода",
                        "fields": "поля", "marsh": "болото", "desert": "пустыня",
                        "scrub": "кустарник", "bridge": "мост", "mountain": "горы",
                        "orchard": "сад",
                    }
                    tname = terrain_names_ru.get(t["type"], t["type"])
                    elev_str = ""
                    elev = situation.get("elevation", {})
                    if elev.get("elevation_m") is not None:
                        elev_str = f", высота {elev['elevation_m']:.0f}м"
                        if elev.get("slope_deg") and elev["slope_deg"] > 5:
                            elev_str += f", уклон {elev['slope_deg']:.0f}°"
                    elif t.get("elevation_m") is not None:
                        elev_str = f", высота {t['elevation_m']:.0f}м"
                    parts.append(f"Местность: {tname}{elev_str}")

                # Surrounding terrain summary
                surr = situation.get("surrounding_terrain")
                if surr:
                    surr_items = sorted(surr.items(), key=lambda x: -x[1])[:3]
                    surr_names = {
                        "road": "дороги", "open": "открытая", "forest": "лес",
                        "urban": "застройка", "water": "вода", "fields": "поля",
                        "marsh": "болото", "scrub": "кустарник", "mountain": "горы",
                    }
                    surr_strs = [surr_names.get(s[0], s[0]) for s in surr_items]
                    parts.append(f"Вокруг: {', '.join(surr_strs)}")

                # Weather
                weather = situation.get("weather")
                if weather:
                    w_parts = []
                    if weather.get("weather"):
                        w_parts.append(str(weather["weather"]))
                    if weather.get("visibility"):
                        w_parts.append(f"видимость: {weather['visibility']}")
                    if weather.get("wind"):
                        w_parts.append(f"ветер: {weather['wind']}")
                    if weather.get("temperature"):
                        w_parts.append(f"температура: {weather['temperature']}")
                    if w_parts:
                        parts.append(f"Погода: {', '.join(w_parts)}")

                # Time of day
                game_time = situation.get("game_time")
                if game_time:
                    period_ru = {
                        "morning": "утро", "afternoon": "день",
                        "evening": "вечер", "night": "ночь",
                    }
                    period_name = period_ru.get(game_time.get("period", ""), "")
                    time_str = f"Ход {game_time.get('tick', '?')}"
                    if game_time.get("hour") is not None:
                        time_str += f", {game_time['hour']:02d}:00"
                    if period_name:
                        time_str += f" ({period_name})"
                    parts.append(time_str)

                # Combat status
                combat_status = situation.get("combat_status")
                combat_ru = {
                    "nominal": None,
                    "light_fire": "лёгкий огневой контакт",
                    "under_fire": "под обстрелом",
                    "heavily_suppressed": "сильно подавлены огнём",
                    "heavy_casualties": "тяжёлые потери",
                    "combat_ineffective": "небоеспособны",
                    "broken": "подразделение разбито",
                    "shaken": "моральный дух подорван",
                }
                cs_text = combat_ru.get(combat_status)
                if cs_text:
                    parts.append(f"Боевая обстановка: {cs_text}")

                # Task with coordinates
                task_info = situation.get("current_task")
                if task_info and task_info.get("target_coordinates"):
                    tc = task_info["target_coordinates"]
                    task_loc = f"Цель задачи: "
                    if task_info.get("target_snail"):
                        task_loc += f"{task_info['target_snail']} "
                    task_loc += f"(коорд. {tc['lat']:.4f}, {tc['lon']:.4f})"
                    if task_info.get("speed_mode"):
                        speed_ru = {"slow": "скрытно", "fast": "быстро"}
                        task_loc += f", режим: {speed_ru.get(task_info['speed_mode'], task_info['speed_mode'])}"
                    parts.append(task_loc)

                # Contacts
                contacts = situation.get("contacts", [])
                if contacts:
                    close = [c for c in contacts if c.get("distance_m", 99999) < 3000]
                    if close:
                        c_descs = []
                        for c in close[:3]:
                            d = c.get("distance_m", "?")
                            ctype = c.get("type", "противник")
                            gref = c.get("grid_ref", "")
                            c_coords = c.get("coordinates")
                            ref_str = ""
                            if gref:
                                ref_str = f" ({gref}"
                                if c_coords:
                                    ref_str += f", {c_coords['lat']:.4f},{c_coords['lon']:.4f}"
                                ref_str += ")"
                            elif c_coords:
                                ref_str = f" ({c_coords['lat']:.4f},{c_coords['lon']:.4f})"
                            bearing = c.get("bearing_deg")
                            bearing_str = f", азимут {bearing}°" if bearing is not None else ""
                            c_descs.append(f"{ctype} ~{d}м{ref_str}{bearing_str}")
                        parts.append(f"Противник: {'; '.join(c_descs)}")
                    else:
                        parts.append("Противника не наблюдаем")
                else:
                    parts.append("Противника не наблюдаем")

                # Nearby map objects (obstacles, structures)
                nearby_objs = situation.get("nearby_objects", [])
                if nearby_objs:
                    obj_names_ru = {
                        "minefield": "минное поле", "barbed_wire": "проволока",
                        "entrenchment": "окопы", "roadblock": "заграждение",
                        "pillbox": "ДОТ", "bridge": "мост",
                        "command_post": "КП", "fuel_depot": "склад ГСМ",
                        "supply_cache": "склад", "observation_tower": "НП",
                        "field_hospital": "госпиталь", "airfield": "аэродром",
                    }
                    close_objs = [o for o in nearby_objs if o.get("distance_m", 99999) < 1500][:3]
                    if close_objs:
                        o_descs = []
                        for o in close_objs:
                            oname = obj_names_ru.get(o["type"], o["type"])
                            o_gref = o.get("grid_ref", "")
                            o_ref = f" ({o_gref})" if o_gref else ""
                            o_descs.append(f"{oname} ~{o['distance_m']}м{o_ref}")
                        parts.append(f"Объекты: {'; '.join(o_descs)}")

                # Nearby friendlies
                friendlies = situation.get("nearby_friendlies", [])
                if friendlies:
                    f_descs = []
                    for f in friendlies[:3]:
                        f_gref = f.get("grid_ref", "")
                        f_ref = f" ({f_gref})" if f_gref else ""
                        f_descs.append(f"{f['name']} ~{f['distance_m']}м{f_ref}")
                    parts.append(f"Рядом свои: {', '.join(f_descs)}")

                # Parent unit
                parent = situation.get("parent_unit")
                if parent:
                    parts.append(f"Подчинены: {parent['name']}")

                # Subordinate summary
                subs = situation.get("subordinate_units", [])
                if subs:
                    sub_strs = [f"{s['name']} ({s['strength']:.0%})" for s in subs[:4]]
                    parts.append(f"Подчинённые: {', '.join(sub_strs)}")

                # Recent events
                events = situation.get("recent_events", [])
                combat_events = [e for e in events if e["type"] in ("combat", "detection", "contact_new", "morale_break")]
                if combat_events:
                    last = combat_events[0]
                    parts.append(f"Последнее: {last.get('summary', last['type'])}")

                if parts:
                    status += " " + ". ".join(parts) + "."
        else:
            # ── English status ──
            task_text = "no task assigned" if not task else f"executing: {task.get('type', '?')}"
            if task and task.get("target_snail"):
                task_text += f", target: {task['target_snail']}"
            status = (
                f"Strength: {strength:.0%}, "
                f"ammo: {ammo:.0%}, "
                f"suppression: {suppression:.0%}. "
                f"Status: {task_text}."
            )
            if morale < 0.3:
                status += " Morale critically low!"
            elif morale < 0.5:
                status += " Morale degraded."

            # Situational awareness
            if situation:
                parts = []

                # Position: coordinates + grid ref
                coords = situation.get("coordinates")
                grid_ref = situation.get("grid_ref")
                if coords and grid_ref:
                    parts.append(f"Position: {grid_ref} (coords {coords['lat']:.4f}, {coords['lon']:.4f})")
                elif grid_ref:
                    parts.append(f"Position: {grid_ref}")
                elif coords:
                    parts.append(f"Position: coords {coords['lat']:.4f}, {coords['lon']:.4f}")

                # Heading
                heading_compass = situation.get("heading_compass")
                if heading_compass:
                    parts.append(f"Facing: {heading_compass} ({situation.get('heading_deg', '?')}°)")

                # Terrain + elevation
                t = situation.get("terrain")
                if t:
                    elev_str = ""
                    elev = situation.get("elevation", {})
                    if elev.get("elevation_m") is not None:
                        elev_str = f", elev {elev['elevation_m']:.0f}m"
                        if elev.get("slope_deg") and elev["slope_deg"] > 5:
                            elev_str += f", slope {elev['slope_deg']:.0f}°"
                    elif t.get("elevation_m") is not None:
                        elev_str = f", elev {t['elevation_m']:.0f}m"
                    parts.append(f"Terrain: {t['type']}{elev_str}")

                # Surrounding terrain summary
                surr = situation.get("surrounding_terrain")
                if surr:
                    surr_items = sorted(surr.items(), key=lambda x: -x[1])[:3]
                    surr_strs = [s[0] for s in surr_items]
                    parts.append(f"Surrounding: {', '.join(surr_strs)}")

                # Weather
                weather = situation.get("weather")
                if weather:
                    w_parts = []
                    if weather.get("weather"):
                        w_parts.append(str(weather["weather"]))
                    if weather.get("visibility"):
                        w_parts.append(f"visibility: {weather['visibility']}")
                    if weather.get("wind"):
                        w_parts.append(f"wind: {weather['wind']}")
                    if weather.get("temperature"):
                        w_parts.append(f"temp: {weather['temperature']}")
                    if w_parts:
                        parts.append(f"Weather: {', '.join(w_parts)}")

                # Time of day
                game_time = situation.get("game_time")
                if game_time:
                    time_str = f"Turn {game_time.get('tick', '?')}"
                    if game_time.get("hour") is not None:
                        time_str += f", {game_time['hour']:02d}:00"
                    period = game_time.get("period")
                    if period:
                        time_str += f" ({period})"
                    parts.append(time_str)

                # Combat status
                combat_status = situation.get("combat_status")
                combat_en = {
                    "nominal": None,
                    "light_fire": "light contact",
                    "under_fire": "under fire",
                    "heavily_suppressed": "heavily suppressed",
                    "heavy_casualties": "heavy casualties",
                    "combat_ineffective": "combat ineffective",
                    "broken": "unit broken",
                    "shaken": "morale shaken",
                }
                cs_text = combat_en.get(combat_status)
                if cs_text:
                    parts.append(f"Situation: {cs_text}")

                # Task with coordinates
                task_info = situation.get("current_task")
                if task_info and task_info.get("target_coordinates"):
                    tc = task_info["target_coordinates"]
                    task_loc = f"Task target: "
                    if task_info.get("target_snail"):
                        task_loc += f"{task_info['target_snail']} "
                    task_loc += f"(coords {tc['lat']:.4f}, {tc['lon']:.4f})"
                    if task_info.get("speed_mode"):
                        task_loc += f", mode: {task_info['speed_mode']}"
                    parts.append(task_loc)

                # Contacts
                contacts = situation.get("contacts", [])
                if contacts:
                    close = [c for c in contacts if c.get("distance_m", 99999) < 3000]
                    if close:
                        c_descs = []
                        for c in close[:3]:
                            d = c.get("distance_m", "?")
                            ctype = c.get("type", "enemy")
                            gref = c.get("grid_ref", "")
                            c_coords = c.get("coordinates")
                            ref_str = ""
                            if gref:
                                ref_str = f" ({gref}"
                                if c_coords:
                                    ref_str += f", {c_coords['lat']:.4f},{c_coords['lon']:.4f}"
                                ref_str += ")"
                            elif c_coords:
                                ref_str = f" ({c_coords['lat']:.4f},{c_coords['lon']:.4f})"
                            bearing = c.get("bearing_deg")
                            bearing_str = f", bearing {bearing}°" if bearing is not None else ""
                            c_descs.append(f"{ctype} ~{d}m{ref_str}{bearing_str}")
                        parts.append(f"Contacts: {'; '.join(c_descs)}")
                    else:
                        parts.append("No enemy contacts")
                else:
                    parts.append("No enemy contacts")

                # Nearby map objects (obstacles, structures)
                nearby_objs = situation.get("nearby_objects", [])
                if nearby_objs:
                    close_objs = [o for o in nearby_objs if o.get("distance_m", 99999) < 1500][:3]
                    if close_objs:
                        o_descs = []
                        for o in close_objs:
                            o_gref = o.get("grid_ref", "")
                            o_ref = f" ({o_gref})" if o_gref else ""
                            o_descs.append(f"{o['type']} ~{o['distance_m']}m{o_ref}")
                        parts.append(f"Objects: {'; '.join(o_descs)}")

                # Nearby friendlies
                friendlies = situation.get("nearby_friendlies", [])
                if friendlies:
                    f_descs = []
                    for f in friendlies[:3]:
                        f_gref = f.get("grid_ref", "")
                        f_ref = f" ({f_gref})" if f_gref else ""
                        f_descs.append(f"{f['name']} ~{f['distance_m']}m{f_ref}")
                    parts.append(f"Friendlies nearby: {', '.join(f_descs)}")

                # Parent unit
                parent = situation.get("parent_unit")
                if parent:
                    parts.append(f"Reporting to: {parent['name']}")

                # Subordinate summary
                subs = situation.get("subordinate_units", [])
                if subs:
                    sub_strs = [f"{s['name']} ({s['strength']:.0%})" for s in subs[:4]]
                    parts.append(f"Subordinates: {', '.join(sub_strs)}")

                # Recent events
                events = situation.get("recent_events", [])
                combat_events = [e for e in events if e["type"] in ("combat", "detection", "contact_new", "morale_break")]
                if combat_events:
                    last = combat_events[0]
                    parts.append(f"Recent: {last.get('summary', last['type'])}")

                if parts:
                    status += " " + ". ".join(parts) + "."

        return status

    def generate_brief_sitrep(
        self,
        unit: dict,
        language: str = "en",
        situation: dict | None = None,
    ) -> str:
        """Build a brief situation line for ack/wilco responses (position + key threats)."""
        if not situation:
            return ""

        if language == "ru":
            parts = []
            # Position (coordinates + grid)
            coords = situation.get("coordinates")
            grid_ref = situation.get("grid_ref")
            if coords and grid_ref:
                parts.append(f"Находимся: {grid_ref} ({coords['lat']:.4f}, {coords['lon']:.4f})")
            elif grid_ref:
                parts.append(f"Находимся: {grid_ref}")
            elif coords:
                parts.append(f"Находимся: {coords['lat']:.4f}, {coords['lon']:.4f}")

            # Terrain
            t = situation.get("terrain")
            if t:
                terrain_names_ru = {
                    "road": "дорога", "open": "открытая", "forest": "лес",
                    "urban": "город", "water": "вода", "fields": "поля",
                    "marsh": "болото", "scrub": "кустарник", "mountain": "горы",
                }
                parts.append(terrain_names_ru.get(t["type"], t["type"]))

            # Combat status if not nominal
            cs = situation.get("combat_status")
            combat_ru = {
                "light_fire": "лёгкий контакт",
                "under_fire": "под обстрелом",
                "heavily_suppressed": "подавлены",
            }
            if cs and cs in combat_ru:
                parts.append(combat_ru[cs])

            # Closest enemy
            contacts = situation.get("contacts", [])
            close = [c for c in contacts if c.get("distance_m", 99999) < 2000]
            if close:
                c = close[0]
                parts.append(f"противник ~{c.get('distance_m', '?')}м")
            return ". ".join(parts) if parts else ""
        else:
            parts = []
            coords = situation.get("coordinates")
            grid_ref = situation.get("grid_ref")
            if coords and grid_ref:
                parts.append(f"At {grid_ref} ({coords['lat']:.4f}, {coords['lon']:.4f})")
            elif grid_ref:
                parts.append(f"At {grid_ref}")
            elif coords:
                parts.append(f"At {coords['lat']:.4f}, {coords['lon']:.4f}")

            t = situation.get("terrain")
            if t:
                parts.append(t["type"])

            cs = situation.get("combat_status")
            combat_en = {
                "light_fire": "light contact",
                "under_fire": "under fire",
                "heavily_suppressed": "suppressed",
            }
            if cs and cs in combat_en:
                parts.append(combat_en[cs])

            contacts = situation.get("contacts", [])
            close = [c for c in contacts if c.get("distance_m", 99999) < 2000]
            if close:
                c = close[0]
                parts.append(f"enemy ~{c.get('distance_m', '?')}m")
            return ". ".join(parts) if parts else ""


# Singleton
response_generator = ResponseGenerator()

