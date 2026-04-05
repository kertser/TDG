/**
 * admin.js – Admin panel: session/scenario management, DB stats, current session controls.
 */
const KAdmin = (() => {

    function init() {
        // Session management
        const deleteAllBtn = document.getElementById('admin-delete-all-sessions');
        const refreshBtn = document.getElementById('admin-refresh-sessions');
        const listScenariosBtn = document.getElementById('admin-list-scenarios');
        const deleteAllScenariosBtn = document.getElementById('admin-delete-all-scenarios');
        const pauseBtn = document.getElementById('admin-pause-session');
        const resetTickBtn = document.getElementById('admin-reset-tick');
        const dbStatsBtn = document.getElementById('admin-db-stats');

        if (deleteAllBtn) {
            deleteAllBtn.addEventListener('click', _deleteAllSessions);
        }
        if (refreshBtn) {
            refreshBtn.addEventListener('click', _refreshSessions);
        }
        if (listScenariosBtn) {
            listScenariosBtn.addEventListener('click', _listScenarios);
        }
        if (deleteAllScenariosBtn) {
            deleteAllScenariosBtn.addEventListener('click', _deleteAllScenarios);
        }
        if (pauseBtn) {
            pauseBtn.addEventListener('click', _pauseSession);
        }
        if (resetTickBtn) {
            resetTickBtn.addEventListener('click', _resetTick);
        }
        if (dbStatsBtn) {
            dbStatsBtn.addEventListener('click', _loadDbStats);
        }
    }

    function _getToken() {
        return KSessionUI.getToken();
    }

    function _getSessionId() {
        return KSessionUI.getSessionId();
    }

    // ── Session Management ──────────────────────────

    async function _deleteAllSessions() {
        const token = _getToken();
        if (!token) {
            _showInfo('admin-session-count', 'Not logged in', 'error');
            return;
        }
        if (!confirm('⚠ Delete ALL sessions? This cannot be undone.')) return;

        try {
            const resp = await fetch('/api/sessions', {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                _showInfo('admin-session-count', '✓ All sessions deleted', 'success');
                KWebSocket.disconnect();
                // Reset session UI state
                document.getElementById('session-info').textContent = '';
                const startBtn = document.getElementById('start-session-btn');
                const tickBtn = document.getElementById('tick-btn');
                if (startBtn) startBtn.style.display = 'none';
                if (tickBtn) tickBtn.style.display = 'none';
                const drawToolbar = document.getElementById('draw-toolbar');
                if (drawToolbar) drawToolbar.style.display = 'none';
                KSessionUI.loadSessions();
                KGameLog.addEntry('All sessions deleted (admin)', 'info');
                _updateCurrentSessionInfo();
            } else {
                const data = await resp.json().catch(() => ({}));
                _showInfo('admin-session-count', `✗ Error: ${data.detail || resp.status}`, 'error');
            }
        } catch (err) {
            _showInfo('admin-session-count', `✗ ${err.message}`, 'error');
        }
    }

    async function _refreshSessions() {
        const token = _getToken();
        if (!token) {
            _showInfo('admin-session-count', 'Not logged in', 'error');
            return;
        }
        try {
            const resp = await fetch('/api/sessions', {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const sessions = await resp.json();
            _showInfo('admin-session-count', `${sessions.length} session(s) found`);
            KSessionUI.loadSessions();
        } catch (err) {
            _showInfo('admin-session-count', `✗ ${err.message}`, 'error');
        }
    }

    // ── Scenario Management ─────────────────────────

    async function _listScenarios() {
        try {
            const resp = await fetch('/api/scenarios');
            const scenarios = await resp.json();
            if (scenarios.length === 0) {
                _showInfo('admin-scenario-list', 'No scenarios');
                return;
            }
            let html = '';
            scenarios.forEach(s => {
                html += `<div class="admin-item">
                    <b>${s.title || 'Untitled'}</b>
                    <span class="admin-item-meta">${s.id.substring(0, 8)}...</span>
                </div>`;
            });
            document.getElementById('admin-scenario-list').innerHTML = html;
        } catch (err) {
            _showInfo('admin-scenario-list', `✗ ${err.message}`, 'error');
        }
    }

    async function _deleteAllScenarios() {
        if (!confirm('⚠ Delete ALL scenarios? Sessions using these will also be affected.')) return;
        try {
            const resp = await fetch('/api/scenarios');
            const scenarios = await resp.json();
            let deleted = 0;
            for (const s of scenarios) {
                const del = await fetch(`/api/scenarios/${s.id}`, { method: 'DELETE' });
                if (del.ok || del.status === 204) deleted++;
            }
            _showInfo('admin-scenario-list', `✓ Deleted ${deleted}/${scenarios.length} scenarios`, 'success');
        } catch (err) {
            _showInfo('admin-scenario-list', `✗ ${err.message}`, 'error');
        }
    }

    // ── Current Session Controls ────────────────────

    async function _pauseSession() {
        const token = _getToken();
        const sessionId = _getSessionId();
        if (!token || !sessionId) return;
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/pause`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const data = await resp.json();
            _showInfo('admin-current-session-info', `Session paused at tick ${data.tick}`, 'success');
            KGameLog.addEntry('Session paused (admin)', 'info');
        } catch (err) {
            _showInfo('admin-current-session-info', `✗ ${err.message}`, 'error');
        }
    }

    async function _resetTick() {
        // This would require a backend endpoint – placeholder
        _showInfo('admin-current-session-info', 'Reset not yet implemented', 'error');
    }

    // ── DB Stats ────────────────────────────────────

    async function _loadDbStats() {
        const token = _getToken();
        if (!token) {
            _showInfo('admin-db-info', 'Not logged in', 'error');
            return;
        }
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

    // ── Update current session info ─────────────────

    function _updateCurrentSessionInfo() {
        const sessionId = _getSessionId();
        const el = document.getElementById('admin-current-session-info');
        const pauseBtn = document.getElementById('admin-pause-session');
        const resetBtn = document.getElementById('admin-reset-tick');

        if (!sessionId) {
            if (el) el.textContent = 'No active session';
            if (pauseBtn) pauseBtn.style.display = 'none';
            if (resetBtn) resetBtn.style.display = 'none';
        } else {
            if (el) el.textContent = `Active: ${sessionId.substring(0, 8)}...`;
            if (pauseBtn) pauseBtn.style.display = 'inline-block';
            if (resetBtn) resetBtn.style.display = 'inline-block';
        }
    }

    function updateSessionContext() {
        _updateCurrentSessionInfo();
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

    return { init, updateSessionContext };
})();

