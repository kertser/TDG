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
    let _lastZoomBucket = null;    // track zoom bucket for marker size changes
    let _adminDragEnabled = false; // admin drag-and-drop mode

    // ── Viewshed (LOS polygon) cache ────────────────
    let _viewshedCache = {};       // unit_id → GeoJSON Feature
    let _viewshedPending = {};     // unit_id → true (fetch in-flight)
    let _viewshedTick = -1;        // invalidate cache when tick changes

    // ── Rubber-band selection state ──────────────────
    let _selectRect = null;
    let _selectStartPt = null;
    let _selectStartLL = null;
    let _isSelecting = false;
    let _shiftHeld = false;
    const SELECT_THRESHOLD = 6;

    // ── Personnel/unit size by type ─────────────────────
    const PERSONNEL = {
        'tank_company':      60,
        'mech_company':      100,
        'infantry_company':  120,
        'infantry_platoon':  30,
        'mortar_section':    12,
        'at_team':           6,
        'recon_team':        6,
        'observation_post':  4,
        'sniper_team':       2,
    };
    const DEFAULT_PERSONNEL = 20;

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
        'artillery_battery': 8000,
        'artillery_platoon': 6000,
        'mortar_team':       2500,
    };
    const DEFAULT_FIRE_RANGE = 500;

    // ── Indirect fire unit types (fire NOT affected by LOS) ──
    const INDIRECT_FIRE_TYPES = new Set([
        'mortar_section', 'mortar_team',
        'artillery_battery', 'artillery_platoon',
    ]);

    // ── Unit-type-specific eye heights (meters) for LOS context ──
    const UNIT_EYE_HEIGHTS = {
        'observation_post':   8.0,    // elevated optics / mast
        'tank_company':       3.0,
        'tank_platoon':       3.0,
        'mech_company':       2.8,
        'mech_platoon':       2.8,
        'recon_team':         3.0,
        'recon_section':      3.0,
        'sniper_team':        2.5,
        'headquarters':       3.0,
        'command_post':       3.0,
        'artillery_battery':  2.5,
        'artillery_platoon':  2.5,
    };
    const DEFAULT_UNIT_EYE_HEIGHT = 2.0;

    // ── Status icons ────────────────────────────────────
    const STATUS_ICONS = {
        idle: '⏸', moving: '🚶', engaging: '⚔', defending: '🛡',
        retreating: '↩', observing: '👁', suppressed: '💥',
        broken: '💔', destroyed: '☠', supporting: '🤝',
    };
    const STATUS_COLORS = {
        idle: '#aaa', moving: '#4fc3f7', engaging: '#f44336', defending: '#66bb6a',
        retreating: '#ff9800', observing: '#90caf9', suppressed: '#e91e63',
        broken: '#9c27b0', destroyed: '#666', supporting: '#4caf50',
    };

    // ── Movement speed options ───────────────────────
    const SPEED_OPTIONS = {
        slow: { label: 'Slow', icon: '🐢', color: '#81c784' },
        fast: { label: 'Fast', icon: '⚡', color: '#ff9800' },
    };

    // ── Unit-type-specific base speeds (m/s), matching backend ──
    const UNIT_TYPE_SPEEDS = {
        'infantry_platoon':   { slow: 1.2, fast: 3.0 },
        'infantry_company':   { slow: 1.0, fast: 2.5 },
        'infantry_section':   { slow: 1.2, fast: 3.0 },
        'infantry_team':      { slow: 1.5, fast: 3.5 },
        'infantry_squad':     { slow: 1.2, fast: 3.0 },
        'infantry_battalion': { slow: 0.8, fast: 2.0 },
        'mech_platoon':       { slow: 3.0, fast: 10.0 },
        'mech_company':       { slow: 2.5, fast: 8.0 },
        'tank_platoon':       { slow: 3.0, fast: 12.0 },
        'tank_company':       { slow: 2.5, fast: 10.0 },
        'artillery_battery':  { slow: 1.5, fast: 5.0 },
        'artillery_platoon':  { slow: 1.5, fast: 5.0 },
        'mortar_section':     { slow: 1.0, fast: 2.5 },
        'mortar_team':        { slow: 1.2, fast: 3.0 },
        'at_team':            { slow: 1.2, fast: 3.0 },
        'recon_team':         { slow: 2.0, fast: 4.0 },
        'recon_section':      { slow: 2.0, fast: 4.0 },
        'sniper_team':        { slow: 1.0, fast: 2.5 },
        'observation_post':   { slow: 0.5, fast: 1.5 },
        'engineer_platoon':   { slow: 1.0, fast: 2.5 },
        'engineer_section':   { slow: 1.0, fast: 2.5 },
        'logistics_unit':     { slow: 2.0, fast: 6.0 },
        'headquarters':       { slow: 1.5, fast: 5.0 },
        'command_post':       { slow: 1.0, fast: 3.0 },
    };
    const DEFAULT_UNIT_SPEEDS = { slow: 1.2, fast: 3.0 };

    /** Get the base speed (m/s) for a unit type and speed label. */
    function _getUnitSpeed(unitType, speedLabel) {
        const speeds = UNIT_TYPE_SPEEDS[unitType] || DEFAULT_UNIT_SPEEDS;
        return speeds[speedLabel] || speeds.slow;
    }

    /** Format m/s as km/h string. */
    function _mpsToKmh(mps) {
        return (mps * 3.6).toFixed(0);
    }

    // ── Formation options ─────────────────────────────
    const FORMATIONS = [
        { key: 'column', label: 'Column', icon: '║' },
        { key: 'line', label: 'Line', icon: '═' },
        { key: 'wedge', label: 'Wedge', icon: '▽' },
        { key: 'vee', label: 'Vee', icon: '△' },
        { key: 'echelon_left', label: 'Echelon L', icon: '╲' },
        { key: 'echelon_right', label: 'Echelon R', icon: '╱' },
        { key: 'staggered', label: 'Staggered', icon: '⋮' },
        { key: 'box', label: 'Box', icon: '▢' },
        { key: 'diamond', label: 'Diamond', icon: '◇' },
        { key: 'dispersed', label: 'Dispersed', icon: '·:·' },
    ];

    // ── Set-move state ──────────────────────────────
    let _setMovePending = null; // {unitId, speed} while waiting for map click

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

        // Track zoom for marker size scaling
        _lastZoomBucket = KSymbols.getZoomBucket(map.getZoom());
        map.on('zoomend', () => {
            const bucket = KSymbols.getZoomBucket(map.getZoom());
            if (bucket !== _lastZoomBucket) {
                _lastZoomBucket = bucket;
                if (allUnitsData.length > 0) {
                    render(allUnitsData);
                }
            }
        });
    }

    // ══════════════════════════════════════════════════
    // ── Permission Helpers ────────────────────────────
    // ══════════════════════════════════════════════════

    /** Can the current user select/command this unit? */
    function _canSelect(unit) {
        const userId = KSessionUI.getUserId();
        if (!userId) return false;
        const mySide = KSessionUI.getSide();
        const myRole = KSessionUI.getRole();
        // Observers cannot select/command units (check both side and role)
        if (mySide === 'observer' || myRole === 'observer') return false;
        // Admin-unlocked bypass: can select any unit regardless of side
        const adminUnlocked = typeof KAdmin !== 'undefined' && KAdmin.isUnlocked();
        if (adminUnlocked) return true;
        // Side check: only own-side units are selectable (admin bypass)
        if (mySide && mySide !== 'admin' && unit.side !== mySide) {
            return false;
        }
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
        const mySide = KSessionUI.getSide();
        const myRole = KSessionUI.getRole();
        // Observers cannot assign units (check both side and role)
        if (mySide === 'observer' || myRole === 'observer') return false;
        // Admin-unlocked bypass: can assign any unit regardless of side
        const adminUnlocked = typeof KAdmin !== 'undefined' && KAdmin.isUnlocked();
        if (adminUnlocked) return true;
        // Side check: only own-side units can be assigned (admin bypass)
        if (mySide && mySide !== 'admin' && unit.side !== mySide) {
            return false;
        }
        if (!unit.assigned_user_ids || unit.assigned_user_ids.length === 0) return true;
        if (unit.assigned_user_ids.includes(userId)) return true;
        // Check command authority via parent chain and subordinate user authority
        return _hasCommandAuthority(unit, userId);
    }

    /** Check if user has authority over unit via ancestor chain or subordinate user */
    function _hasCommandAuthority(unit, userId) {
        const unitMap = {};
        allUnitsData.forEach(u => { unitMap[u.id] = u; });

        // Check 1: Walk up unit hierarchy (direct hierarchy authority)
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

        // Check 2: Subordinate user authority
        // If the unit is assigned to user B, and B is subordinate to userId
        // (B's unit has an ancestor assigned to userId), then userId has authority.
        if (unit.assigned_user_ids && unit.assigned_user_ids.length > 0) {
            for (const assignedUid of unit.assigned_user_ids) {
                if (assignedUid === userId) continue;
                if (_isSubordinateUser(assignedUid, userId, unitMap)) {
                    return true;
                }
            }
        }

        return false;
    }

    /** Check if subordinateUserId is subordinate to superiorUserId via the unit hierarchy */
    function _isSubordinateUser(subordinateUserId, superiorUserId, unitMap) {
        // Find all units assigned to the subordinate user
        const subUnits = Object.values(unitMap).filter(u =>
            u.assigned_user_ids && u.assigned_user_ids.includes(subordinateUserId)
        );
        // For each of those units, walk up the parent chain looking for superiorUserId
        for (const subUnit of subUnits) {
            let parentId = subUnit.parent_unit_id;
            const visited = new Set();
            while (parentId && unitMap[parentId]) {
                if (visited.has(parentId)) break;
                visited.add(parentId);
                const parent = unitMap[parentId];
                if (parent.assigned_user_ids && parent.assigned_user_ids.includes(superiorUserId)) {
                    return true;
                }
                parentId = parent.parent_unit_id;
            }
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
            // Invalidate viewshed for units whose position changed
            _invalidateMovedUnitsViewshed(units);
            allUnitsData = units;
            render(units);
            _updateSelectionUI();
        } catch (err) {
            console.warn('Units load failed:', err);
        }
    }

    // ── Snail path resolution cache (coord key → snail string) ──
    const _snailCache = {};

    /**
     * Resolve missing target_snail for units with move tasks.
     * Uses KGrid.getSnailAtPoint and caches results.
     */
    async function _enrichSnailPaths(units) {
        for (const u of units) {
            const task = u.current_task;
            if (!task || !task.target_location) continue;
            if (task.target_snail) continue; // already resolved
            const lat = task.target_location.lat;
            const lon = task.target_location.lon;
            if (lat == null || lon == null) continue;
            const key = `${lat.toFixed(6)},${lon.toFixed(6)}`;
            if (_snailCache[key] !== undefined) {
                task.target_snail = _snailCache[key];
                continue;
            }
            try {
                if (typeof KGrid !== 'undefined' && KGrid.getSnailAtPoint) {
                    const result = await KGrid.getSnailAtPoint(lat, lon, 2);
                    const snail = result && result.snail_path ? result.snail_path : null;
                    _snailCache[key] = snail;
                    task.target_snail = snail;
                }
            } catch { /* ignore */ }
        }
    }

    function render(units) {
        if (!unitsLayer) return;
        unitsLayer.clearLayers();
        unitMarkers = {};
        allUnitsData = units;

        // Eagerly resolve missing snail paths in background
        _enrichSnailPaths(units);

        // Compute zoom scale factor for current zoom level
        const zoomScale = _map ? KSymbols.getZoomScale(_map.getZoom()) : 1.0;

        units.forEach(u => {
            if (u.lat == null || u.lon == null) return;
            if (u.is_destroyed) return;

            const icon = KSymbols.createIcon(u.sidc, {
                direction: u.heading_deg || 0,
                unitType: u.unit_type,
                zoomScale: zoomScale,
                isHQ: u.unit_type === 'headquarters' || u.unit_type === 'command_post',
                callsign: u.name || '',
            });

            // Admin drag-and-drop: make markers draggable when admin is unlocked
            const isDraggable = _adminDragEnabled;
            const marker = L.marker([u.lat, u.lon], { icon, draggable: isDraggable });

            // Tooltip with unit name + range summary + size + status
            const detR = u.detection_range_m || 2000;
            const fireR = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
            const pers = PERSONNEL[u.unit_type] || DEFAULT_PERSONNEL;
            const status = u.unit_status || 'idle';
            const statusColor = STATUS_COLORS[status] || '#aaa';
            // Show speed label in tooltip when moving
            let ttStatus = status;
            let ttStatusIcon = STATUS_ICONS[status] || '•';
            const ttSpeed = u.current_task && u.current_task.speed;
            if (status === 'moving' && ttSpeed && SPEED_OPTIONS[ttSpeed]) {
                ttStatusIcon = SPEED_OPTIONS[ttSpeed].icon;
                ttStatus = ttSpeed;
            }
            const tooltipEyeH = UNIT_EYE_HEIGHTS[u.unit_type] || DEFAULT_UNIT_EYE_HEIGHT;
            const tooltipEyeTag = tooltipEyeH > DEFAULT_UNIT_EYE_HEIGHT ? ` <span style="color:#a5d6a7">(${tooltipEyeH}m)</span>` : '';
            const tooltipHtml = `<b>${u.name}</b> <span style="font-size:10px;color:#aaa;">(${pers}p)</span><br>`
                + `<span style="color:${statusColor};font-weight:600;">${ttStatusIcon} ${ttStatus}</span> `
                + `<span style="color:#64b5f6">👁 ${_fmtDist(detR)}${tooltipEyeTag}</span> `
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

            // LEFT-CLICK: select/deselect only (no popup)
            marker.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                _closeUnitContextMenu();
                const shiftKey = e.originalEvent && e.originalEvent.shiftKey;
                _selectUnit(u.id, shiftKey);
            });

            // RIGHT-CLICK: context menu with info, rename, etc.
            marker.on('contextmenu', (e) => {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                _showUnitContextMenu(u, e.originalEvent);
            });

            // DRAG: admin drag-and-drop to reposition (supports group move)
            if (isDraggable) {
                marker.on('dragstart', () => {
                    // If this unit is part of a multi-selection, enable group drag
                    if (selectedUnitIds.has(u.id) && selectedUnitIds.size > 1) {
                        marker._groupDrag = true;
                        marker._groupDragStart = marker.getLatLng();
                        marker._groupPeers = [];
                        selectedUnitIds.forEach(uid => {
                            if (uid !== u.id && unitMarkers[uid]) {
                                marker._groupPeers.push({
                                    id: uid,
                                    marker: unitMarkers[uid],
                                    startPos: unitMarkers[uid].getLatLng(),
                                });
                            }
                        });
                    } else {
                        marker._groupDrag = false;
                    }
                });

                marker.on('drag', () => {
                    // Move all selected peers in real-time
                    if (!marker._groupDrag || !marker._groupPeers) return;
                    const newPos = marker.getLatLng();
                    const dLat = newPos.lat - marker._groupDragStart.lat;
                    const dLng = newPos.lng - marker._groupDragStart.lng;
                    marker._groupPeers.forEach(p => {
                        p.marker.setLatLng([
                            p.startPos.lat + dLat,
                            p.startPos.lng + dLng,
                        ]);
                    });
                });

                marker.on('dragend', () => {
                    const pos = marker.getLatLng();
                    _saveUnitPosition(u.id, pos.lat, pos.lng);
                    u.lat = pos.lat;
                    u.lon = pos.lng;

                    // Invalidate viewshed cache for moved unit (position changed)
                    delete _viewshedCache[u.id];

                    // Save all peer positions if group drag
                    if (marker._groupDrag && marker._groupPeers) {
                        const dLat = pos.lat - marker._groupDragStart.lat;
                        const dLng = pos.lng - marker._groupDragStart.lng;
                        marker._groupPeers.forEach(p => {
                            const newLat = p.startPos.lat + dLat;
                            const newLng = p.startPos.lng + dLng;
                            const peerUnit = allUnitsData.find(pu => pu.id === p.id);
                            if (peerUnit) {
                                peerUnit.lat = newLat;
                                peerUnit.lon = newLng;
                            }
                            // Invalidate viewshed cache for each moved peer
                            delete _viewshedCache[p.id];
                            _saveUnitPosition(p.id, newLat, newLng);
                        });
                    }

                    // Collect all moved unit IDs for viewshed refresh
                    const movedIds = [u.id];
                    if (marker._groupDrag && marker._groupPeers) {
                        marker._groupPeers.forEach(p => movedIds.push(p.id));
                    }

                    marker._groupDrag = false;
                    marker._groupPeers = null;
                    marker._groupDragStart = null;
                    _drawMovementArrows();

                    // Re-fetch viewshed for moved units, then redraw overlays
                    Promise.all(movedIds.map(id => _fetchViewshed(id)))
                        .then(() => _drawSelectionOverlays());
                });
            }

            unitsLayer.addLayer(marker);
            unitMarkers[u.id] = marker;
        });

        // Draw movement arrows for ALL moving units (on lower pane)
        _drawMovementArrows();
        // Redraw selection overlays (ranges, heading)
        _drawSelectionOverlays();
    }

    // ══════════════════════════════════════════════════
    // ── Viewshed (LOS polygon) Fetch & Cache ─────────
    // ══════════════════════════════════════════════════

    /**
     * Fetch the viewshed (line-of-sight) polygon for a unit.
     * Returns a promise that resolves when the data is cached.
     * Uses an in-memory cache keyed by unit_id, invalidated on tick change.
     * Failed fetches are cached as `false` to prevent infinite retry loops.
     */
    function _fetchViewshed(unitId) {
        // Already cached (success or failure sentinel) — resolve immediately
        if (_viewshedCache[unitId] !== undefined) return Promise.resolve();
        // Already fetching — return the existing promise so callers wait
        if (_viewshedPending[unitId]) return _viewshedPending[unitId];

        const sessionId = KSessionUI.getSessionId();
        const token = KSessionUI.getToken();
        if (!sessionId || !token) return Promise.resolve();

        const promise = fetch(
            `/api/sessions/${sessionId}/units/${unitId}/viewshed?rays=72`,
            { headers: { 'Authorization': `Bearer ${token}` } }
        ).then(resp => {
            if (resp.ok) return resp.json();
            console.warn('Viewshed API error', resp.status, 'for unit', unitId);
            return null;
        }).then(geojson => {
            if (geojson) {
                _viewshedCache[unitId] = geojson;
            } else {
                // Cache failure sentinel to prevent infinite retries
                _viewshedCache[unitId] = false;
            }
        }).catch(err => {
            console.warn('Viewshed fetch failed for', unitId, err);
            _viewshedCache[unitId] = false;
        }).finally(() => {
            delete _viewshedPending[unitId];
        });

        _viewshedPending[unitId] = promise;
        return promise;
    }

    /**
     * Invalidate the viewshed cache (called on tick update or unit state change).
     */
    function _invalidateViewshedCache(newTick) {
        if (newTick !== undefined && newTick !== _viewshedTick) {
            _viewshedTick = newTick;
            _viewshedCache = {};
            _viewshedPending = {};
        }
    }

    /**
     * Chaikin's corner-cutting algorithm to smooth a polygon.
     * @param {Array<[number,number]>} latlngs  Array of [lat, lng] (Leaflet order).
     * @param {number} iterations  Number of smoothing passes (default 2).
     * @returns {Array<[number,number]>}
     */
    function _smoothPolygon(latlngs, iterations) {
        if (!latlngs || latlngs.length < 4) return latlngs;
        iterations = iterations || 2;
        let pts = latlngs;
        // Remove closing duplicate if present
        const last = pts[pts.length - 1];
        const first = pts[0];
        if (last[0] === first[0] && last[1] === first[1]) pts = pts.slice(0, -1);

        for (let iter = 0; iter < iterations; iter++) {
            const n = pts.length;
            const newPts = [];
            for (let i = 0; i < n; i++) {
                const p0 = pts[i];
                const p1 = pts[(i + 1) % n];
                newPts.push([
                    0.75 * p0[0] + 0.25 * p1[0],
                    0.75 * p0[1] + 0.25 * p1[1],
                ]);
                newPts.push([
                    0.25 * p0[0] + 0.75 * p1[0],
                    0.25 * p0[1] + 0.75 * p1[1],
                ]);
            }
            pts = newPts;
        }
        // Close
        pts.push(pts[0]);
        return pts;
    }

    /**
     * Clip a viewshed polygon to a maximum range (for direct fire range overlay).
     * For each vertex, if its distance from center > maxRange, scale it toward center.
     * @param {Array<[number,number]>} geojsonCoords  [lon, lat] pairs from GeoJSON.
     * @param {number} centerLat
     * @param {number} centerLon
     * @param {number} maxRangeM  Fire range in meters.
     * @returns {Array<[number,number]>}  [lat, lng] pairs (Leaflet order).
     */
    function _clipViewshedToRange(geojsonCoords, centerLat, centerLon, maxRangeM) {
        const result = [];
        for (const coord of geojsonCoords) {
            const lon = coord[0], lat = coord[1];
            const dlat = (lat - centerLat) * 111320;
            const dlon = (lon - centerLon) * 74000;
            const dist = Math.sqrt(dlat * dlat + dlon * dlon);
            if (dist <= maxRangeM || dist < 1) {
                result.push([lat, lon]);
            } else {
                const scale = maxRangeM / dist;
                result.push([
                    centerLat + (lat - centerLat) * scale,
                    centerLon + (lon - centerLon) * scale,
                ]);
            }
        }
        return result;
    }

    /** Draw hover range overlays for a unit (transient, cleared on mouseout). */
    function _drawHoverRanges(u) {
        _hoverLayer.clearLayers();
        const pos = L.latLng(u.lat, u.lon);
        const isBlue = u.side === 'blue';
        const accent = isBlue ? '#4fc3f7' : '#ef5350';
        const detRange = u.detection_range_m || 2000;
        const fireRange = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
        const isIndirect = INDIRECT_FIRE_TYPES.has(u.unit_type);

        // Use viewshed polygon (no fallback circles — fetch silently)
        const cached = _viewshedCache[u.id];
        if (cached && cached.geometry && cached.geometry.coordinates) {
            const coords = cached.geometry.coordinates[0];
            const latlngs = coords.map(c => [c[1], c[0]]);
            const smoothed = _smoothPolygon(latlngs, 2);
            _hoverLayer.addLayer(L.polygon(smoothed, {
                color: accent,
                weight: 1.5,
                opacity: 0.6,
                fillColor: accent,
                fillOpacity: 0.10,
                interactive: false,
            }));

            // Direct fire range: clip viewshed polygon to fire range
            if (!isIndirect && fireRange < detRange * 0.95) {
                const clipped = _clipViewshedToRange(coords, u.lat, u.lon, fireRange);
                const smoothedFire = _smoothPolygon(clipped, 2);
                _hoverLayer.addLayer(L.polygon(smoothedFire, {
                    color: '#ff9800',
                    weight: 1.5,
                    opacity: 0.6,
                    fillColor: '#ff9800',
                    fillOpacity: 0.10,
                    interactive: false,
                }));
            }
        } else if (cached === false) {
            // Viewshed fetch failed — show a fallback dashed circle
            _hoverLayer.addLayer(L.circle(pos, {
                radius: detRange,
                color: accent,
                weight: 1,
                opacity: 0.4,
                dashArray: '6,4',
                fillColor: accent,
                fillOpacity: 0.05,
                interactive: false,
            }));
        } else {
            // No viewshed cached yet — fetch in background, redraw when ready
            _fetchViewshed(u.id).then(() => {
                if (_hoveredUnitId === u.id) _drawHoverRanges(u);
            });
        }

        // Indirect fire range: always a circle (not affected by LOS)
        if (isIndirect) {
            _hoverLayer.addLayer(L.circle(pos, {
                radius: fireRange,
                color: '#ff9800',
                weight: 1.5,
                opacity: 0.6,
                dashArray: '6,4',
                fillColor: '#ff9800',
                fillOpacity: 0.08,
                interactive: false,
            }));
        }
    }

    function _buildPopupHtml(u) {
        const canSel = _canSelect(u);
        const canAsgn = _canAssign(u);
        const userId = KSessionUI.getUserId();
        const isAssignedToMe = u.assigned_user_ids && u.assigned_user_ids.includes(userId);

        const pers = PERSONNEL[u.unit_type] || DEFAULT_PERSONNEL;
        const status = u.unit_status || 'idle';
        const statusIcon = STATUS_ICONS[status] || '•';
        const statusColor = STATUS_COLORS[status] || '#aaa';

        let html = `<b>${u.name}</b><br>`;
        html += `<span style="color:#888">${u.unit_type}</span> <span style="font-size:10px;color:#aaa;">(${pers} pers)</span><br>`;
        html += `Side: <b>${u.side}</b><br>`;
        html += `Status: <span style="color:${statusColor};font-weight:600;">${statusIcon} ${status}</span><br>`;

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
        const eyeH = UNIT_EYE_HEIGHTS[u.unit_type] || DEFAULT_UNIT_EYE_HEIGHT;
        const eyeTag = eyeH > DEFAULT_UNIT_EYE_HEIGHT ? ` <span style="color:#a5d6a7">(${eyeH}m)</span>` : '';
        html += `<span style="font-size:10px;color:#64b5f6">👁 ${_fmtDist(detR)}${eyeTag}</span>`;
        html += ` <span style="font-size:10px;color:#ff9800">🎯 ${_fmtDist(fireR)}</span><br>`;

        // Current task info
        if (u.current_task && u.current_task.type) {
            let taskStr = u.current_task.type;
            const tSpeed = u.current_task.speed;
            const tSpeedOpt = tSpeed && SPEED_OPTIONS[tSpeed];
            if (tSpeedOpt) taskStr += ` ${tSpeedOpt.icon}`;
            if (u.current_task.target_location) {
                const tLat = u.current_task.target_location.lat?.toFixed(4);
                const tLon = u.current_task.target_location.lon?.toFixed(4);
                const snail = u.current_task.target_snail;
                taskStr += snail ? ` → ${snail} (${tLat}, ${tLon})` : ` → ${tLat}, ${tLon}`;
            }
            html += `<span style="font-size:10px;color:#ffd740;">📋 Task: ${taskStr}</span><br>`;
        }

        // ── Chain of Command info ────────────────────────
        if (u.commanding_user_name) {
            const isSelfCO = u.assigned_user_names && u.assigned_user_names.length > 0
                && u.assigned_user_names.includes(u.commanding_user_name);
            if (isSelfCO) {
                html += `<span style="font-size:10px;color:#81c784;">⭐ CO: ${u.commanding_user_name}</span><br>`;
            } else {
                html += `<span style="font-size:10px;color:#90caf9;">⬆ CO: ${u.commanding_user_name}</span><br>`;
            }
        }

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

        return html;
    }

    /** Save unit position via admin API after drag. */
    async function _saveUnitPosition(unitId, lat, lon) {
        const token = KSessionUI.getToken();
        const sessionId = KSessionUI.getSessionId();
        if (!token || !sessionId) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sessionId}/units/${unitId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ lat, lon }),
            });
            if (!resp.ok) {
                console.warn('Save unit position failed:', resp.status);
            }
        } catch (err) {
            console.warn('Save unit position error:', err);
        }
    }

    /** Enable/disable admin drag-and-drop mode. */
    function setAdminDrag(enabled) {
        _adminDragEnabled = enabled;
        // Re-render to apply draggable state to markers
        if (allUnitsData.length > 0) {
            render(allUnitsData);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Unit Context Menu (right-click) ───────────────
    // ══════════════════════════════════════════════════

    let _unitCtxMenuEl = null;

    function _createUnitContextMenu() {
        if (_unitCtxMenuEl) return _unitCtxMenuEl;
        const div = document.createElement('div');
        div.id = 'unit-ctx-menu';
        div.className = 'ctx-menu';
        div.style.display = 'none';
        document.body.appendChild(div);
        _unitCtxMenuEl = div;

        // Close on click outside
        document.addEventListener('click', (e) => {
            if (!div.contains(e.target)) _closeUnitContextMenu();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') _closeUnitContextMenu();
        });

        return div;
    }

    function _closeUnitContextMenu() {
        if (_unitCtxMenuEl) _unitCtxMenuEl.style.display = 'none';
    }

    function _showUnitContextMenu(u, e) {
        const menu = _createUnitContextMenu();
        const canSel = _canSelect(u);
        const canAsgn = _canAssign(u);
        const isSel = selectedUnitIds.has(u.id);
        const status = u.unit_status || 'idle';
        const statusIcon = STATUS_ICONS[status] || '•';
        const statusColor = STATUS_COLORS[status] || '#aaa';

        const userId = KSessionUI.getUserId();
        const isAssignedToMe = u.assigned_user_ids && u.assigned_user_ids.includes(userId);

        const pers = PERSONNEL[u.unit_type] || DEFAULT_PERSONNEL;
        const detR = u.detection_range_m || 2000;
        const fireR = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;

        const sideColor = u.side === 'blue' ? '#4fc3f7' : '#ef5350';
        const strPct = u.strength != null ? Math.round(u.strength * 100) : 100;
        const morPct = u.morale != null ? Math.round(u.morale * 100) : 90;
        const ammPct = u.ammo != null ? Math.round(u.ammo * 100) : 100;
        const supPct = u.suppression != null ? Math.round(u.suppression * 100) : 0;

        const strClr = strPct > 60 ? '#4caf50' : strPct > 30 ? '#ff9800' : '#f44336';
        const morClr = morPct > 60 ? '#64b5f6' : morPct > 30 ? '#ff9800' : '#f44336';
        const ammClr = ammPct > 50 ? '#81c784' : ammPct > 20 ? '#ff9800' : '#f44336';
        const supClr = supPct > 50 ? '#f44336' : supPct > 20 ? '#ff9800' : '#aaa';

        const statusBg = statusColor + '22';

        // Derive display status: if moving, include speed label
        let displayStatus = status;
        let displayStatusIcon = statusIcon;
        const taskSpeed = u.current_task && u.current_task.speed;
        if (status === 'moving' && taskSpeed && SPEED_OPTIONS[taskSpeed]) {
            displayStatusIcon = SPEED_OPTIONS[taskSpeed].icon;
            displayStatus = `${taskSpeed}`;
        }

        // Build elegant card with stat bars
        let html = `<div class="unit-info-card">`;
        html += `<div class="unit-info-header">`;
        html += `<div class="unit-info-side-bar" style="background:${sideColor};"></div>`;
        html += `<div class="unit-info-title">`;
        html += `<div class="unit-info-name">${u.name}</div>`;
        html += `<div class="unit-info-type">${u.unit_type.replace(/_/g, ' ')} · ${pers} personnel</div>`;
        html += `</div>`;
        html += `<div class="unit-info-status"><span class="unit-status-badge" style="background:${statusBg};color:${statusColor};">${displayStatusIcon} ${displayStatus}</span></div>`;
        html += `</div>`;

        // ── Stat bars (visual & compact) ──
        html += `<div class="unit-info-stats">`;
        html += _buildStatBar('STR', strPct, strClr);
        html += _buildStatBar('MOR', morPct, morClr);
        html += _buildStatBar('AMM', ammPct, ammClr);
        if (supPct > 0) {
            html += _buildStatBar('SUP', supPct, supClr);
        }
        html += `</div>`;

        // ── Ranges and capabilities ──
        html += `<div class="unit-info-ranges">`;
        html += `<span title="Detection range" style="color:#64b5f6;">👁 ${_fmtDist(detR)}</span>`;
        html += `<span title="Fire range" style="color:#ff9800;">🎯 ${_fmtDist(fireR)}</span>`;
        if (u.move_speed_mps) {
            const speedOpt = taskSpeed && SPEED_OPTIONS[taskSpeed];
            const speedIcon = speedOpt ? speedOpt.icon : '⚡';
            const speedClr = speedOpt ? speedOpt.color : '#81c784';
            html += `<span title="Movement speed (${taskSpeed || 'base'})" style="color:${speedClr};">${speedIcon} ${u.move_speed_mps.toFixed(1)}m/s</span>`;
        }
        html += `</div>`;

        // ── Current task ──
        if (u.current_task && u.current_task.type) {
            const taskType = u.current_task.type;
            const tSpeed = u.current_task.speed;
            const tSpeedOpt = tSpeed && SPEED_OPTIONS[tSpeed];
            const tSpeedStr = tSpeedOpt ? ` ${tSpeedOpt.icon} ${tSpeedOpt.label}` : '';
            html += `<div style="padding:2px 12px 3px;font-size:10px;">`;
            html += `<span style="color:#ffd740;">📋 Task: <b>${taskType}</b>${tSpeedStr}</span>`;
            if (u.current_task.target_location) {
                const tLat = u.current_task.target_location.lat?.toFixed(4);
                const tLon = u.current_task.target_location.lon?.toFixed(4);
                const snail = u.current_task.target_snail;
                if (snail) {
                    html += ` <span style="color:#aaa;">→ ${snail} (${tLat}, ${tLon})</span>`;
                } else {
                    html += ` <span style="color:#aaa;">→ ${tLat}, ${tLon}</span>`;
                }
            }
            html += `</div>`;
        }

        // ── Communications status ──
        if (u.comms_status && u.comms_status !== 'operational') {
            const commsClr = u.comms_status === 'degraded' ? '#ff9800' : '#f44336';
            html += `<div style="padding:1px 12px 3px;font-size:10px;color:${commsClr};">📡 Comms: ${u.comms_status}</div>`;
        }

        // ── Formation info ──
        const formation = u.formation || (u.capabilities && u.capabilities.formation);
        if (formation) {
            const fObj = FORMATIONS.find(f => f.key === formation);
            const fLabel = fObj ? `${fObj.icon} ${fObj.label}` : formation;
            html += `<div style="padding:1px 12px 3px;font-size:10px;color:#b39ddb;">🔲 Formation: ${fLabel}</div>`;
        }

        // ── Heading info ──
        if (u.heading_deg != null && u.heading_deg !== 0) {
            html += `<div style="padding:1px 12px 3px;font-size:10px;color:#90caf9;">🧭 Heading: ${Math.round(u.heading_deg)}°</div>`;
        }

        // ── Command & assignment info ──
        const hasCmdInfo = u.commanding_user_name || (u.assigned_user_names && u.assigned_user_names.length > 0);
        if (hasCmdInfo) {
            html += `<div class="unit-info-command">`;
            if (u.commanding_user_name) {
                const isSelfCO = u.assigned_user_names && u.assigned_user_names.includes(u.commanding_user_name);
                const coColor = isSelfCO ? '#81c784' : '#90caf9';
                html += `<div class="unit-info-command-line"><span style="color:${coColor};">⬆ CO: <b>${u.commanding_user_name}</b></span></div>`;
            }
            if (u.assigned_user_names && u.assigned_user_names.length > 0) {
                const meTag = isAssignedToMe ? ' <span style="color:#4fc3f7;">(you)</span>' : '';
                html += `<div class="unit-info-command-line"><span style="color:#81c784;">👤 Assigned: <b>${u.assigned_user_names.join(', ')}</b>${meTag}</span></div>`;
            }
            html += `</div>`;
        } else {
            html += `<div style="padding:2px 12px 4px;font-size:10px;color:#555;">Unassigned</div>`;
        }

        // ── Parent unit info ──
        if (u.parent_unit_id) {
            const parent = allUnitsData.find(p => p.id === u.parent_unit_id);
            if (parent) {
                html += `<div style="padding:0 12px 4px;font-size:10px;color:#777;">↳ Part of: ${parent.name}</div>`;
            }
        }

        html += `</div>`; // end unit-info-card

        // ── Action items ──
        const _isAdminMode = typeof KAdmin !== 'undefined' && KAdmin.isUnlocked();
        if (canSel) {
            const selLabel = isSel ? '✓ Deselect' : '☐ Select';
            html += `<div class="ctx-item" data-action="select">${selLabel}</div>`;
        }
        if (canSel) {
            html += `<div class="ctx-item" data-action="rename">✏ Rename</div>`;
        }
        // Formation, Move, Stop — admin-only (direct manipulation bypasses orders)
        if (canSel && _isAdminMode) {
            html += `<div class="ctx-item" data-action="formation">🔲 Formation ▸</div>`;
            html += `<div class="ctx-item" data-action="move">🚶 Set Move ▸</div>`;
            html += `<div class="ctx-item" data-action="stop">⏹ Stop</div>`;
        }
        if (canSel) {
            html += `<div class="ctx-item" data-action="split">✂ Split Unit</div>`;
            // Merge: show if there are nearby units (<50m) of the same principal type
            const principalType = _getPrincipalType(u.unit_type);
            const nearbyMergeable = allUnitsData.filter(ou => {
                if (ou.id === u.id || ou.side !== u.side || ou.is_destroyed) return false;
                if (_getPrincipalType(ou.unit_type) !== principalType) return false;
                // Distance check: only show units within 50m
                if (u.lat == null || u.lon == null || ou.lat == null || ou.lon == null) return false;
                const dist = _haversineDist(u.lat, u.lon, ou.lat, ou.lon);
                return dist <= 50;
            });
            if (nearbyMergeable.length > 0) {
                html += `<div class="ctx-item" data-action="merge">🔗 Merge Unit ▸</div>`;
            }
        }
        // Delete unit — admin only
        if (_isAdminMode) {
            html += `<div class="ctx-item ctx-item-danger" data-action="delete">🗑 Delete Unit</div>`;
        }
        if (canAsgn) {
            const assignLabel = isAssignedToMe ? '✕ Unassign me' : '+ Assign to me';
            html += `<div class="ctx-item" data-action="assign">${assignLabel}</div>`;
        }

        menu.innerHTML = html;

        // Position the menu
        menu.style.left = e.clientX + 'px';
        menu.style.top = e.clientY + 'px';
        menu.style.display = 'block';

        // Ensure menu stays within viewport
        const rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 5) + 'px';
        if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 5) + 'px';

        // Bind actions
        menu.querySelectorAll('.ctx-item').forEach(item => {
            item.addEventListener('click', (evt) => {
                evt.stopPropagation(); // Prevent document handler from closing sub-menus
                const action = item.dataset.action;
                _closeUnitContextMenu();
                if (action === 'select') {
                    _selectUnit(u.id, false);
                } else if (action === 'rename') {
                    _renameUnit(u);
                } else if (action === 'assign') {
                    assignToMe(u.id);
                } else if (action === 'formation') {
                    _showFormationPicker(u, e);
                } else if (action === 'move') {
                    _showMovePicker(u, e);
                } else if (action === 'stop') {
                    _stopUnit(u);
                } else if (action === 'split') {
                    _splitUnit(u);
                } else if (action === 'merge') {
                    _showMergePicker(u, e);
                } else if (action === 'delete') {
                    _deleteUnit(u);
                }
            });
        });
    }

    /** Build an HTML stat bar row for the unit info card. */
    function _buildStatBar(label, pct, color) {
        return `<div class="unit-stat-row">
            <span class="unit-stat-label">${label}</span>
            <div class="unit-stat-bar"><div class="unit-stat-fill" style="width:${pct}%;background:${color};"></div></div>
            <span class="unit-stat-value" style="color:${color};">${pct}%</span>
        </div>`;
    }

    /** Rename a unit via API. */
    async function _renameUnit(u) {
        const newName = prompt('Rename unit:', u.name);
        if (!newName || newName.trim() === u.name) return;
        const token = KSessionUI.getToken();
        const sessionId = KSessionUI.getSessionId();
        if (!token || !sessionId) return;

        try {
            const resp = await fetch(`/api/sessions/${sessionId}/units/${u.id}/rename`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ name: newName.trim() }),
            });
            if (resp.ok) {
                // Update local data and re-render
                u.name = newName.trim();
                render(allUnitsData);
                // Refresh CoC tree
                try { KAdmin.loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Rename failed');
            }
        } catch (err) { alert(err.message); }
    }

    // ══════════════════════════════════════════════════
    // ── Formation Picker ─────────────────────────────
    // ══════════════════════════════════════════════════

    function _showFormationPicker(u, origEvent) {
        const menu = _createUnitContextMenu();
        const curFormation = u.formation || (u.capabilities && u.capabilities.formation) || '';

        let html = '<div class="ctx-menu-header">Formation</div>';
        FORMATIONS.forEach(f => {
            const active = curFormation === f.key ? ' style="color:#4fc3f7;font-weight:700;"' : '';
            html += `<div class="ctx-item" data-formation="${f.key}"${active}>${f.icon} ${f.label}</div>`;
        });

        menu.innerHTML = html;
        menu.style.left = origEvent.clientX + 'px';
        menu.style.top = origEvent.clientY + 'px';
        menu.style.display = 'block';

        const rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 5) + 'px';
        if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 5) + 'px';

        menu.querySelectorAll('.ctx-item').forEach(item => {
            item.addEventListener('click', async (evt) => {
                evt.stopPropagation();
                _closeUnitContextMenu();
                const formation = item.dataset.formation;
                const token = KSessionUI.getToken();
                const sessionId = KSessionUI.getSessionId();
                if (!token || !sessionId) return;
                try {
                    const resp = await fetch(`/api/sessions/${sessionId}/units/${u.id}/formation`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                        body: JSON.stringify({ formation }),
                    });
                    if (resp.ok) {
                        if (!u.capabilities) u.capabilities = {};
                        u.capabilities.formation = formation;
                        u.formation = formation;
                        KGameLog.addEntry(`${u.name} formation → ${formation}`, 'info');
                    } else {
                        const d = await resp.json().catch(() => ({}));
                        alert(d.detail || 'Set formation failed');
                    }
                } catch (err) { alert(err.message); }
            });
        });
    }

    // ══════════════════════════════════════════════════
    // ── Move Picker (speed + map click for target) ───
    // ══════════════════════════════════════════════════

    function _showMovePicker(u, origEvent) {
        const menu = _createUnitContextMenu();
        let html = '<div class="ctx-menu-header">Move Speed</div>';
        for (const [key, opt] of Object.entries(SPEED_OPTIONS)) {
            const mps = _getUnitSpeed(u.unit_type, key);
            const kmh = _mpsToKmh(mps);
            html += `<div class="ctx-item" data-speed="${key}">${opt.icon} ${opt.label} <span style="color:#888;font-size:10px;">(~${kmh} km/h)</span></div>`;
        }
        menu.innerHTML = html;
        menu.style.left = origEvent.clientX + 'px';
        menu.style.top = origEvent.clientY + 'px';
        menu.style.display = 'block';

        const rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 5) + 'px';
        if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 5) + 'px';

        menu.querySelectorAll('.ctx-item').forEach(item => {
            item.addEventListener('click', (evt) => {
                evt.stopPropagation();
                _closeUnitContextMenu();
                const speed = item.dataset.speed;
                _startMoveTargetPick(u, speed);
            });
        });
    }

    function _startMoveTargetPick(u, speed) {
        if (!_map) return;
        _setMovePending = { unitId: u.id, speed, unitName: u.name };
        _map.getContainer().classList.add('pick-mode-active');

        const banner = document.createElement('div');
        banner.className = 'pick-mode-banner';
        banner.id = 'move-pick-banner';
        banner.textContent = `🖱 Click on map to set move target for ${u.name} — ESC to cancel`;
        document.body.appendChild(banner);

        const _cancelPick = () => {
            _setMovePending = null;
            _map.getContainer().classList.remove('pick-mode-active');
            const b = document.getElementById('move-pick-banner');
            if (b) b.remove();
            _map.off('click', _onMovePickClick);
            document.removeEventListener('keydown', _onMovePickKey);
        };

        const _onMovePickClick = async (e) => {
            if (!_setMovePending) return;
            const { unitId, speed, unitName } = _setMovePending;
            _cancelPick();

            const token = KSessionUI.getToken();
            const sessionId = KSessionUI.getSessionId();
            if (!token || !sessionId) return;

            try {
                const resp = await fetch(`/api/sessions/${sessionId}/units/${unitId}/move`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ target_lat: e.latlng.lat, target_lon: e.latlng.lng, speed }),
                });
                if (resp.ok) {
                    const updated = await resp.json();
                    // Update local data
                    const idx = allUnitsData.findIndex(au => au.id === unitId);
                    if (idx >= 0) {
                        Object.assign(allUnitsData[idx], updated);
                    }
                    render(allUnitsData);
                    const snail = updated.current_task && updated.current_task.target_snail;
                    const coordStr = `${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)}`;
                    const destStr = snail ? `${snail} (${coordStr})` : coordStr;
                    KGameLog.addEntry(`${unitName} moving ${speed} → ${destStr}`, 'info');
                } else {
                    const d = await resp.json().catch(() => ({}));
                    alert(d.detail || 'Move command failed');
                }
            } catch (err) { alert(err.message); }
        };

        const _onMovePickKey = (e) => {
            if (e.key === 'Escape') _cancelPick();
        };

        _map.once('click', _onMovePickClick);
        document.addEventListener('keydown', _onMovePickKey, { once: true });
    }

    // ══════════════════════════════════════════════════
    // ── Stop Unit ────────────────────────────────────
    // ══════════════════════════════════════════════════

    async function _stopUnit(u) {
        const token = KSessionUI.getToken();
        const sessionId = KSessionUI.getSessionId();
        if (!token || !sessionId) return;

        try {
            const resp = await fetch(`/api/sessions/${sessionId}/units/${u.id}/stop`, {
                method: 'PUT',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                u.current_task = null;
                render(allUnitsData);
                KGameLog.addEntry(`${u.name} stopped`, 'info');
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Stop failed');
            }
        } catch (err) { alert(err.message); }
    }

    /** Delete unit via admin API. */
    async function _deleteUnit(u) {
        if (!confirm(`Delete unit "${u.name}"? This cannot be undone.`)) return;
        const token = KSessionUI.getToken();
        const sessionId = KSessionUI.getSessionId();
        if (!token || !sessionId) return;

        try {
            const resp = await fetch(`/api/admin/sessions/${sessionId}/units/${u.id}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                selectedUnitIds.delete(u.id);
                KGameLog.addEntry(`Unit "${u.name}" deleted`, 'info');
                await load(sessionId, token);
                try { KAdmin.loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Delete failed');
            }
        } catch (err) { alert(err.message); }
    }

    // ══════════════════════════════════════════════════
    // ── Split Unit ───────────────────────────────────
    // ══════════════════════════════════════════════════

    async function _splitUnit(u) {
        const pctStr = prompt(`Split "${u.name}" — what % goes to new unit? (10–90):`, '50');
        if (!pctStr) return;
        const pct = parseInt(pctStr);
        if (isNaN(pct) || pct < 10 || pct > 90) { alert('Enter a number between 10 and 90'); return; }
        const ratio = pct / 100;

        // Auto-naming: backend handles it
        const token = KSessionUI.getToken();
        const sessionId = KSessionUI.getSessionId();
        if (!token || !sessionId) return;

        try {
            const resp = await fetch(`/api/sessions/${sessionId}/units/${u.id}/split`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ ratio }),
            });
            if (resp.ok) {
                const data = await resp.json();
                KGameLog.addEntry(`${u.name} split → ${data.original.name} + ${data.new_unit.name}`, 'info');
                // Reload units
                await load(sessionId, token);
                try { KAdmin.loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Split failed');
            }
        } catch (err) { alert(err.message); }
    }

    // ══════════════════════════════════════════════════
    // ── Merge Unit ───────────────────────────────────
    // ══════════════════════════════════════════════════

    function _showMergePicker(u, origEvent) {
        const menu = _createUnitContextMenu();
        const principalType = _getPrincipalType(u.unit_type);
        const nearby = allUnitsData.filter(ou => {
            if (ou.id === u.id || ou.side !== u.side || ou.is_destroyed) return false;
            if (_getPrincipalType(ou.unit_type) !== principalType) return false;
            if (u.lat == null || u.lon == null || ou.lat == null || ou.lon == null) return false;
            return _haversineDist(u.lat, u.lon, ou.lat, ou.lon) <= 50;
        });

        let html = '<div class="ctx-menu-header">Merge Into ' + u.name + '</div>';
        if (nearby.length === 0) {
            html += '<div style="padding:6px 12px;font-size:11px;color:#888;">No compatible units within 50m</div>';
        } else {
            nearby.forEach(ou => {
                const strPct = ou.strength != null ? Math.round(ou.strength * 100) + '%' : '?';
                const dist = _haversineDist(u.lat, u.lon, ou.lat, ou.lon);
                html += `<div class="ctx-item" data-merge-id="${ou.id}">${ou.name} <span style="color:#888;font-size:10px;">(${strPct}, ${Math.round(dist)}m)</span></div>`;
            });
        }

        menu.innerHTML = html;
        menu.style.left = origEvent.clientX + 'px';
        menu.style.top = origEvent.clientY + 'px';
        menu.style.display = 'block';

        const rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 5) + 'px';
        if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 5) + 'px';

        menu.querySelectorAll('.ctx-item').forEach(item => {
            item.addEventListener('click', async (evt) => {
                evt.stopPropagation();
                const mergeId = item.dataset.mergeId;
                _closeUnitContextMenu();
                if (!mergeId) return;

                const mergeUnit = allUnitsData.find(ou => ou.id === mergeId);
                if (!mergeUnit) return;
                if (!confirm(`Merge "${mergeUnit.name}" into "${u.name}"?\nThe merged unit will be removed.`)) return;

                const token = KSessionUI.getToken();
                const sessionId = KSessionUI.getSessionId();
                if (!token || !sessionId) return;

                try {
                    const resp = await fetch(`/api/sessions/${sessionId}/units/${u.id}/merge`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                        body: JSON.stringify({ merge_with_unit_id: mergeId }),
                    });
                    if (resp.ok) {
                        const data = await resp.json();
                        KGameLog.addEntry(`${mergeUnit.name} merged into ${u.name}`, 'info');
                        await load(sessionId, token);
                        try { KAdmin.loadPublicCoC(); } catch(e) {}
                    } else {
                        const d = await resp.json().catch(() => ({}));
                        alert(d.detail || 'Merge failed');
                    }
                } catch (err) { alert(err.message); }
            });
        });
    }

    function _fmtDist(m) {
        return m >= 1000 ? (m / 1000).toFixed(1) + 'km' : m + 'm';
    }

    // ── Principal type extraction ─────────────────
    const SIZE_SUFFIXES = ['_battalion', '_company', '_battery', '_platoon', '_section', '_squad', '_team', '_post', '_unit'];

    function _getPrincipalType(unitType) {
        if (!unitType) return '';
        for (const suffix of SIZE_SUFFIXES) {
            if (unitType.endsWith(suffix)) return unitType.slice(0, -suffix.length);
        }
        return unitType;
    }

    // ── Haversine distance (meters) ────────────────
    function _haversineDist(lat1, lon1, lat2, lon2) {
        const R = 6371000;
        const toRad = (d) => d * Math.PI / 180;
        const dLat = toRad(lat2 - lat1);
        const dLon = toRad(lon2 - lon1);
        const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
        return 2 * R * Math.asin(Math.sqrt(a));
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
        }).then(async resp => {
            if (resp.ok) {
                // Reload units from server to get updated assigned_user_names
                await load(sessionId, token);
                if (_map) _map.closePopup();
                // Refresh CoC tree
                try { KAdmin.loadPublicCoC(); } catch(e) {}
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

    /** Draw a single movement arrow: elegant tapered line (max 300m) with pointed arrowhead. */
    function _drawMovementArrow(from, target, accent) {
        const to = target;

        const dLat = to.lat - from.lat;
        const dLon = to.lng - from.lng;
        const geoLen = Math.sqrt(dLat * dLat + dLon * dLon);

        if (geoLen < 0.00005) return;

        // Cap line at ~300m in geographic degrees (approx 0.0027° at mid-latitudes)
        const MAX_LEN = 0.0027;
        let endLat, endLon, arrowLen;
        if (geoLen > MAX_LEN) {
            const ratio = MAX_LEN / geoLen;
            endLat = from.lat + dLat * ratio;
            endLon = from.lng + dLon * ratio;
            arrowLen = MAX_LEN;
        } else {
            endLat = to.lat;
            endLon = to.lng;
            arrowLen = geoLen;
        }

        // Draw tapered line: multiple segments from thick to thin
        const SEGMENTS = 5;
        const startWeight = 5;
        const endWeight = 1.2;
        // Shorten the line so arrowhead sits at the tip
        const ahLen = arrowLen * 0.18;
        const lineLen = arrowLen - ahLen;

        for (let i = 0; i < SEGMENTS; i++) {
            const t0 = i / SEGMENTS;
            const t1 = (i + 1) / SEGMENTS;
            const lat0 = from.lat + (endLat - from.lat) * (t0 * lineLen / arrowLen);
            const lon0 = from.lng + (endLon - from.lng) * (t0 * lineLen / arrowLen);
            const lat1 = from.lat + (endLat - from.lat) * (t1 * lineLen / arrowLen);
            const lon1 = from.lng + (endLon - from.lng) * (t1 * lineLen / arrowLen);
            const w = startWeight + (endWeight - startWeight) * ((t0 + t1) / 2);
            const op = 0.7 - 0.15 * t0; // slight fade

            _movementArrowsLayer.addLayer(L.polyline(
                [[lat0, lon0], [lat1, lon1]], {
                    color: accent,
                    weight: w,
                    opacity: op,
                    lineCap: 'round',
                    lineJoin: 'round',
                    interactive: false,
                    pane: 'movementArrowsPane',
                }
            ));
        }

        // Arrowhead at the end
        _drawArrowheadOnPane(from.lat, from.lng, endLat, endLon, accent, ahLen);
    }

    /** Draw a sleek triangular arrowhead on the movement arrows pane. */
    function _drawArrowheadOnPane(fromLat, fromLon, toLat, toLon, color, size) {
        size = size || 0.0004;
        const dLat = toLat - fromLat;
        const dLon = toLon - fromLon;
        const angle = Math.atan2(dLon, dLat);
        const spread = 0.35;

        const tip   = [toLat, toLon];
        const left  = [toLat - size * Math.cos(angle - spread), toLon - size * Math.sin(angle - spread)];
        const right = [toLat - size * Math.cos(angle + spread), toLon - size * Math.sin(angle + spread)];
        const notch = size * 0.35;
        const back  = [toLat - notch * Math.cos(angle), toLon - notch * Math.sin(angle)];

        _movementArrowsLayer.addLayer(L.polygon([tip, left, back, right], {
            color: color,
            fillColor: color,
            fillOpacity: 0.85,
            weight: 0.5,
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
            const detRange = u.detection_range_m || 2000;
            const fireRange = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
            const isIndirect = INDIRECT_FIRE_TYPES.has(u.unit_type);

            // ── Selection ring (fixed pixel size — does not zoom) ──
            _selectionLayer.addLayer(L.circleMarker(pos, {
                radius: 20,
                color: accent,
                weight: 2,
                fillColor: accent,
                fillOpacity: 0.07,
                interactive: false,
            }));

            // ── Detection / visibility range (viewshed polygon) ──
            const cached = _viewshedCache[u.id];
            if (cached && cached.geometry && cached.geometry.coordinates) {
                const coords = cached.geometry.coordinates[0];
                const latlngs = coords.map(c => [c[1], c[0]]);
                const smoothed = _smoothPolygon(latlngs, 2);
                _selectionLayer.addLayer(L.polygon(smoothed, {
                    color: accent,
                    weight: 1.5,
                    opacity: 0.6,
                    fillColor: accent,
                    fillOpacity: 0.12,
                    interactive: false,
                }));

                // Direct fire range: clip viewshed to fire range
                if (!isIndirect && fireRange < detRange * 0.95) {
                    const clipped = _clipViewshedToRange(coords, u.lat, u.lon, fireRange);
                    const smoothedFire = _smoothPolygon(clipped, 2);
                    _selectionLayer.addLayer(L.polygon(smoothedFire, {
                        color: '#ff9800',
                        weight: 1.5,
                        opacity: 0.6,
                        fillColor: '#ff9800',
                        fillOpacity: 0.12,
                        interactive: false,
                    }));
                }
            } else if (cached === false) {
                // Viewshed fetch failed — show fallback dashed circle
                _selectionLayer.addLayer(L.circle(pos, {
                    radius: detRange,
                    color: accent,
                    weight: 1,
                    opacity: 0.4,
                    dashArray: '6,4',
                    fillColor: accent,
                    fillOpacity: 0.06,
                    interactive: false,
                }));
            } else {
                // No viewshed cached yet — fetch in background, redraw when ready
                _fetchViewshed(u.id).then(() => {
                    if (selectedUnitIds.has(uid)) _drawSelectionOverlays();
                });
            }

            // ── Indirect fire range: always circle (not LOS-affected) ──
            if (isIndirect) {
                _selectionLayer.addLayer(L.circle(pos, {
                    radius: fireRange,
                    color: '#ff9800',
                    weight: 1.5,
                    opacity: 0.6,
                    dashArray: '6,4',
                    fillColor: '#ff9800',
                    fillOpacity: 0.10,
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
        _viewshedCache = {};
        _viewshedPending = {};
        _viewshedTick = -1;
    }

    function update(units, tick) {
        // Invalidate viewshed cache on tick change
        if (tick !== undefined) _invalidateViewshedCache(tick);

        // Invalidate viewshed for units whose position changed (e.g. admin move, engine tick)
        _invalidateMovedUnitsViewshed(units);

        render(units);
    }

    /**
     * Compare incoming unit positions with cached data and invalidate
     * viewshed entries for units that have moved.
     */
    function _invalidateMovedUnitsViewshed(newUnits) {
        if (!newUnits || !allUnitsData.length) return;
        const oldMap = {};
        allUnitsData.forEach(u => { oldMap[u.id] = u; });
        for (const nu of newUnits) {
            const old = oldMap[nu.id];
            if (!old) continue;
            // If position changed, clear viewshed for this unit
            if (old.lat !== nu.lat || old.lon !== nu.lon) {
                delete _viewshedCache[nu.id];
            }
        }
    }

    function getMarker(unitId) {
        return unitMarkers[unitId] || null;
    }

    return {
        init, load, update, render, getMarker,
        toggle, isVisible,
        toggleSelect, assignToMe,
        getSelectedIds, clearSelection, getAllUnits,
        clearAll, setAdminDrag,
        invalidateViewshedCache: _invalidateViewshedCache,
    };
})();
