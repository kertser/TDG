/**
 * KTutorial — interactive onboarding / spotlight tutorial
 *
 * Usage:
 *   KTutorial.init();
 *   KTutorial.start();        // show from step 0
 *   KTutorial.startIfNeeded() // show only if user hasn't completed it yet
 *
 * Each step has:
 *   title       : string
 *   body        : HTML string
 *   selector    : CSS selector to highlight (optional)
 *   waitFor     : custom DOM event name to auto-advance (optional)
 */
const KTutorial = (() => {
    let _steps = [];
    let _idx = 0;
    let _overlay = null;
    let _box = null;
    let _spotlight = null;
    let _autoHandler = null;

    // ─── public API ──────────────────────────────────────────────────────

    function init() {
        _steps = _buildSteps();
    }

    async function startIfNeeded() {
        // Check the session_ui for tutorial_completed flag
        const completed = _getTutorialCompleted();
        if (!completed) {
            // Small delay to let the map render first
            setTimeout(() => start(), 800);
        }
    }

    function start() {
        if (_steps.length === 0) init();
        _idx = 0;
        _buildOverlay();
        _renderStep();
    }

    function skip() {
        _teardown();
        _markCompleted();
    }

    // ─── private ──────────────────────────────────────────────────────────

    function _getTutorialCompleted() {
        try {
            const stored = localStorage.getItem('kshu_tutorial_completed');
            return stored === 'true';
        } catch (_) { return false; }
    }

    function _markCompleted() {
        try { localStorage.setItem('kshu_tutorial_completed', 'true'); } catch (_) {}
        // Also call the backend endpoint
        const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;
        if (token) {
            fetch('/api/auth/tutorial-complete', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
            }).catch(() => {});
        }
    }

    function _next() {
        if (_autoHandler) {
            document.removeEventListener(_steps[_idx]?.waitFor, _autoHandler);
            _autoHandler = null;
        }
        if (_idx >= _steps.length - 1) { skip(); return; }
        _idx++;
        _renderStep();
    }

    function _prev() {
        if (_idx === 0) return;
        _idx--;
        _renderStep();
    }

    function _renderStep() {
        const step = _steps[_idx];
        if (!step) return;

        const target = step.selector ? document.querySelector(step.selector) : null;
        _highlight(target);

        _box.querySelector('.kt-title').textContent = step.title || '';
        _box.querySelector('.kt-body').innerHTML = step.body || '';
        _box.querySelector('.kt-progress').textContent = `${_idx + 1} / ${_steps.length}`;
        _box.querySelector('.kt-prev').disabled = (_idx === 0);
        _box.querySelector('.kt-next').textContent = (_idx === _steps.length - 1) ? 'Finish ✓' : 'Next →';

        _positionBox(target);

        // Auto-advance on waitFor event
        if (step.waitFor) {
            _autoHandler = () => { _autoHandler = null; _next(); };
            document.addEventListener(step.waitFor, _autoHandler, { once: true });
        }
    }

    function _buildOverlay() {
        if (_overlay) return;

        _overlay = document.createElement('div');
        _overlay.className = 'kt-overlay';
        _overlay.innerHTML = `
          <div class="kt-spotlight"></div>
          <div class="kt-box">
            <div class="kt-header">
              <span class="kt-progress"></span>
              <button class="kt-skip-btn" title="Skip tutorial">✕ Skip</button>
            </div>
            <h3 class="kt-title"></h3>
            <div class="kt-body"></div>
            <div class="kt-actions">
              <button class="kt-prev">← Back</button>
              <button class="kt-next">Next →</button>
            </div>
          </div>`;
        document.body.appendChild(_overlay);

        _box = _overlay.querySelector('.kt-box');
        _spotlight = _overlay.querySelector('.kt-spotlight');
        _overlay.querySelector('.kt-skip-btn').onclick = () => skip();
        _overlay.querySelector('.kt-prev').onclick = () => _prev();
        _overlay.querySelector('.kt-next').onclick = () => _next();

        // Clicking the dark backdrop skips
        _overlay.addEventListener('click', (e) => {
            if (e.target === _overlay) skip();
        });
    }

    function _highlight(el) {
        if (!_spotlight) return;
        if (!el) {
            _spotlight.style.display = 'none';
            return;
        }
        const r = el.getBoundingClientRect();
        const pad = 8;
        _spotlight.style.display = 'block';
        _spotlight.style.left   = `${r.left - pad}px`;
        _spotlight.style.top    = `${r.top  - pad}px`;
        _spotlight.style.width  = `${r.width  + pad * 2}px`;
        _spotlight.style.height = `${r.height + pad * 2}px`;
    }

    function _positionBox(target) {
        if (!_box) return;
        if (!target) {
            _box.style.left      = '50%';
            _box.style.top       = '50%';
            _box.style.transform = 'translate(-50%, -50%)';
            return;
        }
        _box.style.transform = 'none';
        const r   = target.getBoundingClientRect();
        const gap = 18;
        const bw  = 340;
        const bh  = 240;

        // Prefer right of target, else left, else below
        let left = r.right + gap;
        let top  = r.top;
        if (left + bw > window.innerWidth - 8) {
            left = r.left - bw - gap;
        }
        if (left < 8) { left = 8; top = r.bottom + gap; }
        if (top + bh > window.innerHeight - 8) { top = window.innerHeight - bh - 8; }
        if (top < 8) { top = 8; }

        _box.style.left = `${left}px`;
        _box.style.top  = `${top}px`;
    }

    function _teardown() {
        if (_autoHandler) {
            const step = _steps[_idx];
            if (step?.waitFor) document.removeEventListener(step.waitFor, _autoHandler);
            _autoHandler = null;
        }
        if (_overlay) { _overlay.remove(); _overlay = null; _box = null; _spotlight = null; }
    }

    function _buildSteps() {
        return [
            {
                title: 'Welcome to KShU',
                body: 'This is a <b>tactical command exercise platform</b>. You will issue orders to units on a live map.<br><br>Press <b>Next</b> to begin the tour or <b>Skip</b> to go straight to the action.',
            },
            {
                selector: '#map',
                title: 'The Tactical Map',
                body: 'Your battlefield. <b>Drag</b> to pan · <b>Scroll</b> to zoom.<br>The grid overlaid on the map provides the reference system for orders — e.g. <code>F7-5-3</code>.',
            },
            {
                selector: '.leaflet-control-zoom',
                title: 'Map Controls',
                body: 'Zoom buttons are top-left. The <b>top-right panel</b> (▼) contains draw tools and map layer toggles (grid, units, overlays, contacts, labels).',
            },
            {
                selector: '#sidebar',
                title: 'Sidebar Panels',
                body: 'The right sidebar has tabs:<br>• <b>Units</b> — chain of command tree<br>• <b>Events</b> — game timeline<br>• <b>Reports</b> — SITREPs, SPOTREPs<br>• <b>Log</b> — app messages',
            },
            {
                selector: '#cmd-panel',
                title: 'Command Panel',
                body: '<b>Click a unit</b> on the map, then type an order here.<br>Examples:<br><code>Move to F7-5 fast</code><br><code>Атакуй квадрат C9-2</code><br>Press <kbd>Ctrl+Enter</kbd> or the send button.',
                waitFor: 'kshu:order-submitted',
            },
            {
                selector: '#radio-tab',
                title: 'Radio Channel',
                body: 'Unit radio responses appear here. They acknowledge commands and report situational changes — terrain, contacts, strength, morale.',
            },
            {
                selector: '#orders-complete-btn',
                title: 'Execute Turn',
                body: 'When ready, click <b>✔ Orders Complete</b> to advance the simulation one tick.<br>Units move, detect enemies, fire, and consume ammo.',
            },
            {
                title: 'You\'re Ready!',
                body: 'That\'s the basics. A few pro tips:<br>• <b>Right-click</b> a unit for context menu (move, formation, split…)<br>• <b>Alt+click</b> overlapping units to cycle through a stack<br>• The <b>⚙ Admin</b> panel (top-right) lets you place map objects, analyse terrain, and manage Red AI<br><br>Good luck, Commander.',
            },
        ];
    }

    return { init, start, startIfNeeded, skip };
})();

// ─── Inject CSS ───────────────────────────────────────────────────────────────
(function injectTutorialCSS() {
    if (document.getElementById('kt-style')) return;
    const style = document.createElement('style');
    style.id = 'kt-style';
    style.textContent = `
.kt-overlay {
    position: fixed; inset: 0; z-index: 10000;
    background: rgba(0,0,0,0.55);
    pointer-events: all;
}
.kt-spotlight {
    position: absolute;
    border-radius: 6px;
    box-shadow: 0 0 0 9999px rgba(0,0,0,0.55);
    pointer-events: none;
    transition: all 0.25s ease;
    border: 2px solid rgba(100,200,255,0.6);
}
.kt-box {
    position: absolute;
    width: 340px;
    background: #1a2332;
    border: 1px solid #2a3f5a;
    border-radius: 10px;
    padding: 18px 20px 14px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.6);
    color: #d9e8f5;
    font-family: inherit;
    pointer-events: all;
}
.kt-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
}
.kt-progress {
    font-size: 11px;
    color: #5b7fa0;
    font-variant-numeric: tabular-nums;
}
.kt-skip-btn {
    background: none; border: none; color: #5b7fa0; cursor: pointer;
    font-size: 12px; padding: 2px 4px;
}
.kt-skip-btn:hover { color: #aaa; }
.kt-title {
    font-size: 15px; font-weight: 700; color: #7ecfff; margin: 0 0 8px;
}
.kt-body {
    font-size: 13px; line-height: 1.55; color: #c5d9ec;
    margin-bottom: 14px;
}
.kt-body code, .kt-body kbd {
    background: #0d1b2a; padding: 2px 5px; border-radius: 4px;
    font-family: monospace; font-size: 12px; color: #7ecfff;
}
.kt-actions {
    display: flex; justify-content: space-between;
}
.kt-prev, .kt-next {
    background: #1e3a5a; border: 1px solid #2a5882; color: #7ecfff;
    padding: 6px 14px; border-radius: 5px; cursor: pointer;
    font-size: 12px; font-weight: 600;
}
.kt-prev:hover, .kt-next:hover { background: #2a4f70; }
.kt-prev:disabled { opacity: 0.35; cursor: default; }
.kt-next { background: #0b4a7a; }
.kt-next:hover { background: #0d5d99; }
`;
    document.head.appendChild(style);
})();

