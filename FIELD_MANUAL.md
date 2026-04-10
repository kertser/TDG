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
23. [Defensive Posture & Dig-In](#23-defensive-posture--dig-in)
24. [Rest & Recovery](#24-rest--recovery)
25. [Smoke Screens](#25-smoke-screens)
26. [Weather & Environment Effects](#26-weather--environment-effects)
27. [Night-Time Operations](#27-night-time-operations)
28. [Phased & Conditional Orders](#28-phased--conditional-orders)
29. [Unit Disband](#29-unit-disband)
30. [Area Effects](#30-area-effects)
31. [Disengage Order](#31-disengage-order)
32. [Grid Boundary Enforcement](#32-grid-boundary-enforcement)

**Appendices:**
- [A. Key Constants Quick Reference](#appendix-a-key-constants-quick-reference)
- [B. Event Types](#appendix-b-event-types)
- [C. Tactical Doctrine Reference](#appendix-c-tactical-doctrine-reference)
- [D. LLM Integration Points](#appendix-d-llm-integration-points)

---

## 1. Tick Sequence

Each **tick** represents a configurable time step (default: **60 seconds** of game time). All processing is **deterministic** — no randomness, no LLM involvement. The tick executes the following steps in strict order:

| Step | Phase | Description |
|------|-------|-------------|
| 0.5 | Red AI | Run Red AI agents (create orders for AI-controlled Red units) |
| 1 | Orders | Process pending/validated orders → assign tasks to units |
| 1b | Target Resolution | Units with attack/engage tasks but no target → find nearest known enemy contact |
| 1c | Effect Decay | Decrement active transient effect timers (smoke, fog, fire, chemical); dissipate expired effects |
| 1d | Weather/Night | Compute weather and night visibility/movement modifiers from scenario environment |
| 2 | Movement | Execute movement for all units with movement tasks (applies weather movement mod) |
| 2a | Order Completion | Mark orders as completed when units arrive at destinations |
| 2a2 | Conditional Orders | Check unit order queues for triggered conditions (task_completed, location_reached) |
| 2b | Engineering | Process engineering tasks (breach, mine-lay, construct, bridge deploy) |
| 3 | Detection | Execute detection checks between opposing sides (uses LOS, weather, night modifiers) |
| 3b | Object Discovery | Check if units have LOS to undiscovered map objects |
| 4 | Contact Decay | Mark stale contacts, expire old contacts |
| 4b | Artillery Support | Auto-assign idle artillery to support attacking units in CoC |
| 4c | Defense | Process dig-in progression for units in defensive posture |
| 4d | Return Fire | Units under attack with no combat task auto-engage nearest attacker |
| 5 | Combat | Resolve combat — damage, suppression, destruction |
| 5b | Contact Cleanup | Remove contacts referencing destroyed units |
| 6 | Suppression Recovery | Recover suppression for units NOT under fire |
| 7 | Morale | Update morale — suppression erosion, casualty effect, recovery, rest, break check |
| 8 | Communications | Update comms status — degradation from suppression, recovery |
| 9 | Ammo Consumption | Consume ammo for units that fired |
| 9b | Structure Effects | Apply resupply, comms bonuses from structures |
| 9c | Effect Damage | Apply damage from fire/chemical area effects to units inside them |
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
                × slope_factor
                × weather_movement_mod
                × (1 - suppression × 0.7)
                × morale_factor

distance_this_tick = effective_speed × tick_duration_seconds
```

- `slope_factor`: `max(0.2, 1.0 - slope_deg / 45)` — steep slopes dramatically slow movement.
- `weather_movement_mod`: 1.0 clear, 0.8 rain, 0.6 heavy rain/storm, 0.95 fog, 0.7 snow. Stacks with precipitation.

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
| Disengaging (break contact) | 0.5 |
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

### 3.10 Recon Concealment

Stationary reconnaissance, sniper, and observation units can enter **concealment mode** — making them extremely difficult to detect.

#### Concealment-Capable Unit Types

```
recon_team, recon_section, sniper_team, observation_post, engineer_recon_team
```

#### Concealment Conditions

A unit is concealed when **all** of the following are true:
- It is a concealment-capable type (listed above)
- It is **NOT** moving, attacking, engaging, firing, or disengaging
- Its morale is above 0.25 (panicked units can't maintain concealment)

#### Concealment Detection Rules

| Parameter | Normal Detection | Against Concealed Unit |
|-----------|-----------------|----------------------|
| **Max detection range** | `base_range × terrain × weather × night` | 300m × target_terrain_vis × weather × night |
| **Base probability** | 0.6 (0.8 for recon) | 0.10 |
| **Probability cap** | 0.95 | 0.25 |
| **Position accuracy** | `max(50, dist × 0.05 + (1-prob) × 200)` | `max(80, dist × 0.1 + (1-prob) × 300)` |
| **Smoke modifier** | Target ×0.1, Observer ×0.15 | ×0.05 |

#### Effective Concealment Ranges by Terrain

| Target Terrain | Terrain Visibility | Max Detection Range |
|---------------|-------------------|-------------------|
| Open/Road | 1.0 | 300m |
| Fields | 0.9 | 270m |
| Scrub | 0.7 | 210m |
| Mountain | 0.6 | 180m |
| Urban | 0.5 | 150m |
| Forest | 0.4 | 120m |

Weather and night further reduce these ranges multiplicatively.

#### Breaking Concealment

Concealment is broken immediately when a unit:
- Receives a move, advance, attack, engage, fire, or disengage order
- Has morale drop below 0.25 (broken/panicking)

#### Fog-of-War Integration

Concealment also affects fog-of-war filtering in the visibility service. Concealed enemies won't appear on the map unless an observer is very close, using the same reduced detection ranges.

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
| `combat_engineer_team` | 3 |
| `mine_layer_section` | 3 |
| `mine_layer_team` | 2 |
| `obstacle_breacher_team` | 4 |
| `obstacle_breacher_section` | 6 |
| `engineer_recon_team` | 3 |
| `engineer_platoon` | 6 |
| `engineer_section` | 4 |
| `construction_engineer_platoon` | 4 |
| `construction_engineer_section` | 3 |
| `avlb_vehicle` | 2 |
| `avlb_section` | 3 |
| `logistics_unit` | 2 |
| *Default (unknown)* | 5 |

### 5.3 Weapon Range by Unit Type

| Unit Type | Range (m) |
|-----------|----------|
| `infantry_team` | 300 |
| `infantry_squad` | 400 |
| `infantry_section` | 400 |
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
| `recon_section` | 500 |
| `observation_post` | 300 |
| `headquarters` | 200 |
| `command_post` | 100 |
| `combat_engineer_platoon` | 600 |
| `combat_engineer_section` | 600 |
| `combat_engineer_team` | 400 |
| `mine_layer_section` | 300 |
| `mine_layer_team` | 200 |
| `obstacle_breacher_team` | 400 |
| `obstacle_breacher_section` | 500 |
| `engineer_recon_team` | 400 |
| `engineer_platoon` | 400 |
| `engineer_section` | 300 |
| `construction_engineer_platoon` | 300 |
| `construction_engineer_section` | 200 |
| `avlb_vehicle` | 200 |
| `avlb_section` | 200 |
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

### 5.10 Area Fire (Indirect)

Artillery and mortar units can fire at a **grid location** rather than a specific enemy unit. This is called area fire.

```
Area fire effectiveness = base_firepower × strength × ammo_factor × (1 - suppression)
Proximity damage = effectiveness × DAMAGE_SCALAR × proximity_factor / target_protection
Proximity suppression = effectiveness × 0.04 × proximity_factor
```

| Parameter | Value |
|-----------|-------|
| **Blast radius** | 150m from target location |
| **Proximity factor** | `max(0.2, 1.0 - distance_to_center / 150)` |
| **Suppression multiplier** | 0.04 (vs 0.03 for direct fire — area fire is more suppressive) |
| **Eligible units** | `artillery_battery`, `artillery_platoon`, `mortar_section`, `mortar_team` |

Area fire damages all enemy units within the blast radius. Damage falls off with distance from the impact point. Friendly units within the blast radius are NOT hit (no friendly fire from area fire).

Visual impact effects are generated at the target location even if no enemy is hit, providing area suppression feedback.

### 5.11 Finite Salvos

Fire missions for artillery/mortar units have a **finite salvo count** (default: 3 salvos). Each tick of firing decrements `salvos_remaining` by 1. When salvos reach 0, the fire task auto-completes.

| Context | Default Salvos |
|---------|---------------|
| Player-ordered fire mission | 3 (or specified in order) |
| Auto-assigned artillery support | 3 |
| Order parser can override | Via `salvos` field in parsed_order |

When salvos are expended:
- Fire task is cleared (`current_task = None`)
- `order_completed` event generated with reason `salvos_expended`
- Unit becomes idle and available for new orders

### 5.12 Danger Close

Artillery/mortar units automatically **cease fire** if any friendly unit is within **50m** of the target location.

- Applies to both direct targeted fire and area fire
- Also checked during auto-assigned artillery support (friendly units are never assigned support fire that would endanger allies)
- Generates `ceasefire_friendly` event
- Fire task is cleared immediately

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
| `TICKS_PER_DIG_IN_LEVEL` | 3 ticks | `defense.py` |
| `MAX_DIG_IN_LEVEL` | 5 | `defense.py` |
| `REST_RECOVERY_SLOW` | +0.003 strength/tick | `morale.py` |
| `REST_RECOVERY_FAST` | +0.008 strength/tick (after 5 ticks) | `morale.py` |
| `SMOKE_DURATION` | 3 ticks | `map_objects.py` |
| `SMOKE_AMMO_COST` | 0.05 | `map_objects.py` |
| `SMOKE_VISIBILITY_FACTOR` | 0.1 | `map_objects.py` |
| `DANGER_CLOSE_RADIUS` | 50m | `combat.py` |
| `NIGHT_MOD_FULL` | 0.3 (21:00-05:00) | `tick.py` |
| `NIGHT_MOD_TWILIGHT` | 0.6 (dawn/dusk) | `tick.py` |
| `NVG_PENALTY_REDUCTION` | 50% | `detection.py` |
| `CONCEALMENT_MAX_RANGE_M` | 300m | `detection.py` |
| `CONCEALMENT_BASE_PROB` | 0.10 | `detection.py` |
| `CONCEALMENT_PROB_CAP` | 0.25 | `detection.py` |
| `AREA_FIRE_BLAST_RADIUS_M` | 150m | `combat.py` |
| `DEFAULT_FIRE_SALVOS` | 3 | `combat.py` |
| `COVER_SEARCH_RADIUS` | 800m | `movement.py` |

---

## 23. Defensive Posture & Dig-In

Units ordered to **defend** gradually improve their defensive position over time.

### 23.1 Dig-In Progression

```
Every 3 ticks of continuous defense → +1 dig-in level (max level 5)
Maximum dig-in achieved after 15 ticks of uninterrupted defense
```

| Dig-In Level | Ticks Required | Protection Multiplier |
|---|---|---|
| 0 | 0 | terrain_protection × 1.0 |
| 1 | 3 | terrain_protection × 1.2 |
| 2 | 6 | terrain_protection × 1.4 |
| 3 | 9 | terrain_protection × 1.6 |
| 4 | 12 | terrain_protection × 1.8 |
| 5 | 15 | terrain_protection × 2.0 (capped at 2.5) |

### 23.2 Detection Penalty

Dug-in units have a posture modifier of **0.3** (vs 0.6 stationary, 1.0 moving), making them very hard to detect.

### 23.3 Interruption

If a unit's task changes from "defend" to anything else, dig-in progress is **reset to zero**. Defending units should stay put.

**Source:** `backend/engine/defense.py`

---

## 24. Rest & Recovery

Units not in combat slowly recover strength and morale, representing reorganization and rest.

### 24.1 Conditions for Rest

- Unit has **no** combat task (not attack/engage/fire)
- Unit is **not** under fire this tick
- Strength is below 1.0

### 24.2 Recovery Rates

```
First 5 rest ticks:   +0.003 strength per tick (slow reorganization)
After 5 rest ticks:   +0.008 strength per tick (extended rest bonus)
Morale recovery:      standard +0.01/tick when safe, boosted +0.02 after 5 ticks
```

Rest ticks are tracked in `unit.capabilities.rest_ticks` and reset to 0 whenever the unit enters combat.

**Source:** `backend/engine/morale.py`

---

## 25. Smoke Screens

Artillery and mortar units can deploy smoke screens to conceal movement and block detection.

### 25.1 Smoke Deployment

- **Eligible units:** `artillery_battery`, `artillery_platoon`, `mortar_section`, `mortar_team`
- **API:** `POST /api/sessions/{id}/units/{uid}/fire-smoke` with `{lat, lon, radius_m}`
- **Cost:** 0.05 ammo per smoke round
- **Duration:** 3 ticks (~3 minutes of game time)
- **Radius:** Configurable; default 100m

### 25.2 Smoke Effects

| Subsystem | Effect |
|---|---|
| **Detection** | Target in smoke: ×0.1 detection probability. Observer in smoke: ×0.15 probability. |
| **Movement** | ×0.9 movement speed through smoke (minor slowdown) |
| **Combat** | No direct modifier, but detection reduction means enemies are harder to engage |

### 25.3 Smoke Decay

Each tick, `ticks_remaining` decrements. When it reaches 0, the smoke MapObject is deactivated and an `effect_dissipated` event is generated. The smoke polygon is removed from the map.

**Source:** `backend/engine/map_objects.py` (definition), `backend/engine/detection.py` (`_is_in_smoke`), `backend/engine/tick.py` (decay), `backend/api/map_objects.py` (`fire_smoke` endpoint)

---

## 26. Weather & Environment Effects

Scenario environment settings affect multiple game subsystems.

### 26.1 Weather Types and Modifiers

| Weather | Visibility Modifier | Movement Modifier |
|---|---|---|
| **Clear** | ×1.0 | ×1.0 |
| **Rain** | ×0.7 | ×0.8 (mud) |
| **Heavy Rain / Storm** | ×0.4 | ×0.6 (heavy mud) |
| **Fog** | ×0.3 | ×0.95 |
| **Snow** | ×0.6 | ×0.7 |

### 26.2 Precipitation (stacks with weather)

| Precipitation | Visibility Modifier | Movement Modifier |
|---|---|---|
| **None** | ×1.0 | ×1.0 |
| **Rain** | ×0.85 | ×0.9 |
| **Heavy Rain** | ×0.5 | ×0.7 |
| **Snow** | ×0.7 | ×0.75 |

### 26.3 Visibility Distance

Base visibility is derived from `scenario.environment.visibility_km`:
```
weather_mod = min(1.0, visibility_km / 5.0) × weather_type_modifier × precipitation_modifier
```

**Source:** `backend/engine/tick.py` (weather calculation)

---

## 27. Night-Time Operations

Game time determines day/night cycle, which significantly affects detection.

### 27.1 Time-Based Visibility

| Time Period | Night Modifier | Description |
|---|---|---|
| 07:00 – 19:00 | 1.0 | Full daylight |
| 05:00 – 07:00 | 0.6 | Dawn — moderate visibility reduction |
| 19:00 – 21:00 | 0.6 | Dusk — moderate visibility reduction |
| 21:00 – 05:00 | 0.3 | Night — heavy visibility reduction |

### 27.2 Night Vision

Units with `capabilities.has_nvg` or `capabilities.night_vision` benefit from reduced night penalty:
```
nvg_night_mod = 1.0 - (1.0 - night_mod) × 0.5
```
Effectively, NVG halves the night penalty (e.g., 0.3 → 0.65 instead of 0.3).

### 27.3 Combined Modifier

Detection uses the product of weather and night modifiers:
```
combined_visibility_mod = weather_mod × night_mod
effective_detection_range = base_range × terrain_visibility × combined_visibility_mod
```

**Source:** `backend/engine/tick.py` (night calculation), `backend/engine/detection.py` (NVG handling)

---

## 28. Phased & Conditional Orders

Orders can include multiple phases that activate sequentially based on conditions.

### 28.1 Order Queue

When an order contains multiple phases, the first phase becomes the unit's immediate task. Remaining phases are stored in `unit.order_queue` as a list of entries, each with:
- `task`: the action to perform (move, attack, defend, etc.)
- `condition`: when to activate this phase

### 28.2 Supported Conditions

| Condition Type | Trigger | Example |
|---|---|---|
| `task_completed` | Unit has no current task (previous task finished) | "Move to B4-3, then defend" |
| `location_reached` | Unit is at the specified snail path | "When at C6-6-8, advance to D4" |

### 28.3 Processing

Conditional orders are checked once per tick (step 2a2). When a condition is met:
1. The task is assigned to the unit
2. The entry is removed from the queue
3. A `conditional_order_activated` event is generated

**Source:** `backend/engine/tick.py` (`_process_conditional_orders`)

---

## 29. Unit Disband

Commanders can disband units that have sustained unsustainable losses.

### 29.1 Mechanics

- **API:** `POST /api/sessions/{id}/units/{uid}/disband`
- **Effect:** Unit is permanently destroyed (sets `is_destroyed = True`, clears task)
- **Authority:** Commander must have authority over the unit
- **Events:** Generates `unit_disbanded` event

### 29.2 UI

Right-click a unit → **⛔ Disband Unit** (only visible for non-admin players who can select the unit).

**Source:** `backend/api/units.py` (`disband_unit`)

---

## 30. Area Effects

Area effects are transient polygon-based hazards that affect units within their boundaries. They are placed by admin or created by game events (e.g., artillery smoke).

### 30.1 Effect Types

| Effect | Visibility Mod | Movement Mod | Damage/Tick (Infantry) | Damage/Tick (Vehicle) | Duration (Ticks) |
|--------|---------------|-------------|----------------------|---------------------|-----------------|
| **Smoke** | ×0.1 | ×0.9 | 0 | 0 | 3 |
| **Fog** | ×0.15 | ×1.0 (no penalty) | 0 | 0 | 6 |
| **Fire** | ×0.3 | ×0.1 (nearly blocked) | 0.03 (3%/tick) | 0.03 (3%/tick) | 5 |
| **Chemical Cloud** | ×0.2 | ×0.5 | 0.05–0.06 (5–6%/tick) | 0.02 (2%/tick) | 8 |

### 30.2 Effect Mechanics

- **Visibility**: Detection engine applies visibility modifiers from all active effects at both observer and target positions.
- **Movement**: Movement engine applies speed penalties when units traverse effect areas.
- **Damage**: Each tick, units inside fire or chemical effects take damage. Infantry suffers more than vehicles from chemical agents.
- **Decay**: Each tick, `ticks_remaining` decrements by 1. When it reaches 0, the effect is deactivated and an `effect_dissipated` event is generated.

### 30.3 Effect Placement

- **Admin**: Place effects from the admin panel "Effects" section (with default durations).
- **Artillery smoke**: Created via `POST /units/{id}/fire-smoke` API. Uses existing fire-smoke mechanics.

**Source:** `backend/engine/map_objects.py` (definitions), `backend/engine/tick.py` (decay + damage), `backend/engine/detection.py` (visibility), `backend/engine/movement.py` (speed penalty)

---

## 31. Disengage Order

The **disengage** order allows units to break contact with the enemy and retreat to cover.

### 31.1 Order Recognition

| Language | Keywords |
|----------|---------|
| English | disengage, break contact |
| Russian | разорвать контакт, выйти из боя, отцепиться |

### 31.2 Execution Sequence

1. Unit stops all combat tasks immediately
2. Speed is set to **fast** (withdrawal speed)
3. Engine searches for nearest **covered terrain** within 800m (forest, urban, scrub, orchard, mountain)
4. If cover found → unit moves there at fast speed, then switches to **defend**
5. If no cover found → unit holds current position and defends

### 31.3 Special Rules

| Rule | Detail |
|------|--------|
| **Auto-return fire** | Disengaging units do **NOT** auto-return fire (they're trying to break contact) |
| **Posture modifier** | 0.5 (lower visibility while retreating — trying to stay low) |
| **Concealment break** | Disengaging breaks concealment for recon/sniper units |
| **Cover search** | Samples terrain cells within 800m for `COVER_TERRAIN_TYPES` (forest, urban, scrub, orchard, mountain). Also samples 8 directions at 200m and 400m for protection factor if no cells available. |

**Source:** `backend/engine/movement.py` (cover search + movement), `backend/engine/detection.py` (posture modifier), `backend/engine/tick.py` (auto-return-fire skip)

---

## 32. Grid Boundary Enforcement

Units cannot move outside the defined grid/operations area.

### 32.1 Validation Points

| Check Point | Behavior |
|-------------|----------|
| **Order submission** | Target location validated against grid bounds. If outside, unit responds with `unable_area` radio message. |
| **Movement engine** | Double-checks target at tick time. Cancels movement if target is outside grid bounds. |

### 32.2 Response

When a target is outside the area of operations:
- English: *"Cannot comply. Target outside area of operations."*
- Russian: *"Не могу выполнить. Указанная цель за пределами района операции."*

**Source:** `backend/services/grid_service.py` (`is_point_inside_grid`), `backend/engine/movement.py`, `backend/api/orders.py`

---

## Appendix B: Event Types

| Event Type | Visibility | Description |
|-----------|-----------|-------------|
| `movement` | all | Unit moved this tick |
| `order_completed` | all | Unit arrived at destination or task finished |
| `order_issued` | all | New task assigned to unit |
| `conditional_order_activated` | all | Conditional/phased order triggered |
| `combat` | all | Unit engaged another unit |
| `unit_destroyed` | all | Unit destroyed |
| `unit_disbanded` | all | Unit disbanded by commander |
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
| `ceasefire_friendly` | all | Artillery ceased fire — friendly near target |
| `dig_in_progress` | all | Unit improved defensive position |
| `effect_dissipated` | all | Area effect expired (smoke, fog, fire, chemical) |
| `effect_damage` | all | Unit taking damage from area effect (fire/chemical) |
| `fire_out_of_range` | all | Artillery target out of weapon range |
| `object_discovered` | discovering side | Map object discovered via LOS |
| `resupply` | all | Unit resupplied from structure |
| `casualty_report` | unit side | Post-combat status report |
| `game_finished` | all | Game ended (turn limit or victory condition) |
| `red_ai_decision` | admin | Red AI issued orders |
| `red_ai_error` | admin | Red AI decision failed |

---

## Appendix C: Tactical Doctrine Reference

> **This section is the SINGLE SOURCE OF TRUTH for tactical doctrine.**
> `backend/prompts/tactical_doctrine.py` reads this section at startup and injects it
> into LLM prompts. All edits to tactical doctrine MUST be made here — never in Python code.
>
> HTML comment markers delimit the text extracted for LLM injection.
> Do NOT remove or rename the DOCTRINE markers below.

<!-- DOCTRINE:FULL:START -->
### C.1 Principles of Combat

1. **Concentration of Force**: Mass combat power at the decisive point. Never attack equally along the whole front — create local superiority (3:1 for assault, 6:1 for fortified positions).
2. **Economy of Force**: Use minimum force on secondary objectives. Accept risk in less critical areas to concentrate strength where it matters.
3. **Maneuver**: Position forces to gain advantage. Avoid frontal assaults when flanking or envelopment is possible. Movement creates opportunity.
4. **Unity of Command**: Coordinate all elements toward a single objective. Supporting fires, maneuver, and reserves must work together.
5. **Security**: Protect your force. Maintain reconnaissance ahead and on flanks. Never advance without knowing what is in front of you.
6. **Surprise**: Strike where and when the enemy does not expect. Use terrain, timing, and deception.
7. **Simplicity**: Clear, simple plans are more reliable than complex ones under stress.

### C.2 Fire and Maneuver

**Fire and maneuver is the fundamental tactic of combined arms combat.**

#### C.2.1 Bounding Overwatch
- One element moves while another provides covering fire.
- The moving element advances to a position, then covers the other element's movement.
- Use when contact is expected or in open terrain.

#### C.2.2 Base of Fire + Maneuver Element
- Designate a **base of fire** (typically heavier weapons, artillery, or a fixing element) to suppress the enemy.
- The **maneuver element** (typically infantry or armor) moves to a flanking or assault position while the enemy is suppressed.
- The base of fire shifts or lifts fire when the maneuver element is about to assault.

#### C.2.3 Fire Support Coordination
- Artillery/mortar units should fire in **support of** attacking units, not independently.
- Request fire BEFORE the assault to suppress defenders (preparatory fires).
- Shift fires to depth targets or flanks as the assault begins.
- **Danger close**: Never fire within 50m of friendly troops. Cease fire if friendlies advance into the beaten zone.

#### C.2.4 Combined Arms Principles

| Unit Type | Primary Role | Employment Principle |
|-----------|-------------|---------------------|
| **Infantry** | Close combat, hold terrain | Assault complex terrain (forest, urban), defend, patrol. Slow but protected in close terrain. |
| **Armor** (tanks, mech) | Shock action, breakthrough | Attack in open terrain, exploit success, counterattack. Vulnerable in close terrain. |
| **Artillery/mortar** | Fires, suppression, area denial | Support attacks from range, suppress defenders before assault, area fire. Most effective vs stationary targets. |
| **Recon/sniper/OP** | Intelligence, observation | Find the enemy. Recon pulls, it does not push. Avoid decisive engagement. Concealment mode makes them nearly invisible. |
| **Engineers** | Mobility/counter-mobility | Breach obstacles (3-10 ticks), lay mines, build bridges (2 ticks). Enable and deny movement. |
| **Logistics** | Sustainment | Resupply ammo. Position near supply caches. Protect from enemy action. |
| **HQ/Command** | Command and control | Central position, near command post structures (prevents comms degradation). Must be protected. |

### C.3 Offensive Operations

#### C.3.1 Movement to Contact
- Use when enemy position is unknown.
- Advance with reconnaissance forward, main body following.
- Formation: advance guard (1/3 force), main body (2/3 force).
- On contact: fix with advance guard, develop the situation, then attack with main body.
- Speed: typically "slow" for advance guard (concealment), "fast" for main body on commitment.

#### C.3.2 Deliberate Attack
- Used against known, prepared enemy positions.
- Requires preparation: reconnaissance, fire plan, coordinated timing.
- Phases:
  1. **Isolation** — cut off enemy reinforcement/retreat
  2. **Suppression** — neutralize enemy fires with artillery/mortar (preparatory fires)
  3. **Assault** — close with and destroy (maneuver element attacks, base of fire supports)
  4. **Consolidation** — secure the objective, prepare for counterattack
- Attack ratio: minimum **3:1** local superiority at the point of attack.

#### C.3.3 Hasty Attack
- Seize a fleeting opportunity before the enemy can prepare.
- Minimal preparation, speed is paramount.
- Accept higher risk for speed of action.
- Particularly effective immediately after enemy withdrawal or during confusion.

#### C.3.4 Flanking Maneuver
- Avoid the enemy's strength (frontal defenses), strike the flank or rear.
- Requires a **fixing element** to hold the enemy's attention from the front.
- The flanking element must move concealed (use terrain: forests, folds in ground, defilade).
- Timing is critical — attack simultaneously from front and flank.
- Combined arms: armor flanks in open terrain, infantry flanks through close terrain.

#### C.3.5 Pursuit
- When enemy begins to withdraw, transition to pursuit immediately.
- Maintain pressure to prevent enemy from reorganizing.
- Direct pressure (follow the retreating enemy) + encirclement (cut off retreat routes).
- Use fastest units (armor, mechanized) for pursuit on open terrain.

### C.4 Defensive Operations

#### C.4.1 Hasty Defense
- Occupy the best available terrain quickly.
- Prioritize: fields of fire, cover and concealment, obstacle integration.
- Dig in immediately — each tick of digging improves protection (+20% per level, up to +100% at level 5, capped at 2.5× total).
- Select reverse slope positions when possible (protected from direct fire, observer on crest).

#### C.4.2 Deliberate Defense
- Time to prepare: dig in fully (15 ticks = 5 levels), site weapons, plan fires, prepare obstacles.
- Organize in depth: security zone (forward) → main battle area → reserve.
- Integrate obstacles (minefields, wire) with fire — obstacles without covering fire are merely a delay.
- Prepare counterattack plans for when the enemy is weakened.

#### C.4.3 Defense in Depth
- Multiple defensive lines, each with mutual support.
- The enemy breaks through one line but faces the next.
- Trade space for time, wear down the attacker.
- Mutual support radius: 500m between units provides morale bonus.

#### C.4.4 Key Terrain
- Control terrain that provides advantage:
  - **Hilltops**: observation (+10% detection per 50m), fields of fire (+15% effectiveness per 50m)
  - **Road junctions**: movement control, block enemy mobility corridors
  - **Bridges**: chokepoints, deny water crossing to enemy
  - **Forest edges**: concealment (0.4 visibility) with fields of fire into open terrain
  - **Urban areas**: best protection (1.5×), favors defenders heavily
- Deny key terrain to the enemy even at cost. Terrain advantage multiplies combat power.

### C.5 Reconnaissance and Security

#### C.5.1 Reconnaissance Principles
- **Recon pulls**: reconnaissance units advance to find the enemy, not push through them.
- Report all contacts immediately — intelligence is their primary product.
- Maintain concealment: stationary recon/sniper/OP units in concealment mode are nearly undetectable (max 300m detection range).
- Do NOT decisively engage — recon units break contact when discovered (disengage order).
- Concealment-capable types: `recon_team`, `recon_section`, `sniper_team`, `observation_post`, `engineer_recon_team`.

#### C.5.2 Screening
- Observe and report enemy activity along a front or flank.
- Provide early warning of enemy approach.
- Engage only to delay; withdraw before being decisively engaged.

#### C.5.3 Flank Security
- Always protect exposed flanks, especially during offensive operations.
- Assign observation posts or recon elements to watch flanks.
- Move reserves to threatened flanks rapidly.

### C.6 Command and Control

#### C.6.1 Commander's Intent
- Every order should convey: (1) the objective, (2) the method, (3) the endstate.
- Subordinates who lose communications should act in accordance with the last known commander's intent.

#### C.6.2 Decentralized Execution
- Issue mission-type orders: tell subordinates WHAT to achieve, not HOW to do it.
- Subordinate leaders make tactical decisions within the commander's intent.

#### C.6.3 Communications Discipline
- Keep radio messages brief, clear, and authenticated.
- Report: (1) position, (2) activity, (3) status, (4) contacts, (5) requests.
- Degraded/offline comms: unit continues last task, cannot receive new orders.
- Units with `offline` comms do NOT respond to orders — they execute last known task.

#### C.6.4 Chain of Command
- Units operate within their chain of command (CoC).
- Artillery support is requested through CoC — parent walks up 3 levels to find artillery siblings.
- Peer support: units in same CoC (siblings) can be called for mutual assistance when under fire.
- When no CoC assignments exist, any same-side player can command any unit.

### C.7 Terrain Utilization Doctrine

#### C.7.1 Cover and Concealment
- **Cover** protects from fire: forest (1.4×), urban (1.5×), mountain (1.5×), entrenchment (2.0×), pillbox (3.0×).
- **Concealment** hides from observation: forest (0.4 vis), urban (0.5 vis), scrub (0.7 vis).
- Best positions combine both: forest edge looking across open ground.

#### C.7.2 Fields of Fire
- Open terrain provides clear fields of fire but no protection.
- Position on terrain edges: forest edge → open ground, hilltop → slopes.
- Weapon systems dictate optimal engagement ranges (infantry 300-800m, tanks 2000-2500m, artillery 3500-5000m).

#### C.7.3 Mobility Corridors
- Roads: fastest (1.0×) but predictable, exposed, vulnerable to ambush.
- Open: good (0.8×) with maneuver options.
- Forest/urban: slow (0.4-0.5×) but concealed approach.
- Water: impassable (0.05×) without bridge. Bridges are critical infrastructure.
- Marsh: very slow (0.3×), poor protection. Avoid for maneuver forces.

#### C.7.4 Obstacle Integration
- Obstacles (minefields, wire, ditches) are most effective when covered by fire.
- An uncovered obstacle is merely a delay — the enemy will breach it.
- Layer obstacles in depth: slow the enemy, channel them into kill zones.
- Engineering units breach obstacles (3-10 ticks depending on type).
- Discovered minefields cause units to halt — engineering support required.

### C.8 Smoke and Obscurants

- Artillery/mortar can fire smoke screens (3-tick duration, 100m radius).
- Smoke reduces detection to ×0.1 — effectively blinds the area.
- Tactical uses:
  1. **Conceal movement** across open terrain (smoke between own forces and enemy observation)
  2. **Screen a withdrawal** (smoke between retreating units and pursuing enemy)
  3. **Isolate** part of enemy position before assault (smoke flanking positions, assault center)
- Smoke costs 0.05 ammo per round — use judiciously.
- Movement in smoke is slightly reduced (×0.9).

### C.9 Night Operations

- Night (21:00-05:00): detection ×0.3 — dramatic reduction in visibility.
- Dawn/dusk (05:00-07:00, 19:00-21:00): detection ×0.6.
- Units with NVG capability: night penalty halved (×0.65 instead of ×0.3).
- Night favors: infiltration, recon, surprise attacks, withdrawal.
- Night disadvantages: navigation harder, coordination more difficult, friendly fire risk.

### C.10 Weather Effects on Operations

| Weather | Visibility | Movement | Tactical Implication |
|---------|-----------|----------|---------------------|
| Clear | ×1.0 | ×1.0 | Normal operations |
| Rain | ×0.7 | ×0.8 (mud) | Reduces visibility; slower movement but better concealment |
| Storm | ×0.4 | ×0.6 (heavy mud) | Severely limits operations; favors defense |
| Fog | ×0.3 | ×0.95 | Excellent concealment for movement; poor for observation |
| Snow | ×0.6 | ×0.7 | Moderate impact; tracks compromise concealment |

- Exploit bad weather for concealed movement approaches.
- Avoid major offensive operations in storms — coordination is nearly impossible.
- Fog is an opportunity for surprise attacks and infiltration.

### C.11 Combat Decision Framework (METT-T)

When making tactical decisions, evaluate:

1. **M**ission: What is the objective? What must be achieved?
2. **E**nemy: Where is the enemy? What is their strength, disposition, activity?
3. **T**errain: What terrain advantages can I exploit? What restricts movement?
4. **T**roops: What is my strength? Ammo? Morale? Available reserves?
5. **T**ime: How urgent is this? Can I prepare, or must I act immediately?

**Decision priorities (in order):**

| Priority | Condition | Action |
|----------|-----------|--------|
| 1 | Unit strength < 15% (broken) | Unit unresponsive — cannot be commanded |
| 2 | Unit comms offline | Unit continues last task — cannot receive orders |
| 3 | Unit strength < retreat threshold | Withdraw to rally point / covered position |
| 4 | Unit ammo < 10% | Cannot attack — defend or withdraw |
| 5 | Contact close (< 500m) + aggressive doctrine | Engage immediately |
| 6 | Contact detected + fire support available | Suppress with artillery before assault |
| 7 | No contacts + objective assigned | Advance toward objective with recon forward |
| 8 | Outnumbered | Seek terrain advantage, request support, delay |
| 9 | Idle units | Assign observation, patrolling, or reserve role |
| 10 | All objectives achieved | Consolidate, dig in, prepare for counterattack |

### C.12 Logistics and Sustainment

- **Ammo**: Units consume ammo each tick in combat (0.01 × fire_rate). At 0 ammo, units cannot fire.
- **Supply caches**: +0.05 ammo/tick and +0.005 strength/tick to friendly units within ~100m.
- **Field hospitals**: +0.01 strength/tick for recovery.
- **Rest**: Units out of combat slowly recover: +0.003 strength/tick (first 5 ticks), +0.008/tick (after 5 rest ticks).
- **Doctrine**: Position logistics assets in protected rear areas, close enough to support front-line units. Protect them from enemy action — logistics are high-value targets.
<!-- DOCTRINE:FULL:END -->

---

### C.13 Tactical Doctrine (Brief / Condensed)

> This condensed version is injected into cost-sensitive LLM calls (order parser).
> Delimited by DOCTRINE:BRIEF markers below.

<!-- DOCTRINE:BRIEF:START -->
#### Key Tactical Principles
- **Fire and Maneuver**: One element suppresses while another moves. Never advance without covering fire.
- **Combined Arms**: Infantry clears complex terrain, armor provides shock in open terrain, artillery suppresses from range, recon finds the enemy.
- **Concentration of Force**: Attack with local superiority (3:1 minimum). Never spread forces evenly.
- **Terrain**: Use cover (forest 1.4× protection, urban 1.5×) and concealment (forest 0.4 vis, urban 0.5 vis). Higher ground = advantage.
- **Security**: Always screen flanks. Recon forward before advancing. Never advance blind.

#### Order Types and Implied Tasks
- **Move**: Maintain comms, report arrival, expect contact en route.
- **Attack**: Suppress enemy, establish fire superiority, consolidate after assault.
- **Defend**: Dig in, improve positions, prepare fire plan, establish OPs.
- **Observe**: Maintain concealment, report contacts, avoid engagement.
- **Fire**: Compute fire solution, observe and adjust. Danger close = 50m.
- **Withdraw**: Maintain contact while disengaging, establish rally point.
- **Disengage**: Break contact immediately, seek covered position, suppress during withdrawal.

#### Unit Employment
- Infantry: assault complex terrain, hold ground, patrol.
- Armor: attack in open terrain, exploit breakthroughs, counterattack.
- Artillery/mortar: support attacks from range (area fire: 150m blast radius). Max 3 salvos per fire mission.
- Recon/sniper/OP: find enemy, report. Nearly undetectable when concealed and stationary (300m max detection). Do NOT engage decisively.
- Engineers: breach obstacles, lay mines, deploy bridges.

#### Decision Priorities
1. Preserve force (withdraw if strength < 30%).
2. Cannot attack with no ammo (< 10%).
3. Broken units (morale < 15%) do not respond.
4. Use terrain advantage — height, cover, concealment.
5. Coordinate fires with maneuver.
<!-- DOCTRINE:BRIEF:END -->

---

## Appendix D: LLM Integration Points

> **FIELD_MANUAL.md is the SINGLE SOURCE OF TRUTH for all tactical doctrine.**
> `tactical_doctrine.py` reads from this file at startup — it contains no doctrine text of its own.

| LLM Consumer | Doctrine Level | File | Purpose |
|-------------|---------------|------|---------|
| **Red AI Commander** | Full (Appendix C.1–C.12) | `backend/prompts/red_commander.py` | Drives tactical decision-making for AI-controlled Red forces. Injected into system prompt with doctrine-specific behavior instructions. |
| **Order Parser** | Brief (Appendix C.13) | `backend/prompts/order_parser.py` | Helps LLM understand military terminology, order types, and implied tasks when parsing player radio messages. |
| **Doctrine Profiles** | Posture-specific | `backend/services/red_ai/doctrine.py` | Each doctrine profile (aggressive/balanced/cautious/defensive) includes posture-specific behavior rules referencing field manual principles. |
| **Intent Interpreter** | Rules-only (no LLM) | `backend/services/intent_interpreter.py` | Deterministic rules encode tactical doctrine (formations, implied tasks, constraints). No LLM call — 100% cost savings. |
| **Response Generator** | Templates-only | `backend/services/response_generator.py` | Template-based unit radio responses. Uses military radio protocol but no doctrine injection needed. |
| **Rule-based Red AI Fallback** | Embedded | `backend/services/red_ai/agent.py` | When LLM unavailable, rule-based decision engine applies doctrine heuristics (combined arms, recon employment, artillery support). |

### D.1 Doctrine Flow (Single Source of Truth)

```
FIELD_MANUAL.md                          ◄── THE SINGLE SOURCE OF TRUTH
│
├── Appendix C.1–C.12 (full doctrine)
│   extracted at startup via markers:
│   <!-- DOCTRINE:FULL:START/END -->
│       │
│       └──▶ tactical_doctrine.py ──▶ TACTICAL_DOCTRINE_FULL
│               │
│               ├──▶ Red AI Commander system prompt
│               │
│               └──▶ (available for any future LLM consumer)
│
├── Appendix C.13 (brief doctrine)
│   extracted at startup via markers:
│   <!-- DOCTRINE:BRIEF:START/END -->
│       │
│       └──▶ tactical_doctrine.py ──▶ TACTICAL_DOCTRINE_BRIEF
│               │
│               └──▶ Order Parser system prompt
│
└── Sections 1–32 (game mechanics/rules)
        │
        └──▶ Referenced by engine code (deterministic, not LLM)

doctrine.py (posture profiles)
│
└──▶ prompt_instruction per posture ──▶ Red AI Commander system prompt
     (behavioral parameters — HOW aggressive/cautious to apply the doctrine)
```

### D.2 How to Update Tactical Doctrine

1. **Edit FIELD_MANUAL.md** — Appendix C sections only.
2. Do NOT edit `tactical_doctrine.py` — it reads from FIELD_MANUAL.md automatically.
3. Keep content between the DOCTRINE FULL START/END markers (Appendix C.1–C.12).
4. Keep content between the DOCTRINE BRIEF START/END markers (Appendix C.13).
5. Restart the backend to pick up changes (doctrine is loaded at import time).
6. Test with: `python -c "from backend.prompts.tactical_doctrine import get_tactical_doctrine; print(len(get_tactical_doctrine('full')))"`

---

*Last updated: 2026-04-10*
*Source: `backend/engine/`, `backend/services/red_ai/`, `backend/services/los_service.py`, `backend/services/visibility_service.py`, `backend/prompts/tactical_doctrine.py`*

