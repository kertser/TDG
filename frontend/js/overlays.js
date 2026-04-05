/**
 * overlays.js – Drawing tools and collaborative overlay rendering.
 *
 * ALL drawing tools are custom (no Leaflet.Editable dependency):
 *   Arrow:     Catmull-Rom spline, click points, right-click finish → blue/red
 *   Polyline:  Click to add points, right-click finish
 *   Rectangle: Click two corners (dashed)
 *   Ellipse:   Click center, click edge (dashed)
 *   Marker:    Single click
 *
 * Right-click on any overlay → context menu (delete, color, width).
 * ESC = cancel any active drawing
 */
const KOverlays = (() => {
    let overlaysLayer = null;
    let overlayMap = {};       // overlay_id → Leaflet layer
    let overlayDataMap = {};   // overlay_id → overlay data object
    let map = null;
    let sessionId = null;
    let token = null;
    let _visible = true;

    // ── Drawing state ────────────────────────────────
    let activeTool = null;   // 'arrow'|'polyline'|'rectangle'|'ellipse'|'marker'|null
    let drawPoints = [];
    let previewGroup = null;
    let _previewLine = null;
    let _previewShape = null;

    // ── Side-based colors ────────────────────────────
    const BLUE_COLOR = '#2196f3';
    const RED_COLOR  = '#f44336';

    function _getDrawColor() { return BLUE_COLOR; }

    function _getOverlayColor(overlay) {
        if (overlay.side === 'red') return RED_COLOR;
        if (overlay.style_json && overlay.style_json.color) return overlay.style_json.color;
        return BLUE_COLOR;
    }

    // ── Context menu state ───────────────────────────
    let _ctxOverlayId = null;
    let _ctxMenu = null;

    function init(leafletMap) {
        map = leafletMap;
        overlaysLayer = L.layerGroup().addTo(map);
        previewGroup = L.layerGroup().addTo(map);

        // Global ESC handler
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                cancelDraw();
                _hideCtxMenu();
            }
        });

        // Click anywhere to dismiss context menu
        document.addEventListener('click', (e) => {
            if (_ctxMenu && !_ctxMenu.contains(e.target)) {
                _hideCtxMenu();
            }
        });

        // Initialize context menu handlers
        _initCtxMenu();
    }

    // ══════════════════════════════════════════════════
    // ── Visibility Toggle ────────────────────────────
    // ══════════════════════════════════════════════════

    function toggle() {
        _visible = !_visible;
        if (overlaysLayer && map) {
            if (_visible) {
                if (!map.hasLayer(overlaysLayer)) map.addLayer(overlaysLayer);
            } else {
                if (map.hasLayer(overlaysLayer)) map.removeLayer(overlaysLayer);
            }
        }
        return _visible;
    }

    function isVisible() { return _visible; }

    // ══════════════════════════════════════════════════
    // ── Context Menu ─────────────────────────────────
    // ══════════════════════════════════════════════════

    function _initCtxMenu() {
        _ctxMenu = document.getElementById('overlay-ctx-menu');
        if (!_ctxMenu) return;

        // Delete button
        _ctxMenu.querySelector('[data-action="delete"]').addEventListener('click', () => {
            if (_ctxOverlayId) {
                KWebSocket.send('overlay_delete', { overlay_id: _ctxOverlayId });
            }
            _hideCtxMenu();
        });

        // Label apply button
        const labelApply = document.getElementById('ctx-label-apply');
        const labelInput = document.getElementById('ctx-label-input');
        if (labelApply && labelInput) {
            labelApply.addEventListener('click', () => {
                if (_ctxOverlayId) {
                    KWebSocket.send('overlay_update', {
                        overlay_id: _ctxOverlayId,
                        label: labelInput.value,  // empty string clears label
                    });
                }
                _hideCtxMenu();
            });
            labelInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') labelApply.click();
                e.stopPropagation(); // prevent ESC from also closing
            });
        }

        // Color swatches
        _ctxMenu.querySelectorAll('.ctx-color-swatch').forEach(swatch => {
            swatch.addEventListener('click', () => {
                const color = swatch.dataset.color;
                if (_ctxOverlayId) {
                    const data = overlayDataMap[_ctxOverlayId];
                    const newStyle = { ...(data?.style_json || {}), color };
                    KWebSocket.send('overlay_update', {
                        overlay_id: _ctxOverlayId,
                        style_json: newStyle,
                    });
                }
                _hideCtxMenu();
            });
        });

        // Width buttons
        _ctxMenu.querySelectorAll('.ctx-width-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const weight = parseInt(btn.dataset.width, 10);
                if (_ctxOverlayId) {
                    const data = overlayDataMap[_ctxOverlayId];
                    const newStyle = { ...(data?.style_json || {}), weight };
                    KWebSocket.send('overlay_update', {
                        overlay_id: _ctxOverlayId,
                        style_json: newStyle,
                    });
                }
                _hideCtxMenu();
            });
        });
    }

    function _showCtxMenu(overlayId, x, y) {
        if (!_ctxMenu) return;
        _ctxOverlayId = overlayId;

        const data = overlayDataMap[overlayId];
        const overlayType = data?.overlay_type || '';
        const isMarker = overlayType === 'marker';
        const currentColor = data?.style_json?.color || BLUE_COLOR;
        const currentWeight = data?.style_json?.weight || 3;

        // Set type header
        const typeLabel = _ctxMenu.querySelector('#ctx-menu-type-label');
        if (typeLabel) {
            const typeNames = { arrow: 'Arrow', polyline: 'Line', rectangle: 'Rectangle', circle: 'Ellipse', marker: 'Marker', polygon: 'Polygon', label: 'Label' };
            typeLabel.textContent = typeNames[overlayType] || overlayType;
        }

        // Show/hide sections based on type
        const labelSection = _ctxMenu.querySelector('.ctx-section-label');
        const colorsSection = _ctxMenu.querySelector('.ctx-section-colors');
        const widthsSection = _ctxMenu.querySelector('.ctx-section-widths');
        if (labelSection) {
            labelSection.style.display = 'block'; // always show label
            const labelInput = document.getElementById('ctx-label-input');
            if (labelInput) labelInput.value = data?.label || '';
        }
        if (colorsSection) colorsSection.style.display = isMarker ? 'none' : 'block';
        if (widthsSection) widthsSection.style.display = isMarker ? 'none' : 'block';

        // Highlight current color/width
        _ctxMenu.querySelectorAll('.ctx-color-swatch').forEach(s => {
            s.classList.toggle('active', s.dataset.color === currentColor);
        });
        _ctxMenu.querySelectorAll('.ctx-width-btn').forEach(b => {
            b.classList.toggle('active', parseInt(b.dataset.width, 10) === currentWeight);
        });

        _ctxMenu.style.display = 'block';

        // Position: keep on screen
        const menuW = _ctxMenu.offsetWidth;
        const menuH = _ctxMenu.offsetHeight;
        const posX = (x + menuW > window.innerWidth) ? x - menuW : x;
        const posY = (y + menuH > window.innerHeight) ? y - menuH : y;
        _ctxMenu.style.left = posX + 'px';
        _ctxMenu.style.top = posY + 'px';

        // Focus label input for markers
        if (isMarker) {
            setTimeout(() => {
                const inp = document.getElementById('ctx-label-input');
                if (inp) inp.focus();
            }, 50);
        }
    }

    function _hideCtxMenu() {
        if (_ctxMenu) {
            _ctxMenu.style.display = 'none';
        }
        _ctxOverlayId = null;
    }

    function setSession(sessId, authToken) {
        sessionId = sessId;
        token = authToken;

        const toolbar = document.getElementById('draw-toolbar');
        if (toolbar) toolbar.style.display = 'flex';
        const centerBtn = document.getElementById('center-btn');
        if (centerBtn) centerBtn.style.display = 'inline-flex';
        const gridToggleBtn = document.getElementById('grid-toggle-btn');
        if (gridToggleBtn) gridToggleBtn.style.display = 'inline-flex';
        const unitsToggleBtn = document.getElementById('units-toggle-btn');
        if (unitsToggleBtn) unitsToggleBtn.style.display = 'inline-flex';
        const overlaysToggleBtn = document.getElementById('overlays-toggle-btn');
        if (overlaysToggleBtn) overlaysToggleBtn.style.display = 'inline-flex';
    }

    // ══════════════════════════════════════════════════
    // ── Drawing Tool Entry Points ────────────────────
    // ══════════════════════════════════════════════════

    function isDrawing() {
        return activeTool !== null;
    }

    function startDraw(type) {
        if (!map) return;
        cancelDraw();

        activeTool = type;
        drawPoints = [];
        _previewLine = null;
        _previewShape = null;
        map.getContainer().style.cursor = 'crosshair';

        if (type === 'marker') {
            map.on('click', _onMarkerClick);
        } else if (type === 'rectangle') {
            map.on('click', _onRectClick);
            map.on('mousemove', _onRectMouseMove);
            map.on('contextmenu', _onRectCancel);
        } else if (type === 'ellipse') {
            map.on('click', _onEllipseClick);
            map.on('mousemove', _onEllipseMouseMove);
            map.on('contextmenu', _onEllipseCancel);
        } else {
            // arrow or polyline
            map.on('click', _onLineClick);
            map.on('mousemove', _onLineMouseMove);
            map.on('contextmenu', _onLineRightClick);
        }
    }

    function cancelDraw() {
        _removeAllDrawListeners();
        activeTool = null;
        drawPoints = [];
        _previewLine = null;
        _previewShape = null;
        previewGroup.clearLayers();
        if (map) map.getContainer().style.cursor = '';
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    /** Stop listening but keep preview visible (for finalized shapes). */
    function _stopDraw() {
        _removeAllDrawListeners();
        activeTool = null;
        drawPoints = [];
        _previewLine = null;
        _previewShape = null;
        if (map) map.getContainer().style.cursor = '';
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    function _removeAllDrawListeners() {
        if (!map) return;
        map.off('click', _onMarkerClick);
        map.off('click', _onRectClick);
        map.off('mousemove', _onRectMouseMove);
        map.off('contextmenu', _onRectCancel);
        map.off('click', _onEllipseClick);
        map.off('mousemove', _onEllipseMouseMove);
        map.off('contextmenu', _onEllipseCancel);
        map.off('click', _onLineClick);
        map.off('mousemove', _onLineMouseMove);
        map.off('contextmenu', _onLineRightClick);
    }

    // ══════════════════════════════════════════════════
    // ── Arrow / Polyline (shared click-to-add logic) ─
    // ══════════════════════════════════════════════════

    function _onLineClick(e) {
        if (!activeTool) return;
        drawPoints.push([e.latlng.lat, e.latlng.lng]);

        const color = _getDrawColor();
        previewGroup.addLayer(L.circleMarker(e.latlng, {
            radius: 4, color, fillColor: color, fillOpacity: 0.8, weight: 2,
        }));
        _updateLinePreview();
    }

    function _onLineMouseMove(e) {
        if (!activeTool || drawPoints.length === 0) return;
        const color = _getDrawColor();
        const pts = [...drawPoints, [e.latlng.lat, e.latlng.lng]];
        const coords = activeTool === 'arrow' ? catmullRomSpline(pts, 16) : pts;

        if (_previewLine) {
            _previewLine.setLatLngs(coords);
        } else {
            _previewLine = L.polyline(coords, {
                color, weight: 2, dashArray: '6,4', opacity: 0.6,
            });
            previewGroup.addLayer(_previewLine);
        }
    }

    function _onLineRightClick(e) {
        if (!activeTool) return;
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _finalizeLine();
    }

    function _updateLinePreview() {
        if (drawPoints.length < 2) return;
        if (_previewLine) {
            previewGroup.removeLayer(_previewLine);
            _previewLine = null;
        }
        const color = _getDrawColor();
        const coords = activeTool === 'arrow' ? catmullRomSpline(drawPoints, 20) : drawPoints;
        _previewLine = L.polyline(coords, { color, weight: 3, opacity: 0.7 });
        previewGroup.addLayer(_previewLine);
    }

    function _finalizeLine() {
        if (drawPoints.length < 2) { cancelDraw(); return; }

        const type = activeTool;
        const color = _getDrawColor();
        let coordinates, properties = {};

        if (type === 'arrow') {
            const spline = catmullRomSpline(drawPoints, 20);
            coordinates = spline.map(p => [p[1], p[0]]);
            properties = { control_points: drawPoints, is_spline: true };
        } else {
            coordinates = drawPoints.map(p => [p[1], p[0]]);
        }

        const geometry = { type: 'LineString', coordinates };
        const style = { color, weight: 3, opacity: 0.9 };

        KWebSocket.send('overlay_create', {
            overlay_type: type,
            geometry,
            style_json: style,
            properties,
        });

        _stopDraw();
    }

    // ══════════════════════════════════════════════════
    // ── Rectangle (two-click, dashed) ────────────────
    // ══════════════════════════════════════════════════

    function _onRectClick(e) {
        if (activeTool !== 'rectangle') return;
        drawPoints.push([e.latlng.lat, e.latlng.lng]);

        if (drawPoints.length === 1) {
            const color = _getDrawColor();
            previewGroup.addLayer(L.circleMarker(e.latlng, {
                radius: 4, color, fillColor: color, fillOpacity: 0.8, weight: 2,
            }));
        } else if (drawPoints.length >= 2) {
            _finalizeRect();
        }
    }

    function _onRectMouseMove(e) {
        if (activeTool !== 'rectangle' || drawPoints.length !== 1) return;
        const color = _getDrawColor();
        const p1 = drawPoints[0];
        const bounds = L.latLngBounds(
            L.latLng(p1[0], p1[1]),
            L.latLng(e.latlng.lat, e.latlng.lng)
        );

        if (_previewShape) {
            _previewShape.setBounds(bounds);
        } else {
            _previewShape = L.rectangle(bounds, {
                color, weight: 2, dashArray: '8,6', fillOpacity: 0.08,
            });
            previewGroup.addLayer(_previewShape);
        }
    }

    function _onRectCancel(e) {
        if (activeTool !== 'rectangle') return;
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        cancelDraw();
    }

    function _finalizeRect() {
        const p1 = drawPoints[0], p2 = drawPoints[1];
        const color = _getDrawColor();

        const minLat = Math.min(p1[0], p2[0]), maxLat = Math.max(p1[0], p2[0]);
        const minLng = Math.min(p1[1], p2[1]), maxLng = Math.max(p1[1], p2[1]);

        const coordinates = [[
            [minLng, minLat], [maxLng, minLat],
            [maxLng, maxLat], [minLng, maxLat],
            [minLng, minLat],
        ]];

        KWebSocket.send('overlay_create', {
            overlay_type: 'rectangle',
            geometry: { type: 'Polygon', coordinates },
            style_json: { color, weight: 2, dashArray: '8,6', fillOpacity: 0.08 },
        });

        _stopDraw();
    }

    // ══════════════════════════════════════════════════
    // ── Ellipse (click center + click edge, dashed) ──
    // ══════════════════════════════════════════════════

    function _onEllipseClick(e) {
        if (activeTool !== 'ellipse') return;
        drawPoints.push([e.latlng.lat, e.latlng.lng]);

        if (drawPoints.length === 1) {
            const color = _getDrawColor();
            previewGroup.addLayer(L.circleMarker(e.latlng, {
                radius: 4, color, fillColor: color, fillOpacity: 0.8, weight: 2,
            }));
        } else if (drawPoints.length >= 2) {
            _finalizeEllipse();
        }
    }

    function _onEllipseMouseMove(e) {
        if (activeTool !== 'ellipse' || drawPoints.length !== 1) return;
        const color = _getDrawColor();
        const center = drawPoints[0];
        const edge = [e.latlng.lat, e.latlng.lng];
        const pts = _computeEllipsePoints(center, edge, 48);

        if (_previewShape) {
            _previewShape.setLatLngs(pts);
        } else {
            _previewShape = L.polygon(pts, {
                color, weight: 2, dashArray: '8,6', fillOpacity: 0.08,
            });
            previewGroup.addLayer(_previewShape);
        }
    }

    function _onEllipseCancel(e) {
        if (activeTool !== 'ellipse') return;
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        cancelDraw();
    }

    function _finalizeEllipse() {
        const center = drawPoints[0], edge = drawPoints[1];
        const color = _getDrawColor();
        const pts = _computeEllipsePoints(center, edge, 48);

        // Convert to GeoJSON Polygon [lng, lat]
        const ring = pts.map(p => [p[1], p[0]]);
        ring.push(ring[0]); // close ring

        KWebSocket.send('overlay_create', {
            overlay_type: 'circle',  // stored as circle type, geometry is polygon
            geometry: { type: 'Polygon', coordinates: [ring] },
            style_json: { color, weight: 2, dashArray: '8,6', fillOpacity: 0.08 },
            properties: { is_ellipse: true, center, edge },
        });

        _stopDraw();
    }

    /** Compute ellipse polygon points. Semi-axes from center→edge delta. */
    function _computeEllipsePoints(center, edge, numPts) {
        const dLat = Math.abs(edge[0] - center[0]);
        const dLng = Math.abs(edge[1] - center[1]);
        const semiA = dLng || dLat * 0.5;
        const semiB = dLat || dLng * 0.5;

        const pts = [];
        for (let i = 0; i < numPts; i++) {
            const a = (2 * Math.PI * i) / numPts;
            pts.push([
                center[0] + semiB * Math.sin(a),
                center[1] + semiA * Math.cos(a),
            ]);
        }
        return pts;
    }

    // ══════════════════════════════════════════════════
    // ── Marker (single click) ────────────────────────
    // ══════════════════════════════════════════════════

    function _onMarkerClick(e) {
        if (activeTool !== 'marker') return;

        KWebSocket.send('overlay_create', {
            overlay_type: 'marker',
            geometry: { type: 'Point', coordinates: [e.latlng.lng, e.latlng.lat] },
            style_json: {},
        });

        cancelDraw();
    }

    // ══════════════════════════════════════════════════
    // ── Catmull-Rom Spline Interpolation ─────────────
    // ══════════════════════════════════════════════════

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
            const p0 = pts[i - 1], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2];
            for (let t = 0; t < 1; t += 1 / numPerSegment) {
                const t2 = t * t, t3 = t2 * t;
                result.push([
                    0.5 * ((2*p1[0]) + (-p0[0]+p2[0])*t + (2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2 + (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3),
                    0.5 * ((2*p1[1]) + (-p0[1]+p2[1])*t + (2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2 + (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3),
                ]);
            }
        }
        result.push(points[points.length - 1]);
        return result;
    }

    // ══════════════════════════════════════════════════
    // ── Arrowhead Rendering ─────────────────────────
    // ══════════════════════════════════════════════════

    function _createArrowhead(latlngs, style = {}) {
        if (latlngs.length < 2) return null;
        const tip = latlngs[latlngs.length - 1];
        const prev = latlngs[latlngs.length - 2];
        const dLat = tip[0] - prev[0];
        const dLng = tip[1] - prev[1];
        const angle = Math.atan2(dLng, dLat);
        const size = 0.0008;
        const spread = 0.45;
        const left = [tip[0] - size * Math.cos(angle - spread), tip[1] - size * Math.sin(angle - spread)];
        const right = [tip[0] - size * Math.cos(angle + spread), tip[1] - size * Math.sin(angle + spread)];
        const color = style.color || BLUE_COLOR;
        return L.polygon([tip, left, right], {
            color, fillColor: color, fillOpacity: 0.9, weight: 1, interactive: true,
        });
    }

    // ══════════════════════════════════════════════════
    // ── Server Overlay Rendering ─────────────────────
    // ══════════════════════════════════════════════════

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
        overlayDataMap = {};
        overlays.forEach(o => addOverlayToMap(o));
    }

    function addOverlayToMap(overlay) {
        if (!overlay.geometry || !overlaysLayer) return;

        // Clear preview since server overlay replaces it
        previewGroup.clearLayers();

        const color = _getOverlayColor(overlay);
        const style = overlay.style_json ? { ...overlay.style_json } : {};
        style.color = style.color || color;
        let layer = null;

        try {
            if (overlay.overlay_type === 'arrow' && overlay.geometry.type === 'LineString') {
                const coords = overlay.geometry.coordinates.map(c => [c[1], c[0]]);
                const group = L.layerGroup();
                group.addLayer(L.polyline(coords, {
                    color: style.color, weight: style.weight || 3, opacity: style.opacity || 0.9,
                }));
                const ah = _createArrowhead(coords, style);
                if (ah) group.addLayer(ah);
                layer = group;
            } else if (overlay.overlay_type === 'polyline' && overlay.geometry.type === 'LineString') {
                const coords = overlay.geometry.coordinates.map(c => [c[1], c[0]]);
                layer = L.polyline(coords, {
                    color: style.color, weight: style.weight || 3, opacity: style.opacity || 0.9,
                });
            } else if (overlay.overlay_type === 'marker' && overlay.geometry.type === 'Point') {
                const c = overlay.geometry.coordinates;
                layer = L.marker([c[1], c[0]]);
                // If marker has a label, show as permanent tooltip
                if (overlay.label) {
                    layer.bindTooltip(overlay.label, {
                        permanent: true,
                        direction: 'top',
                        offset: [0, -20],
                        className: 'overlay-marker-label',
                    });
                }
            } else if (overlay.geometry.type === 'Polygon') {
                const coords = overlay.geometry.coordinates[0].map(c => [c[1], c[0]]);
                layer = L.polygon(coords, {
                    color: style.color || color,
                    weight: style.weight || 2,
                    dashArray: style.dashArray || null,
                    fillOpacity: style.fillOpacity || 0.08,
                });
            } else {
                layer = L.geoJSON(overlay.geometry, {
                    style: () => style,
                    pointToLayer: (f, ll) => L.marker(ll),
                });
            }

            if (layer) {
                // Right-click → context menu
                const ctxHandler = (e) => {
                    L.DomEvent.stopPropagation(e);
                    L.DomEvent.preventDefault(e);
                    const origEvt = e.originalEvent || e;
                    _showCtxMenu(overlay.id, origEvt.clientX, origEvt.clientY);
                };

                if (layer instanceof L.LayerGroup) {
                    layer.eachLayer(sub => sub.on('contextmenu', ctxHandler));
                } else {
                    layer.on('contextmenu', ctxHandler);
                }

                overlaysLayer.addLayer(layer);
                overlayMap[overlay.id] = layer;
                overlayDataMap[overlay.id] = overlay;
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
        if (data.id && overlayMap[data.id]) {
            overlaysLayer.removeLayer(overlayMap[data.id]);
            delete overlayMap[data.id];
            delete overlayDataMap[data.id];
        }
        addOverlayToMap(data);
    }

    function onOverlayDeleted(data) {
        const id = data.overlay_id || data.id;
        if (id && overlayMap[id]) {
            overlaysLayer.removeLayer(overlayMap[id]);
            delete overlayMap[id];
            delete overlayDataMap[id];
        }
    }

    return {
        init, setSession, startDraw, cancelDraw, isDrawing,
        loadFromServer, render, toggle, isVisible,
        onOverlayCreated, onOverlayUpdated, onOverlayDeleted,
    };
})();
