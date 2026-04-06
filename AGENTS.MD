# AGENTS.MD — KShU / TDG Implementation Plan

> Web-based multiplayer tactical command/staff exercise platform.
> This document is the authoritative implementation guide. All coding agents must follow it section by section, phase by phase.

---

## Section 1: Research Summary

### 1.1 Web Mapping Stacks

| Library | Strengths | Weaknesses | Verdict |
|---|---|---|---|
| **Leaflet** | Mature, huge plugin ecosystem, simple API, lightweight (~40 kB). Native GeoJSON support. | Raster-only base tiles; no client-side vector styling. | **Recommended for MVP.** Simplicity wins; plugin ecosystem covers military symbols (Leaflet.draw, leaflet-editable). |
| **MapLibre GL JS** | Vector tiles, smooth zoom/rotation, 3D terrain, styleable layers. | Heavier bundle, steeper learning curve, vector tile infrastructure needed. | Good future upgrade path; overkill for MVP. |
| **OpenLayers** | Very powerful, OGC standards, projection support. | Large API surface, verbose, slower community growth. | Over-engineered for this use case. |

**Borrow:** Leaflet's plugin model and GeoJSON-first workflow.
**Avoid:** Self-hosted vector tile pipelines at MVP stage.

### 1.2 Tactical / Military Symbol Rendering

- **milsymbol.js** — generates MIL-STD-2525D / APP-6D SVG symbols on-the-fly from SIDC codes. Lightweight, no server dependency. **Use this.**
- NATO Joint Military Symbology standard defines the symbol identification coding (SIDC) scheme. Implement a thin wrapper mapping unit types to SIDC strings.
- **Avoid:** Full C2IS symbology engines (MACE, JC3IEDM) — enormous scope, not needed for MVP.

### 1.3 Collaborative Overlay Editing

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| **CRDT (Yjs/Automerge)** | True peer conflict resolution, offline support. | Complex setup, hard to integrate with server-authoritative model. | Overkill for MVP. |
| **OT (Operational Transform)** | Proven (Google Docs). | Difficult to implement correctly, mainly for text. | Not suitable for geometry ops. |
| **Server-broadcast (last-write-wins per overlay)** | Simple, fits server-authoritative model. Mutations go to server → server broadcasts to all clients via WebSocket. | No offline editing, potential brief flicker on conflict. | **Recommended for MVP.** Matches the server-authoritative principle. |

**Borrow:** Yjs's awareness protocol concept (cursor presence, "who is drawing") for future UX improvement.

### 1.4 Geospatial Storage

- **PostGIS** is the standard. Native geometry types, spatial indexes (GiST), ST_Contains/ST_Intersects for fog-of-war queries, GeoJSON export via `ST_AsGeoJSON`. **Use this.**
- Alternative (SQLite + SpatiaLite) is single-file but lacks concurrency. Not suitable for multi-session server.

### 1.5 Event Formats & Situational Awareness

- NATO standards (OVL, NFFI, MIP) are reference-grade but extremely heavy. **Avoid at MVP.**
- Design custom lightweight JSON event schema inspired by OVL concepts (event type, timestamp, visibility scope, payload).
- Append-only event table enables replay/debrief without complex event-sourcing infrastructure.

### 1.6 Tactical Overlay Tools

- **Leaflet.draw / Leaflet.Editable** — polygon, polyline, marker, circle editing. **Use Leaflet.Editable** (more flexible API).
- Custom arrow rendering via Leaflet polyline decorators or SVG overlay.
- NATO tactical graphics (boundary lines, phase lines, axis of advance) can be built as styled GeoJSON features with custom renderers on top of Leaflet.

---

## Section 2: Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        BROWSER CLIENTS                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐   │
│  │ Map View │  │ Order    │  │ Overlay  │  │ Reports /     │   │
│  │ (Leaflet)│  │ Panel    │  │ Tools    │  │ Game Log      │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬───────┘   │
│       │              │             │                 │           │
│       └──────────────┴─────────────┴─────────────────┘           │
│                        WebSocket + REST                          │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│                    BACKEND  (FastAPI + Python)                  │
│                                                                │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐   │
│  │  REST API   │  │ WebSocket   │  │  Session Manager     │   │
│  │  Endpoints  │  │ Hub         │  │  (create/join/tick)  │   │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬───────────┘   │
│         │                │                     │               │
│  ───────┴────────────────┴─────────────────────┴───────────    │
│  │              SERVICE LAYER                            │     │
│  │                                                       │     │
│  │  ┌────────────┐ ┌─────────────┐ ┌──────────────────┐  │     │
│  │  │GridService │ │OrderParser  │ │IntentInterpreter │  │     │
│  │  │(snail math)│ │(text→JSON)  │ │(tactical intent) │  │     │
│  │  └────────────┘ └─────────────┘ └──────────────────┘  │     │
│  │  ┌────────────────┐ ┌───────────────┐ ┌────────────┐  │     │
│  │  │LocationResolver│ │ Rules Engine  │ │ Red AI     │  │     │
│  │  │(coord→geom)    │ │ (simulation)  │ │ Agents     │  │     │
│  │  └────────────────┘ └───────────────┘ └────────────┘  │     │
│  │                                                       │     │
│  │  ┌────────────────────────────────────────────────┐   │     │
│  │  │         Fog-of-War Filter                      │   │     │
│  │  │  (filters world state → per-side visible view) │   │     │
│  │  └────────────────────────────────────────────────┘   │     │
│  └───────────────────────────────────────────────────────┘     │
│                                                                │
│  ───────────────── VALIDATION BOUNDARY ──────────────────────  │
│  AI/LLM calls happen ABOVE this line; all mutations happen     │
│  BELOW via deterministic validated code paths only.            │
│                                                                │
└──────────────────────────┬─────────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
   ┌──────────────────┐      ┌──────────────┐
   │  PostgreSQL +    │      │    Redis     │
   │  PostGIS         │      │  (pub/sub,  │
   │                  │      │   session   │
   │  - Units         │      │   cache)    │
   │  - Orders        │      │             │
   │  - Events        │      └──────────────┘
   │  - Overlays      │
   │  - Grid defs     │
   │  - Contacts      │
   │  - Reports       │
   └──────────────────┘
```

### Layer Separation

| Layer | What it holds | Mutated by | Visible to clients? |
|---|---|---|---|
| **A. Authoritative Simulation** | Unit positions, tasks, contacts, combat state, event timeline | Rules Engine only (deterministic tick) | Only via fog-of-war filter |
| **B. Collaborative Planning** | Arrows, polygons, labels, markers drawn by players | Player actions via REST/WS → server DB | All session participants (same side) |
| **C. Command Interpretation** | Original text, parsed order JSON, intent JSON, ambiguity markers | OrderParser + IntentInterpreter services | Issuing side only |

---

## Section 3: Technology Stack

### Frontend
| Choice | Justification |
|---|---|
| **Leaflet 1.9+** | Lightweight, huge ecosystem, GeoJSON-native. Sufficient for 2D tactical map. |
| **milsymbol.js** | On-the-fly MIL-STD-2525D SVG generation. No sprite sheets needed. |
| **Leaflet.Editable** | Programmatic geometry editing (draw arrows, polygons, polylines). |
| **Vanilla JS + minimal build** | No heavy framework needed for MVP. A single `index.html` + JS modules. If UI complexity grows, migrate to **Vue 3** (lighter than React, good reactivity model). |

### Backend
| Choice | Justification |
|---|---|
| **Python 3.12 + FastAPI** | Async-native, excellent typing, OpenAPI docs auto-generated. Best Python ecosystem for LLM integration (openai, langchain). Strong geospatial libraries (Shapely, GeoAlchemy2). |
| **SQLAlchemy 2.0 + GeoAlchemy2** | Typed ORM with PostGIS geometry column support. Async via `asyncpg`. |
| **Alembic** | Database migrations. |
| **Pydantic v2** | Request/response validation, domain model serialization, GeoJSON compatibility. |

### Database
| Choice | Justification |
|---|---|
| **PostgreSQL 16 + PostGIS 3.4** | Industry-standard geospatial DB. Spatial indexes (GiST), `ST_Contains`, `ST_DWithin` for detection queries, `ST_AsGeoJSON` for client serialization. JSONB for flexible payloads. |
| **Redis 7** | WebSocket pub/sub fan-out across workers, ephemeral session cache (online players, tick locks), rate limiting. |

### Realtime
| Choice | Justification |
|---|---|
| **FastAPI native WebSocket** | No extra dependency. Sufficient for MVP. Each client connects to `/ws/{session_id}`. Server broadcasts via Redis pub/sub to fan out across multiple worker processes. |
| *Alternative: Socket.IO* | Richer features (rooms, reconnect, fallback polling) but adds a layer. Consider post-MVP if reconnection becomes critical. |

### AI / LLM Integration
| Choice | Justification |
|---|---|
| **OpenAI API (GPT-4o / GPT-4.1)** | Best instruction-following for structured JSON output (function calling / structured outputs). Used for OrderParser, IntentInterpreter, Red AI reasoning, Report generation. |
| **Strict validation boundary** | Every LLM response is parsed by Pydantic models. Invalid outputs are rejected/retried. LLM never writes to DB directly. |

### Collaborative Sync
| Choice | Justification |
|---|---|
| **Server-broadcast, last-write-wins per overlay entity** | Matches server-authoritative principle. Client sends create/update/delete → server persists → server broadcasts to room. No CRDT complexity. |

### Tradeoff Summary
- **Leaflet vs MapLibre:** Leaflet is simpler but raster-only base tiles. Acceptable for MVP; MapLibre upgrade path exists.
- **FastAPI vs Node.js:** Python wins on AI/LLM ecosystem and geospatial libraries (Shapely). Node would need `turf.js` and has weaker LLM SDKs.
- **Native WS vs Socket.IO:** Native is simpler, Socket.IO adds reconnection. Start native, migrate if needed.
- **Server-broadcast vs CRDT:** CRDT is theoretically superior for collaboration but fights the server-authoritative model. Server-broadcast is correct here.

---

## Section 4: Domain Model

### 4.1 User
```
User
  id              UUID  PK
  display_name    VARCHAR(100)
  created_at      TIMESTAMP
```

### 4.2 Scenario
```
Scenario
  id              UUID  PK
  title           VARCHAR(200)
  description     TEXT
  map_center      GEOMETRY(Point, 4326)
  map_zoom        INTEGER
  map_bounds      GEOMETRY(Polygon, 4326)  nullable
  terrain_meta    JSONB          -- terrain type regions, elevation data refs
  objectives      JSONB          -- list of objective definitions
  environment     JSONB          -- weather, time-of-day, visibility
  grid_settings   JSONB          -- origin, orientation, square size, labeling
  initial_units   JSONB          -- unit templates for scenario setup
  created_at      TIMESTAMP
```

### 4.3 Session (Game)
```
Session
  id              UUID  PK
  scenario_id     UUID  FK → Scenario
  status          ENUM('lobby','running','paused','finished')
  current_time    TIMESTAMP      -- in-game simulated time
  tick            INTEGER        -- monotonic tick counter
  tick_interval   INTEGER        -- seconds per tick
  created_at      TIMESTAMP
  settings        JSONB          -- speed, house rules, etc.
```

### 4.4 SessionParticipant
```
SessionParticipant
  id              UUID  PK
  session_id      UUID  FK → Session
  user_id         UUID  FK → User
  side            ENUM('blue','red','observer','admin')
  role            VARCHAR(50)    -- 'commander','plt_commander','fires','observer',etc.
  permissions     JSONB          -- future RBAC flags
  joined_at       TIMESTAMP
```

### 4.5 GridDefinition
```
GridDefinition
  id              UUID  PK
  session_id      UUID  FK → Session   (copied from scenario on session create)
  origin          GEOMETRY(Point, 4326)  -- SW corner of grid
  orientation_deg FLOAT          -- grid rotation from north (0 = north-up)
  base_square_size_m  FLOAT      -- side length of top-level square in meters
  columns         INTEGER        -- number of columns at top level
  rows            INTEGER        -- number of rows at top level
  labeling_scheme VARCHAR(20)    -- 'alphanumeric' (A1,B2) or 'numeric' (1..N)
  recursion_base  INTEGER DEFAULT 3
  max_depth       INTEGER DEFAULT 3
  settings_json   JSONB
```

### 4.6 Unit
```
Unit
  id              UUID  PK
  session_id      UUID  FK → Session
  side            ENUM('blue','red')
  name            VARCHAR(100)   -- "1st Plt, A Co"
  unit_type       VARCHAR(50)    -- infantry_platoon, tank_company, etc.
  sidc            VARCHAR(30)    -- MIL-STD-2525D SIDC for rendering
  parent_unit_id  UUID  FK → Unit  nullable
  position        GEOMETRY(Point, 4326)
  heading_deg     FLOAT
  strength        FLOAT          -- 0.0 – 1.0 (fraction of full strength)
  ammo            FLOAT          -- 0.0 – 1.0
  morale          FLOAT          -- 0.0 – 1.0
  suppression     FLOAT          -- 0.0 – 1.0 (0 = none, 1 = fully suppressed)
  comms_status    ENUM('operational','degraded','offline')
  current_task    JSONB          -- {type, target_location, params}
  capabilities    JSONB          -- {has_atgm, has_mortar, recon_range_m, ...}
  move_speed_mps  FLOAT          -- base movement speed m/s
  detection_range_m FLOAT        -- base detection range
  is_destroyed    BOOLEAN DEFAULT FALSE
  updated_at      TIMESTAMP
```

### 4.7 Order
```
Order
  id              UUID  PK
  session_id      UUID  FK → Session
  issued_by_user_id UUID FK → User  nullable  (null = AI-issued)
  issued_by_side  ENUM('blue','red')
  target_unit_ids UUID[]         -- one or more units
  order_type      VARCHAR(50)    -- move, attack, defend, observe, support, ...
  original_text   TEXT  nullable  -- human-written order
  parsed_order    JSONB          -- structured parse output
  parsed_intent   JSONB          -- tactical intent output
  status          ENUM('pending','validated','executing','completed','failed','cancelled')
  issued_at       TIMESTAMP
  validated_at    TIMESTAMP  nullable
  completed_at    TIMESTAMP  nullable
```

### 4.8 LocationReference
```
LocationReference
  id              UUID  PK
  session_id      UUID  FK → Session
  order_id        UUID  FK → Order  nullable
  source_text     VARCHAR(200)
  reference_type  ENUM('coordinate','grid','square','snail','terrain','mixed')
  normalized_ref  VARCHAR(100)   -- e.g. "9-1-3" or "B4" or "48.8566,2.3522"
  resolved_geometry GEOMETRY(Geometry, 4326)  -- point or polygon
  resolution_depth INTEGER       -- snail depth (0-3)
  confidence      FLOAT          -- 0.0 – 1.0
  validated       BOOLEAN DEFAULT FALSE
```

### 4.9 PlanningOverlay
```
PlanningOverlay
  id              UUID  PK
  session_id      UUID  FK → Session
  author_user_id  UUID  FK → User
  side            ENUM('blue','red','observer')
  overlay_type    ENUM('arrow','polyline','polygon','marker','label','rectangle','circle')
  geometry        GEOMETRY(Geometry, 4326)
  style_json      JSONB          -- {color, weight, dashArray, fillOpacity, ...}
  label           VARCHAR(200)  nullable
  properties      JSONB          -- extra metadata, linked order ID, etc.
  created_at      TIMESTAMP
  updated_at      TIMESTAMP
```

### 4.10 Contact
```
Contact
  id              UUID  PK
  session_id      UUID  FK → Session
  observing_side  ENUM('blue','red')
  observing_unit_id UUID FK → Unit  nullable
  estimated_type  VARCHAR(50)  nullable
  estimated_size  VARCHAR(50)  nullable
  location_estimate GEOMETRY(Point, 4326)
  location_accuracy_m FLOAT
  confidence      FLOAT
  last_seen_tick  INTEGER
  last_seen_at    TIMESTAMP
  source          VARCHAR(50)   -- 'visual','recon','report','intercept'
  is_stale        BOOLEAN DEFAULT FALSE
```

### 4.11 Event
```
Event
  id              UUID  PK
  session_id      UUID  FK → Session
  tick            INTEGER
  game_timestamp  TIMESTAMP
  event_type      VARCHAR(50)  -- 'movement','combat','detection','order_issued',
                               -- 'order_completed','contact_new','contact_lost',
                               -- 'unit_destroyed','morale_break','comms_change'
  visibility      ENUM('all','blue','red','admin')
  actor_unit_id   UUID  nullable
  target_unit_id  UUID  nullable
  payload         JSONB
  text_summary    TEXT
  created_at      TIMESTAMP     -- wall-clock time of creation
```

### 4.12 Report
```
Report
  id              UUID  PK
  session_id      UUID  FK → Session
  tick            INTEGER
  game_timestamp  TIMESTAMP
  channel         VARCHAR(50)  -- 'sitrep','spotrep','contactrep','shelrep','intsum','custom'
  from_unit_id    UUID  FK → Unit  nullable
  to_side         ENUM('blue','red')
  text            TEXT
  structured_data JSONB  nullable  -- machine-readable version
  metadata        JSONB  nullable
  created_at      TIMESTAMP
```

### 4.13 RedAgent
```
RedAgent
  id              UUID  PK
  session_id      UUID  FK → Session
  name            VARCHAR(100)  -- "Red Bn Commander"
  doctrine_profile JSONB        -- {aggression, caution, initiative, ...}
  mission_intent  JSONB         -- high-level objective, constraints
  controlled_unit_ids UUID[]
  knowledge_state JSONB         -- what this agent believes about blue positions
  risk_posture    ENUM('aggressive','balanced','cautious','defensive')
  last_decision_tick INTEGER
  decision_state  JSONB         -- last reasoning output for debugging
```

### Entity Relationship Summary
```
Scenario 1──N Session
Session  1──N SessionParticipant ──1 User
Session  1──1 GridDefinition
Session  1──N Unit
Session  1──N Order  ──N LocationReference
Session  1──N PlanningOverlay
Session  1──N Contact
Session  1──N Event
Session  1──N Report
Session  1──N RedAgent
Unit     0──1 Unit (parent)
RedAgent ──N Unit (controlled)
```

---

## Section 5: Storage Strategy

### 5.1 Geometry Storage
- All positions and shapes stored as **PostGIS geometry columns** (`GEOMETRY(..., 4326)` — WGS84).
- Spatial GiST indexes on `Unit.position`, `PlanningOverlay.geometry`, `Contact.location_estimate`.
- Enables server-side spatial queries: fog-of-war (`ST_DWithin`), containment (`ST_Contains`), nearest units.

### 5.2 GeoJSON Serialization
- Query with `ST_AsGeoJSON(geom)` for client-facing responses.
- Pydantic models include `geometry` field as GeoJSON dict; serialized automatically.
- Client sends GeoJSON; backend converts via `ST_GeomFromGeoJSON`.

### 5.3 Flexible JSONB Payloads
Used for fields that vary by context and shouldn't be rigidly schematized at MVP:
- `Order.parsed_order`, `Order.parsed_intent` — AI-generated structured output.
- `Unit.current_task`, `Unit.capabilities` — task shape varies by order type.
- `RedAgent.doctrine_profile`, `knowledge_state`, `decision_state`.
- `Scenario.objectives`, `terrain_meta`, `environment`.
- `Event.payload`, `Report.structured_data`.

### 5.4 Event History (Append-Only)
- `Event` table is **append-only**. No updates or deletes.
- Indexed by `(session_id, tick)` for efficient range scans.
- Supports full replay: query all events for a session ordered by tick.
- After-action review: filter by event_type, visibility, unit.

### 5.5 Mutable Current-State Tables
- `Unit`, `Contact`, `RedAgent` — updated in-place each tick by the Rules Engine.
- `Order.status` — updated as orders progress through lifecycle.
- `PlanningOverlay` — updated by player edits.
- These tables reflect "current truth" and are the basis for client state queries.

### 5.6 Separation of Concerns in Storage
| Concern | Tables | Mutation Pattern |
|---|---|---|
| Authoritative sim state | Unit, Contact, Session (tick/time) | Rules Engine tick only |
| Planning state | PlanningOverlay | Player CRUD via API |
| Command interpretation | Order, LocationReference | OrderParser + IntentInterpreter write; Rules Engine reads |
| History / replay | Event, Report | Append-only |
| Configuration | Scenario, GridDefinition, User, SessionParticipant | Setup-time writes |

### 5.7 Redis Usage
- **Pub/Sub channels** per session: `session:{id}:events` — for WebSocket fan-out.
- **Ephemeral state**: online participant set, tick lock (distributed lock for simulation tick), rate limiting.
- **Not** used for persistent game state.

---

## Section 6: Grid & Snail System Design

### 6.1 Grid Definition
A grid is defined by:
- **origin**: geographic point (lat/lon) — the **southwest corner** of the grid.
- **orientation_deg**: rotation of the grid from north (0 = columns run N-S).
- **base_square_size_m**: side length of a top-level square in meters.
- **columns, rows**: number of squares at top level.
- **labeling_scheme**: `"alphanumeric"` → columns A-Z, rows 1-N (e.g. "B4"); or `"numeric"` → sequential 1..N row-major.

### 6.2 Square Labeling
- Top-level squares are labeled by the labeling scheme.
- In alphanumeric mode: column letter + row number (A1 is SW corner).
- In numeric mode: squares numbered 1..N in row-major order, bottom-to-top, left-to-right.

### 6.3 Recursive "Snail" Subdivision
Each square (at any depth) can be divided into a 3×3 grid of sub-squares. Sub-squares are numbered 1–9 in a **snail (spiral) pattern** starting from the top-left, going clockwise:

```
┌───┬───┬───┐
│ 1 │ 2 │ 3 │
├───┼───┼───┤
│ 8 │ 9 │ 4 │
├───┼───┼───┤
│ 7 │ 6 │ 5 │
└───┴───┴───┘
```

- Depth 0: top-level square (e.g. "B4" or "12")
- Depth 1: "B4-3" → sub-square 3 (top-right) inside B4
- Depth 2: "B4-3-7" → sub-square 7 (bottom-left) inside B4-3
- Depth 3: "B4-3-7-9" → center of that sub-sub-square (maximum depth)

### 6.4 GridService Interface

```
class GridService:
    def __init__(grid_def: GridDefinition)

    # Core geometry
    def square_to_polygon(square_label: str) -> Polygon
        """Top-level square label → geographic polygon."""

    def snail_to_polygon(snail_path: str) -> Polygon
        """'B4-3-7' → geographic polygon of that sub-square."""

    def snail_to_center(snail_path: str) -> Point
        """'B4-3-7' → geographic center point."""

    def point_to_snail(lat: float, lon: float, depth: int = 3) -> str
        """Geographic point → snail address at given depth."""

    def point_to_square(lat: float, lon: float) -> str
        """Geographic point → top-level square label."""

    # Validation
    def validate_snail(snail_path: str) -> bool
        """Check that snail path is syntactically and geometrically valid."""

    def validate_square(label: str) -> bool

    # Enumeration
    def all_squares() -> List[Tuple[str, Polygon]]
        """All top-level squares with their polygons."""

    def subdivide(snail_path: str) -> List[Tuple[str, Polygon]]
        """Children of a square/snail path (9 sub-squares)."""

    # Batch / GeoJSON
    def grid_as_geojson(depth: int = 0) -> FeatureCollection
        """Full grid at given depth as GeoJSON for frontend rendering."""
```

### 6.5 Core Math

1. **Grid-to-geographic transform**: The grid is a planar Cartesian grid anchored at `origin`. Convert grid-local (x, y) to geographic coords using a local tangent plane projection (pyproj UTM or Azimuthal Equidistant centered on origin). Apply rotation by `orientation_deg`.

2. **Square index to local coords**: For top-level square with column `c`, row `r`: local origin is `(c * size, r * size)`, polygon is the axis-aligned square of side `size`.

3. **Snail subdivision**: Given a parent square polygon (in local coords), divide into 3×3 sub-squares. Map snail number (1–9) to (col, row) offset:
   ```
   snail_to_offset = {
       1: (0, 2), 2: (1, 2), 3: (2, 2),
       4: (2, 1), 5: (2, 0), 6: (1, 0),
       7: (0, 0), 8: (0, 1), 9: (1, 1)
   }
   ```
   Sub-square side = parent side / 3. Recursively subdivide.

4. **Point-to-snail**: Transform point to local coords. Find top-level square (floor division). Then recursively find which of the 9 sub-squares contains the point, up to `max_depth`.

### 6.6 Frontend Integration
- On map load, fetch grid GeoJSON via `GET /api/sessions/{id}/grid?depth=0`.
- Render as Leaflet GeoJSON layer with square labels at centroid.
- On hover/click, request snail path: `GET /api/sessions/{id}/grid/point-to-snail?lat=...&lon=...&depth=2`.
- Show snail address in a tooltip/status bar.
- On snail path input (order panel), highlight geometry: `GET /api/sessions/{id}/grid/snail-to-geometry?path=B4-3-7`.
- Clicking a sub-square inserts the snail reference into the order text field.

---

## Section 7: API Design

### 7.1 REST Endpoints

**Authentication (MVP: simple token/session)**
```
POST   /api/auth/register          {display_name} → {user, token}
POST   /api/auth/login             {display_name} → {token}
```

**Sessions**
```
POST   /api/sessions               {scenario_id, settings} → Session
GET    /api/sessions               → [Session]
GET    /api/sessions/{id}          → Session (with participant count, status)
POST   /api/sessions/{id}/join     {side, role} → SessionParticipant
POST   /api/sessions/{id}/start    → Session (status=running)
POST   /api/sessions/{id}/pause    → Session
POST   /api/sessions/{id}/tick     → Session (advance one tick — admin/debug)
```

**Units (Blue-visible only, filtered by fog-of-war)**
```
GET    /api/sessions/{id}/units    → [Unit]  (filtered by requester's side)
GET    /api/sessions/{id}/units/{uid} → Unit
```

**Orders**
```
POST   /api/sessions/{id}/orders   {target_unit_ids, original_text} → Order
GET    /api/sessions/{id}/orders   ?status=...&unit_id=... → [Order]
GET    /api/sessions/{id}/orders/{oid} → Order (with parsed_order, parsed_intent)
POST   /api/sessions/{id}/orders/{oid}/cancel → Order
```

**Planning Overlays**
```
POST   /api/sessions/{id}/overlays  {overlay_type, geometry, style_json, label} → Overlay
GET    /api/sessions/{id}/overlays  → [Overlay]
PUT    /api/sessions/{id}/overlays/{oid}  {geometry, style_json, label} → Overlay
DELETE /api/sessions/{id}/overlays/{oid}  → 204
```

**Grid & Location**
```
GET    /api/sessions/{id}/grid                          → GeoJSON FeatureCollection
GET    /api/sessions/{id}/grid/snail-to-geometry?path=  → GeoJSON Feature
GET    /api/sessions/{id}/grid/point-to-snail?lat=&lon=&depth= → {snail_path, geometry}
POST   /api/sessions/{id}/locations/resolve  {text} → [LocationReference]
```

**Contacts**
```
GET    /api/sessions/{id}/contacts  → [Contact] (filtered by side)
```

**Events & Reports**
```
GET    /api/sessions/{id}/events    ?since_tick=&type= → [Event]
GET    /api/sessions/{id}/reports   ?channel=&since_tick= → [Report]
```

**Scenarios**
```
GET    /api/scenarios               → [Scenario]
GET    /api/scenarios/{id}          → Scenario
POST   /api/scenarios               → Scenario (admin)
```

### 7.2 WebSocket Protocol

**Connection**: `ws://host/ws/{session_id}?token=...`

**Client → Server messages:**
```json
{"type": "ping"}
{"type": "overlay_create", "data": {overlay_type, geometry, style_json, label}}
{"type": "overlay_update", "data": {overlay_id, geometry, style_json, label}}
{"type": "overlay_delete", "data": {overlay_id}}
{"type": "order_submit",   "data": {target_unit_ids, original_text}}
{"type": "cursor_position", "data": {lat, lon}}  // optional: presence
```

**Server → Client messages:**
```json
{"type": "state_update",     "data": {units: [...], contacts: [...], tick, game_time}}
{"type": "overlay_created",  "data": {overlay}}
{"type": "overlay_updated",  "data": {overlay}}
{"type": "overlay_deleted",  "data": {overlay_id}}
{"type": "order_status",     "data": {order_id, status, parsed_order, parsed_intent}}
{"type": "report_new",       "data": {report}}
{"type": "event_new",        "data": {event}}
{"type": "participant_joined", "data": {participant}}
{"type": "participant_left",   "data": {user_id}}
{"type": "tick_update",      "data": {tick, game_time}}
{"type": "cursor_positions", "data": [{user_id, lat, lon}, ...]}  // optional
{"type": "error",            "data": {message}}
```

**Broadcast logic**: On any mutation, server publishes to Redis channel `session:{id}:events`. All WebSocket workers subscribed to that channel push to connected clients (filtered by side/fog-of-war as applicable).

---

## Section 8: Rules Engine

The Rules Engine runs once per **tick**. A tick represents a configurable time step (default: 1 minute of game time). All formulas are deterministic — no LLM involvement.

### 8.1 Tick Sequence
```
1. Process pending validated orders → assign tasks to units
2. Execute movement for all units with movement tasks
3. Execute detection checks (Blue→Red and Red→Blue)
4. Decay stale contacts
5. Execute combat resolution for engaged units
6. Apply suppression effects
7. Apply morale effects
8. Update communication status
9. Generate events and reports
10. Advance session tick counter and game_time
11. Broadcast state_update to all connected clients
```

### 8.2 Movement
```
effective_speed = base_speed × terrain_factor × (1 - suppression × 0.7) × morale_factor
distance_this_tick = effective_speed × tick_duration_seconds
```
- `terrain_factor`: road=1.0, open=0.8, forest=0.5, urban=0.4, water=0.1
- `morale_factor`: morale > 0.5 → 1.0; morale 0.25–0.5 → 0.7; morale < 0.25 → 0.4
- Unit moves along straight line toward target. If distance_remaining < distance_this_tick, unit arrives and task completes.

### 8.3 Detection
```
detection_range_effective = base_detection_range × terrain_visibility × weather_mod
for each opposing unit within detection_range_effective:
    detection_probability = base_prob × (1 - distance/range) × posture_mod × recon_bonus
    roll = deterministic_hash(tick, observer_id, target_id) mod 100 / 100
    if roll < detection_probability:
        create or update Contact
```
- `base_prob`: 0.6 (standard), 0.8 (recon unit)
- `posture_mod`: moving=1.0, stationary=0.6, dug_in=0.3
- `terrain_visibility`: open=1.0, forest=0.5, urban=0.4
- Deterministic hash ensures reproducibility for replay.

### 8.4 Combat Resolution
```
When a unit with an attack/engage task is within weapons range of target:

fire_effectiveness = base_firepower × strength × ammo_factor × (1 - suppression) × terrain_mod
damage = fire_effectiveness × DAMAGE_SCALAR / target_protection
target.strength -= damage
target.suppression += suppression_inflicted
```
- `base_firepower`: by unit type (infantry_platoon=10, tank_company=30, mortar=20)
- `ammo_factor`: ammo > 0.5 → 1.0; 0.2–0.5 → 0.7; < 0.2 → 0.3
- `terrain_mod` (attacker): open=1.0, forest=0.7, urban=0.8
- `target_protection`: open=1.0, forest=1.3, urban=1.5, dug_in=2.0
- `DAMAGE_SCALAR`: 0.02 per tick (tunable — ~2% strength loss per minute under sustained fire)
- `suppression_inflicted`: fire_effectiveness × 0.03

### 8.5 Suppression Recovery
```
each tick where unit is NOT under fire:
    unit.suppression = max(0, unit.suppression - 0.05)
```

### 8.6 Morale
```
morale_delta = 0
morale_delta -= 0.02 × suppression              # suppression erodes morale
morale_delta -= 0.05 if strength < 0.5          # casualties lower morale
morale_delta -= 0.10 if strength < 0.25         # heavy casualties
morale_delta += 0.01 if not_in_combat            # slow recovery when safe
morale_delta += 0.02 if friendly_units_nearby    # mutual support
unit.morale = clamp(unit.morale + morale_delta, 0.0, 1.0)

if unit.morale < 0.15:
    unit breaks (retreats, stops executing orders)
    generate Event('morale_break')
```

### 8.7 Communications
```
if unit.comms_status == 'operational':
    orders reach instantly
elif unit.comms_status == 'degraded':
    order delivery delayed by 2 ticks
elif unit.comms_status == 'offline':
    orders do not reach; unit continues last task

Comms degradation triggers:
    - suppression > 0.7 → degrade to 'degraded' (probabilistic per tick)
    - unit in certain terrain → 'degraded'
    - comms unit destroyed → 'offline' for subordinates
```

### 8.8 Stale Contacts
```
ticks_since_seen = current_tick - contact.last_seen_tick
if ticks_since_seen > STALE_THRESHOLD (default 10):
    contact.is_stale = True
if ticks_since_seen > EXPIRE_THRESHOLD (default 30):
    delete contact
```

### 8.9 Ammo Consumption
```
each tick a unit fires:
    unit.ammo -= 0.01 × fire_rate_modifier
if unit.ammo <= 0:
    unit cannot fire, generates Event('ammo_depleted')
```

---

## Section 9: Implementation Roadmap

### Phase 1: Project Skeleton & Configuration
**Goal:** Runnable project structure, database connection, migrations framework.

| File | Contents |
|---|---|
| `requirements.txt` | FastAPI, uvicorn, sqlalchemy[asyncio], asyncpg, geoalchemy2, alembic, pydantic, shapely, pyproj, redis, httpx, python-jose (JWT) |
| `backend/main.py` | FastAPI app factory, CORS, lifespan (DB pool, Redis connection) |
| `backend/config.py` | Pydantic BaseSettings: DATABASE_URL, REDIS_URL, OPENAI_API_KEY, SECRET_KEY, defaults |
| `backend/database.py` | Async SQLAlchemy engine, sessionmaker, Base declarative |
| `backend/models/__init__.py` | Import all models |
| `backend/models/user.py` | User SQLAlchemy model |
| `backend/models/scenario.py` | Scenario model |
| `backend/models/session.py` | Session, SessionParticipant models |
| `backend/models/unit.py` | Unit model |
| `backend/models/order.py` | Order, LocationReference models |
| `backend/models/overlay.py` | PlanningOverlay model |
| `backend/models/contact.py` | Contact model |
| `backend/models/event.py` | Event model |
| `backend/models/report.py` | Report model |
| `backend/models/red_agent.py` | RedAgent model |
| `backend/models/grid.py` | GridDefinition model |
| `alembic.ini` | Alembic config pointing to DATABASE_URL |
| `alembic/env.py` | Alembic env with GeoAlchemy2 support |
| `alembic/versions/001_initial.py` | Initial migration — all tables |
| `docker-compose.yml` | PostgreSQL+PostGIS container, Redis container |
| `.env.example` | Example environment variables |
| `README.md` | Setup instructions |

### Phase 2: Grid / Snail System
**Goal:** Fully working GridService with API and frontend visualization.

| File | Contents |
|---|---|
| `backend/services/__init__.py` | (empty) |
| `backend/services/grid_service.py` | GridService class: all methods from Section 6.4. Uses Shapely for geometry, pyproj for CRS transforms. |
| `backend/schemas/grid.py` | Pydantic schemas: GridDefinitionRead, SnailQuery, SnailResult, GridGeoJSON |
| `backend/api/__init__.py` | (empty) |
| `backend/api/grid.py` | FastAPI router: `GET /grid`, `GET /grid/snail-to-geometry`, `GET /grid/point-to-snail` |
| `backend/tests/test_grid_service.py` | Unit tests: square_to_polygon, snail_to_polygon, point_to_snail round-trips, edge cases, validation |
| `frontend/index.html` | Base HTML with Leaflet map, CSS |
| `frontend/js/map.js` | Leaflet map initialization, tile layer, grid overlay rendering |
| `frontend/js/grid.js` | Fetch grid GeoJSON, render squares, show labels, hover for snail address |
| `frontend/css/style.css` | Map and UI styling |

### Phase 3: Session, User & Participant Management
**Goal:** Create/join sessions, simple auth.

| File | Contents |
|---|---|
| `backend/services/auth_service.py` | JWT token creation/validation, user lookup/create |
| `backend/services/session_service.py` | Create session (copy scenario → session, create GridDefinition, instantiate units), join, list, start/pause |
| `backend/schemas/auth.py` | RegisterRequest, LoginRequest, TokenResponse |
| `backend/schemas/session.py` | SessionCreate, SessionRead, SessionJoin, ParticipantRead |
| `backend/api/auth.py` | Auth endpoints |
| `backend/api/sessions.py` | Session CRUD endpoints |
| `backend/api/deps.py` | Dependency injection: get_db, get_current_user, get_session_participant |
| `backend/tests/test_sessions.py` | Integration tests: create scenario, create session, join, start |

### Phase 4: Map Frontend — Units, Fog of War
**Goal:** Render Blue-visible units with military symbols; fog-of-war filtering on server.

| File | Contents |
|---|---|
| `backend/services/visibility_service.py` | Fog-of-war filter: given session + side, return only visible units and contacts. Uses PostGIS `ST_DWithin` against Blue unit detection ranges. |
| `backend/schemas/unit.py` | UnitRead (GeoJSON-compatible), ContactRead |
| `backend/api/units.py` | `GET /sessions/{id}/units`, `GET /sessions/{id}/contacts` — calls visibility service |
| `frontend/js/units.js` | Fetch visible units, render with milsymbol.js as Leaflet markers. Poll or (Phase 5) use WebSocket. |
| `frontend/js/symbols.js` | milsymbol.js wrapper: SIDC → SVG icon → Leaflet icon |
| `frontend/js/contacts.js` | Render contacts as uncertainty ellipses / generic enemy markers |

### Phase 5: Collaborative Overlays — Drawing + Sync
**Goal:** Players draw on map; overlays sync via WebSocket.

| File | Contents |
|---|---|
| `backend/services/overlay_service.py` | CRUD for PlanningOverlay, broadcast change events |
| `backend/schemas/overlay.py` | OverlayCreate, OverlayUpdate, OverlayRead |
| `backend/api/overlays.py` | REST endpoints for overlays |
| `backend/api/websocket.py` | WebSocket endpoint `/ws/{session_id}`. Connection manager with Redis pub/sub. Handle overlay_create/update/delete messages. Broadcast state. |
| `backend/services/ws_manager.py` | ConnectionManager: track connections per session, pub/sub via Redis, broadcast helper, fog-of-war filtering on broadcast |
| `frontend/js/websocket.js` | WebSocket client: connect, reconnect, dispatch incoming messages to handlers |
| `frontend/js/overlays.js` | Leaflet.Editable integration: draw tools (arrow, polyline, polygon, marker), send to server, receive and render others' overlays |
| `frontend/js/ui.js` | Toolbar for overlay tools, order panel placeholder, session join UI |

### Phase 6: Order Entry, Parsing, Intent Interpretation
**Goal:** Players submit text orders; AI parses and interprets; results stored and displayed.

| File | Contents |
|---|---|
| `backend/services/order_parser.py` | OrderParser: sends order text + context (units, grid info) to LLM with structured output schema. Returns parsed order JSON. Validates with Pydantic. |
| `backend/services/intent_interpreter.py` | IntentInterpreter: takes parsed order → LLM call with tactical intent schema → returns intent JSON. |
| `backend/services/location_resolver.py` | LocationResolver: extracts location references from parsed order, resolves each via GridService or coordinate parsing. Creates LocationReference records. |
| `backend/services/order_service.py` | Orchestrates: receive text → OrderParser → IntentInterpreter → LocationResolver → validate → persist Order + LocationReferences → set status → broadcast |
| `backend/schemas/order.py` | OrderSubmit, OrderRead, ParsedOrder (Pydantic model for structured order), TacticalIntent (Pydantic model for intent), LocationReferenceRead |
| `backend/api/orders.py` | `POST /orders`, `GET /orders`, `GET /orders/{id}`, `POST /orders/{id}/cancel` |
| `backend/api/locations.py` | `POST /locations/resolve` — standalone location resolution |
| `backend/prompts/order_parser.py` | System prompt template for order parsing LLM call |
| `backend/prompts/intent_interpreter.py` | System prompt template for intent interpretation |
| `frontend/js/orders.js` | Order entry panel: text input, submit, display parsed result and intent, show location highlights on map, click-to-insert snail reference |

### Phase 7: Rules Engine — Simulation Tick
**Goal:** Deterministic simulation: movement, detection, combat, morale, comms.

| File | Contents |
|---|---|
| `backend/engine/__init__.py` | (empty) |
| `backend/engine/tick.py` | Main `run_tick(session_id)` function — orchestrates all sub-steps in order (Section 8.1) |
| `backend/engine/movement.py` | Move units toward task targets. Compute effective speed, advance position. |
| `backend/engine/detection.py` | Detection checks: iterate all opposing unit pairs, compute detection probability, create/update contacts. Deterministic hash for reproducibility. |
| `backend/engine/combat.py` | Combat resolution: fire effectiveness, damage, suppression inflicted. |
| `backend/engine/morale.py` | Morale update: suppression effect, casualty effect, recovery, proximity bonus, break check. |
| `backend/engine/comms.py` | Communications status update: degradation triggers, order delivery delay. |
| `backend/engine/contacts.py` | Stale contact decay and expiration. |
| `backend/engine/events.py` | Event generation helper: create Event rows for all notable state changes. |
| `backend/engine/ammo.py` | Ammo consumption per tick of firing. |
| `backend/services/tick_scheduler.py` | Background task: for each running session, acquire Redis lock, call run_tick, release. Configurable interval. |
| `backend/engine/terrain.py` | Terrain factor lookup: given a point, return terrain type and modifiers. (MVP: simple polygon regions from scenario; later: raster terrain.) |
| `backend/tests/test_movement.py` | Unit tests for movement calculations |
| `backend/tests/test_detection.py` | Unit tests for detection |
| `backend/tests/test_combat.py` | Unit tests for combat resolution |

### Phase 8: Red AI Commander Agents
**Goal:** AI agents control Red units with limited knowledge and doctrine.

| File | Contents |
|---|---|
| `backend/services/red_ai/__init__.py` | (empty) |
| `backend/services/red_ai/agent.py` | RedAIAgent class: `decide(red_agent, visible_state, tick)` → structured orders for controlled units. Builds prompt with doctrine, mission intent, known contacts, own unit status. Calls LLM. Parses response with Pydantic. |
| `backend/services/red_ai/knowledge.py` | Build Red agent's knowledge state: only contacts visible to Red, own unit states, terrain. Never includes hidden Blue data. |
| `backend/services/red_ai/doctrine.py` | Doctrine profiles: aggressive, balanced, cautious, defensive. Maps to prompt instructions and decision weights. |
| `backend/services/red_ai/runner.py` | `run_red_agents(session_id)`: called each tick (or every N ticks). Iterates all RedAgents for session, calls agent.decide(), validates output, creates Order records for Red units. |
| `backend/prompts/red_commander.py` | System prompt template for Red AI decision-making |
| `backend/schemas/red_agent.py` | RedAgentRead, RedDecision (Pydantic) |
| `backend/tests/test_red_ai.py` | Tests: knowledge filtering (no Blue leaks), output validation, doctrine influence |

### Phase 9: Reports, Events, Game Log
**Goal:** AI-generated sitreps/spotreps, event timeline, game log UI.

| File | Contents |
|---|---|
| `backend/services/report_generator.py` | Generate reports: after each tick, produce SITREPs (periodic), SPOTREPs (on new contact), contact reports. Uses LLM for natural language but structured_data is deterministic. |
| `backend/api/events.py` | `GET /sessions/{id}/events` with filtering |
| `backend/api/reports.py` | `GET /sessions/{id}/reports` with filtering |
| `backend/prompts/report_generator.py` | Prompt templates for different report types |
| `frontend/js/events.js` | Event log panel: scrolling timeline, filter by type, click to highlight on map |
| `frontend/js/reports.js` | Reports panel: display sitreps, spotreps, channel filter |
| `frontend/js/gamelog.js` | Unified game log view combining events, reports, orders chronologically |

### Phase 10: Polish, Testing, Deployment
**Goal:** End-to-end testing, UX polish, deployment configuration.

| File | Contents |
|---|---|
| `backend/tests/test_e2e_game.py` | Full game flow test: create scenario → session → join → submit orders → run ticks → verify state |
| `backend/tests/test_fog_of_war.py` | Verify Blue cannot see hidden Red units |
| `backend/tests/test_websocket.py` | WebSocket connection, message flow, broadcast tests |
| `backend/tests/conftest.py` | Pytest fixtures: test DB, test client, sample scenario, sample units |
| `frontend/js/app.js` | Main entry point: initialize map, connect WS, bind all UI modules |
| `frontend/js/session_ui.js` | Session lobby: create/join/list sessions UI |
| `Dockerfile` | Multi-stage build for backend |
| `docker-compose.prod.yml` | Production compose: backend, postgres, redis, nginx |
| `nginx.conf` | Reverse proxy: serve frontend static, proxy /api and /ws to backend |
| `scripts/seed_scenario.py` | Script to create a sample scenario with units, grid, objectives |
| `scripts/run_dev.py` | Dev runner: start backend + watch |
| `AGENTS.MD` | This file |

---

## Appendix A: Implementation Status

> Last updated: 2026-04-06

### Phases Completed / In Progress

| Phase | Status | Notes |
|---|---|---|
| **Phase 1: Project Skeleton** | ✅ Complete | FastAPI app, DB, models, migrations, Docker, config |
| **Phase 2: Grid / Snail System** | ✅ Complete | GridService, API endpoints, frontend rendering |
| **Phase 3: Session & User Management** | ✅ Complete | Auth (JWT), session CRUD, join, start/pause/tick, participants |
| **Phase 4: Map — Units, Fog of War** | ✅ Complete | milsymbol rendering, fog-of-war via PostGIS `ST_DWithin`, detection range filtering |
| **Phase 5: Collaborative Overlays** | ✅ Complete | Drawing tools (arrow, polyline, rect, marker, ellipse, measure), WebSocket sync, overlay context menu (color/width/label/delete) |
| **Phase 6: Order Entry** | ⚠ Partial | Text order submission, parsed_order for direct task (move), unit selection UI. LLM parsing/intent not yet wired. |
| **Phase 7: Rules Engine** | ✅ Complete | Tick processing: movement, detection, combat, morale, comms, suppression, ammo, contacts, events |
| **Phase 8: Red AI** | ❌ Not started | RedAgent model exists, but AI decision loop not implemented |
| **Phase 9: Reports, Events, Log** | ⚠ Partial | Events endpoint + frontend panel. Reports endpoint exists. AI-generated reports not wired. |
| **Phase 10: Polish & Deployment** | ⚠ Partial | Dev workflow works. Dockerfile/nginx not yet tested in production. |

### Features Implemented Beyond Original Plan

| Feature | Files | Description |
|---|---|---|
| **Admin Panel** | `admin.js`, `admin.py` | Floating admin window with password gate, sub-tabs (Session, Monitor, Builder, CoC, Users, Types). Session wizard for guided setup. |
| **Scenario Builder** | `scenario_builder.js`, `admin.js` | Interactive map-click unit placement, grid configuration, save/load scenarios. |
| **God View** | `admin.js`, `admin.py` | Admin toggle to see all units from both sides (bypasses fog-of-war). |
| **Unit Dashboard** | `admin.js` | Admin table of all units with focus, edit, delete, split, merge buttons. |
| **Chain of Command (Public CoC)** | `admin.js` (sidebar CoC tab) | Sidebar CoC tree visible to all players. Commanders can reassign subordinate units. |
| **Chain of Command (Admin CoC)** | `admin.js` (admin CoC sub-tab) | Admin-only full hierarchy editor with parent picker and bulk assign/unassign. |
| **Unit Split / Merge** | `units.py`, `admin.py`, `units.js`, `admin.js` | Split: auto-naming, echelon drop. Merge: combine strength, echelon up, re-parent children. Admin merge has no distance restriction. |
| **Unit Formations** | `units.py`, `units.js` | 10 formation types (column, line, wedge, vee, etc.), set via context menu. |
| **Unit Movement** | `units.py`, `units.js`, `engine/movement.py` | Set move target via map click, 3 speed presets, movement arrows on map, tick-based movement. |
| **Rubber-band Selection** | `units.js` | Left-drag on map for mass unit selection. Shift+click for additive selection. |
| **Unit Context Menu** | `units.js` | Right-click context menu: select, rename, formation, move, stop, split, merge, assign. |
| **Unit Status Icons** | `units.js` | Status derived from task/morale/suppression, shown in tooltip and CoC tree. |
| **Zoom-scaled Markers** | `symbols.js`, `units.js` | milsymbol markers scale with zoom level. |
| **Game Clock** | `map.js` | Top-right map overlay showing turn number and in-game date/time. |
| **Measurement Tool** | `overlays.js`, `map.js` | Polyline measurement with cumulative distance display. |
| **User Settings** | `session_ui.js` | Settings modal: toggle coords/snail/zoom display, tooltips, range circles, sounds. |
| **User Dropdown** | `session_ui.js` | User menu with rename, settings, logout. |
| **Scenario Description** | `session_ui.js` | Click session name to view scenario briefing modal. |
| **Favicon** | `main.py`, `favicon.svg` | Custom SVG favicon with fallback route. |
| **Credits** | `index.html`, `style.css` | Bottom sidebar credits bar. |

### Permission / Authority System

| Rule | Where Enforced |
|---|---|
| Observers cannot select/command units | `units.js: _canSelect`, `_canAssign` — checks both `side` and `role` |
| Observers cannot submit orders | `orders.py: submit_order` — 403 if side or role is observer |
| Observers cannot be assigned to units | `units.py: assign_unit`, `admin.py: PATCH unit` — validates target user |
| Observers cannot assign units | `units.py: assign_unit` — 403 if caller is observer |
| Side check: blue can't command red | `units.js: _canSelect` — rejects cross-side selection |
| Side check: blue can't order red units | `orders.py: submit_order` — validates unit side matches order side |
| Command authority via hierarchy | `visibility_service.py: check_command_authority` — walks parent chain |
| Implicit authority when no CoC | `visibility_service.py` — if no same-side units have assigned users, all same-side players have authority |
| Subordinate user authority | `visibility_service.py: check_subordinate_authority` — user A has authority if target is assigned to a subordinate of A |
| CoC tree assign buttons | `admin.js: _userCanAssign` — mirrors backend logic, includes "no CoC" fallback |

### Fog-of-War Implementation

- **Backend**: `visibility_service.py: get_visible_units()` uses PostGIS `ST_DWithin` to check if enemy units are within detection range of any friendly unit.
- **Own-side units**: Always fully visible with all stats.
- **Enemy units in range**: Visible but with reduced detail (no ammo/morale/suppression/comms/task info).
- **Default detection range**: 1500m if `detection_range_m` is NULL.
- **Admin/observer**: See all units from both sides.
- **Contacts**: Filtered by observing_side.

### Key Architecture Decisions Made

1. **Server-authoritative**: All mutations go through backend. Frontend never modifies game state directly.
2. **Deterministic tick**: Rules engine uses no randomness (deterministic hash for detection). Enables replay.
3. **No CoC = open authority**: When no units on a side have assigned users, any same-side player can command any unit. Authority restrictions activate only once assignments begin.
4. **Admin bypass**: Admin endpoints (`/api/admin/...`) skip authority and distance checks.
5. **Side + role dual observer check**: Observer restriction checks both `participant.side == 'observer'` and `participant.role == 'observer'` to handle blue/red-side observers.
6. **Append-only events**: Event table is never updated/deleted. Supports replay.
7. **JSONB for flexibility**: Unit tasks, capabilities, order payloads, scenario config use JSONB columns.

---

## Appendix B: File Tree Summary

```
KShU/
├── AGENTS.MD
├── README.md
├── Task.MD
├── requirements.txt
├── docker-compose.yml
├── alembic.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── backend/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── scenario.py
│   │   ├── session.py
│   │   ├── unit.py
│   │   ├── order.py
│   │   ├── overlay.py
│   │   ├── contact.py
│   │   ├── event.py
│   │   ├── report.py
│   │   ├── red_agent.py
│   │   └── grid.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py
│   │   ├── auth.py
│   │   ├── admin.py              ← Admin endpoints (session mgmt, god view, unit CRUD, CoC, users)
│   │   ├── sessions.py
│   │   ├── units.py              ← Unit endpoints (fog-of-war, assign, split/merge, move, formation)
│   │   ├── orders.py
│   │   ├── overlays.py
│   │   ├── grid.py
│   │   ├── locations.py
│   │   ├── events.py
│   │   ├── reports.py
│   │   ├── scenarios.py
│   │   └── websocket.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── session_service.py
│   │   ├── grid_service.py
│   │   ├── visibility_service.py ← Fog-of-war, command authority, unit serialization
│   │   ├── overlay_service.py
│   │   ├── ws_manager.py
│   │   └── tick_scheduler.py
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── tick.py
│   │   ├── movement.py
│   │   ├── detection.py
│   │   ├── combat.py
│   │   ├── morale.py
│   │   ├── comms.py
│   │   ├── contacts.py
│   │   ├── events.py
│   │   ├── ammo.py
│   │   ├── suppression.py
│   │   └── terrain.py
│   └── tests/
│       ├── __init__.py
│       ├── test_grid_service.py
│       ├── test_movement.py
│       ├── test_detection.py
│       └── test_combat.py
├── frontend/
│   ├── index.html
│   ├── favicon.svg
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js                ← Main entry, session join callback, WS handlers
│       ├── map.js                ← Leaflet map init, game clock, measurement, middle-button pan
│       ├── grid.js               ← Grid GeoJSON overlay, snail address tracking
│       ├── symbols.js            ← milsymbol.js wrapper, zoom-scaled icons
│       ├── units.js              ← Unit rendering, selection, context menu, split/merge/move/formation
│       ├── contacts.js           ← Contact rendering (enemy detections)
│       ├── overlays.js           ← Drawing tools (arrow, polyline, rect, marker, ellipse, measure)
│       ├── websocket.js          ← WebSocket client with reconnect
│       ├── orders.js             ← Order submission panel
│       ├── events.js             ← Events timeline panel
│       ├── reports.js            ← Reports panel
│       ├── gamelog.js            ← Unified game log
│       ├── session_ui.js         ← Login/logout, session list/join, settings, user menu
│       ├── scenario_builder.js   ← Interactive scenario builder (map-click unit placement)
│       ├── admin.js              ← Admin panel (3200+ lines): dashboard, CoC, wizard, god view, users, types
│       └── ui.js                 ← Map control overlay (2×2 buttons), sidebar tab switching
└── scripts/
    ├── __init__.py
    ├── seed_scenario.py
    ├── test_api_quick.py
    ├── test_flow.py
    ├── test_grid_quick.py
    ├── test_simulation.py
    ├── test_tick.py
    ├── test_viewport.py
    └── verify_system.py
```
