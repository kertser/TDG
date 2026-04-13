/**
 * map_objects.js – Render and manage tactical obstacles & structures on the Leaflet map.
 *
 * Features:
 *   - NATO-standard tactical symbol rendering for obstacles
 *   - SVG icons for structures (bunker, tower, bridge, etc.)
 *   - Line obstacle decorations: X marks (wire), zigzag (trench), double line (AT ditch),
 *     triangles (dragon's teeth)
 *   - Minefield polygons with NATO "M" diamond markers
 *   - Admin placement tools (draw-to-create for each object type)
 *   - Object context menu (delete, toggle active) — admin-only
 *   - Draggable point objects when admin panel is open
 *   - Toggle visibility, WebSocket sync
 */
const KMapObjects = (() => {
    let _map = null;
    let _layerGroup = null;
    let _visible = true;
    let _objects = [];
    let _definitions = null;
    let _drawingMode = null;
    let _drawLayer = null;
    let _sessionId = null;
    let _objectLayers = {};

    // ── Tool-mode hover fade (individual object transparency) ──
    let _toolFadedObjId = null;    // object ID currently faded during picking/LOS tool

    const METERS_PER_DEG = 111320;

    // ═══════════════════════════════════════════════════════════
    // NATO LINE DECORATION DEFINITIONS
    // ═══════════════════════════════════════════════════════════

    const LINE_DECO = {
        barbed_wire: {
            spacing: 50, size: [12, 12],
            svg: (c) => `<svg viewBox="0 0 12 12" width="12" height="12"><line x1="2" y1="2" x2="10" y2="10" stroke="${c}" stroke-width="2.2" stroke-linecap="round"/><line x1="10" y1="2" x2="2" y2="10" stroke="${c}" stroke-width="2.2" stroke-linecap="round"/></svg>`,
        },
        concertina_wire: {
            spacing: 35, size: [14, 14],
            svg: (c) => `<svg viewBox="0 0 14 14" width="14" height="14"><circle cx="7" cy="7" r="4" stroke="${c}" stroke-width="2" fill="none"/><line x1="3" y1="7" x2="11" y2="7" stroke="${c}" stroke-width="1.5"/></svg>`,
        },
        anti_tank_ditch: {
            spacing: 45, size: [8, 16],
            svg: (c) => `<svg viewBox="0 0 8 16" width="8" height="16"><line x1="4" y1="0" x2="4" y2="16" stroke="${c}" stroke-width="3" stroke-linecap="round"/></svg>`,
        },
        dragons_teeth: {
            spacing: 28, size: [14, 14],
            svg: (c) => `<svg viewBox="0 0 14 14" width="14" height="14"><polygon points="7,1 1,13 13,13" fill="${c}" opacity="0.85"/></svg>`,
        },
    };

    // ═══════════════════════════════════════════════════════════
    // STRUCTURE SVG ICONS (for map point markers)
    // ═══════════════════════════════════════════════════════════

    const STRUCTURE_SVGS = {
        pillbox: (c) =>
            `<svg viewBox="0 0 28 24" width="28" height="24">` +
            `<rect x="2" y="6" width="24" height="14" rx="2" fill="${c}" stroke="#333" stroke-width="1.5"/>` +
            `<rect x="2" y="4" width="24" height="5" rx="1.5" fill="#555" stroke="#333" stroke-width="1"/>` +
            `<rect x="9" y="12" width="10" height="4" rx="1" fill="#222"/>` +
            `</svg>`,
        observation_tower: (c) =>
            // Wooden watchtower: four legs, platform, roof
            `<svg viewBox="0 0 30 32" width="30" height="32">` +
            `<line x1="7" y1="30" x2="10" y2="14" stroke="#8B6914" stroke-width="2.2" stroke-linecap="round"/>` +
            `<line x1="23" y1="30" x2="20" y2="14" stroke="#8B6914" stroke-width="2.2" stroke-linecap="round"/>` +
            `<line x1="10" y1="22" x2="20" y2="22" stroke="#8B6914" stroke-width="1.5"/>` +
            `<line x1="9" y1="18" x2="21" y2="18" stroke="#8B6914" stroke-width="1.2"/>` +
            `<rect x="8" y="8" width="14" height="7" rx="1" fill="#A0855C" stroke="#6B4E1E" stroke-width="1.2"/>` +
            `<line x1="15" y1="8" x2="15" y2="15" stroke="#6B4E1E" stroke-width="0.8"/>` +
            `<polygon points="6,8 15,2 24,8" fill="#6B4E1E" stroke="#4A3210" stroke-width="0.8"/>` +
            `<rect x="12" y="10" width="6" height="4" fill="#4fc3f7" opacity="0.5" rx="0.5"/>` +
            `</svg>`,
        field_hospital: (c) =>
            `<svg viewBox="0 0 28 28" width="28" height="28">` +
            `<rect x="2" y="2" width="24" height="24" rx="4" fill="#fff" stroke="${c}" stroke-width="2"/>` +
            `<rect x="11.5" y="5.5" width="5" height="17" rx="0.5" fill="${c}"/>` +
            `<rect x="5.5" y="11.5" width="17" height="5" rx="0.5" fill="${c}"/>` +
            `</svg>`,
        command_post_structure: (c) =>
            // Tactical CP: flag/pennant with star
            `<svg viewBox="0 0 30 30" width="30" height="30">` +
            `<line x1="8" y1="4" x2="8" y2="28" stroke="#444" stroke-width="2" stroke-linecap="round"/>` +
            `<polygon points="9,4 26,9 9,14" fill="${c}" stroke="#0D47A1" stroke-width="1"/>` +
            `<polygon points="15,7.5 16.2,10 19,10.2 17,12 17.5,14.5 15,13 12.5,14.5 13,12 11,10.2 13.8,10" fill="#fff" opacity="0.9"/>` +
            `</svg>`,
        supply_cache: (c) =>
            `<svg viewBox="0 0 28 28" width="28" height="28">` +
            `<rect x="3" y="14" width="22" height="11" rx="1.5" fill="${c}" stroke="#4E342E" stroke-width="1.2"/>` +
            `<line x1="14" y1="14" x2="14" y2="25" stroke="#4E342E" stroke-width="1"/>` +
            `<line x1="3" y1="19.5" x2="25" y2="19.5" stroke="#4E342E" stroke-width="0.8"/>` +
            `<rect x="6" y="5" width="16" height="10.5" rx="1.5" fill="${c}" stroke="#4E342E" stroke-width="1.2" opacity="0.9"/>` +
            `<line x1="14" y1="5" x2="14" y2="15.5" stroke="#4E342E" stroke-width="1"/>` +
            `<line x1="6" y1="10" x2="22" y2="10" stroke="#4E342E" stroke-width="0.8"/>` +
            `</svg>`,
        bridge_structure: (c) =>
            // Top-down bridge view: deck, railings, supports, road markings
            `<svg viewBox="0 0 120 36" width="120" height="36">` +
            // Shadow/water hint underneath
            `<rect x="2" y="3" width="116" height="30" rx="2" fill="#3366aa" opacity="0.2"/>` +
            // Support pillars (visible through/beside deck)
            `<rect x="18" y="1" width="5" height="34" rx="1" fill="#555" opacity="0.5"/>` +
            `<rect x="50" y="1" width="5" height="34" rx="1" fill="#555" opacity="0.5"/>` +
            `<rect x="82" y="1" width="5" height="34" rx="1" fill="#555" opacity="0.5"/>` +
            // Main deck surface
            `<rect x="4" y="5" width="112" height="26" rx="1.5" fill="${c}"/>` +
            // Road surface (slightly lighter)
            `<rect x="8" y="9" width="104" height="18" fill="#606060"/>` +
            // Side barriers / guardrails
            `<rect x="4" y="5" width="112" height="3.5" rx="0.8" fill="#888" opacity="0.85"/>` +
            `<rect x="4" y="27.5" width="112" height="3.5" rx="0.8" fill="#888" opacity="0.85"/>` +
            // Center line dashes
            `<line x1="14" y1="18" x2="106" y2="18" stroke="#fff" stroke-width="1" stroke-dasharray="5,3.5" opacity="0.45"/>` +
            // Edge lane markings
            `<line x1="10" y1="10.5" x2="110" y2="10.5" stroke="#fff" stroke-width="0.5" opacity="0.25"/>` +
            `<line x1="10" y1="25.5" x2="110" y2="25.5" stroke="#fff" stroke-width="0.5" opacity="0.25"/>` +
            `</svg>`,
        airfield: (c) =>
            // Airfield: realistic top-down runway with taxiway, apron, markings
            `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 56" width="140" height="56">` +
            // Grass / ground surround
            `<rect x="0" y="0" width="140" height="56" rx="3" fill="#3a5a2a" opacity="0.35"/>` +
            // ── Main runway ──
            `<rect x="6" y="17" width="128" height="18" rx="1" fill="#505050"/>` +
            // Runway edge stripes
            `<line x1="10" y1="18.5" x2="130" y2="18.5" stroke="#fff" stroke-width="0.7" opacity="0.45"/>` +
            `<line x1="10" y1="33.5" x2="130" y2="33.5" stroke="#fff" stroke-width="0.7" opacity="0.45"/>` +
            // Center line dashes
            `<line x1="24" y1="26" x2="116" y2="26" stroke="#fff" stroke-width="1.4" stroke-dasharray="6,4" opacity="0.7"/>` +
            // ── Left threshold (4 bars) ──
            `<g opacity="0.75">` +
            `<rect x="10" y="19.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="10" y="28.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="13" y="19.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="13" y="28.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="16" y="19.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="16" y="28.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="19" y="19.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="19" y="28.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `</g>` +
            // ── Right threshold (4 bars) ──
            `<g opacity="0.75">` +
            `<rect x="120" y="19.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="120" y="28.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="123" y="19.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="123" y="28.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="126" y="19.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="126" y="28.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="129" y="19.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `<rect x="129" y="28.5" rx="0.3" width="1.6" height="4" fill="#fff"/>` +
            `</g>` +
            // ── Runway numbers ──
            `<text x="23" y="28" text-anchor="middle" font-size="6.5" font-weight="bold" fill="#fff" font-family="Arial,sans-serif" opacity="0.55">09</text>` +
            `<text x="117" y="28" text-anchor="middle" font-size="6.5" font-weight="bold" fill="#fff" font-family="Arial,sans-serif" opacity="0.55">27</text>` +
            // ── Taxiway (connecting runway to apron) ──
            `<rect x="58" y="35" width="14" height="10" rx="0.5" fill="#454545"/>` +
            `<line x1="65" y1="35.5" x2="65" y2="44" stroke="#D4A017" stroke-width="0.7" stroke-dasharray="2,1.5" opacity="0.6"/>` +
            // ── Second taxiway connector ──
            `<rect x="84" y="35" width="10" height="7" rx="0.5" fill="#454545"/>` +
            `<line x1="89" y1="35.5" x2="89" y2="41" stroke="#D4A017" stroke-width="0.6" stroke-dasharray="2,1.5" opacity="0.5"/>` +
            // ── Apron / parking area ──
            `<rect x="46" y="44" width="50" height="10" rx="1" fill="#3e3e3e" stroke="#555" stroke-width="0.4"/>` +
            // Parking stand lines (yellow)
            `<line x1="54" y1="44.5" x2="54" y2="53" stroke="#D4A017" stroke-width="0.5" opacity="0.5"/>` +
            `<line x1="62" y1="44.5" x2="62" y2="53" stroke="#D4A017" stroke-width="0.5" opacity="0.5"/>` +
            `<line x1="70" y1="44.5" x2="70" y2="53" stroke="#D4A017" stroke-width="0.5" opacity="0.5"/>` +
            `<line x1="78" y1="44.5" x2="78" y2="53" stroke="#D4A017" stroke-width="0.5" opacity="0.5"/>` +
            `<line x1="86" y1="44.5" x2="86" y2="53" stroke="#D4A017" stroke-width="0.5" opacity="0.5"/>` +
            // ── Small terminal building ──
            `<rect x="56" y="50" width="20" height="5" rx="1" fill="#606060" stroke="#777" stroke-width="0.4"/>` +
            // ── Runway designator arrowheads (touchdown zone) ──
            `<g opacity="0.4">` +
            `<rect x="28" y="24" width="4" height="4" rx="0.3" fill="none" stroke="#fff" stroke-width="0.6"/>` +
            `<rect x="108" y="24" width="4" height="4" rx="0.3" fill="none" stroke="#fff" stroke-width="0.6"/>` +
            `</g>` +
            `</svg>`,
    };

    // Emoji icons for structures that already look good (user approved)
    const STRUCTURE_EMOJI = {
        roadblock: '🚧',
        fuel_depot: '⛽',
    };

    // NATO minefield marker SVG (diamond with letter)
    const MINE_MARKER = (c, text) =>
        `<svg viewBox="0 0 22 22" width="20" height="20">` +
        `<polygon points="11,1 21,11 11,21 1,11" fill="rgba(0,0,0,0.3)" stroke="${c}" stroke-width="2"/>` +
        `<text x="11" y="14.5" text-anchor="middle" font-size="10" font-weight="bold" fill="${c}" font-family="Arial,sans-serif">${text}</text>` +
        `</svg>`;

    // Default colors
    const DEFAULT_COLORS = {
        barbed_wire: '#8B4513', concertina_wire: '#A0522D',
        minefield: '#FF4444', at_minefield: '#CC3333',
        entrenchment: '#5D4037', anti_tank_ditch: '#795548',
        dragons_teeth: '#9E9E9E', roadblock: '#FF9800',
        pillbox: '#616161', observation_tower: '#78909C',
        field_hospital: '#E53935',
        command_post_structure: '#1565C0', fuel_depot: '#F57F17',
        airfield: '#37474F', supply_cache: '#8D6E63',
        bridge_structure: '#757575',
        smoke: '#888888', fog_effect: '#E0E0E0',
        fire_effect: '#FF4400', chemical_cloud: '#AACC00',
    };

    // ═══════════════════════════════════════════════════════════
    // INITIALIZATION & DATA LOADING
    // ═══════════════════════════════════════════════════════════

    function init(map) {
        _map = map;
        _layerGroup = L.layerGroup().addTo(map);
        _initToolHoverFade();
    }

    /**
     * When coordinate-picking or LOS-checking is active, detect if the cursor
     * is over a map object (by checking bounds/proximity) and fade that object's
     * layer so the user sees the map underneath.
     */
    function _initToolHoverFade() {
        if (!_map) return;
        const container = _map.getContainer();
        if (!container) return;

        container.addEventListener('mousemove', (e) => {
            const isPicking = document.body.classList.contains('map-picking');
            const isLOS = document.body.classList.contains('map-los-checking');
            if (!isPicking && !isLOS) {
                _clearObjToolFade();
                return;
            }

            const rect = container.getBoundingClientRect();
            const mx = e.clientX - rect.left;
            const my = e.clientY - rect.top;
            const threshold = 35; // pixels

            let hitObjId = null;

            for (const obj of _objects) {
                if (!obj || !obj.geometry) continue;
                const layer = _objectLayers[obj.id];
                if (!layer) continue;

                const geom = obj.geometry;
                if (geom.type === 'Point') {
                    // Point object: check pixel distance to the point
                    const [lon, lat] = geom.coordinates;
                    const pt = _map.latLngToContainerPoint([lat, lon]);
                    const dx = mx - pt.x;
                    const dy = my - pt.y;
                    if (Math.sqrt(dx * dx + dy * dy) < threshold) {
                        hitObjId = obj.id;
                        break;
                    }
                } else if (geom.type === 'Polygon' || geom.type === 'MultiPolygon') {
                    // Polygon: check if cursor is inside the bounds (padded)
                    try {
                        const b = (layer.getBounds ? layer : layer.getLayers()[0]).getBounds();
                        if (b) {
                            const nw = _map.latLngToContainerPoint(b.getNorthWest());
                            const se = _map.latLngToContainerPoint(b.getSouthEast());
                            const pad = 8;
                            if (mx >= nw.x - pad && mx <= se.x + pad &&
                                my >= nw.y - pad && my <= se.y + pad) {
                                hitObjId = obj.id;
                                break;
                            }
                        }
                    } catch (_) { /* ignore */ }
                } else if (geom.type === 'LineString') {
                    // Line: check pixel distance to each vertex
                    for (const coord of geom.coordinates) {
                        const pt = _map.latLngToContainerPoint([coord[1], coord[0]]);
                        const dx = mx - pt.x;
                        const dy = my - pt.y;
                        if (Math.sqrt(dx * dx + dy * dy) < threshold) {
                            hitObjId = obj.id;
                            break;
                        }
                    }
                    if (hitObjId) break;
                    // Also check distance to line segments between vertices
                    const coords = geom.coordinates;
                    for (let i = 0; i < coords.length - 1; i++) {
                        const a = _map.latLngToContainerPoint([coords[i][1], coords[i][0]]);
                        const b2 = _map.latLngToContainerPoint([coords[i+1][1], coords[i+1][0]]);
                        const dist = _pointToSegmentDist(mx, my, a.x, a.y, b2.x, b2.y);
                        if (dist < threshold) {
                            hitObjId = obj.id;
                            break;
                        }
                    }
                    if (hitObjId) break;
                }
            }

            if (hitObjId !== _toolFadedObjId) {
                _clearObjToolFade();
                if (hitObjId) _fadeObjLayer(hitObjId);
                _toolFadedObjId = hitObjId;
            }
        });
    }

    /** Pixel distance from point (px,py) to line segment (ax,ay)-(bx,by). */
    function _pointToSegmentDist(px, py, ax, ay, bx, by) {
        const dx = bx - ax, dy = by - ay;
        const lenSq = dx * dx + dy * dy;
        if (lenSq === 0) return Math.sqrt((px - ax) ** 2 + (py - ay) ** 2);
        let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
        t = Math.max(0, Math.min(1, t));
        const cx = ax + t * dx, cy = ay + t * dy;
        return Math.sqrt((px - cx) ** 2 + (py - cy) ** 2);
    }

    /** Set opacity on all sub-layers of a map object. */
    function _fadeObjLayer(objId) {
        const layer = _objectLayers[objId];
        if (!layer) return;
        const fade = (l) => {
            if (l.setStyle) l.setStyle({ opacity: 0.25, fillOpacity: 0.08 });
            if (l.getElement) { const el = l.getElement(); if (el) el.style.opacity = '0.25'; }
            if (l.eachLayer) l.eachLayer(fade);
        };
        fade(layer);
    }

    /** Restore opacity on the previously faded object. */
    function _clearObjToolFade() {
        if (!_toolFadedObjId) return;
        const layer = _objectLayers[_toolFadedObjId];
        _toolFadedObjId = null;
        if (!layer) return;
        // Restore original opacity on all sub-layers
        const restore = (l) => {
            if (l.setStyle) l.setStyle({ opacity: 1, fillOpacity: 0.15 });
            if (l.getElement) { const el = l.getElement(); if (el) el.style.opacity = ''; }
            if (l.eachLayer) l.eachLayer(restore);
        };
        restore(layer);
    }

    function setSession(sid) { _sessionId = sid; }

    async function loadDefinitions(sessionId) {
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/map-objects/definitions`);
            if (resp.ok) {
                const data = await resp.json();
                _definitions = data.definitions;
                return data;
            }
        } catch (e) { console.warn('Failed to load map object definitions:', e); }
        return null;
    }

    async function load(sessionId, token) {
        _sessionId = sessionId;
        try {
            const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
            const resp = await fetch(`/api/sessions/${sessionId}/map-objects`, { headers });
            if (resp.ok) {
                _objects = await resp.json();
                render();

                // Trigger LOS-based discovery check — deferred to avoid blocking
                // initial map load (this endpoint is expensive: builds terrain + LOS service).
                setTimeout(() => {
                    fetch(`/api/sessions/${sessionId}/map-objects/discover`, {
                        method: 'POST', headers,
                    }).then(r => r.json()).then(data => {
                        if (data.discovered_count > 0) {
                            fetch(`/api/sessions/${sessionId}/map-objects`, { headers })
                                .then(r => r.json()).then(objs => {
                                    _objects = objs;
                                    render();
                                }).catch(() => {});
                        }
                    }).catch(() => {});
                }, 2000);
            }
        } catch (e) { console.warn('Failed to load map objects:', e); }
    }

    // ═══════════════════════════════════════════════════════════
    // RENDERING
    // ═══════════════════════════════════════════════════════════

    function disableAdminMode() {
        // Explicitly remove any lingering admin context menus
        const ctxMenu = document.getElementById('map-obj-ctx-menu');
        if (ctxMenu) ctxMenu.remove();
        // Force full re-render with admin state OFF
        render();
    }

    function render() {
        _layerGroup.clearLayers();
        _objectLayers = {};
        if (!_visible) return;
        const adminOpen = _isAdminOpen();
        const playerSide = typeof KSessionUI !== 'undefined' ? KSessionUI.getSide() : null;
        for (const obj of _objects) {
            // Discovery filter: non-admin players only see objects discovered for their side
            if (!adminOpen && playerSide && playerSide !== 'admin' && playerSide !== 'observer') {
                if (playerSide === 'blue' && !obj.discovered_by_blue) continue;
                if (playerSide === 'red' && !obj.discovered_by_red) continue;
            }
            const layer = _createLayer(obj);
            if (layer) {
                layer._mapObjId = obj.id;
                layer.addTo(_layerGroup);
                _objectLayers[obj.id] = layer;
            }
        }
    }

    function _isAdminOpen() {
        try { return KAdmin && KAdmin.isWindowOpen && KAdmin.isWindowOpen(); } catch(e) { return false; }
    }

    function _createLayer(obj) {
        if (!obj.geometry) return null;
        const color = (obj.definition && obj.definition.color) || DEFAULT_COLORS[obj.object_type] || '#888';
        const gtype = obj.geometry.type;
        const inactive = !obj.is_active;
        // When admin: objects hidden from both sides get extra low opacity to distinguish
        const hiddenFromBoth = _isAdminOpen() && !obj.discovered_by_blue && !obj.discovered_by_red;
        const opacity = inactive ? 0.35 : (hiddenFromBoth ? 0.45 : 0.85);

        if (gtype === 'Point') return _createPointLayer(obj, color, inactive, opacity);
        if (gtype === 'LineString' || gtype === 'MultiLineString') return _createLineLayer(obj, color, inactive, opacity);
        if (gtype === 'Polygon' || gtype === 'MultiPolygon') {
            if (obj.object_type === 'smoke') return _createSmokeLayer(obj, inactive);
            if (obj.object_type === 'fog_effect') return _createFogLayer(obj, inactive);
            if (obj.object_type === 'fire_effect') return _createFireLayer(obj, inactive);
            if (obj.object_type === 'chemical_cloud') return _createChemicalLayer(obj, inactive);
            return _createPolygonLayer(obj, color, inactive, opacity);
        }
        return null;
    }

    // ── Smoke screen rendering ──────────────────────────────────

    function _createSmokeLayer(obj, inactive) {
        const rings = obj.geometry.type === 'Polygon'
            ? obj.geometry.coordinates[0]
            : obj.geometry.coordinates[0][0];
        const latlngs = rings.map(c => [c[1], c[0]]);
        const remaining = obj.properties ? obj.properties.ticks_remaining : 3;
        const baseOpacity = inactive ? 0.1 : Math.min(0.35, 0.12 * remaining);

        const group = L.featureGroup();

        // Main smoke polygon — blurred edge
        const poly = L.polygon(latlngs, {
            color: 'rgba(180,180,180,0.2)',
            weight: 0,
            fillColor: '#b0b0b0',
            fillOpacity: baseOpacity * 0.4,
            className: 'smoke-polygon',
            interactive: true,
        });
        group.addLayer(poly);

        // Compute centroid + rough radius
        let cLat = 0, cLon = 0;
        latlngs.forEach(([lat, lon]) => { cLat += lat; cLon += lon; });
        cLat /= latlngs.length;
        cLon /= latlngs.length;

        // Estimate radius from polygon bounds
        let maxDist = 0;
        const mPerLon = 111320 * Math.cos(cLat * Math.PI / 180);
        latlngs.forEach(([lat, lon]) => {
            const d = Math.sqrt(((lat - cLat) * 111320) ** 2 + ((lon - cLon) * mPerLon) ** 2);
            if (d > maxDist) maxDist = d;
        });
        const radiusM = maxDist || 100;

        // Multiple overlapping semi-transparent circles for dispersed smoky effect
        const cloudCount = inactive ? 2 : Math.min(8, Math.max(3, remaining * 2));
        for (let i = 0; i < cloudCount; i++) {
            // Pseudo-random offsets (deterministic from obj.id char codes)
            const seed = (obj.id || '').charCodeAt(i % (obj.id || 'x').length) || 42;
            const angle = ((seed * 137.5 + i * 47) % 360) * Math.PI / 180;
            const offsetFrac = ((seed * 13 + i * 31) % 60) / 100;
            const oLat = cLat + (offsetFrac * radiusM * Math.cos(angle)) / 111320;
            const oLon = cLon + (offsetFrac * radiusM * Math.sin(angle)) / mPerLon;
            const cloudR = radiusM * (0.4 + ((seed + i * 17) % 40) / 60);

            group.addLayer(L.circle([oLat, oLon], {
                radius: cloudR,
                color: 'transparent',
                weight: 0,
                fillColor: '#c8c8c8',
                fillOpacity: baseOpacity * (0.3 + (i % 3) * 0.15),
                className: 'smoke-cloud',
                interactive: false,
            }));
        }

        // Tooltip and context menu
        const label = obj.label || 'Smoke Screen';
        const ticksLeft = remaining || '?';
        poly.bindTooltip(`🌫 ${label}<br>Dissipates in ~${ticksLeft} min`, {sticky: true, opacity: 0.9});
        poly.on('contextmenu', (e) => {
            L.DomEvent.stopPropagation(e);
            if (_isAdminOpen()) _showObjectContextMenu(e, obj);
        });

        // Admin drag handle
        if (_isAdminOpen()) {
            _addDragHandle(group, obj, latlngs);
        }

        return group;
    }

    // ── Fog zone rendering ─────────────────────────────────────

    function _createFogLayer(obj, inactive) {
        const rings = obj.geometry.type === 'Polygon'
            ? obj.geometry.coordinates[0]
            : obj.geometry.coordinates[0][0];
        const latlngs = rings.map(c => [c[1], c[0]]);
        const remaining = obj.properties ? obj.properties.ticks_remaining : 6;
        const baseOpacity = inactive ? 0.08 : Math.min(0.3, 0.06 * remaining);

        const group = L.featureGroup();

        // Main fog polygon — soft white diffuse area
        const poly = L.polygon(latlngs, {
            color: 'rgba(220,220,220,0.1)',
            weight: 0,
            fillColor: '#f0f0f0',
            fillOpacity: baseOpacity * 0.5,
            className: 'fog-polygon',
            interactive: true,
        });
        group.addLayer(poly);

        // Compute centroid + rough radius
        let cLat = 0, cLon = 0;
        latlngs.forEach(([lat, lon]) => { cLat += lat; cLon += lon; });
        cLat /= latlngs.length;
        cLon /= latlngs.length;
        const mPerLon = 111320 * Math.cos(cLat * Math.PI / 180);
        let maxDist = 0;
        latlngs.forEach(([lat, lon]) => {
            const d = Math.sqrt(((lat - cLat) * 111320) ** 2 + ((lon - cLon) * mPerLon) ** 2);
            if (d > maxDist) maxDist = d;
        });
        const radiusM = maxDist || 150;

        // Multiple soft white circles for diffuse fog appearance
        const cloudCount = inactive ? 2 : Math.min(10, Math.max(4, remaining * 2));
        for (let i = 0; i < cloudCount; i++) {
            const seed = (obj.id || '').charCodeAt(i % (obj.id || 'x').length) || 42;
            const angle = ((seed * 137.5 + i * 47) % 360) * Math.PI / 180;
            const offsetFrac = ((seed * 13 + i * 31) % 60) / 100;
            const oLat = cLat + (offsetFrac * radiusM * Math.cos(angle)) / 111320;
            const oLon = cLon + (offsetFrac * radiusM * Math.sin(angle)) / mPerLon;
            const cloudR = radiusM * (0.5 + ((seed + i * 17) % 40) / 60);

            group.addLayer(L.circle([oLat, oLon], {
                radius: cloudR,
                color: 'transparent',
                weight: 0,
                fillColor: '#ffffff',
                fillOpacity: baseOpacity * (0.25 + (i % 3) * 0.1),
                className: 'fog-cloud',
                interactive: false,
            }));
        }

        const label = obj.label || 'Fog Zone';
        const ticksLeft = remaining || '?';
        poly.bindTooltip(`🌫 ${label}<br>Dissipates in ~${ticksLeft} min`, {sticky: true, opacity: 0.9});
        poly.on('contextmenu', (e) => {
            L.DomEvent.stopPropagation(e);
            if (_isAdminOpen()) _showObjectContextMenu(e, obj);
        });

        // Admin drag handle
        if (_isAdminOpen()) {
            _addDragHandle(group, obj, latlngs);
        }

        return group;
    }

    // ── Fire zone rendering ──────────────────────────────────────

    function _createFireLayer(obj, inactive) {
        const rings = obj.geometry.type === 'Polygon'
            ? obj.geometry.coordinates[0]
            : obj.geometry.coordinates[0][0];
        const latlngs = rings.map(c => [c[1], c[0]]);
        const remaining = obj.properties ? obj.properties.ticks_remaining : 5;
        const baseOpacity = inactive ? 0.1 : Math.min(0.5, 0.12 * remaining);

        const group = L.featureGroup();

        // Main fire polygon — orange-red area
        const poly = L.polygon(latlngs, {
            color: 'rgba(255,68,0,0.3)',
            weight: 1,
            fillColor: '#FF4400',
            fillOpacity: baseOpacity * 0.4,
            className: 'fire-polygon',
            interactive: true,
        });
        group.addLayer(poly);

        // Compute centroid + rough radius
        let cLat = 0, cLon = 0;
        latlngs.forEach(([lat, lon]) => { cLat += lat; cLon += lon; });
        cLat /= latlngs.length;
        cLon /= latlngs.length;
        const mPerLon = 111320 * Math.cos(cLat * Math.PI / 180);
        let maxDist = 0;
        latlngs.forEach(([lat, lon]) => {
            const d = Math.sqrt(((lat - cLat) * 111320) ** 2 + ((lon - cLon) * mPerLon) ** 2);
            if (d > maxDist) maxDist = d;
        });
        const radiusM = maxDist || 80;

        // Fire circles: orange/red glowing spots with varying intensity
        const flameCount = inactive ? 2 : Math.min(8, Math.max(3, remaining * 2));
        for (let i = 0; i < flameCount; i++) {
            const seed = (obj.id || '').charCodeAt(i % (obj.id || 'x').length) || 42;
            const angle = ((seed * 137.5 + i * 47) % 360) * Math.PI / 180;
            const offsetFrac = ((seed * 13 + i * 31) % 65) / 100;
            const oLat = cLat + (offsetFrac * radiusM * Math.cos(angle)) / 111320;
            const oLon = cLon + (offsetFrac * radiusM * Math.sin(angle)) / mPerLon;
            const cloudR = radiusM * (0.3 + ((seed + i * 17) % 35) / 70);

            // Alternate orange and bright yellow for flame effect
            const isHot = (i % 3 === 0);
            group.addLayer(L.circle([oLat, oLon], {
                radius: cloudR,
                color: 'transparent',
                weight: 0,
                fillColor: isHot ? '#FFAA00' : '#FF4400',
                fillOpacity: baseOpacity * (isHot ? 0.5 : 0.35),
                className: 'fire-flame',
                interactive: false,
            }));
        }

        // Dark smoke ring on outer edge
        group.addLayer(L.circle([cLat, cLon], {
            radius: radiusM * 1.3,
            color: 'transparent',
            weight: 0,
            fillColor: '#333',
            fillOpacity: baseOpacity * 0.15,
            className: 'fire-smoke',
            interactive: false,
        }));

        const label = obj.label || 'Area Fire';
        const ticksLeft = remaining || '?';
        poly.bindTooltip(`🔥 ${label}<br>Burns out in ~${ticksLeft} min<br>⚠ Damages units inside`, {sticky: true, opacity: 0.9});
        poly.on('contextmenu', (e) => {
            L.DomEvent.stopPropagation(e);
            if (_isAdminOpen()) _showObjectContextMenu(e, obj);
        });

        if (_isAdminOpen()) {
            _addDragHandle(group, obj, latlngs);
        }

        return group;
    }

    // ── Chemical cloud rendering ─────────────────────────────────

    function _createChemicalLayer(obj, inactive) {
        const rings = obj.geometry.type === 'Polygon'
            ? obj.geometry.coordinates[0]
            : obj.geometry.coordinates[0][0];
        const latlngs = rings.map(c => [c[1], c[0]]);
        const remaining = obj.properties ? obj.properties.ticks_remaining : 8;
        const baseOpacity = inactive ? 0.08 : Math.min(0.35, 0.06 * remaining);

        const group = L.featureGroup();

        // Main chemical polygon — yellow-green toxic area
        const poly = L.polygon(latlngs, {
            color: 'rgba(170,204,0,0.25)',
            weight: 1,
            fillColor: '#AACC00',
            fillOpacity: baseOpacity * 0.4,
            className: 'chem-polygon',
            interactive: true,
        });
        group.addLayer(poly);

        // Compute centroid + rough radius
        let cLat = 0, cLon = 0;
        latlngs.forEach(([lat, lon]) => { cLat += lat; cLon += lon; });
        cLat /= latlngs.length;
        cLon /= latlngs.length;
        const mPerLon = 111320 * Math.cos(cLat * Math.PI / 180);
        let maxDist = 0;
        latlngs.forEach(([lat, lon]) => {
            const d = Math.sqrt(((lat - cLat) * 111320) ** 2 + ((lon - cLon) * mPerLon) ** 2);
            if (d > maxDist) maxDist = d;
        });
        const radiusM = maxDist || 120;

        // Toxic cloud circles: yellow-green splotches
        const cloudCount = inactive ? 2 : Math.min(9, Math.max(4, remaining * 1.5));
        for (let i = 0; i < cloudCount; i++) {
            const seed = (obj.id || '').charCodeAt(i % (obj.id || 'x').length) || 42;
            const angle = ((seed * 137.5 + i * 47) % 360) * Math.PI / 180;
            const offsetFrac = ((seed * 13 + i * 31) % 55) / 100;
            const oLat = cLat + (offsetFrac * radiusM * Math.cos(angle)) / 111320;
            const oLon = cLon + (offsetFrac * radiusM * Math.sin(angle)) / mPerLon;
            const cloudR = radiusM * (0.4 + ((seed + i * 17) % 40) / 60);

            // Alternate between yellow-green and darker green
            const isDark = (i % 3 === 0);
            group.addLayer(L.circle([oLat, oLon], {
                radius: cloudR,
                color: 'transparent',
                weight: 0,
                fillColor: isDark ? '#88AA00' : '#CCEE44',
                fillOpacity: baseOpacity * (isDark ? 0.35 : 0.25),
                className: 'chem-cloud',
                interactive: false,
            }));
        }

        const label = obj.label || 'Chemical Cloud';
        const ticksLeft = remaining || '?';
        poly.bindTooltip(`☣ ${label}<br>${KI18n.t('obj.dissipates')} ~${ticksLeft} ${KI18n.t('obj.min')}<br>${KI18n.t('obj.toxic')}`, {sticky: true, opacity: 0.9});
        poly.on('contextmenu', (e) => {
            L.DomEvent.stopPropagation(e);
            if (_isAdminOpen()) _showObjectContextMenu(e, obj);
        });

        if (_isAdminOpen()) {
            _addDragHandle(group, obj, latlngs);
        }

        return group;
    }

    // ── Point structures ──────────────────────────────────────

    function _createPointLayer(obj, color, inactive, opacity) {
        // Airfield uses geographic overlay (scales with map zoom)
        if (obj.object_type === 'airfield') {
            return _createAirfieldLayer(obj, color, inactive, opacity);
        }
        // Bridge uses geographic overlay (top-down, rotatable, resizable)
        if (obj.object_type === 'bridge_structure') {
            return _createBridgeLayer(obj, color, inactive, opacity);
        }

        const coords = obj.geometry.coordinates;
        const latlng = [coords[1], coords[0]];
        const label = obj.label || obj.object_type.replace(/_/g, ' ');
        const adminOpen = _isAdminOpen();

        // Determine icon: SVG function or emoji fallback
        const svgFn = STRUCTURE_SVGS[obj.object_type];
        let iconHtml;
        let iconW = 32, iconH = 32;
        if (svgFn) {
            iconHtml = `<div class="map-obj-svg-wrap" style="opacity:${opacity};" title="${label}">${svgFn(color)}</div>`;
        } else {
            const emoji = STRUCTURE_EMOJI[obj.object_type] || '⬟';
            iconHtml = `<div class="map-obj-point" style="border-color:${color};opacity:${opacity};" title="${label}">${emoji}</div>`;
        }

        const marker = L.marker(latlng, {
            icon: L.divIcon({
                className: 'map-obj-icon',
                html: iconHtml,
                iconSize: [iconW, iconH],
                iconAnchor: [iconW / 2, iconH / 2],
            }),
            draggable: adminOpen,
        });

        // Bind tooltip and contextmenu on the MARKER directly
        _bindTooltipAndContext(marker, obj);

        // Drag handling: update position on server when admin drags
        if (adminOpen) {
            marker.on('dragend', async () => {
                const newLL = marker.getLatLng();
                const newGeom = { type: 'Point', coordinates: [newLL.lng, newLL.lat] };
                const sid = _sessionId || KSessionUI?.getSessionId();
                const token = KSessionUI?.getToken();
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;
                try {
                    await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, {
                        method: 'PUT', headers,
                        body: JSON.stringify({ geometry: newGeom }),
                    });
                    // Update local cache and re-render to reposition effect circle
                    obj.geometry = newGeom;
                    render();
                } catch (err) { console.warn('Move map object failed:', err); }
            });
        }

        const group = L.featureGroup([marker]);

        // Effect radius circle
        const effectR = (obj.definition && obj.definition.effect_radius_m) || 0;
        if (effectR > 0) {
            group.addLayer(L.circle(latlng, {
                radius: effectR, color, weight: 1, dashArray: '4,4',
                fillColor: color, fillOpacity: inactive ? 0.03 : 0.06,
                opacity: opacity * 0.5, interactive: false,
            }));
        }
        return group;
    }

    // ── Airfield (geographic overlay — real-world scale, rotatable) ─────

    function _createAirfieldLayer(obj, color, inactive, opacity) {
        const coords = obj.geometry.coordinates;
        const centerLat = coords[1];
        const centerLon = coords[0];
        const label = obj.label || 'Airfield';
        const adminOpen = _isAdminOpen();
        const rotationDeg = (obj.properties && obj.properties.rotation_deg) || 0;

        // Real-world dimensions in meters (runway ~700m + apron)
        const lengthM = 700;
        const widthM = 280;   // ratio 2.5:1 matching SVG viewBox 140:56

        // Bounding circle radius: any rotation must fit inside a square of this half-side
        const diagM = Math.sqrt(lengthM * lengthM + widthM * widthM); // ~754m
        const halfSide = diagM / 2 + 20; // small padding

        // Square geographic bounds centered on airfield (accommodates any rotation)
        const mPerLon = METERS_PER_DEG * Math.cos(centerLat * Math.PI / 180);
        const dLat = halfSide / METERS_PER_DEG;
        const dLon = halfSide / mPerLon;

        const bounds = L.latLngBounds(
            [centerLat - dLat, centerLon - dLon],   // SW
            [centerLat + dLat, centerLon + dLon]     // NE
        );

        // Build rotated SVG: square viewBox with airfield content centered and rotated
        // Original SVG content: viewBox 0 0 140 56, center at (70, 28)
        const svgSize = 160; // square canvas larger than diagonal
        const cx = svgSize / 2;
        const cy = svgSize / 2;
        // Translation to center original content in new canvas
        const tx = cx - 70;
        const ty = cy - 28;

        const innerSvg = STRUCTURE_SVGS.airfield(color)
            .replace(/<svg[^>]*>/, '')  // strip opening svg tag
            .replace(/<\/svg>/, '');    // strip closing svg tag

        const svgString = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${svgSize} ${svgSize}" width="${svgSize}" height="${svgSize}">` +
            `<g transform="translate(${tx},${ty}) rotate(${rotationDeg}, 70, 28)">` +
            innerSvg +
            `</g></svg>`;

        const dataUri = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgString);

        const overlay = L.imageOverlay(dataUri, bounds, {
            opacity: opacity,
            interactive: false,  // image overlay click zone is unreliable
        });

        const group = L.featureGroup([overlay]);

        // Add an invisible interactive hit area for tooltip/contextmenu
        const hitArea = L.rectangle(bounds, {
            stroke: false,
            weight: 0,
            fill: true,
            fillColor: 'transparent',
            fillOpacity: 0.0001,
            color: 'transparent',
            interactive: true,
        });
        group.addLayer(hitArea);

        // Tooltip and context menu on the hit area (not the image)
        _bindTooltipAndContext(hitArea, obj);

        // Admin: drag handle (center) + rotation handle (runway end)
        if (adminOpen) {
            // ── Center drag handle ──
            const dragMarker = L.marker([centerLat, centerLon], {
                icon: L.divIcon({
                    className: 'map-obj-icon',
                    html: '<div style="width:22px;height:22px;border:2px dashed rgba(79,195,247,0.6);border-radius:50%;cursor:grab;background:rgba(79,195,247,0.1);"></div>',
                    iconSize: [22, 22],
                    iconAnchor: [11, 11],
                }),
                draggable: true,
            });

            // Propagate contextmenu from drag handle to show object menu
            dragMarker.on('contextmenu', (e) => {
                L.DomEvent.stopPropagation(e);
                _showObjectContextMenu(e, obj);
            });

            dragMarker.on('dragend', async () => {
                const newLL = dragMarker.getLatLng();
                const newGeom = { type: 'Point', coordinates: [newLL.lng, newLL.lat] };
                const sid = _sessionId || KSessionUI?.getSessionId();
                const token = KSessionUI?.getToken();
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;
                try {
                    await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, {
                        method: 'PUT', headers,
                        body: JSON.stringify({ geometry: newGeom }),
                    });
                    obj.geometry = newGeom;
                    render();
                } catch (err) { console.warn('Move map object failed:', err); }
            });

            group.addLayer(dragMarker);

            // ── Rotation handle (at runway end) ──
            const halfLen = lengthM / 2;
            const rotRad = rotationDeg * Math.PI / 180;
            // SVG X-axis = east, rotation CW. SVG Y-down = geographic south.
            const handleLat = centerLat - (halfLen * Math.sin(rotRad)) / METERS_PER_DEG;
            const handleLon = centerLon + (halfLen * Math.cos(rotRad)) / mPerLon;

            const rotHandle = L.marker([handleLat, handleLon], {
                icon: L.divIcon({
                    className: 'map-obj-icon',
                    html: '<div style="width:16px;height:16px;border:2px solid #ff9800;border-radius:50%;cursor:pointer;background:rgba(255,152,0,0.25);display:flex;align-items:center;justify-content:center;font-size:10px;" title="Drag to rotate">↻</div>',
                    iconSize: [16, 16],
                    iconAnchor: [8, 8],
                }),
                draggable: true,
            });

            // Propagate contextmenu from rotation handle
            rotHandle.on('contextmenu', (e) => {
                L.DomEvent.stopPropagation(e);
                _showObjectContextMenu(e, obj);
            });

            rotHandle.on('drag', () => {
                // Live preview: compute angle, rebuild SVG on-the-fly
                const handleLL = rotHandle.getLatLng();
                const dLatM = (handleLL.lat - centerLat) * METERS_PER_DEG;
                const dLonM = (handleLL.lng - centerLon) * mPerLon;
                // In SVG coords: X=east, Y=south (negative lat)
                let newDeg = Math.atan2(-dLatM, dLonM) * 180 / Math.PI;
                if (newDeg < 0) newDeg += 360;
                // Rebuild SVG with preview rotation
                const previewSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${svgSize} ${svgSize}" width="${svgSize}" height="${svgSize}">` +
                    `<g transform="translate(${tx},${ty}) rotate(${newDeg.toFixed(1)}, 70, 28)">` +
                    innerSvg +
                    `</g></svg>`;
                const previewUri = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(previewSvg);
                overlay.setUrl(previewUri);
            });

            rotHandle.on('dragend', async () => {
                const handleLL = rotHandle.getLatLng();
                const dLatM = (handleLL.lat - centerLat) * METERS_PER_DEG;
                const dLonM = (handleLL.lng - centerLon) * mPerLon;
                let newDeg = Math.atan2(-dLatM, dLonM) * 180 / Math.PI;
                if (newDeg < 0) newDeg += 360;
                newDeg = Math.round(newDeg);

                const sid = _sessionId || KSessionUI?.getSessionId();
                const token = KSessionUI?.getToken();
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;
                const updatedProps = { ...(obj.properties || {}), rotation_deg: newDeg };
                try {
                    await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, {
                        method: 'PUT', headers,
                        body: JSON.stringify({ properties: updatedProps }),
                    });
                    obj.properties = updatedProps;
                    render();
                } catch (err) { console.warn('Rotate airfield failed:', err); }
            });

            group.addLayer(rotHandle);
        }

        // Effect radius circle
        const effectR = (obj.definition && obj.definition.effect_radius_m) || 0;
        if (effectR > 0) {
            group.addLayer(L.circle([centerLat, centerLon], {
                radius: effectR, color, weight: 1, dashArray: '4,4',
                fillColor: color, fillOpacity: inactive ? 0.03 : 0.06,
                opacity: opacity * 0.5, interactive: false,
            }));
        }

        return group;
    }

    // ── Bridge (geographic overlay — top-down, rotatable, resizable) ─────

    function _buildBridgeSvg(innerSvg, svgW, svgH, svgSize, scaleY, rotationDeg) {
        const cx = svgSize / 2, cy = svgSize / 2;
        // Transform: translate to canvas center → rotate → scale Y for aspect ratio → translate content center to origin
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${svgSize} ${svgSize}" width="${svgSize}" height="${svgSize}">` +
            `<g transform="translate(${cx}, ${cy}) rotate(${rotationDeg}) scale(1, ${scaleY.toFixed(4)}) translate(${-svgW / 2}, ${-svgH / 2})">` +
            innerSvg +
            `</g></svg>`;
    }

    function _createBridgeLayer(obj, color, inactive, opacity) {
        const coords = obj.geometry.coordinates;
        const centerLat = coords[1];
        const centerLon = coords[0];
        const label = obj.label || 'Bridge';
        const adminOpen = _isAdminOpen();
        const rotationDeg = (obj.properties && obj.properties.rotation_deg) || 0;
        // Bridge dimensions from properties (default: 60m long, 14m wide)
        const lengthM = (obj.properties && obj.properties.length_m) || 60;
        const widthM = (obj.properties && obj.properties.width_m) || 14;

        // SVG viewBox: 120 x 36 (ratio 3.33:1)
        const svgW = 120, svgH = 36;

        // Bounding circle radius for any rotation
        const diagM = Math.sqrt(lengthM * lengthM + widthM * widthM);
        const halfSide = diagM / 2 + 5;

        const mPerLon = METERS_PER_DEG * Math.cos(centerLat * Math.PI / 180);
        const dLat = halfSide / METERS_PER_DEG;
        const dLon = halfSide / mPerLon;

        const bounds = L.latLngBounds(
            [centerLat - dLat, centerLon - dLon],
            [centerLat + dLat, centerLon + dLon]
        );

        // Compute svgSize so that svgW (120 units) maps to lengthM in geographic bounds (2*halfSide).
        // 120 / svgSize = lengthM / (2 * halfSide)  →  svgSize = 120 * 2 * halfSide / lengthM
        const svgSize = Math.ceil(svgW * 2 * halfSide / lengthM);
        // Correct Y-axis scaling: the SVG aspect ratio (120:36=3.33) may differ from bridge ratio (lengthM:widthM).
        // scaleY adjusts so that 36 SVG units map to widthM in the same coordinate space.
        const scaleY = (widthM / lengthM) * (svgW / svgH);

        const innerSvg = STRUCTURE_SVGS.bridge_structure(color)
            .replace(/<svg[^>]*>/, '').replace(/<\/svg>/, '');

        const svgString = _buildBridgeSvg(innerSvg, svgW, svgH, svgSize, scaleY, rotationDeg);
        const dataUri = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgString);

        const overlay = L.imageOverlay(dataUri, bounds, {
            opacity: opacity,
            interactive: false,
        });

        const group = L.featureGroup([overlay]);

        // Invisible hit area for tooltip/contextmenu
        const hitArea = L.rectangle(bounds, {
            stroke: false,
            weight: 0,
            fill: true,
            fillColor: 'transparent',
            fillOpacity: 0.0001,
            color: 'transparent',
            interactive: true,
        });
        group.addLayer(hitArea);
        _bindTooltipAndContext(hitArea, obj);

        // Admin: drag handle (center) + rotation handle + resize handle
        if (adminOpen) {
            // Helper: propagate contextmenu from admin handles
            const _bindCtx = (handle) => {
                handle.on('contextmenu', (e) => {
                    L.DomEvent.stopPropagation(e);
                    _showObjectContextMenu(e, obj);
                });
            };

            // ── Center drag handle ──
            const dragMarker = L.marker([centerLat, centerLon], {
                icon: L.divIcon({
                    className: 'map-obj-icon',
                    html: '<div style="width:18px;height:18px;border:2px dashed rgba(79,195,247,0.6);border-radius:50%;cursor:grab;background:rgba(79,195,247,0.1);"></div>',
                    iconSize: [18, 18],
                    iconAnchor: [9, 9],
                }),
                draggable: true,
            });

            dragMarker.on('dragend', async () => {
                const newLL = dragMarker.getLatLng();
                const newGeom = { type: 'Point', coordinates: [newLL.lng, newLL.lat] };
                const sid = _sessionId || KSessionUI?.getSessionId();
                const token = KSessionUI?.getToken();
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;
                try {
                    await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, {
                        method: 'PUT', headers,
                        body: JSON.stringify({ geometry: newGeom }),
                    });
                    obj.geometry = newGeom;
                    render();
                } catch (err) { console.warn('Move bridge failed:', err); }
            });
            _bindCtx(dragMarker);
            group.addLayer(dragMarker);

            // ── Rotation handle (at one end of bridge) ──
            const halfLen = lengthM / 2;
            const rotRad = rotationDeg * Math.PI / 180;
            const rotHandleLat = centerLat - (halfLen * Math.sin(rotRad)) / METERS_PER_DEG;
            const rotHandleLon = centerLon + (halfLen * Math.cos(rotRad)) / mPerLon;

            const rotHandle = L.marker([rotHandleLat, rotHandleLon], {
                icon: L.divIcon({
                    className: 'map-obj-icon',
                    html: '<div style="width:14px;height:14px;border:2px solid #ff9800;border-radius:50%;cursor:pointer;background:rgba(255,152,0,0.25);display:flex;align-items:center;justify-content:center;font-size:9px;" title="Drag to rotate">↻</div>',
                    iconSize: [14, 14],
                    iconAnchor: [7, 7],
                }),
                draggable: true,
            });

            rotHandle.on('drag', () => {
                const handleLL = rotHandle.getLatLng();
                const dLatM = (handleLL.lat - centerLat) * METERS_PER_DEG;
                const dLonM = (handleLL.lng - centerLon) * mPerLon;
                let newDeg = Math.atan2(-dLatM, dLonM) * 180 / Math.PI;
                if (newDeg < 0) newDeg += 360;
                const previewSvg = _buildBridgeSvg(innerSvg, svgW, svgH, svgSize, scaleY, newDeg.toFixed(1));
                overlay.setUrl('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(previewSvg));
            });

            rotHandle.on('dragend', async () => {
                const handleLL = rotHandle.getLatLng();
                const dLatM = (handleLL.lat - centerLat) * METERS_PER_DEG;
                const dLonM = (handleLL.lng - centerLon) * mPerLon;
                let newDeg = Math.atan2(-dLatM, dLonM) * 180 / Math.PI;
                if (newDeg < 0) newDeg += 360;
                newDeg = Math.round(newDeg);
                await _updateBridgeProps(obj, { rotation_deg: newDeg });
            });
            _bindCtx(rotHandle);
            group.addLayer(rotHandle);

            // ── Resize handle (at the other end — drag to change length) ──
            const resHandleLat = centerLat + (halfLen * Math.sin(rotRad)) / METERS_PER_DEG;
            const resHandleLon = centerLon - (halfLen * Math.cos(rotRad)) / mPerLon;

            const resHandle = L.marker([resHandleLat, resHandleLon], {
                icon: L.divIcon({
                    className: 'map-obj-icon',
                    html: '<div style="width:14px;height:14px;border:2px solid #4caf50;border-radius:2px;cursor:nwse-resize;background:rgba(76,175,80,0.25);display:flex;align-items:center;justify-content:center;font-size:9px;" title="Drag to resize">⤡</div>',
                    iconSize: [14, 14],
                    iconAnchor: [7, 7],
                }),
                draggable: true,
            });

            resHandle.on('drag', () => {
                // Live preview: compute new length from drag position
                const handleLL = resHandle.getLatLng();
                const dLatM = (handleLL.lat - centerLat) * METERS_PER_DEG;
                const dLonM = (handleLL.lng - centerLon) * mPerLon;
                const newHalfLen = Math.sqrt(dLatM * dLatM + dLonM * dLonM);
                const newLength = Math.max(20, Math.round(newHalfLen * 2));
                // Recompute SVG params for preview
                const newDiagM = Math.sqrt(newLength * newLength + widthM * widthM);
                const newHalfSide = newDiagM / 2 + 5;
                const newSvgSize = Math.ceil(svgW * 2 * newHalfSide / newLength);
                const newScaleY = (widthM / newLength) * (svgW / svgH);
                const previewSvg = _buildBridgeSvg(innerSvg, svgW, svgH, newSvgSize, newScaleY, rotationDeg);
                overlay.setUrl('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(previewSvg));
                // Update geographic bounds
                const newDLat = newHalfSide / METERS_PER_DEG;
                const newDLon = newHalfSide / mPerLon;
                const newBounds = L.latLngBounds(
                    [centerLat - newDLat, centerLon - newDLon],
                    [centerLat + newDLat, centerLon + newDLon]
                );
                overlay.setBounds(newBounds);
                hitArea.setBounds(newBounds);
            });

            resHandle.on('dragend', async () => {
                const handleLL = resHandle.getLatLng();
                const dLatM = (handleLL.lat - centerLat) * METERS_PER_DEG;
                const dLonM = (handleLL.lng - centerLon) * mPerLon;
                const newHalfLen = Math.sqrt(dLatM * dLatM + dLonM * dLonM);
                const newLength = Math.max(20, Math.round(newHalfLen * 2));
                await _updateBridgeProps(obj, { length_m: newLength });
            });
            _bindCtx(resHandle);
            group.addLayer(resHandle);
        }

        // Effect radius circle
        const effectR = (obj.definition && obj.definition.effect_radius_m) || 0;
        if (effectR > 0) {
            group.addLayer(L.circle([centerLat, centerLon], {
                radius: effectR, color, weight: 1, dashArray: '4,4',
                fillColor: color, fillOpacity: inactive ? 0.03 : 0.06,
                opacity: opacity * 0.5, interactive: false,
            }));
        }

        return group;
    }

    async function _updateBridgeProps(obj, propsUpdate) {
        const sid = _sessionId || KSessionUI?.getSessionId();
        const token = KSessionUI?.getToken();
        const headers = { 'Content-Type': 'application/json' };
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const updatedProps = { ...(obj.properties || {}), ...propsUpdate };
        try {
            await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, {
                method: 'PUT', headers,
                body: JSON.stringify({ properties: updatedProps }),
            });
            obj.properties = updatedProps;
            render();
        } catch (err) { console.warn('Update bridge failed:', err); }
    }

    // ── Line obstacles (NATO style) ───────────────────────────

    function _createLineLayer(obj, color, inactive, opacity) {
        const dashPattern = (obj.definition && obj.definition.dash_pattern) || null;
        const rawCoords = obj.geometry.type === 'LineString'
            ? obj.geometry.coordinates.map(c => [c[1], c[0]])
            : obj.geometry.coordinates.flatMap(ring => ring.map(c => [c[1], c[0]]));
        const objType = obj.object_type;

        // ── Entrenchment: NATO zigzag/sawtooth line ──
        if (objType === 'entrenchment') {
            return _createEntrenchmentLayer(obj, rawCoords, color, inactive, opacity);
        }

        // ── Anti-tank ditch: NATO double parallel lines + perpendicular bars ──
        if (objType === 'anti_tank_ditch') {
            return _createATDitchLayer(obj, rawCoords, color, inactive, opacity);
        }

        // ── Standard line obstacle + NATO decorations ──
        const style = { color, weight: 4, opacity, lineCap: 'round', lineJoin: 'round' };
        if (dashPattern) style.dashArray = dashPattern.join(',');
        if (inactive) style.dashArray = '3,6';

        const polyline = L.polyline(rawCoords, style);
        const group = L.featureGroup([polyline]);

        // Add NATO decoration symbols along the line
        const deco = LINE_DECO[objType];
        if (deco && !inactive) {
            _addLineDecorations(group, rawCoords, deco, color);
        }

        _bindTooltipAndContext(polyline, obj);

        // Admin: add centroid drag handle to move entire line
        if (_isAdminOpen()) {
            _addDragHandle(group, obj, rawCoords);
        }

        return group;
    }

    function _createEntrenchmentLayer(obj, coords, color, inactive, opacity) {
        // NATO trench symbol: sawtooth/zigzag line
        const zigzag = _computeZigzag(coords, 10, 20); // 10m amplitude, 20m wavelength
        const style = { color, weight: 5, opacity, lineCap: 'butt', lineJoin: 'miter' };
        if (inactive) { style.dashArray = '3,6'; style.weight = 3; }

        const zigzagLine = L.polyline(zigzag, style);
        // Thin reference base line
        const baseLine = L.polyline(coords, {
            color, weight: 1.5, opacity: opacity * 0.3, interactive: false,
        });
        const group = L.featureGroup([baseLine, zigzagLine]);
        _bindTooltipAndContext(zigzagLine, obj);

        // Admin: add centroid drag handle
        if (_isAdminOpen()) {
            _addDragHandle(group, obj, coords);
        }

        return group;
    }

    function _createATDitchLayer(obj, coords, color, inactive, opacity) {
        // NATO AT ditch: two parallel lines + perpendicular cross-bars
        const offsetM = 4;
        const line1 = _offsetPolyline(coords, offsetM);
        const line2 = _offsetPolyline(coords, -offsetM);

        const style = { color, weight: 3, opacity, lineCap: 'round' };
        if (inactive) { style.dashArray = '3,6'; style.weight = 2; }

        const pl1 = L.polyline(line1, style);
        const pl2 = L.polyline(line2, { ...style });
        const group = L.featureGroup([pl1, pl2]);

        // Add perpendicular bar decorations
        const deco = LINE_DECO['anti_tank_ditch'];
        if (deco && !inactive) {
            _addLineDecorations(group, coords, deco, color);
        }

        _bindTooltipAndContext(pl1, obj);
        pl2.on('contextmenu', (e) => {
            L.DomEvent.stopPropagation(e);
            _showObjectContextMenu(e, obj);
        });

        // Admin: add centroid drag handle
        if (_isAdminOpen()) {
            _addDragHandle(group, obj, coords);
        }

        return group;
    }

    // ── Polygon obstacles (minefields) ────────────────────────

    function _createPolygonLayer(obj, color, inactive, opacity) {
        const rings = obj.geometry.type === 'Polygon'
            ? [obj.geometry.coordinates[0].map(c => [c[1], c[0]])]
            : obj.geometry.coordinates.map(poly => poly[0].map(c => [c[1], c[0]]));

        // ── Minefields and other polygons ──
        const style = { color, weight: 2, opacity, fillColor: color, fillOpacity: inactive ? 0.05 : 0.15 };
        if (obj.object_type.includes('mine')) {
            style.dashArray = '6,4';
            style.fillOpacity = inactive ? 0.05 : 0.18;
        }

        const polygon = L.polygon(rings, style);
        const group = L.featureGroup([polygon]);

        // Add NATO mine markers inside minefields
        if (obj.object_type.includes('mine') && !inactive) {
            const mText = obj.object_type === 'at_minefield' ? 'AT' : 'M';
            _addMineMarkers(group, rings[0], color, mText);
        }

        _bindTooltipAndContext(polygon, obj);

        // Admin: add centroid drag handle to move entire polygon
        if (_isAdminOpen()) {
            _addDragHandle(group, obj, rings[0]);
        }

        return group;
    }

    // ═══════════════════════════════════════════════════════════
    // ADMIN DRAG HANDLE (for lines & polygons)
    // ═══════════════════════════════════════════════════════════

    /**
     * Add a draggable centroid marker to a featureGroup.
     * On dragend, translate the entire geometry by the delta and save to server.
     * @param {L.featureGroup} group - the layer group to add the handle to
     * @param {Object} obj - the map object data
     * @param {Array} latlngs - array of [lat, lng] coordinate pairs (for centroid calc)
     */
    function _addDragHandle(group, obj, latlngs) {
        if (!latlngs || latlngs.length === 0) return;

        // Compute centroid
        let cLat = 0, cLng = 0;
        latlngs.forEach(([lat, lng]) => { cLat += lat; cLng += lng; });
        cLat /= latlngs.length;
        cLng /= latlngs.length;

        const dragMarker = L.marker([cLat, cLng], {
            icon: L.divIcon({
                className: 'map-obj-icon',
                html: '<div style="width:18px;height:18px;border:2px dashed rgba(79,195,247,0.6);border-radius:50%;cursor:grab;background:rgba(79,195,247,0.15);"></div>',
                iconSize: [18, 18],
                iconAnchor: [9, 9],
            }),
            draggable: true,
        });

        dragMarker.on('dragend', async () => {
            const newLL = dragMarker.getLatLng();
            const dLat = newLL.lat - cLat;
            const dLng = newLL.lng - cLng;

            // Translate geometry coordinates
            const geom = JSON.parse(JSON.stringify(obj.geometry)); // deep clone
            _translateGeometry(geom, dLat, dLng);

            const sid = _sessionId || KSessionUI?.getSessionId();
            const token = KSessionUI?.getToken();
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = `Bearer ${token}`;
            try {
                await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, {
                    method: 'PUT', headers,
                    body: JSON.stringify({ geometry: geom }),
                });
                obj.geometry = geom;
                render();
            } catch (err) { console.warn('Move map object failed:', err); }
        });

        // Propagate contextmenu from drag handle
        dragMarker.on('contextmenu', (e) => {
            L.DomEvent.stopPropagation(e);
            if (_isAdminOpen()) _showObjectContextMenu(e, obj);
        });

        group.addLayer(dragMarker);
    }

    /**
     * Translate all coordinates in a GeoJSON geometry by (dLat, dLng).
     * GeoJSON uses [lon, lat] order.
     */
    function _translateGeometry(geom, dLat, dLng) {
        if (geom.type === 'Point') {
            geom.coordinates[0] += dLng;
            geom.coordinates[1] += dLat;
        } else if (geom.type === 'LineString' || geom.type === 'MultiPoint') {
            geom.coordinates.forEach(c => { c[0] += dLng; c[1] += dLat; });
        } else if (geom.type === 'Polygon' || geom.type === 'MultiLineString') {
            geom.coordinates.forEach(ring => ring.forEach(c => { c[0] += dLng; c[1] += dLat; }));
        } else if (geom.type === 'MultiPolygon') {
            geom.coordinates.forEach(poly => poly.forEach(ring => ring.forEach(c => { c[0] += dLng; c[1] += dLat; })));
        }
    }

    // ═══════════════════════════════════════════════════════════
    // TOOLTIP & CONTEXT MENU
    // ═══════════════════════════════════════════════════════════

    function _bindTooltipAndContext(layer, obj) {
        const label = obj.label || obj.object_type.replace(/_/g, ' ');
        const status = obj.is_active ? KI18n.t('obj.active') : KI18n.t('obj.inactive');
        const prot = obj.definition ? obj.definition.protection_bonus : 1.0;
        const rotDeg = (_isAdminOpen() && obj.properties && obj.properties.rotation_deg) ? ` · ↻${obj.properties.rotation_deg}°` : '';
        const lenM = (_isAdminOpen() && obj.properties && obj.properties.length_m) ? ` · ${obj.properties.length_m}m` : '';
        let tooltipHtml = `<b>${label}</b><br><span style="font-size:10px;">${obj.object_type} · ${status}${prot > 1 ? ` · ${KI18n.t('obj.prot')} ×${prot}` : ''}${rotDeg}${lenM}</span>`;
        // Show discovery status in admin mode
        if (_isAdminOpen()) {
            const bIcon = obj.discovered_by_blue ? '👁' : '🚫';
            const rIcon = obj.discovered_by_red ? '👁' : '🚫';
            tooltipHtml += `<br><span style="font-size:10px;">Blue ${bIcon} · Red ${rIcon}</span>`;
        }
        layer.bindTooltip(tooltipHtml, { sticky: true, className: 'map-obj-tooltip' });

        // When coordinate-picking or LOS-checking is active, let clicks pass through
        // to the map instead of being consumed by the object.
        layer.on('click', (e) => {
            if (document.body.classList.contains('map-picking')
                || (typeof KMap !== 'undefined' && KMap.isLOSChecking && KMap.isLOSChecking())) {
                // Don't stop propagation — let the map receive the click
                return;
            }
        });

        layer.on('contextmenu', (e) => {
            L.DomEvent.stopPropagation(e);
            // Only show admin actions when admin panel is open
            if (_isAdminOpen()) {
                _showObjectContextMenu(e, obj);
            }
        });
    }

    function _showObjectContextMenu(e, obj) {
        let existing = document.getElementById('map-obj-ctx-menu');
        if (existing) existing.remove();

        const menu = document.createElement('div');
        menu.id = 'map-obj-ctx-menu';
        menu.className = 'ctx-menu';
        menu.style.cssText = `display:block;position:fixed;left:${e.originalEvent.clientX}px;top:${e.originalEvent.clientY}px;z-index:10000;min-width:180px;`;

        const label = obj.label || obj.object_type.replace(/_/g, ' ');
        const blueDisc = obj.discovered_by_blue;
        const redDisc = obj.discovered_by_red;
        const blueIcon = blueDisc ? '👁' : '🚫';
        const redIcon = redDisc ? '👁' : '🚫';

        menu.innerHTML = `
            <div class="ctx-menu-header" style="font-size:11px;padding:4px 8px;color:#4fc3f7;">${label}</div>
            <div class="ctx-menu-section">
                <div class="ctx-item" data-action="toggle">${obj.is_active ? KI18n.t('obj.deactivate') : KI18n.t('obj.activate')}</div>
                <div class="ctx-menu-divider" style="border-top:1px solid #333;margin:2px 0;"></div>
                <div class="ctx-item" data-action="disc_blue" style="font-size:12px;">${blueIcon} Blue: ${blueDisc ? KI18n.t('obj.revealed') : KI18n.t('obj.hidden')} → ${blueDisc ? KI18n.t('obj.hide') : KI18n.t('obj.reveal')}</div>
                <div class="ctx-item" data-action="disc_red" style="font-size:12px;">${redIcon} Red: ${redDisc ? KI18n.t('obj.revealed') : KI18n.t('obj.hidden')} → ${redDisc ? KI18n.t('obj.hide') : KI18n.t('obj.reveal')}</div>
                <div class="ctx-menu-divider" style="border-top:1px solid #333;margin:2px 0;"></div>
                <div class="ctx-item ctx-item-danger" data-action="delete">${KI18n.t('obj.delete')}</div>
            </div>`;

        document.body.appendChild(menu);

        menu.addEventListener('click', async (ev) => {
            const action = ev.target.closest('[data-action]')?.dataset.action;
            if (!action) return;
            menu.remove();

            const sid = _sessionId || KSessionUI?.getSessionId();
            const token = KSessionUI?.getToken();
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = `Bearer ${token}`;

            if (action === 'delete') {
                try {
                    await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, { method: 'DELETE', headers });
                    _objects = _objects.filter(o => o.id !== obj.id);
                    render();
                } catch (err) { console.warn('Delete map object failed:', err); }
            } else if (action === 'toggle') {
                try {
                    await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, {
                        method: 'PUT', headers,
                        body: JSON.stringify({ is_active: !obj.is_active }),
                    });
                    obj.is_active = !obj.is_active;
                    render();
                } catch (err) { console.warn('Toggle map object failed:', err); }
            } else if (action === 'disc_blue') {
                const newVal = !obj.discovered_by_blue;
                try {
                    await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, {
                        method: 'PUT', headers,
                        body: JSON.stringify({ discovered_by_blue: newVal }),
                    });
                    obj.discovered_by_blue = newVal;
                    render();
                } catch (err) { console.warn('Toggle Blue discovery failed:', err); }
            } else if (action === 'disc_red') {
                const newVal = !obj.discovered_by_red;
                try {
                    await fetch(`/api/sessions/${sid}/map-objects/${obj.id}`, {
                        method: 'PUT', headers,
                        body: JSON.stringify({ discovered_by_red: newVal }),
                    });
                    obj.discovered_by_red = newVal;
                    render();
                } catch (err) { console.warn('Toggle Red discovery failed:', err); }
            }
        });

        const close = () => { menu.remove(); document.removeEventListener('click', close); };
        setTimeout(() => document.addEventListener('click', close), 50);
    }

    // ═══════════════════════════════════════════════════════════
    // GEOMETRY HELPERS
    // ═══════════════════════════════════════════════════════════

    function _interpolateAlongLine(latlngs, spacingM) {
        const points = [];
        let accumulated = spacingM / 2;
        for (let i = 0; i < latlngs.length - 1; i++) {
            const [lat1, lng1] = latlngs[i];
            const [lat2, lng2] = latlngs[i + 1];
            const mPerLon = METERS_PER_DEG * Math.cos(lat1 * Math.PI / 180);
            const dlat = lat2 - lat1, dlng = lng2 - lng1;
            const segM = Math.sqrt((dlat * METERS_PER_DEG) ** 2 + (dlng * mPerLon) ** 2);
            const bearing = Math.atan2(dlng * mPerLon, dlat * METERS_PER_DEG) * 180 / Math.PI;
            while (accumulated <= segM) {
                const f = accumulated / segM;
                points.push({ lat: lat1 + f * dlat, lng: lng1 + f * dlng, bearing });
                accumulated += spacingM;
            }
            accumulated -= segM;
        }
        return points;
    }

    function _addLineDecorations(group, coords, deco, color) {
        const pts = _interpolateAlongLine(coords, deco.spacing);
        const svgHtml = deco.svg(color);
        for (const pt of pts) {
            group.addLayer(L.marker([pt.lat, pt.lng], {
                icon: L.divIcon({
                    className: 'map-obj-deco',
                    html: svgHtml,
                    iconSize: deco.size,
                    iconAnchor: [deco.size[0] / 2, deco.size[1] / 2],
                }),
                interactive: false,
            }));
        }
    }

    function _computeZigzag(latlngs, amplitudeM, wavelengthM) {
        const result = [];
        for (let i = 0; i < latlngs.length - 1; i++) {
            const [lat1, lng1] = latlngs[i];
            const [lat2, lng2] = latlngs[i + 1];
            const mPerLon = METERS_PER_DEG * Math.cos(lat1 * Math.PI / 180);
            const dlat = lat2 - lat1, dlng = lng2 - lng1;
            const segM = Math.sqrt((dlat * METERS_PER_DEG) ** 2 + (dlng * mPerLon) ** 2);
            const perpLat = -(dlng * mPerLon) / segM * amplitudeM / METERS_PER_DEG;
            const perpLng = (dlat * METERS_PER_DEG) / segM * amplitudeM / mPerLon;
            const nTeeth = Math.max(2, Math.round(segM / wavelengthM));
            for (let j = 0; j <= nTeeth; j++) {
                const f = j / nTeeth;
                const bLat = lat1 + f * dlat;
                const bLng = lng1 + f * dlng;
                if ((j === 0 && i === 0) || (j === nTeeth && i === latlngs.length - 2)) {
                    result.push([bLat, bLng]);
                } else {
                    const sign = (j % 2 === 0) ? 1 : -1;
                    result.push([bLat + sign * perpLat, bLng + sign * perpLng]);
                }
            }
        }
        return result;
    }

    function _offsetPolyline(latlngs, offsetM) {
        const result = [];
        for (let i = 0; i < latlngs.length; i++) {
            const [lat, lng] = latlngs[i];
            const mPerLon = METERS_PER_DEG * Math.cos(lat * Math.PI / 180);
            let bearing;
            if (i < latlngs.length - 1) {
                const [lat2, lng2] = latlngs[i + 1];
                bearing = Math.atan2((lng2 - lng) * mPerLon, (lat2 - lat) * METERS_PER_DEG);
            } else {
                const [lat0, lng0] = latlngs[i - 1];
                bearing = Math.atan2((lng - lng0) * mPerLon, (lat - lat0) * METERS_PER_DEG);
            }
            const perpBearing = bearing + Math.PI / 2;
            result.push([
                lat + offsetM * Math.cos(perpBearing) / METERS_PER_DEG,
                lng + offsetM * Math.sin(perpBearing) / mPerLon,
            ]);
        }
        return result;
    }

    function _addMineMarkers(group, coords, color, text) {
        const centroid = _polygonCentroid(coords);
        if (!centroid) return;
        const svgHtml = MINE_MARKER(color, text);
        group.addLayer(L.marker(centroid, {
            icon: L.divIcon({ className: 'map-obj-deco', html: svgHtml, iconSize: [20, 20], iconAnchor: [10, 10] }),
            interactive: false,
        }));
        if (coords.length >= 4) {
            const bounds = _polygonBounds(coords);
            const dLat = bounds.maxLat - bounds.minLat;
            const dLng = bounds.maxLng - bounds.minLng;
            const mPerLon = METERS_PER_DEG * Math.cos(centroid[0] * Math.PI / 180);
            const areaM2 = (dLat * METERS_PER_DEG) * (dLng * mPerLon);
            if (areaM2 > 10000) {
                for (const pos of [[centroid[0]+dLat*0.25, centroid[1]-dLng*0.2],[centroid[0]-dLat*0.25, centroid[1]+dLng*0.2]]) {
                    group.addLayer(L.marker(pos, {
                        icon: L.divIcon({ className: 'map-obj-deco', html: svgHtml, iconSize: [20, 20], iconAnchor: [10, 10] }),
                        interactive: false,
                    }));
                }
            }
        }
    }

    function _polygonCentroid(coords) {
        if (!coords || coords.length < 3) return null;
        let latSum = 0, lngSum = 0;
        for (const [lat, lng] of coords) { latSum += lat; lngSum += lng; }
        return [latSum / coords.length, lngSum / coords.length];
    }

    function _polygonBounds(coords) {
        let minLat = Infinity, maxLat = -Infinity, minLng = Infinity, maxLng = -Infinity;
        for (const [lat, lng] of coords) {
            if (lat < minLat) minLat = lat; if (lat > maxLat) maxLat = lat;
            if (lng < minLng) minLng = lng; if (lng > maxLng) maxLng = lng;
        }
        return { minLat, maxLat, minLng, maxLng };
    }

    // ═══════════════════════════════════════════════════════════
    // DRAWING / PLACEMENT MODE
    // ═══════════════════════════════════════════════════════════

    function startPlacement(objectType) {
        if (!_definitions && _sessionId) {
            loadDefinitions(_sessionId).then(() => _beginDraw(objectType));
        } else {
            _beginDraw(objectType);
        }
    }

    function _beginDraw(objectType) {
        cancelPlacement();
        const defn = _definitions ? _definitions[objectType] : null;
        const geomType = defn ? defn.geometry_type : 'Point';
        _drawingMode = { objectType, geomType, points: [] };

        _map.getContainer().style.cursor = 'crosshair';
        if (geomType === 'Point') {
            _map.once('click', _onPointClick);
        } else if (geomType === 'LineString') {
            _map.on('click', _onLineClick);
            _map.on('contextmenu', _onLineFinish);
        } else if (geomType === 'Polygon') {
            _map.on('click', _onPolygonClick);
            _map.on('contextmenu', _onPolygonFinish);
        }
    }

    function _onPointClick(e) {
        if (!_drawingMode) return;
        _map.getContainer().style.cursor = '';
        _createObject(_drawingMode.objectType, { type: 'Point', coordinates: [e.latlng.lng, e.latlng.lat] });
        _drawingMode = null;
    }

    function _onLineClick(e) {
        if (!_drawingMode) return;
        _drawingMode.points.push([e.latlng.lat, e.latlng.lng]);
        if (_drawLayer) _map.removeLayer(_drawLayer);
        if (_drawingMode.points.length > 1) {
            const defn = _definitions ? _definitions[_drawingMode.objectType] : {};
            const color = (defn && defn.color) || '#FF9800';
            _drawLayer = L.polyline(_drawingMode.points, { color, weight: 3, dashArray: '5,5', opacity: 0.7 }).addTo(_map);
        }
    }

    function _onLineFinish(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        if (!_drawingMode || _drawingMode.points.length < 2) { cancelPlacement(); return; }
        _map.off('click', _onLineClick);
        _map.off('contextmenu', _onLineFinish);
        _map.getContainer().style.cursor = '';
        const coords = _drawingMode.points.map(p => [p[1], p[0]]);
        _createObject(_drawingMode.objectType, { type: 'LineString', coordinates: coords });
        if (_drawLayer) { _map.removeLayer(_drawLayer); _drawLayer = null; }
        _drawingMode = null;
    }

    function _onPolygonClick(e) {
        if (!_drawingMode) return;
        _drawingMode.points.push([e.latlng.lat, e.latlng.lng]);
        if (_drawLayer) _map.removeLayer(_drawLayer);
        if (_drawingMode.points.length > 2) {
            const defn = _definitions ? _definitions[_drawingMode.objectType] : {};
            const color = (defn && defn.color) || '#FF4444';
            _drawLayer = L.polygon(_drawingMode.points, { color, weight: 2, dashArray: '5,5', fillOpacity: 0.15, opacity: 0.7 }).addTo(_map);
        } else if (_drawingMode.points.length === 2) {
            _drawLayer = L.polyline(_drawingMode.points, { color: '#FF4444', weight: 2, dashArray: '5,5', opacity: 0.7 }).addTo(_map);
        }
    }

    function _onPolygonFinish(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        if (!_drawingMode || _drawingMode.points.length < 3) { cancelPlacement(); return; }
        _map.off('click', _onPolygonClick);
        _map.off('contextmenu', _onPolygonFinish);
        _map.getContainer().style.cursor = '';
        const coords = _drawingMode.points.map(p => [p[1], p[0]]);
        coords.push(coords[0]);
        _createObject(_drawingMode.objectType, { type: 'Polygon', coordinates: [coords] });
        if (_drawLayer) { _map.removeLayer(_drawLayer); _drawLayer = null; }
        _drawingMode = null;
    }

    function cancelPlacement() {
        _map.off('click', _onPointClick);
        _map.off('click', _onLineClick);
        _map.off('contextmenu', _onLineFinish);
        _map.off('click', _onPolygonClick);
        _map.off('contextmenu', _onPolygonFinish);
        _map.getContainer().style.cursor = '';
        if (_drawLayer) { _map.removeLayer(_drawLayer); _drawLayer = null; }
        _drawingMode = null;
    }

    async function _createObject(objectType, geometry) {
        const sid = _sessionId || KSessionUI?.getSessionId();
        const token = KSessionUI?.getToken();
        if (!sid) return;
        const headers = { 'Content-Type': 'application/json' };
        if (token) headers['Authorization'] = `Bearer ${token}`;
        const defaultLabel = objectType.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

        try {
            const resp = await fetch(`/api/sessions/${sid}/map-objects`, {
                method: 'POST', headers,
                body: JSON.stringify({ object_type: objectType, side: 'neutral', geometry, label: defaultLabel }),
            });
            if (resp.ok) {
                const obj = await resp.json();
                _objects.push(obj);
                render();
            } else {
                const err = await resp.json().catch(() => ({}));
                console.warn('Create map object failed:', err.detail || resp.status);
            }
        } catch (e) { console.warn('Create map object error:', e); }
    }

    // ═══════════════════════════════════════════════════════════
    // TOGGLE, WEBSOCKET, GETTERS
    // ═══════════════════════════════════════════════════════════

    function toggle() {
        _visible = !_visible;
        if (_visible) render(); else { _layerGroup.clearLayers(); _objectLayers = {}; }
        return _visible;
    }

    function isVisible() { return _visible; }

    // ── Artillery / combat impact visualization ──────────────
    // Animated explosion effect that fades and disappears.

    let _impactLayer = null;

    function _ensureImpactLayer() {
        if (!_impactLayer && _map) {
            _impactLayer = L.layerGroup().addTo(_map);
        }
        return _impactLayer;
    }

    /**
     * Show an animated impact effect at a given location.
     * @param {number} lat
     * @param {number} lon
     * @param {string} type - 'artillery' | 'combat' | 'smoke_impact'
     * @param {number} durationMs - how long the effect lasts (default 25000ms = 25s)
     */
    function showImpact(lat, lon, type = 'artillery', durationMs = null) {
        const layer = _ensureImpactLayer();
        if (!layer || !_map) return;

        const isArtillery = type === 'artillery';
        // Artillery: 30s splash, regular combat: 15s
        if (durationMs === null) durationMs = isArtillery ? 30000 : 15000;
        const baseRadius = isArtillery ? 100 : 40;
        const color = isArtillery ? '#FF6600' : '#FF4444';
        const coreColor = isArtillery ? '#FFD700' : '#FF8800';

        // Inner flash circle (bright, shrinks)
        const flash = L.circle([lat, lon], {
            radius: baseRadius * 0.4,
            color: coreColor,
            weight: 2,
            opacity: 0.9,
            fillColor: '#FFFFFF',
            fillOpacity: 0.8,
            interactive: false,
            className: 'impact-flash',
        });

        // Main blast circle
        const blast = L.circle([lat, lon], {
            radius: baseRadius,
            color: color,
            weight: 2,
            opacity: 0.8,
            fillColor: color,
            fillOpacity: isArtillery ? 0.45 : 0.35,
            interactive: false,
            className: 'impact-blast',
        });

        // Outer shockwave ring
        const shockwave = L.circle([lat, lon], {
            radius: baseRadius * 1.8,
            color: color,
            weight: 1.5,
            opacity: 0.5,
            fillColor: 'transparent',
            fillOpacity: 0,
            dashArray: '6,4',
            interactive: false,
            className: 'impact-shockwave',
        });

        // Debris/smoke cloud (lingers for the full duration)
        const smoke = L.circle([lat, lon], {
            radius: isArtillery ? baseRadius * 1.5 : baseRadius * 1.2,
            color: 'transparent',
            weight: 0,
            fillColor: isArtillery ? '#443322' : '#555',
            fillOpacity: isArtillery ? 0.35 : 0.25,
            interactive: false,
            className: 'impact-smoke',
        });

        layer.addLayer(flash);
        layer.addLayer(blast);
        layer.addLayer(shockwave);
        layer.addLayer(smoke);

        // Phase 1 (0-2s): bright flash fades
        setTimeout(() => {
            try { layer.removeLayer(flash); } catch(e) {}
        }, 2000);

        // Phase 2: blast circle fades (artillery lingers longer)
        const blastFadeStart = isArtillery ? 5000 : 3000;
        const blastRemove = isArtillery ? 12000 : 8000;
        setTimeout(() => {
            try {
                blast.setStyle({ opacity: 0.3, fillOpacity: isArtillery ? 0.2 : 0.12 });
                shockwave.setStyle({ opacity: 0.15 });
            } catch(e) {}
        }, blastFadeStart);

        setTimeout(() => {
            try { layer.removeLayer(blast); } catch(e) {}
            try { layer.removeLayer(shockwave); } catch(e) {}
        }, blastRemove);

        // Phase 3: smoke/debris lingers then fades
        setTimeout(() => {
            try { smoke.setStyle({ fillOpacity: isArtillery ? 0.15 : 0.1 }); } catch(e) {}
        }, durationMs * 0.6);

        // Final cleanup
        setTimeout(() => {
            try { layer.removeLayer(smoke); } catch(e) {}
        }, durationMs);
    }

    /**
     * Show impact effects for combat events received via tick update.
     * @param {Array} events - tick event list from state_update
     */
    function processTickEvents(events) {
        if (!events || !Array.isArray(events)) return;
        for (const evt of events) {
            const payload = evt.payload || {};
            if (evt.event_type === 'combat' || evt.event_type === 'unit_destroyed' || evt.event_type === 'artillery_support') {
                // Show impact at target location
                const lat = payload.target_lat || payload.lat;
                const lon = payload.target_lon || payload.lon;
                if (lat && lon) {
                    const isArty = evt.event_type === 'unit_destroyed' || evt.event_type === 'artillery_support' || payload.is_artillery;
                    showImpact(lat, lon, isArty ? 'artillery' : 'combat');
                }
            }
        }
    }

    function onObjectCreated(data) {
        if (data && data.id) {
            const idx = _objects.findIndex(o => o.id === data.id);
            if (idx >= 0) {
                // Object already exists — update it (e.g. discovery flags changed)
                _objects[idx] = data;
            } else {
                _objects.push(data);
            }
            render();
        }
    }
    function onObjectUpdated(data) {
        if (data && data.id) {
            const idx = _objects.findIndex(o => o.id === data.id);
            if (idx >= 0) {
                _objects[idx] = data;
            } else {
                _objects.push(data);
            }
            render();
        }
    }
    function onObjectDeleted(data) {
        if (data && (data.id || data.object_id)) {
            const id = data.id || data.object_id;
            _objects = _objects.filter(o => o.id !== id);
            render();
        }
    }

    function getObjects() { return _objects; }
    function getDefinitions() { return _definitions; }
    function clearAll() { _objects = []; _layerGroup.clearLayers(); _objectLayers = {}; }

    return {
        init, setSession, loadDefinitions, load, render, toggle, isVisible,
        startPlacement, cancelPlacement,
        onObjectCreated, onObjectUpdated, onObjectDeleted,
        getObjects, getDefinitions, clearAll,
        showImpact, processTickEvents,
        disableAdminMode,
    };
})();

