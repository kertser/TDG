/**
 * gamelog.js – Unified game log combining events, reports, orders.
 */
const KGameLog = (() => {
    const MAX_ENTRIES = 200;
    let entries = [];

    function addEntry(text, type = 'info') {
        const logEl = document.getElementById('game-log');
        if (!logEl) return;

        const item = document.createElement('div');
        item.className = `log-item ${type}`;
        item.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
        logEl.prepend(item);

        entries.push({ text, type, time: Date.now() });
        if (entries.length > MAX_ENTRIES) {
            entries.shift();
            if (logEl.lastChild) logEl.removeChild(logEl.lastChild);
        }
    }

    return { addEntry };
})();

