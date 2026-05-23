"""
Map object definitions – gameplay properties for obstacles and structures.

Each object type has:
  - category: "obstacle" or "structure"
  - geometry_type: expected geometry (LineString, Polygon, Point)
  - movement_factor: multiplier on unit movement speed (1.0=no effect, 0.0=impassable)
  - vehicle_passable: can vehicles (tank/mech) pass?
  - infantry_passable: can infantry pass?
  - damage_per_tick: strength damage to units inside/crossing per tick
  - protection_bonus: multiplier on protection for units inside/adjacent
  - detection_bonus_m: added detection range for units within effect_radius
  - effect_radius_m: radius of effect for point-type objects
  - breach_ticks: ticks required for engineers to breach/clear
  - build_ticks: ticks required for engineers to construct
  - description: human-readable description
"""

from __future__ import annotations

import math

from geoalchemy2.shape import to_shape

MAP_OBJECT_DEFS: dict[str, dict] = {
    # ═══ OBSTACLES ═══════════════════════════════════════
    "barbed_wire": {
        "category": "obstacle",
        "geometry_type": "LineString",
        "movement_factor_infantry": 0.15,   # very slow for infantry
        "movement_factor_vehicle": 0.6,     # vehicles can push through slowly
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.005,           # minor injury crossing
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 15,              # width of wire obstacle
        "breach_ticks": 3,
        "build_ticks": 2,
        "description": "Barbed wire entanglement — slows infantry, minor vehicle impediment",
        "color": "#8B4513",
        "dash_pattern": [6, 4],
    },
    "concertina_wire": {
        "category": "obstacle",
        "geometry_type": "LineString",
        "movement_factor_infantry": 0.05,   # nearly impassable
        "movement_factor_vehicle": 0.4,
        "vehicle_passable": True,
        "infantry_passable": True,           # technically passable but extremely slow
        "damage_per_tick": 0.01,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 10,
        "breach_ticks": 5,
        "build_ticks": 3,
        "description": "Concertina wire — razor wire, nearly impassable for infantry",
        "color": "#A0522D",
        "dash_pattern": [4, 3],
    },
    "minefield": {
        "category": "obstacle",
        "geometry_type": "Polygon",
        "movement_factor_infantry": 0.3,
        "movement_factor_vehicle": 0.2,
        "vehicle_passable": True,            # can enter but takes damage
        "infantry_passable": True,
        "damage_per_tick": 0.08,             # significant damage
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 0,                # uses polygon geometry directly
        "breach_ticks": 8,
        "build_ticks": 6,
        "description": "Anti-personnel/anti-tank minefield — heavy damage to crossing units",
        "color": "#FF4444",
    },
    "at_minefield": {
        "category": "obstacle",
        "geometry_type": "Polygon",
        "movement_factor_infantry": 0.6,     # infantry can navigate more carefully
        "movement_factor_vehicle": 0.1,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick_infantry": 0.02,
        "damage_per_tick_vehicle": 0.12,     # devastating to vehicles
        "damage_per_tick": 0.06,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 0,
        "breach_ticks": 10,
        "build_ticks": 8,
        "description": "Anti-tank minefield — primarily targets vehicles",
        "color": "#CC3333",
    },
    "entrenchment": {
        "category": "obstacle",
        "geometry_type": "LineString",
        "movement_factor_infantry": 0.7,     # slightly slows crossing
        "movement_factor_vehicle": 0.5,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 2.0,             # major protection for occupants
        "detection_bonus_m": 0,
        "effect_radius_m": 20,               # width of trench zone
        "breach_ticks": 0,                   # can't be "breached", only filled
        "build_ticks": 4,
        "description": "Trench/entrenchment — provides strong protection to occupying infantry",
        "color": "#5D4037",
        "dash_pattern": [8, 3, 2, 3],
    },
    "anti_tank_ditch": {
        "category": "obstacle",
        "geometry_type": "LineString",
        "movement_factor_infantry": 0.4,     # infantry can cross slowly
        "movement_factor_vehicle": 0.0,      # vehicles cannot cross
        "vehicle_passable": False,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.3,
        "detection_bonus_m": 0,
        "effect_radius_m": 15,
        "breach_ticks": 6,
        "build_ticks": 8,
        "description": "Anti-tank ditch — blocks vehicles, infantry crosses slowly",
        "color": "#795548",
        "dash_pattern": [10, 5],
    },
    "dragons_teeth": {
        "category": "obstacle",
        "geometry_type": "LineString",
        "movement_factor_infantry": 0.6,
        "movement_factor_vehicle": 0.0,      # vehicles cannot pass
        "vehicle_passable": False,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.2,
        "detection_bonus_m": 0,
        "effect_radius_m": 10,
        "breach_ticks": 10,                  # very hard to remove
        "build_ticks": 12,
        "description": "Dragon's teeth — concrete anti-vehicle barriers",
        "color": "#9E9E9E",
    },
    "roadblock": {
        "category": "obstacle",
        "geometry_type": "Point",
        "movement_factor_infantry": 0.5,
        "movement_factor_vehicle": 0.0,
        "vehicle_passable": False,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.3,
        "detection_bonus_m": 0,
        "effect_radius_m": 30,
        "breach_ticks": 2,
        "build_ticks": 1,
        "description": "Roadblock/checkpoint — blocks vehicle traffic on road",
        "color": "#FF9800",
    },

    # ═══ STRUCTURES ═══════════════════════════════════════
    "pillbox": {
        "category": "structure",
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 3.0,             # very strong protection
        "detection_bonus_m": 200,
        "effect_radius_m": 30,
        "breach_ticks": 0,
        "build_ticks": 20,
        "description": "Pillbox/bunker — fortified firing position with excellent protection",
        "color": "#616161",
    },
    "observation_tower": {
        "category": "structure",
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 500,            # major detection boost
        "effect_radius_m": 50,
        "breach_ticks": 0,
        "build_ticks": 8,
        "description": "Observation tower — greatly extends detection range for nearby units",
        "color": "#78909C",
    },
    "field_hospital": {
        "category": "structure",
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 150,
        "breach_ticks": 0,
        "build_ticks": 12,
        "resupply": {"strength": 0.01},      # slow strength recovery
        "description": "Field hospital — slowly restores strength to nearby friendly units",
        "color": "#E53935",
    },
    "command_post_structure": {
        "category": "structure",
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.5,
        "detection_bonus_m": 100,
        "effect_radius_m": 200,
        "breach_ticks": 0,
        "build_ticks": 6,
        "comms_bonus": True,                 # prevents comms degradation
        "description": "Command post — prevents comms degradation for nearby units",
        "color": "#1565C0",
    },
    "fuel_depot": {
        "category": "structure",
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 100,
        "breach_ticks": 0,
        "build_ticks": 8,
        "description": "Fuel depot — strategic supply point",
        "color": "#F57F17",
    },
    "airfield": {
        "category": "structure",
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 200,
        "breach_ticks": 0,
        "build_ticks": 50,
        "description": "Airfield — strategic air operations point",
        "color": "#37474F",
    },
    "supply_cache": {
        "category": "structure",
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 100,
        "breach_ticks": 0,
        "build_ticks": 4,
        "resupply": {"ammo": 0.05, "strength": 0.005},
        "description": "Supply cache — resupplies ammo and minor strength recovery to nearby friendly units",
        "color": "#8D6E63",
    },
    "bridge_structure": {
        "category": "structure",
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 0.8,
        "detection_bonus_m": 0,
        "effect_radius_m": 40,
        "breach_ticks": 0,
        "build_ticks": 15,
        "description": "Bridge — enables crossing over water obstacles",
        "color": "#757575",
    },
    # ═══ EFFECTS ════════════════════════════════════════════
    "smoke": {
        "category": "effect",
        "geometry_type": "Polygon",
        "movement_factor_infantry": 0.9,
        "movement_factor_vehicle": 0.9,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 100,
        "visibility_factor": 0.1,       # drastically reduces visibility
        "breach_ticks": 0,
        "build_ticks": 0,
        "description": "Smoke screen — drastically reduces visibility in area for ~3 minutes",
        "color": "#888888",
        "is_transient": True,           # expires after ticks_remaining
        "default_ticks": 3,
    },
    "fog_effect": {
        "category": "effect",
        "geometry_type": "Polygon",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 200,
        "visibility_factor": 0.15,      # very low visibility
        "breach_ticks": 0,
        "build_ticks": 0,
        "description": "Dense fog zone — severely reduces visibility, no movement penalty",
        "color": "#E0E0E0",
        "is_transient": True,
        "default_ticks": 6,
    },
    "fire_effect": {
        "category": "effect",
        "geometry_type": "Polygon",
        "movement_factor_infantry": 0.1,
        "movement_factor_vehicle": 0.2,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.03,        # burns units inside
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 80,
        "visibility_factor": 0.3,       # flames and heat haze reduce visibility
        "breach_ticks": 0,
        "build_ticks": 0,
        "description": "Area fire / wildfire — damages and slows units, reduces visibility from heat and smoke",
        "color": "#FF4400",
        "is_transient": True,
        "default_ticks": 5,
    },
    "chemical_cloud": {
        "category": "effect",
        "geometry_type": "Polygon",
        "movement_factor_infantry": 0.5,
        "movement_factor_vehicle": 0.8,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.05,        # toxic — heavy damage, especially to infantry
        "damage_per_tick_infantry": 0.06,
        "damage_per_tick_vehicle": 0.02, # vehicles provide some NBC protection
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 120,
        "visibility_factor": 0.2,       # opaque toxic cloud
        "breach_ticks": 0,
        "build_ticks": 0,
        "description": "Chemical / toxic gas cloud — heavy damage to infantry, reduced damage to vehicles (NBC protection), severely impairs visibility and movement",
        "color": "#AACC00",
        "is_transient": True,
        "default_ticks": 8,
    },
    # ═══ OBJECTIVES ══════════════════════════════════════
    "objective_point": {
        "category": "structure",        # uses existing DB category (no migration needed)
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 100,         # control radius
        "breach_ticks": 0,
        "build_ticks": 0,
        "description": "Tactical objective — capture by holding 100m radius for N ticks",
        "color": "#FFD700",
        "control_radius_m": 100,
        "capture_ticks": 5,
    },
    "objective_area": {
        "category": "structure",
        "geometry_type": "Polygon",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 0,
        "breach_ticks": 0,
        "build_ticks": 0,
        "description": "Tactical objective area — capture by holding for N ticks",
        "color": "#FFD700",
        "control_radius_m": 0,          # uses polygon geometry directly
        "capture_ticks": 8,
    },
    # ═══ AVIATION / LZ ═══════════════════════════════════
    "landing_zone": {
        "category": "structure",
        "geometry_type": "Point",
        "movement_factor_infantry": 1.0,
        "movement_factor_vehicle": 1.0,
        "vehicle_passable": True,
        "infantry_passable": True,
        "damage_per_tick": 0.0,
        "protection_bonus": 1.0,
        "detection_bonus_m": 0,
        "effect_radius_m": 150,
        "breach_ticks": 0,
        "build_ticks": 0,
        "description": "Helicopter Landing / Pickup Zone — risk from nearby enemies",
        "color": "#00FF80",
    },
}

# Quick lookup helpers
OBSTACLE_TYPES = [k for k, v in MAP_OBJECT_DEFS.items() if v["category"] == "obstacle"]
STRUCTURE_TYPES = [k for k, v in MAP_OBJECT_DEFS.items() if v["category"] == "structure"]
EFFECT_TYPES = [k for k, v in MAP_OBJECT_DEFS.items() if v["category"] == "effect"]
ALL_OBJECT_TYPES = list(MAP_OBJECT_DEFS.keys())

# Category mapping
def get_category(object_type: str) -> str:
    defn = MAP_OBJECT_DEFS.get(object_type)
    if defn:
        return defn["category"]
    return "obstacle"

def get_object_def(object_type: str) -> dict:
    return MAP_OBJECT_DEFS.get(object_type, MAP_OBJECT_DEFS["barbed_wire"])


# ── Objective control processing ─────────────────────────────────────────────

OBJECTIVE_TYPES = {"objective_point", "objective_area"}


def _units_in_radius(units: list, center_pt, radius_m: float, side: str) -> bool:
    """Return True if any non-destroyed unit of the given side is within radius_m of center_pt."""
    for unit in units:
        if unit.is_destroyed:
            continue
        u_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
        if u_side != side:
            continue
        if unit.position is None:
            continue
        try:
            u_pt = to_shape(unit.position)
        except Exception:
            continue
        dlat = (u_pt.y - center_pt.y) * 111_320.0
        dlon = (u_pt.x - center_pt.x) * (111_320.0 * math.cos(math.radians(center_pt.y)))
        dist = math.sqrt(dlat * dlat + dlon * dlon)
        if dist <= radius_m:
            return True
    return False


def process_objective_control(all_units: list, map_objects: list, tick: int) -> list[dict]:
    """
    Process objective capture mechanics for all objective map objects.

    Rules per objective per tick:
    - Both sides present → contested: both counters decay
    - Only blue present → blue counter increments, red counter resets
    - Only red present → red counter increments, blue counter resets
    - Neither side → both counters slowly decay
    - When a side's counter reaches capture_ticks_required → captured

    Returns list of event dicts for capture/loss events.
    """
    events = []

    for obj in map_objects:
        if obj.object_type not in OBJECTIVE_TYPES:
            continue
        if not obj.is_active:
            continue

        props = dict(obj.properties or {})
        capture_req = int(props.get("capture_ticks_required",
                                    MAP_OBJECT_DEFS[obj.object_type].get("capture_ticks", 5)))
        prev_controller = props.get("controlled_by", "neutral")

        # Determine effective control area
        if obj.geometry is None:
            continue
        try:
            obj_geom = to_shape(obj.geometry)
        except Exception:
            continue

        obj_center = obj_geom.centroid if obj_geom.geom_type != "Point" else obj_geom
        radius_m = float(MAP_OBJECT_DEFS[obj.object_type].get("control_radius_m", 100) or 100)
        if obj.object_type == "objective_area":
            # For polygon objectives, check containment rather than a fixed radius
            radius_m = max(radius_m,
                           math.sqrt(obj_geom.area) * 111_320.0 / 2 if obj_geom.area > 0 else 100)

        blue_present = _units_in_radius(all_units, obj_center, radius_m, "blue")
        red_present  = _units_in_radius(all_units, obj_center, radius_m, "red")
        contested    = blue_present and red_present

        ctrl_blue = int(props.get("control_ticks_blue", 0))
        ctrl_red  = int(props.get("control_ticks_red", 0))

        if contested:
            ctrl_blue = max(0, ctrl_blue - 1)
            ctrl_red  = max(0, ctrl_red  - 1)
        elif blue_present:
            ctrl_blue += 1
            ctrl_red  = 0
        elif red_present:
            ctrl_red  += 1
            ctrl_blue = 0
        else:
            ctrl_blue = max(0, ctrl_blue - 1)
            ctrl_red  = max(0, ctrl_red  - 1)

        new_controller = prev_controller
        if ctrl_blue >= capture_req:
            new_controller = "blue"
        elif ctrl_red >= capture_req:
            new_controller = "red"

        props["control_ticks_blue"] = ctrl_blue
        props["control_ticks_red"]  = ctrl_red
        props["controlled_by"]      = new_controller
        obj.properties = props

        if new_controller != prev_controller:
            label = props.get("objective_label") or obj.label or ""
            events.append({
                "event_type": "objective_captured",
                "actor_unit_id": None,
                "payload": {
                    "object_id": str(obj.id),
                    "label": label,
                    "captured_by": new_controller,
                    "lost_by": prev_controller,
                    "objective_value": int(props.get("objective_value", 1)),
                },
                "text_summary": f"Objective '{label}' captured by {new_controller}",
                "visibility": "all",
            })

    return events


def check_deterministic_victory(scenario_objectives: dict | None, map_objects: list) -> str | None:
    """
    Check deterministic victory conditions based on objective control.

    scenario_objectives.deterministic = {"blue_needs": N, "red_needs": N}
    Returns "blue", "red", or None.
    """
    det = (scenario_objectives or {}).get("deterministic", {})
    if not det:
        return None

    blue_count = sum(
        1 for o in map_objects
        if o.object_type in OBJECTIVE_TYPES
        and o.is_active
        and (o.properties or {}).get("controlled_by") == "blue"
    )
    red_count = sum(
        1 for o in map_objects
        if o.object_type in OBJECTIVE_TYPES
        and o.is_active
        and (o.properties or {}).get("controlled_by") == "red"
    )
    if blue_count >= det.get("blue_needs", 9999):
        return "blue"
    if red_count >= det.get("red_needs", 9999):
        return "red"
    return None


AVIATION_LZ_RISK = 0.4   # probability of casualty per tick on a suppressed/hot LZ
LZ_ENEMY_SUPPRESS_RADIUS_M = 300   # enemy within this range → LZ is hot

def process_lz_risk(all_units: list, map_objects: list, tick: int) -> list[dict]:
    """
    Check aviation units executing air_assault / casevac tasks.
    If their designated LZ has enemy forces within 300m → probabilistic casualty.
    Returns list of events.
    """
    from backend.engine.geo_utils import planar_offset_m
    from backend.engine._rng import deterministic_roll

    AVIATION_TASK_TYPES = {"air_assault", "casevac", "medevac"}
    events = []

    lz_objects = {str(o.id): o for o in map_objects if o.object_type == "landing_zone"}
    if not lz_objects:
        return events

    for unit in all_units:
        if unit.is_destroyed or unit.position is None:
            continue
        task = unit.current_task or {}
        if task.get("type") not in AVIATION_TASK_TYPES:
            continue

        lz_id = task.get("lz_id")
        if not lz_id or lz_id not in lz_objects:
            continue

        lz_obj = lz_objects[lz_id]
        lz_geom = to_shape(lz_obj.geometry)
        lz_pos = lz_geom.centroid if lz_geom.geom_type != "Point" else lz_geom
        props = dict(lz_obj.properties or {})

        # Check enemy proximity
        unit_side = unit.side.value if hasattr(unit.side, "value") else unit.side
        enemy_near = False
        for other in all_units:
            if other.is_destroyed or other.position is None:
                continue
            other_side = other.side.value if hasattr(other.side, "value") else other.side
            if other_side == unit_side:
                continue
            _, _, dist = planar_offset_m(lz_pos, to_shape(other.position))
            if dist < LZ_ENEMY_SUPPRESS_RADIUS_M:
                enemy_near = True
                break

        # Check if manually suppressed
        suppressed = bool(props.get("suppressed"))
        suppressed_until = props.get("suppressed_until_tick")
        if suppressed_until and tick > int(suppressed_until):
            suppressed = False
            props["suppressed"] = False
            lz_obj.properties = props

        if enemy_near and not suppressed:
            # Mark LZ as suppressed
            props["suppressed"] = True
            props["suppressed_until_tick"] = tick + 3
            lz_obj.properties = props

        if enemy_near or suppressed:
            roll = deterministic_roll(tick, unit.id)
            if roll < AVIATION_LZ_RISK:
                damage = 0.35
                unit.strength = max(0.0, (unit.strength or 1.0) - damage)
                events.append({
                    "event_type": "aviation_lz_casualty",
                    "actor_unit_id": unit.id,
                    "payload": {"lz_id": lz_id, "damage": damage, "suppressed": suppressed},
                    "text_summary": f"{unit.name} took casualties on hot LZ",
                    "visibility": unit_side,
                })

    return events
