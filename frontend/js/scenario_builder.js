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

    // ── Unit type registry ──────────────────────────────
    const UNIT_TYPES = {
        infantry_platoon:  { label: 'Infantry Platoon',  sidc_blue: '10031000151211000000', sidc_red: '10061000151211000000', speed: 4.0, det: 1500, fire: 600 },
        infantry_company:  { label: 'Infantry Company',  sidc_blue: '10031000151211000000', sidc_red: '10061000151211000000', speed: 3.5, det: 1500, fire: 800 },
        tank_company:      { label: 'Tank Company',      sidc_blue: '10031000151301000000', sidc_red: '10061000151301000000', speed: 8.0, det: 2000, fire: 2500 },
        mech_company:      { label: 'Mech Infantry Co',  sidc_blue: '10031000151211000000', sidc_red: '10061000151211000000', speed: 7.0, det: 1800, fire: 1500 },
        mortar_section:    { label: 'Mortar Section',    sidc_blue: '10031000151215000000', sidc_red: '10061000151215000000', speed: 3.0, det: 1000, fire: 3500 },
        at_team:           { label: 'Anti-Tank Team',    sidc_blue: '10031000151211004000', sidc_red: '10061000151211004000', speed: 3.5, det: 2000, fire: 2000 },
        recon_team:        { label: 'Recon Team',        sidc_blue: '10031000151213000000', sidc_red: '10061000151213000000', speed: 5.0, det: 3000, fire: 400 },
        observation_post:  { label: 'Observation Post',  sidc_blue: '10031000151213000000', sidc_red: '10061000151213000000', speed: 5.0, det: 4000, fire: 300 },
        sniper_team:       { label: 'Sniper Team',       sidc_blue: '10031000151211000000', sidc_red: '10061000151211000000', speed: 3.0, det: 2500, fire: 1000 },
    };

    function init(map) {
        _map = map;
        _stagedLayer = L.layerGroup();
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

        // Install map click handler
        _map.on('click', _onMapClick);

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
    }

    function deactivate() {
        _active = false;
        _scenarioId = null;
        _stagedUnits = [];
        _editingIdx = -1;
        _stagedLayer.clearLayers();
        if (_map.hasLayer(_stagedLayer)) _map.removeLayer(_stagedLayer);

        _map.off('click', _onMapClick);

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
        document.getElementById('sb-unit-strength').value = existing ? existing.strength : 1.0;
        document.getElementById('sb-unit-ammo').value = existing ? existing.ammo : 1.0;
        document.getElementById('sb-unit-morale').value = existing ? existing.morale : 0.9;

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
    }

    function _confirmUnit() {
        const lat = parseFloat(document.getElementById('sb-unit-lat').value);
        const lon = parseFloat(document.getElementById('sb-unit-lon').value);
        const name = document.getElementById('sb-unit-name').value.trim();
        const side = document.getElementById('sb-unit-side').value;
        const unit_type = document.getElementById('sb-unit-type').value;
        const strength = parseFloat(document.getElementById('sb-unit-strength').value) || 1.0;
        const ammo = parseFloat(document.getElementById('sb-unit-ammo').value) || 1.0;
        const morale = parseFloat(document.getElementById('sb-unit-morale').value) || 0.9;

        if (!name) { alert('Unit name required'); return; }
        if (isNaN(lat) || isNaN(lon)) { alert('Invalid coordinates'); return; }

        const info = UNIT_TYPES[unit_type] || {};
        const sidc = side === 'red' ? (info.sidc_red || '') : (info.sidc_blue || '');

        const unitData = {
            tempId: _editingIdx >= 0 ? _stagedUnits[_editingIdx].tempId : 'u_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
            side, name, unit_type, sidc, lat, lon,
            strength, ammo, morale,
            move_speed_mps: info.speed || 4.0,
            detection_range_m: info.det || 1500,
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

        _stagedUnits.forEach((u, idx) => {
            const icon = KSymbols.createIcon(u.sidc, {
                direction: 0,
                unitType: u.unit_type,
            });

            const marker = L.marker([u.lat, u.lon], { icon, draggable: true });

            marker.bindTooltip(`${u.name} [${u.side}]`, {
                permanent: false,
                direction: 'top',
                offset: [0, -18],
                className: 'unit-tooltip',
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

            // Right-click to delete
            marker.on('contextmenu', (e) => {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                if (confirm(`Delete ${u.name}?`)) _deleteUnit(idx);
            });

            _stagedLayer.addLayer(marker);
        });
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
                html += `<div class="sb-unit-item" onclick="KScenarioBuilder.editUnit(${idx})">
                    <span class="sb-unit-name">${u.name}</span>
                    <span class="sb-unit-type-badge">${u.unit_type}</span>
                    <button class="sb-unit-del" onclick="event.stopPropagation();KScenarioBuilder.removeUnit(${idx})">✕</button>
                </div>`;
            });
        }
        if (reds.length > 0) {
            html += '<div style="color:#ef5350;font-size:10px;font-weight:700;margin:4px 0 2px;">RED (' + reds.length + ')</div>';
            reds.forEach((u) => {
                const idx = _stagedUnits.indexOf(u);
                html += `<div class="sb-unit-item sb-unit-red" onclick="KScenarioBuilder.editUnit(${idx})">
                    <span class="sb-unit-name">${u.name}</span>
                    <span class="sb-unit-type-badge">${u.unit_type}</span>
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
        const gridCols = parseInt(document.getElementById('sb-grid-cols').value) || 8;
        const gridRows = parseInt(document.getElementById('sb-grid-rows').value) || 8;
        const gridSize = parseInt(document.getElementById('sb-grid-size').value) || 1000;

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

