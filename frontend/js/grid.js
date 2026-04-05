/**
 * grid.js – Tactical grid overlay with three depth levels and axis labels.
 *
 * Depth 0: Major grid squares (~1 km) – solid lines
 * Depth 1: Sub-grid (1/3 ≈ 333 m)   – thin dashed lines
 * Depth 2: Sub-sub-grid (1/9 ≈ 111 m) – semi-transparent dashed lines
 *
 * Sub-grid lines are computed client-side by interpolating the depth-0
 * polygon corners — no extra API calls needed.
 */
const KGrid = (() => {
    let gridLayer = null;        // depth-0 major grid polygons
    let subGridLayer = null;     // depth-1 lines (1/3)
    let subSubGridLayer = null;  // depth-2 lines (1/9)
    let labelLayer = null;
    let sessionId = null;
    let gridGeoJson = null;
    let _map = null;
    let _zoomHandler = null;

    async function load(map, sessId) {
        sessionId = sessId;
        _map = map;

        _removeLayers(map);
        if (_zoomHandler) { map.off('zoomend', _zoomHandler); _zoomHandler = null; }

        try {
            const resp = await fetch(`/api/sessions/${sessionId}/grid?depth=0`);
            if (!resp.ok) return;
            gridGeoJson = await resp.json();

            _renderAll(map);
            _zoomHandler = () => _renderAll(map);
            map.on('zoomend', _zoomHandler);
        } catch (err) {
            console.warn('Grid load failed:', err);
        }
    }

    function _removeLayers(map) {
        if (gridLayer)       { map.removeLayer(gridLayer);       gridLayer = null; }
        if (subGridLayer)    { map.removeLayer(subGridLayer);    subGridLayer = null; }
        if (subSubGridLayer) { map.removeLayer(subSubGridLayer); subSubGridLayer = null; }
        if (labelLayer)      { map.removeLayer(labelLayer);      labelLayer = null; }
    }

    function _renderAll(map) {
        if (!gridGeoJson || !gridGeoJson.features || gridGeoJson.features.length === 0) return;
        _removeLayers(map);

        const zoom = map.getZoom();

        // ── Depth 0: Major grid – solid lines ──────────
        _renderMajorGrid(map, zoom);

        // ── Depth 1: Sub-grid at 1/3 – dashed ─────────
        _renderSubGrid(map, zoom);

        // ── Depth 2: Sub-sub-grid at 1/9 – faint dashed (zoom ≥ 12) ──
        if (zoom >= 12) {
            _renderSubSubGrid(map, zoom);
        }

        // ── Axis labels ────────────────────────────────
        _renderLabels(map, zoom);
    }

    // ── Major grid (depth 0) ──────────────────────────
    function _renderMajorGrid(map, zoom) {
        let color, weight;
        if (zoom >= 15)      { color = 'rgba(0, 0, 0, 0.7)';  weight = 2.5; }
        else if (zoom >= 13) { color = 'rgba(0, 0, 0, 0.55)'; weight = 2; }
        else                 { color = 'rgba(0, 0, 0, 0.4)';  weight = 1.5; }

        gridLayer = L.geoJSON(gridGeoJson, {
            style: () => ({
                color: color,
                weight: weight,
                fillColor: 'transparent',
                fillOpacity: 0,
            }),
            interactive: false,
        }).addTo(map);
    }

    // ── Interpolation helper ──────────────────────────
    function _lerp(p1, p2, t) {
        return [p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t];
    }

    /**
     * Compute internal grid lines within each square polygon.
     * @param {Array}  features   – depth-0 GeoJSON features
     * @param {number} divisions  – 3 for depth-1, 9 for depth-2
     * @param {number|null} skipEvery – skip lines at multiples (e.g. 3 for depth-2
     *        to avoid re-drawing depth-1 lines)
     * @returns {Array} multi-polyline coords: [[[lat,lng],[lat,lng]], …]
     */
    function _computeInternalLines(features, divisions, skipEvery) {
        const lines = [];

        features.forEach(f => {
            if (!f.geometry || f.geometry.type !== 'Polygon') return;
            const coords = f.geometry.coordinates[0];
            if (coords.length < 5) return;

            // corners: [SW, SE, NE, NW, SW] in [lon, lat]
            const sw = coords[0], se = coords[1], ne = coords[2], nw = coords[3];

            for (let i = 1; i < divisions; i++) {
                if (skipEvery && i % skipEvery === 0) continue;
                const t = i / divisions;

                // Horizontal line (interpolate along left & right edges)
                const hL = _lerp(sw, nw, t);
                const hR = _lerp(se, ne, t);
                lines.push([[hL[1], hL[0]], [hR[1], hR[0]]]);

                // Vertical line (interpolate along bottom & top edges)
                const vB = _lerp(sw, se, t);
                const vT = _lerp(nw, ne, t);
                lines.push([[vB[1], vB[0]], [vT[1], vT[0]]]);
            }
        });

        return lines;
    }

    // ── Sub-grid (depth 1, 1/3) ───────────────────────
    function _renderSubGrid(map, zoom) {
        const lines = _computeInternalLines(gridGeoJson.features, 3, null);
        if (lines.length === 0) return;

        let color, weight;
        if (zoom >= 15)      { color = 'rgba(0, 0, 0, 0.4)';  weight = 1.2; }
        else if (zoom >= 13) { color = 'rgba(0, 0, 0, 0.3)';  weight = 0.8; }
        else                 { color = 'rgba(0, 0, 0, 0.2)';  weight = 0.6; }

        subGridLayer = L.polyline(lines, {
            color: color,
            weight: weight,
            dashArray: '6,4',
            interactive: false,
        }).addTo(map);
    }

    // ── Sub-sub-grid (depth 2, 1/9) ──────────────────
    function _renderSubSubGrid(map, zoom) {
        const lines = _computeInternalLines(gridGeoJson.features, 9, 3);
        if (lines.length === 0) return;

        let color, weight;
        if (zoom >= 15)      { color = 'rgba(0, 0, 0, 0.25)'; weight = 0.8; }
        else if (zoom >= 13) { color = 'rgba(0, 0, 0, 0.15)'; weight = 0.5; }
        else                 { color = 'rgba(0, 0, 0, 0.1)';  weight = 0.4; }

        subSubGridLayer = L.polyline(lines, {
            color: color,
            weight: weight,
            dashArray: '3,3',
            interactive: false,
        }).addTo(map);
    }

    // ── Axis labels (column letters, row numbers, corner coords) ──
    function _renderLabels(map, zoom) {
        const features = gridGeoJson.features;

        const colSet = {};
        const rowSet = {};
        let gridMinLat = 90, gridMaxLat = -90, gridMinLng = 180, gridMaxLng = -180;

        features.forEach(f => {
            const label = f.properties && f.properties.label;
            if (!label) return;
            const col = label.match(/^[A-Z]+/)?.[0];
            const row = label.match(/[0-9]+$/)?.[0];
            if (!col || !row) return;

            const coords = f.geometry.coordinates[0];
            let fMinLat = 90, fMaxLat = -90, fMinLng = 180, fMaxLng = -180;
            coords.forEach(c => {
                if (c[1] < fMinLat) fMinLat = c[1];
                if (c[1] > fMaxLat) fMaxLat = c[1];
                if (c[0] < fMinLng) fMinLng = c[0];
                if (c[0] > fMaxLng) fMaxLng = c[0];
            });

            if (fMinLat < gridMinLat) gridMinLat = fMinLat;
            if (fMaxLat > gridMaxLat) gridMaxLat = fMaxLat;
            if (fMinLng < gridMinLng) gridMinLng = fMinLng;
            if (fMaxLng > gridMaxLng) gridMaxLng = fMaxLng;

            if (!colSet[col]) colSet[col] = { minLng: 180, maxLng: -180 };
            if (fMinLng < colSet[col].minLng) colSet[col].minLng = fMinLng;
            if (fMaxLng > colSet[col].maxLng) colSet[col].maxLng = fMaxLng;

            if (!rowSet[row]) rowSet[row] = { minLat: 90, maxLat: -90 };
            if (fMinLat < rowSet[row].minLat) rowSet[row].minLat = fMinLat;
            if (fMaxLat > rowSet[row].maxLat) rowSet[row].maxLat = fMaxLat;
        });

        Object.keys(colSet).forEach(col => {
            colSet[col].centerLng = (colSet[col].minLng + colSet[col].maxLng) / 2;
        });
        Object.keys(rowSet).forEach(row => {
            rowSet[row].centerLat = (rowSet[row].minLat + rowSet[row].maxLat) / 2;
        });

        labelLayer = L.layerGroup();
        const latOffset = (gridMaxLat - gridMinLat) * 0.04;
        const lngOffset = (gridMaxLng - gridMinLng) * 0.04;
        const labelSize = zoom >= 15 ? 'lg' : zoom >= 13 ? 'md' : 'sm';

        // Column headers – TOP edge
        Object.keys(colSet).sort().forEach(col => {
            labelLayer.addLayer(L.marker([gridMaxLat + latOffset, colSet[col].centerLng], {
                icon: L.divIcon({
                    className: `grid-axis-label grid-axis-${labelSize}`,
                    html: `<span>${col}</span>`,
                    iconSize: [30, 18], iconAnchor: [15, 18],
                }),
                interactive: false, zIndexOffset: 2000,
            }));
        });

        // Column headers – BOTTOM edge
        Object.keys(colSet).sort().forEach(col => {
            labelLayer.addLayer(L.marker([gridMinLat - latOffset, colSet[col].centerLng], {
                icon: L.divIcon({
                    className: `grid-axis-label grid-axis-${labelSize}`,
                    html: `<span>${col}</span>`,
                    iconSize: [30, 18], iconAnchor: [15, 0],
                }),
                interactive: false, zIndexOffset: 2000,
            }));
        });

        // Row headers – LEFT edge
        Object.keys(rowSet).sort((a, b) => Number(a) - Number(b)).forEach(row => {
            labelLayer.addLayer(L.marker([rowSet[row].centerLat, gridMinLng - lngOffset], {
                icon: L.divIcon({
                    className: `grid-axis-label grid-axis-${labelSize}`,
                    html: `<span>${row}</span>`,
                    iconSize: [26, 18], iconAnchor: [26, 9],
                }),
                interactive: false, zIndexOffset: 2000,
            }));
        });

        // Row headers – RIGHT edge
        Object.keys(rowSet).sort((a, b) => Number(a) - Number(b)).forEach(row => {
            labelLayer.addLayer(L.marker([rowSet[row].centerLat, gridMaxLng + lngOffset], {
                icon: L.divIcon({
                    className: `grid-axis-label grid-axis-${labelSize}`,
                    html: `<span>${row}</span>`,
                    iconSize: [26, 18], iconAnchor: [0, 9],
                }),
                interactive: false, zIndexOffset: 2000,
            }));
        });

        // Corner coordinate ticks
        if (zoom >= 13) {
            const corners = [
                { lat: gridMaxLat, lng: gridMinLng, ax: 0,  ay: 18 },
                { lat: gridMaxLat, lng: gridMaxLng, ax: 70, ay: 18 },
                { lat: gridMinLat, lng: gridMinLng, ax: 0,  ay: 0  },
                { lat: gridMinLat, lng: gridMaxLng, ax: 70, ay: 0  },
            ];
            corners.forEach(c => {
                labelLayer.addLayer(L.marker([c.lat, c.lng], {
                    icon: L.divIcon({
                        className: 'grid-corner-coord',
                        html: `<span>${c.lat.toFixed(3)}°N ${c.lng.toFixed(3)}°E</span>`,
                        iconSize: [70, 16], iconAnchor: [c.ax, c.ay],
                    }),
                    interactive: false, zIndexOffset: 2000,
                }));
            });
        }

        labelLayer.addTo(map);
    }

    // ── Public helpers ────────────────────────────────
    async function getSnailAtPoint(lat, lon, depth = 2) {
        if (!sessionId) return null;
        try {
            const resp = await fetch(
                `/api/sessions/${sessionId}/grid/point-to-snail?lat=${lat}&lon=${lon}&depth=${depth}`
            );
            if (!resp.ok) return null;
            return await resp.json();
        } catch {
            return null;
        }
    }

    function setupMouseTracker(map) {
        const display = document.getElementById('snail-display');
        let debounceTimer = null;

        map.on('mousemove', (e) => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(async () => {
                const result = await getSnailAtPoint(e.latlng.lat, e.latlng.lng, 2);
                if (result && result.snail_path) {
                    display.textContent = `📍 ${result.snail_path}`;
                } else {
                    display.textContent = '';
                }
            }, 150);
        });
    }

    function getGridGeoJson() { return gridGeoJson; }

    return { load, getSnailAtPoint, setupMouseTracker, getGridGeoJson };
})();
