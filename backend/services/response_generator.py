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
        support_target: str = "",
    ) -> UnitRadioResponse | None:
        """
        Generate a template-based radio response from a unit.

        Args:
            parsed: The parsed order/message.
            unit: Unit dict (name, id, unit_type, strength, morale, comms_status, etc.).
            response_type: Type of response to generate.
            reason_key: Key for inability reason (destroyed, morale_broken, no_ammo, etc.).
            status_text: Status text to include in status responses.
            support_target: Name of the unit to support (for standby orders).

        Returns:
            UnitRadioResponse or None (for no_response).
        """
        language = parsed.language.value
        unit_name = unit.get("name", "Unknown Unit")

        # Resolve support_target from parsed data if not provided
        if not support_target and response_type == ResponseType.wilco_standby:
            support_target = getattr(parsed, "support_target_ref", "") or ""

        text = get_template_response(
            unit_name=unit_name,
            response_type=response_type.value,
            language=language,
            reason_key=reason_key,
            status_text=status_text,
            support_target=support_target,
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
        if ammo <= 0 and parsed.order_type and parsed.order_type.value in ("attack", "fire"):
            return ResponseType.unable, "no_ammo"

        # Heavy casualties
        strength = unit.get("strength", 1.0)
        if strength < 0.25 and parsed.order_type and parsed.order_type.value in ("attack", "fire", "move"):
            return ResponseType.unable, "heavy_casualties"

        # Normal acknowledgment
        if parsed.classification == MessageClassification.command:
            order_type = parsed.order_type.value if parsed.order_type else ""

            # Fire mission for artillery/mortar → use fire-specific response
            # BUT only if the order is actually a fire order, not observe/standby
            unit_type = unit.get("unit_type", "")
            is_fire_unit = unit_type in (
                "artillery_battery", "artillery_platoon",
                "mortar_section", "mortar_team",
            )
            if is_fire_unit and order_type == "fire":
                return ResponseType.wilco_fire, None
            # Request fire support (non-artillery unit directing CoC fire)
            if order_type == "request_fire":
                return ResponseType.wilco_request_fire, None
            # Disengage/Withdraw order → use disengage-specific response
            if order_type in ("disengage", "withdraw"):
                return ResponseType.wilco_disengage, None
            # Resupply order → use resupply-specific response
            if order_type == "resupply":
                return ResponseType.wilco_resupply, None
            # Observe/standby order for artillery with support_target → standby response
            if order_type == "observe" and is_fire_unit:
                support_target = getattr(parsed, "support_target_ref", None)
                if support_target:
                    return ResponseType.wilco_standby, None
                return ResponseType.wilco_observe, None
            # Static orders (defend, observe, halt) → stationary response (no "moving out")
            if order_type in ("defend", "observe", "halt"):
                return ResponseType.wilco_observe, None
            return ResponseType.wilco, None

        return ResponseType.ack, None

    def generate_status_report(
        self,
        unit: dict,
        language: str = "en",
        situation: dict | None = None,
        request_focus: list[str] | None = None,
    ) -> str:
        """Build a status text string for a unit, including rich situational awareness."""
        name = unit.get("name", "Unknown")
        strength = unit.get("strength", 1.0)
        morale = unit.get("morale", 1.0)
        ammo = unit.get("ammo", 1.0)
        suppression = unit.get("suppression", 0.0)
        task = unit.get("current_task")
        request_focus = [f for f in (request_focus or []) if f]

        if request_focus and "full" not in request_focus:
            if language == "ru":
                return self._generate_focused_status_report_ru(unit, situation or {}, request_focus)
            return self._generate_focused_status_report_en(unit, situation or {}, request_focus)

        if language == "ru":
            return self._generate_status_report_ru(
                unit, strength, morale, ammo, suppression, task, situation
            )
        else:
            return self._generate_status_report_en(
                unit, strength, morale, ammo, suppression, task, situation
            )

    def _generate_focused_status_report_ru(
        self,
        unit: dict,
        situation: dict,
        request_focus: list[str],
    ) -> str:
        """Generate a focused Russian reply for specific status questions."""
        parts: list[str] = []
        seen: set[str] = set()

        def add(text: str):
            if text and text not in seen:
                seen.add(text)
                parts.append(text)

        grid_ref = situation.get("grid_ref")
        coords = situation.get("coordinates") or {}
        task = unit.get("current_task") or {}

        for focus in request_focus:
            if focus == "position":
                if grid_ref:
                    add(f"нахожусь квадрат {grid_ref}")
                elif coords.get("lat") is not None and coords.get("lon") is not None:
                    add(f"нахожусь координаты {coords['lat']:.4f}, {coords['lon']:.4f}")
                else:
                    add("точную позицию определить не могу")
            elif focus == "nearby_friendlies":
                friendlies = situation.get("nearby_friendlies", [])
                if friendlies:
                    desc = []
                    for f in friendlies[:3]:
                        item = f"{f['name']} ~{f['distance_m']}м"
                        if f.get("grid_ref"):
                            item += f" ({f['grid_ref']})"
                        desc.append(item)
                    add("рядом свои: " + "; ".join(desc))
                else:
                    add("своих подразделений в радиусе 2 км не наблюдаю")
            elif focus == "terrain":
                terrain_type = situation.get("terrain_type")
                terrain_name = self.TERRAIN_NAMES_RU.get(terrain_type, terrain_type or "неизвестно")
                terrain_bits = []
                if terrain_name:
                    terrain_bits.append(f"местность: {terrain_name}")
                elevation_m = situation.get("elevation_m")
                if elevation_m is not None:
                    terrain_bits.append(f"высота {elevation_m:.0f}м")
                slope_deg = (situation.get("terrain") or {}).get("slope_deg")
                if slope_deg is not None:
                    terrain_bits.append(f"уклон {slope_deg:.1f}°")
                if terrain_bits:
                    add(", ".join(terrain_bits))

                surrounding = situation.get("surrounding_terrain", {})
                if surrounding:
                    top = sorted(surrounding.items(), key=lambda x: (-x[1], x[0]))[:3]
                    add("вокруг: " + ", ".join(
                        f"{self.TERRAIN_SHORT_RU.get(k, k)} x{v}" for k, v in top
                    ))

                nearby_objects = situation.get("nearby_objects", [])
                if nearby_objects:
                    close = []
                    for obj in nearby_objects[:3]:
                        name = obj.get("label") or self.OBJ_NAMES_RU.get(obj.get("type"), obj.get("type", "объект"))
                        close.append(f"{name} ~{obj['distance_m']}м")
                    add("рядом объекты: " + "; ".join(close))
            elif focus == "enemy":
                contacts = situation.get("contacts", [])
                if contacts:
                    desc = []
                    for c in contacts[:3]:
                        item = self._translate_unit_type(c.get("type", "противник"), "ru")
                        if c.get("distance_m") is not None:
                            item += f" ~{c['distance_m']}м"
                        if c.get("grid_ref"):
                            item += f" ({c['grid_ref']})"
                        desc.append(item)
                    add("противник: " + "; ".join(desc))
                else:
                    add("противника не наблюдаю")
            elif focus == "task":
                task_type = task.get("type")
                if task_type:
                    task_name = self.TASK_TYPES_RU.get(task_type, task_type)
                    if task.get("target_snail"):
                        add(f"текущая задача: {task_name}, район {task['target_snail']}")
                    else:
                        add(f"текущая задача: {task_name}")
                else:
                    add("текущей задачи нет")
            elif focus == "condition":
                condition = []
                condition.append(self._strength_desc(unit.get("strength", 1.0), "ru"))
                ammo_desc = self._ammo_desc(unit.get("ammo", 1.0), "ru")
                if ammo_desc:
                    condition.append(ammo_desc)
                morale_desc = self._morale_desc(unit.get("morale", 1.0), "ru")
                if morale_desc:
                    condition.append(morale_desc)
                combat_status = situation.get("combat_status")
                cs_text = self.COMBAT_STATUS_RU.get(combat_status) if combat_status else None
                if cs_text:
                    condition.append(cs_text)
                add("состояние: " + ", ".join([c for c in condition if c]))
            elif focus == "weather":
                weather = situation.get("weather", {})
                if weather:
                    bits = []
                    for key in ("weather", "visibility", "wind", "precipitation", "light_level", "temperature"):
                        val = weather.get(key)
                        if val is not None and val != "":
                            if isinstance(val, str):
                                val = self._translate_weather_val(val)
                            bits.append(f"{key}={val}")
                    if bits:
                        add("условия: " + ", ".join(bits))
                else:
                    add("данных по погоде нет")
            elif focus == "objects":
                nearby_objects = situation.get("nearby_objects", [])
                if nearby_objects:
                    desc = []
                    for obj in nearby_objects[:4]:
                        name = obj.get("label") or self.OBJ_NAMES_RU.get(obj.get("type"), obj.get("type", "объект"))
                        desc.append(f"{name} ~{obj['distance_m']}м")
                    add("объекты рядом: " + "; ".join(desc))
                else:
                    add("заметных объектов рядом нет")

        if not parts:
            return self._generate_status_report_ru(
                unit,
                unit.get("strength", 1.0),
                unit.get("morale", 1.0),
                unit.get("ammo", 1.0),
                unit.get("suppression", 0.0),
                unit.get("current_task"),
                situation,
            )

        result = ". ".join(p[0].upper() + p[1:] if p else p for p in parts if p)
        return result + ". Приём."

    def _generate_focused_status_report_en(
        self,
        unit: dict,
        situation: dict,
        request_focus: list[str],
    ) -> str:
        """Generate a focused English reply for specific status questions."""
        parts: list[str] = []
        seen: set[str] = set()

        def add(text: str):
            if text and text not in seen:
                seen.add(text)
                parts.append(text)

        grid_ref = situation.get("grid_ref")
        coords = situation.get("coordinates") or {}
        task = unit.get("current_task") or {}

        for focus in request_focus:
            if focus == "position":
                if grid_ref:
                    add(f"position grid {grid_ref}")
                elif coords.get("lat") is not None and coords.get("lon") is not None:
                    add(f"position {coords['lat']:.4f}, {coords['lon']:.4f}")
                else:
                    add("unable to determine precise position")
            elif focus == "nearby_friendlies":
                friendlies = situation.get("nearby_friendlies", [])
                if friendlies:
                    desc = []
                    for f in friendlies[:3]:
                        item = f"{f['name']} ~{f['distance_m']}m"
                        if f.get("grid_ref"):
                            item += f" ({f['grid_ref']})"
                        desc.append(item)
                    add("friendlies nearby: " + "; ".join(desc))
                else:
                    add("no friendly units observed within 2 km")
            elif focus == "terrain":
                terrain_type = situation.get("terrain_type", "unknown")
                terrain_bits = [f"terrain: {terrain_type.replace('_', ' ')}"]
                elevation_m = situation.get("elevation_m")
                if elevation_m is not None:
                    terrain_bits.append(f"elevation {elevation_m:.0f}m")
                slope_deg = (situation.get("terrain") or {}).get("slope_deg")
                if slope_deg is not None:
                    terrain_bits.append(f"slope {slope_deg:.1f}°")
                add(", ".join(terrain_bits))

                surrounding = situation.get("surrounding_terrain", {})
                if surrounding:
                    top = sorted(surrounding.items(), key=lambda x: (-x[1], x[0]))[:3]
                    add("surroundings: " + ", ".join(
                        f"{k.replace('_', ' ')} x{v}" for k, v in top
                    ))

                nearby_objects = situation.get("nearby_objects", [])
                if nearby_objects:
                    close = []
                    for obj in nearby_objects[:3]:
                        name = obj.get("label") or obj.get("type", "object").replace("_", " ")
                        close.append(f"{name} ~{obj['distance_m']}m")
                    add("nearby objects: " + "; ".join(close))
            elif focus == "enemy":
                contacts = situation.get("contacts", [])
                if contacts:
                    desc = []
                    for c in contacts[:3]:
                        item = self._translate_unit_type(c.get("type", "enemy"), "en")
                        if c.get("distance_m") is not None:
                            item += f" ~{c['distance_m']}m"
                        if c.get("grid_ref"):
                            item += f" ({c['grid_ref']})"
                        desc.append(item)
                    add("enemy: " + "; ".join(desc))
                else:
                    add("no enemy observed")
            elif focus == "task":
                task_type = task.get("type")
                if task_type:
                    if task.get("target_snail"):
                        add(f"current task: {task_type}, grid {task['target_snail']}")
                    else:
                        add(f"current task: {task_type}")
                else:
                    add("no current task")
            elif focus == "condition":
                condition = [self._strength_desc(unit.get("strength", 1.0), "en")]
                ammo_desc = self._ammo_desc(unit.get("ammo", 1.0), "en")
                if ammo_desc:
                    condition.append(ammo_desc)
                morale_desc = self._morale_desc(unit.get("morale", 1.0), "en")
                if morale_desc:
                    condition.append(morale_desc)
                combat_status = situation.get("combat_status")
                cs_text = self.COMBAT_STATUS_EN.get(combat_status) if combat_status else None
                if cs_text:
                    condition.append(cs_text)
                add("condition: " + ", ".join([c for c in condition if c]))
            elif focus == "weather":
                weather = situation.get("weather", {})
                if weather:
                    bits = []
                    for key in ("weather", "visibility", "wind", "precipitation", "light_level", "temperature"):
                        val = weather.get(key)
                        if val is not None and val != "":
                            bits.append(f"{key}={val}")
                    if bits:
                        add("conditions: " + ", ".join(bits))
                else:
                    add("no weather data available")
            elif focus == "objects":
                nearby_objects = situation.get("nearby_objects", [])
                if nearby_objects:
                    desc = []
                    for obj in nearby_objects[:4]:
                        name = obj.get("label") or obj.get("type", "object").replace("_", " ")
                        desc.append(f"{name} ~{obj['distance_m']}m")
                    add("objects nearby: " + "; ".join(desc))
                else:
                    add("no significant nearby objects")

        if not parts:
            return self._generate_status_report_en(
                unit,
                unit.get("strength", 1.0),
                unit.get("morale", 1.0),
                unit.get("ammo", 1.0),
                unit.get("suppression", 0.0),
                unit.get("current_task"),
                situation,
            )

        result = ". ".join(p[0].upper() + p[1:] if p else p for p in parts if p)
        return result + ". Over."

    # ── Translation dictionaries ──

    TASK_TYPES_RU = {
        "move": "марш", "attack": "атака", "engage": "огневой контакт",
        "fire": "огонь", "defend": "оборона", "observe": "наблюдение",
        "halt": "остановка", "retreat": "отход", "withdraw": "отступление",
        "disengage": "разрыв контакта",
        "advance": "выдвижение", "dig_in": "окапывание", "support": "поддержка",
    }

    WEATHER_RU = {
        "clear": "ясно", "overcast": "облачно", "cloudy": "облачно",
        "rain": "дождь", "heavy_rain": "ливень", "fog": "туман",
        "snow": "снег", "storm": "шторм", "haze": "дымка",
        "good": "хорошая", "moderate": "умеренная", "poor": "плохая",
        "very_poor": "очень плохая", "light": "слабый", "moderate_wind": "умеренный",
        "strong": "сильный", "calm": "штиль",
    }

    UNIT_TYPES_RU = {
        "infantry_platoon": "пех. взвод", "infantry_company": "пех. рота",
        "infantry_section": "пех. отделение", "infantry_squad": "пех. отделение",
        "infantry_team": "пех. группа", "infantry_battalion": "пех. батальон",
        "mech_platoon": "мех. взвод", "mech_company": "мех. рота",
        "tank_platoon": "танк. взвод", "tank_company": "танк. рота",
        "artillery_battery": "арт. батарея", "artillery_platoon": "арт. взвод",
        "mortar_section": "минометное отделение", "mortar_team": "минометная группа",
        "at_team": "ПТ группа", "recon_team": "разведгруппа",
        "recon_section": "разведотделение", "observation_post": "НП",
        "sniper_team": "снайперская пара", "headquarters": "штаб",
        "command_post": "КП", "logistics_unit": "тыловое подразделение",
        "combat_engineer_platoon": "инж. взвод", "engineer_platoon": "инж. взвод",
    }

    TERRAIN_NAMES_RU = {
        "road": "дорога", "open": "открытая местность",
        "forest": "лес", "urban": "город", "water": "вода",
        "fields": "поля", "marsh": "болото", "desert": "пустыня",
        "scrub": "кустарник", "bridge": "мост", "mountain": "горы",
        "orchard": "сад",
    }

    TERRAIN_SHORT_RU = {
        "road": "дороги", "open": "открытая", "forest": "лес",
        "urban": "застройка", "water": "вода", "fields": "поля",
        "marsh": "болото", "scrub": "кустарник", "mountain": "горы",
    }

    COMBAT_STATUS_RU = {
        "nominal": None,
        "light_fire": "лёгкий огневой контакт",
        "under_fire": "под обстрелом",
        "heavily_suppressed": "сильно подавлены огнём",
        "heavy_casualties": "тяжёлые потери",
        "combat_ineffective": "небоеспособны",
        "broken": "подразделение разбито",
        "shaken": "моральный дух подорван",
    }

    COMBAT_STATUS_EN = {
        "nominal": None,
        "light_fire": "light contact",
        "under_fire": "under fire",
        "heavily_suppressed": "heavily suppressed",
        "heavy_casualties": "heavy casualties",
        "combat_ineffective": "combat ineffective",
        "broken": "unit broken",
        "shaken": "morale shaken",
    }

    OBJ_NAMES_RU = {
        "minefield": "минное поле", "barbed_wire": "проволока",
        "entrenchment": "окопы", "roadblock": "заграждение",
        "pillbox": "ДОТ", "bridge": "мост",
        "command_post": "КП", "fuel_depot": "склад ГСМ",
        "supply_cache": "склад", "observation_tower": "НП",
        "field_hospital": "госпиталь", "airfield": "аэродром",
    }

    COMPASS_RU = {
        "N": "С", "NNE": "ССВ", "NE": "СВ", "ENE": "ВСВ",
        "E": "В", "ESE": "ВЮВ", "SE": "ЮВ", "SSE": "ЮЮВ",
        "S": "Ю", "SSW": "ЮЮЗ", "SW": "ЮЗ", "WSW": "ЗЮЗ",
        "W": "З", "WNW": "ЗСЗ", "NW": "СЗ", "NNW": "ССЗ",
    }

    PERIOD_RU = {
        "morning": "утро", "afternoon": "день",
        "evening": "вечер", "night": "ночь",
    }

    def _translate_weather_val(self, val: str) -> str:
        """Translate a weather value to Russian, or return as-is."""
        if not val:
            return val
        return self.WEATHER_RU.get(str(val).lower(), str(val))

    def _translate_unit_type(self, utype: str, lang: str = "ru") -> str:
        """Translate a unit type key to a readable name."""
        if lang == "ru":
            return self.UNIT_TYPES_RU.get(utype, utype)
        return utype.replace("_", " ")

    # ── Natural language descriptors ──

    @staticmethod
    def _strength_desc(val, lang):
        if lang == "ru":
            if val >= 0.9: return "полный состав"
            if val >= 0.7: return "незначительные потери"
            if val >= 0.5: return "умеренные потери"
            if val >= 0.3: return "тяжёлые потери"
            return "критические потери"
        else:
            if val >= 0.9: return "full strength"
            if val >= 0.7: return "minor casualties"
            if val >= 0.5: return "moderate casualties"
            if val >= 0.3: return "heavy casualties"
            return "critical casualties"

    @staticmethod
    def _ammo_desc(val, lang):
        if lang == "ru":
            if val >= 0.7: return None  # don't mention if OK
            if val >= 0.4: return "бк ниже нормы"
            if val >= 0.2: return "бк на исходе"
            return "бк практически нет"
        else:
            if val >= 0.7: return None
            if val >= 0.4: return "ammo below normal"
            if val >= 0.2: return "running low on ammo"
            return "ammo critical"

    @staticmethod
    def _morale_desc(val, lang):
        if lang == "ru":
            if val >= 0.7: return None  # don't mention if OK
            if val >= 0.5: return "дух снижен"
            if val >= 0.3: return "дух подорван"
            return "подразделение деморализовано"
        else:
            if val >= 0.7: return None
            if val >= 0.5: return "morale degraded"
            if val >= 0.3: return "morale shaken"
            return "unit demoralized"

    def _generate_status_report_ru(
        self, unit, strength, morale, ammo, suppression, task, situation
    ) -> str:
        """Russian status report — natural military radio style."""
        name = unit.get("name", "Unknown")
        parts = []

        # Position (always first in a sitrep)
        if situation:
            grid_ref = situation.get("grid_ref")
            if grid_ref:
                parts.append(f"находимся квадрат {grid_ref}")

        # Task status in natural language
        if task:
            task_type_raw = task.get("type", "")
            task_type = self.TASK_TYPES_RU.get(task_type_raw, task_type_raw)
            target_snail = task.get("target_snail")
            if task_type_raw in ("move", "advance"):
                parts.append(f"выдвигаемся" + (f" на {target_snail}" if target_snail else ""))
            elif task_type_raw in ("attack", "engage"):
                parts.append(f"ведём бой" + (f", район {target_snail}" if target_snail else ""))
            elif task_type_raw in ("fire", "support"):
                parts.append("ведём огневую поддержку" + (f", район {target_snail}" if target_snail else ""))
            elif task_type_raw == "defend":
                parts.append("обороняем позиции")
            elif task_type_raw == "observe":
                parts.append("ведём наблюдение")
            else:
                parts.append(f"выполняем: {task_type}")
        else:
            parts.append("на месте, без задачи")

        # Combat status
        if situation:
            combat_status = situation.get("combat_status")
            cs_text = self.COMBAT_STATUS_RU.get(combat_status) if combat_status else None
            if cs_text:
                parts.append(cs_text)

        # Unit condition — natural language, only notable things
        condition_parts = []
        s_desc = self._strength_desc(strength, "ru")
        if strength < 0.9:  # only mention if not full
            condition_parts.append(s_desc)
        a_desc = self._ammo_desc(ammo, "ru")
        if a_desc:
            condition_parts.append(a_desc)
        m_desc = self._morale_desc(morale, "ru")
        if m_desc:
            condition_parts.append(m_desc)
        if suppression > 0.3:
            condition_parts.append("прижаты огнём" if suppression > 0.6 else "под обстрелом")

        if condition_parts:
            parts.append(", ".join(condition_parts))

        if not situation:
            return ". ".join(p[0].upper() + p[1:] if p else p for p in parts) + ". Приём."

        # Contacts — most critical intel
        contacts = situation.get("contacts", [])
        if contacts:
            close = [c for c in contacts if c.get("distance_m", 99999) < 3000]
            if close:
                c_descs = []
                for c in close[:3]:
                    d = c.get("distance_m", "?")
                    ctype_raw = c.get("type", "противник")
                    ctype = self._translate_unit_type(ctype_raw, "ru")
                    bearing = c.get("bearing_deg")
                    gref = c.get("grid_ref", "")
                    bearing_str = f", азимут {bearing}°" if bearing is not None else ""
                    ref_str = f" ({gref})" if gref else ""
                    c_descs.append(f"{ctype} ~{d}м{ref_str}{bearing_str}")
                parts.append(f"противник: {'; '.join(c_descs)}")
            else:
                parts.append("противника не наблюдаем")
        else:
            parts.append("противника не наблюдаем")

        # Nearby obstacles (brief, only if close)
        nearby_objs = situation.get("nearby_objects", [])
        if nearby_objs:
            close_objs = [o for o in nearby_objs if o.get("distance_m", 99999) < 500][:2]
            if close_objs:
                o_descs = []
                for o in close_objs:
                    oname = self.OBJ_NAMES_RU.get(o["type"], o["type"])
                    o_descs.append(f"{oname} ~{o['distance_m']}м")
                parts.append(f"внимание: {'; '.join(o_descs)}")

        # Nearby friendlies (only in combat or weakened)
        combat_status = situation.get("combat_status")
        if (combat_status and combat_status not in ("nominal",)) or strength < 0.5:
            friendlies = situation.get("nearby_friendlies", [])
            if friendlies:
                f_descs = [f"{f['name']} ~{f['distance_m']}м" for f in friendlies[:2]]
                parts.append(f"свои рядом: {', '.join(f_descs)}")

        # Recent combat events (brief) — skip stale combat events against destroyed targets
        events = situation.get("recent_events", [])
        combat_events = [e for e in events if e["type"] in ("contact_new", "morale_break", "unit_destroyed")]
        if combat_events:
            last = combat_events[0]
            parts.append(last.get("summary", last["type"]))

        result = ". ".join(p[0].upper() + p[1:] if p else p for p in parts if p)
        return result + ". Приём."

    def _generate_status_report_en(
        self, unit, strength, morale, ammo, suppression, task, situation
    ) -> str:
        """English status report — natural military radio style."""
        name = unit.get("name", "Unknown")
        parts = []

        # Position
        if situation:
            grid_ref = situation.get("grid_ref")
            if grid_ref:
                parts.append(f"at grid {grid_ref}")

        # Task status
        if task:
            task_type = task.get("type", "")
            target_snail = task.get("target_snail")
            if task_type in ("move", "advance"):
                parts.append(f"moving" + (f" to {target_snail}" if target_snail else ""))
            elif task_type in ("attack", "engage"):
                parts.append(f"in contact" + (f", grid {target_snail}" if target_snail else ""))
            elif task_type in ("fire", "support"):
                parts.append("providing fire support" + (f", grid {target_snail}" if target_snail else ""))
            elif task_type == "defend":
                parts.append("holding position")
            elif task_type == "observe":
                parts.append("observing")
            else:
                parts.append(f"executing: {task_type}")
        else:
            parts.append("stationary, no orders")

        # Combat status
        if situation:
            combat_status = situation.get("combat_status")
            cs_text = self.COMBAT_STATUS_EN.get(combat_status) if combat_status else None
            if cs_text:
                parts.append(cs_text)

        # Unit condition
        condition_parts = []
        s_desc = self._strength_desc(strength, "en")
        if strength < 0.9:
            condition_parts.append(s_desc)
        a_desc = self._ammo_desc(ammo, "en")
        if a_desc:
            condition_parts.append(a_desc)
        m_desc = self._morale_desc(morale, "en")
        if m_desc:
            condition_parts.append(m_desc)
        if suppression > 0.3:
            condition_parts.append("pinned down" if suppression > 0.6 else "taking fire")

        if condition_parts:
            parts.append(", ".join(condition_parts))

        if not situation:
            return ". ".join(p.capitalize() if p else p for p in parts) + ". Over."

        # Contacts
        contacts = situation.get("contacts", [])
        if contacts:
            close = [c for c in contacts if c.get("distance_m", 99999) < 3000]
            if close:
                c_descs = []
                for c in close[:3]:
                    d = c.get("distance_m", "?")
                    ctype = self._translate_unit_type(c.get("type", "enemy"), "en")
                    bearing = c.get("bearing_deg")
                    gref = c.get("grid_ref", "")
                    bearing_str = f", bearing {bearing}°" if bearing is not None else ""
                    ref_str = f" ({gref})" if gref else ""
                    c_descs.append(f"{ctype} ~{d}m{ref_str}{bearing_str}")
                parts.append(f"enemy: {'; '.join(c_descs)}")
            else:
                parts.append("no enemy contact")
        else:
            parts.append("no enemy contact")

        # Nearby obstacles
        nearby_objs = situation.get("nearby_objects", [])
        if nearby_objs:
            close_objs = [o for o in nearby_objs if o.get("distance_m", 99999) < 500][:2]
            if close_objs:
                o_descs = [f"{o['type'].replace('_',' ')} ~{o['distance_m']}m" for o in close_objs]
                parts.append(f"be advised: {'; '.join(o_descs)}")

        # Nearby friendlies (in combat or weakened)
        combat_status = situation.get("combat_status")
        if (combat_status and combat_status not in ("nominal",)) or strength < 0.5:
            friendlies = situation.get("nearby_friendlies", [])
            if friendlies:
                f_descs = [f"{f['name']} ~{f['distance_m']}m" for f in friendlies[:2]]
                parts.append(f"friendlies nearby: {', '.join(f_descs)}")

        # Recent combat events — skip raw 'combat' events (stale engagement data)
        events = situation.get("recent_events", [])
        combat_events = [e for e in events if e["type"] in ("contact_new", "morale_break", "unit_destroyed")]
        if combat_events:
            last = combat_events[0]
            parts.append(last.get("summary", last["type"]))

        result = ". ".join(p[0].upper() + p[1:] if p else p for p in parts if p)
        return result + ". Over."

    def generate_brief_sitrep(
        self,
        unit: dict,
        language: str = "en",
        situation: dict | None = None,
    ) -> str:
        """Build a brief situation line for ack/wilco responses (position + key threats + tactical assessment)."""
        if not situation:
            return ""

        if language == "ru":
            parts = []
            grid_ref = situation.get("grid_ref")
            if grid_ref:
                parts.append(f"квадрат {grid_ref}")

            cs = situation.get("combat_status")
            cs_text = self.COMBAT_STATUS_RU.get(cs) if cs else None
            if cs_text:
                parts.append(cs_text)

            contacts = situation.get("contacts", [])
            close = [c for c in contacts if c.get("distance_m", 99999) < 2000]
            if close:
                c = close[0]
                ctype = self._translate_unit_type(c.get("type", "противник"), "ru")
                parts.append(f"{ctype} ~{c.get('distance_m', '?')}м")

            # Tactical assessment based on terrain and situation
            tactical = self._tactical_assessment(unit, situation, "ru")
            if tactical:
                parts.append(tactical)

            return ". ".join(parts) if parts else ""
        else:
            parts = []
            grid_ref = situation.get("grid_ref")
            if grid_ref:
                parts.append(f"grid {grid_ref}")

            cs = situation.get("combat_status")
            cs_text = self.COMBAT_STATUS_EN.get(cs) if cs else None
            if cs_text:
                parts.append(cs_text)

            contacts = situation.get("contacts", [])
            close = [c for c in contacts if c.get("distance_m", 99999) < 2000]
            if close:
                c = close[0]
                ctype = self._translate_unit_type(c.get("type", "enemy"), "en")
                parts.append(f"{ctype} ~{c.get('distance_m', '?')}m")

            # Tactical assessment
            tactical = self._tactical_assessment(unit, situation, "en")
            if tactical:
                parts.append(tactical)

            return ". ".join(parts) if parts else ""

    def _tactical_assessment(
        self,
        unit: dict,
        situation: dict,
        lang: str,
    ) -> str:
        """
        Generate a short tactical observation based on field manual doctrine.
        Considers: terrain, enemy proximity, task type, unit strength, elevation.
        """
        task = unit.get("current_task") or {}
        task_type = task.get("type", "")
        terrain_type = situation.get("terrain_type", "open")
        elevation_m = situation.get("elevation_m")
        contacts = situation.get("contacts", [])
        close_enemies = [c for c in contacts if c.get("distance_m", 99999) < 2000]
        strength = unit.get("strength", 1.0)
        unit_type = unit.get("unit_type", "")

        # Tactical assessment based on context
        if lang == "ru":
            # Moving through open terrain toward enemy — warn about exposure
            if task_type in ("move", "advance") and terrain_type == "open" and close_enemies:
                return "внимание: открытая местность, противник рядом — рекомендую перестроение"
            # In forest — good concealment
            if task_type in ("move", "advance") and terrain_type == "forest":
                return "местность закрытая, обеспечивает маскировку"
            # Attacking from low ground
            if task_type in ("attack", "engage") and elevation_m is not None:
                for c in close_enemies:
                    c_elev = c.get("elevation_m")
                    if c_elev and c_elev > elevation_m + 30:
                        return "противник на господствующей высоте — ожидаем усиленное сопротивление"
            # Defending in good terrain
            if task_type == "defend" and terrain_type in ("urban", "forest"):
                return "местность благоприятна для обороны"
            # Defending in open terrain
            if task_type == "defend" and terrain_type == "open":
                return "открытая позиция — необходимо окапывание"
            # Heavy casualties — tactical warning
            if strength < 0.4 and close_enemies:
                return "необходимо подкрепление или отход"
            # Recon unit near enemy — stay concealed
            if unit_type in ("recon_team", "recon_section", "sniper_team", "observation_post"):
                if close_enemies:
                    return "наблюдаем противника, сохраняем скрытность"
            # High ground advantage
            if elevation_m is not None and elevation_m > 200:
                for c in close_enemies:
                    c_elev = c.get("elevation_m")
                    if c_elev and elevation_m > c_elev + 30:
                        return "занимаем господствующую высоту"
        else:
            if task_type in ("move", "advance") and terrain_type == "open" and close_enemies:
                return "caution: open ground with enemy nearby — recommend formation change"
            if task_type in ("move", "advance") and terrain_type == "forest":
                return "good concealment in current terrain"
            if task_type in ("attack", "engage") and elevation_m is not None:
                for c in close_enemies:
                    c_elev = c.get("elevation_m")
                    if c_elev and c_elev > elevation_m + 30:
                        return "enemy on higher ground — expect strong resistance"
            if task_type == "defend" and terrain_type in ("urban", "forest"):
                return "terrain favorable for defense"
            if task_type == "defend" and terrain_type == "open":
                return "open position — recommend digging in"
            if strength < 0.4 and close_enemies:
                return "need reinforcement or withdrawal"
            if unit_type in ("recon_team", "recon_section", "sniper_team", "observation_post"):
                if close_enemies:
                    return "observing enemy, maintaining concealment"
            if elevation_m is not None and elevation_m > 200:
                for c in close_enemies:
                    c_elev = c.get("elevation_m")
                    if c_elev and elevation_m > c_elev + 30:
                        return "holding dominant high ground"

        return ""


# Singleton
response_generator = ResponseGenerator()

