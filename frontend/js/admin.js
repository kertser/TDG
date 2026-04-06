/**
 * admin.js – Full admin/game-master panel (floating window).
 *
 * Sub-tabs: Builder | Session | Monitor | Users | CoC
 *   Builder  – scenario builder toggle, scenario list with edit
 *   Session  – participants, tick controls, reset, event injection, grid
 *   Monitor  – god-view toggle, unit dashboard, all orders
 *   Users    – manage registered users (add/rename/delete/bulk-delete/assign-to-session)
 *   CoC      – chain of command tree, assign units to parents
 *
 * Admin tab is locked behind a password (ADMIN_PASSWORD in settings).
 * Admin selects a session via a dropdown — not dependent on user's joined session.
 */
const KAdmin = (() => {

    let _godViewEnabled = false;
    let _adminUnlocked = false;
    let _adminSelectedSessionId = null;  // admin-chosen session (independent of user's session)
    let _pickingGridOrigin = false;      // map-click pick mode for grid origin

    function init() {
        // ── Admin lock gate ────────────────────────────
        _bind('admin-unlock-btn', 'click', _unlockAdmin);
        const pwInput = document.getElementById('admin-pw-input');
        if (pwInput) {
            pwInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') _unlockAdmin();
            });
        }

        // ── Admin topbar button ─────────────────────────
        _bind('admin-topbar-btn', 'click', _onAdminTopbarClick);

        // ── Admin floating window close ─────────────────
        _bind('admin-window-close', 'click', () => {
            const win = document.getElementById('admin-window');
            if (win) win.style.display = 'none';
        });

        // ── Draggable window header ─────────────────────
        _initDraggableWindow();

        // Sub-tab switching inside admin tab
        document.querySelectorAll('.admin-subtab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.admin-subtab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.admin-subtab-panel').forEach(p => p.style.display = 'none');
                btn.classList.add('active');
                const panel = document.getElementById(btn.dataset.panel);
                if (panel) panel.style.display = 'block';
            });
        });

        // ── Admin Session Selector ──────────────────────
        _bind('admin-refresh-session-selector', 'click', _loadAdminSessions);
        const selector = document.getElementById('admin-session-selector');
        if (selector) {
            selector.addEventListener('change', () => {
                _adminSelectedSessionId = selector.value || null;
                const info = document.getElementById('admin-selected-session-info');
                if (info) info.textContent = _adminSelectedSessionId
                    ? `Selected: ${_adminSelectedSessionId.substring(0, 8)}...`
                    : 'No session selected';
            });
        }

        // ── Builder sub-tab ─────────────────────────
        _bind('sb-toggle-btn', 'click', _toggleBuilder);
        _bind('sb-confirm-unit', 'click', () => KScenarioBuilder.confirmUnit());
        _bind('sb-cancel-unit', 'click', () => KScenarioBuilder.hideUnitForm());
        _bind('sb-save-scenario', 'click', () => KScenarioBuilder.saveScenario());
        _bind('sb-unit-type', 'change', () => KScenarioBuilder.onTypeChange());

        // Populate unit type dropdown
        _populateUnitTypeDropdown();

        // ── Scenario list ───────────────────────────
        _bind('admin-list-scenarios', 'click', refreshScenarioList);
        _bind('admin-delete-all-scenarios', 'click', _deleteAllScenarios);

        // ── Session sub-tab ─────────────────────────
        _bind('admin-delete-all-sessions', 'click', _deleteAllSessions);
        _bind('admin-refresh-sessions', 'click', _refreshSessions);
        _bind('admin-pause-session', 'click', _pauseSession);
        _bind('admin-reset-session', 'click', _resetSession);
        _bind('admin-apply-turn-interval', 'click', _applyTurnInterval);
        _bind('admin-load-participants', 'click', _loadParticipants);
        _bind('admin-inject-event', 'click', _injectEvent);
        _bind('admin-apply-grid', 'click', _applyGrid);
        _bind('admin-grid-from-session', 'click', _loadGridFromSession);
        _bind('admin-grid-pick-map', 'click', _pickGridFromMap);

        // ── Monitor sub-tab ─────────────────────────
        _bind('admin-god-view-toggle', 'click', _toggleGodView);
        _bind('admin-load-dashboard', 'click', _loadUnitDashboard);
        _bind('admin-load-orders', 'click', _loadAllOrders);
        _bind('admin-db-stats', 'click', _loadDbStats);

        // ── Users sub-tab ───────────────────────────
        _bind('admin-load-users', 'click', _loadUsers);
        _bind('admin-add-user-btn', 'click', _addUser);
        _bind('admin-bulk-delete-users', 'click', _bulkDeleteUsers);
        const addUserInput = document.getElementById('admin-add-user-name');
        if (addUserInput) addUserInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') _addUser(); });

        // Select-all checkbox for users
        const selectAllCb = document.getElementById('admin-users-select-all');
        if (selectAllCb) {
            selectAllCb.addEventListener('change', () => {
                document.querySelectorAll('.admin-user-cb').forEach(cb => {
                    cb.checked = selectAllCb.checked;
                });
            });
        }

        // ── Chain of Command sub-tab ────────────────
        _bind('admin-load-coc', 'click', _loadChainOfCommand);

        // ── Public CoC tab refresh ──────────────────
        _bind('coc-refresh-btn', 'click', loadPublicCoC);

        // ── Initialize modals ─────────────────────────
        _initAssignModal();
        _initCocPickerModal();
        _initCocUserAssignModal();
    }

    function _bind(id, evt, fn) {
        const el = document.getElementById(id);
        if (el) el.addEventListener(evt, fn);
    }

    function _getToken() { return KSessionUI.getToken(); }
    function _getUserSessionId() { return KSessionUI.getSessionId(); }
    /** Admin session: prefer the admin dropdown, fall back to user's session */
    function _getAdminSessionId() { return _adminSelectedSessionId || _getUserSessionId(); }

    // ══════════════════════════════════════════════════
    // ── Admin Floating Window (draggable) ────────────
    // ══════════════════════════════════════════════════

    function _initDraggableWindow() {
        const win = document.getElementById('admin-window');
        const header = document.getElementById('admin-window-header');
        if (!win || !header) return;

        let isDragging = false;
        let startX, startY, startLeft, startTop;

        header.addEventListener('pointerdown', (e) => {
            if (e.target.id === 'admin-window-close') return;
            isDragging = true;
            startX = e.clientX;
            startY = e.clientY;
            const rect = win.getBoundingClientRect();
            startLeft = rect.left;
            startTop = rect.top;
            header.setPointerCapture(e.pointerId);
            e.preventDefault();
        });

        header.addEventListener('pointermove', (e) => {
            if (!isDragging) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            win.style.left = (startLeft + dx) + 'px';
            win.style.top = (startTop + dy) + 'px';
            win.style.right = 'auto';
        });

        header.addEventListener('pointerup', () => {
            isDragging = false;
        });
    }

    // ══════════════════════════════════════════════════
    // ── Admin Topbar Button ─────────────────────────
    // ══════════════════════════════════════════════════

    function _onAdminTopbarClick() {
        const win = document.getElementById('admin-window');
        if (!win) return;
        // Toggle visibility
        if (win.style.display === 'none' || !win.style.display) {
            win.style.display = 'flex';
        } else {
            win.style.display = 'none';
        }
    }

    // ══════════════════════════════════════════════════
    // ── Admin Password Gate ─────────────────────────
    // ══════════════════════════════════════════════════

    async function _unlockAdmin() {
        const pw = document.getElementById('admin-pw-input');
        if (!pw) return;
        const password = pw.value.trim();
        if (!password) { alert('Enter admin password'); return; }

        try {
            const resp = await fetch('/api/admin/verify-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password }),
            });
            if (resp.ok) {
                _adminUnlocked = true;
                const gate = document.getElementById('admin-lock-gate');
                const content = document.getElementById('admin-content');
                if (gate) gate.style.display = 'none';
                if (content) content.style.display = 'block';
                pw.value = '';
                // Show admin topbar button
                const topbarBtn = document.getElementById('admin-topbar-btn');
                if (topbarBtn) topbarBtn.style.display = '';
                // Load admin sessions dropdown
                _loadAdminSessions();
            } else {
                const data = await resp.json().catch(() => ({}));
                alert(data.detail || 'Incorrect password');
            }
        } catch (err) {
            alert('Error: ' + err.message);
        }
    }

    function isUnlocked() { return _adminUnlocked; }

    // ══════════════════════════════════════════════════
    // ── Admin Session Selector ──────────────────────
    // ══════════════════════════════════════════════════

    async function _loadAdminSessions() {
        try {
            const token = _getToken();
            const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
            // Try admin endpoint first, fall back to user endpoint
            let resp = await fetch('/api/admin/sessions', { headers });
            if (!resp.ok) {
                resp = await fetch('/api/sessions', { headers });
            }
            if (!resp.ok) return;
            const sessions = await resp.json();
            const sel = document.getElementById('admin-session-selector');
            if (!sel) return;

            const prev = sel.value;
            sel.innerHTML = '<option value="">— Select session —</option>';
            sessions.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.id;
                opt.textContent = `${s.id.substring(0, 8)}… [${s.status}] Turn ${s.tick} (${s.participant_count}p)`;
                sel.appendChild(opt);
            });

            // Restore previous selection or auto-select user's session
            if (prev && sessions.find(s => s.id === prev)) {
                sel.value = prev;
            } else if (_getUserSessionId()) {
                sel.value = _getUserSessionId();
            }
            _adminSelectedSessionId = sel.value || null;

            const info = document.getElementById('admin-selected-session-info');
            if (info) info.textContent = _adminSelectedSessionId
                ? `Selected: ${_adminSelectedSessionId.substring(0, 8)}...`
                : `${sessions.length} session(s) available`;
        } catch (err) {
            console.warn('Admin sessions load:', err);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Scenario Builder Toggle ──────────────────────
    // ══════════════════════════════════════════════════

    function _toggleBuilder() {
        if (KScenarioBuilder.isActive()) {
            KScenarioBuilder.deactivate();
        } else {
            KScenarioBuilder.activate();
        }
    }

    function _populateUnitTypeDropdown() {
        const sel = document.getElementById('sb-unit-type');
        if (!sel) return;
        sel.innerHTML = '';
        const types = KScenarioBuilder.getUnitTypes();
        for (const [key, info] of Object.entries(types)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = info.label;
            sel.appendChild(opt);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Scenario Management ──────────────────────────
    // ══════════════════════════════════════════════════

    async function refreshScenarioList() {
        try {
            const resp = await fetch('/api/scenarios');
            const scenarios = await resp.json();
            const listEl = document.getElementById('admin-scenario-list');
            if (!listEl) return;

            if (scenarios.length === 0) {
                listEl.innerHTML = '<div class="admin-info">No scenarios</div>';
                return;
            }

            let html = '';
            scenarios.forEach(s => {
                const unitCount = _countUnits(s.initial_units);
                html += `<div class="admin-item">
                    <div>
                        <b>${s.title || 'Untitled'}</b>
                        <span class="admin-item-meta">${unitCount} units</span>
                    </div>
                    <div style="display:flex;gap:4px;">
                        <button class="admin-btn" onclick="KAdmin.editScenario('${s.id}')" style="padding:2px 8px;font-size:10px;" title="Edit this scenario">✏ Edit</button>
                        <button class="admin-btn admin-btn-danger" onclick="KAdmin.deleteScenario('${s.id}')" style="padding:2px 8px;font-size:10px;" title="Delete this scenario">✕</button>
                    </div>
                </div>`;
            });
            listEl.innerHTML = html;
        } catch (err) {
            _showInfo('admin-scenario-list', `✗ ${err.message}`, 'error');
        }
    }

    function _countUnits(initialUnits) {
        if (!initialUnits) return 0;
        return (initialUnits.blue || []).length + (initialUnits.red || []).length;
    }

    async function editScenario(scenarioId) {
        KScenarioBuilder.activate(scenarioId);
        // Switch to builder sub-tab
        document.querySelectorAll('.admin-subtab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.admin-subtab-panel').forEach(p => p.style.display = 'none');
        const btn = document.querySelector('[data-panel="admin-builder-panel"]');
        if (btn) btn.classList.add('active');
        const panel = document.getElementById('admin-builder-panel');
        if (panel) panel.style.display = 'block';
    }

    async function deleteScenario(scenarioId) {
        if (!confirm('Delete this scenario?')) return;
        const token = _getToken();
        try {
            await fetch(`/api/admin/scenarios/${scenarioId}`, {
                method: 'DELETE',
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            refreshScenarioList();
        } catch (err) {
            alert('Delete failed: ' + err.message);
        }
    }

    async function _deleteAllScenarios() {
        if (!confirm('⚠ Delete ALL scenarios?')) return;
        try {
            const resp = await fetch('/api/scenarios');
            const scenarios = await resp.json();
            const token = _getToken();
            let deleted = 0;
            for (const s of scenarios) {
                const del = await fetch(`/api/admin/scenarios/${s.id}`, {
                    method: 'DELETE',
                    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
                });
                if (del.ok || del.status === 204) deleted++;
            }
            _showInfo('admin-scenario-list', `✓ Deleted ${deleted}/${scenarios.length}`, 'success');
            refreshScenarioList();
        } catch (err) {
            _showInfo('admin-scenario-list', `✗ ${err.message}`, 'error');
        }
    }

    // ══════════════════════════════════════════════════
    // ── Session Management ───────────────────────────
    // ══════════════════════════════════════════════════

    async function _deleteAllSessions() {
        const token = _getToken();
        if (!token) { _showInfo('admin-session-count', 'Not logged in', 'error'); return; }
        if (!confirm('⚠ Delete ALL sessions?')) return;

        try {
            const resp = await fetch('/api/sessions', {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                _showInfo('admin-session-count', '✓ All sessions deleted', 'success');
                _adminSelectedSessionId = null;
                KWebSocket.disconnect();
                document.getElementById('session-info').textContent = '';
                const startBtn = document.getElementById('start-session-btn');
                const turnBtn = document.getElementById('turn-btn');
                if (startBtn) startBtn.style.display = 'none';
                if (turnBtn) turnBtn.style.display = 'none';
                KSessionUI.loadSessions();
                KGameLog.addEntry('All sessions deleted (admin)', 'info');
                _loadAdminSessions();
            } else {
                const data = await resp.json().catch(() => ({}));
                _showInfo('admin-session-count', `✗ ${data.detail || resp.status}`, 'error');
            }
        } catch (err) {
            _showInfo('admin-session-count', `✗ ${err.message}`, 'error');
        }
    }

    async function _refreshSessions() {
        const token = _getToken();
        if (!token) { _showInfo('admin-session-count', 'Not logged in', 'error'); return; }
        try {
            const resp = await fetch('/api/sessions', {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const sessions = await resp.json();
            _showInfo('admin-session-count', `${sessions.length} session(s)`);
            KSessionUI.loadSessions();
            _loadAdminSessions();
        } catch (err) {
            _showInfo('admin-session-count', `✗ ${err.message}`, 'error');
        }
    }

    async function _pauseSession() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-session-status', 'Select a session first', 'error'); return; }
        try {
            const resp = await fetch(`/api/sessions/${sid}/pause`, {
                method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
            });
            const data = await resp.json();
            _showInfo('admin-session-status', `Paused at turn ${data.tick}`, 'success');
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    async function _resetSession() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-session-status', 'Select a session first', 'error'); return; }
        if (!confirm('⚠ Reset session to turn 0? All progress will be lost.')) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/reset`, {
                method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
            });
            const data = await resp.json();
            _showInfo('admin-session-status', `✓ ${data.message}`, 'success');
            KGameLog.addEntry('Session reset to turn 0 (admin)', 'info');
            // Reload grid and units on the map if this is the active session
            const userSid = _getUserSessionId();
            if (sid === userSid) {
                const map = KMap.getMap();
                try { await KGrid.load(map, userSid); } catch(e) {}
                try { await KUnits.load(userSid, _getToken()); } catch(e) {}
            }
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    async function _applyTurnInterval() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-session-status', 'Select a session first', 'error'); return; }
        const minutes = parseInt(document.getElementById('admin-turn-interval').value);
        if (!minutes || minutes < 1) { alert('Invalid interval'); return; }
        const seconds = minutes * 60;  // Convert minutes to seconds for backend
        try {
            await fetch(`/api/admin/sessions/${sid}/tick-interval`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ tick_interval: seconds }),
            });
            _showInfo('admin-session-status', `Turn interval: ${minutes} min`, 'success');
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    // ── Grid Management ─────────────────────────────

    async function _applyGrid() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-grid-status', 'Select a session first', 'error'); return; }
        const lat = parseFloat(document.getElementById('admin-grid-origin-lat').value);
        const lon = parseFloat(document.getElementById('admin-grid-origin-lon').value);
        const cols = Math.max(1, Math.min(20, parseInt(document.getElementById('admin-grid-cols').value) || 8));
        const rows = Math.max(1, Math.min(20, parseInt(document.getElementById('admin-grid-rows').value) || 8));
        const size = Math.max(100, Math.min(10000, parseInt(document.getElementById('admin-grid-size').value) || 1000));
        if (isNaN(lat) || isNaN(lon)) { _showInfo('admin-grid-status', 'Invalid origin coordinates', 'error'); return; }

        const payload = JSON.stringify({
            origin_lat: lat, origin_lon: lon,
            columns: cols, rows: rows,
            base_square_size_m: size,
        });
        const headers = { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` };

        try {
            // Try PUT first, fall back to POST if 405
            let resp = await fetch(`/api/admin/sessions/${sid}/grid`, {
                method: 'PUT', headers, body: payload,
            });
            if (resp.status === 405) {
                resp = await fetch(`/api/admin/sessions/${sid}/grid`, {
                    method: 'POST', headers, body: payload,
                });
            }
            if (resp.ok) {
                _showInfo('admin-grid-status', '✓ Grid updated', 'success');
                // Reload grid on map if this is the active session
                if (sid === _getUserSessionId()) {
                    const map = KMap.getMap();
                    await KGrid.load(map, sid);
                }
            } else {
                const d = await resp.json().catch(() => ({}));
                _showInfo('admin-grid-status', `✗ ${d.detail || resp.status}`, 'error');
            }
        } catch (err) {
            _showInfo('admin-grid-status', `✗ ${err.message}`, 'error');
        }
    }

    /** Load grid settings from the currently selected session's existing grid. */
    async function _loadGridFromSession() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-grid-status', 'Select a session first', 'error'); return; }
        try {
            const resp = await fetch(`/api/sessions/${sid}/grid/meta`);
            if (!resp.ok) {
                _showInfo('admin-grid-status', 'No grid defined for this session', 'error');
                return;
            }
            const meta = await resp.json();
            const latEl = document.getElementById('admin-grid-origin-lat');
            const lonEl = document.getElementById('admin-grid-origin-lon');
            const colsEl = document.getElementById('admin-grid-cols');
            const rowsEl = document.getElementById('admin-grid-rows');
            const sizeEl = document.getElementById('admin-grid-size');
            if (latEl) latEl.value = meta.origin_lat != null ? meta.origin_lat.toFixed(5) : '';
            if (lonEl) lonEl.value = meta.origin_lon != null ? meta.origin_lon.toFixed(5) : '';
            if (colsEl) colsEl.value = meta.columns || 8;
            if (rowsEl) rowsEl.value = meta.rows || 8;
            if (sizeEl) sizeEl.value = meta.base_square_size_m || 1000;
            _showInfo('admin-grid-status', '✓ Loaded from session grid', 'success');
        } catch (err) {
            _showInfo('admin-grid-status', `✗ ${err.message}`, 'error');
        }
    }

    /** Enter map-click pick mode to set grid origin from a map click. */
    function _pickGridFromMap() {
        const map = KMap.getMap();
        if (!map) { _showInfo('admin-grid-status', 'Map not ready', 'error'); return; }
        if (_pickingGridOrigin) return;

        _pickingGridOrigin = true;
        map.getContainer().classList.add('pick-mode-active');

        // Show banner
        const banner = document.createElement('div');
        banner.className = 'pick-mode-banner';
        banner.id = 'pick-mode-banner';
        banner.textContent = '🖱 Click on map to set grid origin — ESC to cancel';
        document.body.appendChild(banner);

        const _cancelPick = () => {
            _pickingGridOrigin = false;
            map.getContainer().classList.remove('pick-mode-active');
            const b = document.getElementById('pick-mode-banner');
            if (b) b.remove();
            map.off('click', _onPickClick);
            document.removeEventListener('keydown', _onPickKey);
        };

        const _onPickClick = (e) => {
            const latEl = document.getElementById('admin-grid-origin-lat');
            const lonEl = document.getElementById('admin-grid-origin-lon');
            if (latEl) latEl.value = e.latlng.lat.toFixed(5);
            if (lonEl) lonEl.value = e.latlng.lng.toFixed(5);
            _showInfo('admin-grid-status', `✓ Origin set: ${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`, 'success');
            _cancelPick();
        };

        const _onPickKey = (e) => {
            if (e.key === 'Escape') {
                _cancelPick();
                _showInfo('admin-grid-status', 'Pick cancelled');
            }
        };

        map.once('click', _onPickClick);
        document.addEventListener('keydown', _onPickKey, { once: true });

        _showInfo('admin-grid-status', 'Click on map to set origin…');
    }

    // ── Participants ─────────────────────────────────

    async function _loadParticipants() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-participants-list', 'Select a session first'); return; }
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/participants`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const participants = await resp.json();
            const el = document.getElementById('admin-participants-list');
            if (!el) return;

            if (participants.length === 0) {
                el.innerHTML = '<div class="admin-info">No participants</div>';
                return;
            }

            let html = '';
            participants.forEach(p => {
                const sideColor = p.side === 'blue' ? '#4fc3f7' : p.side === 'red' ? '#ef5350' : '#aaa';
                html += `<div class="admin-item" style="border-left:3px solid ${sideColor};">
                    <div>
                        <b>${p.display_name}</b>
                        <span class="admin-item-meta">${p.side} / ${p.role}</span>
                    </div>
                    <button class="admin-btn admin-btn-danger" onclick="KAdmin.kickParticipant('${p.id}')" style="padding:2px 6px;font-size:10px;" title="Remove participant from session">Kick</button>
                </div>`;
            });
            el.innerHTML = html;
        } catch (err) {
            _showInfo('admin-participants-list', `✗ ${err.message}`, 'error');
        }
    }

    async function kickParticipant(participantId) {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;
        if (!confirm('Kick this participant?')) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/participants/${participantId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                _loadParticipants();
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Kick failed');
            }
        } catch (err) { alert(err.message); }
    }

    // ── Event Injection ──────────────────────────────

    async function _injectEvent() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-event-status', 'Select a session first', 'error'); return; }
        const text = document.getElementById('admin-event-text').value.trim();
        const type = document.getElementById('admin-event-type').value || 'custom';
        if (!text) { alert('Enter event text'); return; }
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/events`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ event_type: type, text_summary: text, visibility: 'all' }),
            });
            if (resp.ok) {
                document.getElementById('admin-event-text').value = '';
                _showInfo('admin-event-status', '✓ Event injected', 'success');
            }
        } catch (err) {
            _showInfo('admin-event-status', `✗ ${err.message}`, 'error');
        }
    }

    // ══════════════════════════════════════════════════
    // ── Monitor: God View ────────────────────────────
    // ══════════════════════════════════════════════════

    async function _toggleGodView() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-god-status', 'Select a session first', 'error'); return; }

        _godViewEnabled = !_godViewEnabled;
        const btn = document.getElementById('admin-god-view-toggle');
        if (btn) {
            btn.textContent = _godViewEnabled ? '👁 God View ON' : '👁 God View OFF';
            btn.classList.toggle('admin-btn-active', _godViewEnabled);
        }

        if (_godViewEnabled) {
            await _refreshGodView();
        } else {
            // Reload normal fog-of-war view — both units and contacts
            const userSid = _getUserSessionId();
            if (userSid) {
                await KUnits.load(userSid, token);
                await KContacts.load(userSid, token);
            }
            _showInfo('admin-god-status', 'Normal view restored (blue fog-of-war)');
        }
    }

    /** Fetch and render all units (god view). Called on toggle and on state_update. */
    async function _refreshGodView() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const units = await resp.json();
            KUnits.render(units);
            _showInfo('admin-god-status', `Showing all ${units.length} units`, 'success');
        } catch (err) {
            _showInfo('admin-god-status', `✗ ${err.message}`, 'error');
        }
    }

    function isGodViewEnabled() { return _godViewEnabled; }

    /** Called by app.js when a state_update arrives via WebSocket.
     *  If god view is on, re-fetch admin units instead of using fog-of-war data. */
    async function onStateUpdate(data) {
        if (_godViewEnabled) {
            await _refreshGodView();
        }
    }

    // ── Unit Dashboard ───────────────────────────────

    async function _loadUnitDashboard() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-unit-dashboard', 'Select a session first'); return; }

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const units = await resp.json();
            const el = document.getElementById('admin-unit-dashboard');
            if (!el) return;

            if (units.length === 0) {
                el.innerHTML = '<div class="admin-info">No units</div>';
                return;
            }

            let html = '<table class="admin-dashboard-table"><tr><th>Unit</th><th>Side</th><th>Str</th><th>Mor</th><th>Ammo</th><th>Comms</th></tr>';
            units.forEach(u => {
                const sideClr = u.side === 'blue' ? '#4fc3f7' : '#ef5350';
                const strPct = u.strength != null ? (u.strength * 100).toFixed(0) : '?';
                const morPct = u.morale != null ? (u.morale * 100).toFixed(0) : '?';
                const ammPct = u.ammo != null ? (u.ammo * 100).toFixed(0) : '?';
                const strClr = u.strength > 0.6 ? '#4caf50' : u.strength > 0.3 ? '#ff9800' : '#f44336';

                html += `<tr>
                    <td style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${u.name}">${u.name}</td>
                    <td style="color:${sideClr};font-weight:700;">${u.side}</td>
                    <td><span style="color:${strClr}">${strPct}%</span></td>
                    <td>${morPct}%</td>
                    <td>${ammPct}%</td>
                    <td style="font-size:10px;">${u.comms_status || '—'}</td>
                </tr>`;
            });
            html += '</table>';
            el.innerHTML = html;
        } catch (err) {
            _showInfo('admin-unit-dashboard', `✗ ${err.message}`, 'error');
        }
    }

    // ── All Orders ───────────────────────────────────

    async function _loadAllOrders() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-orders-list', 'Select a session first'); return; }

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/orders`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const orders = await resp.json();
            const el = document.getElementById('admin-orders-list');
            if (!el) return;

            if (orders.length === 0) {
                el.innerHTML = '<div class="admin-info">No orders</div>';
                return;
            }

            let html = '';
            orders.forEach(o => {
                const sideClr = o.issued_by_side === 'blue' ? '#4fc3f7' : '#ef5350';
                html += `<div class="admin-item" style="border-left:3px solid ${sideClr};">
                    <div style="flex:1;min-width:0;">
                        <div style="font-size:11px;color:#aaa;">[${o.status || '?'}] ${o.order_type || ''}</div>
                        <div style="font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${o.original_text || '—'}</div>
                    </div>
                </div>`;
            });
            el.innerHTML = html;
        } catch (err) {
            _showInfo('admin-orders-list', `✗ ${err.message}`, 'error');
        }
    }

    // ── DB Stats ─────────────────────────────────────

    async function _loadDbStats() {
        const token = _getToken();
        if (!token) { _showInfo('admin-db-info', 'Not logged in', 'error'); return; }
        try {
            const resp = await fetch('/api/admin/stats', {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                const stats = await resp.json();
                let html = '<table class="admin-stats-table">';
                for (const [table, count] of Object.entries(stats)) {
                    html += `<tr><td>${table}</td><td>${count}</td></tr>`;
                }
                html += '</table>';
                document.getElementById('admin-db-info').innerHTML = html;
            } else {
                _showInfo('admin-db-info', 'Stats endpoint not available');
            }
        } catch (err) {
            _showInfo('admin-db-info', `✗ ${err.message}`, 'error');
        }
    }

    // ══════════════════════════════════════════════════
    // ── User Management ─────────────────────────────
    // ══════════════════════════════════════════════════

    async function _loadUsers() {
        try {
            const resp = await fetch('/api/admin/users');
            if (!resp.ok) { _showInfo('admin-users-list', 'Failed to load', 'error'); return; }
            const users = await resp.json();
            const el = document.getElementById('admin-users-list');
            if (!el) return;

            // Reset select-all
            const selectAllCb = document.getElementById('admin-users-select-all');
            if (selectAllCb) selectAllCb.checked = false;

            if (users.length === 0) {
                el.innerHTML = '<div class="admin-info">No registered users</div>';
                return;
            }

            let html = '';
            users.forEach(u => {
                const created = u.created_at ? new Date(u.created_at).toLocaleDateString() : '';
                html += `<div class="admin-item">
                    <div style="display:flex;align-items:center;gap:6px;flex:1;min-width:0;">
                        <input type="checkbox" class="admin-user-cb" data-user-id="${u.id}" style="cursor:pointer;flex-shrink:0;" />
                        <div style="min-width:0;">
                            <b title="User ID: ${u.id}">${u.display_name}</b>
                            <span class="admin-item-meta" title="Registered on ${created}">${created}</span>
                        </div>
                    </div>
                    <div style="display:flex;gap:3px;">
                        <button class="admin-btn" onclick="KAdmin.assignUserToSession('${u.id}','${u.display_name.replace(/'/g, "\\'")}')" style="padding:2px 6px;font-size:10px;" title="Assign this user to the selected session">🎯</button>
                        <button class="admin-btn" onclick="KAdmin.renameUser('${u.id}','${u.display_name.replace(/'/g, "\\'")}')" style="padding:2px 6px;font-size:10px;" title="Rename this user">✏</button>
                        <button class="admin-btn admin-btn-danger" onclick="KAdmin.deleteUser('${u.id}','${u.display_name.replace(/'/g, "\\'")}')" style="padding:2px 6px;font-size:10px;" title="Delete this user">✕</button>
                    </div>
                </div>`;
            });
            el.innerHTML = html;
        } catch (err) {
            _showInfo('admin-users-list', `✗ ${err.message}`, 'error');
        }
    }

    async function _bulkDeleteUsers() {
        const checkboxes = document.querySelectorAll('.admin-user-cb:checked');
        if (checkboxes.length === 0) { alert('No users selected'); return; }
        if (!confirm(`⚠ Delete ${checkboxes.length} selected user(s)?`)) return;

        const userIds = Array.from(checkboxes).map(cb => cb.dataset.userId);
        try {
            const resp = await fetch('/api/admin/users/bulk-delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_ids: userIds }),
            });
            if (resp.ok) {
                const data = await resp.json();
                _showInfo('admin-users-list', `✓ Deleted ${data.deleted} user(s)`, 'success');
                setTimeout(_loadUsers, 500);
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Bulk delete failed');
            }
        } catch (err) { alert(err.message); }
    }

    async function _addUser() {
        const nameEl = document.getElementById('admin-add-user-name');
        if (!nameEl) return;
        const name = nameEl.value.trim();
        if (!name) { alert('Enter a display name'); return; }

        try {
            const resp = await fetch('/api/admin/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: name }),
            });
            if (resp.ok) {
                nameEl.value = '';
                _loadUsers();
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Failed');
            }
        } catch (err) { alert(err.message); }
    }

    async function renameUser(userId, currentName) {
        const newName = prompt('New display name:', currentName);
        if (!newName || newName.trim() === currentName) return;
        try {
            await fetch(`/api/admin/users/${userId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: newName.trim() }),
            });
            _loadUsers();
        } catch (err) { alert(err.message); }
    }

    async function deleteUser(userId, name) {
        if (!confirm(`Delete user "${name}"? This will also remove them from all sessions.`)) return;
        const token = _getToken();
        try {
            const resp = await fetch(`/api/admin/users/${userId}`, {
                method: 'DELETE',
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (resp.ok || resp.status === 204) {
                _loadUsers();
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Delete failed');
            }
        } catch (err) { alert(err.message); }
    }

    // ── Assign User to Session (from Users panel) ────

    let _assignPendingUserId = null;
    let _assignPendingDisplayName = null;

    function assignUserToSession(userId, displayName) {
        const sid = _getAdminSessionId();
        if (!sid) { alert('Select a session first in the admin session selector.'); return; }

        _assignPendingUserId = userId;
        _assignPendingDisplayName = displayName;

        // Populate modal
        const label = document.getElementById('admin-assign-user-label');
        if (label) label.textContent = `Assign "${displayName}" to session ${sid.substring(0, 8)}…`;

        // Reset to defaults
        const sideEl = document.getElementById('admin-assign-side');
        const roleEl = document.getElementById('admin-assign-role');
        if (sideEl) sideEl.value = 'blue';
        if (roleEl) roleEl.value = 'commander';

        const statusEl = document.getElementById('admin-assign-status');
        if (statusEl) statusEl.textContent = '';

        // Show modal
        const modal = document.getElementById('admin-assign-modal');
        if (modal) modal.style.display = 'flex';
    }

    function _initAssignModal() {
        _bind('admin-assign-confirm', 'click', _doAssignUser);
        _bind('admin-assign-cancel', 'click', _closeAssignModal);
        _bind('admin-assign-modal-close', 'click', _closeAssignModal);

        // Close on overlay click
        const overlay = document.getElementById('admin-assign-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) _closeAssignModal();
            });
        }
    }

    function _closeAssignModal() {
        const modal = document.getElementById('admin-assign-modal');
        if (modal) modal.style.display = 'none';
        _assignPendingUserId = null;
        _assignPendingDisplayName = null;
    }

    async function _doAssignUser() {
        if (!_assignPendingUserId) return;
        const sid = _getAdminSessionId();
        const token = _getToken();
        if (!sid || !token) return;

        const side = document.getElementById('admin-assign-side').value;
        const role = document.getElementById('admin-assign-role').value;
        const statusEl = document.getElementById('admin-assign-status');

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/add-participant`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ user_id: _assignPendingUserId, side, role }),
            });
            if (resp.ok) {
                if (statusEl) { statusEl.textContent = `✓ ${_assignPendingDisplayName} added as ${side} / ${role}`; statusEl.className = 'admin-info admin-success'; }
                _loadParticipants();
                setTimeout(_closeAssignModal, 800);
            } else {
                const d = await resp.json().catch(() => ({}));
                if (statusEl) { statusEl.textContent = `✗ ${d.detail || 'Failed'}`; statusEl.className = 'admin-info admin-error'; }
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'admin-info admin-error'; }
        }
    }

    // ══════════════════════════════════════════════════
    // ── Chain of Command (CoC) – Admin Sub-Tab ──────
    // ══════════════════════════════════════════════════

    async function _loadChainOfCommand() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-coc-tree', 'Select a session first'); return; }

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/unit-hierarchy`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) { _showInfo('admin-coc-tree', 'Failed to load', 'error'); return; }
            const units = await resp.json();
            _renderCoCTree(units, 'admin-coc-tree', true, units);
        } catch (err) {
            _showInfo('admin-coc-tree', `✗ ${err.message}`, 'error');
        }
    }

    // ══════════════════════════════════════════════════
    // ── Chain of Command – Public Tab ────────────────
    // ══════════════════════════════════════════════════

    /** Cached participants for the current session (for CoC user picker). */
    let _cachedParticipants = [];

    async function _loadParticipantsForCoC(sessionId) {
        const token = _getToken();
        if (!token || !sessionId) return [];
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/participants`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                _cachedParticipants = await resp.json();
                return _cachedParticipants;
            }
        } catch (err) {
            console.warn('Load participants for CoC:', err);
        }
        return [];
    }

    async function loadPublicCoC() {
        const token = _getToken(), sid = _getUserSessionId();
        if (!token || !sid) {
            const el = document.getElementById('coc-tree-public');
            if (el) el.innerHTML = '<div class="admin-info">Join a session first</div>';
            return;
        }

        try {
            // Use the public hierarchy endpoint (works for any participant)
            const resp = await fetch(`/api/sessions/${sid}/units/hierarchy`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) {
                // Fallback to admin endpoint if available
                const adminResp = await fetch(`/api/admin/sessions/${sid}/unit-hierarchy`, {
                    headers: { 'Authorization': `Bearer ${token}` },
                });
                if (!adminResp.ok) {
                    const el = document.getElementById('coc-tree-public');
                    if (el) el.innerHTML = '<div class="admin-info">Could not load hierarchy</div>';
                    return;
                }
                const allUnits = await adminResp.json();
                const visibleUnits = _adminUnlocked ? allUnits : allUnits.filter(u => u.side === 'blue');
                _renderCoCTree(visibleUnits, 'coc-tree-public', true, allUnits);
                return;
            }
            const units = await resp.json();

            // Load participants for the user-assign modal
            await _loadParticipantsForCoC(sid);

            // Editable if user has any assigned units (commander) or is admin
            _renderCoCTree(units, 'coc-tree-public', true, units);
        } catch (err) {
            const el = document.getElementById('coc-tree-public');
            if (el) el.innerHTML = `<div class="admin-info admin-error">✗ ${err.message}</div>`;
        }
    }

    // ══════════════════════════════════════════════════
    // ── CoC Tree Rendering (shared) ─────────────────
    // ══════════════════════════════════════════════════

    /**
     * Check if the current user has command authority over a unit.
     * User can manage a unit if:
     *   1. They are directly assigned to a proper ancestor (they command the parent unit).
     *   2. They are directly assigned to this unit (they can reassign it).
     * They should NOT be able to manage units above them in the hierarchy.
     */
    function _userCanAssign(unit, allUnitMap) {
        const userId = KSessionUI.getUserId();
        if (!userId) return false;

        // Case 1: User is directly assigned to this unit (can manage/reassign)
        if (unit.assigned_user_ids && unit.assigned_user_ids.includes(userId)) {
            return true;
        }

        // Case 2: User is assigned to a proper ancestor (has downward authority)
        let parentId = unit.parent_unit_id;
        const visited = new Set();
        while (parentId && allUnitMap[parentId]) {
            if (visited.has(parentId)) break;
            visited.add(parentId);

            const parent = allUnitMap[parentId];
            if (parent.assigned_user_ids && parent.assigned_user_ids.includes(userId)) {
                return true;
            }
            parentId = parent.parent_unit_id;
        }
        return false;
    }

    function _renderCoCTree(units, targetElId, editable, allUnits) {
        const el = document.getElementById(targetElId);
        if (!el) return;

        if (units.length === 0) {
            el.innerHTML = '<div class="admin-info">No units in session</div>';
            return;
        }

        // Build a lookup map from ALL units (including ones not in 'units')
        // so we can resolve parent names even if parent is filtered out
        const allUnitMap = {};
        (allUnits || units).forEach(u => { allUnitMap[u.id] = u; });

        // Build tree structure from the visible units
        const unitMap = {};
        units.forEach(u => { unitMap[u.id] = { ...u, children: [] }; });

        const roots = [];
        units.forEach(u => {
            if (u.parent_unit_id && unitMap[u.parent_unit_id]) {
                unitMap[u.parent_unit_id].children.push(unitMap[u.id]);
            } else {
                roots.push(unitMap[u.id]);
            }
        });

        // Sort: blue first, then red, then by name
        const sortFn = (a, b) => {
            if (a.side !== b.side) return a.side === 'blue' ? -1 : 1;
            return (a.name || '').localeCompare(b.name || '');
        };
        roots.sort(sortFn);
        Object.values(unitMap).forEach(u => u.children.sort(sortFn));

        // Render tree
        let html = '<div class="coc-tree">';

        const blueRoots = roots.filter(u => u.side === 'blue');
        const redRoots = roots.filter(u => u.side === 'red');

        if (blueRoots.length > 0) {
            html += '<div class="coc-side-header" style="color:#4fc3f7;">BLUE FORCE</div>';
            blueRoots.forEach(u => { html += _renderCoCNode(u, 0, units, editable, allUnitMap); });
        }
        if (redRoots.length > 0) {
            html += '<div class="coc-side-header" style="color:#ef5350;margin-top:8px;">RED FORCE</div>';
            redRoots.forEach(u => { html += _renderCoCNode(u, 0, units, editable, allUnitMap); });
        }

        html += '</div>';
        el.innerHTML = html;

        // Bind admin assign/unassign (hierarchy structure) buttons
        if (editable) {
            el.querySelectorAll('.coc-assign-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const unitId = btn.dataset.unitId;
                    _showParentPicker(unitId, units);
                });
            });

            el.querySelectorAll('.coc-unassign-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const unitId = btn.dataset.unitId;
                    _setUnitParent(unitId, null);
                });
            });
        }

        // Bind user-assignment buttons (assign commander to unit)
        el.querySelectorAll('.coc-user-assign-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const unitId = btn.dataset.unitId;
                const unit = allUnitMap[unitId];
                if (unit) _showUserAssignModal(unit);
            });
        });
    }

    function _renderCoCNode(unit, depth, allUnits, editable, allUnitMap) {
        const indent = depth * 20;
        const sideColor = unit.side === 'blue' ? '#4fc3f7' : '#ef5350';
        const strPct = unit.strength != null ? (unit.strength * 100).toFixed(0) + '%' : '?';
        const strClr = unit.strength > 0.6 ? '#4caf50' : unit.strength > 0.3 ? '#ff9800' : '#f44336';
        const hasChildren = unit.children && unit.children.length > 0;
        const expandIcon = hasChildren ? '▼' : '·';

        // Find assigned users for this unit
        let userBadge = '';
        if (unit.assigned_user_names && unit.assigned_user_names.length > 0) {
            const names = unit.assigned_user_names.join(', ');
            userBadge = `<span class="coc-user-badge" title="Assigned to: ${names}">⭐ ${names}</span>`;
        }

        // Commanding officer info (from hierarchy)
        let cmdInfo = '';
        if (unit.commanding_user_name) {
            const isSelfCO = unit.assigned_user_names
                && unit.assigned_user_names.includes(unit.commanding_user_name);
            if (!isSelfCO) {
                // CO is inherited from parent chain
                cmdInfo = `<span class="coc-cmd" title="CO: ${unit.commanding_user_name}">⬆ ${unit.commanding_user_name}</span>`;
            }
        }

        // Build tooltip with rich info
        let tooltipParts = [unit.unit_type, `Str: ${strPct}`];
        if (unit.commanding_user_name) tooltipParts.push(`CO: ${unit.commanding_user_name}`);
        if (unit.parent_unit_id && allUnitMap && allUnitMap[unit.parent_unit_id]) {
            tooltipParts.push(`Parent: ${allUnitMap[unit.parent_unit_id].name}`);
        }
        const tooltip = tooltipParts.join(' — ');

        let html = `<div class="coc-node" style="margin-left:${indent}px;" title="${tooltip}">
            <span class="coc-expand">${expandIcon}</span>
            <span class="coc-connector" style="background:${sideColor};"></span>
            <span class="coc-name" style="color:#e0e0e0;">${unit.name}</span>
            ${userBadge}
            ${cmdInfo}
            <span class="coc-type" title="${unit.unit_type}">${unit.unit_type}</span>
            <span class="coc-str" style="color:${strClr};" title="Strength">${strPct}</span>`;

        // User-assign button: shown if current user can manage this unit
        const canUserAssign = _adminUnlocked || _userCanAssign(unit, allUnitMap);
        if (canUserAssign) {
            html += `<button class="coc-user-assign-btn coc-assign-btn" data-unit-id="${unit.id}" title="Assign a commander to this unit" style="color:#81c784;">👤</button>`;
        }

        // Admin-only: parent hierarchy buttons
        if (editable && _adminUnlocked) {
            html += `<button class="coc-assign-btn" data-unit-id="${unit.id}" title="Assign to a parent commander">⬆</button>`;
            if (unit.parent_unit_id) {
                html += `<button class="coc-unassign-btn" data-unit-id="${unit.id}" title="Remove from parent chain of command">✕</button>`;
            }
        }

        html += `</div>`;

        if (hasChildren) {
            unit.children.forEach(child => {
                html += _renderCoCNode(child, depth + 1, allUnits, editable, allUnitMap);
            });
        }

        return html;
    }

    let _cocPickerPendingUnitId = null;

    function _showParentPicker(unitId, allUnits) {
        const unit = allUnits.find(u => u.id === unitId);
        if (!unit) return;

        const candidates = allUnits.filter(u =>
            u.id !== unitId && u.side === unit.side
        );

        if (candidates.length === 0) {
            alert('No available parent units');
            return;
        }

        _cocPickerPendingUnitId = unitId;

        // Populate modal
        const label = document.getElementById('admin-coc-picker-unit-label');
        if (label) label.textContent = `Assign "${unit.name}" to which commander?`;

        const sel = document.getElementById('admin-coc-picker-select');
        if (sel) {
            sel.innerHTML = '';
            candidates.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                const sideIcon = c.side === 'blue' ? '🔵' : '🔴';
                opt.textContent = `${sideIcon} ${c.name} (${c.unit_type})`;
                sel.appendChild(opt);
            });
            if (candidates.length > 0) sel.value = candidates[0].id;
        }

        // Show modal
        const modal = document.getElementById('admin-coc-picker-modal');
        if (modal) modal.style.display = 'flex';
    }

    function _initCocPickerModal() {
        _bind('admin-coc-picker-confirm', 'click', () => {
            const sel = document.getElementById('admin-coc-picker-select');
            if (!sel || !sel.value || !_cocPickerPendingUnitId) return;
            _setUnitParent(_cocPickerPendingUnitId, sel.value);
            _closeCocPickerModal();
        });
        _bind('admin-coc-picker-cancel', 'click', _closeCocPickerModal);
        _bind('admin-coc-picker-close', 'click', _closeCocPickerModal);

        // Close on overlay click
        const overlay = document.getElementById('admin-coc-picker-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) _closeCocPickerModal();
            });
        }

        // Double-click to confirm
        const sel = document.getElementById('admin-coc-picker-select');
        if (sel) {
            sel.addEventListener('dblclick', () => {
                if (sel.value && _cocPickerPendingUnitId) {
                    _setUnitParent(_cocPickerPendingUnitId, sel.value);
                    _closeCocPickerModal();
                }
            });
        }
    }

    function _closeCocPickerModal() {
        const modal = document.getElementById('admin-coc-picker-modal');
        if (modal) modal.style.display = 'none';
        _cocPickerPendingUnitId = null;
    }

    // ══════════════════════════════════════════════════
    // ── CoC User Assignment Modal (for commanders) ───
    // ══════════════════════════════════════════════════

    let _userAssignPendingUnit = null;

    function _showUserAssignModal(unit) {
        _userAssignPendingUnit = unit;

        const label = document.getElementById('coc-user-assign-unit-label');
        if (label) label.textContent = `Assign commander for "${unit.name}" (${unit.unit_type})`;

        const statusEl = document.getElementById('coc-user-assign-status');
        if (statusEl) statusEl.textContent = '';

        // Populate participant select — filter to same side
        const sel = document.getElementById('coc-user-assign-select');
        if (sel) {
            sel.innerHTML = '';
            const sameSideParticipants = _cachedParticipants.filter(
                p => p.side === unit.side || p.side === 'admin' || p.side === 'observer'
            );
            if (sameSideParticipants.length === 0) {
                const opt = document.createElement('option');
                opt.textContent = '(no participants available)';
                opt.disabled = true;
                sel.appendChild(opt);
            } else {
                sameSideParticipants.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.user_id;
                    const sideIcon = p.side === 'blue' ? '🔵' : p.side === 'red' ? '🔴' : '👁';
                    const currentMark = (unit.assigned_user_ids && unit.assigned_user_ids.includes(p.user_id))
                        ? ' ⭐' : '';
                    opt.textContent = `${sideIcon} ${p.display_name} (${p.role})${currentMark}`;
                    sel.appendChild(opt);
                });
                // Pre-select the currently assigned user if any
                if (unit.assigned_user_ids && unit.assigned_user_ids.length > 0) {
                    sel.value = unit.assigned_user_ids[0];
                }
            }
        }

        // Show modal
        const modal = document.getElementById('coc-user-assign-modal');
        if (modal) modal.style.display = 'flex';
    }

    function _closeCocUserAssignModal() {
        const modal = document.getElementById('coc-user-assign-modal');
        if (modal) modal.style.display = 'none';
        _userAssignPendingUnit = null;
    }

    function _initCocUserAssignModal() {
        _bind('coc-user-assign-confirm', 'click', _doCocUserAssign);
        _bind('coc-user-assign-unassign', 'click', _doCocUserUnassign);
        _bind('coc-user-assign-cancel', 'click', _closeCocUserAssignModal);
        _bind('coc-user-assign-close', 'click', _closeCocUserAssignModal);

        // Close on overlay click
        const overlay = document.getElementById('coc-user-assign-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) _closeCocUserAssignModal();
            });
        }

        // Double-click to confirm
        const sel = document.getElementById('coc-user-assign-select');
        if (sel) {
            sel.addEventListener('dblclick', () => {
                if (sel.value && _userAssignPendingUnit) {
                    _doCocUserAssign();
                }
            });
        }
    }

    async function _doCocUserAssign() {
        if (!_userAssignPendingUnit) return;
        const sel = document.getElementById('coc-user-assign-select');
        const statusEl = document.getElementById('coc-user-assign-status');
        if (!sel || !sel.value) {
            if (statusEl) { statusEl.textContent = 'Select a participant'; statusEl.className = 'admin-info admin-error'; }
            return;
        }

        const token = _getToken();
        const sid = _getUserSessionId() || _getAdminSessionId();
        if (!token || !sid) return;

        const unitId = _userAssignPendingUnit.id;
        const userId = sel.value;

        try {
            const resp = await fetch(`/api/sessions/${sid}/units/${unitId}/assign`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ assigned_user_ids: [userId] }),
            });
            if (resp.ok) {
                const participant = _cachedParticipants.find(p => p.user_id === userId);
                const name = participant ? participant.display_name : userId.substring(0, 8);
                if (statusEl) { statusEl.textContent = `✓ ${name} assigned as commander`; statusEl.className = 'admin-info admin-success'; }
                // Refresh CoC trees after a brief delay
                setTimeout(() => {
                    _closeCocUserAssignModal();
                    loadPublicCoC();
                    if (_adminUnlocked) _loadChainOfCommand();
                }, 600);
            } else {
                const d = await resp.json().catch(() => ({}));
                if (statusEl) { statusEl.textContent = `✗ ${d.detail || 'Assignment failed'}`; statusEl.className = 'admin-info admin-error'; }
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'admin-info admin-error'; }
        }
    }

    async function _doCocUserUnassign() {
        if (!_userAssignPendingUnit) return;
        const statusEl = document.getElementById('coc-user-assign-status');

        const token = _getToken();
        const sid = _getUserSessionId() || _getAdminSessionId();
        if (!token || !sid) return;

        const unitId = _userAssignPendingUnit.id;

        try {
            const resp = await fetch(`/api/sessions/${sid}/units/${unitId}/assign`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ assigned_user_ids: [] }),
            });
            if (resp.ok) {
                if (statusEl) { statusEl.textContent = '✓ Unit unassigned'; statusEl.className = 'admin-info admin-success'; }
                setTimeout(() => {
                    _closeCocUserAssignModal();
                    loadPublicCoC();
                    if (_adminUnlocked) _loadChainOfCommand();
                }, 600);
            } else {
                const d = await resp.json().catch(() => ({}));
                if (statusEl) { statusEl.textContent = `✗ ${d.detail || 'Failed'}`; statusEl.className = 'admin-info admin-error'; }
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'admin-info admin-error'; }
        }
    }

    async function _setUnitParent(unitId, parentId) {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units/${unitId}/parent`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ parent_unit_id: parentId }),
            });
            if (resp.ok) {
                _loadChainOfCommand();
                loadPublicCoC();
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Failed');
            }
        } catch (err) { alert(err.message); }
    }

    // ── Session Context Update ───────────────────────

    function updateSessionContext() {
        const sid = _getUserSessionId();
        const statusEl = document.getElementById('admin-session-status');
        if (statusEl) {
            statusEl.textContent = sid ? `Active: ${sid.substring(0, 8)}...` : 'No active session';
        }
        // Auto-select in admin dropdown if no selection
        if (sid && !_adminSelectedSessionId) {
            _adminSelectedSessionId = sid;
            const sel = document.getElementById('admin-session-selector');
            if (sel) sel.value = sid;
        }
        // Refresh admin sessions list
        if (_adminUnlocked) _loadAdminSessions();
    }

    // ── Helpers ──────────────────────────────────────

    function _showInfo(elementId, message, type = '') {
        const el = document.getElementById(elementId);
        if (!el) return;
        el.textContent = message;
        el.className = 'admin-info';
        if (type === 'success') el.classList.add('admin-success');
        if (type === 'error') el.classList.add('admin-error');
    }

    /** Reset admin state on logout — re-lock admin, close window, clear god view. */
    function resetOnLogout() {
        _adminUnlocked = false;
        _godViewEnabled = false;
        _adminSelectedSessionId = null;

        // Re-lock admin panel
        const gate = document.getElementById('admin-lock-gate');
        const content = document.getElementById('admin-content');
        if (gate) gate.style.display = 'block';
        if (content) content.style.display = 'none';

        // Clear password input
        const pw = document.getElementById('admin-pw-input');
        if (pw) pw.value = '';

        // Reset god view button
        const godBtn = document.getElementById('admin-god-view-toggle');
        if (godBtn) {
            godBtn.textContent = '👁 God View OFF';
            godBtn.classList.remove('admin-btn-active');
        }

        // Close admin window
        const win = document.getElementById('admin-window');
        if (win) win.style.display = 'none';
    }

    return {
        init, updateSessionContext, refreshScenarioList, isUnlocked, isGodViewEnabled,
        onStateUpdate, resetOnLogout,
        editScenario, deleteScenario, kickParticipant,
        renameUser, deleteUser, assignUserToSession,
        loadPublicCoC,
    };
})();
