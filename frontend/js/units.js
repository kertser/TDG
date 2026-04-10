/**
 * units.js – Fetch and render visible units on the map with military symbols.
 *
 *  Selection:
 *    Left-click        = select unit (replaces previous selection).
 *    Shift+left-click  = add/remove unit from selection.
 *    Alt+left-click    = cycle through stacked/overlapping units.
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

    // ── Pending orders (queued for next tick) ─────────
    // unit_id → {type, target_location, target_snail, speed, order_id}
    let _pendingOrders = {};

    // ── Rubber-band selection state ──────────────────
    let _selectRect = null;
    let _selectStartPt = null;
    let _selectStartLL = null;
    let _isSelecting = false;
    let _shiftHeld = false;

    // ══════════════════════════════════════════════════
    // ── Config (loaded from /config/units_config.json + unit_types.json) ──
    // ══════════════════════════════════════════════════

    /** Display/behavior config loaded from units_config.json */
    let CFG = null;

    // Per-unit-type lookup maps (derived from unit_types.json at init time)
    let PERSONNEL = {};
    let FIRE_RANGE = {};
    let INDIRECT_FIRE_TYPES = new Set();
    let UNIT_EYE_HEIGHTS = {};
    let UNIT_TYPE_SPEEDS = {};
    let STATUS_ICONS = {};
    let STATUS_COLORS = {};
    let SPEED_OPTIONS = {};
    let FORMATIONS = [];
    let SIZE_SUFFIXES = [];

    // Defaults (overridden by config once loaded)
    let DEFAULT_PERSONNEL = 20;
    let DEFAULT_FIRE_RANGE = 400;
    let DEFAULT_UNIT_EYE_HEIGHT = 2.0;
    let DEFAULT_UNIT_SPEEDS = { slow: 1.2, fast: 3.0 };
    let SELECT_THRESHOLD = 6;

    /** Load display config from /config/units_config.json */
    async function _loadConfig() {
        try {
            const resp = await fetch('/config/units_config.json?v=' + Date.now());
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            CFG = await resp.json();
            delete CFG._comment;
        } catch (err) {
            console.warn('[KUnits] Failed to load units_config.json, using built-in defaults:', err);
            CFG = {};
        }

        // Apply defaults from config
        const defs = CFG.defaults || {};
        DEFAULT_PERSONNEL = defs.personnel ?? 20;
        DEFAULT_FIRE_RANGE = defs.fire_range ?? 400;
        DEFAULT_UNIT_EYE_HEIGHT = defs.eye_height ?? 2.0;
        DEFAULT_UNIT_SPEEDS = {
            slow: defs.speed_slow ?? 1.2,
            fast: defs.speed_fast ?? 3.0,
        };
        SELECT_THRESHOLD = (CFG.selection && CFG.selection.threshold_px) ?? 6;

        // Display constants from config
        STATUS_ICONS = CFG.status_icons || {};
        STATUS_COLORS = CFG.status_colors || {};
        SPEED_OPTIONS = CFG.speed_options || {};
        FORMATIONS = CFG.formations || [];
        SIZE_SUFFIXES = CFG.size_suffixes || [];
    }

    /**
     * Build per-unit-type lookup maps from KScenarioBuilder.getUnitTypes().
     * Called after both configs are loaded.
     */
    function _buildTypeMaps() {
        const types = (typeof KScenarioBuilder !== 'undefined') ? KScenarioBuilder.getUnitTypes() : {};

        PERSONNEL = {};
        FIRE_RANGE = {};
        INDIRECT_FIRE_TYPES = new Set();
        UNIT_EYE_HEIGHTS = {};
        UNIT_TYPE_SPEEDS = {};

        for (const [key, info] of Object.entries(types)) {
            if (info.personnel != null) PERSONNEL[key] = info.personnel;
            if (info.fire != null) FIRE_RANGE[key] = info.fire;
            if (info.indirect_fire) INDIRECT_FIRE_TYPES.add(key);
            if (info.eye_height != null) UNIT_EYE_HEIGHTS[key] = info.eye_height;
            if (info.speed_slow != null || info.speed_fast != null) {
                UNIT_TYPE_SPEEDS[key] = {
                    slow: info.speed_slow ?? DEFAULT_UNIT_SPEEDS.slow,
                    fast: info.speed_fast ?? DEFAULT_UNIT_SPEEDS.fast,
                };
            }
        }
    }

    /** Get the base speed (m/s) for a unit type and speed label. */
    function _getUnitSpeed(unitType, speedLabel) {
        const speeds = UNIT_TYPE_SPEEDS[unitType] || DEFAULT_UNIT_SPEEDS;
        return speeds[speedLabel] || speeds.slow;
    }

    /** Format m/s as km/h string. */
    function _mpsToKmh(mps) {
        return (mps * 3.6).toFixed(0);
    }

    // ── Set-move state ──────────────────────────────
    let _setMovePending = null; // {unitId, speed} while waiting for map click

    async function init(map) {
        _map = map;

        // Load display config
        await _loadConfig();

        // Build per-type maps from unit_types.json (already loaded by KScenarioBuilder)
        _buildTypeMaps();

        // Create a custom pane for movement arrows below the default marker pane (z=600)
        const arrowPaneZ = (CFG && CFG.movement_arrow_pane_z) || 350;
        map.createPane('movementArrowsPane');
        map.getPane('movementArrowsPane').style.zIndex = arrowPaneZ;

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
        if (mySide === 'observer' || myRole === 'observer') return false;
        const adminUnlocked = typeof KAdmin !== 'undefined' && KAdmin.isUnlocked();
        if (adminUnlocked) return true;
        if (mySide && mySide !== 'admin' && unit.side !== mySide) {
            return false;
        }
        if (!unit.assigned_user_ids || unit.assigned_user_ids.length === 0) return true;
        if (unit.assigned_user_ids.includes(userId)) return true;
        return _hasCommandAuthority(unit, userId);
    }

    /** Can the current user assign/unassign this unit? */
    function _canAssign(unit) {
        const userId = KSessionUI.getUserId();
        if (!userId) return false;
        const mySide = KSessionUI.getSide();
        const myRole = KSessionUI.getRole();
        if (mySide === 'observer' || myRole === 'observer') return false;
        const adminUnlocked = typeof KAdmin !== 'undefined' && KAdmin.isUnlocked();
        if (adminUnlocked) return true;
        if (mySide && mySide !== 'admin' && unit.side !== mySide) {
            return false;
        }
        if (!unit.assigned_user_ids || unit.assigned_user_ids.length === 0) return true;
        if (unit.assigned_user_ids.includes(userId)) return true;
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

            const rbCfg = (CFG && CFG.selection) || {};
            if (_selectRect) {
                _selectRect.setBounds(L.latLngBounds(_selectStartLL, currentLL));
            } else {
                _selectRect = L.rectangle(
                    L.latLngBounds(_selectStartLL, currentLL),
                    {
                        color: rbCfg.rubber_band_color || '#4fc3f7',
                        weight: rbCfg.rubber_band_weight || 1,
                        fillOpacity: rbCfg.rubber_band_fill_opacity || 0.12,
                        dashArray: rbCfg.rubber_band_dash || '5,4',
                        interactive: false,
                    }
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
            } else if (_selectStartPt && !_isSelecting && selectedUnitIds.size > 0) {
                // Don't deselect units during coordinate picking mode
                if (document.body.classList.contains('map-picking')) {
                    // Picking coordinates — preserve current selection
                } else {
                    const dx = e.clientX - _selectStartPt.x;
                    const dy = e.clientY - _selectStartPt.y;
                    if (Math.abs(dx) < SELECT_THRESHOLD && Math.abs(dy) < SELECT_THRESHOLD) {
                        selectedUnitIds.clear();
                        _drawSelectionOverlays();
                        _updateSelectionUI();
                    }
                }
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
            _invalidateMovedUnitsViewshed(units);
            allUnitsData = units;
            render(units);
            _updateSelectionUI();

            if (selectedUnitIds.size > 0) {
                const ids = Array.from(selectedUnitIds);
                Promise.all(ids.map(id => _fetchViewshed(id)))
                    .then(() => _drawSelectionOverlays());
            }
        } catch (err) {
            console.warn('Units load failed:', err);
        }
    }

    // ── Snail path resolution cache (coord key → snail string) ──
    const _snailCache = {};

    async function _enrichSnailPaths(units) {
        for (const u of units) {
            const task = u.current_task;
            if (!task || !task.target_location) continue;
            if (task.target_snail) continue;
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

    // ── Unit stack cycling state ──
    let _lastStackCycleIdx = {};  // posKey → last selected index
    const STACK_THRESHOLD_DEG = 0.0004; // ~40m

    function _getStackKey(lat, lon) {
        // Round to grid to detect nearby units
        const rLat = Math.round(lat / STACK_THRESHOLD_DEG) * STACK_THRESHOLD_DEG;
        const rLon = Math.round(lon / STACK_THRESHOLD_DEG) * STACK_THRESHOLD_DEG;
        return `${rLat.toFixed(5)},${rLon.toFixed(5)}`;
    }

    function _findStackedUnits(unitId) {
        const u = allUnitsData.find(x => x.id === unitId);
        if (!u || u.lat == null) return [u];
        const stacked = allUnitsData.filter(x =>
            !x.is_destroyed && x.lat != null &&
            Math.abs(x.lat - u.lat) < STACK_THRESHOLD_DEG &&
            Math.abs(x.lon - u.lon) < STACK_THRESHOLD_DEG
        );
        return stacked.length > 1 ? stacked : [u];
    }

    function render(units) {
        if (!unitsLayer) return;
        unitsLayer.clearLayers();
        unitMarkers = {};
        allUnitsData = units;

        // Rebuild type maps in case unit_types.json was reloaded (e.g. admin edit)
        _buildTypeMaps();

        // Eagerly resolve missing snail paths in background
        _enrichSnailPaths(units);

        const zoomScale = _map ? KSymbols.getZoomScale(_map.getZoom()) : 1.0;
        const sideColors = (CFG && CFG.side_colors) || {};
        const defaultDetRange = (CFG && CFG.defaults && CFG.defaults.detection_range) || 2000;
        const tooltipOff = (CFG && CFG.tooltip_offset) || [0, -18];

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

            const isDraggable = _adminDragEnabled;
            // Own-side units render on top of enemy units
            const mySide = KSessionUI.getSide();
            const isOwnSide = u.side === mySide;
            const marker = L.marker([u.lat, u.lon], {
                icon,
                draggable: isDraggable,
                zIndexOffset: isOwnSide ? 1000 : 0,
            });

            // Tooltip with unit name + range summary + size + status
            const detR = u.detection_range_m || defaultDetRange;
            const fireR = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
            const pers = PERSONNEL[u.unit_type] || DEFAULT_PERSONNEL;
            const status = u.unit_status || 'idle';
            const statusColor = STATUS_COLORS[status] || '#aaa';

            // Detect enemy unit for tooltip fog-of-war
            const _myTtSide = KSessionUI.getSide ? KSessionUI.getSide() : null;
            const _isAdminTt = typeof KAdmin !== 'undefined' && KAdmin.isUnlocked();
            const _isEnemyTt = u.is_enemy === true || (_myTtSide && _myTtSide !== 'admin' && _myTtSide !== 'observer' && u.side !== _myTtSide);

            // Stack count for overlapping units
            const _stackCount = units.filter(x => !x.is_destroyed && x.lat != null
                && Math.abs(x.lat - u.lat) < STACK_THRESHOLD_DEG
                && Math.abs(x.lon - u.lon) < STACK_THRESHOLD_DEG).length;
            let ttStatus = status;
            let ttStatusIcon = STATUS_ICONS[status] || '•';
            const ttSpeed = u.current_task && u.current_task.speed;
            if (status === 'moving' && ttSpeed && SPEED_OPTIONS[ttSpeed]) {
                ttStatusIcon = SPEED_OPTIONS[ttSpeed].icon;
                ttStatus = ttSpeed;
            }
            const tooltipEyeH = UNIT_EYE_HEIGHTS[u.unit_type] || DEFAULT_UNIT_EYE_HEIGHT;
            const tooltipEyeTag = tooltipEyeH > DEFAULT_UNIT_EYE_HEIGHT ? ` <span style="color:#a5d6a7">(${tooltipEyeH}m)</span>` : '';
            // Effective personnel: base × strength (floor to show even small casualties)
            const _effPers = Math.max(0, Math.floor(pers * (u.strength != null ? u.strength : 1.0)));
            let tooltipHtml;
            if (_isEnemyTt && !_isAdminTt) {
                // Enemy tooltip: show generic type estimate, NOT real unit name
                const estimateLabel = u.strength_estimate || (u.strength > 0.75 ? 'full' : u.strength > 0.5 ? 'reduced' : u.strength > 0.25 ? 'weakened' : 'critical');
                const _enemyTypeLabel = _getEnemyTypeLabel(u);
                tooltipHtml = `<b>${_enemyTypeLabel}</b> <span style="font-size:10px;color:#ef5350;">[ENEMY]</span>`
                    + (_stackCount > 1 ? ` <span style="background:#ff9800;color:#000;font-size:9px;padding:0 4px;border-radius:3px;font-weight:700;">×${_stackCount}</span>` : '')
                    + `<br><span style="color:${statusColor};font-weight:600;">⚡ ${estimateLabel}</span>`;
            } else {
                tooltipHtml = `<b>${u.name}</b> <span style="font-size:10px;color:#aaa;">(${_effPers}p)</span>`
                    + (_stackCount > 1 ? ` <span style="background:#ff9800;color:#000;font-size:9px;padding:0 4px;border-radius:3px;font-weight:700;">×${_stackCount}</span>` : '')
                    + `<br>`
                    + `<span style="color:${statusColor};font-weight:600;">${ttStatusIcon} ${ttStatus}</span> `
                    + `<span style="color:#64b5f6">👁 ${_fmtDist(detR)}${tooltipEyeTag}</span> `
                    + `<span style="color:#ff9800">🎯 ${_fmtDist(fireR)}</span>`;
            }
            marker.bindTooltip(tooltipHtml, {
                permanent: false,
                direction: 'top',
                offset: tooltipOff,
                className: 'unit-tooltip',
            });

            // HOVER: show range circles
            marker.on('mouseover', () => {
                if (selectedUnitIds.has(u.id)) return;
                _hoveredUnitId = u.id;
                _drawHoverRanges(u);
            });
            marker.on('mouseout', () => {
                if (_hoveredUnitId === u.id) {
                    _hoveredUnitId = null;
                    _hoverLayer.clearLayers();
                }
            });

            // LEFT-CLICK: select/deselect — with Alt+click stack cycling for overlapping units
            marker.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                _closeUnitContextMenu();
                const shiftKey = e.originalEvent && e.originalEvent.shiftKey;
                const altKey = e.originalEvent && e.originalEvent.altKey;
                const stack = _findStackedUnits(u.id);
                if (stack.length > 1 && altKey && !shiftKey) {
                    // Alt+click: cycle through stacked units
                    const posKey = _getStackKey(u.lat, u.lon);
                    const lastIdx = _lastStackCycleIdx[posKey] || 0;
                    const nextIdx = (lastIdx + 1) % stack.length;
                    _lastStackCycleIdx[posKey] = nextIdx;
                    const nextUnit = stack[nextIdx];
                    _selectUnit(nextUnit.id, false);
                    // Bring the selected unit's marker on top of the stack
                    const selMarker = unitMarkers[nextUnit.id];
                    if (selMarker) {
                        selMarker.setZIndexOffset(2000);
                        // Reset other stacked units to normal z-index
                        stack.forEach(su => {
                            if (su.id !== nextUnit.id && unitMarkers[su.id]) {
                                const mySide = KSessionUI.getSide();
                                unitMarkers[su.id].setZIndexOffset(su.side === mySide ? 1000 : 0);
                            }
                        });
                    }
                } else {
                    _selectUnit(u.id, shiftKey);
                }
            });

            // RIGHT-CLICK: context menu
            marker.on('contextmenu', (e) => {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                // Also stop native DOM event so document-level listeners don't close the menu
                if (e.originalEvent) e.originalEvent.stopPropagation();
                _showUnitContextMenu(u, e.originalEvent);
            });

            // DRAG: admin drag-and-drop
            if (isDraggable) {
                marker.on('dragstart', () => {
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

                marker.on('dragend', async () => {
                    const pos = marker.getLatLng();
                    u.lat = pos.lat;
                    u.lon = pos.lng;
                    delete _viewshedCache[u.id];

                    const savePromises = [_saveUnitPosition(u.id, pos.lat, pos.lng)];

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
                            delete _viewshedCache[p.id];
                            savePromises.push(_saveUnitPosition(p.id, newLat, newLng));
                        });
                    }

                    const movedIds = [u.id];
                    if (marker._groupDrag && marker._groupPeers) {
                        marker._groupPeers.forEach(p => movedIds.push(p.id));
                    }

                    marker._groupDrag = false;
                    marker._groupPeers = null;
                    marker._groupDragStart = null;
                    _drawMovementArrows();

                    await Promise.all(savePromises);

                    Promise.all(movedIds.map(id => _fetchViewshed(id)))
                        .then(() => _drawSelectionOverlays());
                });
            }

            unitsLayer.addLayer(marker);
            unitMarkers[u.id] = marker;
        });

        _drawMovementArrows();
        _drawSelectionOverlays();
    }

    // ══════════════════════════════════════════════════
    // ── Viewshed (LOS polygon) Fetch & Cache ─────────
    // ══════════════════════════════════════════════════

    function _fetchViewshed(unitId) {
        if (_viewshedCache[unitId] !== undefined) return Promise.resolve();
        if (_viewshedPending[unitId]) return _viewshedPending[unitId];

        const godView = typeof KAdmin !== 'undefined' && KAdmin.isGodViewEnabled();
        const sessionId = (godView && typeof KAdmin !== 'undefined' && KAdmin.getAdminSessionId)
            ? (KAdmin.getAdminSessionId() || KSessionUI.getSessionId())
            : KSessionUI.getSessionId();
        const token = KSessionUI.getToken();
        if (!sessionId || !token) return Promise.resolve();

        const rays = (CFG && CFG.viewshed && CFG.viewshed.rays) || 72;
        const apiBase = godView
            ? `/api/admin/sessions/${sessionId}/units/${unitId}/viewshed?rays=${rays}`
            : `/api/sessions/${sessionId}/units/${unitId}/viewshed?rays=${rays}`;

        const promise = fetch(
            apiBase,
            { headers: { 'Authorization': `Bearer ${token}` } }
        ).then(resp => {
            if (resp.ok) return resp.json();
            console.warn('Viewshed API error', resp.status, 'for unit', unitId);
            return null;
        }).then(geojson => {
            if (geojson) {
                _viewshedCache[unitId] = geojson;
            } else {
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

    function _invalidateViewshedCache(newTick) {
        if (newTick !== undefined && newTick !== _viewshedTick) {
            _viewshedTick = newTick;
            _viewshedCache = {};
            _viewshedPending = {};
        }
    }

    function _smoothPolygon(latlngs, iterations) {
        if (!latlngs || latlngs.length < 4) return latlngs;
        iterations = iterations || ((CFG && CFG.viewshed && CFG.viewshed.smooth_iterations) || 2);
        let pts = latlngs;
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
        pts.push(pts[0]);
        return pts;
    }

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

    /** Helper: get side accent color from config */
    function _sideColor(side) {
        const sc = (CFG && CFG.side_colors) || {};
        return side === 'blue' ? (sc.blue || '#4fc3f7') : (sc.red || '#ef5350');
    }

    /** Draw hover range overlays for a unit. */
    function _drawHoverRanges(u) {
        _hoverLayer.clearLayers();
        const pos = L.latLng(u.lat, u.lon);
        const accent = _sideColor(u.side);
        const defaultDetRange = (CFG && CFG.defaults && CFG.defaults.detection_range) || 2000;
        const detRange = u.detection_range_m || defaultDetRange;
        const fireRange = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
        const isIndirect = INDIRECT_FIRE_TYPES.has(u.unit_type);
        const vsCfg = (CFG && CFG.viewshed) || {};
        const fireColor = vsCfg.fire_range_color || '#ff9800';
        const fbDash = vsCfg.fallback_dash || '6,4';

        const cached = _viewshedCache[u.id];
        if (cached && cached.geometry && cached.geometry.coordinates) {
            const coords = cached.geometry.coordinates[0];
            const latlngs = coords.map(c => [c[1], c[0]]);
            const smoothed = _smoothPolygon(latlngs);
            _hoverLayer.addLayer(L.polygon(smoothed, {
                color: accent,
                weight: vsCfg.detection_line_weight || 1.5,
                opacity: vsCfg.detection_line_opacity || 0.6,
                fillColor: accent,
                fillOpacity: vsCfg.detection_fill_opacity_hover || 0.10,
                interactive: false,
            }));

            if (!isIndirect && fireRange < detRange * 0.95) {
                const clipped = _clipViewshedToRange(coords, u.lat, u.lon, fireRange);
                const smoothedFire = _smoothPolygon(clipped);
                _hoverLayer.addLayer(L.polygon(smoothedFire, {
                    color: fireColor,
                    weight: vsCfg.detection_line_weight || 1.5,
                    opacity: vsCfg.detection_line_opacity || 0.6,
                    fillColor: fireColor,
                    fillOpacity: vsCfg.detection_fill_opacity_hover || 0.10,
                    interactive: false,
                }));
            }
        } else if (cached === false) {
            _hoverLayer.addLayer(L.circle(pos, {
                radius: detRange,
                color: accent,
                weight: 1,
                opacity: 0.4,
                dashArray: fbDash,
                fillColor: accent,
                fillOpacity: 0.05,
                interactive: false,
            }));
        } else {
            _fetchViewshed(u.id).then(() => {
                if (_hoveredUnitId === u.id) _drawHoverRanges(u);
            });
        }

        if (isIndirect) {
            _hoverLayer.addLayer(L.circle(pos, {
                radius: fireRange,
                color: fireColor,
                weight: vsCfg.detection_line_weight || 1.5,
                opacity: vsCfg.detection_line_opacity || 0.6,
                dashArray: fbDash,
                fillColor: fireColor,
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

        const defaultDetRange = (CFG && CFG.defaults && CFG.defaults.detection_range) || 2000;
        const detR = u.detection_range_m || defaultDetRange;
        const fireR = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
        const eyeH = UNIT_EYE_HEIGHTS[u.unit_type] || DEFAULT_UNIT_EYE_HEIGHT;
        const eyeTag = eyeH > DEFAULT_UNIT_EYE_HEIGHT ? ` <span style="color:#a5d6a7">(${eyeH}m)</span>` : '';
        html += `<span style="font-size:10px;color:#64b5f6">👁 ${_fmtDist(detR)}${eyeTag}</span>`;
        html += ` <span style="font-size:10px;color:#ff9800">🎯 ${_fmtDist(fireR)}</span><br>`;

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
            // Show salvos remaining for fire tasks
            if (u.current_task.type === 'fire' && u.current_task.salvos_remaining != null) {
                taskStr += ` [${u.current_task.salvos_remaining} salvos]`;
            }
            html += `<span style="font-size:10px;color:#ffd740;">📋 Task: ${taskStr}</span><br>`;
        }

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

    function setAdminDrag(enabled) {
        _adminDragEnabled = enabled;
        if (allUnitsData.length > 0) {
            render(allUnitsData);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Unit Context Menu (right-click) ───────────────
    // ══════════════════════════════════════════════════

    /** Generate a generic type/size label for enemy units (fog-of-war: hide real name). */
    function _getEnemyTypeLabel(u) {
        // Map unit_type to a vague category + size estimate
        const type = (u.unit_type || '').toLowerCase();
        let category = 'Unknown unit';
        if (type.includes('infantry') || type.includes('mech')) category = 'Infantry';
        else if (type.includes('tank')) category = 'Armored';
        else if (type.includes('artillery') || type.includes('mortar')) category = 'Artillery';
        else if (type.includes('recon') || type.includes('sniper') || type.includes('observation')) category = 'Recon';
        else if (type.includes('engineer') || type.includes('mine') || type.includes('breacher') || type.includes('avlb')) category = 'Engineer';
        else if (type.includes('logistics')) category = 'Support';
        else if (type.includes('command') || type.includes('headquarters')) category = 'Command';
        // Size suffix
        let size = '';
        if (type.includes('battalion')) size = ' battalion';
        else if (type.includes('company') || type.includes('battery')) size = ' company';
        else if (type.includes('platoon')) size = ' platoon';
        else if (type.includes('section')) size = ' section';
        else if (type.includes('team') || type.includes('squad')) size = ' team';
        return category + size;
    }

    let _unitCtxMenuEl = null;
    let _menuOpenTime = 0;  // timestamp when menu was last opened (guards against same-event close)

    function _createUnitContextMenu() {
        if (_unitCtxMenuEl) return _unitCtxMenuEl;
        const div = document.createElement('div');
        div.id = 'unit-ctx-menu';
        div.className = 'ctx-menu';
        div.style.display = 'none';
        document.body.appendChild(div);
        _unitCtxMenuEl = div;

        // Close on left click outside the menu (not right-click, not inside)
        document.addEventListener('click', (e) => {
            if (e.button === 2) return;  // Ignore right-clicks
            if (!div.contains(e.target)) _closeUnitContextMenu();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') _closeUnitContextMenu();
        });
        // Also close on right-click outside (new context menu replaces old)
        document.addEventListener('contextmenu', (e) => {
            // Guard: don't close if the menu was just opened in the same event cycle
            if (Date.now() - _menuOpenTime < 200) return;
            if (!div.contains(e.target) && div.style.display !== 'none') {
                _closeUnitContextMenu();
            }
        });

        return div;
    }

    function _closeUnitContextMenu() {
        if (_unitCtxMenuEl) _unitCtxMenuEl.style.display = 'none';
    }

    /** Render a compact stat bar (STR/MOR/AMM/SUP) for the context menu info card. */
    function _buildStatBar(label, pct, color) {
        return `<div class="unit-stat-row">
            <span class="unit-stat-label">${label}</span>
            <div class="unit-stat-bar"><div class="unit-stat-fill" style="width:${pct}%;background:${color};"></div></div>
            <span class="unit-stat-value" style="color:${color};">${pct}%</span>
        </div>`;
    }

    function _showUnitContextMenu(u, e) {
        _menuOpenTime = Date.now();
        const canSel = _canSelect(u);
        // Use isWindowOpen() — not isUnlocked() — because isUnlocked() stays true
        // for the entire session even after closing the admin panel.
        const isAdmin = typeof KAdmin !== 'undefined' && KAdmin.isWindowOpen && KAdmin.isWindowOpen();

        // Detect enemy unit — block context menu entirely for non-admins
        const _mySideCtx = KSessionUI.getSide ? KSessionUI.getSide() : null;
        const isEnemy = u.is_enemy === true || (_mySideCtx && _mySideCtx !== 'admin' && _mySideCtx !== 'observer' && u.side !== _mySideCtx);
        if (isEnemy && !isAdmin) {
            return; // No context menu on enemy units for non-admins
        }

        const menu = _createUnitContextMenu();
        const canAsgn = _canAssign(u);
        const isSel = selectedUnitIds.has(u.id);
        const status = u.unit_status || 'idle';
        const statusIcon = STATUS_ICONS[status] || '•';
        const statusColor = STATUS_COLORS[status] || '#aaa';


        const userId = KSessionUI.getUserId();
        const isAssignedToMe = u.assigned_user_ids && u.assigned_user_ids.includes(userId);

        const pers = PERSONNEL[u.unit_type] || DEFAULT_PERSONNEL;
        const defaultDetRange = (CFG && CFG.defaults && CFG.defaults.detection_range) || 2000;
        const detR = u.detection_range_m || defaultDetRange;
        const fireR = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;

        const sideColor = _sideColor(u.side);
        const strPct = u.strength != null ? Math.round(u.strength * 100) : 100;
        const morPct = u.morale != null ? Math.round(u.morale * 100) : 90;
        const ammPct = u.ammo != null ? Math.round(u.ammo * 100) : 100;
        const supPct = u.suppression != null ? Math.round(u.suppression * 100) : 0;

        const strClr = strPct > 60 ? '#4caf50' : strPct > 30 ? '#ff9800' : '#f44336';
        const morClr = morPct > 60 ? '#64b5f6' : morPct > 30 ? '#ff9800' : '#f44336';
        const ammClr = ammPct > 50 ? '#81c784' : ammPct > 20 ? '#ff9800' : '#f44336';
        const supClr = supPct > 50 ? '#f44336' : supPct > 20 ? '#ff9800' : '#aaa';

        const statusBg = statusColor + '22';

        let displayStatus = status;
        let displayStatusIcon = statusIcon;
        const taskSpeed = u.current_task && u.current_task.speed;
        if (status === 'moving' && taskSpeed && SPEED_OPTIONS[taskSpeed]) {
            displayStatusIcon = SPEED_OPTIONS[taskSpeed].icon;
            displayStatus = `${taskSpeed}`;
        }

        let html = `<div class="unit-info-card">`;
        html += `<div class="unit-info-header">`;
        html += `<div class="unit-info-side-bar" style="background:${sideColor};"></div>`;
        html += `<div class="unit-info-title">`;
        if (isEnemy && !isAdmin) {
            // Enemy unit: hide real name, show generic type/size estimate
            const _enemyLabel = _getEnemyTypeLabel(u);
            html += `<div class="unit-info-name">${_enemyLabel}</div>`;
            html += `<div class="unit-info-type" style="color:#ef5350;">Enemy contact</div>`;
        } else {
            const _effPers = Math.max(0, Math.floor(pers * (u.strength != null ? u.strength : 1.0)));
            html += `<div class="unit-info-name">${u.name}</div>`;
            html += `<div class="unit-info-type">${u.unit_type.replace(/_/g, ' ')} · ${_effPers}/${pers} personnel</div>`;
        }
        html += `</div>`;
        html += `<div class="unit-info-status"><span class="unit-status-badge" style="background:${statusBg};color:${statusColor};">${displayStatusIcon} ${displayStatus}</span></div>`;
        html += `</div>`;

        if (isEnemy && !isAdmin) {
            // Enemy unit: show only approximate strength estimate (from fog-of-war quantized data)
            const estimate = u.strength_estimate || (strPct > 75 ? 'full' : strPct > 50 ? 'reduced' : strPct > 25 ? 'weakened' : 'critical');
            const estimateLabels = { full: 'Full strength', reduced: 'Reduced', weakened: 'Weakened', critical: 'Critical' };
            const estimateColors = { full: '#4caf50', reduced: '#ff9800', weakened: '#ff5722', critical: '#f44336' };
            html += `<div style="padding:4px 12px;font-size:11px;color:${estimateColors[estimate] || '#aaa'};">⚡ Estimated condition: <b>${estimateLabels[estimate] || estimate}</b></div>`;
        } else {
            html += `<div class="unit-info-stats">`;
            html += _buildStatBar('STR', strPct, strClr);
            html += _buildStatBar('MOR', morPct, morClr);
            html += _buildStatBar('AMM', ammPct, ammClr);
            if (supPct > 0) {
                html += _buildStatBar('SUP', supPct, supClr);
            }
            html += `</div>`;
        }

        if (!isEnemy || isAdmin) {
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
        }

        if ((!isEnemy || isAdmin) && u.current_task && u.current_task.type) {
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
            // Show salvos remaining for fire tasks
            if (taskType === 'fire' && u.current_task.salvos_remaining != null) {
                html += ` <span style="color:#ff8a65;">[${u.current_task.salvos_remaining} salvos]</span>`;
            }
            html += `</div>`;
        }

        if ((!isEnemy || isAdmin) && u.comms_status && u.comms_status !== 'operational') {
            const commsClr = u.comms_status === 'degraded' ? '#ff9800' : '#f44336';
            html += `<div style="padding:1px 12px 3px;font-size:10px;color:${commsClr};">📡 Comms: ${u.comms_status}</div>`;
        }

        if (!isEnemy || isAdmin) {
            const formation = u.formation || (u.capabilities && u.capabilities.formation);
            if (formation) {
                const fObj = FORMATIONS.find(f => f.key === formation);
                const fLabel = fObj ? `${fObj.icon} ${fObj.label}` : formation;
                html += `<div style="padding:1px 12px 3px;font-size:10px;color:#b39ddb;">🔲 Formation: ${fLabel}</div>`;
            }
        }

        if (u.heading_deg != null && u.heading_deg !== 0) {
            html += `<div style="padding:1px 12px 3px;font-size:10px;color:#90caf9;">🧭 Heading: ${Math.round(u.heading_deg)}°</div>`;
        }

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

        if (u.parent_unit_id) {
            const parent = allUnitsData.find(p => p.id === u.parent_unit_id);
            if (parent) {
                html += `<div style="padding:0 12px 4px;font-size:10px;color:#777;">↳ Part of: ${parent.name}</div>`;
            }
        }

        html += `</div>`;

        const _isAdminMode = typeof KAdmin !== 'undefined' && KAdmin.isUnlocked();
        if (canSel) {
            const selLabel = isSel ? '✓ Deselect' : '☐ Select';
            html += `<div class="ctx-item" data-action="select">${selLabel}</div>`;
        }
        if (canSel) {
            html += `<div class="ctx-item" data-action="rename">✏ Rename</div>`;
        }
        if (canSel && _isAdminMode) {
            html += `<div class="ctx-item" data-action="formation">🔲 Formation ▸</div>`;
            html += `<div class="ctx-item" data-action="move">🚶 Set Move ▸</div>`;
            html += `<div class="ctx-item" data-action="stop">⏹ Stop</div>`;
        }
        if (canSel) {
            html += `<div class="ctx-item" data-action="split">✂ Split Unit</div>`;
            const principalType = _getPrincipalType(u.unit_type);
            const mergeDistM = (CFG && CFG.merge_distance_m) || 50;
            const nearbyMergeable = allUnitsData.filter(ou => {
                if (ou.id === u.id || ou.side !== u.side || ou.is_destroyed) return false;
                if (_getPrincipalType(ou.unit_type) !== principalType) return false;
                if (u.lat == null || u.lon == null || ou.lat == null || ou.lon == null) return false;
                const dist = _haversineDist(u.lat, u.lon, ou.lat, ou.lon);
                return dist <= mergeDistM;
            });
            if (nearbyMergeable.length > 0) {
                html += `<div class="ctx-item" data-action="merge">🔗 Merge Unit ▸</div>`;
            }
        }
        if (_isAdminMode) {
            html += `<div class="ctx-item ctx-item-danger" data-action="delete">🗑 Delete Unit</div>`;
        }
        if (canSel) {
            html += `<div class="ctx-item ctx-item-danger" data-action="disband">⛔ Disband Unit</div>`;
        }
        // Fire Smoke — artillery/mortar units with ammo
        const _smokeTypes = ['artillery_battery', 'artillery_platoon', 'mortar_section', 'mortar_team'];
        if (canSel && _smokeTypes.includes(u.unit_type) && (u.ammo == null || u.ammo > 0)) {
            html += `<div class="ctx-item" data-action="fire_smoke">🌫 Fire Smoke</div>`;
        }
        if (canAsgn) {
            const assignLabel = isAssignedToMe ? '✕ Unassign me' : '+ Assign to me';
            html += `<div class="ctx-item" data-action="assign">${assignLabel}</div>`;
        }

        menu.innerHTML = html;

        menu.style.left = e.clientX + 'px';
        menu.style.top = e.clientY + 'px';
        menu.style.display = 'block';

        const rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 5) + 'px';
        if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 5) + 'px';

        menu.querySelectorAll('.ctx-item').forEach(item => {
            item.addEventListener('click', (evt) => {
                evt.stopPropagation();
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
                } else if (action === 'disband') {
                    _disbandUnit(u);
                } else if (action === 'fire_smoke') {
                    _fireSmoke(u);
                }
            });
        });
    }


    async function _renameUnit(u) {
        const newName = await KDialogs.prompt('Rename unit:', u.name);
        if (!newName || newName.trim() === u.name) return;
        const token = KSessionUI.getToken();
        const sessionId = KSessionUI.getSessionId();
        if (!token || !sessionId) return;

        try {
            const isAdmin = typeof KAdmin !== 'undefined' && KAdmin.isUnlocked();
            let resp;
            if (isAdmin) {
                // Use admin endpoint — bypasses side/authority checks
                resp = await fetch(`/api/admin/sessions/${sessionId}/units/${u.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ name: newName.trim() }),
                });
            } else {
                const headers = { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` };
                // Send admin mode header if admin is unlocked — allows cross-side rename
                if (typeof KAdmin !== 'undefined' && KAdmin.isUnlocked()) {
                    headers['X-Admin-Mode'] = '1';
                }
                resp = await fetch(`/api/sessions/${sessionId}/units/${u.id}/rename`, {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({ name: newName.trim() }),
                });
            }
            if (resp.ok) {
                u.name = newName.trim();
                render(allUnitsData);
                try { KAdmin.loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Rename failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
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
                        await KDialogs.alert(d.detail || 'Set formation failed');
                    }
                } catch (err) { await KDialogs.alert(err.message); }
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
                    await KDialogs.alert(d.detail || 'Move command failed');
                }
            } catch (err) { await KDialogs.alert(err.message); }
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
                const updated = await resp.json();
                // Track pending halt — unit still has current_task until tick
                if (updated.pending_order) {
                    _pendingOrders[u.id] = { type: 'halt', order_id: updated.pending_order.id };
                    // Remove any pending move for this unit
                    if (_pendingOrders[u.id] && _pendingOrders[u.id].type === 'move') {
                        delete _pendingOrders[u.id];
                    }
                    _pendingOrders[u.id] = { type: 'halt' };
                }
                render(allUnitsData);
                KGameLog.addEntry(`📋 ${u.name} ordered: halt (next turn)`, 'order');
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Stop failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    async function _deleteUnit(u) {
        if (!await KDialogs.confirm(`Delete unit "${u.name}"? This cannot be undone.`, {dangerous: true})) return;
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
                // Use god-view-aware refresh to avoid losing red units
                if (typeof KAdmin !== 'undefined' && KAdmin.isGodViewEnabled()) {
                    await KAdmin.refreshMapUnits();
                } else {
                    await load(sessionId, token);
                }
                try { KAdmin.loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Delete failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    // ══════════════════════════════════════════════════
    // ── Disband Unit ─────────────────────────────────

    async function _disbandUnit(u) {
        if (!await KDialogs.confirm(`Disband unit "${u.name}"? This unit will be permanently removed.`, {dangerous: true})) return;
        const token = KSessionUI.getToken();
        const sessionId = KSessionUI.getSessionId();
        if (!token || !sessionId) return;

        try {
            const resp = await fetch(`/api/sessions/${sessionId}/units/${u.id}/disband`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                selectedUnitIds.delete(u.id);
                KGameLog.addEntry(`${u.name} disbanded`, 'info');
                if (typeof KAdmin !== 'undefined' && KAdmin.isGodViewEnabled()) {
                    await KAdmin.refreshMapUnits();
                } else {
                    await load(sessionId, token);
                }
                try { KAdmin.loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Disband failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    // ══════════════════════════════════════════════════
    // ── Fire Smoke ───────────────────────────────────

    let _smokePending = null; // unit waiting for smoke target click

    async function _fireSmoke(u) {
        // Start map-click target selection for smoke fire.
        // When user clicks the map, call the smoke API directly.
        _smokePending = u;
        document.body.style.cursor = 'crosshair';
        KGameLog.addEntry(`Select smoke target location for ${u.name}…`, 'info');

        function _onSmokeClick(e) {
            _map.off('click', _onSmokeClick);
            document.removeEventListener('keydown', _onEscSmoke);
            _map.off('contextmenu', _cancelSmoke);
            document.body.style.cursor = '';
            if (!_smokePending) return;
            const unit = _smokePending;
            _smokePending = null;

            const lat = e.latlng.lat;
            const lon = e.latlng.lng;
            const token = KSessionUI.getToken();
            const sessionId = KSessionUI.getSessionId();
            if (!token || !sessionId) return;

            fetch(`/api/sessions/${sessionId}/units/${unit.id}/fire-smoke`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`,
                },
                body: JSON.stringify({
                    target_lat: lat,
                    target_lon: lon,
                    radius_m: 100,
                    duration_ticks: 3,
                }),
            }).then(resp => {
                if (resp.ok) {
                    KGameLog.addEntry(`${unit.name} firing smoke`, 'info');
                    // Reload map objects to show the new smoke
                    try { KMapObjects.load(sessionId, token); } catch(e2) {}
                } else {
                    resp.json().then(d => {
                        KGameLog.addEntry(`Smoke failed: ${d.detail || 'unknown error'}`, 'warning');
                    }).catch(() => {
                        KGameLog.addEntry(`Smoke fire failed`, 'warning');
                    });
                }
            }).catch(err => {
                KGameLog.addEntry(`Smoke fire error: ${err.message}`, 'warning');
            });
        }

        _map.once('click', _onSmokeClick);

        // Cancel on right-click or Escape
        function _cancelSmoke() {
            _map.off('click', _onSmokeClick);
            document.body.style.cursor = '';
            _smokePending = null;
            document.removeEventListener('keydown', _onEscSmoke);
            _map.off('contextmenu', _cancelSmoke);
        }
        function _onEscSmoke(ev) { if (ev.key === 'Escape') _cancelSmoke(); }
        document.addEventListener('keydown', _onEscSmoke);
        _map.once('contextmenu', _cancelSmoke);
    }

    // ══════════════════════════════════════════════════
    // ── Merge Unit ───────────────────────────────────
    // ══════════════════════════════════════════════════

    function _showMergePicker(u, origEvent) {
        const menu = _createUnitContextMenu();
        const principalType = _getPrincipalType(u.unit_type);
        const mergeDistM = (CFG && CFG.merge_distance_m) || 50;
        const nearby = allUnitsData.filter(ou => {
            if (ou.id === u.id || ou.side !== u.side || ou.is_destroyed) return false;
            if (_getPrincipalType(ou.unit_type) !== principalType) return false;
            if (u.lat == null || u.lon == null || ou.lat == null || ou.lon == null) return false;
            return _haversineDist(u.lat, u.lon, ou.lat, ou.lon) <= mergeDistM;
        });

        let html = '<div class="ctx-menu-header">Merge Into ' + u.name + '</div>';
        if (nearby.length === 0) {
            html += `<div style="padding:6px 12px;font-size:11px;color:#888;">No compatible units within ${mergeDistM}m</div>`;
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
                if (!await KDialogs.confirm(`Merge "${mergeUnit.name}" into "${u.name}"?\nThe merged unit will be removed.`, {title: "Merge Units", dangerous: true})) return;

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
                        await KDialogs.alert(d.detail || 'Merge failed');
                    }
                } catch (err) { await KDialogs.alert(err.message); }
            });
        });
    }

    function _fmtDist(m) {
        return m >= 1000 ? (m / 1000).toFixed(1) + 'km' : m + 'm';
    }

    // ── Principal type extraction ─────────────────
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
                await load(sessionId, token);
                if (_map) _map.closePopup();
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

            // Check for active task arrow
            const target = _extractTarget(u);
            if (target) {
                const taskType = u.current_task && u.current_task.type;
                const isMoving = taskType && ['move', 'attack', 'advance', 'retreat', 'withdraw', 'disengage'].includes(taskType);
                const isEngaging = taskType && ['engage', 'fire'].includes(taskType);
                if (isMoving || isEngaging) {
                    const from = L.latLng(u.lat, u.lon);
                    const to = L.latLng(target.lat, target.lon);
                    const accent = _sideColor(u.side);
                    if (isEngaging) {
                        _drawEngageArrow(from, to, accent);
                    } else {
                        _drawMovementArrow(from, to, accent);
                    }
                }
            }

            // Check for pending order arrow (dashed)
            const pending = _pendingOrders[u.id];
            if (pending && pending.type === 'move' && pending.target_location) {
                const from = L.latLng(u.lat, u.lon);
                const to = L.latLng(pending.target_location.lat, pending.target_location.lon);
                const accent = _sideColor(u.side);
                _drawPendingArrow(from, to, accent);
            }
        });
    }

    /** Draw a dashed engage/fire arrow (lighter, dashed — indicates target, not movement path). */
    function _drawEngageArrow(from, to, accent) {
        const engageColor = '#ff5252';
        const dLat = to.lat - from.lat;
        const dLon = to.lng - from.lng;
        const geoLen = Math.sqrt(dLat * dLat + dLon * dLon);
        if (geoLen < 0.00005) return;

        const MAX_LEN = 0.004;
        let endLat = to.lat, endLon = to.lng;
        if (geoLen > MAX_LEN) {
            const ratio = MAX_LEN / geoLen;
            endLat = from.lat + dLat * ratio;
            endLon = from.lng + dLon * ratio;
        }

        _movementArrowsLayer.addLayer(L.polyline(
            [from, [endLat, endLon]], {
                color: engageColor,
                weight: 2,
                opacity: 0.6,
                dashArray: '6, 4',
                lineCap: 'round',
                interactive: false,
                pane: 'movementArrowsPane',
            }
        ));

        // Crosshair at target
        _movementArrowsLayer.addLayer(L.circleMarker([endLat, endLon], {
            radius: 5,
            color: engageColor,
            fillColor: 'transparent',
            fillOpacity: 0,
            weight: 1.5,
            opacity: 0.6,
            interactive: false,
            pane: 'movementArrowsPane',
        }));
    }

    /** Draw a dashed arrow for a pending (queued) move order. */
    function _drawPendingArrow(from, to, accent) {
        const dLat = to.lat - from.lat;
        const dLon = to.lng - from.lng;
        const geoLen = Math.sqrt(dLat * dLat + dLon * dLon);
        if (geoLen < 0.00005) return;

        _movementArrowsLayer.addLayer(L.polyline(
            [from, to], {
                color: accent,
                weight: 2.5,
                opacity: 0.5,
                dashArray: '8, 6',
                lineCap: 'round',
                interactive: false,
                pane: 'movementArrowsPane',
            }
        ));

        // Small circle at target end
        _movementArrowsLayer.addLayer(L.circleMarker(to, {
            radius: 4,
            color: accent,
            fillColor: accent,
            fillOpacity: 0.3,
            weight: 1.5,
            opacity: 0.5,
            interactive: false,
            pane: 'movementArrowsPane',
        }));
    }

    /** Draw a single movement arrow: elegant tapered line (max 300m) with pointed arrowhead. */
    function _drawMovementArrow(from, target, accent) {
        const to = target;
        const aCfg = (CFG && CFG.movement_arrows) || {};

        const dLat = to.lat - from.lat;
        const dLon = to.lng - from.lng;
        const geoLen = Math.sqrt(dLat * dLat + dLon * dLon);

        const minGeoLen = aCfg.min_geo_len || 0.00005;
        if (geoLen < minGeoLen) return;

        const MAX_LEN = aCfg.max_length_deg || 0.0027;
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

        const SEGMENTS = aCfg.segments || 5;
        const startWeight = aCfg.start_weight || 5;
        const endWeight = aCfg.end_weight || 1.2;
        const ahRatio = aCfg.arrowhead_ratio || 0.18;
        const ahLen = arrowLen * ahRatio;
        const lineLen = arrowLen - ahLen;

        for (let i = 0; i < SEGMENTS; i++) {
            const t0 = i / SEGMENTS;
            const t1 = (i + 1) / SEGMENTS;
            const lat0 = from.lat + (endLat - from.lat) * (t0 * lineLen / arrowLen);
            const lon0 = from.lng + (endLon - from.lng) * (t0 * lineLen / arrowLen);
            const lat1 = from.lat + (endLat - from.lat) * (t1 * lineLen / arrowLen);
            const lon1 = from.lng + (endLon - from.lng) * (t1 * lineLen / arrowLen);
            const w = startWeight + (endWeight - startWeight) * ((t0 + t1) / 2);
            const op = 0.7 - 0.15 * t0;

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

        _drawArrowheadOnPane(from.lat, from.lng, endLat, endLon, accent, ahLen);
    }

    /** Draw a sleek triangular arrowhead on the movement arrows pane. */
    function _drawArrowheadOnPane(fromLat, fromLon, toLat, toLon, color, size) {
        size = size || 0.0004;
        const aCfg = (CFG && CFG.movement_arrows) || {};
        const dLat = toLat - fromLat;
        const dLon = toLon - fromLon;
        const angle = Math.atan2(dLon, dLat);
        const spread = aCfg.arrowhead_spread || 0.35;

        const tip   = [toLat, toLon];
        const left  = [toLat - size * Math.cos(angle - spread), toLon - size * Math.sin(angle - spread)];
        const right = [toLat - size * Math.cos(angle + spread), toLon - size * Math.sin(angle + spread)];
        const notchRatio = aCfg.arrowhead_notch_ratio || 0.35;
        const notch = size * notchRatio;
        const back  = [toLat - notch * Math.cos(angle), toLon - notch * Math.sin(angle)];

        _movementArrowsLayer.addLayer(L.polygon([tip, left, back, right], {
            color: color,
            fillColor: color,
            fillOpacity: aCfg.fill_opacity || 0.85,
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

        const defaultDetRange = (CFG && CFG.defaults && CFG.defaults.detection_range) || 2000;
        const selCfg = (CFG && CFG.selection) || {};
        const ringRadius = selCfg.ring_radius_px || 20;
        const vsCfg = (CFG && CFG.viewshed) || {};
        const fireColor = vsCfg.fire_range_color || '#ff9800';
        const fbDash = vsCfg.fallback_dash || '6,4';

        selectedUnitIds.forEach(uid => {
            const u = allUnitsData.find(unit => unit.id === uid);
            if (!u || u.lat == null || u.lon == null) return;

            const pos = L.latLng(u.lat, u.lon);
            const accent = _sideColor(u.side);
            const detRange = u.detection_range_m || defaultDetRange;
            const fireRange = FIRE_RANGE[u.unit_type] || DEFAULT_FIRE_RANGE;
            const isIndirect = INDIRECT_FIRE_TYPES.has(u.unit_type);

            // Selection ring
            _selectionLayer.addLayer(L.circleMarker(pos, {
                radius: ringRadius,
                color: accent,
                weight: 2,
                fillColor: accent,
                fillOpacity: 0.07,
                interactive: false,
            }));

            // Detection / visibility range (viewshed polygon)
            const cached = _viewshedCache[u.id];
            if (cached && cached.geometry && cached.geometry.coordinates) {
                const coords = cached.geometry.coordinates[0];
                const latlngs = coords.map(c => [c[1], c[0]]);
                const smoothed = _smoothPolygon(latlngs);
                _selectionLayer.addLayer(L.polygon(smoothed, {
                    color: accent,
                    weight: vsCfg.detection_line_weight || 1.5,
                    opacity: vsCfg.detection_line_opacity || 0.6,
                    fillColor: accent,
                    fillOpacity: vsCfg.detection_fill_opacity_selected || 0.12,
                    interactive: false,
                }));

                if (!isIndirect && fireRange < detRange * 0.95) {
                    const clipped = _clipViewshedToRange(coords, u.lat, u.lon, fireRange);
                    const smoothedFire = _smoothPolygon(clipped);
                    _selectionLayer.addLayer(L.polygon(smoothedFire, {
                        color: fireColor,
                        weight: vsCfg.detection_line_weight || 1.5,
                        opacity: vsCfg.detection_line_opacity || 0.6,
                        fillColor: fireColor,
                        fillOpacity: vsCfg.detection_fill_opacity_selected || 0.12,
                        interactive: false,
                    }));
                }
            } else if (cached === false) {
                _selectionLayer.addLayer(L.circle(pos, {
                    radius: detRange,
                    color: accent,
                    weight: 1,
                    opacity: 0.4,
                    dashArray: fbDash,
                    fillColor: accent,
                    fillOpacity: 0.06,
                    interactive: false,
                }));
            } else {
                _fetchViewshed(u.id).then(() => {
                    if (selectedUnitIds.has(uid)) _drawSelectionOverlays();
                });
            }

            if (isIndirect) {
                _selectionLayer.addLayer(L.circle(pos, {
                    radius: fireRange,
                    color: fireColor,
                    weight: vsCfg.detection_line_weight || 1.5,
                    opacity: vsCfg.detection_line_opacity || 0.6,
                    dashArray: fbDash,
                    fillColor: fireColor,
                    fillOpacity: 0.10,
                    interactive: false,
                }));
            }

            // Heading indicator
            const target = _extractTarget(u);
            if (!target && u.heading_deg != null && u.heading_deg !== 0) {
                _drawHeadingIndicator(pos, u.heading_deg, accent);
            }
        });
    }

    function _extractTarget(u) {
        if (!u.current_task) return null;
        const t = u.current_task;
        if (t.target_location && t.target_location.lat != null) {
            return { lat: t.target_location.lat, lon: t.target_location.lon };
        }
        if (t.target_lat != null && t.target_lon != null) {
            return { lat: t.target_lat, lon: t.target_lon };
        }
        // Resolve target_unit_id to position from allUnitsData
        if (t.target_unit_id) {
            const tgt = allUnitsData.find(x => x.id === t.target_unit_id);
            if (tgt && tgt.lat != null && tgt.lon != null) {
                return { lat: tgt.lat, lon: tgt.lon };
            }
        }
        return null;
    }

    function _drawHeadingIndicator(pos, headingDeg, color) {
        const rad = (headingDeg * Math.PI) / 180;
        const dist = (CFG && CFG.heading_indicator_m) || 250;
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

        const haCfg = (CFG && CFG.heading_arrowhead) || {};
        _drawArrowhead(pos.lat, pos.lng, endLat, endLon, color, haCfg.size || 0.00025);
    }

    function _drawArrowhead(fromLat, fromLon, toLat, toLon, color, size) {
        size = size || 0.0005;
        const haCfg = (CFG && CFG.heading_arrowhead) || {};
        const dLat = toLat - fromLat;
        const dLon = toLon - fromLon;
        const angle = Math.atan2(dLon, dLat);
        const spread = haCfg.spread || 0.5;

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
        const ids = Array.from(selectedUnitIds);
        try { KOrders.updateSelectedDisplay(ids); } catch(e) {
            const selDisplay = document.getElementById('selected-units-display');
            if (!selDisplay) return;
            if (ids.length === 0) {
                selDisplay.innerHTML = '<span class="cmd-hint">Select units on the map</span>';
            } else {
                const names = allUnitsData.filter(u => selectedUnitIds.has(u.id)).map(u => u.name);
                selDisplay.innerHTML = names.map(n =>
                    `<span class="orders-unit-chip">${n}</span>`
                ).join(' ');
            }
        }

        // When units are selected, open the command panel to the Orders tab
        if (ids.length > 0) {
            try {
                const panel = document.getElementById('command-panel');
                if (panel) {
                    // Switch to Orders tab
                    document.querySelectorAll('.cmd-tab-btn').forEach(b => b.classList.remove('active'));
                    document.querySelectorAll('.cmd-tab-panel').forEach(p => p.classList.remove('active'));
                    const ordersTabBtn = document.querySelector('.cmd-tab-btn[data-cmd-tab="cmd-orders"]');
                    const ordersTabPanel = document.getElementById('cmd-orders');
                    if (ordersTabBtn) ordersTabBtn.classList.add('active');
                    if (ordersTabPanel) ordersTabPanel.classList.add('active');
                    // Expand the panel
                    panel.classList.add('hovered');
                }
            } catch(e) { /* ignore */ }
        } else {
            // All units deselected — collapse the panel if not pinned
            try {
                const panel = document.getElementById('command-panel');
                if (panel && !panel.classList.contains('expanded')) {
                    panel.classList.remove('hovered');
                    const focused = panel.querySelector(':focus');
                    if (focused) focused.blur();
                }
            } catch(e) { /* ignore */ }
        }
    }

    /** Select all units the current user can command (same side, not observer). */
    function selectAllCommandable() {
        const side = typeof KSessionUI !== 'undefined' ? KSessionUI.getSide() : null;
        const role = typeof KSessionUI !== 'undefined' ? KSessionUI.getRole() : null;
        if (role === 'observer' || side === 'observer') return;

        selectedUnitIds.clear();
        for (const u of allUnitsData) {
            if (u.side === side) {
                selectedUnitIds.add(u.id);
            }
        }
        _drawSelectionOverlays();
        _updateSelectionUI();
    }

    function getAllUnits() {
        return allUnitsData;
    }

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

    // ── Animation state ─────────────────────────────
    let _previousPositions = {};  // unit_id → {lat, lon}
    let _animating = false;
    let _animationFrameId = null;
    const ANIM_DURATION_MS = 800;

    function update(units, tick) {
        if (tick !== undefined) _invalidateViewshedCache(tick);
        _invalidateMovedUnitsViewshed(units);

        // Clear pending orders that have been picked up by the tick engine
        if (units) {
            for (const u of units) {
                if (_pendingOrders[u.id]) {
                    const pending = _pendingOrders[u.id];
                    if (pending.type === 'halt') {
                        if (!u.current_task || u.current_task.type !== 'move') {
                            delete _pendingOrders[u.id];
                        }
                    } else if (pending.type === 'move' && u.current_task && u.current_task.type === 'move') {
                        delete _pendingOrders[u.id];
                    }
                }
            }
        }

        // Snapshot positions BEFORE render for animation
        _previousPositions = {};
        for (const u of allUnitsData) {
            if (u.lat != null && u.lon != null) {
                _previousPositions[u.id] = { lat: u.lat, lon: u.lon };
            }
        }

        render(units);

        // Animate units that moved
        if (units && Object.keys(_previousPositions).length > 0) {
            _animateMovedUnits(units);
        }
    }

    /** Animate markers that moved from old positions to new positions. */
    function _animateMovedUnits(newUnits) {
        const movedUnits = [];
        for (const u of newUnits) {
            if (u.lat == null || u.lon == null || u.is_destroyed) continue;
            const old = _previousPositions[u.id];
            if (!old) continue;
            const marker = unitMarkers[u.id];
            if (!marker) continue;
            const dLat = u.lat - old.lat;
            const dLon = u.lon - old.lon;
            if (Math.abs(dLat) < 0.000001 && Math.abs(dLon) < 0.000001) continue;
            movedUnits.push({ marker, fromLat: old.lat, fromLon: old.lon, toLat: u.lat, toLon: u.lon });
        }

        if (movedUnits.length === 0) return;

        // Set markers to old positions first
        for (const m of movedUnits) {
            m.marker.setLatLng([m.fromLat, m.fromLon]);
        }

        const startTime = performance.now();
        _animating = true;

        function step(now) {
            const elapsed = now - startTime;
            const t = Math.min(1, elapsed / ANIM_DURATION_MS);
            // Ease-out cubic
            const ease = 1 - Math.pow(1 - t, 3);

            for (const m of movedUnits) {
                const lat = m.fromLat + (m.toLat - m.fromLat) * ease;
                const lon = m.fromLon + (m.toLon - m.fromLon) * ease;
                m.marker.setLatLng([lat, lon]);
            }

            if (t < 1) {
                _animationFrameId = requestAnimationFrame(step);
            } else {
                _animating = false;
                _animationFrameId = null;
                // Ensure final positions are exact
                for (const m of movedUnits) {
                    m.marker.setLatLng([m.toLat, m.toLon]);
                }
                _drawMovementArrows();
            }
        }

        _animationFrameId = requestAnimationFrame(step);
    }

    function _invalidateMovedUnitsViewshed(newUnits) {
        if (!newUnits || !allUnitsData.length) return;
        const oldMap = {};
        allUnitsData.forEach(u => { oldMap[u.id] = u; });
        for (const nu of newUnits) {
            const old = oldMap[nu.id];
            if (!old) continue;
            if (old.lat !== nu.lat || old.lon !== nu.lon) {
                delete _viewshedCache[nu.id];
            }
        }
    }

    function _invalidateAllViewsheds() {
        _viewshedCache = {};
        _viewshedPending = {};
    }

    function getMarker(unitId) {
        return unitMarkers[unitId] || null;
    }

    /** Expose config getters for other modules that may need them */
    function getConfig() { return CFG; }
    function getFireRange(unitType) { return FIRE_RANGE[unitType] || DEFAULT_FIRE_RANGE; }
    function getPersonnel(unitType) { return PERSONNEL[unitType] || DEFAULT_PERSONNEL; }
    function getEyeHeight(unitType) { return UNIT_EYE_HEIGHTS[unitType] || DEFAULT_UNIT_EYE_HEIGHT; }
    function getStatusIcon(status) { return STATUS_ICONS[status] || '•'; }
    function getStatusColor(status) { return STATUS_COLORS[status] || '#aaa'; }
    function getSpeedOptions() { return SPEED_OPTIONS; }
    function getFormations() { return FORMATIONS; }
    function isIndirectFire(unitType) { return INDIRECT_FIRE_TYPES.has(unitType); }

    return {
        init, load, update, render, getMarker,
        toggle, isVisible,
        toggleSelect, assignToMe,
        getSelectedIds, clearSelection, selectAllCommandable, getAllUnits,
        clearAll, setAdminDrag,
        invalidateViewshedCache: _invalidateViewshedCache,
        invalidateAllViewsheds: _invalidateAllViewsheds,
        getPendingOrdersCount: () => Object.keys(_pendingOrders).length,
        clearPendingOrders: () => { _pendingOrders = {}; },
        // Config accessors
        getConfig, getFireRange, getPersonnel, getEyeHeight,
        getStatusIcon, getStatusColor, getSpeedOptions, getFormations,
        isIndirectFire,
    };
})();
