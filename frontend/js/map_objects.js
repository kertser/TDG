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
            `<svg viewBox="0 0 32 24" width="32" height="24">` +
            `<path d="M2,18 Q16,4 30,18" fill="none" stroke="${c}" stroke-width="3" stroke-linecap="round"/>` +
            `<line x1="2" y1="18" x2="30" y2="18" stroke="${c}" stroke-width="2.5"/>` +
            `<line x1="9" y1="18" x2="9" y2="13" stroke="${c}" stroke-width="2"/>` +
            `<line x1="16" y1="18" x2="16" y2="8" stroke="${c}" stroke-width="2"/>` +
            `<line x1="23" y1="18" x2="23" y2="13" stroke="${c}" stroke-width="2"/>` +
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
    };

    // ═══════════════════════════════════════════════════════════
    // INITIALIZATION & DATA LOADING
    // ═══════════════════════════════════════════════════════════

    function init(map) {
        _map = map;
        _layerGroup = L.layerGroup().addTo(map);
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
            }
        } catch (e) { console.warn('Failed to load map objects:', e); }
    }

    // ═══════════════════════════════════════════════════════════
    // RENDERING
    // ═══════════════════════════════════════════════════════════

    function render() {
        _layerGroup.clearLayers();
        _objectLayers = {};
        if (!_visible) return;
        for (const obj of _objects) {
            const layer = _createLayer(obj);
            if (layer) {
                layer._mapObjId = obj.id;
                layer.addTo(_layerGroup);
                _objectLayers[obj.id] = layer;
            }
        }
    }

    function _isAdminOpen() {
        try { return KAdmin && KAdmin.isUnlocked(); } catch(e) { return false; }
    }

    function _createLayer(obj) {
        if (!obj.geometry) return null;
        const color = (obj.definition && obj.definition.color) || DEFAULT_COLORS[obj.object_type] || '#888';
        const gtype = obj.geometry.type;
        const inactive = !obj.is_active;
        const opacity = inactive ? 0.35 : 0.85;

        if (gtype === 'Point') return _createPointLayer(obj, color, inactive, opacity);
        if (gtype === 'LineString' || gtype === 'MultiLineString') return _createLineLayer(obj, color, inactive, opacity);
        if (gtype === 'Polygon' || gtype === 'MultiPolygon') return _createPolygonLayer(obj, color, inactive, opacity);
        return null;
    }

    // ── Point structures ──────────────────────────────────────

    function _createPointLayer(obj, color, inactive, opacity) {
        // Airfield uses geographic overlay (scales with map zoom)
        if (obj.object_type === 'airfield') {
            return _createAirfieldLayer(obj, color, inactive, opacity);
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

    // ── Airfield (geographic overlay — real-world scale) ─────

    function _createAirfieldLayer(obj, color, inactive, opacity) {
        const coords = obj.geometry.coordinates;
        const centerLat = coords[1];
        const centerLon = coords[0];
        const label = obj.label || 'Airfield';
        const adminOpen = _isAdminOpen();

        // Real-world dimensions in meters (runway ~700m + apron)
        const lengthM = 700;
        const widthM = 280;   // ratio 2.5:1 matching SVG viewBox 140:56

        // Convert meters to geographic offset
        const mPerLon = METERS_PER_DEG * Math.cos(centerLat * Math.PI / 180);
        const dLat = (widthM / 2) / METERS_PER_DEG;
        const dLon = (lengthM / 2) / mPerLon;

        const bounds = L.latLngBounds(
            [centerLat - dLat, centerLon - dLon],   // SW
            [centerLat + dLat, centerLon + dLon]     // NE
        );

        // SVG as data URI for imageOverlay (scales with the map)
        const svgString = STRUCTURE_SVGS.airfield(color);
        const dataUri = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgString);

        const overlay = L.imageOverlay(dataUri, bounds, {
            opacity: opacity,
            interactive: true,
        });

        const group = L.featureGroup([overlay]);

        // Tooltip and context menu on the image overlay
        _bindTooltipAndContext(overlay, obj);

        // Admin: draggable center handle (small visible grab point)
        if (adminOpen) {
            const dragMarker = L.marker([centerLat, centerLon], {
                icon: L.divIcon({
                    className: 'map-obj-icon',
                    html: '<div style="width:22px;height:22px;border:2px dashed rgba(79,195,247,0.6);border-radius:50%;cursor:grab;background:rgba(79,195,247,0.1);"></div>',
                    iconSize: [22, 22],
                    iconAnchor: [11, 11],
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
                } catch (err) { console.warn('Move map object failed:', err); }
            });

            group.addLayer(dragMarker);
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
        return group;
    }

    // ═══════════════════════════════════════════════════════════
    // TOOLTIP & CONTEXT MENU
    // ═══════════════════════════════════════════════════════════

    function _bindTooltipAndContext(layer, obj) {
        const label = obj.label || obj.object_type.replace(/_/g, ' ');
        const status = obj.is_active ? '✓ Active' : '✗ Inactive';
        const prot = obj.definition ? obj.definition.protection_bonus : 1.0;
        const tooltipHtml = `<b>${label}</b><br><span style="font-size:10px;">${obj.object_type} · ${status}${prot > 1 ? ` · Prot ×${prot}` : ''}</span>`;
        layer.bindTooltip(tooltipHtml, { sticky: true, className: 'map-obj-tooltip' });
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
        menu.style.cssText = `display:block;position:fixed;left:${e.originalEvent.clientX}px;top:${e.originalEvent.clientY}px;z-index:10000;min-width:160px;`;

        const label = obj.label || obj.object_type.replace(/_/g, ' ');
        menu.innerHTML = `
            <div class="ctx-menu-header" style="font-size:11px;padding:4px 8px;color:#4fc3f7;">${label}</div>
            <div class="ctx-menu-section">
                <div class="ctx-item" data-action="toggle">${obj.is_active ? '🔴 Deactivate' : '🟢 Activate'}</div>
                <div class="ctx-item ctx-item-danger" data-action="delete">🗑 Delete</div>
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

    function onObjectCreated(data) {
        if (data && data.id) {
            // Prevent duplicates: check if already exists (e.g. local POST already added it)
            const exists = _objects.some(o => o.id === data.id);
            if (!exists) {
                _objects.push(data);
                render();
            }
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
    };
})();

