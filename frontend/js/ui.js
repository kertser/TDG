/**
 * ui.js – Sidebar tab switching, drawing toolbar in topbar,
 *         standalone center-on-map button.
 */
const KUI = (() => {
    function init() {
        // Tab switching
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
                btn.classList.add('active');
                const panel = document.getElementById(btn.dataset.tab);
                if (panel) panel.classList.add('active');
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
                } else if (tool === 'clear-measure') {
                    KMap.clearMeasure();
                    _clearActive();
                } else {
                    // Drawing tool (arrow, polyline, rectangle, marker, circle)
                    KMap.stopMeasure();
                    _clearActive();
                    btn.classList.add('active');
                    KOverlays.startDraw(tool);
                }
            });
        });

        // Standalone center button (right side of topbar)
        const centerBtn = document.getElementById('center-btn');
        if (centerBtn) {
            centerBtn.addEventListener('click', () => {
                KMap.centerOnOperation();
            });
        }

        // Grid toggle button (right side of topbar)
        const gridToggleBtn = document.getElementById('grid-toggle-btn');
        if (gridToggleBtn) {
            gridToggleBtn.addEventListener('click', () => {
                const visible = KGrid.toggle();
                gridToggleBtn.style.opacity = visible ? '1' : '0.4';
                gridToggleBtn.title = visible ? 'Hide grid' : 'Show grid';
            });
        }
    }

    function _clearActive() {
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    return { init };
})();
