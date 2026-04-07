/**
 * ui.js – Sidebar tab switching, drawing toolbar in topbar,
 *         map control overlay (two 3×2 grids, top-right of map)
 *         with show/hide toggle, and compass rose.
 */
const KUI = (() => {
    let _mapCtrlControl = null;
    let _compassControl = null;
    let _compassVisible = true;

    // ...existing code...
    function init() {
        // Tab switching (with auto-load for CoC tab)
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
                btn.classList.add('active');
                const panel = document.getElementById(btn.dataset.tab);
                if (panel) panel.classList.add('active');

                // Auto-load data for specific tabs
                if (btn.dataset.tab === 'coc-tab') {
                    try { KAdmin.loadPublicCoC(); } catch(e) {}
                }
            });
        });

        // Drawing toolbar button events are bound in addMapControls() (dynamic creation)
    }

    /** Add two stacked 3×2 button groups to top-right of the map with show/hide toggle. */
    function addMapControls(map) {
        if (_mapCtrlControl) return;

        // ── Compass Rose (bottom-right, above game clock) ──
        const CompassControl = L.Control.extend({
            options: { position: 'bottomright' },
            onAdd: function () {
                const container = L.DomUtil.create('div', 'compass-control');
                container.innerHTML =
                    '<svg class="compass-rose" viewBox="0 0 100 100" width="80" height="80">' +
                    // Outer ring
                    '<circle cx="50" cy="50" r="46" fill="none" stroke="rgba(79,195,247,0.15)" stroke-width="0.8"/>' +
                    '<circle cx="50" cy="50" r="38" fill="none" stroke="rgba(79,195,247,0.1)" stroke-width="0.5"/>' +
                    // Tick marks (every 30°)
                    '<g stroke="rgba(79,195,247,0.3)" stroke-width="0.8">' +
                    '<line x1="50" y1="4" x2="50" y2="10"/>' +   // N
                    '<line x1="50" y1="90" x2="50" y2="96"/>' +   // S
                    '<line x1="4" y1="50" x2="10" y2="50"/>' +    // W
                    '<line x1="90" y1="50" x2="96" y2="50"/>' +   // E
                    '<line x1="73" y1="10.4" x2="70" y2="15.6"/>' + // 30°
                    '<line x1="89.6" y1="27" x2="84.4" y2="30"/>' + // 60°
                    '<line x1="89.6" y1="73" x2="84.4" y2="70"/>' + // 120°
                    '<line x1="73" y1="89.6" x2="70" y2="84.4"/>' + // 150°
                    '<line x1="27" y1="89.6" x2="30" y2="84.4"/>' + // 210°
                    '<line x1="10.4" y1="73" x2="15.6" y2="70"/>' + // 240°
                    '<line x1="10.4" y1="27" x2="15.6" y2="30"/>' + // 300°
                    '<line x1="27" y1="10.4" x2="30" y2="15.6"/>' + // 330°
                    '</g>' +
                    // Cardinal direction labels
                    '<text x="50" y="19" text-anchor="middle" class="compass-label compass-n">N</text>' +
                    '<text x="50" y="89" text-anchor="middle" class="compass-label compass-s">S</text>' +
                    '<text x="13" y="54" text-anchor="middle" class="compass-label">W</text>' +
                    '<text x="87" y="54" text-anchor="middle" class="compass-label">E</text>' +
                    // North needle (red triangle)
                    '<polygon points="50,12 45,50 55,50" class="compass-needle-n"/>' +
                    // South needle (dark blue)
                    '<polygon points="50,88 45,50 55,50" class="compass-needle-s"/>' +
                    // Center dot
                    '<circle cx="50" cy="50" r="3" fill="rgba(79,195,247,0.5)" stroke="rgba(79,195,247,0.8)" stroke-width="0.5"/>' +
                    '<circle cx="50" cy="50" r="1.2" fill="#4fc3f7"/>' +
                    '</svg>';
                L.DomEvent.disableClickPropagation(container);
                return container;
            },
        });
        _compassControl = new CompassControl();
        _compassControl.addTo(map);

        const MapCtrl = L.Control.extend({
            options: { position: 'topright' },
            onAdd: function () {
                const wrapper = L.DomUtil.create('div', 'map-ctrl-wrapper');

                // Toggle button (always visible)
                const toggleBtn = L.DomUtil.create('button', 'map-ctrl-toggle-btn', wrapper);
                toggleBtn.innerHTML = '▼';
                toggleBtn.title = 'Hide map controls';

                // Container for both groups
                const groupsContainer = L.DomUtil.create('div', 'map-ctrl-groups', wrapper);

                // ── Group 1: Drawing tools (3×2) ──
                const drawGroup = L.DomUtil.create('div', 'map-ctrl-overlay', groupsContainer);
                drawGroup.id = 'map-draw-group';
                drawGroup.style.display = 'none';  // hidden until session active
                drawGroup.innerHTML =
                    '<button class="topbar-icon-btn draw-btn" data-tool="arrow" title="Curved Arrow (right-click to finish)"><svg viewBox="0 0 16 16" width="14" height="14"><path d="M2 13C5 9 9 6 13 3" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/><path d="M9 2L13.5 2.5L13 7" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg></button>' +
                    '<button class="topbar-icon-btn draw-btn" data-tool="polyline" title="Line (right-click to finish)"><svg viewBox="0 0 16 16" width="14" height="14"><line x1="2" y1="13" x2="14" y2="3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="2" cy="13" r="1.8" fill="currentColor"/><circle cx="14" cy="3" r="1.8" fill="currentColor"/></svg></button>' +
                    '<button class="topbar-icon-btn draw-btn" data-tool="rectangle" title="Rectangle (dashed)"><svg viewBox="0 0 16 16" width="14" height="14"><rect x="2" y="3.5" width="12" height="9" rx="1" stroke="currentColor" stroke-width="1.4" stroke-dasharray="2.5,1.8" fill="none"/></svg></button>' +
                    '<button class="topbar-icon-btn draw-btn" data-tool="marker" title="Marker"><svg viewBox="0 0 16 16" width="14" height="14"><path d="M8 1.5C5.5 1.5 3.5 3.5 3.5 6C3.5 9.5 8 14.5 8 14.5S12.5 9.5 12.5 6C12.5 3.5 10.5 1.5 8 1.5Z" fill="currentColor" opacity="0.85"/><circle cx="8" cy="6" r="2" fill="#1a1a2e"/></svg></button>' +
                    '<button class="topbar-icon-btn draw-btn" data-tool="ellipse" title="Ellipse (dashed)"><svg viewBox="0 0 16 16" width="14" height="14"><ellipse cx="8" cy="8" rx="6.5" ry="4.5" stroke="currentColor" stroke-width="1.4" stroke-dasharray="2.5,1.8" fill="none"/></svg></button>' +
                    '<button class="topbar-icon-btn draw-btn" data-tool="measure" title="Measure (right-click to finish)"><svg viewBox="0 0 16 16" width="14" height="14"><line x1="1" y1="11" x2="15" y2="11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="1.5" y1="9" x2="1.5" y2="13" stroke="currentColor" stroke-width="1.3"/><line x1="14.5" y1="9" x2="14.5" y2="13" stroke="currentColor" stroke-width="1.3"/><line x1="5.5" y1="10" x2="5.5" y2="12" stroke="currentColor" stroke-width="1"/><line x1="8" y1="9.5" x2="8" y2="12.5" stroke="currentColor" stroke-width="1"/><line x1="10.5" y1="10" x2="10.5" y2="12" stroke="currentColor" stroke-width="1"/></svg></button>';

                // ── Group 2: Map controls (3×2) ──
                const ctrlGroup = L.DomUtil.create('div', 'map-ctrl-overlay', groupsContainer);
                ctrlGroup.innerHTML =
                    '<button id="center-btn" class="topbar-icon-btn" title="Center on operation area"><svg viewBox="0 0 16 16" width="14" height="14"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.4" fill="none"/><circle cx="8" cy="8" r="1.5" fill="currentColor"/><line x1="8" y1="0.5" x2="8" y2="3.5" stroke="currentColor" stroke-width="1.2"/><line x1="8" y1="12.5" x2="8" y2="15.5" stroke="currentColor" stroke-width="1.2"/><line x1="0.5" y1="8" x2="3.5" y2="8" stroke="currentColor" stroke-width="1.2"/><line x1="12.5" y1="8" x2="15.5" y2="8" stroke="currentColor" stroke-width="1.2"/></svg></button>' +
                    '<button id="grid-toggle-btn" class="topbar-icon-btn" title="Show/hide grid"><svg viewBox="0 0 16 16" width="14" height="14"><rect x="1" y="1" width="14" height="14" rx="1" stroke="currentColor" stroke-width="1.2" fill="none"/><line x1="5.5" y1="1" x2="5.5" y2="15" stroke="currentColor" stroke-width="0.8"/><line x1="10.5" y1="1" x2="10.5" y2="15" stroke="currentColor" stroke-width="0.8"/><line x1="1" y1="5.5" x2="15" y2="5.5" stroke="currentColor" stroke-width="0.8"/><line x1="1" y1="10.5" x2="15" y2="10.5" stroke="currentColor" stroke-width="0.8"/></svg></button>' +
                    '<button id="units-toggle-btn" class="topbar-icon-btn" title="Show/hide units"><svg viewBox="0 0 16 16" width="14" height="14"><path d="M8 2C5.8 2 4 3.6 4 5.5C4 8 8 12 8 12S12 8 12 5.5C12 3.6 10.2 2 8 2Z" stroke="currentColor" stroke-width="1.2" fill="none"/><circle cx="8" cy="5.5" r="1.5" stroke="currentColor" stroke-width="1" fill="none"/><line x1="4" y1="14" x2="12" y2="14" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg></button>' +
                    '<button id="overlays-toggle-btn" class="topbar-icon-btn" title="Show/hide overlays"><svg viewBox="0 0 16 16" width="14" height="14"><path d="M8 2L1.5 6L8 10L14.5 6Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M1.5 8.5L8 12.5L14.5 8.5" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" opacity="0.7"/><path d="M1.5 11L8 15L14.5 11" fill="none" stroke="currentColor" stroke-width="1" stroke-linejoin="round" opacity="0.45"/></svg></button>' +
                    '<button id="terrain-toggle-btn" class="topbar-icon-btn" title="Show/hide terrain"><svg viewBox="0 0 16 16" width="14" height="14"><path d="M1 14 L5 5 L8 9 L11 4 L15 14 Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><circle cx="12" cy="3" r="1.5" fill="currentColor" opacity="0.5"/></svg></button>' +
                    '<button id="compass-toggle-btn" class="topbar-icon-btn" title="Show/hide compass"><svg viewBox="0 0 16 16" width="14" height="14"><circle cx="8" cy="8" r="6.8" stroke="currentColor" stroke-width="0.8" fill="none"/><circle cx="8" cy="8" r="5.2" stroke="currentColor" stroke-width="0.4" fill="none" opacity="0.4"/><line x1="8" y1="1" x2="8" y2="3" stroke="currentColor" stroke-width="0.7" opacity="0.5"/><line x1="8" y1="13" x2="8" y2="15" stroke="currentColor" stroke-width="0.7" opacity="0.5"/><line x1="1" y1="8" x2="3" y2="8" stroke="currentColor" stroke-width="0.7" opacity="0.5"/><line x1="13" y1="8" x2="15" y2="8" stroke="currentColor" stroke-width="0.7" opacity="0.5"/><polygon points="8,2.2 6.8,7.5 8,6.8 9.2,7.5" fill="#ef5350" opacity="0.85"/><polygon points="8,13.8 6.8,8.5 8,9.2 9.2,8.5" fill="currentColor" opacity="0.4"/><circle cx="8" cy="8" r="1.2" fill="none" stroke="currentColor" stroke-width="0.6" opacity="0.6"/><text x="8" y="2" text-anchor="middle" font-size="2.8" font-weight="bold" fill="#ef5350" font-family="sans-serif" opacity="0.9">N</text></svg></button>';

                L.DomEvent.disableClickPropagation(wrapper);
                L.DomEvent.disableScrollPropagation(wrapper);

                return wrapper;
            },
        });

        _mapCtrlControl = new MapCtrl();
        _mapCtrlControl.addTo(map);

        // ── Get references from the live DOM ──
        const wrapper = _mapCtrlControl.getContainer();
        const toggleBtn = wrapper.querySelector('.map-ctrl-toggle-btn');
        const groupsContainer = wrapper.querySelector('.map-ctrl-groups');

        // Toggle show/hide
        let _groupVisible = true;
        toggleBtn.addEventListener('click', () => {
            _groupVisible = !_groupVisible;
            groupsContainer.style.display = _groupVisible ? '' : 'none';
            toggleBtn.innerHTML = _groupVisible ? '▼' : '☰';
            toggleBtn.classList.toggle('collapsed', !_groupVisible);
            toggleBtn.title = _groupVisible ? 'Hide map controls' : 'Show map controls';
        });

        // ── Bind drawing tool buttons ──
        wrapper.querySelectorAll('.draw-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const tool = btn.dataset.tool;
                if (tool === 'measure') {
                    KOverlays.cancelDraw();
                    _clearActive();
                    btn.classList.add('active');
                    KMap.startMeasure();
                } else {
                    KMap.stopMeasure();
                    _clearActive();
                    btn.classList.add('active');
                    KOverlays.startDraw(tool);
                }
            });
        });

        // ── Bind map control toggle buttons ──
        const centerBtn = wrapper.querySelector('#center-btn');
        if (centerBtn) {
            centerBtn.addEventListener('click', () => KMap.centerOnOperation());
        }

        const gridToggleBtn = wrapper.querySelector('#grid-toggle-btn');
        if (gridToggleBtn) {
            gridToggleBtn.addEventListener('click', () => {
                const visible = KGrid.toggle();
                gridToggleBtn.classList.toggle('toggled-off', !visible);
                gridToggleBtn.title = visible ? 'Hide grid' : 'Show grid';
            });
        }

        const unitsToggleBtn = wrapper.querySelector('#units-toggle-btn');
        if (unitsToggleBtn) {
            unitsToggleBtn.addEventListener('click', () => {
                const visible = KUnits.toggle();
                unitsToggleBtn.classList.toggle('toggled-off', !visible);
                unitsToggleBtn.title = visible ? 'Hide units' : 'Show units';
            });
        }

        const overlaysToggleBtn = wrapper.querySelector('#overlays-toggle-btn');
        if (overlaysToggleBtn) {
            overlaysToggleBtn.addEventListener('click', () => {
                const visible = KOverlays.toggle();
                overlaysToggleBtn.classList.toggle('toggled-off', !visible);
                overlaysToggleBtn.title = visible ? 'Hide overlays' : 'Show overlays';
            });
        }

        const compassToggleBtn = wrapper.querySelector('#compass-toggle-btn');
        if (compassToggleBtn) {
            compassToggleBtn.addEventListener('click', () => {
                _compassVisible = !_compassVisible;
                const el = _compassControl ? _compassControl.getContainer() : null;
                if (el) el.style.display = _compassVisible ? '' : 'none';
                compassToggleBtn.classList.toggle('toggled-off', !_compassVisible);
                compassToggleBtn.title = _compassVisible ? 'Hide compass' : 'Show compass';
            });
        }

        const terrainToggleBtn = wrapper.querySelector('#terrain-toggle-btn');
        if (terrainToggleBtn) {
            terrainToggleBtn.addEventListener('click', () => {
                const visible = KTerrain.toggle();
                terrainToggleBtn.classList.toggle('toggled-off', !visible);
                terrainToggleBtn.title = visible ? 'Hide terrain' : 'Show terrain';
                if (visible) KTerrain.showLegend();
                else KTerrain.hideLegend();
            });
        }

    }

    function _clearActive() {
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    return { init, addMapControls };
})();
