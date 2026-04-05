/**
 * units.js – Fetch and render visible units on the map with military symbols.
 *            Left-click = select unit (replaces previous selection).
 *            Shift+left-click = add/remove unit from selection.
 *            Right-click = open detail popup.
 *            Left-click drag on empty map = rubber-band mass selection.
 */
const KUnits = (() => {
    let unitMarkers = {};
    let unitsLayer = null;
    let allUnitsData = [];
    let selectedUnitIds = new Set();
    let _map = null;
    let _visible = true;

    // ── Rubber-band selection state ──────────────────
    let _selectRect = null;     // Leaflet rectangle layer
    let _selectStartPt = null;  // {x, y} screen coords
    let _selectStartLL = null;  // L.latLng
    let _isSelecting = false;
    let _shiftHeld = false;
    const SELECT_THRESHOLD = 6; // pixels before drag becomes selection

    function init(map) {
        _map = map;
        unitsLayer = L.layerGroup().addTo(map);
        _initRubberBandSelection();
    }

    // ══════════════════════════════════════════════════
    // ── Permission Helpers ────────────────────────────
    // ══════════════════════════════════════════════════

    /** Can the current user select/command this unit? */
    function _canSelect(unit) {
        const userId = KSessionUI.getUserId();
        if (!userId) return false;
        // TODO: check participant role once available
        // Admin / observer can select anything
        // If unit has no assignments, anyone on the same side can select
        if (!unit.assigned_user_ids || unit.assigned_user_ids.length === 0) return true;
        // Otherwise only if assigned to this user
        return unit.assigned_user_ids.includes(userId);
    }

    /** Can the current user assign/unassign this unit? */
    function _canAssign(unit) {
        const userId = KSessionUI.getUserId();
        if (!userId) return false;
        // If unit has no owners yet, anyone can claim (admin/commander)
        if (!unit.assigned_user_ids || unit.assigned_user_ids.length === 0) return true;
        // If user is already an owner, they can modify
        return unit.assigned_user_ids.includes(userId);
    }

    // ══════════════════════════════════════════════════
    // ── Visibility Toggle ────────────────────────────
    // ══════════════════════════════════════════════════

    function toggle() {
        _visible = !_visible;
        if (unitsLayer && _map) {
            if (_visible) {
                if (!_map.hasLayer(unitsLayer)) _map.addLayer(unitsLayer);
            } else {
                if (_map.hasLayer(unitsLayer)) _map.removeLayer(unitsLayer);
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
                    if (!_shiftHeld) {
                        // Replace selection
                        selectedUnitIds.clear();
                    }
                    inBounds.forEach(u => selectedUnitIds.add(u.id));
                    render(allUnitsData);
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
            });

            const marker = L.marker([u.lat, u.lon], { icon });

            // Build detail popup content (shown on right-click)
            const popupHtml = _buildPopupHtml(u);
            marker.bindPopup(popupHtml);

            // Tooltip with unit name
            marker.bindTooltip(u.name, {
                permanent: false,
                direction: 'top',
                offset: [0, -20],
            });

            // LEFT-CLICK: select this unit (replace selection, shift=add)
            marker.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                const shiftKey = e.originalEvent && e.originalEvent.shiftKey;
                _selectUnit(u.id, shiftKey);
            });

            // RIGHT-CLICK: open detail popup
            marker.on('contextmenu', (e) => {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                marker.setPopupContent(_buildPopupHtml(u));
                marker.openPopup();
            });

            unitsLayer.addLayer(marker);
            unitMarkers[u.id] = marker;

            // Apply selected visual
            if (selectedUnitIds.has(u.id)) {
                _applySelectedStyle(marker);
            }
        });
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
            const strengthPct = (u.strength * 100).toFixed(0);
            const strengthColor = u.strength > 0.6 ? '#4caf50' : u.strength > 0.3 ? '#ff9800' : '#f44336';
            html += `Strength: <span style="color:${strengthColor};font-weight:700">${strengthPct}%</span><br>`;
        }
        if (u.morale != null) {
            html += `Morale: ${(u.morale * 100).toFixed(0)}%<br>`;
        }
        if (u.ammo != null) {
            html += `Ammo: ${(u.ammo * 100).toFixed(0)}%<br>`;
        }
        if (u.suppression != null && u.suppression > 0) {
            html += `Suppression: ${(u.suppression * 100).toFixed(0)}%<br>`;
        }
        if (u.comms_status && u.comms_status !== 'operational') {
            html += `Comms: <span style="color:#ff9800">${u.comms_status}</span><br>`;
        }

        // Show assignment info
        if (u.assigned_user_ids && u.assigned_user_ids.length > 0) {
            const myTag = isAssignedToMe ? ' (you)' : '';
            html += `<span style="font-size:10px;color:#81c784;">Assigned ✓${myTag}</span><br>`;
        } else {
            html += `<span style="font-size:10px;color:#777;">Unassigned</span><br>`;
        }

        // Select button — only if user can select this unit
        if (canSel) {
            const isSelected = selectedUnitIds.has(u.id);
            const selectLabel = isSelected ? '✓ Selected' : '☐ Select';
            const selectStyle = isSelected
                ? 'margin-top:4px;background:#0d3460;color:#4fc3f7;border:1px solid #4fc3f7;'
                : 'margin-top:4px;';
            html += `<button onclick="KUnits.toggleSelect('${u.id}')" style="${selectStyle}">${selectLabel}</button>`;
        }

        // Assign button — only if user can assign
        if (canAsgn) {
            const assignLabel = isAssignedToMe ? '✕ Unassign me' : '+ Assign to me';
            html += ` <button onclick="KUnits.assignToMe('${u.id}')" style="margin-top:4px;font-size:10px;" title="${assignLabel}">${assignLabel}</button>`;
        }

        return html;
    }

    /**
     * Select a unit. Without shift: replaces selection. With shift: toggles.
     */
    function _selectUnit(unitId, shiftKey) {
        const unit = allUnitsData.find(u => u.id === unitId);
        if (unit && !_canSelect(unit)) return; // Can't select units not assigned to us

        if (shiftKey) {
            // Shift+click: toggle this unit in/out of selection
            if (selectedUnitIds.has(unitId)) {
                selectedUnitIds.delete(unitId);
            } else {
                selectedUnitIds.add(unitId);
            }
        } else {
            // Normal click: if already selected and it's the only one, deselect
            if (selectedUnitIds.has(unitId) && selectedUnitIds.size === 1) {
                selectedUnitIds.clear();
            } else {
                // Replace selection with just this unit
                selectedUnitIds.clear();
                selectedUnitIds.add(unitId);
            }
        }
        render(allUnitsData);
        _updateSelectionUI();
    }

    function toggleSelect(unitId) {
        _selectUnit(unitId, true);  // popup button always toggles
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

    function _applySelectedStyle(marker) {
        const latlng = marker.getLatLng();
        const ring = L.circleMarker(latlng, {
            radius: 18,
            color: '#4fc3f7',
            weight: 3,
            fillColor: '#4fc3f7',
            fillOpacity: 0.12,
            interactive: false,
        });
        unitsLayer.addLayer(ring);
    }

    function getSelectedIds() {
        return Array.from(selectedUnitIds);
    }

    function clearSelection() {
        selectedUnitIds.clear();
        render(allUnitsData);
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

    return { init, load, update, render, getMarker, toggle, isVisible, toggleSelect, assignToMe, getSelectedIds, clearSelection, getAllUnits };
})();
