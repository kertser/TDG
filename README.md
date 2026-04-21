# TDG — Tactical Decision Game Platform

Web-based multiplayer tactical command/staff exercise platform with AI-controlled opponent forces,
collaborative map drawing, terrain intelligence, and structured order understanding.

## Features

- **Interactive Tactical Map** — Leaflet-based map with MIL-STD-2525D military symbols (milsymbol.js), zoom-scaled markers, grid overlay with recursive snail subdivision, height tops (▲) with elevation numbers
- **Fog of War** — Server-authoritative visibility filtering via PostGIS `ST_DWithin` + terrain-aware LOS viewshed; players only see enemy units within line-of-sight; recon/sniper units in concealment mode are nearly invisible; enemy unit type and echelon masked (only broad category shown)
- **LOS Viewshed** — Ray-casting based visibility polygons replace simple circles; terrain obstacles (forests, buildings) block line-of-sight; unit-type-specific eye heights; visibility absorption model
- **Tactical A* Pathfinding** — Terrain-aware movement trajectories over depth-1 terrain cells (~333m resolution); considers terrain cost, slope, minefields, enemy avoidance, cover preference, friendly proximity; speed-mode-aware routing (slow=concealment, fast=speed); waypoints computed immediately on order and recalculated every 5 ticks; smooth Catmull-Rom spline rendering on frontend
- **Aviation & Air-Mobility** — 3 aviation unit types (attack helicopter, transport helicopter, recon UAV) with terrain-bypass flight mechanics; aviation-specific order types (air_assault, casevac/medevac, airstrike); high eye heights (100-200m) for superior detection; bilingual EN/RU aviation keywords
- **Collaborative Overlays** — Real-time synchronized drawing tools (arrows, polylines, rectangles, markers, ellipses, measurement) via WebSocket; shared across all sides
- **Terrain Intelligence** — Automatic terrain classification from OSM Overpass + ESA WorldCover + Open-Elevation API; 12-type taxonomy with military modifiers; admin manual painting; SSE progress streaming; height tops detection
- **Rules Engine** — Deterministic tick-based simulation: movement (unit-type-specific slow/fast speeds with A* pathfinding), detection (LOS-based with recon concealment), combat (direct + area fire with finite salvos, combat role coordination), morale, suppression, ammo, communications, disengage/break contact, defensive dig-in, rest & recovery, resupply, engineer task execution, and targeted logistics support
- **Combat Role Coordination** — Units attacking the same enemy auto-coordinate: suppress (~40%, covering fire at weapon range), assault (1–2 infantry close in), flank (60° offset approach via covered terrain). Radio announces roles.
- **Artillery-Infantry Coordination** — Three-tier friendly fire prevention: proactive ceasefire request at 250m, danger-close auto-stop at 50m, area-fire friendly check. Artillery supports both attacking and defending units. Explicit fire request system. Auto-artillery-request on target acquisition.
- **Tactical Map Objects** — Static battlefield objects: barbed wire, minefields, entrenchments, roadblocks, pillboxes, bridges, command posts, fuel depots, airfields (rotatable), etc. NATO-style markers with per-side discovery system
- **Area Effects** — Transient polygon-based hazards: smoke (blocks detection), fog (reduces visibility), fire (damages units, blocks movement), chemical clouds (heavy infantry damage). All effects decay over time. Combat impact visual effects (explosions).
- **Resupply System** — Supply caches (+10% ammo/tick), logistics units (mobile +8% ammo/tick), field hospitals (+1% strength/tick). Resupply order type with auto-movement to nearest supply source.
- **Chain of Command** — Hierarchical unit tree with command authority enforcement, unit assignment, drag-and-drop hierarchy editing, split/merge, authority checks, and support for command-driven reorganization orders
- **Admin Panel** — Floating admin window with session wizard (4-step: Setup → Participants → Terrain → Done), god view, unit dashboard, scenario builder, CoC editor, terrain analysis controls, unit type editor, debug log, area effects placement
- **Order System** — Text order submission with AI-powered parsing (GPT-4.1, bilingual EN/RU), deterministic intent interpretation, 3-tier cost-optimized routing (keyword → nano → full LLM), unit radio responses with tactical assessment, smart formation suggestion, height/coordinate/snail location resolution, immediate task assignment, map-object-aware engineer/logistics handling, and doctrinal parsing of split/merge and support-unit commands
- **Order Phrasebook** — Data-driven keyword lexicon (`order_phrasebook.toml`) for bilingual command classification, order type detection, speed/formation parsing, engagement rules, location references, and 60+ regression test cases; loaded at runtime by the order parser
- **Doctrine-Aware Prompting** — Tactical doctrine is loaded from `FIELD_MANUAL.md` and injected by topic so prompts receive only relevant slices such as fires, recon, engineers, logistics, aviation, map objects, or split/merge
- **Prompt Compression & Retrieval** — 4-layer context packing for local/cloud LLM: task frame, state deltas (not full history), topic-scoped doctrine cards (BM25-like retrieval), dynamically selected few-shot exemplars. Negative context suppression omits empty sections. Deterministic continuity resolution ("same target", "the bridge"). Prompt-result cache with 5min TTL. Static system prefix for llama.cpp KV cache reuse. Typical prompt size: 1292–1648 tokens.
- **Local LLM Support** — Air-gapped deployment via llama.cpp (OpenAI-compatible API). Docker Compose profile `llm` with CPU-tuned settings: ctx=4096, reasoning off, Q4_K_M quantization, KV cache reuse. Configurable via `LOCAL_MODEL_URL` / `LOCAL_MODEL_NAME` in `.env`. Three parsing modes: `llm_first` (default), `keyword_first` (legacy), `keyword_only` (offline).
- **Radio Chat** — Tactical radio channel between session commanders with recipient selection, three channel filters (All / 💬 Chat / 📡 Units), and unread indicator. Auto-generated unit radio chatter: idle reports, peer support requests, casualty reports, artillery fire exchanges, coordinated attack planning, contact-during-advance halt/resume
- **Reports** — Five auto-generated report types: SPOTREP (enemy contacts), SHELREP (under fire), CASREP (unit destroyed), SITREP (periodic status), INTSUM (intelligence summary). Bilingual RU/EN. Unread badge on sidebar tab.
- **Session Replay** — Turn-by-turn playback with transport controls (play/pause, step forward/back, speed 0.5×–4×), timeline slider, per-tick unit position rendering with smooth animation, and LLM-generated After-Action Report (AAR)
- **Internationalization (i18n)** — Full EN/RU UI language switching via `KI18n` module; `data-i18n` HTML attributes for declarative translation; language selector in user settings; real-time re-rendering on language change
- **AI Victory Referee** — LLM-based victory evaluation every 5 ticks against scenario objectives. Game turn limit support. Auto-finish on victory or turn limit.
- **Red AI Opponents** — AI commander agents with 4 doctrine profiles (aggressive/balanced/cautious/defensive), limited knowledge (no Blue leaks), LLM decisions with rule-based fallback
- **Game Log** — Append-only event timeline, reports panel with channel filtering, app log (separated from tactical data)
- **Editable Config** — Unit type definitions and display constants stored in JSON config files (`unit_types.json`, `units_config.json`) instead of hardcoded JavaScript

## Quick Start

### Production Deployment (Recommended)

**One-command Docker deployment** — the easiest way to get started. The local LLM sidecar is included by default.

**Windows (PowerShell)**:
```powershell
# 1. Clone and configure
git clone <repository-url>
cd KShU
Copy-Item .env.example .env
# Edit .env: set OPENAI_API_KEY, SECRET_KEY, ADMIN_PASSWORD

# 2. Deploy everything (PostgreSQL + Redis + Backend + Nginx + Local LLM)
.\deploy.ps1

# 3. Access
# Frontend:  http://localhost
# API docs:  http://localhost/api/docs
# Local LLM: http://localhost:8081
```

**Linux/Unix**:
```bash
git clone <repository-url>
cd KShU
cp .env.example .env
# Edit .env: set OPENAI_API_KEY, SECRET_KEY, ADMIN_PASSWORD
chmod +x deploy.sh
./deploy.sh
```

### Deployment Script Flags

Both `deploy.ps1` (Windows) and `deploy.sh` (Linux) support identical flags:

| Flag | Description |
|---|---|
| *(none)* | Build images (using cache) and start the full stack |
| `--rebuild` | Tear down, rebuild images with `--no-cache`, restart. **Keeps database.** |
| `--clean` | **Full wipe**: remove containers, **volumes (data erased)**, images, build cache — then rebuild fresh |
| `--down` | Stop and remove all containers. **Database volume preserved** — data survives. |
| `--logs` | Follow container logs after start (`Ctrl+C` to exit) |

```powershell
# Windows examples
.\deploy.ps1 --rebuild        # force full rebuild
.\deploy.ps1 --rebuild --logs # rebuild then tail logs
.\deploy.ps1 --clean          # nuclear option
.\deploy.ps1 --down           # stop everything
```

> **To disable the local LLM**: comment out `COMPOSE_PROFILES=llm` in your `.env`.

---

## Deployment Architecture

```
Client Browser (http://localhost)
        ↓
    Nginx (port 80)
    ├─→ Frontend static files
    ├─→ /api/*  → Backend:8000
    └─→ /ws/*   → Backend:8000 (WebSocket)
        ↓
    FastAPI Backend (port 8000)
    ├─→ PostgreSQL:5432 (+ PostGIS)
    ├─→ Redis:6379 (pub/sub + cache)
    └─→ LLM:8081 (local llama.cpp)
```

| Service | Port | Description |
|---|---|---|
| `nginx` | 80 | Reverse proxy + frontend static files |
| `backend` | 8000 | FastAPI app + auto migrations (internal) |
| `postgres` | 5432 | PostgreSQL 16 + PostGIS 3.4 |
| `redis` | 6379 | Redis (pub/sub, session cache) |
| `llm` | 8081 | llama.cpp OpenAI-compatible API |

All services have health checks, restart policies, and proper dependency chains.

---

## Environment Configuration

Copy `.env.example` to `.env`. Required settings:

```env
OPENAI_API_KEY=sk-...         # for LLM order parsing and Red AI
SECRET_KEY=<random-64-chars>  # for JWT tokens
ADMIN_PASSWORD=<strong-pass>  # for admin panel
```

Optional settings:
```env
LOCAL_MODEL_URL=http://localhost:8081/v1
LOCAL_MODEL_NAME=local
LOCAL_TRIAGE_ENABLED=true
LLM_PARSING_MODE=llm_first    # llm_first | keyword_first | keyword_only

OPENAI_MODEL=gpt-4.1
OPENAI_MODEL_MINI=gpt-4.1-mini
OPENAI_MODEL_NANO=gpt-4o-mini
```

Generate a `SECRET_KEY`:
```powershell
# Windows PowerShell
[Convert]::ToBase64String((1..48 | ForEach-Object { [byte](Get-Random -Max 256) }))
```

---

## Logs, Health & Troubleshooting

```powershell
# All service logs
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f llm

# Service health
docker compose ps
curl http://localhost/health
curl http://localhost:8081/health
```

**Backend won't start** — check `docker compose logs backend`. Common causes: missing `OPENAI_API_KEY`, database not ready (wait 30s, backend retries automatically), port 8000 already in use.

**Database migrations fail**:
```powershell
docker compose exec backend alembic upgrade head
# or full reset (destroys data):
.\deploy.ps1 --down
docker volume rm tdg_pgdata
.\deploy.ps1
```

**Nginx 502 Bad Gateway** — backend not healthy yet: `docker compose restart backend`

**Local LLM not responding**:
```powershell
docker compose logs llm
# Warm the model after first start (slow cold load):
python scripts\warm_local_llm.py
```

---

## Data Persistence & Reset

Game data lives in Docker volume `pgdata`. It **survives** `--down` and is reattached on next start.

To fully reset (wipe the database):

```powershell
# Windows — wipes everything including data
.\deploy.ps1 --clean

# Linux/Unix
./deploy.sh --clean
```

Backup the database before wiping:
```powershell
docker compose exec postgres pg_dump -U tdg tdg > backup.sql
```

---

## Updates

```powershell
# Windows
git pull
.\deploy.ps1 --rebuild

# Linux/Unix
git pull
./deploy.sh --rebuild
```

---

## Production Hardening

1. **Change all default passwords** in `.env` — `SECRET_KEY`, `ADMIN_PASSWORD`, and the database passwords in `docker-compose.yml`
2. **Enable HTTPS** — use nginx SSL config or a reverse proxy (Traefik, Caddy)
3. **Resource limits** — add to `docker-compose.yml` backend service:
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '2'
         memory: 4G
   ```
4. **Scheduled backups** of the `pgdata` volume
5. **Monitoring** — Prometheus + Grafana on the `/health` endpoints

---

### Development Setup (Alternative)

For active development with hot-reload:


#### 1. Prerequisites
- Python 3.12+
- Docker & Docker Compose

##### 2. Start infrastructure
```powershell
docker compose up -d
```
This launches PostgreSQL + PostGIS (port 5432) and Redis (port 6379).

> **Note:** If upgrading from a previous version that used `kshu` as the DB name,
> run `docker compose down -v` first to remove the old volume, then `docker compose up -d`.

#### 3. Install Python dependencies
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

#### 4. Configure environment
```powershell
Copy-Item .env.example .env
# Edit .env and set your OPENAI_API_KEY
```

#### 5. Seed the database (creates tables + sample scenario)
```powershell
python -m scripts.seed_scenario
```

#### 6. Start the backend
```powershell
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

#### 7. Open the frontend
Navigate to `http://localhost:8000` in your browser.

### Local LLM (Optional — Air-Gapped Deployment)
If no `OPENAI_API_KEY` is set, the parser falls back to a local model via llama.cpp:

```powershell
# Download model (default: Gemma 3 1B Instruct Q4_K_M, ~800MB)
.\scripts\download_model.ps1

# The LLM container starts automatically with the stack (COMPOSE_PROFILES=llm in .env)
# Or run native (faster on Windows):
.\tools\llama-cpp\llama-server.exe --model models\model.gguf --alias local --host 127.0.0.1 --port 8081 --ctx-size 4096 --threads 8 --reasoning off --no-webui --mlock
```

Configure in `.env`:
```ini
LOCAL_MODEL_URL=http://localhost:8081/v1
LOCAL_MODEL_NAME=local
LLM_PARSING_MODE=llm_first   # or keyword_first, keyword_only
```

## Order Parsing Pipeline

```
Player types radio message
         │
         ▼
┌─────────────────────────────────┐
│  1. KEYWORD PARSER  (~0 ms)     │  Deterministic regex/keyword matching via
│     order_phrasebook.toml       │  order_phrasebook.toml lexicon.
│                                 │  Extracts: classification, order_type,
│     → ParsedOrderData           │  locations, units, speed, formation,
│     + confidence 0.15–0.90      │  engagement rules.
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  2. LOCAL TRIAGE  (optional)    │  If LOCAL_TRIAGE_ENABLED and local LLM
│     ~200-token prompt, 2 s      │  is available. Asks only:
│     timeout, 30 s backoff       │  "command / ack / report / request / unclear?"
│                                 │  + language detection (en/ru).
│  Agrees with keyword?           │
│   → boost confidence +0.10      │  No doctrine, no context, no few-shot.
│  Disagrees?                     │
│   → reduce confidence           │
│   → force full cloud model      │
│  Unavailable?                   │
│   → skip, use keyword conf      │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  3. ROUTING DECISION            │
│                                 │
│  Non-command + conf ≥ 0.95 ───► SKIP LLM, return keyword result
│                                 │
│  Command + conf ≥ 0.70 ───────► Cloud NANO  (gpt-5-nano)
│                                 │
│  Command + conf < 0.70 ───────► Cloud FULL  (gpt-5.4-mini)
│  or unclear                     │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. CONTEXT BUILDING  (retrieval_context.py, profile="cloud")   │
│                                                                 │
│  ┌─ Doctrine (RAG-like) ─────────────────────────────────────┐  │
│  │  Infer topics from order text → extract matching sections │  │
│  │  from FIELD_MANUAL.md → BM25-like scoring → top 6, ≤2 KB  │  │
│  └───────────────────────────────────────────────────────────┘  │
│  ┌─ Unit Roster ─────────────────────────────────────────────┐  │
│  │  Rank units by relevance (target refs, type match) → 18   │  │
│  └───────────────────────────────────────────────────────────┘  │
│  ┌─ 9 Contextual Sections ───────────────────────────────────┐  │
│  │  terrain · contacts · friendly_status · objectives        │  │
│  │  environment · orders · radio · reports · map_objects     │  │
│  │  Each section: score by query-token overlap + recency,    │  │
│  │  pick top N lines. Empty sections suppressed entirely.    │  │
│  └───────────────────────────────────────────────────────────┘  │
│  ┌─ State Packet (≤ 2400 chars) ─────────────────────────────┐  │
│  │  task frame · compact unit atoms · section summaries      │  │
│  │  history digest · continuity hints · height tops          │  │
│  └───────────────────────────────────────────────────────────┘  │
└──────────┬──────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. CLOUD LLM CALL                                              │
│                                                                 │
│  SYSTEM (~3–5 K tokens): parsing instructions, doctrine         │
│    excerpt, grid format, unit roster, 9 context sections,       │
│    Russian radio conventions                                    │
│                                                                 │
│  USER (~0.5–1.5 K tokens): radio message text, state packet,    │
│    continuity hints, 1–4 few-shot examples (by order type+lang) │
│                                                                 │
│  Response → JSON → Pydantic validation → ParsedOrderData        │
└──────────┬──────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  6. RECONCILIATION              │  Merge LLM result with keyword hints:
│     _reconcile_llm_result()     │  • Strong command frame → prevent downgrade
│                                 │  • Fire-request signals → preserve order_type
│                                 │  • Nano returns "unclear" → escalate to full
└──────────┬──────────────────────┘
           │
           ▼
   ParsedOrderData
           │
     ┌─────┴──────┬────────────────┐
     ▼            ▼                ▼
IntentInterp. LocationResolver  ResponseGen.
(deterministic) (grid/coord/    (template-based
 25+ rules)      snail/height)   unit radio ack)
     │            │                │
     └─────┬──────┘                │
           ▼                       ▼
      OrderService ──────► WebSocket broadcast
    (persist + task assign)  (order status + unit response)
```

## Doctrine Loading

- `FIELD_MANUAL.md` is the authoritative tactical source.
- `backend/prompts/tactical_doctrine.py` loads:
  - full doctrine markers for deep AI reasoning
  - brief doctrine markers for compact parsing context
  - topic-scoped snippets (`DOCTRINE:TOPIC:*`) so prompts receive only relevant tactical context
- Tactical regression should be expanded as scenario packs, not only isolated parser tests. Keep bilingual RU/EN cases for maneuver, fires, engineers, logistics, aviation, split/merge, and map-object interaction.
- The order parser now selects doctrine by command family, for example:
  - `fires`
  - `recon`
  - `engineers`
  - `logistics`
  - `aviation`
  - `map_objects`
  - `split_merge`

## Order Phrasebook

The keyword parser is driven by `backend/data/order_phrasebook.toml` — a structured TOML file that contains:

- **Classification lexicon** — bilingual command/ack/report/status-request keywords
- **Order detection patterns** — standby, coordination, fire requests, breach, mining, bridge deployment, construction, smoke, split/merge, air mobility, screening, withdrawal, disengage, resupply, and more
- **Speed keywords** — slow/fast movement qualifiers in EN and RU (30+ keywords each)
- **Formation patterns** — column, line, wedge, vee, echelon, diamond, box, staggered, herringbone with explicit prefix patterns
- **Engagement rules** — hold fire, fire at will, return fire only
- **Location object patterns** — minefields, barbed wire, bridges, pillboxes, command posts, supply caches, etc.
- **60+ regression test cases** — `[[case]]` entries with expected classification, order type, location refs, speed, and map object type; validated by the test suite

The phrasebook is loaded at startup by `backend/services/order_phrasebook.py` and consumed by the order parser for deterministic keyword matching before any LLM call.

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
13. **Switch language**: open user settings and select English or Russian — the entire UI updates in real-time
14. **Replay a session**: hover the game clock (bottom-right) and click **Replay** to load turn-by-turn playback; use transport controls to step through ticks or auto-play; click **📊 AAR** to generate an AI-written After-Action Report

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
- **Aviation units bypass all terrain** — helicopters and UAVs fly over water, minefields, obstacles, and steep terrain without penalty
- **Tactical A* pathfinding**: ground units navigate terrain-aware paths that avoid minefields, enemy observation, and impassable terrain
- Speed mode affects routing: **slow** prefers concealed routes (forest, urban), **fast** prefers roads and open terrain
- `effective_speed = base_speed × terrain_factor × slope_factor × (1 - suppression × 0.7) × morale_factor × weather_mod`
- **Terrain factors**: road=1.0, open=0.8, forest=0.5, urban=0.4, water=0.05, fields=0.7, marsh=0.3, etc.
- **Slope penalty**: `max(0.2, 1.0 - slope_deg/45)` — steep terrain dramatically slows movement
- Map objects affect movement: minefields damage/slow, barbed wire slows, etc.
- Ground units halt before discovered minefields (request engineers) and at water without bridges
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
39 unit types defined in `frontend/config/unit_types.json`, each with:
- MIL-STD-2525D SIDC codes (Blue + Red variants)
- Slow/fast movement speeds (m/s)
- Detection range, fire range, personnel count
- Eye height for LOS calculations
- Indirect fire flag (mortars, artillery)
- Special capabilities (aviation terrain bypass, cargo capacity, etc.)

**Aviation units** (3 types):
- **Attack Helicopter** — 70 m/s fast, 5km detection, 4km fire range, 100m eye height
- **Transport Helicopter** — 60 m/s fast, 3km detection, 12-person cargo, 150m eye height
- **Recon UAV** — 35 m/s fast, 8km detection, unarmed, 200m eye height

Aviation units bypass all terrain restrictions (water, minefields, obstacles, slope) and are handled by special order types: `air_assault` (helicopter insertion), `casevac`/`medevac` (casualty evacuation), `airstrike` (attack run).

## Configuration Files

| File | Purpose |
|---|---|
| `frontend/config/unit_types.json` | Unit type registry: SIDC codes, speeds, ranges, personnel, eye heights, aviation flags |
| `frontend/config/units_config.json` | Display/behavior constants: status icons, formations, movement arrows, selection params |
| `backend/data/order_phrasebook.toml` | Bilingual keyword lexicon + regression test cases for order parsing |
| `FIELD_MANUAL.md` | Tactical doctrine source (loaded by `backend/prompts/tactical_doctrine.py`) |
| `backend/config.py` | Server configuration: DB URL, Redis, API keys, LLM settings |
| `.env` | Environment variables (secrets, overrides) |
| `docker-compose.yml` | Multi-container orchestration (postgres, redis, backend, nginx, llm) |
| `nginx.conf` | Reverse proxy configuration for production deployment |

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

### Order Phrasebook Regression
The `order_phrasebook.toml` file contains 60+ `[[case]]` entries that serve as regression tests for the keyword parser. Each case specifies input text and expected outputs (classification, order type, locations, speed, map object type). These are validated by the test suite to prevent parser regressions.

See `scripts/tactical_tests/` for scenario definitions and the test framework.

## API Documentation
FastAPI auto-generates interactive docs:
- **Production**: `http://localhost/api/docs` (Swagger UI), `http://localhost/api/redoc` (ReDoc)
- **Development**: `http://localhost:8000/docs` (Swagger UI), `http://localhost:8000/redoc` (ReDoc)

## Project Structure
See `AGENTS.MD` for full architecture, domain model, and implementation roadmap.

```
KShU/
├── AGENTS.MD                       # Architecture & implementation guide
├── FIELD_MANUAL.md                 # Tactical doctrine source
├── README.md
├── Task.MD                         # Original project requirements
├── requirements.txt
├── docker-compose.yml              # PostgreSQL+PostGIS, Redis, backend, nginx, llama.cpp
├── Dockerfile                      # Backend multi-stage build
├── docker-entrypoint.sh            # Runs migrations on container start
├── nginx.conf                      # Reverse proxy + static serving config
├── deploy.ps1                      # One-command deployment script (Windows PowerShell)
├── deploy.sh                       # One-command deployment script (Linux/Unix Bash)
├── alembic.ini
├── .env
├── backend/
│   ├── main.py                     # FastAPI app factory
│   ├── config.py                   # Pydantic settings
│   ├── database.py                 # Async SQLAlchemy engine
│   ├── models/                     # SQLAlchemy models (15 tables)
│   ├── api/                        # REST + WebSocket endpoints
│   ├── engine/                     # Deterministic rules engine (tick processing)
│   ├── services/                   # Business logic (grid, orders, visibility, pathfinding, etc.)
│   │   ├── order_parser.py         # 3-tier LLM routing (keyword→nano→full)
│   │   ├── order_phrasebook.py     # TOML phrasebook loader
│   │   ├── pathfinding_service.py  # Tactical A* over terrain cells
│   │   ├── retrieval_context.py    # Prompt compression & doctrine retrieval
│   │   ├── local_triage.py         # Local LLM triage classifier
│   │   ├── los_service.py          # LOS viewshed ray casting
│   │   └── terrain_analysis/       # OSM + ESA + elevation analyzers
│   ├── data/
│   │   └── order_phrasebook.toml   # Bilingual keyword lexicon + regression cases
│   ├── prompts/                    # LLM prompt templates
│   ├── schemas/                    # Pydantic v2 schemas
│   └── tests/                      # Unit & integration tests
├── frontend/
│   ├── index.html
│   ├── config/                     # unit_types.json, units_config.json
│   ├── css/style.css
│   └── js/
│       ├── app.js                  # Main entry, WS handlers
│       ├── map.js                  # Leaflet map, game clock
│       ├── units.js                # Unit rendering, selection, movement
│       ├── orders.js               # Command panel + radio chat
│       ├── admin.js                # Admin panel (~4300 lines)
│       ├── i18n.js                 # EN/RU internationalization
│       ├── replay.js               # Session replay with AAR
│       ├── terrain.js              # Terrain overlay + elevation
│       ├── map_objects.js          # Tactical objects (mines, wire, bridges, etc.)
│       ├── overlays.js             # Drawing tools
│       ├── dialogs.js              # Themed confirm/alert/prompt modals
│       └── ...                     # contacts, events, reports, grid, symbols, etc.
├── scripts/
│   ├── seed_scenario.py            # DB seed script
│   ├── download_model.ps1          # Download local LLM model
│   └── tactical_tests/             # Automated tactical scenario framework
└── models/                         # Local LLM model files (GGUF)
```

## Tech Stack
| Layer | Technology |
|---|---|
| Frontend | Leaflet 1.9, Leaflet.Editable, milsymbol.js, Vanilla JS |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, GeoAlchemy2 |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Cache/PubSub | Redis 7 |
| Deployment | Docker, Docker Compose, Nginx (reverse proxy) |
| AI | OpenAI GPT-4.1 / GPT-5 (order parsing, Red AI decisions, unit responses, AAR); local llama.cpp fallback (Gemma/Qwen GGUF) |
| Geospatial | Shapely, pyproj, PostGIS spatial queries |
| Terrain Data | OSM Overpass API, ESA WorldCover 2021, Open-Elevation API |
| i18n | Custom `KI18n` module with EN/RU dictionaries |
