# FIELD MANUAL — KShU / TDG Tactical Rules Reference

> Authoritative reference for all simulation rules governing unit behavior, AI decision-making, and game mechanics.
> All formulas and thresholds are extracted from the implemented engine code (`backend/engine/`).
> This document describes **what units do** and **why**, in a format suitable for both human players and AI agents.

---

## Table of Contents

1. [Tick Sequence (Game Loop)](#1-tick-sequence)
2. [Movement](#2-movement)
3. [Detection & Observation](#3-detection--observation)
4. [Line of Sight (LOS)](#4-line-of-sight)
5. [Combat Resolution](#5-combat-resolution)
6. [Suppression](#6-suppression)
7. [Morale](#7-morale)
8. [Communications](#8-communications)
9. [Ammunition](#9-ammunition)
10. [Contact Management & Intelligence](#10-contact-management--intelligence)
11. [Terrain Effects](#11-terrain-effects)
12. [Obstacles & Map Objects](#12-obstacles--map-objects)
13. [Engineering Operations](#13-engineering-operations)
14. [Structure Effects (Logistics)](#14-structure-effects)
15. [Artillery Support (Automatic)](#15-artillery-support)
16. [Automatic Return Fire](#16-automatic-return-fire)
17. [Map Object Discovery](#17-map-object-discovery)
18. [Fog of War](#18-fog-of-war)
19. [Order Processing Pipeline](#19-order-processing-pipeline)
20. [Radio Chatter & Reports](#20-radio-chatter--reports)
21. [Red AI Doctrine & Behavior](#21-red-ai-doctrine--behavior)
22. [Unit Type Reference Tables](#22-unit-type-reference-tables)

---

## 1. Tick Sequence

Each **tick** represents a configurable time step (default: **60 seconds** of game time). All processing is **deterministic** — no randomness, no LLM involvement. The tick executes the following steps in strict order:

| Step | Phase | Description |
|------|-------|-------------|
| 0.5 | Red AI | Run Red AI agents (create orders for AI-controlled Red units) |
| 1 | Orders | Process pending/validated orders → assign tasks to units |
| 1b | Target Resolution | Units with attack/engage tasks but no target → find nearest known enemy contact |
| 2 | Movement | Execute movement for all units with movement tasks |
| 2a | Order Completion | Mark orders as completed when units arrive at destinations |
| 2b | Engineering | Process engineering tasks (breach, mine-lay, construct, bridge deploy) |
| 3 | Detection | Execute detection checks between opposing sides (uses LOS) |
| 3b | Object Discovery | Check if units have LOS to undiscovered map objects |
| 4 | Contact Decay | Mark stale contacts, expire old contacts |
| 4b | Artillery Support | Auto-assign idle artillery to support attacking units in CoC |
| 4c | Return Fire | Units under attack with no combat task auto-engage nearest attacker |
| 5 | Combat | Resolve combat — damage, suppression, destruction |
| 5b | Contact Cleanup | Remove contacts referencing destroyed units |
| 6 | Suppression Recovery | Recover suppression for units NOT under fire |
| 7 | Morale | Update morale — suppression erosion, casualty effect, recovery, break check |
| 8 | Communications | Update comms status — degradation from suppression, recovery |
| 9 | Ammo Consumption | Consume ammo for units that fired |
| 9b | Structure Effects | Apply resupply, comms bonuses from structures |
| 10 | Events & Reports | Persist events, generate auto-reports (SPOTREP, SHELREP, CASREP, SITREP, INTSUM) |
| 10c | Radio Chatter | Generate idle unit requests and peer support messages |
| 11 | Advance | Increment tick counter and game time |

---

## 2. Movement

### 2.1 Core Formula

```
effective_speed = base_speed
                × terrain_factor
                × obstacle_factor
                × (1 - suppression × 0.7)
                × morale_factor

distance_this_tick = effective_speed × tick_duration_seconds
```

### 2.2 Base Speed

Each unit type has two speed modes:

| Mode | Description |
|------|-------------|
| **Slow** (🐢) | Cautious/tactical movement. Better concealment, less fatigue. |
| **Fast** (⚡) | Rapid movement. Exposed, tiring, vehicles at higher speed. |

Speed is stored as `unit.move_speed_mps` (meters per second) and set when an order is issued.

**Speed table (m/s):**

| Unit Type | Slow | Fast | Notes |
|-----------|------|------|-------|
| `infantry_team` | 1.5 | 3.5 | Small team = faster |
| `infantry_squad` | 1.2 | 3.0 | |
| `infantry_section` | 1.2 | 3.0 | |
| `infantry_platoon` | 1.2 | 3.0 | ~4 / ~11 km/h |
| `infantry_company` | 1.0 | 2.5 | Larger = slower |
| `infantry_battalion` | 0.8 | 2.0 | Large formation |
| `mech_platoon` | 3.0 | 10.0 | ~11 / ~36 km/h |
| `mech_company` | 2.5 | 8.0 | |
| `tank_platoon` | 3.0 | 12.0 | ~11 / ~43 km/h |
| `tank_company` | 2.5 | 10.0 | |
| `artillery_battery` | 1.5 | 5.0 | |
| `artillery_platoon` | 1.5 | 5.0 | |
| `mortar_section` | 1.0 | 2.5 | Heavy load |
| `mortar_team` | 1.2 | 3.0 | |
| `at_team` | 1.2 | 3.0 | |
| `recon_team` | 2.0 | 4.0 | Scouts are faster |
| `recon_section` | 2.0 | 4.0 | |
| `sniper_team` | 1.0 | 2.5 | Stealthy |
| `observation_post` | 0.5 | 1.5 | Rarely moves |
| `headquarters` | 1.5 | 5.0 | |
| `command_post` | 1.0 | 3.0 | |
| `logistics_unit` | 2.0 | 6.0 | Trucks |
| `combat_engineer_platoon` | 1.2 | 3.0 | |
| `combat_engineer_section` | 1.2 | 3.0 | |
| `combat_engineer_team` | 1.5 | 3.5 | |
| `mine_layer_section` | 1.0 | 2.5 | |
| `mine_layer_team` | 1.2 | 3.0 | |
| `obstacle_breacher_team` | 1.2 | 3.0 | |
| `obstacle_breacher_section` | 1.0 | 2.5 | |
| `engineer_recon_team` | 2.0 | 4.0 | |
| `construction_engineer_platoon` | 0.8 | 2.0 | |
| `construction_engineer_section` | 0.8 | 2.0 | |
| `avlb_vehicle` | 2.0 | 6.0 | Armored vehicle |
| `avlb_section` | 2.0 | 6.0 | |
| *Default (unknown type)* | 1.2 | 3.0 | |

### 2.3 Morale Factor

| Morale Level | Factor |
|-------------|--------|
| > 0.50 | 1.0 (full speed) |
| 0.25 – 0.50 | 0.7 (impaired) |
| < 0.25 | 0.4 (severely impaired) |

### 2.4 Movement Task Types

The movement engine processes units with the following task types:
- `move` — standard movement to a point
- `attack` — advance toward attack target
- `advance` — advance toward objective
- `engage` — move toward engagement target (if out of weapons range)
- `fire` — move toward fire target (if out of range)

### 2.5 Arrival & Completion

When `distance_remaining ≤ distance_this_tick`:
- Unit snaps to the target position.
- If task type is `move`, the task is cleared and an `order_completed` event is generated.
- If task type is `attack`/`engage`/`fire`, the unit stops moving but the combat task remains active.

### 2.6 Pre-Movement Checks

Before movement, the engine checks (in order):

1. **Discovered minefield ahead**: If the movement path intersects a minefield that the unit's side has discovered, the unit **halts** and the task is cleared. Generates `minefield_avoidance` event. Units already *inside* a minefield are not halted (they need to get out).

2. **Water crossing without bridge**: Samples terrain along the path every ~50m. If `water` terrain is detected without a bridge (map object or OSM-sourced bridge terrain cell) within 60m, the unit **halts**. Both infantry and vehicles are blocked — engineering bridge unit required.

3. **Obstacle interaction**: Checks movement path against all active map objects (see [Section 12](#12-obstacles--map-objects)).

### 2.7 Vehicle Classification

The following unit types are classified as **vehicles** for obstacle passability rules:

```
tank_company, tank_platoon, tank_section,
mech_company, mech_platoon, mech_section,
avlb_vehicle, avlb_section,
artillery_battery, artillery_platoon,
logistics_unit, headquarters
```

All other unit types are classified as **infantry**.

---

## 3. Detection & Observation

### 3.1 Core Formula

```
effective_range = base_detection_range × terrain_visibility × weather_mod × height_bonus

For each opposing unit within effective_range:
  distance_factor = max(0, 1 - distance / effective_range)
  target_concealment = 0.5 + 0.5 × target_terrain_visibility
  prob = base_prob × distance_factor × posture_mod × recon_bonus × target_concealment
  prob = min(prob, 0.95)  # capped at 95%

  roll = deterministic_hash(tick, observer_id, target_id) mod 100 / 100
  if roll < prob → detection successful
```

### 3.2 Parameters

| Parameter | Value | Source |
|-----------|-------|--------|
| `base_detection_range` | `unit.detection_range_m` or 1500m default | Per-unit |
| `base_prob` | 0.6 (standard) / 0.8 (recon unit) | Unit capabilities |
| `recon_bonus` | 1.3 (recon) / 1.0 (standard) | Unit capabilities `is_recon` |
| `weather_mod` | `min(1.0, visibility_km / 5.0)` | Scenario environment |

### 3.3 Posture Modifier (Target)

| Target Activity | Modifier |
|----------------|----------|
| Moving (move, advance, attack) | 1.0 |
| Stationary (no task) | 0.6 |
| Dug in (defend, dig_in) | 0.3 |

### 3.4 Height Advantage

```
height_bonus = 1.0 + (observer_elevation - target_elevation) / 500
```

- +10% detection range per 50m height advantage.
- No penalty for being lower (minimum 1.0).

### 3.5 Target Concealment

Targets in obscuring terrain (forest, urban, scrub) are harder to detect:

```
target_concealment = 0.5 + 0.5 × target_terrain_visibility_factor
```

Where `target_terrain_visibility_factor` is the terrain visibility at the target's position (e.g. forest = 0.4, giving concealment = 0.7).

### 3.6 Position Accuracy

Detected contacts have position inaccuracy:

```
accuracy_m = max(50, distance × 0.05 + (1 - probability) × 200)
```

Higher confidence → lower accuracy error. Minimum 50m.

### 3.7 LOS Requirement

If LOS service is available, detection **requires** clear line-of-sight from observer to target. Terrain features (forests, buildings) block LOS according to [Section 4](#4-line-of-sight).

### 3.8 Eye Heights

Different unit types observe from different heights above ground:

| Unit Type | Eye Height (m) |
|-----------|---------------|
| `observation_post` | 8.0 |
| `tank_company`, `tank_platoon` | 3.0 |
| `mech_company`, `mech_platoon` | 2.8 |
| `recon_team`, `recon_section` | 3.0 |
| `sniper_team` | 2.5 |
| `headquarters`, `command_post` | 3.0 |
| `artillery_battery`, `artillery_platoon` | 2.5 |
| All others (infantry default) | 2.0 |

Higher eye height lets units see over obstacles (forests, buildings).

### 3.9 Deterministic Hash

Detection uses a deterministic hash `SHA256(tick:observer_id:target_id)` instead of random numbers. This ensures:
- Identical results on replay
- No exploitation through save/reload

---

## 4. Line of Sight

### 4.1 Algorithm

The LOS service casts rays from the observer's position outward:

1. **72 rays**, every 5° (full circle), from observer position
2. Step along each ray at intervals matching terrain cell resolution
3. At each step check:
   - LOS line elevation from observer eye-height to sample point
   - Ground elevation at sample point (from ElevationCells)
   - Terrain obstacle height (tall features block LOS)
4. Ray terminates at max range or when blocked
5. Returns polygon formed by ray endpoints (viewshed)

### 4.2 Terrain Obstacle Heights

Tall terrain features act as vertical walls even on flat ground:

| Terrain Type | Obstacle Height (m) |
|-------------|---------------------|
| Forest | 12.0 |
| Urban | 10.0 |
| Orchard | 5.0 |
| Scrub | 2.0 |

### 4.3 Visibility Budget

Rays have a visibility "budget" that starts at 1.0 and is reduced by terrain absorption. When the budget drops below **0.05**, the ray is terminated (fully blocked).

### 4.4 Point-to-Point LOS

The `has_los(observer → target)` function uses the same visibility absorption model as the viewshed computation. Used by:
- **Detection engine**: Units cannot detect targets without LOS
- **Map object discovery**: Units cannot discover objects without LOS

---

## 5. Combat Resolution

### 5.1 Core Formula

```
fire_effectiveness = base_firepower
                   × strength
                   × ammo_factor
                   × (1 - suppression)
                   × terrain_attack_mod

damage = fire_effectiveness × DAMAGE_SCALAR / target_protection
suppression_inflicted = fire_effectiveness × 0.03
```

Where `DAMAGE_SCALAR = 0.02` (~2% strength loss per tick under sustained fire).

### 5.2 Firepower by Unit Type

| Unit Type | Base Firepower |
|-----------|---------------|
| `infantry_team` | 3 |
| `infantry_squad` | 6 |
| `infantry_section` | 7 |
| `infantry_platoon` | 10 |
| `infantry_company` | 20 |
| `infantry_battalion` | 40 |
| `mech_platoon` | 15 |
| `mech_company` | 25 |
| `tank_platoon` | 25 |
| `tank_company` | 30 |
| `artillery_battery` | 35 |
| `artillery_platoon` | 25 |
| `mortar_section` | 20 |
| `mortar_team` | 12 |
| `at_team` | 15 |
| `recon_team` | 5 |
| `recon_section` | 7 |
| `sniper_team` | 4 |
| `observation_post` | 2 |
| `headquarters` | 5 |
| `command_post` | 3 |
| `combat_engineer_platoon` | 8 |
| `combat_engineer_section` | 5 |
| `logistics_unit` | 2 |
| *Default (unknown)* | 5 |

### 5.3 Weapon Range by Unit Type

| Unit Type | Range (m) |
|-----------|----------|
| `infantry_team` | 300 |
| `infantry_squad` | 400 |
| `infantry_platoon` | 600 |
| `infantry_company` | 800 |
| `infantry_battalion` | 1200 |
| `mech_platoon` | 1200 |
| `mech_company` | 1500 |
| `tank_platoon` | 2000 |
| `tank_company` | 2500 |
| `artillery_battery` | 5000 |
| `artillery_platoon` | 5000 |
| `mortar_section` | 3500 |
| `mortar_team` | 3000 |
| `at_team` | 2000 |
| `sniper_team` | 1000 |
| `recon_team` | 400 |
| `observation_post` | 300 |
| `headquarters` | 200 |
| `command_post` | 100 |
| `logistics_unit` | 100 |

Special ranges from `capabilities.atgm_range_m` and `capabilities.mortar_range_m` extend weapon range.

### 5.4 Ammo Factor

| Ammo Level | Factor |
|-----------|--------|
| > 0.50 | 1.0 |
| 0.20 – 0.50 | 0.7 |
| < 0.20 | 0.3 |

### 5.5 Target Protection

Protection is the **highest** of:
1. **Terrain protection** at target location (see [Section 11](#11-terrain-effects))
2. **Dug-in bonus**: If target task type is `dig_in` → protection = 2.0
3. **Map object protection**: Entrenchments (2.0×), pillboxes (3.0×), anti-tank ditches (1.3×), etc.

### 5.6 Auto-Targeting

If an attacker has an attack/engage/fire task but the specific target is not found:
1. Search all enemy units within weapon range
2. Select the **nearest** enemy
3. Update task with found target for future ticks

If no enemy is in range, the movement engine advances the unit toward the target location.

### 5.7 Destruction

When `target.strength ≤ 0.01`:
- Unit is marked `is_destroyed = True`
- Current task is cleared
- `unit_destroyed` event is generated

### 5.8 Fire Intensity Descriptions

| Damage per Tick | Description |
|----------------|-------------|
| < 0.005 | Ineffective fire |
| 0.005 – 0.015 | Light fire |
| 0.015 – 0.035 | Moderate fire |
| 0.035 – 0.06 | Heavy fire |
| ≥ 0.06 | Devastating fire |

### 5.9 Strength Categories

| Strength | Description |
|----------|-------------|
| > 0.85 | At full strength |
| 0.65 – 0.85 | Lightly damaged |
| 0.45 – 0.65 | Reduced to ~50% |
| 0.25 – 0.45 | Heavily damaged |
| ≤ 0.25 | Near destruction |

---

## 6. Suppression

### 6.1 How Suppression Is Applied

During combat resolution:
```
suppression_inflicted = fire_effectiveness × 0.03
target.suppression = min(1.0, current_suppression + suppression_inflicted)
```

Suppression ranges from 0.0 (none) to 1.0 (fully suppressed).

### 6.2 Suppression Recovery

Each tick where a unit is **NOT** under fire:
```
unit.suppression = max(0, unit.suppression - 0.05)
```

Recovery rate: **5% per tick** (one tick = ~1 minute). Full recovery from maximum suppression takes ~20 ticks (~20 minutes).

### 6.3 Suppression Effects

| System | Effect |
|--------|--------|
| **Movement speed** | `× (1 - suppression × 0.7)`. At full suppression → 30% speed. |
| **Combat effectiveness** | `× (1 - suppression)`. At full suppression → cannot fire. |
| **Engineering work** | `× max(0.2, 1 - suppression × 0.8)`. Breaching/construction slowed. |
| **Morale** | `-0.02 × suppression` per tick. High suppression erodes morale. |
| **Communications** | Suppression > 0.7 → comms may degrade to `degraded`. |

---

## 7. Morale

### 7.1 Morale Delta Calculation (Per Tick)

```
delta = 0

// Suppression erosion
delta -= 0.02 × suppression

// Casualty effects
if strength < 0.25:  delta -= 0.10
elif strength < 0.50: delta -= 0.05

// Combat stress / recovery
if under_fire: delta -= 0.01
else:          delta += 0.01

// Mutual support (friendly unit within 500m)
if friendly_nearby: delta += 0.02  (one bonus only)

// Enemy destroyed nearby (within 2km)
if enemy_destroyed_this_tick: delta += 0.05  (one bonus per tick)

// March fatigue (sustained movement > 10 consecutive ticks)
if marching > 10 ticks: delta -= 0.01 per tick

morale = clamp(morale + delta, 0.0, 1.0)
```

### 7.2 Morale Break

When morale drops below **0.15**:
- Unit **breaks** — current task is cleared
- Unit stops executing orders, will not respond to commands
- `morale_break` event generated: "Unit routing"

### 7.3 Morale Summary

| Threshold | Effect |
|-----------|--------|
| > 0.50 | Full speed, normal combat effectiveness |
| 0.25 – 0.50 | 70% movement speed, morale eroding from casualties |
| 0.15 – 0.25 | 40% movement speed, heavy casualty penalty |
| < 0.15 | **BROKEN** — unit routes, stops all tasks |

### 7.4 March Fatigue

The march counter (`current_task.march_ticks`) increments each tick a unit is moving or advancing. Resets when the unit stops. After **10 consecutive ticks** of movement, morale erodes by 0.01/tick.

### 7.5 Mutual Support Radius

Friendly units within **500 meters** provide mutual support bonus (+0.02 morale/tick). Only one bonus per unit per tick.

---

## 8. Communications

### 8.1 Status Levels

| Status | Effect on Orders |
|--------|-----------------|
| `operational` | Orders reach instantly |
| `degraded` | Order delivery delayed by 2 ticks |
| `offline` | Orders do not reach; unit continues last task |

### 8.2 Degradation Triggers

| Condition | Transition |
|-----------|-----------|
| Suppression > 0.7 (and currently operational) | → `degraded` |

### 8.3 Recovery

| Condition | Transition |
|-----------|-----------|
| Suppression ≤ 0.3 (and currently degraded) | → `operational` |
| Near a command post structure | → `operational` (forced) |

### 8.4 Impact on AI/Unit Behavior

- Units with `offline` comms cannot receive new orders
- Red AI skips units with `offline` comms
- Radio chatter messages are not generated for `offline` units

---

## 9. Ammunition

### 9.1 Consumption Rate

Each tick a unit fires (task type is `attack`, `engage`, or `fire`):
```
consumption = 0.01 × fire_rate_modifier
unit.ammo = max(0, ammo - consumption)
```

### 9.2 Fire Rate Modifiers

| Unit Type | Modifier |
|-----------|---------|
| `infantry_platoon` | 1.0 |
| `tank_company` | 1.5 |
| `mortar_section` | 2.0 |
| `mortar` | 2.0 |
| `at_team` | 0.5 |
| `recon_team` | 0.5 |
| `observation_post` | 0.2 |
| *Default* | 1.0 |

### 9.3 Ammo Depletion

When `ammo ≤ 0`:
- Unit cannot fire (combat effectiveness multiplier → 0 via ammo_factor)
- `ammo_depleted` event generated

### 9.4 Resupply

Ammo can be restored by:
- **Supply cache** map object: +0.05 ammo/tick to nearby friendly units (within effect radius)
- There is no manual resupply mechanic in the current simulation

---

## 10. Contact Management & Intelligence

### 10.1 Contact Creation

When detection succeeds, a Contact record is created with:
- `observing_side`, `observing_unit_id` — who detected
- `target_unit_id` — the actual enemy unit (internal tracking)
- `estimated_type` — observed unit type
- `location_estimate` — position with accuracy error
- `confidence` — detection probability
- `source` — `"visual"` or `"recon"`

### 10.2 Contact Update

If a contact for the **same target unit** already exists (matched by `target_unit_id`), it is updated rather than duplicated.

### 10.3 Contact Staleness

| Ticks Since Last Seen | State |
|----------------------|-------|
| ≤ 10 | Fresh |
| 11 – 30 | **Stale** (`is_stale = True`) |
| > 30 | **Expired** — contact deleted |

### 10.4 Contact Cleanup

When a unit is destroyed, all contacts referencing it (by `target_unit_id`) are deleted.

### 10.5 Contact-Based Targeting

Units with attack/engage tasks but no specific target location will automatically acquire the **nearest known fresh contact** from their side as their target destination.

---

## 11. Terrain Effects

### 11.1 Terrain Type Taxonomy (12 Types)

| Type | Movement | Visibility | Protection | Attack Mod | Source |
|------|----------|-----------|-----------|-----------|--------|
| `road` | 1.0 | 1.0 | 1.0 | 1.0 | OSM `highway=*` |
| `open` | 0.8 | 1.0 | 1.0 | 1.0 | ESA grassland |
| `forest` | 0.5 | 0.4 | 1.4 | 0.7 | ESA tree cover |
| `urban` | 0.4 | 0.5 | 1.5 | 0.8 | OSM buildings, ESA |
| `water` | 0.05 | 1.0 | 0.5 | 0.2 | OSM/ESA water |
| `fields` | 0.7 | 0.9 | 1.0 | 0.9 | ESA cropland |
| `marsh` | 0.3 | 0.8 | 0.8 | 0.5 | ESA wetland |
| `desert` | 0.7 | 1.0 | 0.8 | 1.0 | ESA bare/sparse |
| `scrub` | 0.6 | 0.7 | 1.2 | 0.8 | ESA shrubland |
| `bridge` | 1.0 | 1.0 | 0.8 | 0.9 | OSM `bridge=yes` |
| `mountain` | 0.3 | 0.6 | 1.5 | 0.6 | Slope > 20° |
| `orchard` | 0.5 | 0.6 | 1.2 | 0.8 | OSM orchard/vineyard |

### 11.2 How Terrain Affects Each System

| Factor | Used By | Effect |
|--------|---------|--------|
| **Movement** | Movement engine | Multiplied into effective speed |
| **Visibility** | Detection engine | Multiplied into effective detection range at observer position |
| **Protection** | Combat engine | Divides incoming damage for units at that location |
| **Attack** | Combat engine | Multiplied into fire effectiveness for attacker's position |

### 11.3 Elevation Effects

| Subsystem | Elevation Effect | Formula |
|-----------|-----------------|---------|
| **Movement** | Slope penalty | `slope_factor = max(0.2, 1.0 - slope_deg / 45)` |
| **Detection** | Higher ground bonus | `+10% range per 50m advantage` |
| **Combat** | Height advantage | `+15% effectiveness per 50m advantage (capped 0.7–1.5)` |
| **Terrain type** | Override | Slope > 20° → `mountain` |

### 11.4 Terrain Lookup Modes

1. **DB-backed cells**: TerrainCell records with snail_path → terrain_type. Uses fast spatial index with O(1) lookup.
2. **Legacy regions**: Scenario `terrain_meta` JSONB with bounding-box polygons.
3. **Default**: Everything is `"open"` if no terrain data.

### 11.5 Tactical Implications

| Terrain | Tactical Advice |
|---------|----------------|
| **Road** | Fastest movement but no concealment. Good for rapid advance, vulnerable to ambush. |
| **Forest** | Half-speed movement, excellent concealment (0.4 vis), good protection (1.4×). Ideal for defense and infiltration. |
| **Urban** | Slowest traversal (0.4×), best protection (1.5×), poor visibility. Urban combat favors defenders heavily. |
| **Water** | Nearly impassable (0.05×). Requires bridge or engineering support. |
| **Fields** | Good movement (0.7×), slight concealment reduction. Acceptable approach route. |
| **Marsh** | Very slow (0.3×), poor protection. Avoid for maneuver. |
| **Mountain** | Very slow (0.3×), excellent protection (1.5×). Defensive strongpoint but hard to reinforce. |

---

## 12. Obstacles & Map Objects

### 12.1 Obstacle Types

| Object | Geometry | Infantry Move | Vehicle Move | Damage/Tick | Breach Ticks | Notes |
|--------|----------|--------------|-------------|------------|-------------|-------|
| **Barbed wire** | Line | 0.15 (very slow) | 0.6 | 0.005 | 3 | Minor injury crossing |
| **Concertina wire** | Line | 0.05 (nearly blocked) | 0.4 | 0.01 | 5 | Razor wire |
| **Minefield** | Polygon | 0.3 | 0.2 | 0.08 | 8 | Heavy damage to all |
| **AT minefield** | Polygon | 0.6 | 0.1 | Inf: 0.02 / Veh: 0.12 | 10 | Devastating to vehicles |
| **Entrenchment** | Line | 0.7 | 0.5 | 0 | N/A | 2.0× protection bonus |
| **Anti-tank ditch** | Line | 0.4 | **Impassable** | 0 | 6 | Blocks vehicles |
| **Dragon's teeth** | Line | 0.6 | **Impassable** | 0 | 10 | Concrete barriers |
| **Roadblock** | Point (30m) | 0.5 | **Impassable** | 0 | 2 | Blocks vehicle traffic |

### 12.2 Structure Types

| Object | Effect Radius | Protection | Detection Bonus | Special |
|--------|-------------|-----------|----------------|---------|
| **Pillbox** | 30m | 3.0× | +200m | Fortified position |
| **Observation tower** | 50m | 1.0× | +500m | Major detection boost |
| **Field hospital** | 150m | 1.0× | — | +0.01 strength/tick |
| **Command post** | 200m | 1.5× | +100m | Prevents comms degradation |
| **Fuel depot** | 100m | 1.0× | — | Strategic supply point |
| **Supply cache** | 100m | 1.0× | — | +0.05 ammo/tick, +0.005 strength/tick |
| **Bridge** | 40m | 0.8× | — | Enables water crossing |
| **Airfield** | 200m | 1.0× | — | Strategic air operations |

### 12.3 Obstacle Interaction Rules

1. Path is checked from current position to target position
2. For line obstacles: buffered by `effect_radius_m` degrees
3. For polygon obstacles: direct intersection check
4. For point obstacles: distance check against `effect_radius_m`
5. If **impassable** for unit type → movement factor = 0, unit blocked
6. If passable but slow → lowest movement factor from all obstacles applies
7. Damage accumulates from all obstacles in path

---

## 13. Engineering Operations

### 13.1 Engineer Unit Types

| Capability | Unit Types |
|-----------|-----------|
| **Breach** | combat_engineer_platoon/section/team, obstacle_breacher_team/section, engineer_platoon/section, avlb_vehicle/section |
| **Mine clearing** | combat_engineer_platoon/section/team, obstacle_breacher_team/section, engineer_platoon/section |
| **Mine laying** | mine_layer_section/team, combat_engineer_platoon/section |
| **Construction** | construction_engineer_platoon/section, combat_engineer_platoon/section, engineer_platoon/section |
| **Bridge deploy** | avlb_vehicle, avlb_section, construction_engineer_platoon |

### 13.2 Breach Operation

- Task: `{type: "breach", target_object_id: UUID}`
- Must be within **150m** of the obstacle
- Progress: `1 / breach_ticks` per tick (modified by suppression)
- Suppression penalty: `× max(0.2, 1 - suppression × 0.8)`
- On completion: obstacle is deactivated (`is_active = false`)

### 13.3 Mine Laying

- Task: `{type: "lay_mines", geometry: GeoJSON, mine_type: "minefield"|"at_minefield"}`
- Progress: `1 / build_ticks` per tick (modified by suppression)
- On completion: creates a new MapObject (minefield polygon)

### 13.4 Construction

- Task: `{type: "construct", object_type: str, geometry: GeoJSON}`
- Progress: `1 / build_ticks` per tick (modified by suppression)
- On completion: creates a new MapObject (structure or obstacle)

### 13.5 Bridge Deployment

- Task: `{type: "deploy_bridge", target_location: {lat, lon}}`
- Must be within **200m** of target
- Progress: 0.5 per tick (2 ticks to deploy)
- On completion: creates a `bridge_structure` point MapObject

---

## 14. Structure Effects

### 14.1 Processing

Each tick, all active structure-type map objects check for nearby friendly units:
- Side check: neutral structures affect all, sided structures only affect same side
- Distance check: unit must be within `effect_radius_m`

### 14.2 Supply Cache Effects

- **Ammo resupply**: +0.05 ammo/tick (if ammo < 1.0)
- **Strength recovery**: +0.005 strength/tick (if strength < 1.0)
- Generates `resupply` event when ammo crosses the 0.5 threshold

### 14.3 Field Hospital Effects

- **Strength recovery**: +0.01 strength/tick (if strength < 1.0)

### 14.4 Command Post Effects

- **Comms restoration**: Forces comms status to `operational` regardless of suppression
- Generates `comms_change` event on status change

---

## 15. Artillery Support

### 15.1 Automatic Fire Support

The engine automatically assigns idle artillery units to support attacking units in their Chain of Command:

1. For each unit with an `attack`/`engage`/`fire` task:
2. Walk up the parent chain (up to **3 levels**)
3. Find artillery siblings (children of the same parent)
4. Requirements: same side, not destroyed, has ammo, idle (no fire task), target within weapon range
5. Assign `fire` task to the artillery unit targeting the same enemy

### 15.2 Artillery Unit Types

```
artillery_battery, artillery_platoon, mortar_section, mortar_team
```

### 15.3 Limits

- **One artillery unit** per requesting unit per tick
- Already-tasked artillery units are not reassigned

---

## 16. Automatic Return Fire

### 16.1 Rule

Units that are being attacked but have **no combat task** (`attack`/`engage`/`fire`) automatically engage their **nearest attacker**:

```
if unit is targeted by an attacker AND unit has no combat task:
    unit.current_task = {type: "engage", target_unit_id: nearest_attacker, auto_return_fire: true}
```

### 16.2 Purpose

Prevents units from standing idle while being shot at. This simulates the natural self-defense behavior of trained soldiers.

---

## 17. Map Object Discovery

### 17.1 Per-Side Discovery System

Map objects have per-side discovery flags:
- `discovered_by_blue` (boolean)
- `discovered_by_red` (boolean)

### 17.2 Default Visibility

| Object Category | Default State |
|----------------|--------------|
| **Obstacles** (mines, wire, ditches) | Hidden from both sides |
| **Structures** (buildings, bridges) | Revealed to both sides |

### 17.3 Discovery Mechanics

Each tick, for each undiscovered map object:
1. Check if any unit from that side is within detection range
2. Check if LOS exists from that unit to the object
3. If both conditions met → object is **permanently discovered** for that side

### 17.4 Fog-of-War Filtering

Non-admin players only receive map objects discovered by their side in API responses and WebSocket broadcasts.

---

## 18. Fog of War

### 18.1 Own-Side Units

Always **fully visible** with all stats (strength, ammo, morale, suppression, comms, task).

### 18.2 Enemy Units

Only visible if within detection range of at least one friendly unit AND LOS is clear. Visible enemy units show:
- Position, type, SIDC
- **Approximate strength** (quantized to 25% buckets: full/reduced/weakened/critical)
- No ammo, morale, suppression, comms, or task details

### 18.3 Admin/Observer

See **all** units from both sides with full details.

### 18.4 Strength Approximation

Enemy unit strength is quantized:

| Actual Strength | Shown As |
|----------------|----------|
| > 0.75 | "full" (1.0) |
| 0.50 – 0.75 | "reduced" (0.75) |
| 0.25 – 0.50 | "weakened" (0.50) |
| ≤ 0.25 | "critical" (0.25) |

---

## 19. Order Processing Pipeline

### 19.1 Order Lifecycle

```
pending → validated → executing → completed | failed | cancelled
```

### 19.2 Task Assignment from Orders

Orders are parsed into unit tasks using priority:
1. **Parsed intent** (`parsed_intent.action` + `parsed_intent.destination`)
2. **Parsed order** (`parsed_order.order_type` + `parsed_order.target_location`)
3. **Keyword fallback** from `original_text` (halt, stop, move, advance, attack, engage, defend, hold, observe, recon)

### 19.3 Order Precedence

For the same unit, the **most recent** order overrides older ones.

### 19.4 Speed Application

When a move-type order is processed, the speed label (`slow`/`fast`) is looked up in `UNIT_TYPE_SPEEDS` and written to `unit.move_speed_mps`.

### 19.5 Halt Orders

`halt` orders clear the unit's current task (`unit.current_task = None`).

---

## 20. Radio Chatter & Reports

### 20.1 Idle Radio Messages

When a unit completes its task and becomes idle:
- Broadcasts a radio message: "Objective complete, holding at [grid]. Awaiting orders."
- Only if comms are not `offline`
- Bilingual RU/EN templates

### 20.2 Peer Support Requests

When a unit is under fire AND (strength < 0.5 OR suppression > 0.5):
1. Broadcasts support request to CoC siblings (same parent)
2. One eligible sibling auto-acknowledges
3. **Cooldown**: 5 ticks between requests per unit
4. Only if comms are not `offline`

### 20.3 Auto-Generated Reports

| Report Type | Trigger | Content |
|------------|---------|---------|
| **SPOTREP** | New enemy contact detected | Observer, type, grid ref, confidence |
| **SHELREP** | Unit under significant fire | Unit name, grid, damage, strength |
| **CASREP** | Unit destroyed | Destroyed unit details |
| **SITREP** | Every 5 ticks (periodic) | Unit counts, avg strength/morale, ammo status, contacts |
| **INTSUM** | Every 10 ticks (periodic) | All known contacts with type, grid, confidence |

---

## 21. Red AI Doctrine & Behavior

### 21.1 Decision Interval

| Condition | Interval |
|-----------|---------|
| No human Red players | Every **3 ticks** |
| Human Red players present | Every **6 ticks** |

### 21.2 Decision Modes

1. **LLM-based** (GPT-4.1): Structured output → list of unit orders (if API key available)
2. **Rule-based fallback**: Deterministic heuristics (always available)

LLM output is **always validated** — only known unit IDs are accepted.

### 21.3 Doctrine Profiles

| Parameter | Aggressive | Balanced | Cautious | Defensive |
|-----------|-----------|---------|---------|-----------|
| Engage distance factor | 1.3 | 1.0 | 0.8 | 0.7 |
| Retreat threshold | 0.15 | 0.30 | 0.45 | 0.20 |
| Advance bias | 0.8 | 0.5 | 0.2 | 0.05 |
| Counter-attack threshold | 0.4 | 0.55 | 0.7 | 0.85 |
| Patrol range factor | 1.5 | 1.0 | 0.7 | 0.5 |
| Hold position bias | 0.1 | 0.4 | 0.7 | 0.95 |
| Pursuit aggression | 0.9 | 0.5 | 0.2 | 0.1 |
| Risk tolerance | 0.8 | 0.5 | 0.3 | 0.2 |

### 21.4 Rule-Based Decision Logic

#### Pre-Checks (Applied to Every Unit)

| Condition | Action |
|-----------|--------|
| Unit already has active move/attack/advance task | Keep current task (unless very close contact and aggressive doctrine) |
| Morale < 0.15 (broken) | Skip — unit not responding |
| Comms = `offline` | Skip — can't receive orders |
| Strength < retreat threshold | **Withdraw** toward rally point (mission target) at fast speed |

#### Mission-Type Behaviors

**HOLD / DEFEND**
- Stay at current position
- If enemy contact within engagement range AND advance_bias > 0.3 → engage
- Otherwise hold and wait

**PATROL**
- Cycle between mission waypoints (changing every 5 ticks)
- If enemy contact within 1000m AND risk_tolerance > 0.3 → engage
- Move to next waypoint at slow speed

**ATTACK / ADVANCE**
- If strength < retreat threshold → hold (too weak)
- If ammo < 0.1 → hold (no ammo)
- If contacts available → attack **nearest** contact
  - Speed: `fast` if advance_bias > 0.6, else `slow`
  - Engagement rules: `fire_at_will`
- If no contacts → advance to mission target

**WITHDRAW / RETREAT**
- Move to rally point (mission target) at **fast** speed
- If already at rally point (< 50m) → hold

#### Contact Diversion

During active movement, if a new contact appears within **500m** and doctrine's advance_bias > 0.6, the unit diverts to engage.

### 21.5 Knowledge State

Red AI operates with **limited information**:
- Only units controlled by this Red agent
- Only contacts detected by Red-side units
- Terrain, grid, elevation at unit positions
- **No** hidden Blue data is ever exposed to Red AI

---

## 22. Unit Type Reference Tables

### 22.1 Unit Status Derivation

| Priority | Condition | Status |
|----------|-----------|--------|
| 1 | `is_destroyed` or strength ≤ 0 | `destroyed` |
| 2 | Morale < 0.15 | `broken` |
| 3 | Suppression > 0.7 | `suppressed` |
| 4 | Task: attack/engage | `engaging` |
| 5 | Task: move/advance | `moving` |
| 6 | Task: retreat/withdraw | `retreating` |
| 7 | Task: defend/hold | `defending` |
| 8 | Task: observe/recon | `observing` |
| 9 | Task: support | `supporting` |
| 10 | No task | `idle` |

### 22.2 Personnel Count by Unit Type

| Unit Type | Personnel |
|-----------|----------|
| `headquarters` | 20 |
| `command_post` | 10 |
| `infantry_platoon` | 30 |
| `infantry_company` | 120 |
| `infantry_section` | 15 |
| `infantry_team` | 6 |
| `infantry_squad` | 6 |
| `tank_company` | 60 |
| `tank_platoon` | 15 |
| `mech_company` | 100 |
| `mech_platoon` | 30 |
| `artillery_battery` | 40 |
| `artillery_platoon` | 20 |
| `mortar_section` | 12 |
| `mortar_team` | 6 |
| `at_team` | 6 |
| `recon_team` | 6 |
| `recon_section` | 12 |
| `observation_post` | 4 |
| `sniper_team` | 2 |
| `engineer_platoon` | 30 |
| `engineer_section` | 15 |
| `logistics_unit` | 20 |
| `combat_engineer_platoon` | 30 |
| `combat_engineer_section` | 15 |
| `combat_engineer_team` | 8 |
| `mine_layer_section` | 10 |
| `mine_layer_team` | 5 |
| `obstacle_breacher_team` | 6 |
| `obstacle_breacher_section` | 12 |
| `engineer_recon_team` | 4 |
| `construction_engineer_platoon` | 30 |
| `construction_engineer_section` | 15 |
| `avlb_vehicle` | 4 |
| `avlb_section` | 8 |

### 22.3 Implied Tasks by Order Type

| Order Type | Implied Tasks |
|-----------|--------------|
| `move` | Maintain communication, report arrival |
| `attack` | Suppress enemy fire, establish fire superiority, consolidate after assault |
| `defend` | Improve positions, establish observation posts, prepare fire plan |
| `observe` | Maintain concealment, report all contacts, avoid engagement |
| `support` | Coordinate fires with supported element, maintain ammo supply |
| `withdraw` | Maintain contact until disengaged, establish rally point, report clear |
| `halt` | Establish local security, report status |
| `regroup` | Consolidate personnel, redistribute ammo, report readiness |
| `report_status` | Assess unit condition, count personnel and equipment |

---

## Appendix A: Key Constants Quick Reference

| Constant | Value | File |
|----------|-------|------|
| `DAMAGE_SCALAR` | 0.02 | `combat.py` |
| `SUPPRESSION_RECOVERY_RATE` | 0.05/tick | `suppression.py` |
| `STALE_CONTACT_THRESHOLD` | 10 ticks | `contacts.py` |
| `EXPIRE_CONTACT_THRESHOLD` | 30 ticks | `contacts.py` |
| `MUTUAL_SUPPORT_RADIUS` | 500m | `morale.py` |
| `MORALE_BREAK_THRESHOLD` | 0.15 | `morale.py` |
| `MARCH_FATIGUE_THRESHOLD` | 10 ticks | `morale.py` |
| `VICTORY_BOOST_RANGE` | 2000m | `morale.py` |
| `VICTORY_BOOST_VALUE` | +0.05 | `morale.py` |
| `COMMS_DEGRADE_SUPPRESSION` | > 0.7 | `comms.py` |
| `COMMS_RECOVER_SUPPRESSION` | ≤ 0.3 | `comms.py` |
| `DETECTION_PROB_CAP` | 0.95 | `detection.py` |
| `DEFAULT_DETECTION_RANGE` | 1500m | `detection.py` |
| `DEFAULT_EYE_HEIGHT` | 2.0m | `detection.py` |
| `MIN_VISIBILITY_BUDGET` | 0.05 | `los_service.py` |
| `NUM_LOS_RAYS` | 72 (5° each) | `los_service.py` |
| `RED_AI_DECISION_INTERVAL` | 3 ticks | `runner.py` |
| `SUPPORT_REQUEST_COOLDOWN` | 5 ticks | `radio_chatter.py` |
| `SITREP_INTERVAL` | 5 ticks | `report_generator.py` |
| `INTSUM_INTERVAL` | 10 ticks | `report_generator.py` |
| `DEFAULT_TICK_DURATION` | 60 seconds | `tick.py` |

---

## Appendix B: Event Types

| Event Type | Visibility | Description |
|-----------|-----------|-------------|
| `movement` | all | Unit moved this tick |
| `order_completed` | all | Unit arrived at destination |
| `order_issued` | all | New task assigned to unit |
| `combat` | all | Unit engaged another unit |
| `unit_destroyed` | all | Unit destroyed |
| `contact_new` | detecting side | New enemy contact detected |
| `contact_lost` | detecting side | Lost contact with enemy |
| `morale_break` | all | Unit morale broke, routing |
| `comms_change` | all | Communications status changed |
| `ammo_depleted` | all | Unit ran out of ammo |
| `obstacle_blocked` | all | Unit blocked by obstacle |
| `obstacle_damage` | all | Unit taking damage from obstacle |
| `minefield_avoidance` | all | Unit halted before discovered minefield |
| `water_blocked` | all | Unit halted at water without bridge |
| `engineering` | all | Engineering task progress/completion |
| `artillery_support` | all | Artillery unit firing in support |
| `object_discovered` | discovering side | Map object discovered via LOS |
| `resupply` | all | Unit resupplied from structure |
| `red_ai_decision` | admin | Red AI issued orders |
| `red_ai_error` | admin | Red AI decision failed |

---

*Last updated: 2026-04-09*
*Source: `backend/engine/`, `backend/services/red_ai/`, `backend/services/los_service.py`, `backend/services/visibility_service.py`*

