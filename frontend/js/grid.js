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
    let _mouseTracker = null;
    let _mouseOutTracker = null;

    // Shared canvas renderer for all grid line layers (much faster than SVG)
    let _gridCanvas = null;

    // Cached computed internal lines
    let _subLines = null;
    let _subSubLines = null;
    let _loading = false;  // guard against concurrent loads

    async function load(map, sessId) {
        // Prevent concurrent loads that cause double grid
        if (_loading) return;
        _loading = true;

        sessionId = sessId;
        _map = map;

        // Fully clean up previous grid (layers + canvas + zoom handler)
        _removeLayers(map);
        _subLines = null;
        _subSubLines = null;
        _visible = true;  // Always show grid after a fresh load
        if (_zoomHandler) { map.off('zoomend', _zoomHandler); _zoomHandler = null; }

        try {
            const resp = await fetch(`/api/sessions/${sessionId}/grid?depth=0`);
            if (!resp.ok) { _loading = false; return; }
            gridGeoJson = await resp.json();

            if (!gridGeoJson || !gridGeoJson.features || gridGeoJson.features.length === 0) { _loading = false; return; }

            // Pre-compute internal lines once
            _subLines = _computeInternalLines(gridGeoJson.features, 3, null);
            _subSubLines = _computeInternalLines(gridGeoJson.features, 9, 3);

            _createAllLayers(map);
            _updateZoomVisibility(map);

            _zoomHandler = () => _updateZoomVisibility(map);
            map.on('zoomend', _zoomHandler);

            // Clear scenario builder grid preview (session grid takes precedence)
            try {
                if (typeof KScenarioBuilder !== 'undefined' && KScenarioBuilder.isActive()) {
                    KScenarioBuilder.clearGridPreview && KScenarioBuilder.clearGridPreview();
                }
            } catch(e) {}
        } catch (err) {
            console.warn('Grid load failed:', err);
        }
        _loading = false;
    }

    /** Remove all grid layers AND the canvas renderer from the map. */
    function _removeLayers(map) {
        if (gridLayer)       { try { map.removeLayer(gridLayer); } catch(e){} gridLayer = null; }
        if (subGridLayer)    { try { map.removeLayer(subGridLayer); } catch(e){} subGridLayer = null; }
        if (subSubGridLayer) { try { map.removeLayer(subSubGridLayer); } catch(e){} subSubGridLayer = null; }
        if (labelLayer)      { try { map.removeLayer(labelLayer); } catch(e){} labelLayer = null; }
        // Always destroy the canvas renderer to prevent ghost layers
        if (_gridCanvas) {
            try { map.removeLayer(_gridCanvas); } catch(e){}
            _gridCanvas = null;
        }
        _subSubVisible = false;
    }

    /** Create all layers with a FRESH canvas renderer – called on load and toggle-on. */
    function _createAllLayers(map) {
        if (!_visible || !gridGeoJson) return;

        // Always create a fresh canvas renderer to avoid stale/ghost canvas
        if (_gridCanvas) {
            try { map.removeLayer(_gridCanvas); } catch(e){}
        }
        _gridCanvas = L.canvas({ padding: 0.5 });

        // ── Depth 0: Major grid – solid lines
        gridLayer = L.geoJSON(gridGeoJson, {
            renderer: _gridCanvas,
            style: () => ({
                color: '#1a3a5c',
                weight: 2.5,
                fillColor: 'transparent',
                fillOpacity: 0,
            }),
            interactive: false,
        }).addTo(map);

        // ── Depth 1: Sub-grid at 1/3 – dashed
        if (_subLines && _subLines.length > 0) {
            subGridLayer = L.polyline(_subLines, {
                renderer: _gridCanvas,
                color: 'rgba(26, 58, 92, 0.55)',
                weight: 1,
                dashArray: '6,4',
                interactive: false,
            }).addTo(map);
        }

        // ── Depth 2: Sub-sub-grid at 1/9 – faint dashed (may be hidden)
        if (_subSubLines && _subSubLines.length > 0) {
            subSubGridLayer = L.polyline(_subSubLines, {
                renderer: _gridCanvas,
                color: 'rgba(26, 58, 92, 0.3)',
                weight: 0.6,
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
        if (!_labelsVisible) {
            map.removeLayer(labelLayer);
        }
    }

    // ── Public helpers
    async function getSnailAtPoint(lat, lon, depth = 2) {
        if (!sessionId) return null;
        // Quick client-side bounds check — skip request if clearly outside grid
        if (gridGeoJson && gridGeoJson.features && gridGeoJson.features.length > 0) {
            let minLat = 90, maxLat = -90, minLng = 180, maxLng = -180;
            gridGeoJson.features.forEach(f => {
                if (!f.geometry || !f.geometry.coordinates) return;
                f.geometry.coordinates[0].forEach(c => {
                    if (c[1] < minLat) minLat = c[1];
                    if (c[1] > maxLat) maxLat = c[1];
                    if (c[0] < minLng) minLng = c[0];
                    if (c[0] > maxLng) maxLng = c[0];
                });
            });
            if (lat < minLat || lat > maxLat || lon < minLng || lon > maxLng) {
                return null; // Outside grid — don't even call the API
            }
        }
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

        // Remove previous tracker if any
        if (_mouseTracker) {
            map.off('mousemove', _mouseTracker);
            map.off('mouseout', _mouseOutTracker);
        }

        _mouseTracker = async (e) => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(async () => {
                const result = await getSnailAtPoint(e.latlng.lat, e.latlng.lng, 2);
                if (result && result.snail_path) {
                    display.textContent = `📍 ${result.snail_path}`;
                } else {
                    display.textContent = '';
                }
            }, 400);
        };

        _mouseOutTracker = () => {
            clearTimeout(debounceTimer);
            display.textContent = '';
        };

        map.on('mousemove', _mouseTracker);
        map.on('mouseout', _mouseOutTracker);
    }

    function getGridGeoJson() { return gridGeoJson; }

    let _labelsVisible = true;

    function toggle() {
        _visible = !_visible;
        if (!_map) return _visible;

        if (_visible) {
            // Recreate layers (and fresh canvas) from cached data
            _createAllLayers(_map);
            _updateZoomVisibility(_map);
        } else {
            // Remove all layers including canvas renderer
            _removeLayers(_map);
        }
        return _visible;
    }

    /** Toggle just the grid axis/corner labels. */
    function toggleLabels() {
        _labelsVisible = !_labelsVisible;
        if (!_map) return _labelsVisible;
        if (_labelsVisible) {
            if (labelLayer && !_map.hasLayer(labelLayer) && _visible) labelLayer.addTo(_map);
        } else {
            if (labelLayer && _map.hasLayer(labelLayer)) _map.removeLayer(labelLayer);
        }
        return _labelsVisible;
    }

    function isVisible() { return _visible; }

    /** Clear all grid layers and data (used on logout). */
    function clearAll() {
        if (_map) {
            _removeLayers(_map);
            if (_zoomHandler) { _map.off('zoomend', _zoomHandler); _zoomHandler = null; }
            if (_mouseTracker) { _map.off('mousemove', _mouseTracker); _mouseTracker = null; }
            if (_mouseOutTracker) { _map.off('mouseout', _mouseOutTracker); _mouseOutTracker = null; }
        }
        gridGeoJson = null;
        sessionId = null;
        _subLines = null;
        _subSubLines = null;
        _subSubVisible = false;
    }

    return { load, getSnailAtPoint, setupMouseTracker, getGridGeoJson, toggle, toggleLabels, isVisible, clearAll };
})();
