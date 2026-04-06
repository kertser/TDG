/**
 * units.js – Fetch and render visible units on the map with military symbols.
 *
 *  Selection:
 *    Left-click        = select unit (replaces previous selection).
 *    Shift+left-click  = add/remove unit from selection.
 *    Right-click       = open detail popup.
 *    Left-drag on map  = rubber-band mass selection.
 *
 *  On selection, the following overlays are shown per-unit:
 *    • Detection/visibility range  (outer dashed circle)
 *    • Effective fire range        (inner dashed circle, amber)
 *    • Heading indicator           (if unit is stationary but has heading)
 *
 *  Movement arrows are always shown for units with movement tasks,
 *  drawn from unit center on a pane beneath unit markers.
 *
 *  Assignment: user can only select units assigned to them (or unassigned).
 */
const KUnits = (() => {
    let unitMarkers = {};          // unit_id → Leaflet marker
    let unitsLayer = null;         // L.layerGroup for unit markers
    let _selectionLayer = null;    // L.layerGroup for selection overlays (range, direction)
    let _hoverLayer = null;        // L.layerGroup for hover range circles
    let _movementArrowsLayer = null; // L.layerGroup for movement arrows (below markers)
    let allUnitsData = [];
    let selectedUnitIds = new Set();
    let _map = null;
    let _visible = true;
    let _hoveredUnitId = null;     // currently hovered unit ID

    // ── Rubber-band selection state ──────────────────
    let _selectRect = null;
    let _selectStartPt = null;
    let _selectStartLL = null;
    let _isSelecting = false;
    let _shiftHeld = false;
    const SELECT_THRESHOLD = 6;

    // ── Fire range by unit type (meters) ─────────────
    const FIRE_RANGE = {
        'tank_company':      2500,
        'mech_company':      1500,
        'infantry_company':  800,
        'infantry_platoon':  600,
        'mortar_section':    3500,
        'at_team':           2000,
        'recon_team':        400,
        'observation_post':  300,
        'sniper_team':       1000,
    };
    const DEFAULT_FIRE_RANGE = 500;

    function init(map) {
        _map = map;

        // Create a custom pane for movement arrows below the default marker pane (z=600)
        map.createPane('movementArrowsPane');
        map.getPane('movementArrowsPane').style.zIndex = 350;

        unitsLayer = L.layerGroup().addTo(map);
        _selectionLayer = L.layerGroup().addTo(map);
        _hoverLayer = L.layerGroup().addTo(map);
        _movementArrowsLayer = L.layerGroup().addTo(map);
        _initRubberBandSelection();
    }

    // ══════════════════════════════════════════════════
    // ── Permission Helpers ────────────────────────────
    // ══════════════════════════════════════════════════

    /** Can the current user select/command this unit? */
    function _canSelect(unit) {
        const userId = KSessionUI.getUserId();
        if (!userId) return false;
        // If unit has no assignments, anyone on the same side can select
        if (!unit.assigned_user_ids || unit.assigned_user_ids.length === 0) return true;
        // If assigned to this user
        if (unit.assigned_user_ids.includes(userId)) return true;
        // Check if user has command authority via parent chain
        return _hasCommandAuthority(unit, userId);
    }

    /** Can the current user assign/unassign this unit? */
    function _canAssign(unit) {
        const userId = KSessionUI.getUserId();
        if (!userId) return false;
        if (!unit.assigned_user_ids || unit.assigned_user_ids.length === 0) return true;
        if (unit.assigned_user_ids.includes(userId)) return true;
        // Check command authority via parent chain
        return _hasCommandAuthority(unit, userId);
    }

    /** Check if user has authority over unit via ancestor chain */
    function _hasCommandAuthority(unit, userId) {
        const unitMap = {};
        allUnitsData.forEach(u => { unitMap[u.id] = u; });

        let parentId = unit.parent_unit_id;
        const visited = new Set();
        while (parentId && unitMap[parentId]) {
            if (visited.has(parentId)) break;
            visited.add(parentId);
            const parent = unitMap[parentId];
            if (parent.assigned_user_ids && parent.assigned_user_ids.includes(userId)) {
                return true;
            }
            parentId = parent.parent_unit_id;
        }
        return false;
    }

    // ══════════════════════════════════════════════════
    // ── Visibility Toggle ────────────────────────────
    // ══════════════════════════════════════════════════

    function toggle() {
        _visible = !_visible;
        if (_map) {
            if (_visible) {
                if (unitsLayer && !_map.hasLayer(unitsLayer)) _map.addLayer(unitsLayer);
                if (_selectionLayer && !_map.hasLayer(_selectionLayer)) _map.addLayer(_selectionLayer);
                if (_hoverLayer && !_map.hasLayer(_hoverLayer)) _map.addLayer(_hoverLayer);
                if (_movementArrowsLayer && !_map.hasLayer(_movementArrowsLayer)) _map.addLayer(_movementArrowsLayer);
            } else {
                if (unitsLayer && _map.hasLayer(unitsLayer)) _map.removeLayer(unitsLayer);
                if (_selectionLayer && _map.hasLayer(_selectionLayer)) _map.removeLayer(_selectionLayer);
                if (_hoverLayer && _map.hasLayer(_hoverLayer)) _map.removeLayer(_hoverLayer);
                if (_movementArrowsLayer && _map.hasLayer(_movementArrowsLayer)) _map.removeLayer(_movementArrowsLayer);
            }
        }
        return _visible;
    }

    function isVisible() { return _visible; }

    // ══════════════════════════════════════════════════
    // ── Rubber-band Mass Selection ───────────────────
    // ══════════════════════════════════════════════════

    function _initRubberBandSelection() {
        const container = _map.getContainer();

        container.addEventListener('pointerdown', (e) => {
            if (e.button !== 0) return;
            if (KOverlays.isDrawing()) return;
            if (KMap.isMeasuring()) return;
            if (e.target.closest('.leaflet-marker-icon') ||
                e.target.closest('.leaflet-marker-shadow') ||
                e.target.closest('.leaflet-popup') ||
                e.target.closest('.leaflet-interactive') ||
                e.target.closest('.leaflet-control') ||
                e.target.closest('#topbar') ||
                e.target.closest('#sidebar') ||
                e.target.closest('.ctx-menu')) return;

            const rect = container.getBoundingClientRect();
            _selectStartPt = { x: e.clientX, y: e.clientY };
            _selectStartLL = _map.containerPointToLatLng(
                L.point(e.clientX - rect.left, e.clientY - rect.top)
            );
            _shiftHeld = e.shiftKey;
            _isSelecting = false;
        });

        container.addEventListener('pointermove', (e) => {
            if (!_selectStartPt) return;
            if (!(e.buttons & 1)) { _cancelRubberBand(); return; }

            const dx = e.clientX - _selectStartPt.x;
            const dy = e.clientY - _selectStartPt.y;
            if (!_isSelecting && Math.abs(dx) < SELECT_THRESHOLD && Math.abs(dy) < SELECT_THRESHOLD) return;

            _isSelecting = true;

            const rect = container.getBoundingClientRect();
            const currentLL = _map.containerPointToLatLng(
                L.point(e.clientX - rect.left, e.clientY - rect.top)
            );

            if (_selectRect) {
                _selectRect.setBounds(L.latLngBounds(_selectStartLL, currentLL));
            } else {
                _selectRect = L.rectangle(
                    L.latLngBounds(_selectStartLL, currentLL),
                    { color: '#4fc3f7', weight: 1, fillOpacity: 0.12, dashArray: '5,4', interactive: false }
                ).addTo(_map);
            }
        });

        container.addEventListener('pointerup', (e) => {
            if (e.button !== 0) return;

            if (_isSelecting && _selectRect) {
                const bounds = _selectRect.getBounds();
                const inBounds = allUnitsData.filter(u => {
                    if (u.lat == null || u.lon == null || u.is_destroyed) return false;
                    if (!_canSelect(u)) return false;
                    return bounds.contains(L.latLng(u.lat, u.lon));
                });

                if (inBounds.length > 0) {
                    if (!_shiftHeld) selectedUnitIds.clear();
                    inBounds.forEach(u => selectedUnitIds.add(u.id));
                    _drawSelectionOverlays();
                    _updateSelectionUI();
                }

                _map.removeLayer(_selectRect);
                _selectRect = null;
            }

            _selectStartPt = null;
            _selectStartLL = null;
            _isSelecting = false;
            _shiftHeld = false;
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') _cancelRubberBand();
        });
    }

    function _cancelRubberBand() {
        if (_selectRect && _map) {
            _map.removeLayer(_selectRect);
            _selectRect = null;
        }
        _selectStartPt = null;
        _selectStartLL = null;
        _isSelecting = false;
        _shiftHeld = false;
    }

    // ══════════════════════════════════════════════════
    // ── Load & Render ────────────────────────────────
    // ══════════════════════════════════════════════════

    async function load(sessionId, token) {
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/units`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) return;
            const units = await resp.json();
            allUnitsData = units;
            render(units);
            _updateSelectionUI();
        } catch (err) {
            console.warn('Units load failed:', err);
        }
    }

    function render(units) {
        if (!unitsLayer) return;
        unitsLayer.clearLayers();
        unitMarkers = {};
        allUnitsData = units;

        units.forEach(u => {
            if (u.lat == null || u.lon == null) return;
            if (u.is_destroyed) return;

            const icon = KSymbols.createIcon(u.sidc, {
                direction: u.heading_deg || 0,
                unitType: u.unit_type,
            });

            const marker = L.marker([u.lat, u.lon], { icon });

            // Detail popup (right-click)
            marker.bindPopup(_buildPopupHtml(u));

            // Tooltip with unit name + range summary
            const detR = u.detection_range_m || 2000;
            const fireR = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
            const tooltipHtml = `<b>${u.name}</b><br>`
                + `<span style="color:#64b5f6">👁 ${_fmtDist(detR)}</span> `
                + `<span style="color:#ff9800">🎯 ${_fmtDist(fireR)}</span>`;
            marker.bindTooltip(tooltipHtml, {
                permanent: false,
                direction: 'top',
                offset: [0, -18],
                className: 'unit-tooltip',
            });

            // HOVER: show range circles
            marker.on('mouseover', () => {
                if (selectedUnitIds.has(u.id)) return; // already shown via selection
                _hoveredUnitId = u.id;
                _drawHoverRanges(u);
            });
            marker.on('mouseout', () => {
                if (_hoveredUnitId === u.id) {
                    _hoveredUnitId = null;
                    _hoverLayer.clearLayers();
                }
            });

            // LEFT-CLICK: select (replace, or shift-add)
            marker.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                const shiftKey = e.originalEvent && e.originalEvent.shiftKey;
                _selectUnit(u.id, shiftKey);
            });

            // RIGHT-CLICK: detail popup
            marker.on('contextmenu', (e) => {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                marker.setPopupContent(_buildPopupHtml(u));
                marker.openPopup();
            });

            unitsLayer.addLayer(marker);
            unitMarkers[u.id] = marker;
        });

        // Draw movement arrows for ALL moving units (on lower pane)
        _drawMovementArrows();
        // Redraw selection overlays (ranges, heading)
        _drawSelectionOverlays();
    }

    /** Draw hover range circles for a unit (transient, cleared on mouseout). */
    function _drawHoverRanges(u) {
        _hoverLayer.clearLayers();
        const pos = L.latLng(u.lat, u.lon);
        const isBlue = u.side === 'blue';
        const accent = isBlue ? '#4fc3f7' : '#ef5350';

        // Detection range
        const detRange = u.detection_range_m || 2000;
        _hoverLayer.addLayer(L.circle(pos, {
            radius: detRange,
            color: accent,
            weight: 1,
            opacity: 0.3,
            dashArray: '6,8',
            fillColor: accent,
            fillOpacity: 0.02,
            interactive: false,
        }));

        // Fire range
        const fireRange = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
        if (fireRange < detRange * 0.95) {
            _hoverLayer.addLayer(L.circle(pos, {
                radius: fireRange,
                color: '#ff9800',
                weight: 1,
                opacity: 0.35,
                dashArray: '4,5',
                fillColor: '#ff9800',
                fillOpacity: 0.03,
                interactive: false,
            }));
        }
    }

    function _buildPopupHtml(u) {
        const canSel = _canSelect(u);
        const canAsgn = _canAssign(u);
        const userId = KSessionUI.getUserId();
        const isAssignedToMe = u.assigned_user_ids && u.assigned_user_ids.includes(userId);

        let html = `<b>${u.name}</b><br>`;
        html += `<span style="color:#888">${u.unit_type}</span><br>`;
        html += `Side: <b>${u.side}</b><br>`;

        if (u.strength != null) {
            const pct = (u.strength * 100).toFixed(0);
            const clr = u.strength > 0.6 ? '#4caf50' : u.strength > 0.3 ? '#ff9800' : '#f44336';
            html += `Strength: <span style="color:${clr};font-weight:700">${pct}%</span><br>`;
        }
        if (u.morale != null) html += `Morale: ${(u.morale * 100).toFixed(0)}%<br>`;
        if (u.ammo != null) html += `Ammo: ${(u.ammo * 100).toFixed(0)}%<br>`;
        if (u.suppression != null && u.suppression > 0)
            html += `Suppression: ${(u.suppression * 100).toFixed(0)}%<br>`;
        if (u.comms_status && u.comms_status !== 'operational')
            html += `Comms: <span style="color:#ff9800">${u.comms_status}</span><br>`;

        // Detection / fire range info
        const detR = u.detection_range_m || 2000;
        const fireR = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
        html += `<span style="font-size:10px;color:#64b5f6">👁 ${_fmtDist(detR)}</span>`;
        html += ` <span style="font-size:10px;color:#ff9800">🎯 ${_fmtDist(fireR)}</span><br>`;

        // ── Chain of Command info ────────────────────────
        // Commanding officer (nearest assigned user up the parent chain)
        if (u.commanding_user_name) {
            const isSelfCO = u.assigned_user_names && u.assigned_user_names.length > 0
                && u.assigned_user_names.includes(u.commanding_user_name);
            if (isSelfCO) {
                // Unit's own assigned user is the CO
                html += `<span style="font-size:10px;color:#81c784;">⭐ CO: ${u.commanding_user_name}</span><br>`;
            } else {
                // CO is from an ancestor in the hierarchy
                html += `<span style="font-size:10px;color:#90caf9;">⬆ CO: ${u.commanding_user_name}</span><br>`;
            }
        }

        // Direct assignment info
        if (u.assigned_user_names && u.assigned_user_names.length > 0) {
            const names = u.assigned_user_names.join(', ');
            const meTag = isAssignedToMe ? ' (you)' : '';
            html += `<span style="font-size:10px;color:#81c784;">👤 Assigned: ${names}${meTag}</span><br>`;
        } else if (u.assigned_user_ids && u.assigned_user_ids.length > 0) {
            const tag = isAssignedToMe ? ' (you)' : '';
            html += `<span style="font-size:10px;color:#81c784;">Assigned ✓${tag}</span><br>`;
        } else {
            html += `<span style="font-size:10px;color:#777;">Unassigned</span><br>`;
        }

        if (canSel) {
            const isSel = selectedUnitIds.has(u.id);
            const label = isSel ? '✓ Selected' : '☐ Select';
            const style = isSel
                ? 'margin-top:4px;background:#0d3460;color:#4fc3f7;border:1px solid #4fc3f7;'
                : 'margin-top:4px;';
            html += `<button onclick="KUnits.toggleSelect('${u.id}')" style="${style}">${label}</button>`;
        }

        if (canAsgn) {
            const lbl = isAssignedToMe ? '✕ Unassign me' : '+ Assign to me';
            html += ` <button onclick="KUnits.assignToMe('${u.id}')" style="margin-top:4px;font-size:10px;" title="${lbl}">${lbl}</button>`;
        }

        return html;
    }

    function _fmtDist(m) {
        return m >= 1000 ? (m / 1000).toFixed(1) + 'km' : m + 'm';
    }

    // ══════════════════════════════════════════════════
    // ── Selection Logic ──────────────────────────────
    // ══════════════════════════════════════════════════

    /** Select a unit. Without shift: replaces selection. With shift: toggles. */
    function _selectUnit(unitId, shiftKey) {
        const unit = allUnitsData.find(u => u.id === unitId);
        if (unit && !_canSelect(unit)) return;

        if (shiftKey) {
            if (selectedUnitIds.has(unitId)) {
                selectedUnitIds.delete(unitId);
            } else {
                selectedUnitIds.add(unitId);
            }
        } else {
            if (selectedUnitIds.has(unitId) && selectedUnitIds.size === 1) {
                selectedUnitIds.clear();
            } else {
                selectedUnitIds.clear();
                selectedUnitIds.add(unitId);
            }
        }
        _drawSelectionOverlays();
        _updateSelectionUI();
    }

    function toggleSelect(unitId) {
        _selectUnit(unitId, true);
        if (_map) _map.closePopup();
    }

    function assignToMe(unitId) {
        const userId = KSessionUI.getUserId();
        const token = KSessionUI.getToken();
        const sessionId = KSessionUI.getSessionId();
        if (!userId || !token || !sessionId) return;

        const unit = allUnitsData.find(u => u.id === unitId);
        if (unit && !_canAssign(unit)) return;

        let currentIds = (unit && unit.assigned_user_ids) ? [...unit.assigned_user_ids] : [];
        if (currentIds.includes(userId)) {
            currentIds = currentIds.filter(id => id !== userId);
        } else {
            currentIds.push(userId);
        }

        fetch(`/api/sessions/${sessionId}/units/${unitId}/assign`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
            },
            body: JSON.stringify({ assigned_user_ids: currentIds }),
        }).then(resp => {
            if (resp.ok) {
                if (unit) unit.assigned_user_ids = currentIds.length > 0 ? currentIds : null;
                render(allUnitsData);
                if (_map) _map.closePopup();
            } else {
                resp.json().then(d => console.warn('Assign rejected:', d.detail || d)).catch(() => {});
            }
        }).catch(err => console.warn('Assign failed:', err));
    }

    // ══════════════════════════════════════════════════
    // ── Movement Arrows (all moving units, below markers)
    // ══════════════════════════════════════════════════

    /** Draw movement arrows for ALL units that have a movement/attack task. */
    function _drawMovementArrows() {
        if (!_movementArrowsLayer) return;
        _movementArrowsLayer.clearLayers();

        allUnitsData.forEach(u => {
            if (u.lat == null || u.lon == null || u.is_destroyed) return;
            const target = _extractTarget(u);
            if (!target) return;

            // Check if unit has a movement-type task
            const taskType = u.current_task && u.current_task.type;
            const isMoving = taskType && ['move', 'attack', 'advance', 'retreat', 'withdraw'].includes(taskType);
            if (!isMoving && !target) return;

            const from = L.latLng(u.lat, u.lon);
            const to = L.latLng(target.lat, target.lon);
            const isBlue = u.side === 'blue';
            const accent = isBlue ? '#4fc3f7' : '#ef5350';

            _drawMovementArrow(from, to, accent);
        });
    }

    /** Draw a single movement arrow from unit center to target on the movement pane. */
    function _drawMovementArrow(from, target, accent) {
        const to = target;

        const dLat = to.lat - from.lat;
        const dLon = to.lng - from.lng;
        const dist = Math.sqrt(dLat * dLat + dLon * dLon);

        if (dist < 0.0001) return;

        // Dashed movement line from unit center (no offset!)
        _movementArrowsLayer.addLayer(L.polyline([from, to], {
            color: '#ffd740',
            weight: 2,
            dashArray: '8,6',
            opacity: 0.7,
            interactive: false,
            pane: 'movementArrowsPane',
        }));

        // Arrowhead at target end
        const ahSize = Math.min(dist * 0.12, 0.001);
        _drawArrowheadOnPane(from.lat, from.lng, to.lat, to.lng, '#ffd740', ahSize);

        // Target crosshair
        _movementArrowsLayer.addLayer(L.circleMarker(to, {
            radius: 5,
            color: '#ffd740',
            weight: 2,
            fillColor: '#ffd740',
            fillOpacity: 0.2,
            interactive: false,
            pane: 'movementArrowsPane',
        }));
    }

    /** Draw arrowhead on the movement arrows pane. */
    function _drawArrowheadOnPane(fromLat, fromLon, toLat, toLon, color, size) {
        size = size || 0.0005;
        const dLat = toLat - fromLat;
        const dLon = toLon - fromLon;
        const angle = Math.atan2(dLon, dLat);
        const spread = 0.5;

        const tip   = [toLat, toLon];
        const left  = [toLat - size * Math.cos(angle - spread), toLon - size * Math.sin(angle - spread)];
        const right = [toLat - size * Math.cos(angle + spread), toLon - size * Math.sin(angle + spread)];

        _movementArrowsLayer.addLayer(L.polygon([tip, left, right], {
            color: color,
            fillColor: color,
            fillOpacity: 0.7,
            weight: 1,
            interactive: false,
            pane: 'movementArrowsPane',
        }));
    }

    // ══════════════════════════════════════════════════
    // ── Selection Overlays (range circles, heading) ──
    // ══════════════════════════════════════════════════

    function _drawSelectionOverlays() {
        if (!_selectionLayer) return;
        _selectionLayer.clearLayers();

        selectedUnitIds.forEach(uid => {
            const u = allUnitsData.find(unit => unit.id === uid);
            if (!u || u.lat == null || u.lon == null) return;

            const pos = L.latLng(u.lat, u.lon);
            const isBlue = u.side === 'blue';
            const accent = isBlue ? '#4fc3f7' : '#ef5350';

            // ── Selection ring (fixed pixel size — does not zoom) ──
            _selectionLayer.addLayer(L.circleMarker(pos, {
                radius: 20,
                color: accent,
                weight: 2,
                fillColor: accent,
                fillOpacity: 0.07,
                interactive: false,
            }));

            // ── Detection / visibility range (geographic circle) ──
            const detRange = u.detection_range_m || 2000;
            _selectionLayer.addLayer(L.circle(pos, {
                radius: detRange,
                color: accent,
                weight: 1,
                opacity: 0.35,
                dashArray: '6,8',
                fillColor: accent,
                fillOpacity: 0.03,
                interactive: false,
            }));

            // ── Effective fire range (geographic, amber) ──
            const fireRange = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
            if (fireRange < detRange * 0.95) {
                _selectionLayer.addLayer(L.circle(pos, {
                    radius: fireRange,
                    color: '#ff9800',
                    weight: 1,
                    opacity: 0.4,
                    dashArray: '4,5',
                    fillColor: '#ff9800',
                    fillOpacity: 0.04,
                    interactive: false,
                }));
            }

            // ── Heading indicator (only if NOT moving — arrows handle movement) ──
            const target = _extractTarget(u);
            if (!target && u.heading_deg != null && u.heading_deg !== 0) {
                _drawHeadingIndicator(pos, u.heading_deg, accent);
            }
        });
    }

    /** Extract target lat/lon from unit's current_task, if any. */
    function _extractTarget(u) {
        if (!u.current_task) return null;
        const t = u.current_task;
        if (t.target_location && t.target_location.lat != null) {
            return { lat: t.target_location.lat, lon: t.target_location.lon };
        }
        if (t.target_lat != null && t.target_lon != null) {
            return { lat: t.target_lat, lon: t.target_lon };
        }
        return null;
    }

    /** Draw a short heading indicator line (no movement target). */
    function _drawHeadingIndicator(pos, headingDeg, color) {
        const rad = (headingDeg * Math.PI) / 180;
        const dist = 250; // 250 m indicator
        const latRad = pos.lat * Math.PI / 180;
        const dLat = (dist / 111320) * Math.cos(rad);
        const dLon = (dist / (111320 * Math.cos(latRad))) * Math.sin(rad);
        const endLat = pos.lat + dLat;
        const endLon = pos.lng + dLon;
        const end = L.latLng(endLat, endLon);

        _selectionLayer.addLayer(L.polyline([pos, end], {
            color: color,
            weight: 2,
            opacity: 0.55,
            interactive: false,
        }));

        _drawArrowhead(pos.lat, pos.lng, endLat, endLon, color, 0.00025);
    }

    /** Draw a small triangular arrowhead at (toLat, toLon) on the selection layer. */
    function _drawArrowhead(fromLat, fromLon, toLat, toLon, color, size) {
        size = size || 0.0005;
        const dLat = toLat - fromLat;
        const dLon = toLon - fromLon;
        const angle = Math.atan2(dLon, dLat);
        const spread = 0.5;

        const tip   = [toLat, toLon];
        const left  = [toLat - size * Math.cos(angle - spread), toLon - size * Math.sin(angle - spread)];
        const right = [toLat - size * Math.cos(angle + spread), toLon - size * Math.sin(angle + spread)];

        _selectionLayer.addLayer(L.polygon([tip, left, right], {
            color: color,
            fillColor: color,
            fillOpacity: 0.7,
            weight: 1,
            interactive: false,
        }));
    }

    // ══════════════════════════════════════════════════
    // ── Selection Helpers ────────────────────────────
    // ══════════════════════════════════════════════════

    function getSelectedIds() {
        return Array.from(selectedUnitIds);
    }

    function clearSelection() {
        selectedUnitIds.clear();
        _drawSelectionOverlays();
        _updateSelectionUI();
    }

    function _updateSelectionUI() {
        const selDisplay = document.getElementById('selected-units-display');
        if (!selDisplay) return;

        if (selectedUnitIds.size === 0) {
            selDisplay.innerHTML = '<span style="color:#888;font-size:11px;">No units selected</span>';
            return;
        }

        const names = allUnitsData
            .filter(u => selectedUnitIds.has(u.id))
            .map(u => u.name);

        selDisplay.innerHTML = names.map(n =>
            `<span class="selected-unit-tag">${n}</span>`
        ).join(' ');
    }

    function getAllUnits() {
        return allUnitsData;
    }

    /** Clear all unit layers and data (used on logout). */
    function clearAll() {
        if (unitsLayer) unitsLayer.clearLayers();
        if (_selectionLayer) _selectionLayer.clearLayers();
        if (_hoverLayer) _hoverLayer.clearLayers();
        if (_movementArrowsLayer) _movementArrowsLayer.clearLayers();
        unitMarkers = {};
        allUnitsData = [];
        selectedUnitIds.clear();
    }

    function update(units) {
        render(units);
    }

    function getMarker(unitId) {
        return unitMarkers[unitId] || null;
    }

    return {
        init, load, update, render, getMarker,
        toggle, isVisible,
        toggleSelect, assignToMe,
        getSelectedIds, clearSelection, getAllUnits,
        clearAll,
    };
})();
