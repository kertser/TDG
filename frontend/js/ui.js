/**
 * ui.js – Sidebar tab switching, drawing toolbar in topbar,
 *         map control overlay (two 3×2 grids, top-right of map)
 *         with show/hide toggle.
 */
const KUI = (() => {
    let _mapCtrlControl = null;

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

        // Hide old draw toolbar in topbar (replaced by map control group)
        const oldDrawToolbar = document.getElementById('draw-toolbar');
        if (oldDrawToolbar) oldDrawToolbar.style.display = 'none';

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
                    '<button id="overlays-toggle-btn" class="topbar-icon-btn" title="Show/hide overlays"><svg viewBox="0 0 16 16" width="14" height="14"><line x1="2" y1="13" x2="14" y2="3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><polygon points="12,2 15,3.5 13,6" fill="currentColor" opacity="0.8"/><rect x="3" y="7" width="5" height="4" rx="0.5" stroke="currentColor" stroke-width="1" stroke-dasharray="2,1.5" fill="none"/></svg></button>' +
                    '<button id="contacts-toggle-btn" class="topbar-icon-btn" title="Show/hide contacts"><svg viewBox="0 0 16 16" width="14" height="14"><path d="M8 3L10.5 8L8 13L5.5 8Z" stroke="currentColor" stroke-width="1.3" fill="none"/><circle cx="8" cy="8" r="1.2" fill="currentColor"/></svg></button>' +
                    '<button id="labels-toggle-btn" class="topbar-icon-btn" title="Show/hide grid labels"><svg viewBox="0 0 16 16" width="14" height="14"><text x="3" y="7" font-size="6" font-weight="bold" fill="currentColor" font-family="sans-serif">A</text><text x="8" y="13" font-size="6" font-weight="bold" fill="currentColor" font-family="sans-serif">1</text><line x1="7" y1="3" x2="13" y2="3" stroke="currentColor" stroke-width="0.8"/><line x1="11" y1="7" x2="14" y2="7" stroke="currentColor" stroke-width="0.8"/></svg></button>';

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

        const contactsToggleBtn = wrapper.querySelector('#contacts-toggle-btn');
        if (contactsToggleBtn) {
            contactsToggleBtn.addEventListener('click', () => {
                const visible = KContacts.toggle();
                contactsToggleBtn.classList.toggle('toggled-off', !visible);
                contactsToggleBtn.title = visible ? 'Hide contacts' : 'Show contacts';
            });
        }

        const labelsToggleBtn = wrapper.querySelector('#labels-toggle-btn');
        if (labelsToggleBtn) {
            labelsToggleBtn.addEventListener('click', () => {
                const visible = KGrid.toggleLabels();
                labelsToggleBtn.classList.toggle('toggled-off', !visible);
                labelsToggleBtn.title = visible ? 'Hide grid labels' : 'Show grid labels';
            });
        }
    }

    function _clearActive() {
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    return { init, addMapControls };
})();
