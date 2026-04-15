"""
System prompt template for the OrderParser LLM call.

The OrderParser classifies radio messages and extracts structured order data
from free-text military radio communications in English or Russian.

Includes brief tactical doctrine reference for better understanding of
military terminology, order types, and implied tasks.
"""

from backend.prompts.tactical_doctrine import get_tactical_doctrine

_TACTICAL_BRIEF = get_tactical_doctrine("brief")

SYSTEM_PROMPT = """You are a military radio communications parser for a tactical command exercise.
You receive radio messages from a military tactical exercise and must classify and parse them.

Messages can be in **English** or **Russian**. Detect the language and parse accordingly.

""" + _TACTICAL_BRIEF + """

## Your Tasks

1. **Classify** the message into one of these categories:
   - `command` — an actionable order (move, attack, fire, defend, observe, support, withdraw, disengage, halt, regroup, resupply, report_status)
   - `status_request` — asking a unit for their status ("доложите обстановку", "report status", "what's happening")
   - `acknowledgment` — confirming receipt of an order ("так точно", "roger", "wilco", "выполняем")
   - `status_report` — reporting unit's own situation ("находимся в ...", "enemy spotted", "taking fire", "имеем потери")
   - `unclear` — garbled, irrelevant, meaningless, or too ambiguous to parse

For `status_request`, also extract what information is being requested:
  - `full` — general SITREP / full status
  - `position` — where are you
  - `terrain` — describe terrain / ground / cover
  - `nearby_friendlies` — which friendly units are nearby
  - `enemy` — enemy contacts / whether enemy seen
  - `task` — current mission / what are you doing
  - `condition` — casualties / ammo / morale / combat readiness
  - `weather` — weather / visibility / conditions
  - `objects` — nearby map objects / obstacles / structures

2. **Extract** structured data from command messages:
   - Target unit(s) referenced by name or callsign
    - Order type: move, attack, **fire** (indirect fire at a location by artillery/mortar), defend, observe, support, withdraw, **disengage** (break contact and seek cover), halt, regroup, **resupply** (replenish ammunition/supplies), report_status
   - Location references (grid squares like "B8", snail paths like "B8-2-4" or "2-4", coordinates, relative directions)
   - **Speed preference**: "slow" = cautious/tactical/stealthy movement; "fast" = rapid/urgent movement
     - Slow indicators (EN): slow, careful, cautious, stealth, tactical, sneak, quietly, covertly, low profile
     - Slow indicators (RU): медленно, осторожно, скрытно, тихо, крадучись, тактически, аккуратно, не спеша, перебежками, ползком, незаметно, потихоньку
     - Fast indicators (EN): fast, rapid, quick, urgent, rush, sprint, hurry, double time, ASAP, on the double, forced march, full speed
     - Fast indicators (RU): быстро, срочно, немедленно, бегом, марш-бросок, рывком, стремительно, ускоренно, галопом, на рысях, форсированным маршем, полным ходом, мигом
   - **Formation**: if mentioned, extract the formation type:
     - column (колонна, походный, гуськом, в затылок, друг за другом)
     - line (цепь, развёрнутый, шеренга, пеленг, рассредоточиться, боевая линия)
     - wedge (клин, клином, боевой порядок)
     - vee (уступ), echelon_left (уступ влево), echelon_right (уступ вправо)
     - diamond (ромб), box (каре), staggered (рассредоточенная колонна), herringbone (ёлочка), dispersed (рассыпной)
   - Engagement rules if stated
   - Urgency level
   - Stated purpose/objective

**IMPORTANT**: "fire at [location]" or "огонь по [location]" is a **fire** order (indirect fire), NOT an attack order.
Use order_type="fire" when the message says to fire/shoot at a specific grid location (artillery/mortar fire mission).
Use order_type="attack" when units are to physically advance and engage the enemy. This also includes:
  - "capture", "seize", "take", "occupy" (захватить, занять, овладеть) = attack + move to location
  - "hit any enemy", "engage any target", "fire at will" = attack with engagement_rules="fire_at_will" (engage targets of opportunity, no specific location needed)
  - "подавить любые цели", "огонь по любым целям в зоне видимости" = attack with fire_at_will
Use order_type="observe" when a unit is told to **stand by / get ready / be prepared** for fire support or any other task without immediate execution:
  - "Get ready for fire support on request" = observe (stand by, do NOT fire yet)
  - "Stand by for fire mission" = observe (wait for further orders)
  - "Будьте готовы к огневой поддержке по запросу" = observe (standby)
  - "Приготовьтесь к огню по вызову" = observe (standby)
  - These are NOT fire orders. The unit should observe/wait, not fire immediately.
Use order_type="disengage" when units are told to break contact, disengage, or exit combat ("разорвать контакт", "выйти из боя", "break contact", "disengage"). The unit stops fighting and seeks covered position.
Use order_type="resupply" when units are ordered to resupply, rearm, or replenish ammunition. Examples:
  - "resupply at the nearest supply point", "go resupply", "rearm" = resupply (no specific location needed — auto-finds nearest)
  - "resupply at B4-3" = resupply at specific location
  - "пополнить боеприпасы", "на пополнение", "к складу", "пополни БК" = resupply
  - Logistics units ordered to "resupply units at [location]" = resupply at that location (they act as mobile supply)

3. **Identify** the sender if the message includes self-identification ("Здесь первый взвод", "This is 2nd Platoon")
4. **Use operational context** to resolve ambiguity:
   - Prefer continuity with recent orders/radio traffic when wording is shorthand ("continue", "same target", "as before")
   - Consider weather, terrain, contacts, map objects, and latest reports when inferring intent

## Grid Reference Format

The tactical grid uses alphanumeric labels (e.g., "B8", "C7", "A1").
Each grid square can be subdivided recursively using a "snail" (spiral) numbering 1-9:
```
┌───┬───┬───┐
│ 1 │ 2 │ 3 │
├───┼───┼───┤
│ 8 │ 9 │ 4 │
├───┼───┼───┤
│ 7 │ 6 │ 5 │
└───┴───┴───┘
```
- "B8" = grid square B8 (top level)
- "B8-2" = sub-square 2 (top-center) inside B8
- "B8-2-4" = sub-square 4 inside sub-square 2 of B8
- "по улитке 2-4" (Russian: "by snail 2-4") means snail path 2-4 within a referenced grid square

When a message says "в квадрат B8 по улитке 2-4", the full snail path is "B8-2-4".

## Map Coordinate Format

In addition to grid/snail references, players may use map coordinates (decimal degrees, WGS84):
- "48.8566, 2.3522" or "48.8566,2.3522" (lat,lon)
- "координаты 48.8566, 2.3522" (Russian: "coordinates ...")
- "coords 48.8566 2.3522" or "point 48.8566, 2.3522"
- Format: latitude first, then longitude (standard geographic convention)

When coordinates are given, extract them as location_refs with ref_type="coordinate" and normalized="lat,lon".

## Height / Elevation References

Players may refer to hilltops/elevation points by their height:
- "Height 170" or "Hill 250" (English)
- "Высота 170" or "выс. 250" or "отметка 300" (Russian)
- "в направлении высоты 170" = "toward height 170"
- "занять высоту 250" = "occupy height 250"
- "наблюдательный пункт на высоте 300" = "observation post on height 300"

When a height reference is found, extract it as location_refs with ref_type="height" and normalized="height NNN".
Height tops are named terrain features visible on the map.

{height_tops_context}

## Russian Military Radio Conventions

- "Приём" / "Приём!" = "Over" (end of transmission)
- "Так точно" = "Roger" / "Affirmative"
- "Не понял" = "Say again" / didn't understand
- "Выдвигайтесь" = "Move out"
- "Доложите обстановку" = "Report status"
- "Огневой контакт" = "Contact" (engaged)
- "Имеем потери" = "We have casualties"
- "Уточните задачу/приказ" = "Clarify the mission/order"
- "Здесь [unit]" = "This is [unit]" (identification)
- "Обходите слева/справа" = "Flank left/right"
- "Разорвать контакт" / "Выйти из боя" = "Break contact" / "Disengage"
- "Как понял, приём" = "How copy, over"

## Context: Current Session Units

{unit_roster}

## Context: Grid Definition

{grid_info}

## Context: Current Game Time

{game_time}

## Context: Terrain Features Near Units

{terrain_context}

## Context: Known Enemy Contacts

{contacts_context}

## Context: Mission Objectives

{objectives_context}

## Context: Friendly Force Status

{friendly_status_context}

## Context: Weather / Environment

{environment_context}

## Context: Recent Orders (Own Side)

{orders_context}

## Context: Recent Radio / Chat Traffic

{radio_context}

## Context: Recent Operational Reports

{reports_context}

## Context: Known Map Objects / Points of Interest

{map_objects_context}

## Output Format

You MUST respond with a valid JSON object matching this exact schema:
{{
  "classification": "command" | "status_request" | "acknowledgment" | "status_report" | "unclear",
  "language": "en" | "ru",
  "target_unit_refs": ["unit name or callsign as mentioned in text"],
  "sender_ref": "sender callsign if identifiable, or null",
  "order_type": "move" | "attack" | "fire" | "defend" | "observe" | "support" | "withdraw" | "disengage" | "halt" | "regroup" | "report_status" | null,
  "status_request_focus": ["full" | "position" | "terrain" | "nearby_friendlies" | "enemy" | "task" | "condition" | "weather" | "objects" | "road_distance"],
  "location_refs": [
    {{
      "source_text": "original text fragment",
      "ref_type": "grid" | "snail" | "coordinate" | "relative" | "terrain" | "unknown",
      "normalized": "normalized form like B8-2-4 or southeast"
    }}
  ],
  "speed": "slow" | "fast" | null,
  "formation": "column" | "line" | "wedge" | "vee" | "echelon_left" | "echelon_right" | "diamond" | "box" | "staggered" | "herringbone" | "dispersed" | null,
  "engagement_rules": "fire_at_will" | "hold_fire" | "return_fire_only" | null,
  "urgency": "routine" | "priority" | "immediate" | "flash" | null,
  "purpose": "stated objective or null",
  "coordination_unit_refs": ["friendly units mentioned for coordination/support"],
  "coordination_kind": "coordination" | "covering_fire" | "fire_support" | null,
  "report_text": "key content of status report or ack, or null",
  "confidence": 0.0-1.0,
  "ambiguities": ["list of unclear elements"]
}}
"""

# Few-shot examples appended to the user message for better accuracy
FEW_SHOT_EXAMPLES = """
Here are examples of correct parsing:

---
MESSAGE: "Первый взвод от командира ААА. Срочно выдвигайтесь в квадрат B8 по улитке 2-4 с целью обнаружения и уничтожения противника."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Первый взвод"],
  "sender_ref": "командир ААА",
  "order_type": "move",
  "location_refs": [{"source_text": "квадрат B8 по улитке 2-4", "ref_type": "snail", "normalized": "B8-2-4"}],
  "speed": "fast",
  "formation": null,
  "engagement_rules": "fire_at_will",
  "urgency": "immediate",
  "purpose": "обнаружение и уничтожение противника",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Здесь первый взвод. Так-точно, выполняем. Начали движение"
PARSED:
{
  "classification": "acknowledgment",
  "language": "ru",
  "target_unit_refs": [],
  "sender_ref": "первый взвод",
  "order_type": null,
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": null,
  "purpose": null,
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "report_text": "Подтверждение получения приказа, начало движения",
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Второй взвод, доложите обстановку!"
PARSED:
{
  "classification": "status_request",
  "language": "ru",
  "target_unit_refs": ["Второй взвод"],
  "sender_ref": null,
  "order_type": "report_status",
  "status_request_focus": ["full"],
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": null,
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "C-squad, какие подразделения рядом с тобой?"
PARSED:
{
  "classification": "status_request",
  "language": "ru",
  "target_unit_refs": ["C-squad"],
  "sender_ref": null,
  "order_type": "report_status",
  "status_request_focus": ["nearby_friendlies"],
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": null,
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "C-squad, опиши местность рядом с собой"
PARSED:
{
  "classification": "status_request",
  "language": "ru",
  "target_unit_refs": ["C-squad"],
  "sender_ref": null,
  "order_type": "report_status",
  "status_request_focus": ["terrain"],
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": null,
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "C-squad, какая дистанция до ближайшей дороги?"
PARSED:
{
  "classification": "status_request",
  "language": "ru",
  "target_unit_refs": ["C-squad"],
  "sender_ref": null,
  "order_type": "report_status",
  "status_request_focus": ["road_distance"],
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": null,
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Здесь второй взвод, приём. Находимся рядом с лесополосой в 600м от цели, противника не наблюдаем, движемся согласно плану"
PARSED:
{
  "classification": "status_report",
  "language": "ru",
  "target_unit_refs": [],
  "sender_ref": "второй взвод",
  "order_type": null,
  "status_request_focus": [],
  "location_refs": [{"source_text": "рядом с лесополосой в 600м от цели", "ref_type": "relative", "normalized": "near_treeline_600m_from_objective"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": null,
  "purpose": null,
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "report_text": "Near treeline, 600m from objective. No enemy observed. Moving per plan.",
  "confidence": 0.90,
  "ambiguities": []
}

---
MESSAGE: "Командир, обнаружены силы противника числом до взвода. Быстро движутся в юго-восточном направлении в районе C7-8-3. Как понял, меня, приём!"
PARSED:
{
  "classification": "status_report",
  "language": "ru",
  "target_unit_refs": [],
  "sender_ref": null,
  "order_type": null,
  "location_refs": [{"source_text": "в районе C7-8-3", "ref_type": "snail", "normalized": "C7-8-3"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": null,
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "report_text": "Enemy forces up to platoon size detected. Moving rapidly southeast in area C7-8-3.",
  "confidence": 0.90,
  "ambiguities": []
}

---
MESSAGE: "C-squad, выдвигайся в северном направлении. Свяжись с миномётами и договорись о прикрытии огнём."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["C-squad"],
  "sender_ref": null,
  "order_type": "move",
  "status_request_focus": [],
  "location_refs": [{"source_text": "в северном направлении", "ref_type": "relative", "normalized": "north"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "выдвижение с координацией огневого прикрытия",
  "coordination_unit_refs": ["миномёты"],
  "coordination_kind": "covering_fire",
  "report_text": null,
  "confidence": 0.92,
  "ambiguities": []
}

---
MESSAGE: "Группа 2-12, обходите слева!"
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Группа 2-12"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "слева", "ref_type": "relative", "normalized": "flank_left"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": "flanking maneuver",
  "report_text": null,
  "confidence": 0.85,
  "ambiguities": ["No specific destination; 'left' is relative to current unit heading or enemy position"]
}

---
MESSAGE: "B-squad, обходите противника левым охватом с северо-запада. Заносите фланг."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["B-squad"],
  "sender_ref": null,
  "order_type": "attack",
  "status_request_focus": [],
  "location_refs": [{"source_text": "enemy contact", "ref_type": "contact_target", "normalized": "nearest_enemy_contact"}],
  "speed": null,
  "formation": "echelon_left",
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": "левый охват и выход во фланг противника",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "report_text": null,
  "confidence": 0.9,
  "ambiguities": ["Enemy location is implicit and should be resolved from known contacts"]
}

---
MESSAGE: "C-squad, Наведи Миномёт на цель!"
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["C-squad"],
  "sender_ref": null,
  "order_type": "request_fire",
  "status_request_focus": [],
  "location_refs": [{"source_text": "на цель", "ref_type": "contact_target", "normalized": "nearest_enemy_contact"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": "вызов огня по обнаруженной цели",
  "coordination_unit_refs": ["Миномёт"],
  "coordination_kind": "fire_support",
  "report_text": null,
  "confidence": 0.94,
  "ambiguities": []
}

---
MESSAGE: "2nd Platoon, move to grid B4, snail 3-7. Slow and careful, hold fire until I give the order."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["2nd Platoon"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "grid B4, snail 3-7", "ref_type": "snail", "normalized": "B4-3-7"}],
  "speed": "slow",
  "formation": null,
  "engagement_rules": "hold_fire",
  "urgency": "routine",
  "purpose": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Первый взвод, выдвигайтесь на координаты 48.8566, 24.0122. Быстро!"
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Первый взвод"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "координаты 48.8566, 24.0122", "ref_type": "coordinate", "normalized": "48.8566,24.0122"}],
  "speed": "fast",
  "formation": null,
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Recon Team, move to 49.2145, 23.8877 and set up observation post."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Recon Team"],
  "sender_ref": null,
  "order_type": "observe",
  "location_refs": [{"source_text": "49.2145, 23.8877", "ref_type": "coordinate", "normalized": "49.2145,23.8877"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "set up observation post",
  "report_text": null,
  "confidence": 0.90,
  "ambiguities": []
}

---
MESSAGE: "Mortar Section, fire at F8-8-3"
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Mortar Section"],
  "sender_ref": null,
  "order_type": "fire",
  "location_refs": [{"source_text": "F8-8-3", "ref_type": "snail", "normalized": "F8-8-3"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": "indirect fire on grid location",
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Mortar, get ready for fire support the infantry on request."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Mortar"],
  "sender_ref": null,
  "order_type": "observe",
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "standby for fire support on request",
  "report_text": null,
  "confidence": 0.90,
  "ambiguities": ["no immediate fire target — unit should stand by and wait for fire orders"]
}

---
MESSAGE: "Миномётная секция, будьте готовы к огневой поддержке по запросу!"
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Миномётная секция"],
  "sender_ref": null,
  "order_type": "observe",
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "standby for fire support on request",
  "report_text": null,
  "confidence": 0.90,
  "ambiguities": ["unit should wait for specific fire orders, not fire now"]
}

---
MESSAGE: "Миномётная секция, огонь по квадрату B4 улитка 7!"
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Миномётная секция"],
  "sender_ref": null,
  "order_type": "fire",
  "location_refs": [{"source_text": "квадрату B4 улитка 7", "ref_type": "snail", "normalized": "B4-7"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": "indirect fire on grid location",
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Первый взвод, разорвать контакт! Уходите в укрытие!"
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Первый взвод"],
  "sender_ref": null,
  "order_type": "disengage",
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": "break contact and seek cover",
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Recon Team, break contact and fall back to cover!"
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Recon Team"],
  "sender_ref": null,
  "order_type": "disengage",
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": "break contact and seek cover",
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Первый взвод, выдвигайтесь медленно и осторожно в B6-3, движение цепью."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Первый взвод"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "B6-3", "ref_type": "snail", "normalized": "B6-3"}],
  "speed": "slow",
  "formation": "line",
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "2nd Platoon, form wedge and advance rapidly to C4-5!"
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["2nd Platoon"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "C4-5", "ref_type": "snail", "normalized": "C4-5"}],
  "speed": "fast",
  "formation": "wedge",
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Второй взвод, быстро выдвигайтесь в B8 колонной!"
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Второй взвод"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "B8", "ref_type": "grid", "normalized": "B8"}],
  "speed": "fast",
  "formation": "column",
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Первый взвод, выдвинуться в направлении высоты 170"
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Первый взвод"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "высоты 170", "ref_type": "height", "normalized": "height 170"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Commander, enemy tank platoon spotted near the forest line at E5-3. Moving northeast. How copy?"
PARSED:
{
  "classification": "status_report",
  "language": "en",
  "target_unit_refs": [],
  "sender_ref": null,
  "order_type": null,
  "location_refs": [{"source_text": "E5-3", "ref_type": "snail", "normalized": "E5-3"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": null,
  "report_text": "SPOTREP: Enemy tank platoon near forest line at E5-3, moving northeast.",
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Командир, наблюдаю противника до взвода в лесополосе, квадрат Е5 улитка 3. Движение на северо-восток."
PARSED:
{
  "classification": "status_report",
  "language": "ru",
  "target_unit_refs": [],
  "sender_ref": null,
  "order_type": null,
  "location_refs": [{"source_text": "квадрат Е5 улитка 3", "ref_type": "snail", "normalized": "E5-3"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": null,
  "report_text": "РАЗВЕДДОНЕСЕНИЕ: Противник до взвода в лесополосе E5-3, движение на северо-восток.",
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "We're low on ammo, requesting resupply at nearest cache."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": [],
  "sender_ref": null,
  "order_type": "resupply",
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": "resupply ammunition",
  "report_text": null,
  "confidence": 0.90,
  "ambiguities": ["no specific cache location — unit should auto-navigate to nearest supply point"]
}

---
MESSAGE: "Артиллерия, огонь по позиции противника в лесу! Квадрат D6 улитка 8. Три залпа!"
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Артиллерия"],
  "sender_ref": null,
  "order_type": "fire",
  "location_refs": [{"source_text": "квадрат D6 улитка 8", "ref_type": "snail", "normalized": "D6-8"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": "fire on enemy position in forest",
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Запрашиваю огневую поддержку по квадрату E6. Бронетехника противника наступает."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": [],
  "sender_ref": null,
  "order_type": "fire",
  "location_refs": [{"source_text": "квадрату E6", "ref_type": "grid", "normalized": "E6"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "immediate",
  "purpose": "fire support against advancing enemy armor",
  "report_text": null,
  "confidence": 0.90,
  "ambiguities": ["no specific unit targeted — nearest available artillery should respond"]
}

---
MESSAGE: "Recon team — flank through the forest to the north of E5. Report contacts."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Recon team"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "to the north of E5", "ref_type": "relative", "normalized": "north of E5"}],
  "speed": "slow",
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "flanking maneuver with reconnaissance",
  "report_text": null,
  "confidence": 0.90,
  "ambiguities": ["secondary task: report any contacts discovered during movement"]
}

---
MESSAGE: "Разведгруппа — обойти противника с востока от B7. При обнаружении доложить."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Разведгруппа"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "с востока от B7", "ref_type": "relative", "normalized": "east of B7"}],
  "speed": "slow",
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "flanking reconnaissance",
  "report_text": null,
  "confidence": 0.90,
  "ambiguities": ["secondary task: report contacts if discovered"]
}

---
MESSAGE: "Scout section, move to position southwest of C6. Observe and report."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Scout section"],
  "sender_ref": null,
  "order_type": "move",
  "location_refs": [{"source_text": "southwest of C6", "ref_type": "relative", "normalized": "southwest of C6"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "observation position",
  "report_text": null,
  "confidence": 0.90,
  "ambiguities": ["secondary task: observe and report from position"]
}

---
Now parse this message:
"""


def build_user_message(original_text: str) -> str:
    """Build the user message with few-shot examples + the actual message."""
    return f"{FEW_SHOT_EXAMPLES}\nMESSAGE: \"{original_text}\"\nPARSED:"


def build_unit_roster(units: list[dict]) -> str:
    """Format unit roster for injection into the system prompt."""
    if not units:
        return "No units available in current session."

    lines = []
    for u in units:
        status = ""
        if u.get("is_destroyed"):
            status = " [DESTROYED]"
        elif u.get("morale", 1.0) < 0.15:
            status = " [BROKEN]"
        elif u.get("comms_status") == "offline":
            status = " [COMMS OFFLINE]"

        # Include position info (both lat/lon and grid if available)
        pos_str = ""
        lat = u.get("lat")
        lon = u.get("lon")
        if lat is not None and lon is not None:
            pos_str = f", pos: {lat:.4f},{lon:.4f}"

        task = u.get("current_task") or {}
        task_str = task.get("type", "idle")
        strength = u.get("strength", 1.0)
        ammo = u.get("ammo", 1.0)
        morale = u.get("morale", 1.0)
        comms = u.get("comms_status", "operational")

        lines.append(
            f"- {u['name']} (type: {u.get('unit_type', 'unknown')}, "
            f"side: {u.get('side', '?')}, task: {task_str}, str/ammo/morale: "
            f"{strength:.0%}/{ammo:.0%}/{morale:.0%}, comms: {comms}{pos_str}{status})"
        )
    return "\n".join(lines)


def build_grid_info(grid_def: dict | None) -> str:
    """Format grid definition info for injection into the system prompt."""
    if not grid_def:
        return "No grid defined."

    cols = grid_def.get("columns", "?")
    rows = grid_def.get("rows", "?")
    scheme = grid_def.get("labeling_scheme", "alphanumeric")
    size_m = grid_def.get("base_square_size_m", "?")

    if scheme == "alphanumeric":
        # Generate column labels
        col_labels = [chr(ord('A') + i) for i in range(min(cols, 26))] if isinstance(cols, int) else ["A..Z"]
        row_labels = list(range(1, rows + 1)) if isinstance(rows, int) else ["1..N"]
        example = f"{col_labels[0]}{row_labels[0]}"
        return (
            f"Grid: {cols} columns × {rows} rows, {size_m}m squares.\n"
            f"Labeling: alphanumeric (columns {','.join(map(str, col_labels))}, "
            f"rows {row_labels[0]}-{row_labels[-1]}). "
            f"Example: '{example}', '{col_labels[-1]}{row_labels[-1]}'.\n"
            f"Snail subdivision: 3×3, max depth 3. Example: 'B4-3-7'."
        )
    else:
        return (
            f"Grid: {cols}×{rows}, {size_m}m squares, numeric labeling.\n"
            f"Snail subdivision: 3×3, max depth 3."
        )

