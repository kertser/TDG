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

