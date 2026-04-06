/**
 * terrain.js – Terrain overlay rendering, legend, elevation heatmap, admin painting.
 *
 * KTerrain module:
 *   - Fetches terrain GeoJSON from backend
 *   - Renders semi-transparent colored polygons by terrain type
 *   - Toggle show/hide
 *   - Hover tooltip with terrain type + modifiers + elevation
 *   - Elevation heatmap overlay mode
 *   - Terrain legend
 *   - Admin painting mode (click cells to set terrain type)
 */
const KTerrain = (() => {
    let map = null;
    let sessionId = null;
    let terrainLayer = null;
    let elevationLayer = null;
    let legendControl = null;
    let _visible = false;
    let _elevVisible = false;
    let _terrainData = null;
    let _elevationData = null;

    // Admin painting state
    let _paintMode = false;
    let _paintType = 'forest';

    // Terrain color map (must match backend TERRAIN_COLORS)
    const TERRAIN_COLORS = {
        road:     '#666666',
        open:     '#90EE90',
        forest:   '#228B22',
        urban:    '#A0A0A0',
        water:    '#4488FF',
        fields:   '#DAA520',
        marsh:    '#8B8B00',
        desert:   '#DEB887',
        scrub:    '#9ACD32',
        bridge:   '#888888',
        mountain: '#8B7355',
        orchard:  '#556B2F',
    };

    const TERRAIN_LABELS = {
        road: '🛣 Road', open: '🌾 Open', forest: '🌲 Forest',
        urban: '🏘 Urban', water: '💧 Water', fields: '🌽 Fields',
        marsh: '🌿 Marsh', desert: '🏜 Desert', scrub: '🌿 Scrub',
        bridge: '🌉 Bridge', mountain: '⛰ Mountain', orchard: '🌳 Orchard',
    };

    function init(leafletMap) {
        map = leafletMap;
        terrainLayer = L.layerGroup();
        elevationLayer = L.layerGroup();
    }

    function setSession(sid) {
        sessionId = sid;
    }

    // ── Load terrain data from backend ──────────────────

    async function load(sid, token) {
        sessionId = sid || sessionId;
        if (!sessionId) return;

        try {
            const resp = await fetch(`/api/sessions/${sessionId}/terrain`, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (!resp.ok) return;
            _terrainData = await resp.json();
            _renderTerrain();
        } catch (err) {
            console.warn('Terrain load error:', err);
        }
    }

    async function loadElevation(token) {
        if (!sessionId) return;
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/elevation`, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (!resp.ok) return;
            _elevationData = await resp.json();
            _renderElevation();
        } catch (err) {
            console.warn('Elevation load error:', err);
        }
    }

    // ── Render terrain polygons ─────────────────────────

    function _renderTerrain() {
        if (!_terrainData || !_terrainData.features) return;
        terrainLayer.clearLayers();

        L.geoJSON(_terrainData, {
            style: (feature) => {
                const props = feature.properties || {};
                return {
                    fillColor: props.color || TERRAIN_COLORS[props.terrain_type] || '#90EE90',
                    fillOpacity: 0.35,
                    color: props.color || TERRAIN_COLORS[props.terrain_type] || '#90EE90',
                    weight: 0.5,
                    opacity: 0.6,
                };
            },
            onEachFeature: (feature, layer) => {
                const props = feature.properties || {};
                const mods = props.modifiers || {};

                let tip = `<b>${TERRAIN_LABELS[props.terrain_type] || props.terrain_type}</b>`;
                tip += `<br>Source: ${props.source || '?'}`;
                if (props.elevation_m != null) tip += `<br>Elevation: ${Math.round(props.elevation_m)}m`;
                if (props.slope_deg != null) tip += `<br>Slope: ${props.slope_deg.toFixed(1)}°`;
                tip += `<br>Move: ${mods.movement ?? '?'} | Vis: ${mods.visibility ?? '?'}`;
                tip += `<br>Prot: ${mods.protection ?? '?'} | Atk: ${mods.attack ?? '?'}`;
                tip += `<br><span style="opacity:0.6">${props.snail_path}</span>`;

                layer.bindTooltip(tip, { sticky: true, className: 'terrain-tooltip' });

                // Admin painting: click to paint
                layer.on('click', (e) => {
                    if (_paintMode && props.snail_path) {
                        _paintCell(props.snail_path);
                        L.DomEvent.stopPropagation(e);
                    }
                });
            },
        }).addTo(terrainLayer);
    }

    // ── Render elevation heatmap ────────────────────────

    function _renderElevation() {
        if (!_elevationData || !_elevationData.features) return;
        elevationLayer.clearLayers();

        L.geoJSON(_elevationData, {
            style: (feature) => {
                const props = feature.properties || {};
                return {
                    fillColor: props.color || '#8B7355',
                    fillOpacity: 0.4,
                    color: props.color || '#8B7355',
                    weight: 0.3,
                    opacity: 0.5,
                };
            },
            onEachFeature: (feature, layer) => {
                const props = feature.properties || {};
                let tip = `<b>Elevation: ${Math.round(props.elevation_m || 0)}m</b>`;
                if (props.slope_deg != null) tip += `<br>Slope: ${props.slope_deg.toFixed(1)}°`;
                if (props.aspect_deg != null) tip += `<br>Aspect: ${props.aspect_deg.toFixed(0)}°`;
                tip += `<br><span style="opacity:0.6">${props.snail_path}</span>`;
                layer.bindTooltip(tip, { sticky: true, className: 'terrain-tooltip' });
            },
        }).addTo(elevationLayer);
    }

    // ── Toggle visibility ───────────────────────────────

    function toggle() {
        _visible = !_visible;
        if (_visible) {
            if (!map.hasLayer(terrainLayer)) terrainLayer.addTo(map);
        } else {
            if (map.hasLayer(terrainLayer)) map.removeLayer(terrainLayer);
        }
        return _visible;
    }

    function toggleElevation() {
        _elevVisible = !_elevVisible;
        if (_elevVisible) {
            if (!map.hasLayer(elevationLayer)) elevationLayer.addTo(map);
        } else {
            if (map.hasLayer(elevationLayer)) map.removeLayer(elevationLayer);
        }
        return _elevVisible;
    }

    function show() {
        _visible = true;
        if (!map.hasLayer(terrainLayer)) terrainLayer.addTo(map);
    }

    function hide() {
        _visible = false;
        if (map.hasLayer(terrainLayer)) map.removeLayer(terrainLayer);
    }

    function isVisible() { return _visible; }

    // ── Legend ───────────────────────────────────────────

    function showLegend() {
        if (legendControl) return;
        const LegendControl = L.Control.extend({
            options: { position: 'bottomleft' },
            onAdd: function () {
                const div = L.DomUtil.create('div', 'terrain-legend');
                div.innerHTML = '<b>Terrain</b><br>';
                for (const [type, color] of Object.entries(TERRAIN_COLORS)) {
                    const label = TERRAIN_LABELS[type] || type;
                    div.innerHTML += `<span class="terrain-legend-item">` +
                        `<span class="terrain-legend-swatch" style="background:${color}"></span>` +
                        `${label}</span><br>`;
                }
                L.DomEvent.disableClickPropagation(div);
                return div;
            },
        });
        legendControl = new LegendControl();
        legendControl.addTo(map);
    }

    function hideLegend() {
        if (legendControl) {
            map.removeControl(legendControl);
            legendControl = null;
        }
    }

    function toggleLegend() {
        if (legendControl) hideLegend();
        else showLegend();
    }

    // ── Admin painting ──────────────────────────────────

    function startPaintMode(terrainType) {
        _paintMode = true;
        _paintType = terrainType || 'forest';
        if (map) map.getContainer().style.cursor = 'crosshair';
    }

    function stopPaintMode() {
        _paintMode = false;
        if (map) map.getContainer().style.cursor = '';
    }

    function setPaintType(terrainType) {
        _paintType = terrainType;
    }

    function isPaintMode() { return _paintMode; }

    async function _paintCell(snailPath) {
        if (!sessionId || !_paintType) return;
        const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;
        try {
            const resp = await fetch(
                `/api/sessions/${sessionId}/terrain/${encodeURIComponent(snailPath)}?terrain_type=${_paintType}`,
                {
                    method: 'PATCH',
                    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
                }
            );
            if (resp.ok) {
                // Reload terrain to show change
                await load(sessionId, token);
            }
        } catch (err) {
            console.warn('Paint cell error:', err);
        }
    }

    // ── Analyze (trigger backend analysis) ──────────────

    async function analyze(depth = 1, force = false, skipElevation = false) {
        if (!sessionId) return null;
        const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/terrain/analyze`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
                },
                body: JSON.stringify({ depth, force, skip_elevation: skipElevation }),
            });
            if (resp.ok) {
                const result = await resp.json();
                // Auto-load terrain after analysis
                await load(sessionId, token);
                if (!skipElevation) await loadElevation(token);
                return result;
            } else {
                const err = await resp.json().catch(() => ({}));
                console.error('Analyze failed:', err);
                return null;
            }
        } catch (err) {
            console.error('Analyze error:', err);
            return null;
        }
    }

    /**
     * SSE-based terrain analysis with real-time progress reporting.
     * @param {number} depth - snail depth (1-4)
     * @param {boolean} force - overwrite existing cells
     * @param {boolean} skipElevation - skip elevation API
     * @param {function} onProgress - callback({step, message, progress, summary?})
     * @returns {Promise<object|null>} final summary or null on error
     */
    async function analyzeWithProgress(depth = 2, force = false, skipElevation = false, onProgress = null) {
        if (!sessionId) return null;
        const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;

        const params = new URLSearchParams({
            depth: String(depth),
            force: String(force),
            skip_elevation: String(skipElevation),
        });
        const url = `/api/sessions/${sessionId}/terrain/analyze-stream?${params}`;

        try {
            const resp = await fetch(url, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                console.error('Analyze stream failed:', err);
                if (onProgress) onProgress({ step: 'error', message: err.detail || 'Request failed', progress: -1 });
                return null;
            }

            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let result = null;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    const trimmed = line.trim();
                    if (trimmed.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(trimmed.substring(6));
                            if (onProgress) onProgress(data);
                            if (data.step === 'complete' && data.summary) {
                                result = data.summary;
                            }
                        } catch (parseErr) {
                            // ignore malformed events
                        }
                    }
                }
            }

            // Auto-load terrain after analysis
            if (result) {
                await load(sessionId, token);
                if (!skipElevation) await loadElevation(token);
            }

            return result;
        } catch (err) {
            console.error('Analyze stream error:', err);
            if (onProgress) onProgress({ step: 'error', message: err.message, progress: -1 });
            return null;
        }
    }

    /**
     * Estimate number of cells for a given depth (calls backend).
     */
    async function estimateCellCount(depth) {
        if (!sessionId) return null;
        const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/terrain/cell-count?depth=${depth}`, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (resp.ok) return await resp.json();
        } catch (err) {}
        return null;
    }

    // ── Clear terrain ───────────────────────────────────

    async function clearTerrain(keepManual = true) {
        if (!sessionId) return;
        const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;
        try {
            await fetch(`/api/sessions/${sessionId}/terrain?keep_manual=${keepManual}`, {
                method: 'DELETE',
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            terrainLayer.clearLayers();
            elevationLayer.clearLayers();
            _terrainData = null;
            _elevationData = null;
        } catch (err) {
            console.warn('Clear terrain error:', err);
        }
    }

    // ── Stats ───────────────────────────────────────────

    async function getStats() {
        if (!sessionId) return null;
        const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/terrain/stats`, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (resp.ok) return await resp.json();
        } catch (err) {}
        return null;
    }

    function getTerrainColors() { return TERRAIN_COLORS; }
    function getTerrainLabels() { return TERRAIN_LABELS; }

    return {
        init, setSession, load, loadElevation,
        toggle, toggleElevation, show, hide, isVisible,
        showLegend, hideLegend, toggleLegend,
        startPaintMode, stopPaintMode, setPaintType, isPaintMode,
        analyze, analyzeWithProgress, estimateCellCount,
        clearTerrain, getStats,
        getTerrainColors, getTerrainLabels,
    };
})();

