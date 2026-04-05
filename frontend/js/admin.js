/**
 * admin.js – Full admin/game-master panel.
 *
 * Sub-tabs: Builder | Session | Monitor
 *   Builder  – scenario builder toggle, scenario list with edit
 *   Session  – participants, tick controls, reset, event injection
 *   Monitor  – god-view toggle, unit dashboard, all orders
 */
const KAdmin = (() => {

    let _godViewEnabled = false;

    function init() {
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
        _bind('admin-apply-tick-interval', 'click', _applyTickInterval);
        _bind('admin-load-participants', 'click', _loadParticipants);
        _bind('admin-inject-event', 'click', _injectEvent);

        // ── Monitor sub-tab ─────────────────────────
        _bind('admin-god-view-toggle', 'click', _toggleGodView);
        _bind('admin-load-dashboard', 'click', _loadUnitDashboard);
        _bind('admin-load-orders', 'click', _loadAllOrders);
        _bind('admin-db-stats', 'click', _loadDbStats);
    }

    function _bind(id, evt, fn) {
        const el = document.getElementById(id);
        if (el) el.addEventListener(evt, fn);
    }

    function _getToken() { return KSessionUI.getToken(); }
    function _getSessionId() { return KSessionUI.getSessionId(); }

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
                        <button class="admin-btn" onclick="KAdmin.editScenario('${s.id}')" style="padding:2px 8px;font-size:10px;">✏ Edit</button>
                        <button class="admin-btn admin-btn-danger" onclick="KAdmin.deleteScenario('${s.id}')" style="padding:2px 8px;font-size:10px;">✕</button>
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
                KWebSocket.disconnect();
                document.getElementById('session-info').textContent = '';
                const startBtn = document.getElementById('start-session-btn');
                const tickBtn = document.getElementById('tick-btn');
                if (startBtn) startBtn.style.display = 'none';
                if (tickBtn) tickBtn.style.display = 'none';
                KSessionUI.loadSessions();
                KGameLog.addEntry('All sessions deleted (admin)', 'info');
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
        } catch (err) {
            _showInfo('admin-session-count', `✗ ${err.message}`, 'error');
        }
    }

    async function _pauseSession() {
        const token = _getToken(), sid = _getSessionId();
        if (!token || !sid) return;
        try {
            const resp = await fetch(`/api/sessions/${sid}/pause`, {
                method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
            });
            const data = await resp.json();
            _showInfo('admin-session-status', `Paused at tick ${data.tick}`, 'success');
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    async function _resetSession() {
        const token = _getToken(), sid = _getSessionId();
        if (!token || !sid) return;
        if (!confirm('⚠ Reset session to tick 0? All progress will be lost.')) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/reset`, {
                method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
            });
            const data = await resp.json();
            _showInfo('admin-session-status', `✓ ${data.message}`, 'success');
            KGameLog.addEntry('Session reset to tick 0 (admin)', 'info');
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    async function _applyTickInterval() {
        const token = _getToken(), sid = _getSessionId();
        if (!token || !sid) return;
        const val = parseInt(document.getElementById('admin-tick-interval').value);
        if (!val || val < 1) { alert('Invalid interval'); return; }
        try {
            await fetch(`/api/admin/sessions/${sid}/tick-interval`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ tick_interval: val }),
            });
            _showInfo('admin-session-status', `Tick interval: ${val}s`, 'success');
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    // ── Participants ─────────────────────────────────

    async function _loadParticipants() {
        const token = _getToken(), sid = _getSessionId();
        if (!token || !sid) { _showInfo('admin-participants-list', 'No session'); return; }
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
                    <button class="admin-btn admin-btn-danger" onclick="KAdmin.kickParticipant('${p.id}')" style="padding:2px 6px;font-size:10px;">Kick</button>
                </div>`;
            });
            el.innerHTML = html;
        } catch (err) {
            _showInfo('admin-participants-list', `✗ ${err.message}`, 'error');
        }
    }

    async function kickParticipant(participantId) {
        const token = _getToken(), sid = _getSessionId();
        if (!token || !sid) return;
        if (!confirm('Kick this participant?')) return;
        try {
            await fetch(`/api/admin/sessions/${sid}/participants/${participantId}`, {
                method: 'DELETE', headers: { 'Authorization': `Bearer ${token}` },
            });
            _loadParticipants();
        } catch (err) { alert(err.message); }
    }

    // ── Event Injection ──────────────────────────────

    async function _injectEvent() {
        const token = _getToken(), sid = _getSessionId();
        if (!token || !sid) return;
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
        const token = _getToken(), sid = _getSessionId();
        if (!token || !sid) return;

        _godViewEnabled = !_godViewEnabled;
        const btn = document.getElementById('admin-god-view-toggle');
        if (btn) {
            btn.textContent = _godViewEnabled ? '👁 God View ON' : '👁 God View OFF';
            btn.classList.toggle('admin-btn-active', _godViewEnabled);
        }

        if (_godViewEnabled) {
            // Load all units (admin endpoint)
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
        } else {
            // Reload normal fog-of-war view
            await KUnits.load(sid, token);
            _showInfo('admin-god-status', 'Normal view restored');
        }
    }

    // ── Unit Dashboard ───────────────────────────────

    async function _loadUnitDashboard() {
        const token = _getToken(), sid = _getSessionId();
        if (!token || !sid) return;

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
        const token = _getToken(), sid = _getSessionId();
        if (!token || !sid) return;

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

    // ── Session Context Update ───────────────────────

    function updateSessionContext() {
        const sid = _getSessionId();
        const statusEl = document.getElementById('admin-session-status');
        if (statusEl) {
            statusEl.textContent = sid ? `Active: ${sid.substring(0, 8)}...` : 'No active session';
        }
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

    return {
        init, updateSessionContext, refreshScenarioList,
        editScenario, deleteScenario, kickParticipant,
    };
})();
