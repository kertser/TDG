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
    let _sessionGridWasVisible = false; // track session grid visibility to restore later

    // ── Unit type registry ──────────────────────────────
    const UNIT_TYPES = {
        headquarters:      { label: 'Headquarters',       sidc_blue: '10031000151200000000', sidc_red: '10061000151200000000', speed: 3.0, det: 2000, fire: 200,  personnel: 20, isHQ: true },
        command_post:      { label: 'Command Post',       sidc_blue: '10031000151200000000', sidc_red: '10061000151200000000', speed: 2.0, det: 1500, fire: 100,  personnel: 10, isHQ: true },
        infantry_platoon:  { label: 'Infantry Platoon',   sidc_blue: '10031000151211000000', sidc_red: '10061000151211000000', speed: 4.0, det: 1500, fire: 600,  personnel: 30 },
        infantry_company:  { label: 'Infantry Company',   sidc_blue: '10031000151211000000', sidc_red: '10061000151211000000', speed: 3.5, det: 1500, fire: 800,  personnel: 120 },
        tank_company:      { label: 'Tank Company',       sidc_blue: '10031000151301000000', sidc_red: '10061000151301000000', speed: 8.0, det: 2000, fire: 2500, personnel: 60 },
        mech_company:      { label: 'Mech Infantry Co',   sidc_blue: '10031000151211000000', sidc_red: '10061000151211000000', speed: 7.0, det: 1800, fire: 1500, personnel: 100 },
        artillery_battery: { label: 'Artillery Battery',  sidc_blue: '10031000151303000000', sidc_red: '10061000151303000000', speed: 3.0, det: 1200, fire: 5000, personnel: 40 },
        mortar_section:    { label: 'Mortar Section',     sidc_blue: '10031000151215000000', sidc_red: '10061000151215000000', speed: 3.0, det: 1000, fire: 3500, personnel: 12 },
        at_team:           { label: 'Anti-Tank Team',     sidc_blue: '10031000151211004000', sidc_red: '10061000151211004000', speed: 3.5, det: 2000, fire: 2000, personnel: 6 },
        recon_team:        { label: 'Recon Team',         sidc_blue: '10031000151213000000', sidc_red: '10061000151213000000', speed: 5.0, det: 3000, fire: 400,  personnel: 6 },
        observation_post:  { label: 'Observation Post',   sidc_blue: '10031000151213000000', sidc_red: '10061000151213000000', speed: 5.0, det: 4000, fire: 300,  personnel: 4 },
        sniper_team:       { label: 'Sniper Team',        sidc_blue: '10031000151211000000', sidc_red: '10061000151211000000', speed: 3.0, det: 2500, fire: 1000, personnel: 2 },
        engineer_platoon:  { label: 'Engineer Platoon',   sidc_blue: '10031000151206000000', sidc_red: '10061000151206000000', speed: 3.5, det: 1200, fire: 400,  personnel: 30 },
        logistics_unit:    { label: 'Logistics Unit',     sidc_blue: '10031000151207000000', sidc_red: '10061000151207000000', speed: 5.0, det: 800,  fire: 100,  personnel: 20 },
    };

    function init(map) {
        _map = map;
        _stagedLayer = L.layerGroup();
        _rangePreviewLayer = L.layerGroup();
        _gridPreviewLayer = L.layerGroup();
        _initContextMenu();

        // Auto-update grid preview when grid settings change
        ['sb-grid-origin-lat', 'sb-grid-origin-lon', 'sb-grid-cols', 'sb-grid-rows', 'sb-grid-size'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', () => { if (_active) _updateGridPreview(); });
        });
    }

    // ══════════════════════════════════════════════════
    // ── Activate / Deactivate Builder Mode ───────────
    // ══════════════════════════════════════════════════

    function activate(scenarioId = null) {
        _active = true;
        _scenarioId = scenarioId;
        _stagedUnits = [];
        _editingIdx = -1;
        if (!_map.hasLayer(_stagedLayer)) _stagedLayer.addTo(_map);
        if (!_map.hasLayer(_rangePreviewLayer)) _rangePreviewLayer.addTo(_map);
        if (!_map.hasLayer(_gridPreviewLayer)) _gridPreviewLayer.addTo(_map);

        // Hide the session grid so only the builder preview grid is shown
        _sessionGridWasVisible = KGrid.isVisible();
        if (_sessionGridWasVisible) KGrid.toggle();

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

        // If editing an existing scenario, load its data
        if (scenarioId) _loadScenario(scenarioId);

        _refreshUnitList();
        // Render a client-side grid preview from the form values
        _updateGridPreview();
    }

    function deactivate() {
        _active = false;
        _scenarioId = null;
        _stagedUnits = [];
        _editingIdx = -1;
        _stagedLayer.clearLayers();
        _rangePreviewLayer.clearLayers();
        _gridPreviewLayer.clearLayers();
        if (_map.hasLayer(_stagedLayer)) _map.removeLayer(_stagedLayer);
        if (_map.hasLayer(_rangePreviewLayer)) _map.removeLayer(_rangePreviewLayer);
        if (_map.hasLayer(_gridPreviewLayer)) _map.removeLayer(_gridPreviewLayer);

        // Restore session grid if it was visible before builder was activated
        if (_sessionGridWasVisible && !KGrid.isVisible()) KGrid.toggle();

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

    function _onMapClick(e) {
        if (!_active) return;
        // Don't place if clicking on an existing marker or UI
        if (e.originalEvent && e.originalEvent.target.closest &&
            (e.originalEvent.target.closest('.leaflet-marker-icon') ||
             e.originalEvent.target.closest('.leaflet-popup') ||
             e.originalEvent.target.closest('#sidebar') ||
             e.originalEvent.target.closest('#topbar'))) return;

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
        if (info && !nameEl.value) {
            // Auto-generate a name
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

    function _confirmUnit() {
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

        if (!name) { alert('Unit name required'); return; }
        if (isNaN(lat) || isNaN(lon)) { alert('Invalid coordinates'); return; }

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
        if (!title) { alert('Scenario title required'); return; }

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

        const body = {
            title,
            description: description || null,
            map_center_lat: center.lat,
            map_center_lon: center.lng,
            map_zoom: _map.getZoom(),
            grid_settings,
            initial_units,
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
                alert(`Scenario "${data.title}" saved!`);
                KAdmin.refreshScenarioList();
            } else {
                const err = await resp.json().catch(() => ({}));
                alert('Save failed: ' + (err.detail || resp.status));
            }
        } catch (err) {
            alert('Save error: ' + err.message);
        }
    }

    function _unitToPayload(u) {
        return {
            name: u.name,
            unit_type: u.unit_type,
            sidc: u.sidc,
            lat: u.lat,
            lon: u.lon,
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
                for (const side of ['blue', 'red']) {
                    for (const u of (s.initial_units[side] || [])) {
                        const info = UNIT_TYPES[u.unit_type] || {};
                        _stagedUnits.push({
                            tempId: 'u_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
                            side,
                            name: u.name,
                            unit_type: u.unit_type,
                            sidc: u.sidc || (side === 'red' ? info.sidc_red : info.sidc_blue) || '',
                            lat: u.lat,
                            lon: u.lon,
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
            _updateGridPreview();

            // Pan to scenario area
            if (_stagedUnits.length > 0) {
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
     */
    function _updateGridPreview() {
        if (!_gridPreviewLayer || !_map) return;
        _gridPreviewLayer.clearLayers();

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

        // Draw grid lines
        for (let c = 0; c <= cols; c++) {
            const lon = originLon + c * dLon;
            _gridPreviewLayer.addLayer(L.polyline([
                [originLat, lon],
                [originLat + rows * dLat, lon],
            ], {
                color: 'rgba(255,255,255,0.35)',
                weight: c === 0 || c === cols ? 2 : 1,
                dashArray: c === 0 || c === cols ? null : '6,4',
                interactive: false,
            }));
        }
        for (let r = 0; r <= rows; r++) {
            const lat = originLat + r * dLat;
            _gridPreviewLayer.addLayer(L.polyline([
                [lat, originLon],
                [lat, originLon + cols * dLon],
            ], {
                color: 'rgba(255,255,255,0.35)',
                weight: r === 0 || r === rows ? 2 : 1,
                dashArray: r === 0 || r === rows ? null : '6,4',
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

    return {
        init, activate, deactivate, isActive,
        saveScenario, editUnit, removeUnit,
        getUnitTypes,
        // For form callbacks
        confirmUnit: _confirmUnit,
        hideUnitForm: _hideUnitForm,
        onTypeChange: _onTypeChange,
    };
})();

