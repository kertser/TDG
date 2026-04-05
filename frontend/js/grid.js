/**
 * grid.js – Tactical grid overlay with three depth levels and axis labels.
 *
 * Depth 0: Major grid squares (~1 km) – solid lines
 * Depth 1: Sub-grid (1/3 ≈ 333 m)   – thin dashed lines
 * Depth 2: Sub-sub-grid (1/9 ≈ 111 m) – semi-transparent dashed lines
 *
 * Performance: Uses Canvas renderer, caches computed lines, only toggles
 * sub-sub-grid visibility on zoom threshold instead of full re-render.
 */
const KGrid = (() => {
    let gridLayer = null;
    let subGridLayer = null;
    let subSubGridLayer = null;
    let labelLayer = null;
    let sessionId = null;
    let gridGeoJson = null;
    let _map = null;
    let _zoomHandler = null;
    let _visible = true;
    let _subSubVisible = false;

    // Shared canvas renderer for all grid line layers (much faster than SVG)
    let _gridCanvas = null;

    // Cached computed internal lines
    let _subLines = null;
    let _subSubLines = null;

    async function load(map, sessId) {
        sessionId = sessId;
        _map = map;

        _removeLayers(map);
        _subLines = null;
        _subSubLines = null;
        if (_zoomHandler) { map.off('zoomend', _zoomHandler); _zoomHandler = null; }

        try {
            const resp = await fetch(`/api/sessions/${sessionId}/grid?depth=0`);
            if (!resp.ok) return;
            gridGeoJson = await resp.json();

            if (!gridGeoJson || !gridGeoJson.features || gridGeoJson.features.length === 0) return;

            // Create canvas renderer (shared by all grid layers)
            _gridCanvas = L.canvas({ padding: 0.5 });

            // Pre-compute internal lines once
            _subLines = _computeInternalLines(gridGeoJson.features, 3, null);
            _subSubLines = _computeInternalLines(gridGeoJson.features, 9, 3);

            _createAllLayers(map);
            _updateZoomVisibility(map);

            _zoomHandler = () => _updateZoomVisibility(map);
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

    /** Create all layers once – called only on load, not on zoom. */
    function _createAllLayers(map) {
        if (!_visible || !gridGeoJson) return;

        // ── Depth 0: Major grid – solid lines
        gridLayer = L.geoJSON(gridGeoJson, {
            renderer: _gridCanvas,
            style: () => ({
                color: 'rgba(0, 0, 0, 0.55)',
                weight: 2,
                fillColor: 'transparent',
                fillOpacity: 0,
            }),
            interactive: false,
        }).addTo(map);

        // ── Depth 1: Sub-grid at 1/3 – dashed
        if (_subLines && _subLines.length > 0) {
            subGridLayer = L.polyline(_subLines, {
                renderer: _gridCanvas,
                color: 'rgba(0, 0, 0, 0.3)',
                weight: 0.8,
                dashArray: '6,4',
                interactive: false,
            }).addTo(map);
        }

        // ── Depth 2: Sub-sub-grid at 1/9 – faint dashed (may be hidden)
        if (_subSubLines && _subSubLines.length > 0) {
            subSubGridLayer = L.polyline(_subSubLines, {
                renderer: _gridCanvas,
                color: 'rgba(0, 0, 0, 0.15)',
                weight: 0.5,
                dashArray: '3,3',
                interactive: false,
            });
            // Don't add yet – _updateZoomVisibility will decide
            _subSubVisible = false;
        }

        // ── Labels
        _createLabels(map);
    }

    /** Toggle sub-sub-grid based on zoom threshold (lightweight). */
    function _updateZoomVisibility(map) {
        if (!_visible) return;
        const zoom = map.getZoom();
        const shouldShow = zoom >= 12;

        if (shouldShow && !_subSubVisible && subSubGridLayer) {
            subSubGridLayer.addTo(map);
            _subSubVisible = true;
        } else if (!shouldShow && _subSubVisible && subSubGridLayer) {
            map.removeLayer(subSubGridLayer);
            _subSubVisible = false;
        }
    }

    // ── Interpolation helper
    function _lerp(p1, p2, t) {
        return [p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t];
    }

    function _computeInternalLines(features, divisions, skipEvery) {
        const lines = [];
        features.forEach(f => {
            if (!f.geometry || f.geometry.type !== 'Polygon') return;
            const coords = f.geometry.coordinates[0];
            if (coords.length < 5) return;

            const sw = coords[0], se = coords[1], ne = coords[2], nw = coords[3];

            for (let i = 1; i < divisions; i++) {
                if (skipEvery && i % skipEvery === 0) continue;
                const t = i / divisions;

                const hL = _lerp(sw, nw, t);
                const hR = _lerp(se, ne, t);
                lines.push([[hL[1], hL[0]], [hR[1], hR[0]]]);

                const vB = _lerp(sw, se, t);
                const vT = _lerp(nw, ne, t);
                lines.push([[vB[1], vB[0]], [vT[1], vT[0]]]);
            }
        });
        return lines;
    }

    // ── Axis labels
    function _createLabels(map) {
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

        // Column headers – TOP and BOTTOM
        Object.keys(colSet).sort().forEach(col => {
            labelLayer.addLayer(L.marker([gridMaxLat + latOffset, colSet[col].centerLng], {
                icon: L.divIcon({
                    className: 'grid-axis-label grid-axis-md',
                    html: `<span>${col}</span>`,
                    iconSize: [30, 18], iconAnchor: [15, 18],
                }),
                interactive: false, zIndexOffset: 2000,
            }));
            labelLayer.addLayer(L.marker([gridMinLat - latOffset, colSet[col].centerLng], {
                icon: L.divIcon({
                    className: 'grid-axis-label grid-axis-md',
                    html: `<span>${col}</span>`,
                    iconSize: [30, 18], iconAnchor: [15, 0],
                }),
                interactive: false, zIndexOffset: 2000,
            }));
        });

        // Row headers – LEFT and RIGHT
        Object.keys(rowSet).sort((a, b) => Number(a) - Number(b)).forEach(row => {
            labelLayer.addLayer(L.marker([rowSet[row].centerLat, gridMinLng - lngOffset], {
                icon: L.divIcon({
                    className: 'grid-axis-label grid-axis-md',
                    html: `<span>${row}</span>`,
                    iconSize: [26, 18], iconAnchor: [26, 9],
                }),
                interactive: false, zIndexOffset: 2000,
            }));
            labelLayer.addLayer(L.marker([rowSet[row].centerLat, gridMaxLng + lngOffset], {
                icon: L.divIcon({
                    className: 'grid-axis-label grid-axis-md',
                    html: `<span>${row}</span>`,
                    iconSize: [26, 18], iconAnchor: [0, 9],
                }),
                interactive: false, zIndexOffset: 2000,
            }));
        });

        // Corner coordinates
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

        labelLayer.addTo(map);
    }

    // ── Public helpers
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
            }, 400);
        });
    }

    function getGridGeoJson() { return gridGeoJson; }

    function toggle() {
        _visible = !_visible;
        if (!_map) return _visible;

        if (_visible) {
            _createAllLayers(_map);
            _updateZoomVisibility(_map);
        } else {
            _removeLayers(_map);
            _subSubVisible = false;
        }
        return _visible;
    }

    function isVisible() { return _visible; }

    return { load, getSnailAtPoint, setupMouseTracker, getGridGeoJson, toggle, isVisible };
})();
