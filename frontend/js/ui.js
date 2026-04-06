/**
 * ui.js – Sidebar tab switching, drawing toolbar in topbar,
 *         map control overlay (2×2 grid of buttons in top-right of map).
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

        // Drawing toolbar buttons (inside #draw-toolbar)
        document.querySelectorAll('.draw-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const tool = btn.dataset.tool;

                if (tool === 'measure') {
                    KOverlays.cancelDraw();
                    _clearActive();
                    btn.classList.add('active');
                    KMap.startMeasure();
                } else {
                    // Drawing tool (arrow, polyline, rectangle, ellipse, marker)
                    KMap.stopMeasure();
                    _clearActive();
                    btn.classList.add('active');
                    KOverlays.startDraw(tool);
                }
            });
        });
    }

    /** Add the 2×2 map control overlay (center, grid, units, overlays) to the map. */
    function addMapControls(map) {
        if (_mapCtrlControl) return;

        const MapCtrlOverlay = L.Control.extend({
            options: { position: 'topright' },
            onAdd: function () {
                const container = L.DomUtil.create('div', 'map-ctrl-overlay');
                container.innerHTML =
                    // Row 1
                    '<button id="center-btn" class="topbar-icon-btn" title="Center on operation area"><svg viewBox="0 0 16 16" width="14" height="14"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.4" fill="none"/><circle cx="8" cy="8" r="1.5" fill="currentColor"/><line x1="8" y1="0.5" x2="8" y2="3.5" stroke="currentColor" stroke-width="1.2"/><line x1="8" y1="12.5" x2="8" y2="15.5" stroke="currentColor" stroke-width="1.2"/><line x1="0.5" y1="8" x2="3.5" y2="8" stroke="currentColor" stroke-width="1.2"/><line x1="12.5" y1="8" x2="15.5" y2="8" stroke="currentColor" stroke-width="1.2"/></svg></button>' +
                    '<button id="grid-toggle-btn" class="topbar-icon-btn" title="Show/hide grid"><svg viewBox="0 0 16 16" width="14" height="14"><rect x="1" y="1" width="14" height="14" rx="1" stroke="currentColor" stroke-width="1.2" fill="none"/><line x1="5.5" y1="1" x2="5.5" y2="15" stroke="currentColor" stroke-width="0.8"/><line x1="10.5" y1="1" x2="10.5" y2="15" stroke="currentColor" stroke-width="0.8"/><line x1="1" y1="5.5" x2="15" y2="5.5" stroke="currentColor" stroke-width="0.8"/><line x1="1" y1="10.5" x2="15" y2="10.5" stroke="currentColor" stroke-width="0.8"/></svg></button>' +
                    // Row 2
                    '<button id="units-toggle-btn" class="topbar-icon-btn" title="Show/hide units"><svg viewBox="0 0 16 16" width="14" height="14"><path d="M8 2C5.8 2 4 3.6 4 5.5C4 8 8 12 8 12S12 8 12 5.5C12 3.6 10.2 2 8 2Z" stroke="currentColor" stroke-width="1.2" fill="none"/><circle cx="8" cy="5.5" r="1.5" stroke="currentColor" stroke-width="1" fill="none"/><line x1="4" y1="14" x2="12" y2="14" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg></button>' +
                    '<button id="overlays-toggle-btn" class="topbar-icon-btn" title="Show/hide overlays"><svg viewBox="0 0 16 16" width="14" height="14"><line x1="2" y1="13" x2="14" y2="3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><polygon points="12,2 15,3.5 13,6" fill="currentColor" opacity="0.8"/><rect x="3" y="7" width="5" height="4" rx="0.5" stroke="currentColor" stroke-width="1" stroke-dasharray="2,1.5" fill="none"/></svg></button>';
                L.DomEvent.disableClickPropagation(container);
                L.DomEvent.disableScrollPropagation(container);
                return container;
            },
        });
        _mapCtrlControl = new MapCtrlOverlay();
        _mapCtrlControl.addTo(map);

        // Bind button events (after DOM insertion)
        setTimeout(() => {
            const centerBtn = document.getElementById('center-btn');
            if (centerBtn) {
                centerBtn.addEventListener('click', () => KMap.centerOnOperation());
            }

            const gridToggleBtn = document.getElementById('grid-toggle-btn');
            if (gridToggleBtn) {
                gridToggleBtn.addEventListener('click', () => {
                    const visible = KGrid.toggle();
                    gridToggleBtn.classList.toggle('toggled-off', !visible);
                    gridToggleBtn.title = visible ? 'Hide grid' : 'Show grid';
                });
            }

            const unitsToggleBtn = document.getElementById('units-toggle-btn');
            if (unitsToggleBtn) {
                unitsToggleBtn.addEventListener('click', () => {
                    const visible = KUnits.toggle();
                    unitsToggleBtn.classList.toggle('toggled-off', !visible);
                    unitsToggleBtn.title = visible ? 'Hide units' : 'Show units';
                });
            }

            const overlaysToggleBtn = document.getElementById('overlays-toggle-btn');
            if (overlaysToggleBtn) {
                overlaysToggleBtn.addEventListener('click', () => {
                    const visible = KOverlays.toggle();
                    overlaysToggleBtn.classList.toggle('toggled-off', !visible);
                    overlaysToggleBtn.title = visible ? 'Hide overlays' : 'Show overlays';
                });
            }
        }, 0);
    }

    function _clearActive() {
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    return { init, addMapControls };
})();
