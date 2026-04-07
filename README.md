# TDG — Tactical Decision Game Platform

Web-based multiplayer tactical command/staff exercise platform with AI-controlled opponent forces,
collaborative map drawing, terrain intelligence, and structured order understanding.

## Features

- **Interactive Tactical Map** — Leaflet-based map with MIL-STD-2525D military symbols (milsymbol.js), zoom-scaled markers, grid overlay with recursive snail subdivision
- **Fog of War** — Server-authoritative visibility filtering via PostGIS `ST_DWithin`; players only see enemy units within detection range
- **Collaborative Overlays** — Real-time synchronized drawing tools (arrows, polylines, rectangles, markers, ellipses, measurement) via WebSocket
- **Terrain Intelligence** — Automatic terrain classification from OSM Overpass + ESA WorldCover + Open-Elevation API; 12-type taxonomy with military modifiers; admin manual painting; SSE progress streaming
- **Rules Engine** — Deterministic tick-based simulation: movement (unit-type-specific slow/fast speeds), detection, combat, morale, suppression, ammo, communications
- **Chain of Command** — Hierarchical unit tree with command authority enforcement, unit assignment, split/merge
- **Admin Panel** — Floating admin window with session wizard, god view, unit dashboard, scenario builder, CoC editor, terrain analysis controls
- **Order System** — Text order submission with parsed task assignment (move with snail grid reference, attack, defend); LLM integration planned
- **Game Log** — Append-only event timeline, reports panel, unified game log

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

1. Enter a display name and click **Register / Login**
2. Click **Create Session** to create a new game session
3. Click **Start Session** to initialize units from the scenario
4. Military unit symbols appear on the map (filtered by your side's fog of war)
5. Use the **map control panel** (top-right) to toggle drawing tools, grid, units, overlays, contacts, and labels
6. **Draw overlays**: select a tool (arrow, polyline, rectangle, marker, ellipse, measure) and draw on the map; overlays sync in real-time via WebSocket
7. **Command units**: right-click for context menu (move slow 🐢/fast ⚡, formation, split, merge, rename, assign); drag-select for rubber-band mass selection
8. **Submit orders** in the **Orders** tab (text-based)
9. **Advance simulation** via admin tick control — units move, detect, and engage per Rules Engine
10. View events and reports in the **Log** tab

### Admin Panel
Press the admin button and enter the admin password to access:
- **Session** — start/pause/tick, session wizard
- **Monitor** — god view (see all units on both sides), unit dashboard with edit/delete/split/merge
- **Builder** — interactive scenario builder with map-click unit placement and grid configuration
- **CoC** — full chain of command hierarchy editor
- **Terrain** — analyze terrain (OSM + ESA + elevation), paint cells manually, clear/reload terrain data

### Terrain Intelligence
From the admin panel's terrain tab:
1. Select analysis depth (1–4, where depth 2 ≈ 111m cells)
2. Click **Analyze** — progress streams via SSE in real-time
3. Toggle terrain overlay visibility from the map control panel
4. Paint individual cells with the terrain painting tool
5. Terrain modifiers (movement, visibility, protection, attack) feed into the simulation engine

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
| AI | OpenAI GPT-4.1 (order parsing, reports — planned) |
| Geospatial | Shapely, pyproj, PostGIS spatial queries |
| Terrain Data | OSM Overpass API, ESA WorldCover 2021, Open-Elevation API |
