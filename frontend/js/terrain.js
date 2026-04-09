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
    let _peaksVisible = false;
    let _terrainData = null;
    let _elevationData = null;
    let _peaksData = null;

    // Admin painting state
    let _paintMode = false;
    let _paintType = 'forest';

    // Spatial index for fast point → terrain lookup (from cached data)
    let _cellIndex = null;  // { cellSizeLat, cellSizeLon, grid: Map<"row,col" → cell> }

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

        // Create custom panes so we can set layer-level opacity
        // This avoids overlap-stacking: cells are opaque within the pane,
        // and the pane itself is translucent as a whole.
        if (!map.getPane('terrainPane')) {
            map.createPane('terrainPane');
            map.getPane('terrainPane').style.zIndex = 250;
            map.getPane('terrainPane').style.opacity = '0.25';
        }
        if (!map.getPane('elevationPane')) {
            map.createPane('elevationPane');
            map.getPane('elevationPane').style.zIndex = 249;
            map.getPane('elevationPane').style.opacity = '0.35';
        }

        terrainLayer = L.layerGroup({ pane: 'terrainPane' });
        elevationLayer = L.layerGroup({ pane: 'elevationPane' });

        // Peaks layer uses the overlay pane (above terrain)
        if (!map.getPane('peaksPane')) {
            map.createPane('peaksPane');
            map.getPane('peaksPane').style.zIndex = 450;
            map.getPane('peaksPane').style.pointerEvents = 'none';
        }
    }

    function setSession(sid) {
        sessionId = sid;
    }

    // ── Load terrain data from backend ──────────────────

    async function load(sid, token) {
        sessionId = sid || sessionId;
        if (!sessionId) return;

        try {
            const resp = await fetch(`/api/sessions/${sessionId}/terrain/compact`, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (!resp.ok) return;
            _terrainData = await resp.json();
            _buildSpatialIndex();
            _renderTerrain();
            // Auto-load peaks (derived from elevation data) if not yet loaded
            if (!_peaksData) loadPeaks(token);
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
            // Auto-load peaks when elevation data is available
            loadPeaks(token);
        } catch (err) {
            console.warn('Elevation load error:', err);
        }
    }

    // ── Render terrain polygons ─────────────────────────

    /**
     * Build a grid-based spatial index from loaded compact terrain data.
     * Maps each cell to a grid bucket by (row, col) for O(1) point lookup.
     */
    function _buildSpatialIndex() {
        _cellIndex = null;
        if (!_terrainData || !_terrainData.cells || !_terrainData.cells.length) return;

        const cells = _terrainData.cells;
        const cellSizeLat = _terrainData.cell_size_lat || 0.0003;
        const cellSizeLon = _terrainData.cell_size_lon || 0.0003;

        // Find bounding box origin (min lat/lon)
        let minLat = Infinity, minLon = Infinity;
        for (let i = 0; i < cells.length; i++) {
            if (cells[i].la < minLat) minLat = cells[i].la;
            if (cells[i].lo < minLon) minLon = cells[i].lo;
        }

        // Build grid map: "row,col" → cell
        const grid = new Map();
        for (let i = 0; i < cells.length; i++) {
            const c = cells[i];
            const row = Math.round((c.la - minLat) / cellSizeLat);
            const col = Math.round((c.lo - minLon) / cellSizeLon);
            grid.set(`${row},${col}`, c);
        }

        _cellIndex = { cellSizeLat, cellSizeLon, minLat, minLon, grid };
    }

    /**
     * Fast client-side terrain lookup at a geographic point.
     * Uses cached compact data + spatial index — no API call.
     * @param {number} lat
     * @param {number} lon
     * @returns {{type: string, label: string, elevation: number|null, slope: number|null, source: string}|null}
     */
    function getTerrainAtPoint(lat, lon) {
        if (!_cellIndex) return null;

        const { cellSizeLat, cellSizeLon, minLat, minLon, grid } = _cellIndex;
        const row = Math.round((lat - minLat) / cellSizeLat);
        const col = Math.round((lon - minLon) / cellSizeLon);
        const cell = grid.get(`${row},${col}`);
        if (!cell) return null;

        return {
            type:      cell.t,
            label:     TERRAIN_LABELS[cell.t] || cell.t,
            elevation: cell.e != null ? cell.e : null,
            slope:     cell.sl != null ? cell.sl : null,
            source:    cell.sr || '?',
            snailPath: cell.s,
        };
    }

    function _renderTerrain() {
        if (!_terrainData) return;
        terrainLayer.clearLayers();

        // Compact format: {cells: [{s, t, la, lo, e, sl, sr, c}, ...], cell_size_lat, cell_size_lon, colors}
        const cells = _terrainData.cells;
        if (!cells || !cells.length) return;

        const halfLat = (_terrainData.cell_size_lat || 0.0003) / 2;
        const halfLon = (_terrainData.cell_size_lon || 0.0003) / 2;
        const colors = _terrainData.colors || TERRAIN_COLORS;
        const modifiers = _terrainData.modifiers || {};

        // Use Canvas renderer in custom pane for performance (critical for 65k+ cells)
        const canvasRenderer = L.canvas({ padding: 0.1, pane: 'terrainPane' });

        for (let i = 0; i < cells.length; i++) {
            const c = cells[i];
            const color = colors[c.t] || TERRAIN_COLORS[c.t] || '#90EE90';
            const bounds = [
                [c.la - halfLat, c.lo - halfLon],
                [c.la + halfLat, c.lo + halfLon],
            ];

            const rect = L.rectangle(bounds, {
                fillColor: color,
                fillOpacity: 0.7,
                stroke: false,
                renderer: canvasRenderer,
                pane: 'terrainPane',
            });

            // Tooltip on hover (built lazily)
            rect.on('mouseover', function () {
                if (!this.getTooltip()) {
                    const mods = modifiers[c.t] || {};
                    let tip = `<b>${TERRAIN_LABELS[c.t] || c.t}</b>`;
                    tip += `<br>Source: ${c.sr || '?'}`;
                    if (c.e != null) tip += `<br>Elevation: ${Math.round(c.e)}m`;
                    if (c.sl != null) tip += `<br>Slope: ${c.sl}°`;
                    tip += `<br>Move: ${mods.movement ?? '?'} | Vis: ${mods.visibility ?? '?'}`;
                    tip += `<br>Prot: ${mods.protection ?? '?'} | Atk: ${mods.attack ?? '?'}`;
                    tip += `<br><span style="opacity:0.6">${c.s}</span>`;
                    this.bindTooltip(tip, { sticky: true, className: 'terrain-tooltip' });
                    this.openTooltip();
                }
            });

            // Admin painting: click to paint
            if (_paintMode) {
                rect.on('click', (e) => {
                    if (_paintMode && c.s) {
                        _paintCell(c.s);
                        L.DomEvent.stopPropagation(e);
                    }
                });
            }

            rect.addTo(terrainLayer);
        }
    }

    // ── Render elevation heatmap ────────────────────────

    function _renderElevation() {
        if (!_elevationData || !_elevationData.features) return;
        elevationLayer.clearLayers();

        L.geoJSON(_elevationData, {
            pane: 'elevationPane',
            style: (feature) => {
                const props = feature.properties || {};
                return {
                    fillColor: props.color || '#8B7355',
                    fillOpacity: 0.7,
                    color: props.color || '#8B7355',
                    weight: 0.3,
                    opacity: 0.5,
                    pane: 'elevationPane',
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

    // Peaks layer group (created lazily)
    let _peaksLayerGroup = null;

    function _ensurePeaksLayer() {
        if (!_peaksLayerGroup && map) {
            _peaksLayerGroup = L.layerGroup();
        }
        return _peaksLayerGroup;
    }

    // ── Load and render elevation peaks (height tops) ───

    async function loadPeaks(token) {
        if (!sessionId) return;
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/elevation/peaks`, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (!resp.ok) return;
            _peaksData = await resp.json();
            _renderPeaks();
            // Auto-show peaks when data is available
            if (_peaksData && _peaksData.peaks && _peaksData.peaks.length > 0) {
                const layer = _ensurePeaksLayer();
                if (layer && map && !map.hasLayer(layer)) {
                    layer.addTo(map);
                    _peaksVisible = true;
                }
            }
        } catch (err) {
            console.warn('Peaks load error:', err);
        }
    }

    function _renderPeaks() {
        const layer = _ensurePeaksLayer();
        if (!layer) return;
        layer.clearLayers();

        if (!_peaksData || !_peaksData.peaks || !_peaksData.peaks.length) return;

        for (const peak of _peaksData.peaks) {
            const elev = Math.round(peak.elevation_m);
            // Small fixed-size triangle marker + elevation number (does not scale with zoom)
            const labelHtml = `<div class="height-top-marker" style="pointer-events:auto;">` +
                `<svg width="8" height="8" viewBox="0 0 14 14" style="display:block;margin:0 auto;">` +
                `<polygon points="7,1 1,13 13,13" fill="#8B4513" stroke="#5D3A1A" stroke-width="1.2" opacity="0.9"/>` +
                `</svg>` +
                `<div class="height-top-label">${elev}</div>` +
                `</div>`;

            const icon = L.divIcon({
                className: 'height-top-icon',
                html: labelHtml,
                iconSize: [26, 18],
                iconAnchor: [13, 16],
            });

            const marker = L.marker([peak.lat, peak.lon], {
                icon: icon,
                interactive: true,
                pane: 'peaksPane',
            });

            marker.bindTooltip(
                `${elev} m`,
                { sticky: false, className: 'terrain-tooltip' }
            );

            marker.addTo(layer);
        }
    }

    function togglePeaks() {
        _peaksVisible = !_peaksVisible;
        const layer = _ensurePeaksLayer();
        if (!layer) return _peaksVisible;
        if (_peaksVisible) {
            if (!map.hasLayer(layer)) layer.addTo(map);
            // Auto-load if not yet loaded
            if (!_peaksData) {
                const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;
                loadPeaks(token);
            }
        } else {
            if (map.hasLayer(layer)) map.removeLayer(layer);
        }
        return _peaksVisible;
    }

    function showPeaks() {
        _peaksVisible = true;
        const layer = _ensurePeaksLayer();
        if (layer && !map.hasLayer(layer)) layer.addTo(map);
        if (!_peaksData) {
            const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;
            loadPeaks(token);
        }
    }

    function hidePeaks() {
        _peaksVisible = false;
        const layer = _ensurePeaksLayer();
        if (layer && map.hasLayer(layer)) map.removeLayer(layer);
    }

    function isPeaksVisible() { return _peaksVisible; }

    function getPeaks() { return _peaksData ? _peaksData.peaks : []; }

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
            _cellIndex = null;
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
        init, setSession, load, loadElevation, loadPeaks,
        toggle, toggleElevation, togglePeaks, show, hide, isVisible,
        showPeaks, hidePeaks, isPeaksVisible, getPeaks,
        showLegend, hideLegend, toggleLegend,
        startPaintMode, stopPaintMode, setPaintType, isPaintMode,
        analyze, analyzeWithProgress, estimateCellCount,
        clearTerrain, getStats,
        getTerrainColors, getTerrainLabels,
        getTerrainAtPoint,
    };
})();

