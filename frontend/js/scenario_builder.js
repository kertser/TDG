/**
 * scenario_builder.js – Interactive scenario builder.
 *
 * Lets admin place units on the map, configure them, set grid parameters,
 * and save everything as a reusable scenario.
 *
 * Map-click to place units (draggable markers), right-click to edit/delete.
 * Staged units are rendered with milsymbol previews, saved on "Save Scenario".
 */
const KScenarioBuilder = (() => {
    let _map = null;
    let _active = false;
    let _stagedUnits = [];   // {tempId, side, name, unit_type, sidc, lat, lon, ...marker}
    let _stagedLayer = null; // L.layerGroup for staged unit markers
    let _editingIdx = -1;    // index in _stagedUnits currently being edited
    let _scenarioId = null;  // if editing an existing scenario
    let _ctxMenuEl = null;   // right-click context menu element
    let _ctxIdx = -1;        // index of unit in context menu
    let _rangePreviewLayer = null; // range preview layer for builder
    let _gridPreviewLayer = null;  // grid preview layer for builder
    let _pickingOrigin = false;    // true while waiting for map click to set grid origin
    let _sessionGridWasVisible = false;  // remember KGrid state before builder hid it

    // ── Unit type registry (loaded from /config/unit_types.json) ──
    let UNIT_TYPES = {};
    let _defaultUnitTypes = {};  // pristine copy for reset

    /** Load unit types from the external config JSON file. */
    async function _loadUnitTypes() {
        try {
            const resp = await fetch('/config/unit_types.json?v=' + Date.now());
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            // Strip metadata keys (prefixed with _)
            for (const k of Object.keys(data)) {
                if (k.startsWith('_')) delete data[k];
            }
            UNIT_TYPES = data;
            _defaultUnitTypes = JSON.parse(JSON.stringify(data));
            console.log(`[UnitTypes] Loaded ${Object.keys(UNIT_TYPES).length} types from config`);
        } catch (err) {
            console.error('[UnitTypes] Failed to load config/unit_types.json, using empty registry:', err);
            UNIT_TYPES = {};
            _defaultUnitTypes = {};
        }
    }

    /** Reset UNIT_TYPES back to the loaded defaults. */
    function resetUnitTypes() {
        UNIT_TYPES = JSON.parse(JSON.stringify(_defaultUnitTypes));
        _populateTypeDropdown();
    }

    async function init(map) {
        _map = map;
        _stagedLayer = L.layerGroup();
        _rangePreviewLayer = L.layerGroup();
        _gridPreviewLayer = L.layerGroup();
        _initContextMenu();

        // Load unit types from external config file
        await _loadUnitTypes();

        // Populate the unit type dropdown from UNIT_TYPES registry
        _populateTypeDropdown();

        // Auto-update grid preview when grid settings change
        ['sb-grid-origin-lat', 'sb-grid-origin-lon', 'sb-grid-cols', 'sb-grid-rows', 'sb-grid-size'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', () => { if (_active) _updateGridPreview(true); });
        });

        // "Update Grid" button
        const updateGridBtn = document.getElementById('sb-update-grid-btn');
        if (updateGridBtn) {
            updateGridBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (!_active) return;
                _updateGridPreview(true);
            });
        }

        // Pick-origin button
        const pickBtn = document.getElementById('sb-pick-origin-btn');
        if (pickBtn) {
            pickBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (!_active) return;
                _setPickingOrigin(!_pickingOrigin);
            });
        }
    }

    /** Fill the sb-unit-type <select> with entries from UNIT_TYPES. */
    function _populateTypeDropdown() {
        const sel = document.getElementById('sb-unit-type');
        if (!sel) return;
        sel.innerHTML = '';
        for (const [key, info] of Object.entries(UNIT_TYPES)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = info.label || key;
            sel.appendChild(opt);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Activate / Deactivate Builder Mode ───────────
    // ══════════════════════════════════════════════════

    async function activate(scenarioId = null) {
        // Clean up if already active (prevent double click handlers, stale state)
        if (_active) {
            _map.off('click', _onMapClick);
            document.removeEventListener('click', _dismissCtxMenu);
            _dismissCtxMenu();
            _stagedLayer.clearLayers();
            _rangePreviewLayer.clearLayers();
            _gridPreviewLayer.clearLayers();
        } else {
            // Remember whether the session grid was visible before builder opened
            _sessionGridWasVisible = (typeof KGrid !== 'undefined') && KGrid.isVisible();
        }

        // Always hide the session grid while builder is active — only the
        // builder's preview (driven by the form values) should be visible.
        if (typeof KGrid !== 'undefined' && KGrid.isVisible()) KGrid.toggle();

        _active = true;
        _scenarioId = scenarioId;
        _stagedUnits = [];
        _editingIdx = -1;
        if (!_map.hasLayer(_stagedLayer)) _stagedLayer.addTo(_map);
        if (!_map.hasLayer(_rangePreviewLayer)) _rangePreviewLayer.addTo(_map);
        if (!_map.hasLayer(_gridPreviewLayer)) _gridPreviewLayer.addTo(_map);


        // Install map click handler
        _map.on('click', _onMapClick);

        // Dismiss context menu on any click
        document.addEventListener('click', _dismissCtxMenu);

        // Show the builder panel
        const panel = document.getElementById('sb-panel');
        if (panel) panel.style.display = 'block';
        const toggleBtn = document.getElementById('sb-toggle-btn');
        if (toggleBtn) {
            toggleBtn.textContent = '✕ Exit Builder';
            toggleBtn.classList.add('admin-btn-danger');
        }

        // Refresh the unit type dropdown (may have been modified)
        _populateTypeDropdown();

        // If editing an existing scenario, load its data (await to prevent race)
        if (scenarioId) await _loadScenario(scenarioId);

        _refreshUnitList();
        // Render a client-side grid preview from the form values (always force —
        // the session grid is hidden while builder is active so there is no conflict)
        _updateGridPreview(true);
    }

    function deactivate() {
        _active = false;
        _scenarioId = null;
        _stagedUnits = [];
        _editingIdx = -1;
        _setPickingOrigin(false);  // cancel pick mode and restore cursor
        _stagedLayer.clearLayers();
        _rangePreviewLayer.clearLayers();
        _gridPreviewLayer.clearLayers();
        if (_map.hasLayer(_stagedLayer)) _map.removeLayer(_stagedLayer);
        if (_map.hasLayer(_rangePreviewLayer)) _map.removeLayer(_rangePreviewLayer);
        if (_map.hasLayer(_gridPreviewLayer)) _map.removeLayer(_gridPreviewLayer);

        // Restore session grid if it was visible before builder activated
        if (_sessionGridWasVisible && typeof KGrid !== 'undefined' && !KGrid.isVisible()) {
            KGrid.toggle();
        }
        _sessionGridWasVisible = false;


        _map.off('click', _onMapClick);
        document.removeEventListener('click', _dismissCtxMenu);
        _dismissCtxMenu();

        const panel = document.getElementById('sb-panel');
        if (panel) panel.style.display = 'none';
        const toggleBtn = document.getElementById('sb-toggle-btn');
        if (toggleBtn) {
            toggleBtn.textContent = '🗺 Scenario Builder';
            toggleBtn.classList.remove('admin-btn-danger');
        }
    }

    function isActive() { return _active; }

    // ══════════════════════════════════════════════════
    // ── Map Click → Place Unit ───────────────────────
    // ══════════════════════════════════════════════════

    function _setPickingOrigin(active) {
        _pickingOrigin = active;
        const btn = document.getElementById('sb-pick-origin-btn');
        const mapEl = _map && _map.getContainer ? _map.getContainer() : null;
        if (active) {
            if (btn) {
                btn.textContent = '✕ Cancel';
                btn.style.background = '#1a3a5c';
                btn.style.color = '#ef9a9a';
                btn.style.borderColor = '#c62828';
            }
            if (mapEl) mapEl.style.cursor = 'crosshair';
        } else {
            if (btn) {
                btn.textContent = '📍 Pick';
                btn.style.background = '#1a3a5c';
                btn.style.color = '#64b5f6';
                btn.style.borderColor = '#1565c0';
            }
            if (mapEl) mapEl.style.cursor = '';
        }
    }

    function _onMapClick(e) {
        if (!_active) return;

        // If picking grid origin, capture this click as the SW corner
        if (_pickingOrigin) {
            const latEl = document.getElementById('sb-grid-origin-lat');
            const lonEl = document.getElementById('sb-grid-origin-lon');
            if (latEl) latEl.value = e.latlng.lat.toFixed(6);
            if (lonEl) lonEl.value = e.latlng.lng.toFixed(6);
            _setPickingOrigin(false);
            _updateGridPreview(true);  // force show even if session grid exists
            // Pan to show the grid in view
            const cols = parseInt(document.getElementById('sb-grid-cols')?.value) || 8;
            const rows = parseInt(document.getElementById('sb-grid-rows')?.value) || 8;
            const sizeMt = parseFloat(document.getElementById('sb-grid-size')?.value) || 1000;
            const latRad = e.latlng.lat * Math.PI / 180;
            const dLat = (rows * sizeMt) / 111320;
            const dLon = (cols * sizeMt) / (111320 * Math.cos(latRad));
            _map.fitBounds([
                [e.latlng.lat, e.latlng.lng],
                [e.latlng.lat + dLat, e.latlng.lng + dLon],
            ], { padding: [30, 30] });
            return;
        }

        // Don't place if clicking on an existing marker or UI elements
        if (e.originalEvent && e.originalEvent.target.closest &&
            (e.originalEvent.target.closest('.leaflet-marker-icon') ||
             e.originalEvent.target.closest('.leaflet-popup') ||
             e.originalEvent.target.closest('#sidebar') ||
             e.originalEvent.target.closest('#topbar') ||
             e.originalEvent.target.closest('#admin-window') ||
             e.originalEvent.target.closest('.ctx-menu'))) return;

        // Pre-fill the form with clicked coordinates
        _showUnitForm(e.latlng.lat, e.latlng.lng);
    }

    // ══════════════════════════════════════════════════
    // ── Unit Form (create/edit) ──────────────────────
    // ══════════════════════════════════════════════════

    function _showUnitForm(lat, lon, editIdx = -1) {
        _editingIdx = editIdx;
        const existing = editIdx >= 0 ? _stagedUnits[editIdx] : null;

        const form = document.getElementById('sb-unit-form');
        if (!form) return;
        form.style.display = 'block';

        document.getElementById('sb-unit-lat').value = lat.toFixed(6);
        document.getElementById('sb-unit-lon').value = lon.toFixed(6);
        document.getElementById('sb-unit-name').value = existing ? existing.name : '';
        document.getElementById('sb-unit-side').value = existing ? existing.side : 'blue';
        document.getElementById('sb-unit-type').value = existing ? existing.unit_type : 'infantry_platoon';
        // Show as percentages (0-100)
        document.getElementById('sb-unit-strength').value = existing ? Math.round(existing.strength * 100) : 100;
        document.getElementById('sb-unit-ammo').value = existing ? Math.round(existing.ammo * 100) : 100;
        document.getElementById('sb-unit-morale').value = existing ? Math.round(existing.morale * 100) : 90;
        document.getElementById('sb-unit-detection').value = existing ? existing.detection_range_m : 1500;
        document.getElementById('sb-unit-speed').value = existing ? existing.move_speed_mps : 4.0;

        const title = document.getElementById('sb-form-title');
        if (title) title.textContent = existing ? 'Edit Unit' : 'Place Unit';

        // Auto-populate defaults from type
        if (!existing) _onTypeChange();
    }

    function _hideUnitForm() {
        const form = document.getElementById('sb-unit-form');
        if (form) form.style.display = 'none';
        _editingIdx = -1;
    }

    function _onTypeChange() {
        const typeEl = document.getElementById('sb-unit-type');
        const nameEl = document.getElementById('sb-unit-name');
        if (!typeEl) return;
        const info = UNIT_TYPES[typeEl.value];
        const currentName = nameEl ? nameEl.value : '';

        // Check if current name is empty or matches an auto-generated pattern
        // (i.e. "{AnyTypeLabel} {number}")
        let isAutoName = !currentName;
        if (!isAutoName) {
            for (const [, typeInfo] of Object.entries(UNIT_TYPES)) {
                const label = typeInfo.label || '';
                const re = new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s+\\d+$`);
                if (re.test(currentName)) { isAutoName = true; break; }
            }
        }

        if (info && isAutoName) {
            // Auto-generate a name based on the new type
            const side = document.getElementById('sb-unit-side').value;
            const count = _stagedUnits.filter(u => u.side === side && u.unit_type === typeEl.value).length + 1;
            nameEl.value = `${info.label} ${count}`;
        }
        // Auto-fill detection and speed from type defaults
        if (info) {
            const detEl = document.getElementById('sb-unit-detection');
            const spdEl = document.getElementById('sb-unit-speed');
            if (detEl) detEl.value = info.det || 1500;
            if (spdEl) spdEl.value = info.speed || 4.0;
        }
    }

    async function _confirmUnit() {
        const lat = parseFloat(document.getElementById('sb-unit-lat').value);
        const lon = parseFloat(document.getElementById('sb-unit-lon').value);
        const name = document.getElementById('sb-unit-name').value.trim();
        const side = document.getElementById('sb-unit-side').value;
        const unit_type = document.getElementById('sb-unit-type').value;
        // Form values are in percentages (0-100), store as 0.0-1.0
        const strengthPct = parseFloat(document.getElementById('sb-unit-strength').value) || 100;
        const ammoPct = parseFloat(document.getElementById('sb-unit-ammo').value) || 100;
        const moralePct = parseFloat(document.getElementById('sb-unit-morale').value) || 90;
        const strength = Math.max(0, Math.min(1, strengthPct / 100));
        const ammo = Math.max(0, Math.min(1, ammoPct / 100));
        const morale = Math.max(0, Math.min(1, moralePct / 100));

        if (!name) { await KDialogs.alert('Unit name required'); return; }
        if (isNaN(lat) || isNaN(lon)) { await KDialogs.alert('Invalid coordinates'); return; }

        const info = UNIT_TYPES[unit_type] || {};
        const sidc = side === 'red' ? (info.sidc_red || '') : (info.sidc_blue || '');

        // Read detection and speed from form (or fall back to type defaults)
        const detEl = document.getElementById('sb-unit-detection');
        const spdEl = document.getElementById('sb-unit-speed');
        const detection_range_m = detEl ? (parseFloat(detEl.value) || info.det || 1500) : (info.det || 1500);
        const move_speed_mps = spdEl ? (parseFloat(spdEl.value) || info.speed || 4.0) : (info.speed || 4.0);

        const unitData = {
            tempId: _editingIdx >= 0 ? _stagedUnits[_editingIdx].tempId : 'u_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
            side, name, unit_type, sidc, lat, lon,
            strength, ammo, morale,
            move_speed_mps: move_speed_mps,
            detection_range_m: detection_range_m,
            capabilities: {},
        };

        if (_editingIdx >= 0) {
            _stagedUnits[_editingIdx] = unitData;
        } else {
            _stagedUnits.push(unitData);
        }

        _hideUnitForm();
        _renderStagedUnits();
        _refreshUnitList();
    }

    function _deleteUnit(idx) {
        _stagedUnits.splice(idx, 1);
        _hideUnitForm();
        _renderStagedUnits();
        _refreshUnitList();
    }

    // ══════════════════════════════════════════════════
    // ── Render Staged Units on Map ───────────────────
    // ══════════════════════════════════════════════════

    function _renderStagedUnits() {
        _stagedLayer.clearLayers();
        _rangePreviewLayer.clearLayers();

        _stagedUnits.forEach((u, idx) => {
            const icon = KSymbols.createIcon(u.sidc, {
                direction: 0,
                unitType: u.unit_type,
                isHQ: u.unit_type === 'headquarters' || u.unit_type === 'command_post',
            });

            const marker = L.marker([u.lat, u.lon], { icon, draggable: true });

            // Tooltip with unit info + ranges
            const info = UNIT_TYPES[u.unit_type] || {};
            const detR = u.detection_range_m || info.det || 1500;
            const fireR = info.fire || 600;
            const pers = info.personnel || '?';
            const strPct = Math.round((u.strength || 1) * 100);
            const morPct = Math.round((u.morale || 0.9) * 100);
            const ammPct = Math.round((u.ammo || 1) * 100);
            const tooltipHtml = `<b>${u.name}</b> <span style="color:${u.side === 'red' ? '#ef5350' : '#4fc3f7'}">[${u.side}]</span><br>`
                + `<span style="font-size:10px;color:#aaa">${info.label || u.unit_type} (${pers} pers)</span><br>`
                + `<span style="font-size:10px;">Str:${strPct}% Mor:${morPct}% Ammo:${ammPct}%</span><br>`
                + `<span style="color:#64b5f6">👁 ${_fmtDist(detR)}</span> `
                + `<span style="color:#ff9800">🎯 ${_fmtDist(fireR)}</span>`;
            marker.bindTooltip(tooltipHtml, {
                permanent: false,
                direction: 'top',
                offset: [0, -18],
                className: 'unit-tooltip',
            });

            // Hover: show range circles
            marker.on('mouseover', () => {
                _rangePreviewLayer.clearLayers();
                const pos = L.latLng(u.lat, u.lon);
                const accent = u.side === 'red' ? '#ef5350' : '#4fc3f7';
                _rangePreviewLayer.addLayer(L.circle(pos, {
                    radius: detR, color: accent, weight: 1, opacity: 0.3,
                    dashArray: '6,8', fillColor: accent, fillOpacity: 0.02, interactive: false,
                }));
                if (fireR < detR * 0.95) {
                    _rangePreviewLayer.addLayer(L.circle(pos, {
                        radius: fireR, color: '#ff9800', weight: 1, opacity: 0.35,
                        dashArray: '4,5', fillColor: '#ff9800', fillOpacity: 0.03, interactive: false,
                    }));
                }
            });
            marker.on('mouseout', () => {
                _rangePreviewLayer.clearLayers();
            });

            // Drag to reposition
            marker.on('dragend', () => {
                const pos = marker.getLatLng();
                u.lat = pos.lat;
                u.lon = pos.lng;
                _refreshUnitList();
            });

            // Click to edit
            marker.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                _showUnitForm(u.lat, u.lon, idx);
            });

            // Right-click to show context menu
            marker.on('contextmenu', (e) => {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                const origEvt = e.originalEvent || e;
                _showUnitCtxMenu(idx, origEvt.clientX, origEvt.clientY);
            });

            _stagedLayer.addLayer(marker);
        });
    }

    function _fmtDist(m) {
        return m >= 1000 ? (m / 1000).toFixed(1) + 'km' : m + 'm';
    }

    // ══════════════════════════════════════════════════
    // ── Unit List in Sidebar ─────────────────────────
    // ══════════════════════════════════════════════════

    function _refreshUnitList() {
        const listEl = document.getElementById('sb-unit-list');
        const countEl = document.getElementById('sb-unit-count');
        if (countEl) countEl.textContent = _stagedUnits.length;
        if (!listEl) return;

        if (_stagedUnits.length === 0) {
            listEl.innerHTML = '<div style="color:#888;font-size:11px;padding:4px;">Click on map to place units</div>';
            return;
        }

        const blues = _stagedUnits.filter(u => u.side === 'blue');
        const reds = _stagedUnits.filter(u => u.side === 'red');

        let html = '';
        if (blues.length > 0) {
            html += '<div style="color:#4fc3f7;font-size:10px;font-weight:700;margin-bottom:2px;">BLUE (' + blues.length + ')</div>';
            blues.forEach((u, i) => {
                const idx = _stagedUnits.indexOf(u);
                const info = UNIT_TYPES[u.unit_type] || {};
                const strPct = Math.round((u.strength || 1) * 100);
                const morPct = Math.round((u.morale || 0.9) * 100);
                const ammPct = Math.round((u.ammo || 1) * 100);
                const pers = info.personnel || '?';
                html += `<div class="sb-unit-item" onclick="KScenarioBuilder.editUnit(${idx})">
                    <span class="sb-unit-name">${u.name}</span>
                    <span class="sb-unit-type-badge">${u.unit_type}</span>
                    <span style="font-size:9px;color:#aaa;margin-left:auto;">${pers}p S${strPct}% M${morPct}% A${ammPct}%</span>
                    <button class="sb-unit-del" onclick="event.stopPropagation();KScenarioBuilder.removeUnit(${idx})">✕</button>
                </div>`;
            });
        }
        if (reds.length > 0) {
            html += '<div style="color:#ef5350;font-size:10px;font-weight:700;margin:4px 0 2px;">RED (' + reds.length + ')</div>';
            reds.forEach((u) => {
                const idx = _stagedUnits.indexOf(u);
                const info = UNIT_TYPES[u.unit_type] || {};
                const strPct = Math.round((u.strength || 1) * 100);
                const morPct = Math.round((u.morale || 0.9) * 100);
                const ammPct = Math.round((u.ammo || 1) * 100);
                const pers = info.personnel || '?';
                html += `<div class="sb-unit-item sb-unit-red" onclick="KScenarioBuilder.editUnit(${idx})">
                    <span class="sb-unit-name">${u.name}</span>
                    <span class="sb-unit-type-badge">${u.unit_type}</span>
                    <span style="font-size:9px;color:#aaa;margin-left:auto;">${pers}p S${strPct}% M${morPct}% A${ammPct}%</span>
                    <button class="sb-unit-del" onclick="event.stopPropagation();KScenarioBuilder.removeUnit(${idx})">✕</button>
                </div>`;
            });
        }

        listEl.innerHTML = html;
    }

    function editUnit(idx) {
        const u = _stagedUnits[idx];
        if (!u) return;
        _showUnitForm(u.lat, u.lon, idx);
        // Pan to unit
        _map.panTo([u.lat, u.lon]);
    }

    function removeUnit(idx) {
        _deleteUnit(idx);
    }

    // ══════════════════════════════════════════════════
    // ── Save Scenario ────────────────────────────────
    // ══════════════════════════════════════════════════

    async function saveScenario() {
        const title = document.getElementById('sb-scenario-title').value.trim();
        if (!title) { await KDialogs.alert('Scenario title required'); return; }

        const description = document.getElementById('sb-scenario-desc').value.trim();
        const center = _map.getCenter();

        // Grid settings
        const gridOriginLat = parseFloat(document.getElementById('sb-grid-origin-lat').value) || center.lat - 0.04;
        const gridOriginLon = parseFloat(document.getElementById('sb-grid-origin-lon').value) || center.lng - 0.04;
        const gridCols = Math.max(1, Math.min(20, parseInt(document.getElementById('sb-grid-cols').value) || 8));
        const gridRows = Math.max(1, Math.min(20, parseInt(document.getElementById('sb-grid-rows').value) || 8));
        const gridSize = Math.max(100, Math.min(10000, parseInt(document.getElementById('sb-grid-size').value) || 1000));

        const grid_settings = {
            origin_lat: gridOriginLat,
            origin_lon: gridOriginLon,
            orientation_deg: 0,
            base_square_size_m: gridSize,
            columns: gridCols,
            rows: gridRows,
            labeling_scheme: 'alphanumeric',
        };

        // Build initial_units payload
        const blue = _stagedUnits.filter(u => u.side === 'blue').map(_unitToPayload);
        const red = _stagedUnits.filter(u => u.side === 'red').map(_unitToPayload);

        const initial_units = { blue, red, red_agents: [] };

        // Game rules: turn limit, mission, victory conditions
        const turnLimit = parseInt(document.getElementById('sb-turn-limit')?.value) || 0;
        const mission = (document.getElementById('sb-scenario-mission')?.value || '').trim();
        const victoryBlue = (document.getElementById('sb-victory-blue')?.value || '').trim();
        const victoryRed = (document.getElementById('sb-victory-red')?.value || '').trim();

        const objectives = {
            turn_limit: turnLimit,
            mission: mission || null,
            victory_blue: victoryBlue || null,
            victory_red: victoryRed || null,
        };

        const body = {
            title,
            description: description || null,
            map_center_lat: center.lat,
            map_center_lon: center.lng,
            map_zoom: _map.getZoom(),
            grid_settings,
            initial_units,
            objectives,
        };

        try {
            let resp;
            if (_scenarioId) {
                resp = await fetch(`/api/scenarios/${_scenarioId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
            } else {
                resp = await fetch('/api/scenarios', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
            }

            if (resp.ok) {
                const data = await resp.json();
                _scenarioId = data.id;
                KAdmin.refreshScenarioList();

                // If there's an active session, resync its units from the updated scenario
                const currentSessionId = KSessionUI.getSessionId();
                const token = KSessionUI.getToken();
                if (currentSessionId && token) {
                    try {
                        const resyncResp = await fetch(`/api/admin/sessions/${currentSessionId}/resync-units`, {
                            method: 'POST',
                            headers: { 'Authorization': `Bearer ${token}` },
                        });
                        if (resyncResp.ok) {
                            const resyncData = await resyncResp.json();
                            console.log('Resync units:', resyncData.message);

                            // Reload units, grid, and contacts on the map
                            try {
                                const map = KMap.getMap();
                                await KGrid.load(map, currentSessionId);
                                // Use god-view-aware refresh to avoid overwriting all-units with fog-of-war data
                                if (typeof KAdmin !== 'undefined' && KAdmin.isGodViewEnabled()) {
                                    await KAdmin.refreshMapUnits();
                                } else {
                                    await KUnits.load(currentSessionId, token);
                                }
                                await KContacts.load(currentSessionId, token);
                            } catch (e) {
                                console.warn('Map reload after resync:', e);
                            }

                            // Refresh admin CoC tree
                            try { KAdmin.loadPublicCoC(); } catch(e) {}
                        }
                    } catch (e) {
                        console.warn('Resync units after save:', e);
                    }
                }

                await KDialogs.alert(`Scenario "${data.title}" saved!`);
            } else {
                const err = await resp.json().catch(() => ({}));
                await KDialogs.alert('Save failed: ' + (err.detail || resp.status));
            }
        } catch (err) {
            await KDialogs.alert('Save error: ' + err.message);
        }
    }

    function _unitToPayload(u) {
        // Compute grid-relative offset (meters from grid origin)
        const originLat = parseFloat(document.getElementById('sb-grid-origin-lat').value);
        const originLon = parseFloat(document.getElementById('sb-grid-origin-lon').value);

        let grid_offset_x = null;
        let grid_offset_y = null;
        if (!isNaN(originLat) && !isNaN(originLon)) {
            const latRad = originLat * Math.PI / 180;
            const mPerDegLat = 111320;
            const mPerDegLon = 111320 * Math.cos(latRad);
            grid_offset_x = (u.lon - originLon) * mPerDegLon; // meters east of origin
            grid_offset_y = (u.lat - originLat) * mPerDegLat; // meters north of origin
        }

        return {
            name: u.name,
            unit_type: u.unit_type,
            sidc: u.sidc,
            lat: u.lat,
            lon: u.lon,
            grid_offset_x: grid_offset_x,
            grid_offset_y: grid_offset_y,
            strength: u.strength,
            ammo: u.ammo,
            morale: u.morale,
            move_speed_mps: u.move_speed_mps,
            detection_range_m: u.detection_range_m,
            capabilities: u.capabilities || {},
        };
    }

    // ══════════════════════════════════════════════════
    // ── Load Existing Scenario ───────────────────────
    // ══════════════════════════════════════════════════

    async function _loadScenario(scenarioId) {
        try {
            const resp = await fetch(`/api/scenarios/${scenarioId}`);
            if (!resp.ok) return;
            const s = await resp.json();

            // Fill metadata
            const titleEl = document.getElementById('sb-scenario-title');
            const descEl = document.getElementById('sb-scenario-desc');
            if (titleEl) titleEl.value = s.title || '';
            if (descEl) descEl.value = s.description || '';

            // Fill game rules from objectives
            if (s.objectives) {
                _setVal('sb-turn-limit', s.objectives.turn_limit || 0);
                const missionEl = document.getElementById('sb-scenario-mission');
                if (missionEl) missionEl.value = s.objectives.mission || '';
                const vbEl = document.getElementById('sb-victory-blue');
                if (vbEl) vbEl.value = s.objectives.victory_blue || '';
                const vrEl = document.getElementById('sb-victory-red');
                if (vrEl) vrEl.value = s.objectives.victory_red || '';
            }

            // Fill grid settings
            if (s.grid_settings) {
                const gs = s.grid_settings;
                _setVal('sb-grid-origin-lat', gs.origin_lat);
                _setVal('sb-grid-origin-lon', gs.origin_lon);
                _setVal('sb-grid-cols', gs.columns);
                _setVal('sb-grid-rows', gs.rows);
                _setVal('sb-grid-size', gs.base_square_size_m);
            }

            // Load units
            _stagedUnits = [];
            if (s.initial_units) {
                // Grid origin for resolving grid-relative offsets
                const gOriginLat = s.grid_settings ? s.grid_settings.origin_lat : null;
                const gOriginLon = s.grid_settings ? s.grid_settings.origin_lon : null;

                for (const side of ['blue', 'red']) {
                    for (const u of (s.initial_units[side] || [])) {
                        const info = UNIT_TYPES[u.unit_type] || {};

                        // Resolve position: prefer grid offsets, fall back to raw lat/lon
                        let unitLat = u.lat;
                        let unitLon = u.lon;
                        if (u.grid_offset_x != null && u.grid_offset_y != null
                            && gOriginLat != null && gOriginLon != null) {
                            const latRad = gOriginLat * Math.PI / 180;
                            const mPerDegLat = 111320;
                            const mPerDegLon = 111320 * Math.cos(latRad);
                            unitLat = gOriginLat + u.grid_offset_y / mPerDegLat;
                            unitLon = gOriginLon + u.grid_offset_x / mPerDegLon;
                        }

                        _stagedUnits.push({
                            tempId: 'u_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
                            side,
                            name: u.name,
                            unit_type: u.unit_type,
                            sidc: u.sidc || (side === 'red' ? info.sidc_red : info.sidc_blue) || '',
                            lat: unitLat,
                            lon: unitLon,
                            strength: u.strength ?? 1.0,
                            ammo: u.ammo ?? 1.0,
                            morale: u.morale ?? 0.9,
                            move_speed_mps: u.move_speed_mps ?? info.speed ?? 4.0,
                            detection_range_m: u.detection_range_m ?? info.det ?? 1500,
                            capabilities: u.capabilities || {},
                        });
                    }
                }
            }

            _renderStagedUnits();
            _refreshUnitList();
            _updateGridPreview(true);  // force re-render with loaded grid values

            // Pan/fit to the grid bounds (preferred), falling back to units
            let centered = false;
            if (s.grid_settings) {
                const gs = s.grid_settings;
                const oLat = parseFloat(gs.origin_lat);
                const oLon = parseFloat(gs.origin_lon);
                const cols = parseInt(gs.columns) || 8;
                const rows = parseInt(gs.rows) || 8;
                const sizeMt = parseFloat(gs.base_square_size_m) || 1000;
                if (!isNaN(oLat) && !isNaN(oLon)) {
                    const latRad = oLat * Math.PI / 180;
                    const dLat = (rows * sizeMt) / 111320;
                    const dLon = (cols * sizeMt) / (111320 * Math.cos(latRad));
                    _map.fitBounds([
                        [oLat, oLon],
                        [oLat + dLat, oLon + dLon],
                    ], { padding: [30, 30] });
                    centered = true;
                }
            }
            if (!centered && _stagedUnits.length > 0) {
                const lats = _stagedUnits.map(u => u.lat);
                const lons = _stagedUnits.map(u => u.lon);
                _map.fitBounds([
                    [Math.min(...lats) - 0.005, Math.min(...lons) - 0.005],
                    [Math.max(...lats) + 0.005, Math.max(...lons) + 0.005],
                ]);
            }
        } catch (err) {
            console.warn('Failed to load scenario:', err);
        }
    }

    function _setVal(id, val) {
        const el = document.getElementById(id);
        if (el && val != null) el.value = val;
    }

    // ══════════════════════════════════════════════════
    // ── Elegant Right-Click Context Menu ─────────────
    // ══════════════════════════════════════════════════

    function _initContextMenu() {
        // Create the context menu element once
        _ctxMenuEl = document.createElement('div');
        _ctxMenuEl.className = 'sb-ctx-menu ctx-menu';
        _ctxMenuEl.style.display = 'none';
        document.body.appendChild(_ctxMenuEl);
    }

    function _showUnitCtxMenu(idx, x, y) {
        _ctxIdx = idx;
        const u = _stagedUnits[idx];
        if (!u || !_ctxMenuEl) return;

        const info = UNIT_TYPES[u.unit_type] || {};
        const sideColor = u.side === 'red' ? '#ef5350' : '#4fc3f7';
        const oppositeSide = u.side === 'red' ? 'blue' : 'red';
        const oppositeSideColor = oppositeSide === 'red' ? '#ef5350' : '#4fc3f7';
        const strPct = Math.round((u.strength || 1) * 100);
        const morPct = Math.round((u.morale || 0.9) * 100);
        const ammPct = Math.round((u.ammo || 1) * 100);
        const pers = info.personnel || '?';

        _ctxMenuEl.innerHTML = `
            <div class="ctx-menu-header" style="border-left:3px solid ${sideColor};padding-left:8px;">
                <div style="font-size:12px;font-weight:700;color:#e0e0e0;">${u.name}</div>
                <div style="font-size:10px;color:#aaa;margin-top:1px;">${info.label || u.unit_type} (${pers}p) · <span style="color:${sideColor}">${u.side.toUpperCase()}</span></div>
                <div style="font-size:10px;color:#888;margin-top:2px;">Str:${strPct}% Mor:${morPct}% Ammo:${ammPct}%</div>
            </div>
            <div class="ctx-menu-section" style="padding:2px 0;">
                <button class="ctx-item" data-action="edit" title="Edit unit properties">
                    <svg viewBox="0 0 16 16" width="12" height="12" style="vertical-align:-1px;margin-right:6px;"><path d="M11.5 1.5L14.5 4.5L5 14H2V11L11.5 1.5Z" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linejoin="round"/></svg>
                    Edit Properties
                </button>
                <button class="ctx-item" data-action="duplicate" title="Create a copy of this unit nearby">
                    <svg viewBox="0 0 16 16" width="12" height="12" style="vertical-align:-1px;margin-right:6px;"><rect x="1" y="4" width="9" height="9" rx="1" stroke="currentColor" stroke-width="1.3" fill="none"/><rect x="5" y="1" width="9" height="9" rx="1" stroke="currentColor" stroke-width="1.3" fill="none"/></svg>
                    Duplicate Unit
                </button>
                <button class="ctx-item" data-action="toggleside" title="Switch unit to ${oppositeSide} side">
                    <svg viewBox="0 0 16 16" width="12" height="12" style="vertical-align:-1px;margin-right:6px;"><path d="M4 8H12M12 8L9 5M12 8L9 11" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
                    Switch to <span style="color:${oppositeSideColor};font-weight:700;">${oppositeSide.toUpperCase()}</span>
                </button>
                <button class="ctx-item" data-action="center" title="Pan map to this unit">
                    <svg viewBox="0 0 16 16" width="12" height="12" style="vertical-align:-1px;margin-right:6px;"><circle cx="8" cy="8" r="5" stroke="currentColor" stroke-width="1.3" fill="none"/><circle cx="8" cy="8" r="1.5" fill="currentColor"/></svg>
                    Center on Unit
                </button>
            </div>
            <div class="ctx-menu-section" style="padding:2px 0;border-top:1px solid rgba(15,52,96,0.4);">
                <button class="ctx-item" data-action="strength100" title="Set strength to 100%">💪 Full Strength (100%)</button>
                <button class="ctx-item" data-action="strength50" title="Set strength to 50%">⚠ Half Strength (50%)</button>
                <button class="ctx-item" data-action="strength25" title="Set strength to 25%">🩸 Quarter Strength (25%)</button>
            </div>
            <div class="ctx-menu-section" style="padding:2px 0;border-top:1px solid rgba(15,52,96,0.4);">
                <button class="ctx-item ctx-item-danger" data-action="delete" title="Remove this unit">
                    <svg viewBox="0 0 16 16" width="12" height="12" style="vertical-align:-1px;margin-right:6px;"><path d="M4 4L12 12M12 4L4 12" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
                    Delete Unit
                </button>
            </div>`;

        // Bind actions
        _ctxMenuEl.querySelectorAll('.ctx-item').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const action = btn.dataset.action;
                _handleCtxAction(action, _ctxIdx);
                _dismissCtxMenu();
            });
        });

        _ctxMenuEl.style.display = 'block';

        // Position on screen
        const menuW = _ctxMenuEl.offsetWidth;
        const menuH = _ctxMenuEl.offsetHeight;
        const posX = (x + menuW > window.innerWidth) ? x - menuW : x;
        const posY = (y + menuH > window.innerHeight) ? Math.max(0, y - menuH) : y;
        _ctxMenuEl.style.left = posX + 'px';
        _ctxMenuEl.style.top = posY + 'px';
    }

    function _handleCtxAction(action, idx) {
        const u = _stagedUnits[idx];
        if (!u) return;

        switch (action) {
            case 'edit':
                _showUnitForm(u.lat, u.lon, idx);
                _map.panTo([u.lat, u.lon]);
                break;
            case 'duplicate': {
                const info = UNIT_TYPES[u.unit_type] || {};
                const clone = {
                    ...u,
                    tempId: 'u_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
                    name: u.name + ' (copy)',
                    lat: u.lat + 0.001 * (Math.random() - 0.5),
                    lon: u.lon + 0.001 * (Math.random() - 0.5),
                };
                _stagedUnits.push(clone);
                _renderStagedUnits();
                _refreshUnitList();
                break;
            }
            case 'toggleside': {
                const newSide = u.side === 'red' ? 'blue' : 'red';
                const info = UNIT_TYPES[u.unit_type] || {};
                u.side = newSide;
                u.sidc = newSide === 'red' ? (info.sidc_red || '') : (info.sidc_blue || '');
                _renderStagedUnits();
                _refreshUnitList();
                break;
            }
            case 'center':
                _map.panTo([u.lat, u.lon]);
                break;
            case 'strength100':
                u.strength = 1.0;
                _refreshUnitList();
                break;
            case 'strength50':
                u.strength = 0.5;
                _refreshUnitList();
                break;
            case 'strength25':
                u.strength = 0.25;
                _refreshUnitList();
                break;
            case 'delete':
                _deleteUnit(idx);
                break;
        }
    }

    function _dismissCtxMenu() {
        if (_ctxMenuEl) {
            _ctxMenuEl.style.display = 'none';
        }
        _ctxIdx = -1;
    }

    // ══════════════════════════════════════════════════
    // ── Client-Side Grid Preview ─────────────────────
    // ══════════════════════════════════════════════════

    /**
     * Compute and render a quick client-side grid preview from the builder's
     * grid settings form (origin, cols, rows, size). Uses a simple local projection.
     * @param {boolean} force - if true, show even when a session grid is loaded
     */
    function _updateGridPreview(force = false) {
        if (!_gridPreviewLayer || !_map) return;
        _gridPreviewLayer.clearLayers();

        // Only skip if the session grid is currently *visible* on the map.
        // When the builder is active, the session grid is toggled off, so
        // KGrid.isVisible() is false and we always render the builder preview.
        // force=true bypasses this check entirely.
        if (!force && typeof KGrid !== 'undefined') {
            const sessionGrid = KGrid.getGridGeoJson();
            if (sessionGrid && sessionGrid.features && sessionGrid.features.length > 0 && KGrid.isVisible()) {
                return;
            }
        }

        const originLat = parseFloat(document.getElementById('sb-grid-origin-lat').value);
        const originLon = parseFloat(document.getElementById('sb-grid-origin-lon').value);
        const cols = Math.max(1, Math.min(20, parseInt(document.getElementById('sb-grid-cols').value) || 8));
        const rows = Math.max(1, Math.min(20, parseInt(document.getElementById('sb-grid-rows').value) || 8));
        const sizeMt = Math.max(100, Math.min(10000, parseFloat(document.getElementById('sb-grid-size').value) || 1000));

        if (isNaN(originLat) || isNaN(originLon)) return;

        // Approximate degree offsets for meters at this latitude
        const latRad = originLat * Math.PI / 180;
        const mPerDegLat = 111320;
        const mPerDegLon = 111320 * Math.cos(latRad);

        const dLat = sizeMt / mPerDegLat;
        const dLon = sizeMt / mPerDegLon;

        // Draw grid lines — same style as game grid (grid.js)
        const borderColor = '#1a3a5c';
        const innerColor  = 'rgba(26, 58, 92, 0.55)';
        for (let c = 0; c <= cols; c++) {
            const lon = originLon + c * dLon;
            const isBorder = c === 0 || c === cols;
            _gridPreviewLayer.addLayer(L.polyline([
                [originLat, lon],
                [originLat + rows * dLat, lon],
            ], {
                color: isBorder ? borderColor : innerColor,
                weight: isBorder ? 2.5 : 1,
                dashArray: isBorder ? null : '6,4',
                interactive: false,
            }));
        }
        for (let r = 0; r <= rows; r++) {
            const lat = originLat + r * dLat;
            const isBorder = r === 0 || r === rows;
            _gridPreviewLayer.addLayer(L.polyline([
                [lat, originLon],
                [lat, originLon + cols * dLon],
            ], {
                color: isBorder ? borderColor : innerColor,
                weight: isBorder ? 2.5 : 1,
                dashArray: isBorder ? null : '6,4',
                interactive: false,
            }));
        }

        // Column labels (A, B, C...) at top
        for (let c = 0; c < cols; c++) {
            const colLabel = String.fromCharCode(65 + c);
            const cLon = originLon + (c + 0.5) * dLon;
            const cLat = originLat + rows * dLat + dLat * 0.15;
            _gridPreviewLayer.addLayer(L.marker([cLat, cLon], {
                icon: L.divIcon({
                    className: 'grid-axis-label grid-axis-md',
                    html: `<span>${colLabel}</span>`,
                    iconSize: [20, 14],
                    iconAnchor: [10, 14],
                }),
                interactive: false,
            }));
        }
        // Row labels (1, 2, 3...) at left
        for (let r = 0; r < rows; r++) {
            const rLat = originLat + (r + 0.5) * dLat;
            const rLon = originLon - dLon * 0.15;
            _gridPreviewLayer.addLayer(L.marker([rLat, rLon], {
                icon: L.divIcon({
                    className: 'grid-axis-label grid-axis-md',
                    html: `<span>${r + 1}</span>`,
                    iconSize: [20, 14],
                    iconAnchor: [20, 7],
                }),
                interactive: false,
            }));
        }
    }

    // ══════════════════════════════════════════════════
    // ── Public API ───────────────────────────────────
    // ══════════════════════════════════════════════════

    function getUnitTypes() { return UNIT_TYPES; }

    function clearGridPreview() {
        if (_gridPreviewLayer) _gridPreviewLayer.clearLayers();
    }

    return {
        init, activate, deactivate, isActive,
        saveScenario, editUnit, removeUnit,
        getUnitTypes, resetUnitTypes, clearGridPreview,
        /** Force-render the builder's grid preview (called by grid.js after a KGrid load while builder is active). */
        forceGridPreview: () => { if (_active) _updateGridPreview(true); },
        // For form callbacks
        confirmUnit: _confirmUnit,
        hideUnitForm: _hideUnitForm,
        onTypeChange: _onTypeChange,
    };
})();

