# TDG — Tactical Decision Game Platform

Web-based multiplayer tactical command/staff exercise platform with AI-controlled opponent forces,
collaborative map drawing, terrain intelligence, and structured order understanding.

## Features

- **Interactive Tactical Map** — Leaflet-based map with MIL-STD-2525D military symbols (milsymbol.js), zoom-scaled markers, grid overlay with recursive snail subdivision, height tops (▲) with elevation numbers
- **Fog of War** — Server-authoritative visibility filtering via PostGIS `ST_DWithin` + terrain-aware LOS viewshed; players only see enemy units within line-of-sight; recon/sniper units in concealment mode are nearly invisible; enemy unit type and echelon masked (only broad category shown)
- **LOS Viewshed** — Ray-casting based visibility polygons replace simple circles; terrain obstacles (forests, buildings) block line-of-sight; unit-type-specific eye heights; visibility absorption model
- **Tactical A* Pathfinding** — Terrain-aware movement trajectories over depth-1 terrain cells (~333m resolution); considers terrain cost, slope, minefields, enemy avoidance, cover preference, friendly proximity; speed-mode-aware routing (slow=concealment, fast=speed); waypoints computed immediately on order and recalculated every 5 ticks; smooth Catmull-Rom spline rendering on frontend
- **Collaborative Overlays** — Real-time synchronized drawing tools (arrows, polylines, rectangles, markers, ellipses, measurement) via WebSocket; shared across all sides
- **Terrain Intelligence** — Automatic terrain classification from OSM Overpass + ESA WorldCover + Open-Elevation API; 12-type taxonomy with military modifiers; admin manual painting; SSE progress streaming; height tops detection
- **Rules Engine** — Deterministic tick-based simulation: movement (unit-type-specific slow/fast speeds with A* pathfinding), detection (LOS-based with recon concealment), combat (direct + area fire with finite salvos, combat role coordination), morale, suppression, ammo, communications, disengage/break contact, defensive dig-in, rest & recovery, resupply
- **Combat Role Coordination** — Units attacking the same enemy auto-coordinate: suppress (~40%, covering fire at weapon range), assault (1–2 infantry close in), flank (60° offset approach via covered terrain). Radio announces roles.
- **Artillery-Infantry Coordination** — Three-tier friendly fire prevention: proactive ceasefire request at 250m, danger-close auto-stop at 50m, area-fire friendly check. Artillery supports both attacking and defending units. Explicit fire request system. Auto-artillery-request on target acquisition.
- **Tactical Map Objects** — Static battlefield objects: barbed wire, minefields, entrenchments, roadblocks, pillboxes, bridges, command posts, fuel depots, airfields (rotatable), etc. NATO-style markers with per-side discovery system
- **Area Effects** — Transient polygon-based hazards: smoke (blocks detection), fog (reduces visibility), fire (damages units, blocks movement), chemical clouds (heavy infantry damage). All effects decay over time. Combat impact visual effects (explosions).
- **Resupply System** — Supply caches (+10% ammo/tick), logistics units (mobile +8% ammo/tick), field hospitals (+1% strength/tick). Resupply order type with auto-movement to nearest supply source.
- **Chain of Command** — Hierarchical unit tree with command authority enforcement, unit assignment, drag-and-drop hierarchy editing, split/merge, authority checks
- **Admin Panel** — Floating admin window with session wizard (4-step: Setup → Participants → Terrain → Done), god view, unit dashboard, scenario builder, CoC editor, terrain analysis controls, unit type editor, debug log, area effects placement
- **Order System** — Text order submission with AI-powered parsing (GPT-4.1, bilingual EN/RU), deterministic intent interpretation, 3-tier cost-optimized routing (keyword → nano → full LLM), unit radio responses with tactical assessment, smart formation suggestion, height/coordinate/snail location resolution, immediate task assignment
- **Radio Chat** — Tactical radio channel between session commanders with recipient selection, three channel filters (All / 💬 Chat / 📡 Units), and unread indicator. Auto-generated unit radio chatter: idle reports, peer support requests, casualty reports, artillery fire exchanges, coordinated attack planning, contact-during-advance halt/resume
- **Reports** — Five auto-generated report types: SPOTREP (enemy contacts), SHELREP (under fire), CASREP (unit destroyed), SITREP (periodic status), INTSUM (intelligence summary). Bilingual RU/EN. Unread badge on sidebar tab.
- **AI Victory Referee** — LLM-based victory evaluation every 5 ticks against scenario objectives. Game turn limit support. Auto-finish on victory or turn limit.
- **Red AI Opponents** — AI commander agents with 4 doctrine profiles (aggressive/balanced/cautious/defensive), limited knowledge (no Blue leaks), LLM decisions with rule-based fallback
- **Game Log** — Append-only event timeline, reports panel with channel filtering, app log (separated from tactical data)
- **Editable Config** — Unit type definitions and display constants stored in JSON config files (`unit_types.json`, `units_config.json`) instead of hardcoded JavaScript

## Quick Start

### 1. Prerequisites
- Python 3.12+
- Docker & Docker Compose

### 2. Start infrastructure
```powershell
docker compose up -d
```
This launches PostgreSQL + PostGIS (port 5432) and Redis (port 6379).

> **Note:** If upgrading from a previous version that used `kshu` as the DB name,
> run `docker compose down -v` first to remove the old volume, then `docker compose up -d`.

### 3. Install Python dependencies
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configure environment
```powershell
Copy-Item .env.example .env
# Edit .env and set your OPENAI_API_KEY
```

### 5. Seed the database (creates tables + sample scenario)
```powershell
python -m scripts.seed_scenario
```

### 6. Start the backend
```powershell
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### 7. Open the frontend
Navigate to `http://localhost:8000` in your browser.

## Usage

1. Enter a callsign and password, then click **Register** (first time) or **Login**
2. Click a session from the list to join (sessions are created by the admin)
3. Click **Start Session** to initialize units from the scenario
4. Military unit symbols appear on the map (filtered by your side's fog of war)
5. Use the **map control panel** (top-right) to toggle drawing tools, grid, units, overlays, contacts, labels, and terrain
6. **Draw overlays**: select a tool (arrow, polyline, rectangle, marker, ellipse, measure) and draw on the map; overlays sync in real-time via WebSocket
7. **Command units**: left-click to select, shift+click for multi-select, left-drag for rubber-band mass selection, alt+click to cycle stacked units; right-click for context menu (move slow 🐢/fast ⚡, formation, split, merge, rename, assign)
8. **Submit orders** in the **📡 Orders** tab of the bottom command panel (select units first, or click **👥 All**)
9. **Radio chat** in the **📻 Radio** tab — send tactical messages to specific commanders or broadcast to all; filter by channel (All / 💬 Chat / 📡 Units)
10. **Advance simulation** by clicking **Execute Orders** — units move along A*-optimized paths, detect enemies, fight with coordinated roles, and report back via radio
11. View events and reports in the sidebar tabs (**Events**, **Reports** with unread badge); click the **📋 session name** for scenario briefing
12. Reference **height tops** in orders: *"Move toward height 170"* / *"Выдвинуться к высоте 170"*

### Admin Panel
Press the admin button (🔑) and enter the admin password to access:
- **Session** — start/pause/tick controls, session creation wizard (4 steps), delete all units, reset session
- **Monitor** — god view (see all units on both sides), unit dashboard with focus/edit/delete/split/merge, debug log toggle (detailed tick-by-tick engine data)
- **Builder** — interactive scenario builder with map-click unit placement, grid configuration, save/load, save session → scenario
- **CoC** — full chain of command hierarchy editor with drag-and-drop reparenting, bulk assign/unassign
- **Users** — manage session participants
- **Types** — unit type editor with live SIDC preview (modify speeds, ranges, personnel, eye heights)
- **Terrain** — analyze terrain (OSM + ESA + elevation), paint cells manually, clear/reload
- **Effects** — place area effects: smoke, fog, fire, chemical clouds (transient polygon hazards)

### Map Objects (Tactical Obstacles & Structures)
When admin panel is open:
- Place obstacles (barbed wire, minefields, entrenchments, AT ditches, dragon's teeth) and structures (pillboxes, bridges, command posts, fuel depots, airfields, observation towers, etc.)
- **Airfield rotation**: drag the orange ↻ handle at the runway end to rotate to any angle
- Right-click objects for context menu: activate/deactivate, toggle Blue/Red discovery, delete
- Drag objects to reposition (center handle for points/airfields, centroid handle for lines/polygons)
- Objects have per-side discovery: obstacles hidden by default, revealed when a unit's LOS reaches them

### Terrain Intelligence
From the admin panel's terrain tab:
1. Select analysis depth (1–4, where depth 2 ≈ 111m cells)
2. Click **Analyze** — progress streams via SSE in real-time
3. Toggle terrain overlay visibility from the map control panel
4. Paint individual cells with the terrain painting tool
5. Terrain modifiers (movement, visibility, protection, attack) feed into the simulation engine
6. **Height tops** (▲) automatically detected and displayed on map with elevation numbers
7. A* pathfinding graph auto-built and persisted after terrain analysis

## Game Rules & Simulation

### Tick-Based Simulation
The game advances in discrete ticks (default: 1 minute of game time per tick). Each tick processes:
1. **Red AI** → AI agents make decisions for Red-controlled units
2. **Orders** → validated orders assign tasks to units; immediate task assignment on confirmation
3. **Pathfinding** → A* waypoints computed for all moving units (terrain-aware, enemy-avoiding)
4. **Movement** → units follow A* waypoints at type-specific speeds modified by terrain, slope, suppression, morale, and weather. Units halt at discovered minefields and rivers without bridges. Contact-during-advance: moving units halt on enemy detection and request orders.
5. **Detection** → LOS-based visibility checks between opposing sides; new contacts created/updated; recon concealment applied
6. **Map Object Discovery** → units reveal hidden obstacles/structures within their LOS
7. **Stale Contacts** → old contacts decay and eventually expire
8. **Artillery Support** → idle artillery auto-assigned to support attacking or defending units in CoC. Explicit fire requests processed first. Ceasefire coordination with advancing infantry.
9. **Defense** → dig-in progression for defending units
10. **Return Fire** → units under attack auto-engage nearest attacker (except disengaging units)
11. **Combat** → engaged units exchange fire with coordinated roles (suppress/assault/flank). Damage, suppression based on firepower, terrain, ammo. Area fire 150m blast radius. Finite salvos (default 3). Danger close at 50m.
12. **Suppression Recovery** → units not under fire gradually recover
13. **Morale** → suppression and casualties erode morale; safety and nearby friendlies restore it; units break below 15%; destroying enemies boosts nearby morale; march fatigue
14. **Communications** → heavy suppression can degrade comms; offline units continue last task
15. **Ammo & Resupply** → ammo consumed per fire tick; supply caches and logistics units resupply nearby friendlies
16. **Events & Reports** → notable state changes logged; auto-reports generated (SPOTREP, SHELREP, CASREP, SITREP, INTSUM)
17. **Radio Chatter** → idle unit reports, peer support requests, casualty reports, artillery fire exchanges, coordinated attack planning, contact-during-advance messages
18. **Area Effects** → fire/chemical cloud damage applied; effect durations tick down; expired effects removed
19. **Victory Check** → LLM evaluates victory conditions every 5 ticks; turn limit checked
20. **Broadcast** → updated state pushed to all connected clients via WebSocket

### Movement
- Each unit type has unique **slow** (tactical) and **fast** (rapid) movement speeds in m/s
- **Tactical A* pathfinding**: units navigate terrain-aware paths that avoid minefields, enemy observation, and impassable terrain
- Speed mode affects routing: **slow** prefers concealed routes (forest, urban), **fast** prefers roads and open terrain
- `effective_speed = base_speed × terrain_factor × slope_factor × (1 - suppression × 0.7) × morale_factor × weather_mod`
- **Terrain factors**: road=1.0, open=0.8, forest=0.5, urban=0.4, water=0.05, fields=0.7, marsh=0.3, etc.
- **Slope penalty**: `max(0.2, 1.0 - slope_deg/45)` — steep terrain dramatically slows movement
- Map objects affect movement: minefields damage/slow, barbed wire slows, etc.
- Units halt before discovered minefields (request engineers) and at water without bridges
- **Contact during advance**: moving (non-attack) units halt on enemy detection and request orders; resume after 3 ticks if no new orders

### Detection & LOS
- **Viewshed-based**: 72-ray cast from unit position, terrain obstacles (forests, buildings) block view
- **Eye height**: unit-type-specific (observation post=8m, tanks=3m, infantry=2m default)
- **Detection probability**: `base_prob × (1 - distance/range) × posture_mod × recon_bonus × concealment`
- **Recon concealment**: Stationary recon/sniper/OP units are nearly invisible (max 300m detection range, 10% base probability, 25% cap)
- Deterministic hash ensures reproducibility for replay

### Combat
- `fire_effectiveness = base_firepower × strength × ammo_factor × (1 - suppression) × terrain_mod`
- Elevation advantage: +15% effectiveness when firing from higher ground
- **Combat role coordination**: Multiple units attacking the same enemy auto-coordinate — suppress (40%, covering fire at range), assault (1–2 infantry close in), flank (60° offset via covered terrain)
- **Area fire**: Artillery/mortar can fire at grid locations — 150m blast radius, damage falls off with distance
- **Finite salvos**: Fire missions limited to 3 salvos (configurable), then auto-complete
- **Danger close**: Artillery auto-ceases fire if friendly within 50m of target
- **Ceasefire coordination**: Infantry approaching a friendly bombardment zone (250m) halts and requests cease-fire; artillery finishes last salvo, infantry resumes
- **Artillery support**: Auto-assigned to support both attacking and defending units in the CoC. Explicit fire requests override standby orders. Auto-request on target acquisition.
- Indirect fire units (mortars, artillery) have extended range shown as dashed circles
- **Auto-return fire**: Units under attack with no orders engage nearest attacker (except disengaging units)

### Unit Types
36 unit types defined in `frontend/config/unit_types.json`, each with:
- MIL-STD-2525D SIDC codes (Blue + Red variants)
- Slow/fast movement speeds (m/s)
- Detection range, fire range, personnel count
- Eye height for LOS calculations
- Indirect fire flag (mortars, artillery)

## Configuration Files

| File | Purpose |
|---|---|
| `frontend/config/unit_types.json` | Unit type registry: SIDC codes, speeds, ranges, personnel, eye heights |
| `frontend/config/units_config.json` | Display/behavior constants: status icons, formations, movement arrows, selection params |
| `backend/config.py` | Server configuration: DB URL, Redis, API keys |
| `.env` | Environment variables (secrets, overrides) |

## Testing

### Tactical Scenario Tests
Automated tactical scenario tests validate engine behavior (movement, detection, combat, coordination):

```powershell
# Run all tactical scenarios (requires running backend infrastructure)
python -m scripts.tactical_tests.run_all

# The test runner will:
# 1. Create a test session with units and orders
# 2. Execute the specified number of ticks
# 3. Evaluate assertions (events, detections, unit state)
# 4. Generate an HTML report: tactical_test_report.html
```

**10 test scenarios** covering:
- Basic movement (unit-type-specific speeds)
- Armored breakthrough (combined arms)
- Defensive stand (dig-in, return fire)
- Combined arms coordination
- Recon infiltration (concealment)
- Meeting engagement (mutual detection)
- Urban combat (terrain effects)
- Night operations (visibility modifiers)
- River crossing (bridge requirements)
- Withdraw under pressure (morale, disengage)

See `scripts/tactical_tests/` for scenario definitions and the test framework.

## API Documentation
FastAPI auto-generates interactive docs:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Project Structure
See `AGENTS.MD` for full architecture, domain model, and implementation roadmap.

## Tech Stack
| Layer | Technology |
|---|---|
| Frontend | Leaflet 1.9, Leaflet.Editable, milsymbol.js, Vanilla JS |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, GeoAlchemy2 |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Cache/PubSub | Redis 7 |
| AI | OpenAI GPT-4.1 (order parsing, Red AI decisions, unit responses) |
| Geospatial | Shapely, pyproj, PostGIS spatial queries |
| Terrain Data | OSM Overpass API, ESA WorldCover 2021, Open-Elevation API |
