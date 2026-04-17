/**
 * replay.js – Session replay with turn-by-turn playback.
 *
 * Integrates into the game clock panel (bottom-right).
 * Expands on hover to show playback controls.
 * In replay mode: orders/chat panel is hidden, map is view-only.
 * Units are rendered at their positions per tick with smooth animation.
 */
const KReplay = (() => {
    let _sessionId = null;
    let _token = null;
    let _replayData = null;
    let _currentTick = 0;
    let _maxTick = 0;
    let _playing = false;
    let _playInterval = null;
    let _playSpeed = 1000;
    let _isReplayMode = false;
    let _aarText = null;
    let _aarLoading = false;
    let _aarWindow = null;   // floating AAR window element
    let _replayMarkers = {};  // unit_id -> L.marker
    let _replayLayer = null;  // L.layerGroup for replay unit markers

    function init() {
        _buildReplayPanel();
    }

    function _buildReplayPanel() {
        // Wait for the game clock to be rendered by Leaflet
        const clockCtrl = document.querySelector('.game-clock-control');
        if (!clockCtrl) {
            // Retry after DOM is ready
            setTimeout(_buildReplayPanel, 500);
            return;
        }

        // Create replay panel (hidden by default, shown on hover)
        const panel = document.createElement('div');
        panel.id = 'replay-panel';
        panel.className = 'replay-panel';
        panel.innerHTML = `
            <div class="replay-controls">
                <button class="replay-btn" id="replay-load-btn" title="Load replay data">
                    <svg viewBox="0 0 16 16" width="12" height="12"><path d="M2 2v12l12-6z" fill="currentColor"/></svg>
                    Replay
                </button>
                <div class="replay-playback" id="replay-playback" style="display:none;">
                    <div class="replay-transport">
                        <button class="replay-ctrl-btn" id="replay-start-btn" title="Go to start">⏮</button>
                        <button class="replay-ctrl-btn" id="replay-back-btn" title="Previous turn">◀</button>
                        <button class="replay-ctrl-btn replay-play-btn" id="replay-play-btn" title="Play/Pause">▶</button>
                        <button class="replay-ctrl-btn" id="replay-fwd-btn" title="Next turn">▶</button>
                        <button class="replay-ctrl-btn" id="replay-end-btn" title="Go to end">⏭</button>
                    </div>
                    <div class="replay-progress">
                        <input type="range" id="replay-slider" class="replay-slider" min="0" max="0" value="0">
                        <span class="replay-tick-display" id="replay-tick-display">0 / 0</span>
                    </div>
                    <div class="replay-speed-row">
                        <label class="replay-speed-label">Speed:</label>
                        <select id="replay-speed" class="replay-speed-select">
                            <option value="2000">0.5×</option>
                            <option value="1000" selected>1×</option>
                            <option value="500">2×</option>
                            <option value="250">4×</option>
                        </select>
                        <button class="replay-ctrl-btn replay-aar-btn" id="replay-aar-btn" title="Generate After-Action Report">📊 AAR</button>
                        <button class="replay-ctrl-btn replay-exit-btn" id="replay-exit-btn" title="Exit replay mode">✕</button>
                    </div>
                </div>
            </div>
            <div class="replay-events" id="replay-events" style="display:none;"></div>
        `;
        clockCtrl.appendChild(panel);

        // Event listeners
        document.getElementById('replay-load-btn').addEventListener('click', _loadReplay);
        document.getElementById('replay-play-btn').addEventListener('click', _togglePlay);
        document.getElementById('replay-back-btn').addEventListener('click', () => _stepTick(-1));
        document.getElementById('replay-fwd-btn').addEventListener('click', () => _stepTick(1));
        document.getElementById('replay-start-btn').addEventListener('click', () => _goToTick(0));
        document.getElementById('replay-end-btn').addEventListener('click', () => _goToTick(_maxTick));
        document.getElementById('replay-exit-btn').addEventListener('click', _exitReplay);
        document.getElementById('replay-aar-btn').addEventListener('click', _showAAR);

        const slider = document.getElementById('replay-slider');
        slider.addEventListener('input', () => {
            _goToTick(parseInt(slider.value, 10));
        });

        const speedSel = document.getElementById('replay-speed');
        speedSel.addEventListener('change', () => {
            _playSpeed = parseInt(speedSel.value, 10);
            if (_playing) {
                _stopPlay();
                _startPlay();
            }
        });
    }

    function setSession(sessionId, token) {
        _sessionId = sessionId;
        _token = token;
    }

    async function _loadReplay() {
        if (!_sessionId || !_token) return;

        const btn = document.getElementById('replay-load-btn');
        btn.textContent = '⏳ Loading…';
        btn.disabled = true;

        try {
            const resp = await fetch(`/api/sessions/${_sessionId}/replay`, {
                headers: { 'Authorization': `Bearer ${_token}` },
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            _replayData = await resp.json();
            _maxTick = _replayData.max_tick || 0;
            if (_maxTick === 0) {
                btn.textContent = '⚠ No turns';
                setTimeout(_resetLoadBtn, 2000);
                return;
            }
            _currentTick = 0;
            _enterReplayMode();
        } catch (err) {
            console.error('Replay load failed:', err);
            btn.textContent = '⚠ Failed';
            setTimeout(_resetLoadBtn, 2000);
        }
    }

    function _resetLoadBtn() {
        const btn = document.getElementById('replay-load-btn');
        if (btn) {
            btn.innerHTML = '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M2 2v12l12-6z" fill="currentColor"/></svg> Replay';
            btn.disabled = false;
        }
    }

    function _enterReplayMode() {
        _isReplayMode = true;

        // Show playback controls, hide load button
        document.getElementById('replay-load-btn').style.display = 'none';
        document.getElementById('replay-playback').style.display = '';
        document.getElementById('replay-events').style.display = '';

        // Pin the panel open
        const panel = document.getElementById('replay-panel');
        if (panel) panel.classList.add('replay-active');

        // Configure slider
        const slider = document.getElementById('replay-slider');
        slider.max = _maxTick;
        slider.value = 0;

        // Hide live units + command panel
        const cmdPanel = document.getElementById('command-panel');
        if (cmdPanel) cmdPanel.style.display = 'none';
        try { KUnits.toggle(false); } catch(e) {}

        // Create replay layer
        const map = KMap.getMap();
        if (map) {
            _replayLayer = L.layerGroup().addTo(map);
        }

        // Render tick 0
        _goToTick(0);
    }

    function _exitReplay() {
        _isReplayMode = false;
        _stopPlay();
        _aarText = null;

        _clearReplayMarkers();
        if (_replayLayer) {
            const map = KMap.getMap();
            if (map) map.removeLayer(_replayLayer);
            _replayLayer = null;
        }

        // Hide playback, show load button
        document.getElementById('replay-playback').style.display = 'none';
        document.getElementById('replay-events').style.display = 'none';
        document.getElementById('replay-load-btn').style.display = '';
        _resetLoadBtn();
        if (_aarWindow) { _aarWindow.remove(); _aarWindow = null; }

        const panel = document.getElementById('replay-panel');
        if (panel) panel.classList.remove('replay-active');

        // Restore live units + command panel
        const cmdPanel = document.getElementById('command-panel');
        if (cmdPanel) cmdPanel.style.display = '';
        try { KUnits.toggle(true); } catch(e) {}

        _replayData = null;
    }

    function _togglePlay() {
        if (_playing) {
            _stopPlay();
        } else {
            _startPlay();
        }
    }

    function _startPlay() {
        if (_currentTick >= _maxTick) _currentTick = 0;
        _playing = true;
        document.getElementById('replay-play-btn').textContent = '⏸';
        _playInterval = setInterval(() => {
            if (_currentTick >= _maxTick) {
                _stopPlay();
                return;
            }
            _stepTick(1);
        }, _playSpeed);
    }

    function _stopPlay() {
        _playing = false;
        document.getElementById('replay-play-btn').textContent = '▶';
        if (_playInterval) {
            clearInterval(_playInterval);
            _playInterval = null;
        }
    }

    function _stepTick(delta) {
        _goToTick(_currentTick + delta);
    }

    function _goToTick(tick) {
        tick = Math.max(0, Math.min(_maxTick, tick));
        _currentTick = tick;

        const slider = document.getElementById('replay-slider');
        if (slider) slider.value = tick;
        const display = document.getElementById('replay-tick-display');
        if (display) display.textContent = `${tick} / ${_maxTick}`;

        // Update game clock display
        KMap.setGameTime(tick, null);
        _renderTickUnits(tick);
        _renderTickEvents(tick);
    }

    function _clearReplayMarkers() {
        for (const uid in _replayMarkers) {
            if (_replayLayer) _replayLayer.removeLayer(_replayMarkers[uid]);
        }
        _replayMarkers = {};
        if (_replayLayer) _replayLayer.clearLayers();
    }

    function _renderTickUnits(tick) {
        if (!_replayData || !_replayData.ticks) return;
        const tickData = _replayData.ticks[tick];
        if (!tickData || !tickData.units) return;

        const unitList = tickData.units;
        const activeIds = new Set();

        for (const u of unitList) {
            if (u.is_destroyed) continue;
            if (u.lat == null || u.lon == null) continue;
            activeIds.add(u.id);

            const pos = L.latLng(u.lat, u.lon);

            if (_replayMarkers[u.id]) {
                // Animate to new position
                const marker = _replayMarkers[u.id];
                const oldPos = marker.getLatLng();
                if (Math.abs(oldPos.lat - pos.lat) > 0.00001 || Math.abs(oldPos.lng - pos.lng) > 0.00001) {
                    _animateMarker(marker, oldPos, pos, Math.min(400, _playSpeed * 0.4));
                }
            } else {
                // Create new marker
                const icon = _makeReplayIcon(u);
                const marker = L.marker(pos, { icon: icon, interactive: true });
                if (_replayLayer) _replayLayer.addLayer(marker);
                _replayMarkers[u.id] = marker;
                marker.bindTooltip(u.name, {
                    permanent: false, direction: 'top', offset: [0, -15],
                });
            }
        }

        // Remove markers for destroyed/absent units
        for (const uid in _replayMarkers) {
            if (!activeIds.has(uid)) {
                if (_replayLayer) _replayLayer.removeLayer(_replayMarkers[uid]);
                delete _replayMarkers[uid];
            }
        }
    }

    function _makeReplayIcon(u) {
        if (typeof ms !== 'undefined' && u.sidc) {
            try {
                const sym = new ms.Symbol(u.sidc, { size: 28, frame: true });
                const svg = sym.asSVG();
                const anchor = sym.getAnchor();
                return L.divIcon({
                    html: svg,
                    className: 'replay-unit-icon',
                    iconSize: [sym.getSize().width, sym.getSize().height],
                    iconAnchor: [anchor.x, anchor.y],
                });
            } catch(e) {}
        }
        const color = u.side === 'red' ? '#ef5350' : '#42a5f5';
        return L.divIcon({
            html: `<div style="width:14px;height:14px;border-radius:50%;background:${color};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.5);"></div>`,
            className: 'replay-unit-dot',
            iconSize: [14, 14],
            iconAnchor: [7, 7],
        });
    }

    function _animateMarker(marker, from, to, duration) {
        const start = performance.now();
        function step(now) {
            const elapsed = now - start;
            const t = Math.min(1, elapsed / duration);
            const ease = 1 - Math.pow(1 - t, 3);
            const lat = from.lat + (to.lat - from.lat) * ease;
            const lng = from.lng + (to.lng - from.lng) * ease;
            marker.setLatLng([lat, lng]);
            if (t < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }

    function _renderTickEvents(tick) {
        if (!_replayData || !_replayData.ticks) return;
        const tickData = _replayData.ticks[tick] || { events: [], orders: [], reports: [] };
        const el = document.getElementById('replay-events');
        if (!el) return;

        let html = '';

        // Orders issued this tick
        if (tickData.orders && tickData.orders.length > 0) {
            for (const o of tickData.orders) {
                const icon = o.issued_by_side === 'red' ? '🔴' : '🔵';
                html += `<div class="replay-event replay-order">${icon} 📋 ${_esc(o.original_text || o.order_type || 'Order')}</div>`;
            }
        }

        // Events this tick
        if (tickData.events && tickData.events.length > 0) {
            for (const e of tickData.events) {
                if (e.event_type === 'movement') continue;
                const icon = _eventIcon(e.event_type);
                html += `<div class="replay-event replay-evt-${e.event_type}">${icon} ${_esc(e.text_summary || e.event_type)}</div>`;
            }
        }

        // Reports
        if (tickData.reports && tickData.reports.length > 0) {
            for (const r of tickData.reports) {
                html += `<div class="replay-event replay-report">📄 [${_esc(r.channel)}] ${_esc((r.text || '').substring(0, 120))}</div>`;
            }
        }

        if (!html) html = '<div class="replay-event replay-empty">— No events —</div>';
        el.innerHTML = html;
        el.scrollTop = el.scrollHeight;
    }

    function _eventIcon(type) {
        const icons = {
            combat: '⚔', unit_destroyed: '💥', contact_new: '👁', contact_lost: '❌',
            morale_break: '💔', order_issued: '📋', order_completed: '✅',
            artillery_support: '💣', detection: '🔍', object_discovered: '🗺',
        };
        return icons[type] || '•';
    }

    async function _showAAR() {
        // Open or focus the floating AAR window
        if (_aarWindow && document.body.contains(_aarWindow)) {
            _aarWindow.style.display = 'flex';
            return;
        }
        _createAARWindow();
        const body = _aarWindow.querySelector('.aar-win-body');

        if (_aarText) { body.innerHTML = _formatAAR(_aarText); return; }
        if (_aarLoading) return;
        _aarLoading = true;
        body.innerHTML = '<div class="replay-aar-loading">⏳ Generating After-Action Report…<br><span style="font-size:10px;color:#667;">This may take 15-30 seconds</span></div>';

        try {
            const resp = await fetch(`/api/sessions/${_sessionId}/aar`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${_token}`, 'Content-Type': 'application/json' },
            });
            const data = await resp.json();
            _aarText = data.aar || 'No AAR generated.';
            body.innerHTML = _formatAAR(_aarText);
        } catch (err) {
            body.innerHTML = '<div class="replay-aar-error">⚠ Failed to generate After-Action Report</div>';
        } finally {
            _aarLoading = false;
        }
    }

    function _createAARWindow() {
        const win = document.createElement('div');
        win.className = 'aar-floating-window';
        win.innerHTML = `
            <div class="aar-win-titlebar">
                <div class="aar-win-title">
                    <span class="aar-win-icon">📊</span>
                    <span>AFTER-ACTION REPORT</span>
                    <span class="aar-win-classification">UNCLASSIFIED // FOUO</span>
                </div>
                <div class="aar-win-controls">
                    <button class="aar-win-btn aar-win-copy" title="Copy to clipboard">📋</button>
                    <button class="aar-win-btn aar-win-close" title="Close">✕</button>
                </div>
            </div>
            <div class="aar-win-body"></div>
            <div class="aar-win-footer">
                <span class="aar-win-footer-text">Generated by AI Staff Officer • Exercise AAR</span>
            </div>
        `;
        document.body.appendChild(win);
        _aarWindow = win;

        // Close
        win.querySelector('.aar-win-close').addEventListener('click', () => {
            win.style.display = 'none';
        });

        // Copy
        win.querySelector('.aar-win-copy').addEventListener('click', () => {
            const text = _aarText || '';
            navigator.clipboard.writeText(text).then(() => {
                const btn = win.querySelector('.aar-win-copy');
                btn.textContent = '✅';
                setTimeout(() => btn.textContent = '📋', 1500);
            });
        });

        // Drag
        _makeDraggable(win, win.querySelector('.aar-win-titlebar'));
    }

    function _makeDraggable(el, handle) {
        let offsetX = 0, offsetY = 0, dragging = false;
        handle.style.cursor = 'move';
        handle.addEventListener('mousedown', (e) => {
            if (e.target.closest('.aar-win-btn')) return;
            dragging = true;
            offsetX = e.clientX - el.offsetLeft;
            offsetY = e.clientY - el.offsetTop;
            el.style.transition = 'none';
            e.preventDefault();
        });
        document.addEventListener('mousemove', (e) => {
            if (!dragging) return;
            el.style.left = (e.clientX - offsetX) + 'px';
            el.style.top = (e.clientY - offsetY) + 'px';
            el.style.right = 'auto';
            el.style.bottom = 'auto';
        });
        document.addEventListener('mouseup', () => {
            if (dragging) {
                dragging = false;
                el.style.transition = '';
            }
        });
    }

    function _formatAAR(text) {
        let html = text
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/^# (.+)$/gm, '<h2 class="aar-h2">$1</h2>')
            .replace(/^## (.+)$/gm, '<h3 class="aar-h3">$1</h3>')
            .replace(/^### (.+)$/gm, '<h4 class="aar-h4">$1</h4>')
            .replace(/^\d+\.\s+(.+)$/gm, '<div class="aar-numbered">$&</div>')
            .replace(/^- (.+)$/gm, '<li>$1</li>')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\n\n/g, '</p><p>')
            .replace(/\n/g, '<br>');
        return `<div class="aar-text"><p>${html}</p></div>`;
    }

    function _esc(s) {
        const d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    function isReplayMode() { return _isReplayMode; }

    function clearData() {
        _replayData = null;
        _aarText = null;
        _maxTick = 0;
        _currentTick = 0;
        if (_isReplayMode) _exitReplay();
    }

    return { init, setSession, isReplayMode, clearData };
})();
