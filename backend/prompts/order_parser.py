"""
System prompt template for the OrderParser LLM call.

The OrderParser classifies radio messages and extracts structured order data
from free-text military radio communications in English or Russian.

Doctrine is injected dynamically so the parser only receives the tactical
sections relevant to the current order family.
"""

import json

SYSTEM_PROMPT_TEMPLATE = """You are a military radio communications parser for a tactical command exercise.
You receive radio messages from a military tactical exercise and must classify and parse them.

Messages can be in **English** or **Russian**. Detect the language and parse accordingly.

{tactical_doctrine}

## Your Tasks

1. **Classify** the message into one of these categories:
   - `command` — an actionable order (move, attack, fire, request_fire, defend, observe, support, split, merge, breach, lay_mines, construct, deploy_bridge, withdraw, disengage, halt, regroup, resupply, report_status)
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
    - Order type: move, attack, **fire** (indirect fire at a location by artillery/mortar), **request_fire** (direct another support unit to fire), defend, observe, support, split, merge, breach, lay_mines, construct, deploy_bridge, withdraw, **disengage** (break contact and seek cover), halt, regroup, **resupply** (replenish ammunition/supplies), report_status
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
Use engineering order types when the task is clearly about engineer action rather than generic movement:
  - `breach` = clear or open a lane through wire, mines, roadblocks, ditches, or other obstacles
  - `lay_mines` = emplace minefields / lay mines in an area
  - `construct` = build entrenchments, roadblocks, towers, command posts, supply caches, field hospitals, and similar structures/obstacles
  - `deploy_bridge` = lay or deploy a bridge / AVLB bridge at a crossing point
When possible, also extract `map_object_type` such as `minefield`, `at_minefield`, `entrenchment`, `roadblock`, `barbed_wire`, `bridge_structure`, `observation_tower`, `field_hospital`, `command_post_structure`, `supply_cache`, or `smoke`.
Use `split` when a unit is ordered to detach, split off, or break into sub-elements.
Use `merge` when units are ordered to combine, join up, or merge back into one element.
For `split`, also extract `split_ratio` when the text specifies a share such as half / one third / 30%.
For `merge`, extract `merge_target_ref` naming the partner unit when present.
Use operational shorthand consistently:
  - "follow", "trail", "следуй за", "держись за" = persistent lead-follow relationship, usually order_type="move", maneuver_kind="follow"
  - "bound", "bounding", "перебежками", "скачками" = phased movement under cover, usually order_type="move", maneuver_kind="bounding"
  - "support by fire", "position of support by fire", "поддержка огнём" = support/covering-fire posture, usually order_type="support", maneuver_kind="support_by_fire"
  - "screen", "screen the flank", "прикрой фланг наблюдением" = observe/recon security mission, usually order_type="observe"
  - "delay", "задерживай и отходи", "fighting withdrawal" = disengage / delay mission, usually order_type="disengage"
If an attack or fire-support order references "the target", "на цель", "the enemy", or "противника" without a new grid reference, infer that it means the current known contact and emit a `contact_target` location reference rather than leaving the order targetless.

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
  "order_type": "move" | "attack" | "fire" | "request_fire" | "defend" | "observe" | "support" | "split" | "merge" | "breach" | "lay_mines" | "construct" | "deploy_bridge" | "withdraw" | "disengage" | "halt" | "regroup" | "report_status" | null,
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
  "merge_target_ref": "unit to merge with or null",
  "split_ratio": 0.1-0.9 or null,
  "map_object_type": "minefield" | "at_minefield" | "barbed_wire" | "concertina_wire" | "roadblock" | "anti_tank_ditch" | "dragons_teeth" | "entrenchment" | "pillbox" | "observation_tower" | "field_hospital" | "command_post_structure" | "supply_cache" | "bridge_structure" | "smoke" | null,
  "coordination_unit_refs": ["friendly units mentioned for coordination/support"],
  "coordination_kind": "coordination" | "covering_fire" | "fire_support" | null,
  "maneuver_kind": "follow" | "flank" | "bounding" | "support_by_fire" | "lead" | "trail" | null,
  "maneuver_side": "left" | "right" | null,
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
  "maneuver_kind": null,
  "maneuver_side": null,
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
  "maneuver_kind": "flank",
  "maneuver_side": "left",
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
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.94,
  "ambiguities": []
}

---
MESSAGE: "C-squad, свяжись с B-squad и следуй за ним."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["C-squad"],
  "sender_ref": null,
  "order_type": "move",
  "status_request_focus": [],
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "следование за ведущим подразделением с координацией",
  "coordination_unit_refs": ["B-squad"],
  "coordination_kind": "coordination",
  "maneuver_kind": "follow",
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.94,
  "ambiguities": []
}

---
MESSAGE: "A-squad, follow B-squad and keep to its left rear."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["A-squad"],
  "sender_ref": null,
  "order_type": "move",
  "status_request_focus": [],
  "location_refs": [],
  "speed": null,
  "formation": "echelon_left",
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "follow the lead element and maintain left-rear interval",
  "coordination_unit_refs": ["B-squad"],
  "coordination_kind": "coordination",
  "maneuver_kind": "follow",
  "maneuver_side": "left",
  "report_text": null,
  "confidence": 0.93,
  "ambiguities": []
}

---
MESSAGE: "2nd Platoon, bound forward by teams to B7-4. A-squad covers your move."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["2nd Platoon"],
  "sender_ref": null,
  "order_type": "move",
  "status_request_focus": [],
  "location_refs": [{"source_text": "B7-4", "ref_type": "snail", "normalized": "B7-4"}],
  "speed": "slow",
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": "bounding advance under cover",
  "coordination_unit_refs": ["A-squad"],
  "coordination_kind": "covering_fire",
  "maneuver_kind": "bounding",
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.92,
  "ambiguities": []
}

---
MESSAGE: "Mortar section, occupy support-by-fire position at E6-2 and cover B-squad's advance."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Mortar section"],
  "sender_ref": null,
  "order_type": "support",
  "status_request_focus": [],
  "location_refs": [{"source_text": "E6-2", "ref_type": "snail", "normalized": "E6-2"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": "occupy support-by-fire position and cover B-squad",
  "coordination_unit_refs": ["B-squad"],
  "coordination_kind": "covering_fire",
  "maneuver_kind": "support_by_fire",
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.93,
  "ambiguities": []
}

---
MESSAGE: "Разведгруппа, прикрой левый фланг наблюдением у высоты 149 и докладывай о контактах."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Разведгруппа"],
  "sender_ref": null,
  "order_type": "observe",
  "status_request_focus": [],
  "location_refs": [{"source_text": "высоты 149", "ref_type": "height", "normalized": "height 149"}],
  "speed": null,
  "formation": null,
  "engagement_rules": "hold_fire",
  "urgency": "routine",
  "purpose": "screen the left flank and report contacts",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "maneuver_kind": null,
  "maneuver_side": "left",
  "report_text": null,
  "confidence": 0.92,
  "ambiguities": []
}

---
MESSAGE: "Combat engineers, breach the roadblock at E6-3 and open a lane for B-squad."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Combat engineers"],
  "sender_ref": null,
  "order_type": "breach",
  "status_request_focus": [],
  "location_refs": [{"source_text": "E6-3", "ref_type": "snail", "normalized": "E6-3"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": "breach the obstacle and open a lane for B-squad",
  "map_object_type": "roadblock",
  "coordination_unit_refs": ["B-squad"],
  "coordination_kind": "coordination",
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.93,
  "ambiguities": []
}

---
MESSAGE: "Минно-заградительная секция, установите минное поле в квадрате F7-2."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Минно-заградительная секция"],
  "sender_ref": null,
  "order_type": "lay_mines",
  "status_request_focus": [],
  "location_refs": [{"source_text": "F7-2", "ref_type": "snail", "normalized": "F7-2"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "создать минное заграждение",
  "map_object_type": "minefield",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.93,
  "ambiguities": []
}

---
MESSAGE: "Construction engineers, build an entrenchment on height 149."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Construction engineers"],
  "sender_ref": null,
  "order_type": "construct",
  "status_request_focus": [],
  "location_refs": [{"source_text": "height 149", "ref_type": "height", "normalized": "height 149"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "routine",
  "purpose": "build a prepared fighting position",
  "map_object_type": "entrenchment",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.92,
  "ambiguities": []
}

---
MESSAGE: "AVLB section, deploy bridge at the crossing near B5."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["AVLB section"],
  "sender_ref": null,
  "order_type": "deploy_bridge",
  "status_request_focus": [],
  "location_refs": [{"source_text": "B5", "ref_type": "grid", "normalized": "B5"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": "establish a crossing point",
  "map_object_type": "bridge_structure",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.92,
  "ambiguities": []
}

---
MESSAGE: "Logistics unit, resupply B-squad and stay with them."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Logistics unit"],
  "sender_ref": null,
  "order_type": "resupply",
  "status_request_focus": [],
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": "priority",
  "purpose": "move to B-squad and replenish them",
  "map_object_type": null,
  "coordination_unit_refs": ["B-squad"],
  "coordination_kind": "coordination",
  "maneuver_kind": "follow",
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.92,
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
MESSAGE: "Engineer platoon, clear a lane through the minefield at F7-2-1 and mark the breach."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Engineer platoon"],
  "sender_ref": null,
  "order_type": "breach",
  "status_request_focus": [],
  "location_refs": [{"source_text": "F7-2-1", "ref_type": "snail", "normalized": "F7-2-1"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": null,
  "purpose": "open a marked lane through the obstacle",
  "map_object_type": "minefield",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.95,
  "ambiguities": []
}

---
MESSAGE: "Сапёры, оборудуйте окопы и разверните КП в квадрате C4-2."
PARSED:
{
  "classification": "command",
  "language": "ru",
  "target_unit_refs": ["Сапёры"],
  "sender_ref": null,
  "order_type": "construct",
  "status_request_focus": [],
  "location_refs": [{"source_text": "C4-2", "ref_type": "snail", "normalized": "C4-2"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": null,
  "purpose": "укрепить позицию и подготовить пункт управления",
  "map_object_type": "entrenchment",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.92,
  "ambiguities": ["secondary structure mentioned: command_post_structure"]
}

---
MESSAGE: "Logistics section, resupply B-squad and stay behind them as they advance to Hill 149."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Logistics section"],
  "sender_ref": null,
  "order_type": "resupply",
  "status_request_focus": [],
  "location_refs": [{"source_text": "Hill 149", "ref_type": "height", "normalized": "height 149"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": null,
  "purpose": "mobile sustainment in support of B-squad",
  "map_object_type": null,
  "coordination_unit_refs": ["B-squad"],
  "coordination_kind": "coordination",
  "maneuver_kind": "follow",
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.93,
  "ambiguities": []
}

---
MESSAGE: "Mortar, put smoke on the bridge crossing at E6-2."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["Mortar"],
  "sender_ref": null,
  "order_type": "fire",
  "status_request_focus": [],
  "location_refs": [
    {"source_text": "bridge crossing", "ref_type": "map_object", "normalized": "bridge crossing"},
    {"source_text": "E6-2", "ref_type": "snail", "normalized": "E6-2"}
  ],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": null,
  "purpose": "mask movement with smoke",
  "merge_target_ref": null,
  "split_ratio": null,
  "map_object_type": "smoke",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.93,
  "ambiguities": []
}

---
MESSAGE: "A-squad, split off half your strength and send the new element to screen the bridge."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["A-squad"],
  "sender_ref": null,
  "order_type": "split",
  "status_request_focus": [],
  "location_refs": [{"source_text": "bridge", "ref_type": "map_object", "normalized": "bridge"}],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": null,
  "purpose": "detach a screening element",
  "merge_target_ref": null,
  "split_ratio": 0.5,
  "map_object_type": "bridge_structure",
  "coordination_unit_refs": [],
  "coordination_kind": null,
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.92,
  "ambiguities": []
}

---
MESSAGE: "B-squad, merge with C-squad and continue the advance as one element."
PARSED:
{
  "classification": "command",
  "language": "en",
  "target_unit_refs": ["B-squad"],
  "sender_ref": null,
  "order_type": "merge",
  "status_request_focus": [],
  "location_refs": [],
  "speed": null,
  "formation": null,
  "engagement_rules": null,
  "urgency": null,
  "purpose": "recombine combat power before continuing the advance",
  "merge_target_ref": "C-squad",
  "split_ratio": null,
  "map_object_type": null,
  "coordination_unit_refs": ["C-squad"],
  "coordination_kind": "coordination",
  "maneuver_kind": null,
  "maneuver_side": null,
  "report_text": null,
  "confidence": 0.93,
  "ambiguities": []
}

---
Now parse this message:
"""


# ── Dynamic few-shot examples indexed by (order_type, language) ──────────
# Used by the optimized local prompt to inject only 2-3 relevant examples
# instead of the full 35+ set. Keeps total prompt under ~3500 tokens.

_FEW_SHOT_BY_TYPE: dict[str, list[dict]] = {
    "move": [
        {"lang": "ru", "msg": 'Первый взвод, выдвигайтесь медленно и осторожно в B6-3, движение цепью.',
         "out": '{"classification":"command","language":"ru","target_unit_refs":["Первый взвод"],"sender_ref":null,"order_type":"move","location_refs":[{"source_text":"B6-3","ref_type":"snail","normalized":"B6-3"}],"speed":"slow","formation":"line","engagement_rules":null,"urgency":"routine","purpose":null,"confidence":0.95,"ambiguities":[]}'},
        {"lang": "en", "msg": '2nd Platoon, move to grid B4, snail 3-7. Slow and careful, hold fire until I give the order.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["2nd Platoon"],"sender_ref":null,"order_type":"move","location_refs":[{"source_text":"grid B4, snail 3-7","ref_type":"snail","normalized":"B4-3-7"}],"speed":"slow","formation":null,"engagement_rules":"hold_fire","urgency":"routine","purpose":null,"confidence":0.95,"ambiguities":[]}'},
    ],
    "attack": [
        {"lang": "ru", "msg": 'B-squad, обходите противника левым охватом с северо-запада.',
         "out": '{"classification":"command","language":"ru","target_unit_refs":["B-squad"],"sender_ref":null,"order_type":"attack","location_refs":[{"source_text":"enemy contact","ref_type":"contact_target","normalized":"nearest_enemy_contact"}],"speed":null,"formation":"echelon_left","engagement_rules":null,"urgency":"priority","purpose":"фланговый обход","maneuver_kind":"flank","maneuver_side":"left","confidence":0.9,"ambiguities":[]}'},
        {"lang": "en", "msg": 'Recon team — flank through the forest to the north of E5. Report contacts.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["Recon team"],"sender_ref":null,"order_type":"move","location_refs":[{"source_text":"to the north of E5","ref_type":"relative","normalized":"north of E5"}],"speed":"slow","formation":null,"engagement_rules":null,"urgency":"routine","purpose":"flanking maneuver with reconnaissance","confidence":0.9,"ambiguities":[]}'},
    ],
    "fire": [
        {"lang": "ru", "msg": 'Миномётная секция, огонь по квадрату B4 улитка 7!',
         "out": '{"classification":"command","language":"ru","target_unit_refs":["Миномётная секция"],"sender_ref":null,"order_type":"fire","location_refs":[{"source_text":"квадрату B4 улитка 7","ref_type":"snail","normalized":"B4-7"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"immediate","purpose":"indirect fire on grid location","confidence":0.95,"ambiguities":[]}'},
        {"lang": "en", "msg": 'Mortar Section, fire at F8-8-3',
         "out": '{"classification":"command","language":"en","target_unit_refs":["Mortar Section"],"sender_ref":null,"order_type":"fire","location_refs":[{"source_text":"F8-8-3","ref_type":"snail","normalized":"F8-8-3"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"immediate","purpose":"indirect fire on grid location","confidence":0.95,"ambiguities":[]}'},
    ],
    "request_fire": [
        {"lang": "en", "msg": 'B-squad, request mortar smoke on the bridge crossing and move under concealment.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["B-squad"],"sender_ref":null,"order_type":"request_fire","location_refs":[{"source_text":"bridge crossing","ref_type":"map_object","normalized":"bridge crossing"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"priority","purpose":"request smoke support for movement","coordination_unit_refs":["Mortar"],"coordination_kind":"fire_support","map_object_type":"smoke","confidence":0.92,"ambiguities":[]}'},
        {"lang": "ru", "msg": 'Первый взвод, вызови огонь миномётов по противнику у высоты 170.',
         "out": '{"classification":"command","language":"ru","target_unit_refs":["Первый взвод"],"sender_ref":null,"order_type":"request_fire","location_refs":[{"source_text":"у высоты 170","ref_type":"height","normalized":"height 170"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"priority","purpose":"вызов огневой поддержки по противнику","coordination_unit_refs":["миномётов"],"coordination_kind":"fire_support","confidence":0.9,"ambiguities":[]}'},
    ],
    "defend": [
        {"lang": "en", "msg": '2nd Platoon, dig in and defend at C4-2.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["2nd Platoon"],"sender_ref":null,"order_type":"defend","location_refs":[{"source_text":"C4-2","ref_type":"snail","normalized":"C4-2"}],"speed":null,"formation":null,"engagement_rules":"return_fire_only","urgency":"routine","purpose":"defend position","confidence":0.93,"ambiguities":[]}'},
        {"lang": "ru", "msg": 'Первый взвод, закрепиться на высоте 170. Огонь по готовности.',
         "out": '{"classification":"command","language":"ru","target_unit_refs":["Первый взвод"],"sender_ref":null,"order_type":"defend","location_refs":[{"source_text":"высоте 170","ref_type":"height","normalized":"height 170"}],"speed":null,"formation":null,"engagement_rules":"fire_at_will","urgency":"routine","purpose":"закрепиться на высоте","confidence":0.93,"ambiguities":[]}'},
    ],
    "observe": [
        {"lang": "en", "msg": 'Mortar, get ready for fire support the infantry on request.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["Mortar"],"sender_ref":null,"order_type":"observe","location_refs":[],"speed":null,"formation":null,"engagement_rules":null,"urgency":"routine","purpose":"standby for fire support on request","confidence":0.9,"ambiguities":[]}'},
        {"lang": "ru", "msg": 'Миномётная секция, будьте готовы к огневой поддержке по запросу!',
         "out": '{"classification":"command","language":"ru","target_unit_refs":["Миномётная секция"],"sender_ref":null,"order_type":"observe","location_refs":[],"speed":null,"formation":null,"engagement_rules":null,"urgency":"routine","purpose":"standby for fire support on request","confidence":0.9,"ambiguities":[]}'},
    ],
    "support": [
        {"lang": "en", "msg": 'Machine-gun team, support B-squad with covering fire from the orchard edge.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["Machine-gun team"],"sender_ref":null,"order_type":"support","location_refs":[{"source_text":"orchard edge","ref_type":"terrain","normalized":"orchard edge"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"priority","purpose":"cover B-squad with supporting fire","coordination_unit_refs":["B-squad"],"coordination_kind":"covering_fire","maneuver_kind":"support_by_fire","confidence":0.92,"ambiguities":[]}'},
    ],
    "disengage": [
        {"lang": "ru", "msg": 'Первый взвод, разорвать контакт! Уходите в укрытие!',
         "out": '{"classification":"command","language":"ru","target_unit_refs":["Первый взвод"],"sender_ref":null,"order_type":"disengage","location_refs":[],"speed":null,"formation":null,"engagement_rules":null,"urgency":"immediate","purpose":"break contact and seek cover","confidence":0.95,"ambiguities":[]}'},
        {"lang": "en", "msg": 'Recon Team, break contact and fall back to cover!',
         "out": '{"classification":"command","language":"en","target_unit_refs":["Recon Team"],"sender_ref":null,"order_type":"disengage","location_refs":[],"speed":null,"formation":null,"engagement_rules":null,"urgency":"immediate","purpose":"break contact and seek cover","confidence":0.95,"ambiguities":[]}'},
    ],
    "resupply": [
        {"lang": "en", "msg": "We're low on ammo, requesting resupply at nearest cache.",
         "out": '{"classification":"command","language":"en","target_unit_refs":[],"sender_ref":null,"order_type":"resupply","location_refs":[],"speed":null,"formation":null,"engagement_rules":null,"urgency":"priority","purpose":"resupply ammunition","confidence":0.9,"ambiguities":[]}'},
    ],
    "breach": [
        {"lang": "en", "msg": 'Combat engineers, breach the roadblock at E6-3 and open a lane for B-squad.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["Combat engineers"],"sender_ref":null,"order_type":"breach","location_refs":[{"source_text":"E6-3","ref_type":"snail","normalized":"E6-3"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"priority","purpose":"breach the obstacle","map_object_type":"roadblock","confidence":0.93,"ambiguities":[]}'},
    ],
    "lay_mines": [
        {"lang": "en", "msg": 'Engineer section, lay mines on the northern road approach to the bridge.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["Engineer section"],"sender_ref":null,"order_type":"lay_mines","location_refs":[{"source_text":"northern road approach to the bridge","ref_type":"relative","normalized":"north road approach to bridge"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"routine","purpose":"emplace a minefield on likely approach","map_object_type":"minefield","confidence":0.92,"ambiguities":[]}'},
    ],
    "construct": [
        {"lang": "en", "msg": 'Construction engineers, build a command post near Hill 170.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["Construction engineers"],"sender_ref":null,"order_type":"construct","location_refs":[{"source_text":"Hill 170","ref_type":"height","normalized":"height 170"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"routine","purpose":"build a command post","map_object_type":"command_post_structure","confidence":0.92,"ambiguities":[]}'},
    ],
    "deploy_bridge": [
        {"lang": "ru", "msg": 'Сапёры, наведите мост у переправы восточнее B6.',
         "out": '{"classification":"command","language":"ru","target_unit_refs":["Сапёры"],"sender_ref":null,"order_type":"deploy_bridge","location_refs":[{"source_text":"переправы восточнее B6","ref_type":"relative","normalized":"east of B6 crossing"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"priority","purpose":"наведение моста у переправы","map_object_type":"bridge_structure","confidence":0.92,"ambiguities":[]}'},
    ],
    "split": [
        {"lang": "en", "msg": 'A-squad, split off one third to screen the bunker while the rest continue forward.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["A-squad"],"sender_ref":null,"order_type":"split","location_refs":[{"source_text":"the bunker","ref_type":"map_object","normalized":"bunker"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"priority","purpose":"detach a screening element","split_ratio":0.33,"confidence":0.9,"ambiguities":[]}'},
    ],
    "merge": [
        {"lang": "en", "msg": 'B-squad, merge with C-squad and continue as one element.',
         "out": '{"classification":"command","language":"en","target_unit_refs":["B-squad"],"sender_ref":null,"order_type":"merge","location_refs":[],"speed":null,"formation":null,"engagement_rules":null,"urgency":"routine","purpose":"combine combat power into one element","merge_target_ref":"C-squad","confidence":0.92,"ambiguities":[]}'},
    ],
    "status_request": [
        {"lang": "ru", "msg": 'Второй взвод, доложите обстановку!',
         "out": '{"classification":"status_request","language":"ru","target_unit_refs":["Второй взвод"],"sender_ref":null,"order_type":"report_status","status_request_focus":["full"],"location_refs":[],"speed":null,"formation":null,"engagement_rules":null,"urgency":"priority","purpose":null,"confidence":0.95,"ambiguities":[]}'},
    ],
    "acknowledgment": [
        {"lang": "ru", "msg": 'Здесь первый взвод. Так-точно, выполняем.',
         "out": '{"classification":"acknowledgment","language":"ru","target_unit_refs":[],"sender_ref":"первый взвод","order_type":null,"location_refs":[],"speed":null,"formation":null,"engagement_rules":null,"urgency":null,"purpose":null,"report_text":"Подтверждение получения приказа","confidence":0.95,"ambiguities":[]}'},
    ],
    "status_report": [
        {"lang": "ru", "msg": 'Командир, обнаружены силы противника в районе C7-8-3.',
         "out": '{"classification":"status_report","language":"ru","target_unit_refs":[],"sender_ref":null,"order_type":null,"location_refs":[{"source_text":"C7-8-3","ref_type":"snail","normalized":"C7-8-3"}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"priority","purpose":null,"report_text":"Enemy forces detected at C7-8-3.","confidence":0.9,"ambiguities":[]}'},
    ],
}

# Default examples for order types not in the index
_FEW_SHOT_DEFAULT = _FEW_SHOT_BY_TYPE["move"]


def _select_few_shot_examples(
    order_type_hint: str | None,
    language_hint: str | None,
    max_examples: int = 3,
    *,
    compact: bool = False,
) -> str:
    """Select the most relevant few-shot examples for the detected order type and language."""
    examples: list[dict] = []

    # Primary: examples for the detected order type
    if order_type_hint and order_type_hint in _FEW_SHOT_BY_TYPE:
        pool = _FEW_SHOT_BY_TYPE[order_type_hint]
        # Prefer examples matching the detected language
        if language_hint:
            lang_match = [e for e in pool if e["lang"] == language_hint]
            others = [e for e in pool if e["lang"] != language_hint]
            examples.extend(lang_match[:2])
            examples.extend(others[:max_examples - len(examples)])
        else:
            examples.extend(pool[:max_examples])

    # Fill remaining slots with a diverse set
    if len(examples) < max_examples:
        for otype in ("move", "fire", "status_request", "acknowledgment"):
            if len(examples) >= max_examples:
                break
            if otype == order_type_hint:
                continue
            pool = _FEW_SHOT_BY_TYPE.get(otype, [])
            if pool:
                examples.append(pool[0])

    # De-duplicate examples by message text so compact local prompts do not
    # waste budget repeating near-identical demonstrations.
    unique_examples: list[dict] = []
    seen_messages: set[str] = set()
    for ex in examples:
        msg = ex["msg"]
        if msg in seen_messages:
            continue
        unique_examples.append(ex)
        seen_messages.add(msg)

    def _format_example_output(example: dict) -> str:
        if not compact:
            return example["out"]
        try:
            payload = json.loads(example["out"])
        except json.JSONDecodeError:
            return example["out"]

        compact_payload = {"classification": payload.get("classification"), "language": payload.get("language")}
        preferred_keys = (
            "target_unit_refs",
            "order_type",
            "status_request_focus",
            "location_refs",
            "engagement_rules",
            "urgency",
            "support_target_ref",
            "merge_target_ref",
            "split_ratio",
            "map_object_type",
            "coordination_unit_refs",
            "coordination_kind",
            "maneuver_kind",
            "maneuver_side",
            "report_text",
        )
        for key in preferred_keys:
            value = payload.get(key)
            if value in (None, [], "", {}):
                continue
            compact_payload[key] = value
        return json.dumps(compact_payload, ensure_ascii=False, separators=(",", ":"))

    # Format as text
    lines = ["Examples:"]
    for ex in unique_examples[:max_examples]:
        lines.append(f'MESSAGE: "{ex["msg"]}"')
        lines.append(f"PARSED: {_format_example_output(ex)}")
        lines.append("")
    return "\n".join(lines)


# ── Optimized mid-tier prompt for local models ──────────────────────────
# Design goals:
# 1. ~1500-2000 token system prompt (vs ~5000+ full) — fits 1B/16K context
# 2. Static prefix for llama.cpp KV cache reuse across requests
# 3. Dynamic context (roster, contacts, etc.) in user message only
# 4. 2-3 dynamically selected few-shot examples instead of 35+
# 5. Preserves all critical parsing capabilities

# Part A: Stable system prefix — NEVER changes between requests.
# llama.cpp will cache KV for this entire block after the first request.
LOCAL_SYSTEM_PREFIX = """Parse EN/RU tactical radio into JSON.

Classes: command, status_request, acknowledgment, status_report, unclear.
Command order_type: move, attack, fire, request_fire, defend, observe, support, halt, withdraw, disengage, resupply, breach, lay_mines, construct, deploy_bridge, split, merge, regroup.
status_request_focus: full, position, terrain, nearby_friendlies, enemy, task, condition, weather, objects, road_distance.

Rules:
- "fire at [grid]" / "огонь по [grid]" = fire, not attack.
- "stand by"/"get ready"/"будьте готовы"/"по запросу" = observe, not fire.
- "break contact"/"разорвать контакт" = disengage.
- Trust parser state packet and continuity hints for shorthand like continue, same target, the bridge.
- speed: slow or fast.
- refs: grid B8, snail B8-2-4, coordinate 48.85,24.01, height 170 / высота 170.
- snail: 1 TL, 2 TC, 3 TR, 4 MR, 5 BR, 6 BC, 7 BL, 8 ML, 9 center.
- Use null or [] for missing fields.

Schema:
{"classification":"command|status_request|acknowledgment|status_report|unclear","language":"en|ru","target_unit_refs":[],"sender_ref":null,"order_type":"move|attack|fire|request_fire|defend|observe|support|halt|withdraw|disengage|resupply|breach|lay_mines|construct|deploy_bridge|split|merge|regroup|report_status|null","status_request_focus":[],"location_refs":[{"source_text":"","ref_type":"snail|grid|coordinate|relative|height|terrain","normalized":""}],"speed":"slow|fast|null","formation":"column|line|wedge|vee|echelon_left|echelon_right|diamond|box|staggered|herringbone|dispersed|null","engagement_rules":"fire_at_will|hold_fire|return_fire_only|null","urgency":"routine|priority|immediate|null","purpose":null,"support_target_ref":null,"coordination_unit_refs":[],"coordination_kind":"coordination|covering_fire|fire_support|null","maneuver_kind":"follow|flank|bounding|support_by_fire|null","maneuver_side":"left|right|null","map_object_type":"minefield|barbed_wire|roadblock|entrenchment|bridge_structure|smoke|null","merge_target_ref":null,"split_ratio":null,"report_text":null,"confidence":0.9,"ambiguities":[]}

Return JSON only."""


def build_optimized_local_prompt(
    units: list[dict],
    order_type_hint: str | None = None,
    language_hint: str | None = None,
    grid_info: dict | None = None,
    doctrine_excerpt: str = "",
    state_packet: str = "",
    continuity_hints: str = "",
    contacts_summary: str = "",
    objectives_summary: str = "",
    terrain_summary: str = "",
    history_summary: str = "",
    map_objects_summary: str = "",
    environment_summary: str = "",
    friendly_status_summary: str = "",
    height_tops_context: str = "",
    game_time: str = "",
) -> tuple[str, str]:
    """
    Build an optimized prompt pair for local 1-3B models.

    Returns (system_prompt, user_context_prefix) where:
    - system_prompt: stable prefix (cached by llama.cpp KV cache)
    - user_context_prefix: dynamic context prepended to the actual message

    The system prompt is STABLE across all requests — llama.cpp only processes
    it once and reuses the KV cache for subsequent requests.
    All dynamic content goes into the user message.
    """
    # ── System message: static only (for KV cache reuse) ──
    system = LOCAL_SYSTEM_PREFIX

    # ── User message: dynamic context + few-shot + actual message ──
    # This part changes per request, so llama.cpp will process only this.
    user_parts = []

    if doctrine_excerpt:
        user_parts.append(doctrine_excerpt)

    if state_packet:
        user_parts.append(state_packet)

    if continuity_hints:
        user_parts.append(continuity_hints)

    if not state_packet:
        # Unit roster (names + types only, very compact)
        if units:
            alive = [u for u in units if not u.get("is_destroyed")]
            roster_lines = []
            for u in alive[:15]:
                task = (u.get("current_task") or {}).get("type", "idle")
                roster_lines.append(f"{u['name']}({u.get('unit_type', '?')},{task})")
            user_parts.append("Units: " + "; ".join(roster_lines))
        else:
            user_parts.append("Units: unknown")

    # Grid info (one line)
    if grid_info:
        cols = grid_info.get("columns", "?")
        rows = grid_info.get("rows", "?")
        scheme = grid_info.get("labeling_scheme", "alphanumeric")
        if scheme == "alphanumeric" and isinstance(cols, int):
            col_labels = [chr(ord('A') + i) for i in range(min(cols, 26))]
            user_parts.append(f"Grid: {cols}×{rows}, columns {col_labels[0]}-{col_labels[-1]}, rows 1-{rows}")
        else:
            user_parts.append(f"Grid: {cols}×{rows}")

    # Game time (one line)
    if game_time:
        user_parts.append(f"Time: {game_time}")

    if not state_packet:
        # Contacts (compact, already summarized by caller)
        if contacts_summary:
            user_parts.append(contacts_summary)

        # Objectives (one line)
        if objectives_summary:
            user_parts.append(objectives_summary)

        # Terrain (one line summary)
        if terrain_summary:
            user_parts.append(terrain_summary)

        if map_objects_summary:
            user_parts.append(map_objects_summary)

        if friendly_status_summary:
            user_parts.append(friendly_status_summary)

        if environment_summary:
            user_parts.append(environment_summary)

        if history_summary:
            user_parts.append(history_summary)

    if height_tops_context:
        user_parts.append(height_tops_context)

    # Dynamic few-shot examples:
    # local small models benefit from demonstrations, but long examples dominate
    # CPU prefill. Keep them short and fewer when a state packet already exists.
    local_example_budget = 1 if state_packet else 2
    few_shot = _select_few_shot_examples(
        order_type_hint,
        language_hint,
        max_examples=local_example_budget,
        compact=True,
    )
    user_parts.append(few_shot)

    user_context = "\n".join(user_parts)
    return system, user_context


def summarize_history_for_local(
    orders_context: str,
    radio_context: str,
    reports_context: str,
) -> str:
    """
    Compress verbose order/radio/report history into a 2-3 line summary.

    Used by local model path to avoid injecting 40+ raw history entries.
    Extracts only the essential info: counts and last few actions.
    """
    lines = []

    # Orders: extract count and last order type
    if orders_context and orders_context != "No prior own-side orders.":
        order_lines = [l.strip() for l in orders_context.split("\n") if l.strip().startswith("- ")]
        if order_lines:
            # Extract order types from format: "  - timestamp: order_type -> units [status] | text"
            last_orders = []
            for ol in order_lines[:3]:
                parts = ol.split(":", 2)
                if len(parts) >= 2:
                    # Try to extract order type after the timestamp
                    remainder = parts[-1].strip() if len(parts) > 2 else parts[1].strip()
                    type_part = remainder.split("->")[0].strip().split(",")[0].strip()
                    last_orders.append(type_part)
            if last_orders:
                lines.append(f"Recent orders ({len(order_lines)} total): last={', '.join(last_orders[:3])}")

    # Radio: count + last sender
    if radio_context and radio_context != "No recent radio/chat traffic.":
        radio_lines = [l.strip() for l in radio_context.split("\n") if l.strip().startswith("- ")]
        if radio_lines:
            last_msg = radio_lines[-1] if radio_lines else ""
            # Truncate to essential info
            if len(last_msg) > 80:
                last_msg = last_msg[:77] + "..."
            lines.append(f"Radio traffic ({len(radio_lines)} msgs): last: {last_msg}")

    # Reports: count + last channel
    if reports_context and reports_context != "No recent operational reports.":
        report_lines = [l.strip() for l in reports_context.split("\n") if l.strip().startswith("- ")]
        if report_lines:
            channels = set()
            for rl in report_lines[:10]:
                if "[" in rl and "]" in rl:
                    ch = rl.split("[")[1].split("]")[0]
                    channels.add(ch)
            lines.append(f"Reports ({len(report_lines)} total): channels={', '.join(sorted(channels)[:4])}")

    return "\n".join(lines) if lines else ""


def summarize_contacts_for_local(contacts_context: str) -> str:
    """Compress contacts context to a compact summary for local models."""
    if not contacts_context or contacts_context == "No known enemy contacts.":
        return ""

    contact_lines = [l.strip() for l in contacts_context.split("\n") if l.strip().startswith("- ")]
    if not contact_lines:
        return ""

    # Keep up to 5 contacts, trim each to essential info
    compact = [f"Contacts ({len(contact_lines)}):"]
    for cl in contact_lines[:5]:
        # Trim verbose fields — keep type, position, grid
        compact.append(f"  {cl[:100]}")
    return "\n".join(compact)


def summarize_terrain_for_local(terrain_context: str) -> str:
    """Compress terrain context to one-line summary for local models."""
    if not terrain_context or terrain_context == "No terrain data available.":
        return ""

    # Extract just terrain type counts and elevation range
    lines = terrain_context.split("\n")
    types = []
    elev = ""
    for line in lines:
        line = line.strip()
        if line.startswith("- ") and ":" in line and "cells" in line:
            ttype = line.split(":")[0].replace("- ", "").strip()
            types.append(ttype)
        elif "Elevation" in line:
            elev = line.strip()

    result = f"Terrain: {', '.join(types[:6])}" if types else ""
    if elev:
        result += f". {elev}" if result else elev
    return result


def build_system_prompt(tactical_doctrine: str) -> str:
    """Build the system prompt with only the doctrine relevant to this parse."""
    return SYSTEM_PROMPT_TEMPLATE.replace("{tactical_doctrine}", tactical_doctrine)


def build_user_message(
    original_text: str,
    *,
    order_type_hint: str | None = None,
    language_hint: str | None = None,
    context_block: str = "",
    max_examples: int = 4,
    include_examples: bool = True,
    compact_examples: bool = False,
) -> str:
    """Build the user message with task-relevant few-shot examples."""
    parts = []
    if context_block:
        parts.append(context_block.strip())
    if include_examples and max_examples > 0:
        parts.append(
            _select_few_shot_examples(
                order_type_hint,
                language_hint,
                max_examples=max_examples,
                compact=compact_examples,
            )
        )
    parts.append(f'MESSAGE: "{original_text}"\nPARSED:')
    return "\n\n".join(parts)


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


# ── Compact prompt for small local models (1-3B params, ≤16K context) ──
# LEGACY: kept for backward compatibility. New code uses build_optimized_local_prompt.

COMPACT_SYSTEM_PROMPT = """You are a military radio message parser. Parse the message and respond with JSON only.

Classify as: command, acknowledgment, status_report, status_request, unclear.

For commands extract: order_type (move/attack/fire/defend/observe/halt/withdraw/disengage/resupply/request_fire/support/breach/split/merge), location_refs, speed (slow/fast/null), formation (column/line/wedge/vee/echelon_left/echelon_right/diamond/box/staggered/herringbone/dispersed or null).

Location ref_type: snail (e.g. "F6-8-9"), grid (e.g. "B4"), coordinate (lat,lon), direction (e.g. "north 500m").

{unit_roster}

JSON schema:
{{"classification":"command","language":"en","confidence":0.9,"target_unit_refs":["Alpha"],"sender_ref":null,"order_type":"move","location_refs":[{{"source_text":"F6-8-9","ref_type":"snail","normalized":"F6-8-9"}}],"speed":null,"formation":null,"engagement_rules":null,"urgency":"routine","purpose":null,"support_target_ref":null,"status_request_type":null}}"""


def build_compact_prompt(units: list[dict]) -> str:
    """Build a minimal prompt for small local models."""
    if units:
        names = [f"{u['name']} ({u.get('unit_type','?')})" for u in units[:20]]
        roster = "Units: " + ", ".join(names)
    else:
        roster = "Units: unknown"

    return COMPACT_SYSTEM_PROMPT.replace("{unit_roster}", roster)
