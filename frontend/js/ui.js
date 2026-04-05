/**
 * ui.js – Sidebar tab switching, drawing toolbar in topbar, center-on-map.
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

        // Drawing toolbar buttons
        document.querySelectorAll('.draw-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const tool = btn.dataset.tool;

                if (tool === 'cancel') {
                    KOverlays.cancelDraw();
                    KMap.stopMeasure();
                    _clearActive();
                } else if (tool === 'measure') {
                    KOverlays.cancelDraw();
                    _clearActive();
                    btn.classList.add('active');
                    KMap.startMeasure();
                } else if (tool === 'clear-measure') {
                    KMap.clearMeasure();
                    _clearActive();
                } else if (tool === 'center') {
                    KMap.centerOnOperation();
                } else {
                    // Drawing tool
                    KMap.stopMeasure();
                    _clearActive();
                    btn.classList.add('active');
                    KOverlays.startDraw(tool);
                }
            });
        });
    }

    function _clearActive() {
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    return { init };
})();
