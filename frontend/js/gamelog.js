/**
 * gamelog.js – Unified game log combining events, reports, orders.
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

    function exportLog() {
        if (entries.length === 0) return;
        const lines = entries.map(e => `[${e.time}] [${(e.type || 'info').toUpperCase()}] ${e.text}`);
        const header = `TDG Game Log — Exported ${new Date().toISOString()}\n${'═'.repeat(60)}\n\n`;
        const blob = new Blob([header + lines.join('\n')], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `tdg_gamelog_${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
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
    // Also try immediately (for scripts loaded at end of body)
    setTimeout(() => {
        const btn = document.getElementById('export-log-btn');
        if (btn && !btn._bound) {
            btn.addEventListener('click', exportLog);
            btn._bound = true;
        }
    }, 0);

    return { addEntry, exportLog };
})();
