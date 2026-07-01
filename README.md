![TDG](artwork/image1.png)

# TDG — Tactical Decision Game Platform

> **English** | [Русский](README.ru.md)

Web-based multiplayer tactical command/staff exercise platform with AI-controlled opponent forces,
collaborative map drawing, terrain intelligence, and structured order understanding.

---

## Table of Contents

1. [Features](#features)
2. [Quick Start](#quick-start)
3. [Deployment Architecture](#deployment-architecture)
4. [Environment Configuration](#environment-configuration)
5. [Development Setup](#development-setup)
6. [Order Parsing Pipeline](#order-parsing-pipeline)
7. [Game Rules & Simulation](#game-rules--simulation)
8. [Unit Types](#unit-types)
9. [Configuration Files](#configuration-files)
10. [Testing](#testing)
11. [API Documentation](#api-documentation)
12. [Project Structure](#project-structure)
13. [Tech Stack](#tech-stack)

---

## Features

### Map & Visualization

- **Interactive Tactical Map** — Leaflet-based map with MIL-STD-2525D military symbols (milsymbol.js), zoom-scaled markers, grid overlay with recursive snail subdivision, height tops (▲) with elevation numbers
- **Fog of War** — Server-authoritative visibility filtering via PostGIS `ST_DWithin` + terrain-aware LOS viewshed; enemy unit type and echelon masked (only broad category shown); recon/sniper units in concealment mode are nearly invisible
- **LOS Viewshed** — 72-ray cast from unit position; terrain obstacles (forests, buildings) block line-of-sight; unit-type-specific eye heights; visibility absorption model
- **Collaborative Overlays** — Real-time synchronized drawing tools (arrows, polylines, rectangles, markers, ellipses, measurement) via WebSocket; shared across all sides
- **Session Replay** — Turn-by-turn playback with transport controls (play/pause, step ±1 tick, speed 0.5×–4×), timeline slider, smooth unit animation, and LLM-generated After-Action Report (AAR)
- **Internationalization (i18n)** — Full EN/RU UI language switching via `KI18n` module; `data-i18n` HTML attributes; language selector in user settings; real-time re-rendering on language change

### Terrain & Objects

- **Terrain Intelligence** — Automatic terrain classification from OSM Overpass + ESA WorldCover + Open-Elevation API; 12-type taxonomy with military modifiers; admin manual painting; SSE progress streaming; height tops detection
- **Tactical A\* Pathfinding** — Terrain-aware movement trajectories over depth-1 cells (~333m resolution); considers terrain cost, slope, minefields, enemy avoidance, cover preference, friendly proximity; speed-mode-aware routing (slow = concealment, fast = speed); Catmull-Rom spline rendering on frontend
- **Tactical Map Objects** — Barbed wire, minefields, entrenchments, roadblocks, pillboxes, bridges, command posts, fuel depots, airfields (rotatable), etc. NATO-style markers with per-side discovery system
- **Area Effects** — Transient polygon-based hazards: smoke (blocks detection), fog (reduces visibility), fire (damages units, blocks movement), chemical clouds (heavy infantry damage). All effects decay over time. Combat impact visual effects (explosions).

### Combat Simulation

- **Rules Engine** — Deterministic tick-based simulation: movement (unit-type-specific slow/fast speeds with A* pathfinding), detection (LOS-based with recon concealment), combat (direct + area fire, finite salvos, combat role coordination, **defensive posture advantage**), morale, suppression (posture-aware recovery), ammo, communications, disengage/break contact, defensive dig-in, rest & recovery, resupply, engineer task execution
- **Defensive Posture Advantage** — Defenders maintain a 1.77:1 exchange ratio at equal forces. Attackers suffer −35% fire effectiveness; defenders gain +15%. Suppression recovery is twice as fast for stationary defenders. Flanking degrades defender protection by 25%. A breakthrough window opens when suppression exceeds 60% — rewarding fire-and-movement doctrine.
- **Combat Role Coordination** — Units attacking the same enemy auto-coordinate: suppress (~40%, covering fire at weapon range), assault (1–2 infantry close in), flank (60° offset via covered terrain). Radio announces roles.
- **Artillery-Infantry Coordination** — Three-tier friendly fire prevention: proactive ceasefire request at 250m, danger-close auto-stop at 50m, area-fire friendly check. Artillery supports both attacking and defending units. Explicit fire request system. Auto-artillery-request on target acquisition.
- **Aviation & Air-Mobility** — 3 aviation unit types (attack helicopter, transport helicopter, recon UAV) with terrain-bypass flight mechanics; aviation-specific order types (air_assault, casevac/medevac, airstrike); high eye heights (100–200m) for superior detection

### Command & Control

- **Chain of Command** — Hierarchical unit tree with command authority enforcement, unit assignment, drag-and-drop hierarchy editing, split/merge, and support for command-driven reorganization orders
- **Resupply System** — Supply caches (+10% ammo/tick), logistics units (mobile +8% ammo/tick), field hospitals (+1% strength/tick). Resupply order type with auto-movement to nearest supply source.
- **Radio Chat** — Tactical radio channel with recipient selection, three channel filters (All / 💬 Chat / 📡 Units), unread indicator. Auto-generated unit radio chatter: idle reports, peer support requests, casualty reports, artillery fire exchanges, contact-during-advance halt/resume
- **Reports** — Five auto-generated types: SPOTREP (enemy contacts), SHELREP (under fire), CASREP (unit destroyed), SITREP (periodic status), INTSUM (intelligence summary). Bilingual RU/EN. Unread badge on sidebar tab.
- **Objective Control & Victory** — Deterministic territorial check each tick: `objective_captured`/`objective_contested` events, annihilation detection, `objectives_to_win` threshold — all without LLM. LLM-based narrative referee runs every 5 ticks against custom scenario objectives. Game turn limit and auto-finish on victory.

### Order Parsing & AI

- **Order System** — Text order submission with AI-powered parsing (GPT-4.1, bilingual EN/RU), deterministic intent interpretation, 3-tier cost-optimized routing (keyword → nano → full LLM), unit radio responses with tactical assessment, smart formation suggestion, height/coordinate/snail location resolution, immediate task assignment
- **Order Phrasebook** — Data-driven keyword lexicon (`order_phrasebook.toml`): bilingual command/ack/report keywords, order-type detection patterns, speed/formation parsing, engagement rules, location object patterns, 60+ regression test cases; loaded at runtime
- **Doctrine-Aware Prompting** — Tactical doctrine loaded from `FIELD_MANUAL.md` and injected by topic (fires, recon, engineers, logistics, aviation, map_objects, split_merge), so each prompt receives only the relevant slice
- **Prompt Compression & Retrieval** — 4-layer context packing: task frame, state deltas, topic-scoped doctrine cards (BM25-like retrieval), dynamically selected few-shot exemplars. Negative context suppression omits empty sections. Deterministic continuity resolution. Prompt-result cache with 5-min TTL. Typical size: 1 292–1 648 tokens.
- **Local LLM Support** — Air-gapped deployment via llama.cpp (OpenAI-compatible API). Docker Compose profile `llm` with CPU-tuned settings: ctx=4096, Q4_K_M quantization, KV-cache reuse. Three parsing modes: `llm_first` (default), `keyword_first`, `keyword_only`.
- **Red AI Opponents** — AI commander agents with 4 doctrine profiles (aggressive/balanced/cautious/defensive), limited knowledge (no Blue leaks), LLM decisions with rule-based fallback

### Trainer Tools

- **Admin Panel** — Floating window with session wizard (4-step: Setup → Participants → Terrain → Done), god view, unit dashboard, scenario builder, CoC editor, terrain analysis controls, unit type editor, debug log, tactical objects & area effects placement (Objects tab), Red AI agents (Red AI tab)
- **Trainer Friction Injection** — Inject mid-exercise degradation onto any unit: `breakdown`, `comms_failure`, `position_error`, `ammo_shortage`, `fuel_depletion`, `commander_casualty` — each with duration (ticks) and magnitude
- **Adaptive Phrasebook Learning** — Statistical mining of phrasebook proposals from real session data (in Monitor tab). Cross-session clustering (≥5 sessions, ≥3 users), optional LLM quality judge, human review workflow (approve/reject/apply). Approved proposals written to `order_phrasebook.toml` and hot-reloaded without restart.

### Interface

- **Interactive Tutorial** — Spotlight-based onboarding auto-shows on first login (`KTutorial`). Step-by-step guided tour with DOM element highlighting. Completion persisted server-side; reopenable from settings.
- **Game Log** — Append-only event timeline, reports panel with channel filtering, app log (separated from tactical data)
- **Editable Config** — Unit type definitions and display constants in JSON files (`unit_types.json`, `units_config.json`) instead of hardcoded JavaScript

---

## Quick Start

### Production Deployment (Recommended)

**One-command Docker deployment** — the easiest way to get started. The local LLM sidecar is included by default.

```powershell
# 1. Clone and configure
git clone <repository-url>
Set-Location KShU
Copy-Item .env.example .env
# Edit .env: set OPENAI_API_KEY, SECRET_KEY, ADMIN_PASSWORD

# 2. Deploy everything (PostgreSQL + Redis + Backend + Nginx + Local LLM)
.\deploy.ps1

# 3. Access
# Frontend:  http://localhost
# API docs:  http://localhost/api/docs
# Local LLM: http://localhost:8081
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
.\deploy.ps1 --rebuild        # force full rebuild
.\deploy.ps1 --rebuild --logs # rebuild then tail logs
.\deploy.ps1 --clean          # nuclear option
.\deploy.ps1 --down           # stop everything
```

> **To disable the local LLM**: comment out `COMPOSE_PROFILES=llm` in your `.env`.

### Data Persistence & Reset

Game data lives in Docker volume `pgdata`. It **survives** `--down` and is reattached on next start.

```powershell
# Backup before wiping
docker compose exec postgres pg_dump -U tdg tdg > backup.sql

# Full wipe (destroys data)
.\deploy.ps1 --clean
```

### Updates

```powershell
git pull
.\deploy.ps1 --rebuild
```

### Custom Domain Deployment (`tdg.alpha-numerical.com`)

TDG can be served at a custom domain with automatic TLS via a shared Caddy reverse proxy
(the same Caddy instance used by SmartVoter on the same server).
The production overlay `docker-compose.prod.yml` removes the host port bindings from nginx
and attaches it to an external Docker network `web` so Caddy can reach it.

See **[docs/DEPLOY_DOMAIN.md](docs/DEPLOY_DOMAIN.md)** for the full step-by-step guide
(DNS setup, shared network creation, Caddyfile update, deployment, verification, and rollback).

### Production Hardening

1. **Change all default passwords** in `.env` — `SECRET_KEY`, `ADMIN_PASSWORD`, and database passwords in `docker-compose.yml`
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

Generate a `SECRET_KEY` (PowerShell):
```powershell
[Convert]::ToBase64String((1..48 | ForEach-Object { [byte](Get-Random -Max 256) }))
```

### Logs & Troubleshooting

```powershell
# All service logs
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f llm

# Service health
docker compose ps
Invoke-WebRequest http://localhost/health
Invoke-WebRequest http://localhost:8081/health
```

| Problem | Solution |
|---|---|
| Backend won't start | Check `OPENAI_API_KEY`; wait 30 s — backend retries DB connection automatically |
| Database migrations fail | `docker compose exec backend alembic upgrade head` |
| Nginx 502 Bad Gateway | Backend not healthy yet: `docker compose restart backend` |
| Local LLM not responding | `docker compose logs llm`, then `python scripts\warm_local_llm.py` |

---

## Development Setup

For active development with hot-reload:

```powershell
# 1. Start infrastructure (PostgreSQL + Redis)
docker compose up -d

# 2. Create virtualenv and install dependencies
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Configure environment
Copy-Item .env.example .env
# Edit .env and set OPENAI_API_KEY

# 4. Seed the database (creates tables + sample scenario)
python -m scripts.seed_scenario

# 5. Start the backend
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 6. Open http://localhost:8000
```

> **Note:** If upgrading from a previous version that used `kshu` as the DB name,
> run `docker compose down -v` first to remove the old volume, then `docker compose up -d`.

### Local LLM (Optional — Air-Gapped Deployment)

If no `OPENAI_API_KEY` is set, the parser falls back to a local model via llama.cpp:

```powershell
# Download model (default: Gemma 3 1B Instruct Q4_K_M, ~800 MB)
.\scripts\download_model.ps1

# Run natively (faster on Windows):
.\tools\llama-cpp\llama-server.exe `
    --model models\model.gguf `
    --alias local `
    --host 127.0.0.1 `
    --port 8081 `
    --ctx-size 4096 `
    --threads 8 `
    --reasoning off `
    --no-webui `
    --mlock
```

Configure in `.env`:
```env
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

- `FIELD_MANUAL.md` is the single authoritative tactical source.
- `backend/prompts/tactical_doctrine.py` loads: full doctrine markers for deep AI reasoning, brief doctrine markers for compact parsing context, and topic-scoped snippets (`DOCTRINE:TOPIC:*`) so prompts receive only relevant tactical context.
- Doctrine is selected by command family: `fires` · `recon` · `engineers` · `logistics` · `aviation` · `map_objects` · `split_merge`
- Tactical regression tests should be expanded as scenario packs (bilingual RU/EN cases for maneuver, fires, engineers, logistics, aviation, split/merge, and map-object interaction), not only isolated parser tests.

## Order Phrasebook

The keyword parser is driven by `backend/data/order_phrasebook.toml` — a structured TOML file containing:

- **Classification lexicon** — bilingual command/ack/report/status-request keywords
- **Order detection patterns** — standby, coordination, fire requests, breach, mining, bridge deployment, construction, smoke, split/merge, air mobility, screening, withdrawal, disengage, resupply, and more
- **Speed keywords** — slow/fast movement qualifiers in EN and RU (30+ keywords each)
- **Formation patterns** — column, line, wedge, vee, echelon, diamond, box, staggered, herringbone with explicit prefix patterns
- **Engagement rules** — hold fire, fire at will, return fire only
- **Location object patterns** — minefields, barbed wire, bridges, pillboxes, command posts, supply caches, etc.
- **60+ regression test cases** — `[[case]]` entries with expected classification, order type, location refs, speed, and map object type; validated by the test suite


## Usage

1. Enter a callsign and password, then click **Register** (first time) or **Login**
2. On first login an **interactive tutorial** walks you through the interface step by step; dismiss at any time or replay it from user settings
3. Click a session from the list to join (sessions are created by the admin)
4. Click **Start Session** to initialize units from the scenario
5. Use the **map control panel** (top-right) to toggle drawing tools, grid, units, overlays, contacts, labels, and terrain
6. **Draw overlays**: select a tool (arrow, polyline, rectangle, marker, ellipse, measure) and draw on the map; overlays sync in real-time via WebSocket
7. **Command units**: left-click to select, shift+click for multi-select, left-drag for rubber-band mass selection, alt+click to cycle stacked units; right-click for context menu (move slow 🐢/fast ⚡, formation, split, merge, rename, assign)
8. **Submit orders** in the **📡 Orders** tab of the bottom command panel (select units first, or click **👥 All**)
9. **Radio chat** in the **📻 Radio** tab — send tactical messages to specific commanders or broadcast to all; filter by channel (All / 💬 Chat / 📡 Units)
10. **Advance simulation** by clicking **Execute Orders** — units move along A*-optimized paths, detect enemies, fight with coordinated roles, and report back via radio
11. View events and reports in the sidebar tabs (**Events**, **Reports** with unread badge); click the **📋 session name** for scenario briefing
12. Reference **height tops** in orders: *"Move toward height 170"*
13. **Switch language**: open user settings and select English or Russian — the entire UI updates in real-time
14. **Replay a session**: hover the game clock (bottom-right) and click **Replay** to load turn-by-turn playback; use transport controls to step through ticks or auto-play; click **📊 AAR** to generate an AI-written After-Action Report

### Admin Panel

Press the admin button (🔑) and enter the admin password to access:

| Tab | Capabilities |
|---|---|
| **Session** | Start/pause/tick controls, session creation wizard (4 steps), delete all units, reset session |
| **Monitor** | God view (see all units on both sides), unit dashboard with focus/edit/delete/split/merge, debug log toggle (detailed tick-by-tick engine data), **trainer friction injection**, 🎓 **Learning Analysis** (mine phrasebook proposals, review & apply) |
| **Builder** | Interactive scenario builder with map-click unit placement, grid configuration, save/load, save session → scenario |
| **CoC** | Full chain of command hierarchy editor with drag-and-drop reparenting, bulk assign/unassign |
| **Users** | Manage session participants |
| **Types** | Unit type editor with live SIDC preview (modify speeds, ranges, personnel, eye heights) |
| **Terrain** | Analyze terrain (OSM + ESA + elevation), paint cells manually, clear/reload |
| **Objects** | Place tactical obstacles (barbed wire, minefields, entrenchments, etc.), structures (bridges, CPs, depots, airfields), and place area effects (smoke, fog, fire, chemical clouds) |
| **Red AI** | Create/edit Red AI commander agents (doctrine profiles, mission intent, controlled units), force-decide |

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

---

## Game Rules & Simulation

### Tick-Based Simulation

By default, 1 minute of game time per tick. Processing sequence:

| # | Phase | Description |
|---|---|---|
| 1 | **Red AI** | AI agents make decisions for Red-controlled units |
| 2 | **Orders** | Validated orders assign tasks; immediate task assignment on confirmation |
| 3 | **Pathfinding** | A* waypoints computed for all moving units (terrain-aware, enemy-avoiding) |
| 4 | **Movement** | Units follow A* waypoints at type-specific speeds; halt at discovered minefields and rivers without bridges; moving units halt on enemy detection and request orders |
| 5 | **Detection** | LOS-based visibility checks; new contacts created/updated; recon concealment applied |
| 6 | **Map Object Discovery** | Units reveal hidden obstacles/structures within their LOS |
| 7 | **Stale Contacts** | Old contacts decay and eventually expire |
| 8 | **Artillery Support** | Idle artillery auto-assigned to support attacking/defending units in CoC; explicit fire requests processed first; ceasefire coordination with advancing infantry |
| 9 | **Defense** | Dig-in progression for defending units |
| 10 | **Return Fire** | Units under attack auto-engage nearest attacker (except disengaging units) |
| 11 | **Combat** | Coordinated roles (suppress/assault/flank); area fire 150m blast radius; finite salvos (default 3); danger close at 50m |
| 12 | **Suppression Recovery** | Defenders/stationary: 0.05/tick; attackers/advancing: 0.02/tick |
| 13 | **Morale** | Suppression and casualties erode morale; safety, nearby friendlies, and enemy kills restore it; march fatigue; units break below 15% |
| 14 | **Communications** | Heavy suppression degrades comms; offline units continue last task |
| 15 | **Ammo & Resupply** | Ammo consumed per fire tick; supply caches and logistics units resupply nearby friendlies |
| 16 | **Events & Reports** | Notable state changes logged; auto-reports generated (SPOTREP, SHELREP, CASREP, SITREP, INTSUM) |
| 17 | **Radio Chatter** | Idle unit reports, peer support requests, casualty reports, artillery fire exchanges, coordinated attack planning, contact-during-advance messages |
| 18 | **Area Effects** | Fire/chemical cloud damage applied; effect durations tick down; expired effects removed |
| 19 | **Victory Check** | Deterministic objective-control check (annihilation + objective threshold, no LLM); LLM evaluates custom victory conditions every 5 ticks; turn limit checked |
| 20 | **Broadcast** | Updated state pushed to all connected clients via WebSocket |

### Movement

```
effective_speed = base_speed × terrain_factor × slope_factor
                × (1 - suppression × 0.7) × morale_factor × weather_mod
```

| Terrain | Factor |
|---|---|
| Road | 1.0 |
| Open | 0.8 |
| Fields | 0.7 |
| Forest | 0.5 |
| Urban | 0.4 |
| Marsh | 0.3 |
| Water | 0.05 |

Slope penalty: `max(0.2, 1.0 - slope_deg/45)` — steep terrain dramatically slows movement.

**Aviation units bypass all terrain** — helicopters and UAVs fly over water, minefields, obstacles, and steep terrain without penalty.

- **Tactical A* pathfinding**: ground units navigate terrain-aware paths that avoid minefields, enemy observation, and impassable terrain
- **Speed mode affects routing**: slow prefers concealed routes (forest, urban); fast prefers roads and open terrain
- Ground units halt before discovered minefields (request engineers) and at water without bridges
- **Contact during advance**: moving (non-attack) units halt on enemy detection and request orders; resume after 3 ticks if no new orders

### Detection & LOS

```
detection_probability = base_prob × (1 - distance/range) × posture_mod × recon_bonus × concealment
```

- **Viewshed-based**: 72-ray cast; terrain obstacles block view; unit-type-specific eye heights (observation post=8m, tanks=3m, infantry=2m default)
- **Recon concealment**: Stationary recon/sniper/OP units nearly invisible (max 300m detection range, 10% base probability, 25% cap)
- Deterministic BLAKE2b hash ensures reproducibility for replay

### Combat

```
fire_effectiveness = base_firepower × strength × ammo_factor
                   × (1 - suppression) × terrain_mod × posture_mod
```

**Posture modifier** (defender advantage, historical basis: NATO 3:1 rule, Dupuy QJM):

| Situation | Modifier |
|---|---|
| Attacking unit | ×**0.65** — advancing under stress, no pre-positioned fires |
| Defending unit (auto-return fire) | ×**1.15** — pre-aimed sectors, range cards, known ground |
| Artillery / suppressing fire | ×1.0 (unchanged) |
| Equal units, open terrain | **1.77:1 exchange ratio** in favour of defender |
| Defender suppression ≥ 60% | Attack penalty fades linearly to zero (**breakthrough window**) |

- **Flanking**: ×0.75 to defender's protection — approaching from unfortified side
- **Elevation advantage**: +15% fire effectiveness when firing from higher ground
- **Combat role coordination**: Multiple attackers auto-coordinate — suppress (40%, covering fire at range), assault (1–2 infantry close in), flank (60° offset via covered terrain)
- **Area fire**: Artillery/mortar can fire at grid locations — 150m blast radius, damage falls off with distance
- **Finite salvos**: Fire missions limited to 3 salvos (configurable), then auto-complete
- **Danger close**: Artillery auto-ceases fire if friendly within 50m of target
- **Ceasefire coordination**: Infantry approaching a friendly bombardment zone (250m) halts and requests cease-fire; artillery finishes last salvo, infantry resumes

---

## Unit Types
43 unit types defined in `frontend/config/unit_types.json`, each with:
- MIL-STD-2525D SIDC codes (Blue + Red variants)
- Slow/fast movement speeds (m/s)
- Detection range, fire range, personnel count
- Eye height for LOS calculations
- Indirect fire flag (mortars, artillery)
- Special capabilities (aviation terrain bypass, cargo capacity, etc.)

**Air Defence units** (4 types):

| Type | Personnel | Fire Range |
|---|---|---|
| MANPADS Team | 4 | 3 km |
| MANPADS Section | 8 | 3.5 km |
| SAM Section | 12 | 8 km (10 km detection) |
| AA Gun Section | 10 | 2.5 km |

**Aviation units** (3 types):

| Type | Max Speed | Detection | Fire Range | Eye Height |
|---|---|---|---|---|
| Attack Helicopter | 70 m/s | 5 km | 4 km | 100 m |
| Transport Helicopter | 60 m/s | 3 km | — (12-person cargo) | 150 m |
| Recon UAV | 35 m/s | 8 km | — (unarmed) | 200 m |

Aviation order types: `air_assault` (helicopter insertion), `casevac`/`medevac` (casualty evacuation), `airstrike` (attack run).

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

---

## Testing

### Tactical Scenario Tests

Automated tactical scenario tests validate engine behavior (movement, detection, combat, coordination):

```powershell
# Run all tactical scenarios (requires running backend infrastructure)
python -m scripts.tactical_tests.run_all
# Output: tactical_test_report.html
```

**10 test scenarios:**

| Scenario | What it validates |
|---|---|
| Basic movement | Unit-type-specific speeds |
| Armored breakthrough | Combined arms coordination |
| Defensive stand | Dig-in, return fire |
| Combined arms | Multi-role coordination |
| Recon infiltration | Concealment mechanics |
| Meeting engagement | Mutual detection |
| Urban combat | Terrain effects |
| Night operations | Visibility modifiers |
| River crossing | Bridge requirements |
| Withdraw under pressure | Morale, disengage |

### Order Phrasebook Regression

The `order_phrasebook.toml` file contains **60+ `[[case]]` entries** that serve as regression tests for the keyword parser. Each case specifies input text and expected outputs (classification, order type, locations, speed, map object type). These are validated by the test suite to prevent parser regressions.

See `scripts/tactical_tests/` for scenario definitions and the test framework.

---

## API Documentation

FastAPI auto-generates interactive docs:

| Environment | Swagger UI | ReDoc |
|---|---|---|
| Production | `http://localhost/api/docs` | `http://localhost/api/redoc` |
| Development | `http://localhost:8000/docs` | `http://localhost:8000/redoc` |

---

## Project Structure

See `AGENTS.MD` for full architecture, domain model, and implementation roadmap.

```
KShU/
├── AGENTS.MD                       # Architecture & implementation guide
├── FIELD_MANUAL.md                 # Tactical doctrine source
├── README.md
├── README.ru.md
├── requirements.txt
├── docker-compose.yml              # PostgreSQL+PostGIS, Redis, backend, nginx, llama.cpp
├── Dockerfile                      # Backend multi-stage build
├── docker-entrypoint.sh            # Runs migrations on container start
├── nginx.conf                      # Reverse proxy + static serving config
├── deploy.ps1                      # One-command deployment script (Windows PowerShell)
├── alembic.ini
├── .env
│
├── backend/
│   ├── main.py                     # FastAPI app factory
│   ├── config.py                   # Pydantic settings
│   ├── database.py                 # Async SQLAlchemy engine
│   │
│   ├── models/                     # SQLAlchemy models (16 tables incl. learning_proposals)
│   │   ├── unit.py / order.py / session.py / scenario.py
│   │   ├── map_object.py           # Tactical map objects (obstacles, structures)
│   │   ├── terrain_cell.py         # TerrainCell (snail_path → terrain type + modifiers)
│   │   ├── elevation_cell.py       # ElevationCell (height/slope/aspect)
│   │   └── learning_proposal.py    # Candidate phrasebook entries mined from sessions
│   │
│   ├── api/                        # REST + WebSocket endpoints
│   │   ├── admin.py                # Session mgmt, god view, unit CRUD, CoC
│   │   ├── orders.py / units.py / sessions.py / scenarios.py
│   │   ├── map_objects.py          # Tactical object CRUD
│   │   ├── terrain.py              # Terrain analysis, painting, pathfinding endpoints
│   │   └── websocket.py            # WebSocket hub with Redis pub/sub
│   │
│   ├── engine/                     # Deterministic rules engine (tick processing)
│   │   ├── tick.py                 # Main tick orchestrator
│   │   ├── movement.py / detection.py / combat.py
│   │   ├── morale.py / suppression.py / comms.py / ammo.py
│   │   ├── defense.py              # Dig-in progression
│   │   ├── engineering.py          # Engineer unit interactions with tactical objects
│   │   ├── map_objects.py          # Area effect definitions and impact on units
│   │   ├── radio_chatter.py        # Auto-generated unit radio messages
│   │   ├── resupply.py             # Resupply engine
│   │   ├── intent_cascade.py       # HQ order intent propagation to subordinates
│   │   ├── geo_utils.py            # Geographic utilities (single source of truth)
│   │   └── _rng.py                 # Deterministic BLAKE2b RNG for reproducible replay
│   │
│   ├── services/
│   │   ├── order_parser.py         # 3-tier LLM routing (keyword → nano → full)
│   │   ├── order_phrasebook.py     # TOML phrasebook loader
│   │   ├── pathfinding_service.py  # Tactical A* over terrain cells
│   │   ├── retrieval_context.py    # Prompt compression & doctrine retrieval
│   │   ├── local_triage.py         # Local LLM triage classifier
│   │   ├── los_service.py          # LOS viewshed ray casting
│   │   ├── visibility_service.py   # Fog-of-war, command authority, unit serialization
│   │   ├── report_generator.py     # Auto-generate 5 report types per tick
│   │   ├── learning/               # Adaptive phrasebook mining
│   │   └── terrain_analysis/       # OSM + ESA + elevation analyzers
│   │
│   ├── data/
│   │   └── order_phrasebook.toml   # Bilingual keyword lexicon + regression cases
│   ├── prompts/                    # LLM prompt templates
│   ├── schemas/                    # Pydantic v2 schemas
│   └── tests/                      # Unit & integration tests
│
├── frontend/
│   ├── index.html
│   ├── config/
│   │   ├── unit_types.json         # Unit type registry (SIDC, speeds, personnel)
│   │   └── units_config.json       # Display/behavior constants
│   ├── css/style.css
│   └── js/
│       ├── app.js                  # Main entry, WS handlers
│       ├── map.js                  # Leaflet map, game clock
│       ├── units.js                # Unit rendering, selection, movement
│       ├── orders.js               # Command panel + radio chat
│       ├── admin.js                # Admin panel (~4300 lines)
│       ├── i18n.js                 # EN/RU internationalization
│       ├── replay.js               # Session replay with AAR
│       ├── tutorial.js             # KTutorial spotlight onboarding
│       ├── terrain.js              # Terrain overlay + elevation
│       ├── map_objects.js          # Tactical objects (mines, wire, bridges, etc.)
│       ├── overlays.js             # Drawing tools
│       ├── dialogs.js              # Themed confirm/alert/prompt modals
│       └── contacts.js / events.js / reports.js / grid.js / symbols.js
│
├── scripts/
│   ├── seed_scenario.py            # DB seed script
│   ├── download_model.ps1          # Download local LLM model
│   └── tactical_tests/             # Automated tactical scenario framework
│       ├── runner.py               # Creates sessions, injects orders, runs ticks
│       ├── run_all.py              # CLI entry point; generates HTML report
│       └── scenarios/              # 10 tactical scenario files (s01–s10)
│
└── models/                         # Local LLM model files (GGUF)
```

---

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
