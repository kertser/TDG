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
 *    • Movement line to target     (if unit has a movement task)
 *    • Heading indicator           (if unit is stationary but has heading)
 *
 *  Assignment: user can only select units assigned to them (or unassigned).
 */
const KUnits = (() => {
    let unitMarkers = {};          // unit_id → Leaflet marker
    let unitsLayer = null;         // L.layerGroup for unit markers
    let _selectionLayer = null;    // L.layerGroup for selection overlays (range, direction)
    let _hoverLayer = null;        // L.layerGroup for hover range circles
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
        unitsLayer = L.layerGroup().addTo(map);
        _selectionLayer = L.layerGroup().addTo(map);
        _hoverLayer = L.layerGroup().addTo(map);
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
        // Otherwise only if assigned to this user
        return unit.assigned_user_ids.includes(userId);
    }

    /** Can the current user assign/unassign this unit? */
    function _canAssign(unit) {
        const userId = KSessionUI.getUserId();
        if (!userId) return false;
        if (!unit.assigned_user_ids || unit.assigned_user_ids.length === 0) return true;
        return unit.assigned_user_ids.includes(userId);
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
            } else {
                if (unitsLayer && _map.hasLayer(unitsLayer)) _map.removeLayer(unitsLayer);
                if (_selectionLayer && _map.hasLayer(_selectionLayer)) _map.removeLayer(_selectionLayer);
                if (_hoverLayer && _map.hasLayer(_hoverLayer)) _map.removeLayer(_hoverLayer);
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

        // Redraw selection overlays (ranges, movement lines)
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

        // Assignment info
        if (u.assigned_user_ids && u.assigned_user_ids.length > 0) {
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
    // ── Selection Overlays (range circles, movement) ─
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

            // ── Movement direction OR heading indicator ──
            const target = _extractTarget(u);
            if (target) {
                _drawMovementLine(pos, target, accent);
            } else if (u.heading_deg != null && u.heading_deg !== 0) {
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

    /** Draw a dashed movement line from unit to target with arrowhead. */
    function _drawMovementLine(from, target, accent) {
        const to = L.latLng(target.lat, target.lon);

        // Compute direction and offset the start point to avoid drawing under the unit marker
        const dLat = target.lat - from.lat;
        const dLon = target.lon - from.lng;
        const dist = Math.sqrt(dLat * dLat + dLon * dLon);

        if (dist < 0.0001) return; // too close

        // Offset start ~60 meters from unit center (rough pixel margin)
        const offsetDeg = 0.0006;
        const startLat = from.lat + (dLat / dist) * offsetDeg;
        const startLon = from.lng + (dLon / dist) * offsetDeg;
        const lineStart = L.latLng(startLat, startLon);

        // Dashed movement line (from offset start to target)
        _selectionLayer.addLayer(L.polyline([lineStart, to], {
            color: '#ffd740',
            weight: 2,
            dashArray: '8,6',
            opacity: 0.7,
            interactive: false,
        }));

        // Arrowhead at target end
        if (dist > 0) {
            const ahSize = Math.min(dist * 0.12, 0.001);
            _drawArrowhead(startLat, startLon, target.lat, target.lon, '#ffd740', ahSize);
        }

        // Target crosshair
        _selectionLayer.addLayer(L.circleMarker(to, {
            radius: 5,
            color: '#ffd740',
            weight: 2,
            fillColor: '#ffd740',
            fillOpacity: 0.2,
            interactive: false,
        }));
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

    /** Draw a small triangular arrowhead at (toLat, toLon). */
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
    };
})();
