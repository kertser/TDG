/**
 * overlays.js – Drawing tools and collaborative overlay rendering.
 * Uses Leaflet.Editable for standard shapes and custom Catmull-Rom
 * spline drawing for curved arrow overlays.
 *
 * Right-click = finish/commit drawing (arrow: finalizes the spline)
 * ESC = cancel drawing
 */
const KOverlays = (() => {
    let overlaysLayer = null;
    let overlayMap = {};  // overlay_id → L.layer
    let map = null;
    let sessionId = null;
    let token = null;
    let currentDrawing = null;  // { type, layer }

    // ── Arrow/spline drawing state ──────────────────
    let arrowDrawing = false;
    let arrowControlPoints = [];
    let arrowPreviewGroup = null;

    // Default overlay styles per type
    const DEFAULT_STYLES = {
        polyline:  { color: '#2196f3', weight: 3, opacity: 0.9 },
        polygon:   { color: '#4caf50', weight: 2, fillOpacity: 0.15 },
        rectangle: { color: '#ff9800', weight: 2, fillOpacity: 0.12 },
        marker:    {},
        circle:    { color: '#9c27b0', weight: 2, fillOpacity: 0.1 },
        arrow:     { color: '#f44336', weight: 3, opacity: 0.9 },
        label:     {},
    };

    function init(leafletMap) {
        map = leafletMap;
        overlaysLayer = L.layerGroup().addTo(map);
        arrowPreviewGroup = L.layerGroup().addTo(map);

        // Listen for Leaflet.Editable drawing completion
        map.on('editable:drawing:commit', _onDrawingCommit);

        // Global ESC handler to cancel any active drawing
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                cancelDraw();
            }
        });

        // Right-click on the map: finish active Leaflet.Editable drawing
        map.on('contextmenu', (e) => {
            // Arrow tool has its own right-click handler
            if (arrowDrawing) return;

            if (currentDrawing && currentDrawing.layer) {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                _finishCurrentDrawing();
            }
        });
    }

    function setSession(sessId, authToken) {
        sessionId = sessId;
        token = authToken;

        // Show drawing toolbar and right-side controls
        const toolbar = document.getElementById('draw-toolbar');
        if (toolbar) toolbar.style.display = 'flex';
        const centerBtn = document.getElementById('center-btn');
        if (centerBtn) centerBtn.style.display = 'inline-flex';
        const gridToggleBtn = document.getElementById('grid-toggle-btn');
        if (gridToggleBtn) gridToggleBtn.style.display = 'inline-flex';
    }

    // ── Finish current Leaflet.Editable drawing ─────
    function _finishCurrentDrawing() {
        if (!currentDrawing || !currentDrawing.layer) return;

        try {
            const layer = currentDrawing.layer;
            const editor = layer.editor;

            if (!editor) {
                cancelDraw();
                return;
            }

            // For polylines: need at least 2 points
            if (currentDrawing.type === 'polyline') {
                const latlngs = layer.getLatLngs();
                if (latlngs.length < 2) {
                    cancelDraw();
                    return;
                }
                // Pop the temporary guide vertex, then commit
                try { editor.pop(); } catch {}
                editor.commitDrawing();
            } else if (currentDrawing.type === 'polygon') {
                const latlngs = layer.getLatLngs();
                const ring = latlngs[0] || latlngs;
                if (ring.length < 3) {
                    cancelDraw();
                    return;
                }
                try { editor.pop(); } catch {}
                editor.commitDrawing();
            } else {
                // Rectangle, circle, marker: commit directly
                editor.commitDrawing();
            }
        } catch (err) {
            console.warn('Finish drawing error:', err);
            cancelDraw();
        }
    }

    // ── Standard Shape Drawing ──────────────────────

    function startDraw(type) {
        if (!map) return;

        // Cancel any active drawing first
        cancelDraw();

        if (type === 'arrow') {
            _startArrowDraw();
            return;
        }

        switch (type) {
            case 'polyline':
                currentDrawing = { type: 'polyline', layer: map.editTools.startPolyline() };
                break;
            case 'polygon':
                currentDrawing = { type: 'polygon', layer: map.editTools.startPolygon() };
                break;
            case 'rectangle':
                currentDrawing = { type: 'rectangle', layer: map.editTools.startRectangle() };
                break;
            case 'marker':
                currentDrawing = { type: 'marker', layer: map.editTools.startMarker() };
                break;
            case 'circle':
                currentDrawing = { type: 'circle', layer: map.editTools.startCircle() };
                break;
            default:
                console.warn('Unknown draw type:', type);
                return;
        }

        // Visual feedback
        map.getContainer().style.cursor = 'crosshair';
    }

    function cancelDraw() {
        // Cancel standard Leaflet.Editable drawing
        if (currentDrawing && currentDrawing.layer) {
            try { currentDrawing.layer.remove(); } catch {}
        }
        currentDrawing = null;
        if (map && map.editTools) {
            try { map.editTools.stopDrawing(); } catch {}
        }
        if (map) {
            map.getContainer().style.cursor = '';
        }
        // Cancel arrow drawing (clears preview)
        _cancelArrowDraw();
        // Reset toolbar buttons
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    function _onDrawingCommit(e) {
        const layer = e.layer;
        if (!currentDrawing || !sessionId) return;

        const overlayType = currentDrawing.type;
        const geometry = _layerToGeoJSON(layer);

        if (!geometry) {
            layer.remove();
            currentDrawing = null;
            map.getContainer().style.cursor = '';
            return;
        }

        const style = { ...(DEFAULT_STYLES[overlayType] || {}) };

        // Send via WebSocket
        KWebSocket.send('overlay_create', {
            overlay_type: overlayType,
            geometry: geometry,
            style_json: style,
        });

        // Remove the temporary drawn layer (re-added from server response)
        layer.remove();
        currentDrawing = null;
        map.getContainer().style.cursor = '';
        // Reset toolbar buttons
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    function _layerToGeoJSON(layer) {
        if (!layer) return null;
        if (layer instanceof L.Circle) {
            const center = layer.getLatLng();
            const radius = layer.getRadius();
            return {
                type: 'Point',
                coordinates: [center.lng, center.lat],
            };
        }
        if (layer.toGeoJSON) {
            return layer.toGeoJSON().geometry;
        }
        return null;
    }

    // ══════════════════════════════════════════════════
    // ── Curved Arrow (Spline) Drawing ────────────────
    // ══════════════════════════════════════════════════

    function _startArrowDraw() {
        // Clear any previous arrow preview first
        arrowPreviewGroup.clearLayers();
        arrowDrawing = true;
        arrowControlPoints = [];
        _arrowPreviewLine = null;
        map.getContainer().style.cursor = 'crosshair';

        map.on('click', _onArrowClick);
        map.on('mousemove', _onArrowMouseMove);
        map.on('contextmenu', _onArrowRightClick);
    }

    /**
     * Stop the arrow drawing mode (remove event listeners)
     * but do NOT clear the preview visuals.
     */
    function _stopArrowDrawMode() {
        arrowDrawing = false;
        arrowControlPoints = [];
        _arrowPreviewLine = null;
        if (map) {
            map.getContainer().style.cursor = '';
            map.off('click', _onArrowClick);
            map.off('mousemove', _onArrowMouseMove);
            map.off('contextmenu', _onArrowRightClick);
        }
    }

    /**
     * Cancel arrow drawing: stop drawing AND clear preview visuals.
     * Used when user presses ESC or starts a different tool.
     */
    function _cancelArrowDraw() {
        _stopArrowDrawMode();
        arrowPreviewGroup.clearLayers();
    }

    let _arrowPreviewLine = null;

    function _onArrowClick(e) {
        if (!arrowDrawing) return;

        arrowControlPoints.push([e.latlng.lat, e.latlng.lng]);

        // Draw control point marker
        const marker = L.circleMarker(e.latlng, {
            radius: 5, color: '#f44336', fillColor: '#f44336',
            fillOpacity: 0.9, weight: 2,
        });
        arrowPreviewGroup.addLayer(marker);

        // Redraw spline preview
        _updateArrowPreview();
    }

    function _onArrowMouseMove(e) {
        if (!arrowDrawing || arrowControlPoints.length === 0) return;

        // Show a preview line from last point to cursor
        const pts = [...arrowControlPoints, [e.latlng.lat, e.latlng.lng]];
        if (pts.length >= 2) {
            const spline = catmullRomSpline(pts, 16);
            if (_arrowPreviewLine) {
                _arrowPreviewLine.setLatLngs(spline);
            } else {
                _arrowPreviewLine = L.polyline(spline, {
                    color: '#f44336', weight: 2, dashArray: '6,4', opacity: 0.6,
                });
                arrowPreviewGroup.addLayer(_arrowPreviewLine);
            }
        }
    }

    function _onArrowRightClick(e) {
        if (!arrowDrawing) return;
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _finalizeArrow();
    }

    function _updateArrowPreview() {
        if (arrowControlPoints.length < 2) return;

        // Remove old preview line (keep control point markers)
        if (_arrowPreviewLine) {
            arrowPreviewGroup.removeLayer(_arrowPreviewLine);
            _arrowPreviewLine = null;
        }

        const spline = catmullRomSpline(arrowControlPoints, 20);
        _arrowPreviewLine = L.polyline(spline, {
            color: '#f44336', weight: 3, opacity: 0.7,
        });
        arrowPreviewGroup.addLayer(_arrowPreviewLine);
    }

    function _finalizeArrow() {
        if (arrowControlPoints.length < 2) {
            _cancelArrowDraw();
            return;
        }

        // Compute final spline
        const spline = catmullRomSpline(arrowControlPoints, 20);

        // Build GeoJSON LineString
        const coordinates = spline.map(p => [p[1], p[0]]); // [lng, lat]
        const geometry = { type: 'LineString', coordinates };

        const style = { ...DEFAULT_STYLES.arrow };

        // Send to server
        KWebSocket.send('overlay_create', {
            overlay_type: 'arrow',
            geometry: geometry,
            style_json: style,
            properties: {
                control_points: arrowControlPoints,
                is_spline: true,
            },
        });

        // Stop drawing mode but keep preview visible until server
        // responds with overlay_created (the preview will be cleared
        // when the user starts a new drawing tool).
        _stopArrowDrawMode();

        // Reset toolbar button
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    // ── Catmull-Rom Spline Interpolation ─────────────
    function catmullRomSpline(points, numPerSegment = 20) {
        if (points.length < 2) return points.slice();
        if (points.length === 2) {
            const result = [];
            for (let t = 0; t <= 1; t += 1 / numPerSegment) {
                result.push([
                    points[0][0] + (points[1][0] - points[0][0]) * t,
                    points[0][1] + (points[1][1] - points[0][1]) * t,
                ]);
            }
            result.push(points[1]);
            return result;
        }

        const pts = [points[0], ...points, points[points.length - 1]];
        const result = [];

        for (let i = 1; i < pts.length - 2; i++) {
            const p0 = pts[i - 1];
            const p1 = pts[i];
            const p2 = pts[i + 1];
            const p3 = pts[i + 2];

            for (let t = 0; t < 1; t += 1 / numPerSegment) {
                const t2 = t * t;
                const t3 = t2 * t;

                const lat = 0.5 * (
                    (2 * p1[0]) +
                    (-p0[0] + p2[0]) * t +
                    (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                    (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
                );
                const lng = 0.5 * (
                    (2 * p1[1]) +
                    (-p0[1] + p2[1]) * t +
                    (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                    (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
                );
                result.push([lat, lng]);
            }
        }
        result.push(points[points.length - 1]);

        return result;
    }

    // ── Arrowhead Rendering Helper ──────────────────
    function _createArrowhead(latlngs, style = {}) {
        if (latlngs.length < 2) return null;

        const tip = latlngs[latlngs.length - 1];
        const prev = latlngs[latlngs.length - 2];

        const dLat = tip[0] - prev[0];
        const dLng = tip[1] - prev[1];
        const angle = Math.atan2(dLng, dLat);

        const size = 0.0008;
        const spread = 0.45;

        const left = [
            tip[0] - size * Math.cos(angle - spread),
            tip[1] - size * Math.sin(angle - spread),
        ];
        const right = [
            tip[0] - size * Math.cos(angle + spread),
            tip[1] - size * Math.sin(angle + spread),
        ];

        return L.polygon([tip, left, right], {
            color: style.color || '#f44336',
            fillColor: style.color || '#f44336',
            fillOpacity: 0.9,
            weight: 1,
            interactive: false,
        });
    }

    // ── Rendering ────────────────────────────────────

    async function loadFromServer() {
        if (!sessionId || !token) return;
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/overlays`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) return;
            const overlays = await resp.json();
            render(overlays);
        } catch (err) {
            console.warn('Overlays load failed:', err);
        }
    }

    function render(overlays) {
        if (!overlaysLayer) return;
        overlaysLayer.clearLayers();
        overlayMap = {};
        overlays.forEach(o => addOverlayToMap(o));
    }

    function addOverlayToMap(overlay) {
        if (!overlay.geometry || !overlaysLayer) return;

        // Clear arrow preview since overlay from server replaces it
        arrowPreviewGroup.clearLayers();

        const style = overlay.style_json || DEFAULT_STYLES[overlay.overlay_type] || {};
        let layer = null;

        try {
            if (overlay.overlay_type === 'arrow' && overlay.geometry.type === 'LineString') {
                const coords = overlay.geometry.coordinates.map(c => [c[1], c[0]]);
                const group = L.layerGroup();

                const line = L.polyline(coords, {
                    color: style.color || '#f44336',
                    weight: style.weight || 3,
                    opacity: style.opacity || 0.9,
                });
                group.addLayer(line);

                const arrow = _createArrowhead(coords, style);
                if (arrow) group.addLayer(arrow);

                layer = group;
            } else {
                layer = L.geoJSON(overlay.geometry, {
                    style: () => style,
                    pointToLayer: (feature, latlng) => {
                        if (overlay.overlay_type === 'circle') {
                            return L.circle(latlng, {
                                radius: overlay.properties?.radius || 500,
                                ...style,
                            });
                        }
                        return L.marker(latlng);
                    },
                });
            }

            if (layer) {
                // Right-click to delete overlay
                layer.on('contextmenu', (e) => {
                    L.DomEvent.stopPropagation(e);
                    L.DomEvent.preventDefault(e);
                    if (confirm('Delete this overlay?')) {
                        KWebSocket.send('overlay_delete', { overlay_id: overlay.id });
                    }
                });

                overlaysLayer.addLayer(layer);
                overlayMap[overlay.id] = layer;
            }
        } catch (err) {
            console.warn('Failed to render overlay:', overlay.id, err);
        }
    }

    // ── WS event handlers ────────────────────────────

    function onOverlayCreated(data) {
        addOverlayToMap(data);
    }

    function onOverlayUpdated(data) {
        // Remove old, add new
        if (data.id && overlayMap[data.id]) {
            overlaysLayer.removeLayer(overlayMap[data.id]);
            delete overlayMap[data.id];
        }
        addOverlayToMap(data);
    }

    function onOverlayDeleted(data) {
        const id = data.overlay_id || data.id;
        if (id && overlayMap[id]) {
            overlaysLayer.removeLayer(overlayMap[id]);
            delete overlayMap[id];
        }
    }

    return {
        init, setSession, startDraw, cancelDraw,
        loadFromServer, render,
        onOverlayCreated, onOverlayUpdated, onOverlayDeleted,
    };
})();
