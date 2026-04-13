/**
 * gamelog.js – Application log: system messages, session events, admin actions.
 *             Tactical data (events, reports, orders, radio) is in their own panels.
 *             Export as plain text file.
 */
const KGameLog = (() => {
    const MAX_ENTRIES = 300;
    let entries = [];

    const TYPE_ICONS = {
        info: 'ℹ',
        order: '📋',
        event: '⚔',
        report: '📡',
        error: '⚠',
    };

    function _timeStr() {
        const d = new Date();
        return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
    }

    function addEntry(text, type = 'info') {
        const logEl = document.getElementById('game-log');
        if (!logEl) return;

        const time = _timeStr();
        const icon = TYPE_ICONS[type] || 'ℹ';

        const item = document.createElement('div');
        item.className = `log-item ${type}`;
        item.innerHTML = `<span class="log-time">${time}</span> <span class="log-icon">${icon}</span> ${_escHtml(text)}`;
        logEl.prepend(item);

        entries.push({ text, type, time, icon });
        if (entries.length > MAX_ENTRIES) {
            entries.shift();
            if (logEl.lastChild) logEl.removeChild(logEl.lastChild);
        }
    }

    /**
     * Export application log as plain text file.
     * Tactical data (events, reports, orders, radio) is exported from their own panels.
     */
    async function exportLog() {
        if (entries.length === 0) {
            try { await KDialogs.alert(KI18n.t('log.no_entries')); } catch(e) { alert(KI18n.t('log.no_entries')); }
            return;
        }

        const sessionId = _getSessionId();
        const sessionInfo = sessionId
            ? await _fetchJSON(`/api/sessions/${sessionId}`, _getToken()).catch(() => null)
            : null;

        const sessionName = sessionInfo && sessionInfo.scenario_title
            ? sessionInfo.scenario_title.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 30)
            : 'session';
        const dateStr = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

        let text = `${KI18n.t('log.header')}\n`;
        text += `${KI18n.t('log.session')}: ${sessionName}\n`;
        text += `${KI18n.t('log.exported')}: ${new Date().toLocaleString()}\n`;
        text += '═'.repeat(60) + '\n\n';

        // Entries in chronological order (oldest first)
        const sorted = [...entries];
        for (const e of sorted) {
            const typeLabel = (e.type || 'info').toUpperCase().padEnd(7);
            text += `[${e.time}] [${typeLabel}] ${e.text}\n`;
        }

        const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `TDG_Log_${sessionName}_${dateStr}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    async function _fetchJSON(url, token) {
        try {
            const resp = await fetch(url, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (!resp.ok) return null;
            return await resp.json();
        } catch {
            return null;
        }
    }

    function _getSessionId() {
        if (typeof KSessionUI !== 'undefined' && KSessionUI.getSessionId) return KSessionUI.getSessionId();
        return null;
    }

    function _getToken() {
        if (typeof KSessionUI !== 'undefined' && KSessionUI.getToken) return KSessionUI.getToken();
        return null;
    }


    function _escHtml(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    // Bind export button on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        const btn = document.getElementById('export-log-btn');
        if (btn) btn.addEventListener('click', exportLog);
    });
    setTimeout(() => {
        const btn = document.getElementById('export-log-btn');
        if (btn && !btn._bound) {
            btn.addEventListener('click', exportLog);
            btn._bound = true;
        }
    }, 0);

    return { addEntry, exportLog };
})();
