/**
 * gamelog.js – Unified game log combining events, reports, orders.
 *             Export to Excel with full structured data.
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

    /** Lazy-load SheetJS (xlsx) library on demand — avoids blocking page load. */
    async function _ensureXLSX() {
        if (typeof XLSX !== 'undefined') return true;
        return new Promise((resolve) => {
            const s = document.createElement('script');
            s.src = 'https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js';
            s.onload = () => resolve(true);
            s.onerror = () => resolve(false);
            document.head.appendChild(s);
        });
    }

    /**
     * Export a comprehensive game log as Excel (.xlsx) with multiple sheets:
     * - Events (all game events with tick, time, type, description, payload)
     * - Reports (SITREP, SPOTREP, SHELREP, etc.)
     * - Orders (all issued orders with status)
     * - Radio Log (chat messages)
     * - Game Log (local client log entries)
     */
    async function exportLog() {
        if (typeof XLSX === 'undefined') {
            const btn = document.getElementById('export-log-btn');
            if (btn) { btn.disabled = true; btn.innerHTML = '<span>⏳ Loading...</span>'; }
            const loaded = await _ensureXLSX();
            if (btn) { btn.disabled = false; btn.innerHTML = '📥 Export Log'; }
            if (!loaded) {
                alert('Failed to load Excel export library. Check your internet connection.');
                return;
            }
        }

        const sessionId = _getSessionId();
        const token = _getToken();

        const btn = document.getElementById('export-log-btn');
        const origHtml = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span>⏳ Exporting...</span>';
        }

        try {
            const [eventsData, reportsData, ordersData, chatData] = await Promise.all([
                _fetchJSON(`/api/sessions/${sessionId}/events`, token),
                _fetchJSON(`/api/sessions/${sessionId}/reports?limit=1000`, token),
                _fetchJSON(`/api/sessions/${sessionId}/orders`, token),
                _fetchJSON(`/api/sessions/${sessionId}/chat`, token),
            ]);

            const sessionInfo = await _fetchJSON(`/api/sessions/${sessionId}`, token).catch(() => null);

            const wb = XLSX.utils.book_new();

            // ── Sheet 1: Events ──
            const eventsRows = (eventsData || []).map(e => ({
                'Turn': e.tick != null ? e.tick : '',
                'Game Time': e.created_at ? _fmtDT(e.created_at) : '',
                'Type': e.event_type || '',
                'Description': e.text_summary || '',
                'Visibility': e.visibility || '',
                'Details': e.payload ? _flatPayload(e.payload) : '',
            }));
            _addSheet(wb, 'Events', eventsRows, [
                { key: 'Turn', width: 6 },
                { key: 'Game Time', width: 20 },
                { key: 'Type', width: 18 },
                { key: 'Description', width: 60 },
                { key: 'Visibility', width: 10 },
                { key: 'Details', width: 40 },
            ]);

            // ── Sheet 2: Reports ──
            const reportsRows = (reportsData || []).sort((a, b) => (a.tick || 0) - (b.tick || 0)).map(r => ({
                'Turn': r.tick != null ? r.tick : '',
                'Game Time': r.game_timestamp ? _fmtDT(r.game_timestamp) : (r.created_at ? _fmtDT(r.created_at) : ''),
                'Channel': (r.channel || '').toUpperCase(),
                'Side': r.to_side || '',
                'Report Text': r.text || '',
            }));
            _addSheet(wb, 'Reports', reportsRows, [
                { key: 'Turn', width: 6 },
                { key: 'Game Time', width: 20 },
                { key: 'Channel', width: 12 },
                { key: 'Side', width: 8 },
                { key: 'Report Text', width: 80 },
            ]);

            // ── Sheet 3: Orders ──
            const ordersRows = (ordersData || []).sort((a, b) =>
                (a.issued_at || '').localeCompare(b.issued_at || '')
            ).map(o => ({
                'Issued At': o.issued_at ? _fmtDT(o.issued_at) : '',
                'Game Time': o.game_timestamp ? _fmtDT(o.game_timestamp) : '',
                'Side': o.issued_by_side || '',
                'Status': o.status || '',
                'Classification': o.classification || (o.parsed_order && o.parsed_order.classification) || '',
                'Order Text': o.original_text || '',
                'Target Units': (o.target_unit_ids || []).join(', '),
                'Confidence': o.confidence != null ? Math.round(o.confidence * 100) + '%' :
                    (o.parsed_order && o.parsed_order.confidence != null ? Math.round(o.parsed_order.confidence * 100) + '%' : ''),
            }));
            _addSheet(wb, 'Orders', ordersRows, [
                { key: 'Issued At', width: 20 },
                { key: 'Game Time', width: 20 },
                { key: 'Side', width: 8 },
                { key: 'Status', width: 12 },
                { key: 'Classification', width: 16 },
                { key: 'Order Text', width: 60 },
                { key: 'Target Units', width: 30 },
                { key: 'Confidence', width: 10 },
            ]);

            // ── Sheet 4: Radio Log ──
            const radioRows = (chatData || []).map(m => ({
                'Time': m.timestamp ? _fmtDT(m.timestamp) : '',
                'Sender': m.sender_name || '',
                'Recipient': m.recipient === 'all' ? 'All' : (m.recipient_name || m.recipient || ''),
                'Type': m.is_order ? 'ORDER' : m.is_unit_response ? 'UNIT' : 'CHAT',
                'Message': m.text || '',
            }));
            _addSheet(wb, 'Radio', radioRows, [
                { key: 'Time', width: 20 },
                { key: 'Sender', width: 22 },
                { key: 'Recipient', width: 22 },
                { key: 'Type', width: 8 },
                { key: 'Message', width: 80 },
            ]);

            // ── Sheet 5: Client Game Log ──
            const logRows = entries.map(e => ({
                'Time': e.time || '',
                'Type': (e.type || 'info').toUpperCase(),
                'Entry': e.text || '',
            }));
            _addSheet(wb, 'Client Log', logRows, [
                { key: 'Time', width: 10 },
                { key: 'Type', width: 10 },
                { key: 'Entry', width: 80 },
            ]);

            // Filename
            const sessionName = sessionInfo && sessionInfo.scenario_title
                ? sessionInfo.scenario_title.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 30)
                : 'session';
            const dateStr = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
            XLSX.writeFile(wb, `TDG_GameLog_${sessionName}_${dateStr}.xlsx`);

        } catch (err) {
            console.error('Excel export failed:', err);
            alert('Export failed: ' + (err.message || 'Unknown error'));
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = origHtml;
            }
        }
    }

    function _addSheet(wb, name, rows, colDefs) {
        if (!rows || rows.length === 0) {
            const ws = XLSX.utils.aoa_to_sheet([colDefs.map(c => c.key)]);
            ws['!cols'] = colDefs.map(c => ({ wch: c.width }));
            XLSX.utils.book_append_sheet(wb, ws, name);
            return;
        }
        const ws = XLSX.utils.json_to_sheet(rows, { header: colDefs.map(c => c.key) });
        ws['!cols'] = colDefs.map(c => ({ wch: c.width }));
        XLSX.utils.book_append_sheet(wb, ws, name);
    }

    async function _fetchJSON(url, token) {
        try {
            const resp = await fetch(url, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (!resp.ok) return [];
            return await resp.json();
        } catch {
            return [];
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

    function _fmtDT(isoStr) {
        if (!isoStr) return '';
        try {
            const d = new Date(isoStr);
            if (isNaN(d.getTime())) return isoStr;
            return d.toLocaleString([], {
                year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', second: '2-digit',
            });
        } catch {
            return isoStr;
        }
    }

    function _flatPayload(obj) {
        if (!obj || typeof obj !== 'object') return String(obj || '');
        const parts = [];
        for (const [k, v] of Object.entries(obj)) {
            if (v == null) continue;
            if (typeof v === 'object') {
                parts.push(`${k}: ${JSON.stringify(v)}`);
            } else {
                parts.push(`${k}: ${v}`);
            }
        }
        return parts.join('; ');
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
