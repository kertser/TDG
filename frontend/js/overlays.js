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

    // ── Shape Edit Mode state ────────────────────────
    let _editState = null;   // null or {overlayId, shapeType, params, layer, handleGroup, ...}
    let _editHandleInteracting = false;

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
                if (_editState) {
                    // Revert to original params and exit without saving
                    _editState.params = JSON.parse(JSON.stringify(_editState.originalParams));
                    _exitEditMode(false);
                } else {
                    cancelDraw();
                }
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
                if (_editState && _editState.overlayId === _ctxOverlayId) _exitEditMode(false);
                KWebSocket.send('overlay_delete', { overlay_id: _ctxOverlayId });
            }
            _hideCtxMenu();
        });

        // Edit shape button
        const editBtn = _ctxMenu.querySelector('[data-action="edit"]');
        if (editBtn) {
            editBtn.addEventListener('click', () => {
                if (_ctxOverlayId) _enterEditMode(_ctxOverlayId);
                _hideCtxMenu();
            });
        }

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

        // Edit Shape button — only for rectangle/ellipse, and only if user can edit
        const editSection = _ctxMenu.querySelector('.ctx-section-edit');
        if (editSection) {
            const isEditable = (overlayType === 'rectangle' || overlayType === 'circle') && _canEditOverlays();
            editSection.style.display = isEditable ? 'block' : 'none';
        }

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

        // Show drawing tools group (inside map control overlay)
        const drawGroup = document.getElementById('map-draw-group');
        if (drawGroup) drawGroup.style.display = '';
    }

    // ══════════════════════════════════════════════════
    // ── Drawing Tool Entry Points ────────────────────
    // ══════════════════════════════════════════════════

    function isDrawing() {
        return activeTool !== null;
    }

    function startDraw(type) {
        if (!map) return;
        if (_editState) _exitEditMode(true); // save and exit any active edit
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

        // Store parametric form for editing (resize, rotate, move)
        const rect_params = {
            center: [(minLat + maxLat) / 2, (minLng + maxLng) / 2],
            halfW: (maxLng - minLng) / 2,
            halfH: (maxLat - minLat) / 2,
            rotation: 0,
        };

        KWebSocket.send('overlay_create', {
            overlay_type: 'rectangle',
            geometry: { type: 'Polygon', coordinates },
            style_json: { color, weight: 2, dashArray: '8,6', fillOpacity: 0.08 },
            properties: { rect_params },
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

        const dLat = Math.abs(edge[0] - center[0]);
        const dLng = Math.abs(edge[1] - center[1]);
        const ellipse_params = {
            center: [...center],
            semiA: dLng || dLat * 0.5,
            semiB: dLat || dLng * 0.5,
            rotation: 0,
        };

        KWebSocket.send('overlay_create', {
            overlay_type: 'circle',  // stored as circle type, geometry is polygon
            geometry: { type: 'Polygon', coordinates: [ring] },
            style_json: { color, weight: 2, dashArray: '8,6', fillOpacity: 0.08 },
            properties: { is_ellipse: true, center, edge, ellipse_params },
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
    // ── Shape Edit Mode (Resize / Rotate / Move) ─────
    // ══════════════════════════════════════════════════
    //
    // Entry: double-click on a rectangle/ellipse overlay, or context menu "Edit Shape".
    // Exit:  ESC (revert), click outside (save), start draw tool (save).
    // Handles: corner/axis handles for resize, rotation handle, body drag for move.

    // ── Coordinate transforms: local (x=east, y=north) ↔ geo ──

    function _localToGeo(x, y, center, rotation) {
        const c = Math.cos(rotation), s = Math.sin(rotation);
        // x is in lng-degree units, y is in lat-degree units.
        // Equalize to physical scale before rotation, then convert back.
        const cosLat = Math.cos(center[0] * Math.PI / 180);
        const eqX = x * cosLat;          // now same physical scale as y
        const rotEqX = eqX * c - y * s;  // rotated east (equalized)
        const rotY   = eqX * s + y * c;  // rotated north
        return [
            center[0] + rotY,                // lat offset is direct
            center[1] + rotEqX / cosLat,     // lng offset: convert back from equalized
        ];
    }

    function _geoToLocal(lat, lng, center, rotation) {
        const cosLat = Math.cos(center[0] * Math.PI / 180);
        const eqDx = (lng - center[1]) * cosLat;  // equalize lng to match lat scale
        const dy   = lat - center[0];
        const c = Math.cos(rotation), s = Math.sin(rotation);
        // Inverse rotation in equalized space
        const localEqX =  eqDx * c + dy * s;
        const localY   = -eqDx * s + dy * c;
        return { x: localEqX / cosLat, y: localY };  // convert eqX back to lng-degrees
    }

    // ── Polygon computation from params ──

    function _rectPolygonFromParams(p) {
        return [
            _localToGeo(-p.halfW, +p.halfH, p.center, p.rotation),
            _localToGeo(+p.halfW, +p.halfH, p.center, p.rotation),
            _localToGeo(+p.halfW, -p.halfH, p.center, p.rotation),
            _localToGeo(-p.halfW, -p.halfH, p.center, p.rotation),
        ];
    }

    function _ellipsePolygonFromParams(p, n = 48) {
        const pts = [];
        for (let i = 0; i < n; i++) {
            const a = (2 * Math.PI * i) / n;
            pts.push(_localToGeo(p.semiA * Math.cos(a), p.semiB * Math.sin(a), p.center, p.rotation));
        }
        return pts;
    }

    // ── Extract params from existing overlay data ──

    function _extractShapeParams(overlay) {
        if (overlay.overlay_type === 'rectangle') {
            if (overlay.properties?.rect_params) {
                const rp = overlay.properties.rect_params;
                return { center: [...rp.center], halfW: rp.halfW, halfH: rp.halfH, rotation: rp.rotation || 0 };
            }
            const coords = overlay.geometry.coordinates[0];
            const lats = coords.map(c => c[1]), lngs = coords.map(c => c[0]);
            const mnLat = Math.min(...lats), mxLat = Math.max(...lats);
            const mnLng = Math.min(...lngs), mxLng = Math.max(...lngs);
            return { center: [(mnLat + mxLat) / 2, (mnLng + mxLng) / 2], halfW: (mxLng - mnLng) / 2, halfH: (mxLat - mnLat) / 2, rotation: 0 };
        }
        if (overlay.overlay_type === 'circle') {
            if (overlay.properties?.ellipse_params) {
                const ep = overlay.properties.ellipse_params;
                return { center: [...ep.center], semiA: ep.semiA, semiB: ep.semiB, rotation: ep.rotation || 0 };
            }
            const ct = overlay.properties?.center, ed = overlay.properties?.edge;
            if (ct && ed) {
                const dLat = Math.abs(ed[0] - ct[0]), dLng = Math.abs(ed[1] - ct[1]);
                return { center: [...ct], semiA: dLng || dLat * 0.5, semiB: dLat || dLng * 0.5, rotation: 0 };
            }
            const coords = overlay.geometry.coordinates[0];
            const lats = coords.map(c => c[1]), lngs = coords.map(c => c[0]);
            return {
                center: [(Math.min(...lats) + Math.max(...lats)) / 2, (Math.min(...lngs) + Math.max(...lngs)) / 2],
                semiA: (Math.max(...lngs) - Math.min(...lngs)) / 2,
                semiB: (Math.max(...lats) - Math.min(...lats)) / 2,
                rotation: 0,
            };
        }
        return null;
    }

    function _canEditOverlays() {
        const role = typeof KSessionUI !== 'undefined' ? KSessionUI.getRole() : null;
        const side = typeof KSessionUI !== 'undefined' ? KSessionUI.getSide() : null;
        return role !== 'observer' && side !== 'observer';
    }

    // ── Enter / Exit Edit Mode ──

    function _enterEditMode(overlayId) {
        if (_editState) _exitEditMode(true);
        if (activeTool) cancelDraw();
        if (!_canEditOverlays()) return;

        const data = overlayDataMap[overlayId];
        if (!data) return;
        const isRect = data.overlay_type === 'rectangle';
        const isEllipse = data.overlay_type === 'circle';
        if (!isRect && !isEllipse) return;

        const params = _extractShapeParams(data);
        if (!params) return;

        const style = data.style_json || {};
        const color = style.color || BLUE_COLOR;

        // Remove the existing rendered overlay temporarily
        if (overlayMap[overlayId]) {
            overlaysLayer.removeLayer(overlayMap[overlayId]);
        }

        // Create editable shape with highlight
        const pts = isRect ? _rectPolygonFromParams(params) : _ellipsePolygonFromParams(params);
        const editLayer = L.polygon(pts, {
            color: '#4fc3f7', weight: 2, dashArray: '6,4',
            fillOpacity: 0.12, fillColor: color, interactive: true,
            className: 'overlay-editing',
        });
        editLayer.addTo(map);

        // Right-click on edit shape → context menu still works
        editLayer.on('contextmenu', (e) => {
            L.DomEvent.stopPropagation(e);
            L.DomEvent.preventDefault(e);
            _showCtxMenu(overlayId, e.originalEvent.clientX, e.originalEvent.clientY);
        });

        const handleGroup = L.layerGroup().addTo(map);

        _editState = {
            overlayId,
            shapeType: isRect ? 'rectangle' : 'ellipse',
            params,
            originalParams: JSON.parse(JSON.stringify(params)),
            style,
            layer: editLayer,
            handleGroup,
            handles: {},
            rotLine: null,
            isDragging: false,
            dragStartLatLng: null,
            dragStartCenter: null,
            activeDragKey: null, // key of handle currently being dragged (skip its setLatLng)
        };

        _createEditHandles();

        // Shape body drag
        editLayer.on('mousedown', _onShapeMouseDown);

        // Map mousedown outside edit UI → exit edit mode (with flag-based protection)
        _editHandleInteracting = false;
        setTimeout(() => {
            if (_editState?.overlayId === overlayId) {
                map.on('mousedown', _onMapMouseDownDuringEdit);
            }
        }, 300);
    }

    function _exitEditMode(save = true) {
        if (!_editState) return;

        map.off('mousedown', _onMapMouseDownDuringEdit);
        map.off('mousemove', _onShapeDragMove);
        map.off('mouseup', _onShapeDragEnd);

        if (save) _sendEditUpdate();

        // Clean up edit layers
        if (_editState.layer) map.removeLayer(_editState.layer);
        if (_editState.handleGroup) map.removeLayer(_editState.handleGroup);
        if (_editState.rotLine) map.removeLayer(_editState.rotLine);

        // Re-render the overlay from data
        const oid = _editState.overlayId;
        const data = overlayDataMap[oid];
        _editState = null;
        if (data) addOverlayToMap(data);
        map.dragging.enable();
    }

    function isEditing() {
        return _editState !== null;
    }

    function _onMapMouseDownDuringEdit() {
        // Check after a delay — if a handle/shape mousedown set the flag, don't exit.
        // The flag is reset AFTER the check (not before) to avoid a race condition
        // where the map handler resets it after a handle already set it.
        setTimeout(() => {
            if (!_editHandleInteracting && _editState && !_editState.isDragging) {
                _exitEditMode(true);
            }
            _editHandleInteracting = false;
        }, 150);
    }

    // ── Send update to server ──

    function _sendEditUpdate() {
        if (!_editState) return;
        const { overlayId, shapeType, params } = _editState;
        const isRect = shapeType === 'rectangle';
        const pts = isRect ? _rectPolygonFromParams(params) : _ellipsePolygonFromParams(params);
        const ring = pts.map(p => [p[1], p[0]]);
        ring.push(ring[0]);
        const geometry = { type: 'Polygon', coordinates: [ring] };

        // Merge with existing properties to preserve other fields
        const existing = overlayDataMap[overlayId]?.properties || {};
        const properties = isRect
            ? { ...existing, rect_params: { ...params } }
            : { ...existing, is_ellipse: true, center: params.center, ellipse_params: { ...params } };

        KWebSocket.send('overlay_update', { overlay_id: overlayId, geometry, properties });
        if (overlayDataMap[overlayId]) {
            overlayDataMap[overlayId].geometry = geometry;
            overlayDataMap[overlayId].properties = properties;
        }
    }

    // ── Handle Creation ──

    function _createEditHandles() {
        if (_editState.shapeType === 'rectangle') _createRectHandles();
        else _createEllipseHandles();
        _createRotationHandle();
    }

    function _makeHandle(pos, cls, size) {
        cls = cls || 'overlay-edit-handle';
        size = size || 12;
        const h = L.marker(pos, {
            draggable: true,
            icon: L.divIcon({ className: cls, iconSize: [size, size], iconAnchor: [size / 2, size / 2] }),
            zIndexOffset: 1000,
        });
        h.on('mousedown', () => { _editHandleInteracting = true; });
        return h;
    }

    function _createRectHandles() {
        const { params, handleGroup } = _editState;
        const { center, halfW, halfH, rotation } = params;
        const defs = {
            tl: { local: [-halfW, +halfH], opp: 'br' },
            tr: { local: [+halfW, +halfH], opp: 'bl' },
            br: { local: [+halfW, -halfH], opp: 'tl' },
            bl: { local: [-halfW, -halfH], opp: 'tr' },
        };
        for (const [key, def] of Object.entries(defs)) {
            const pos = _localToGeo(def.local[0], def.local[1], center, rotation);
            const h = _makeHandle(pos);
            h._editKey = key;
            h._oppKey = def.opp;
            h.on('dragstart', () => { if (_editState) _editState.activeDragKey = key; });
            h.on('drag', _onRectCornerDrag);
            h.on('dragend', () => { if (_editState) _editState.activeDragKey = null; _sendEditUpdate(); });
            handleGroup.addLayer(h);
            _editState.handles[key] = h;
        }
    }

    function _createEllipseHandles() {
        const { params, handleGroup } = _editState;
        const { center, semiA, semiB, rotation } = params;
        const defs = {
            r: [+semiA, 0], l: [-semiA, 0],
            t: [0, +semiB], b: [0, -semiB],
        };
        for (const [key, local] of Object.entries(defs)) {
            const pos = _localToGeo(local[0], local[1], center, rotation);
            const h = _makeHandle(pos);
            h._editKey = key;
            h.on('dragstart', () => { if (_editState) _editState.activeDragKey = key; });
            h.on('drag', _onEllipseAxisDrag);
            h.on('dragend', () => { if (_editState) _editState.activeDragKey = null; _sendEditUpdate(); });
            handleGroup.addLayer(h);
            _editState.handles[key] = h;
        }
    }

    function _createRotationHandle() {
        const { params, shapeType, handleGroup } = _editState;
        const { center, rotation } = params;
        const topVal = shapeType === 'rectangle' ? params.halfH : params.semiB;
        const maxDim = shapeType === 'rectangle' ? Math.max(params.halfW, params.halfH) : Math.max(params.semiA, params.semiB);
        const offset = Math.max(maxDim * 0.3, 0.0005);
        const topDist = topVal + offset;

        const rotPos = _localToGeo(0, topDist, center, rotation);
        const shapeTopPos = _localToGeo(0, topVal, center, rotation);

        const rotLine = L.polyline([shapeTopPos, rotPos], {
            color: '#4fc3f7', weight: 1.5, dashArray: '4,4', opacity: 0.7, interactive: false,
        }).addTo(map);
        _editState.rotLine = rotLine;

        const h = _makeHandle(rotPos, 'overlay-edit-handle-rotate', 16);
        h.on('dragstart', () => { if (_editState) _editState.activeDragKey = 'rotate'; });
        h.on('drag', _onRotationDrag);
        h.on('dragend', () => { if (_editState) _editState.activeDragKey = null; _sendEditUpdate(); });
        handleGroup.addLayer(h);
        _editState.handles.rotate = h;
    }

    // ── Handle Drag Handlers ──

    function _onRectCornerDrag(e) {
        const pos = e.target.getLatLng();
        const oppPos = _editState.handles[e.target._oppKey].getLatLng();
        const { rotation } = _editState.params;

        // New center = midpoint between dragged corner and its opposite (which stays fixed)
        const newCenter = [(pos.lat + oppPos.lat) / 2, (pos.lng + oppPos.lng) / 2];
        const local = _geoToLocal(pos.lat, pos.lng, newCenter, rotation);
        _editState.params.center = newCenter;
        _editState.params.halfW = Math.max(Math.abs(local.x), 0.00002);
        _editState.params.halfH = Math.max(Math.abs(local.y), 0.00002);
        _refreshEditVisuals();
    }

    function _onEllipseAxisDrag(e) {
        const pos = e.target.getLatLng();
        const key = e.target._editKey;
        const { center, rotation } = _editState.params;
        const local = _geoToLocal(pos.lat, pos.lng, center, rotation);

        if (key === 'r' || key === 'l') {
            _editState.params.semiA = Math.max(Math.abs(local.x), 0.00002);
        } else {
            _editState.params.semiB = Math.max(Math.abs(local.y), 0.00002);
        }
        _refreshEditVisuals();
    }

    function _onRotationDrag(e) {
        const pos = e.target.getLatLng();
        const { center } = _editState.params;
        const dx = pos.lng - center[1], dy = pos.lat - center[0];
        _editState.params.rotation = Math.atan2(-dx, dy);
        _refreshEditVisuals();
    }

    // ── Shape Body Drag (move) ──

    function _onShapeMouseDown(e) {
        if (!_editState || e.originalEvent.button !== 0) return;
        L.DomEvent.stop(e);
        _editHandleInteracting = true;

        _editState.isDragging = true;
        _editState.dragStartLatLng = { lat: e.latlng.lat, lng: e.latlng.lng };
        _editState.dragStartCenter = [..._editState.params.center];
        map.dragging.disable();
        map.on('mousemove', _onShapeDragMove);
        map.on('mouseup', _onShapeDragEnd);
    }

    function _onShapeDragMove(e) {
        if (!_editState?.isDragging) return;
        _editState.params.center = [
            _editState.dragStartCenter[0] + (e.latlng.lat - _editState.dragStartLatLng.lat),
            _editState.dragStartCenter[1] + (e.latlng.lng - _editState.dragStartLatLng.lng),
        ];
        _refreshEditVisuals();
    }

    function _onShapeDragEnd() {
        if (!_editState) return;
        map.off('mousemove', _onShapeDragMove);
        map.off('mouseup', _onShapeDragEnd);
        map.dragging.enable();
        _editState.isDragging = false;
        _sendEditUpdate();
    }

    // ── Refresh all edit visuals (shape + handles + rotation line) ──

    function _refreshEditVisuals() {
        const { shapeType, params, layer, handles, rotLine, activeDragKey } = _editState;

        // Update polygon
        const pts = shapeType === 'rectangle' ? _rectPolygonFromParams(params) : _ellipsePolygonFromParams(params);
        layer.setLatLngs(pts);

        // Update resize handles — skip the one being dragged to avoid feedback loop
        if (shapeType === 'rectangle') {
            const { center, halfW, halfH, rotation } = params;
            const pos = {
                tl: _localToGeo(-halfW, +halfH, center, rotation),
                tr: _localToGeo(+halfW, +halfH, center, rotation),
                br: _localToGeo(+halfW, -halfH, center, rotation),
                bl: _localToGeo(-halfW, -halfH, center, rotation),
            };
            for (const [k, p] of Object.entries(pos)) {
                if (handles[k] && k !== activeDragKey) handles[k].setLatLng(p);
            }
        } else {
            const { center, semiA, semiB, rotation } = params;
            const pos = {
                r: _localToGeo(+semiA, 0, center, rotation),
                l: _localToGeo(-semiA, 0, center, rotation),
                t: _localToGeo(0, +semiB, center, rotation),
                b: _localToGeo(0, -semiB, center, rotation),
            };
            for (const [k, p] of Object.entries(pos)) {
                if (handles[k] && k !== activeDragKey) handles[k].setLatLng(p);
            }
        }

        // Update rotation handle + dashed line — skip handle if it's being dragged
        if (handles.rotate) {
            const { center, rotation } = params;
            const topVal = shapeType === 'rectangle' ? params.halfH : params.semiB;
            const maxDim = shapeType === 'rectangle' ? Math.max(params.halfW, params.halfH) : Math.max(params.semiA, params.semiB);
            const topDist = topVal + Math.max(maxDim * 0.3, 0.0005);
            if (activeDragKey !== 'rotate') {
                handles.rotate.setLatLng(_localToGeo(0, topDist, center, rotation));
            }
            if (rotLine) {
                const shapeTop = _localToGeo(0, topVal, center, rotation);
                // Line end: use actual handle position during drag, computed position otherwise
                const lineEnd = activeDragKey === 'rotate'
                    ? [handles.rotate.getLatLng().lat, handles.rotate.getLatLng().lng]
                    : _localToGeo(0, topDist, center, rotation);
                rotLine.setLatLngs([shapeTop, lineEnd]);
            }
        }
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
                // Label at midpoint for arrows
                if (overlay.label && coords.length >= 2) {
                    const mid = coords[Math.floor(coords.length / 2)];
                    const lbl = L.marker(mid, {
                        icon: L.divIcon({ className: 'overlay-shape-label', html: overlay.label, iconSize: [0, 0] }),
                        interactive: false,
                    });
                    group.addLayer(lbl);
                }
                layer = group;
            } else if (overlay.overlay_type === 'polyline' && overlay.geometry.type === 'LineString') {
                const coords = overlay.geometry.coordinates.map(c => [c[1], c[0]]);
                layer = L.polyline(coords, {
                    color: style.color, weight: style.weight || 3, opacity: style.opacity || 0.9,
                });
                // Label at midpoint for polylines
                if (overlay.label && coords.length >= 2) {
                    const mid = coords[Math.floor(coords.length / 2)];
                    const lbl = L.marker(mid, {
                        icon: L.divIcon({ className: 'overlay-shape-label', html: overlay.label, iconSize: [0, 0] }),
                        interactive: false,
                    });
                    // Wrap in layerGroup so both line and label travel together
                    const group = L.layerGroup();
                    group.addLayer(layer);
                    group.addLayer(lbl);
                    layer = group;
                }
            } else if (overlay.overlay_type === 'marker' && overlay.geometry.type === 'Point') {
                const c = overlay.geometry.coordinates;
                // Make markers draggable for non-observer users
                const role = typeof KSessionUI !== 'undefined' ? KSessionUI.getRole() : null;
                const side = typeof KSessionUI !== 'undefined' ? KSessionUI.getSide() : null;
                const canDrag = role !== 'observer' && side !== 'observer';
                layer = L.marker([c[1], c[0]], { draggable: canDrag });
                // If marker has a label, show as permanent tooltip
                if (overlay.label) {
                    layer.bindTooltip(overlay.label, {
                        permanent: true,
                        direction: 'top',
                        offset: [0, -20],
                        className: 'overlay-marker-label',
                    });
                }
                // Send position update on drag end
                if (canDrag) {
                    layer.on('dragend', () => {
                        const pos = layer.getLatLng();
                        const newGeom = { type: 'Point', coordinates: [pos.lng, pos.lat] };
                        if (typeof KWebSocket !== 'undefined') {
                            KWebSocket.send('overlay_update', { overlay_id: overlay.id, geometry: newGeom });
                        }
                        // Update local data
                        if (overlayDataMap[overlay.id]) {
                            overlayDataMap[overlay.id].geometry = newGeom;
                        }
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
                // Label at centroid for rectangles, ellipses, polygons
                if (overlay.label) {
                    layer.bindTooltip(overlay.label, {
                        permanent: true,
                        direction: 'center',
                        className: 'overlay-shape-label',
                    });
                }
                // Double-click to enter edit mode for rectangles/ellipses
                if ((overlay.overlay_type === 'rectangle' || overlay.overlay_type === 'circle') && _canEditOverlays()) {
                    layer.on('dblclick', (e) => {
                        L.DomEvent.stop(e);
                        _enterEditMode(overlay.id);
                    });
                }
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
        // If we're editing this overlay, just update data — don't re-render
        if (_editState && _editState.overlayId === data.id) {
            overlayDataMap[data.id] = data;
            return;
        }
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

    /** Clear all overlays (used on logout). */
    function clearAll() {
        if (_editState) _exitEditMode(false);
        cancelDraw();
        if (overlaysLayer) overlaysLayer.clearLayers();
        overlayMap = {};
        overlayDataMap = {};
        sessionId = null;
        token = null;
    }

    return {
        init, setSession, startDraw, cancelDraw, isDrawing, isEditing,
        loadFromServer, render, toggle, isVisible,
        onOverlayCreated, onOverlayUpdated, onOverlayDeleted,
        clearAll,
        enterEditMode: _enterEditMode,
    };
})();
