Your task is to research, design, and then implement a credible MVP foundation for a web-based multiplayer tactical command/staff exercise platform (KShU / TDG), where:
- multiple human players cooperatively control the Blue side,
- the Red side is controlled by AI commander-agents representing enemy command hierarchy or subunits,
- the game runs on an interactive internet-accessible map,
- the server maintains authoritative hidden world state,
- multiple game sessions can exist simultaneously,
- players can collaboratively draw planning graphics on the map,
- the system understands tactical orders expressed in military language,
- the system understands locations by coordinates, grid squares, and recursive “snail” addressing.

Do NOT treat this as a chatbot project.
This is a multi-session, server-authoritative tactical simulation and collaborative command platform.

Your job is not merely to write code.
Your job is to make sound technical choices, document the reasoning, recommend the most suitable stack, and implement a clean MVP foundation.

==================================================
1. REQUIRED RESEARCH-FIRST APPROACH
==================================================

Before proposing the implementation, briefly analyze relevant existing solution categories and design patterns, such as:
- modern web mapping stacks,
- tactical/military symbol rendering,
- collaborative overlay editing,
- geospatial storage and tile serving,
- event exchange / situational awareness formats,
- tactical map overlay tools.

You do NOT need to clone any existing product.
You must extract useful architectural lessons and explicitly explain:
- what is worth borrowing,
- what should be avoided,
- what is overkill for MVP.

The final design must be grounded in this research.

==================================================
2. PRODUCT GOAL
==================================================

Build an MVP platform where:

- many Blue players can join the same session,
- they share the same visible tactical picture,
- they collaboratively draw arrows, routes, polygons, and notes on the map,
- they submit orders to Blue units,
- the system stores both the original order text and a structured parsed interpretation,
- the system extracts not only literal actions but tactical intent,
- AI Referee produces realistic reports,
- AI Red commander-agents control Red units using limited knowledge,
- the server advances simulation and enforces fog of war,
- many sessions can run in parallel.

The map is not only a visualization layer.
It is a command interface.

==================================================
3. NON-NEGOTIABLE ARCHITECTURAL PRINCIPLES
==================================================

These are mandatory:

1. Server-authoritative state
- The complete true world state exists only on the server.
- Clients are views and editors, never the source of truth.

2. AI/LLM is not the source of truth
- AI may interpret orders, infer intent, generate reports, and propose Red decisions.
- AI must never directly mutate authoritative state without backend validation.

3. Strict separation of concerns
The system must distinguish at least these layers:

A. Authoritative simulation state
- actual unit positions
- actual tasks
- actual contacts
- hidden enemy state
- combat/morale/comms state
- event timeline

B. Collaborative planning state
- arrows
- lines
- polygons
- points
- labels
- annotations
- planning graphics and sketches

C. Command interpretation state
- original order text
- parsed order structure
- resolved location references
- inferred tactical intent
- constraints and conditions
- confidence / ambiguity markers

These layers must not be conflated.

4. Multiple concurrent sessions
- All session state must be isolated by session/game ID.
- No single-session assumptions.

5. Fog of war
- Blue clients receive only Blue-visible state.
- Hidden Red information remains server-side.

6. Tactical location semantics are first-class
- The system must support:
  - absolute coordinates,
  - grid coordinates,
  - square IDs,
  - recursive snail addresses.

==================================================
4. TACTICAL GRID AND RECURSIVE “SNAIL” ADDRESSING
==================================================

This is critical.

The map must support a deterministic tactical square grid.
Each square may be recursively divided into 3x3 sub-squares.
The recursive location notation is called “snail”.

Examples:
- “9” means square 9
- “9-1” means sub-square 1 inside square 9
- “9-1-3” means sub-square 3 inside sub-square 1 inside square 9

Assume maximum depth = 3 levels, which is sufficient for tactical-level play.

Requirements:
- the grid system must be deterministic and code-based, not prompt-based,
- given a point on the map, the system can compute its snail path up to configured depth,
- given a snail reference, the system can resolve it to geometry,
- the system can highlight the corresponding square/sub-square on the frontend,
- AI and users can refer to locations using square and snail references,
- reports may also reference these locations.

Implement or propose a dedicated GridService responsible for:
- grid definition,
- square indexing,
- recursive subdivision,
- text-to-geometry resolution,
- point-to-snail lookup,
- validation of tactical location references.

Grid logic must NOT be hidden inside AI prompts.

==================================================
5. ORDER UNDERSTANDING REQUIREMENTS
==================================================

The system must understand tactical orders, not just parse generic text.

Orders may include:
- one or more units,
- square references,
- recursive snail references,
- terrain references,
- movement instructions,
- support relationships,
- timing/phasing,
- engagement restrictions,
- reserve instructions,
- observation sectors,
- intent statements.

The system must store:
- original human text,
- structured parsed order,
- resolved location references,
- inferred tactical intent,
- ambiguity/missing-fields markers.

Examples of semantics the system must support:
- move to square B4
- hold in square 9-1
- occupy 9-1-3
- advance along the creek toward D5
- observe east, avoid engagement unless necessary
- fix enemy frontally while another unit flanks
- keep anti-tank group in reserve
- support by fire from western edge of a square

You must design at least these logical services/interfaces:

A. OrderParser
- transforms free text into structured order JSON
- identifies unit references
- identifies location references
- identifies missing/ambiguous pieces

B. IntentInterpreter
- infers tactical meaning:
  - main effort,
  - support effort,
  - recon,
  - fix,
  - flank,
  - assault,
  - defend,
  - delay,
  - reserve,
  - support-by-fire,
  - screening,
  - engagement constraints,
  - sequencing / conditional transitions

C. LocationResolver
- validates and resolves referenced coordinates / squares / snail paths against grid and map data

AI may assist with interpretation, but backend validation is mandatory.

==================================================
6. RED SIDE AI MODEL
==================================================

Red side is not one omniscient AI blob.
It must be modeled as one or more commander-agents responsible for enemy formations or subunits.

Each Red agent should have:
- doctrine profile,
- mission intent,
- controlled units,
- limited knowledge,
- risk posture,
- local priorities.

Each Red agent outputs structured orders/intents for its own units.

Important:
- Red AI must not know hidden Blue information beyond what Red could reasonably infer.
- Red AI must not directly edit authoritative state.
- Red AI should be pluggable and replaceable.

==================================================
7. MULTIPLAYER BLUE SIDE
==================================================

Blue side is controlled by multiple human players.

Architecture must support:
- many users in one session,
- shared visible state,
- shared reports/log,
- collaborative planning overlays,
- potentially role-based permissions in future.

Future-friendly role examples:
- overall commander,
- company/platoon commander,
- support/fires officer,
- drone/recon operator,
- observer,
- admin/referee observer.

For MVP, permissions can be simpler, but the domain model must not block RBAC later.

==================================================
8. MAP AND COLLABORATIVE DRAWING REQUIREMENTS
==================================================

Players must be able to collaboratively draw on the map.

Required planning tools:
- arrows,
- polylines,
- polygons/areas,
- markers/points,
- labels/annotations if feasible.

Requirements:
- overlays are synchronized live between session participants,
- overlays are stored as first-class entities,
- overlays are editable/deletable,
- overlays are distinct from authoritative unit state,
- drawing an arrow does NOT automatically move a unit,
- overlays can be used as planning aids inside orders, but are not the order itself.

The frontend must also support:
- showing square IDs,
- showing snail reference under cursor or selection,
- inserting resolved references into order entry,
- highlighting square/sub-square geometry.

==================================================
9. DATA MODEL REQUIREMENTS
==================================================

Design explicit typed entities. At minimum cover:

1. User
- id
- display_name

2. Session / Game
- id
- scenario_id
- status
- current_time
- tick
- created_at
- settings

3. SessionParticipant
- session_id
- user_id
- side
- role
- permissions

4. Scenario
- id
- title
- map metadata
- terrain metadata
- objectives
- environment settings
- grid settings

5. Unit
- id
- session_id
- side
- name
- type
- parent_unit_id
- position / geometry
- strength
- ammo
- morale
- suppression
- communications status
- current task
- capabilities
- internal visibility metadata

6. Order
- id
- session_id
- issued_by_user_id nullable
- issued_by_side
- target unit or units
- order_type
- original_text nullable
- parsed_order_json
- parsed_intent_json
- status
- timestamps

7. LocationReference
- id
- session_id
- source_text
- reference_type (coordinate, grid, square, snail, terrain, mixed)
- normalized_reference
- resolved_geometry
- resolution_depth
- confidence
- validated

8. PlanningOverlay
- id
- session_id
- author_user_id
- overlay_type
- geometry
- style_json
- label
- created_at
- updated_at

9. Contact
- id
- session_id
- observing_side
- estimated_target_ref nullable
- location_estimate
- confidence
- last_seen_at
- source

10. Event
- id
- session_id
- timestamp
- type
- visibility_scope
- structured_payload
- text_summary

11. Report
- id
- session_id
- timestamp
- channel/type
- text
- metadata

12. RedAgent
- id
- session_id
- name
- doctrine_profile
- controlled_units
- intent_state
- knowledge_state

13. GridDefinition
- id
- scenario_id or session_id
- origin
- orientation
- base_square_size
- labeling_scheme
- recursion_base = 3
- max_depth = 3
- settings_json

You must recommend a practical persistence approach for:
- geometry,
- overlays,
- parsed order data,
- event history,
- replay/debrief support.

==================================================
10. STORAGE AND PERSISTENCE EXPECTATIONS
==================================================

Provide recommendations, not dogma, for storage architecture.

You must discuss and choose an approach for:
- authoritative geospatial data,
- collaborative overlays,
- parsed order payloads,
- event logs,
- AI interpretation artifacts.

The recommended design should explicitly address:
- whether geometries are stored in geospatial DB types or JSON,
- how GeoJSON-compatible serialization is handled,
- how to store structured order payloads and intent payloads,
- how to support replay and after-action review,
- how to separate immutable event history from mutable current state.

You may recommend:
- a geospatial database,
- document/json fields for flexible payloads,
- append-only event tables,
- materialized current-state tables,
- or another architecture,
but you must justify the choice.

==================================================
11. API / REALTIME REQUIREMENTS
==================================================

Design a clear API and realtime model for multi-session play.

At minimum support:
- create/list/join sessions,
- fetch visible state,
- submit orders,
- fetch reports/events,
- create/update/delete overlays,
- resolve location references,
- subscribe to realtime session updates.

Realtime updates should cover:
- visible state updates,
- order status,
- report creation,
- overlay changes,
- participant join/leave,
- clock/tick updates.

==================================================
12. RULES ENGINE REQUIREMENTS
==================================================

Implement or specify a deterministic rules engine for:
- movement,
- action durations,
- order delays,
- detection/visibility,
- engagement/fire resolution,
- suppression/losses,
- morale/cohesion,
- communication degradation,
- stale contacts.

Keep MVP formulas simple but explicit.

Do NOT hide core simulation transitions inside prompts.

==================================================
13. TECHNOLOGY SELECTION GUIDANCE
==================================================

You may choose the stack yourself, but your choices must satisfy the architecture.

You must explicitly justify:
- mapping/rendering framework,
- collaborative sync model,
- backend framework/runtime,
- geospatial storage,
- realtime transport,
- AI integration boundary.

When comparing options, prefer:
- maintainability,
- extensibility,
- good geometry support,
- good realtime support,
- clean separation of concerns.

Avoid stacks that force game logic into the client or blur simulation/planning layers.

==================================================
14. DELIVERABLES
==================================================

First provide:

1. research summary of existing relevant solution categories,
2. recommended architecture,
3. technology recommendations with tradeoffs,
4. domain/data model,
5. storage strategy,
6. tactical grid / recursive snail design,
7. API and realtime model,
8. implementation roadmap.

Only after that, begin implementation.

==================================================
15. WHAT TO AVOID
==================================================

Do NOT:
- build a simple chatbot pretending to be a wargame,
- keep state only in the frontend,
- let AI directly mutate authoritative world state,
- confuse planning drawings with orders,
- hardcode single-session assumptions,
- implement snail/grid logic only through prompts,
- make Red AI omniscient,
- store everything as opaque blobs with no typed model.

==================================================
16. SUCCESS CRITERION
==================================================

The final result must be a credible MVP foundation for a real AI-assisted KShU / TDG platform, with:
- multi-session support,
- multiplayer Blue collaboration,
- Red commander-agent AI,
- recursive snail-based tactical location system,
- collaborative planning overlays,
- structured order understanding,
- clear separation between simulation truth, planning graphics, and interpreted command intent.