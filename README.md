# TDG — Tactical Decision Game Platform

Web-based multiplayer tactical command/staff exercise platform with AI-controlled opponent forces,
collaborative map drawing, terrain intelligence, and structured order understanding.

## Features

- **Interactive Tactical Map** — Leaflet-based map with MIL-STD-2525D military symbols (milsymbol.js), zoom-scaled markers, grid overlay with recursive snail subdivision
- **Fog of War** — Server-authoritative visibility filtering via PostGIS `ST_DWithin` + terrain-aware LOS viewshed; players only see enemy units within line-of-sight
- **LOS Viewshed** — Ray-casting based visibility polygons replace simple circles; terrain obstacles (forests, buildings) block line-of-sight; unit-type-specific eye heights
- **Collaborative Overlays** — Real-time synchronized drawing tools (arrows, polylines, rectangles, markers, ellipses, measurement) via WebSocket
- **Terrain Intelligence** — Automatic terrain classification from OSM Overpass + ESA WorldCover + Open-Elevation API; 12-type taxonomy with military modifiers; admin manual painting; SSE progress streaming
- **Rules Engine** — Deterministic tick-based simulation: movement (unit-type-specific slow/fast speeds), detection (LOS-based), combat, morale, suppression, ammo, communications
- **Tactical Map Objects** — Static battlefield objects: barbed wire, minefields, entrenchments, roadblocks, pillboxes, bridges, command posts, fuel depots, airfields (rotatable), etc. NATO-style markers with per-side discovery system
- **Chain of Command** — Hierarchical unit tree with command authority enforcement, unit assignment, drag-and-drop hierarchy editing, split/merge
- **Admin Panel** — Floating admin window with session wizard (4-step: Setup → Participants → Terrain → Done), god view, unit dashboard, scenario builder, CoC editor, terrain analysis controls, unit type editor
- **Order System** — Text order submission with AI-powered parsing (GPT-4.1, bilingual EN/RU), deterministic intent interpretation, 3-tier cost-optimized routing (keyword → nano → full LLM), unit radio responses with situational awareness
- **Game Log** — Append-only event timeline, reports panel, unified game log
- **Radio Chat** — Tactical radio channel between session commanders with recipient selection, three channel filters (All / 💬 Chat / 📡 Units), and unread indicator. Auto-generated unit radio chatter: idle reports on task completion, peer support requests under fire
- **Editable Config** — Unit type definitions and display constants stored in JSON config files (`unit_types.json`, `units_config.json`) instead of hardcoded JavaScript

## Quick Start

### 1. Prerequisites
- Python 3.12+
- Docker & Docker Compose

### 2. Start infrastructure
```bash
docker compose up -d
```
This launches PostgreSQL + PostGIS (port 5432) and Redis (port 6379).

> **Note:** If upgrading from a previous version that used `kshu` as the DB name,
> run `docker compose down -v` first to remove the old volume, then `docker compose up -d`.

### 3. Install Python dependencies
```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

### 4. Configure environment
```bash
copy .env.example .env
# Edit .env and set your OPENAI_API_KEY
```

### 5. Seed the database (creates tables + sample scenario)
```bash
python -m scripts.seed_scenario
```

### 6. Start the backend
```bash
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
10. **Advance simulation** by clicking **Execute Orders** — units move, detect, fight, and report back via radio
11. View events and reports in the **Log** tab on the sidebar; click the **📋 session name** for scenario briefing

### Admin Panel
Press the admin button (🔑) and enter the admin password to access:
- **Session** — start/pause/tick controls, session creation wizard (4 steps)
- **Monitor** — god view (see all units on both sides), unit dashboard with focus/edit/delete/split/merge
- **Builder** — interactive scenario builder with map-click unit placement, grid configuration, save/load
- **CoC** — full chain of command hierarchy editor with drag-and-drop reparenting
- **Users** — manage session participants
- **Types** — unit type editor with live SIDC preview (modify speeds, ranges, personnel)
- **Terrain** — analyze terrain (OSM + ESA + elevation), paint cells manually, clear/reload

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

## Game Rules & Simulation

### Tick-Based Simulation
The game advances in discrete ticks (default: 1 minute of game time per tick). Each tick processes:
1. **Orders** → validated orders assign tasks to units
2. **Movement** → units move toward targets at type-specific speeds modified by terrain, slope, suppression, and morale
3. **Detection** → LOS-based visibility checks between opposing units; new contacts created/updated
4. **Map Object Discovery** → units reveal hidden obstacles/structures within their LOS
5. **Stale Contacts** → old contacts decay and eventually expire
6. **Combat** → engaged units exchange fire; damage, suppression inflicted based on firepower, terrain, ammo. Artillery auto-supports attacking allies. Units under fire auto-return fire
7. **Suppression Recovery** → units not under fire gradually recover from suppression
8. **Morale** → suppression and casualties erode morale; safety and nearby friendlies restore it; units break below 15%; destroying enemies boosts nearby morale; long marches cause fatigue
9. **Communications** → heavy suppression can degrade comms; offline units continue last task but can't receive new orders
10. **Events & Reports** → notable state changes logged as events
11. **Radio Chatter** → idle units report task completion; units under fire request support from CoC siblings
12. **Broadcast** → updated state pushed to all connected clients via WebSocket

### Movement
- Each unit type has unique **slow** (tactical) and **fast** (rapid) movement speeds in m/s
- `effective_speed = base_speed × terrain_factor × slope_factor × (1 - suppression × 0.7) × morale_factor`
- **Terrain factors**: road=1.0, open=0.8, forest=0.5, urban=0.4, water=0.05, fields=0.7, marsh=0.3, etc.
- **Slope penalty**: `max(0.2, 1.0 - slope_deg/45)` — steep terrain dramatically slows movement
- Map objects affect movement: minefields damage/slow, barbed wire slows, etc.

### Detection & LOS
- **Viewshed-based**: 72-ray cast from unit position, terrain obstacles (forests, buildings) block view
- **Eye height**: unit-type-specific (observation post=8m, tanks=3m, infantry=2m default)
- **Detection probability**: `base_prob × (1 - distance/range) × posture_mod × recon_bonus × concealment`
- Deterministic hash ensures reproducibility for replay

### Combat
- `fire_effectiveness = base_firepower × strength × ammo_factor × (1 - suppression) × terrain_mod`
- Elevation advantage: +15% effectiveness when firing from higher ground
- Indirect fire units (mortars, artillery) have extended range shown as dashed circles

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
