/**
 * reports.js – Reports panel: SPOTREPs, SHELREPs, SITREPs, INTSUMs, CASREPs.
 *             Displays tactical reports with channel filtering.
 *             Clicking a report re-centers the map on its location.
 */
const KReports = (() => {
    let allReports = [];
    let filterChannel = null;
    let _sessionId = null;
    let _token = null;

    const CHANNEL_ICONS = {
        spotrep:  '👁️',
        shelrep:  '💥',
        sitrep:   '📊',
        intsum:   '🔍',
        casrep:   '☠️',
        contactrep: '📡',
        custom:   '📝',
    };

    const CHANNEL_LABELS = {
        spotrep:  'SPOTREP',
        shelrep:  'SHELREP',
        sitrep:   'SITREP',
        intsum:   'INTSUM',
        casrep:   'CASREP',
        contactrep: 'CONTACT',
        custom:   'REPORT',
    };

    const CHANNEL_COLORS = {
        spotrep:  '#f0ad4e',
        shelrep:  '#d9534f',
        sitrep:   '#5bc0de',
        intsum:   '#5cb85c',
        casrep:   '#d9534f',
        contactrep: '#f0ad4e',
        custom:   '#888',
    };

    async function load(sessionId, token) {
        _sessionId = sessionId;
        _token = token;
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/reports`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) return;
            allReports = await resp.json();
            render();
        } catch (err) {
            console.warn('Reports load failed:', err);
        }
    }

    function addReport(report) {
        allReports.unshift(report);
        if (allReports.length > 500) allReports.pop();
        if (!filterChannel || report.channel === filterChannel) {
            _prependReportDom(report);
        }
        _renderFilterBar();
    }

    function render() {
        const container = document.getElementById('reports-list');
        if (!container) return;
        container.innerHTML = '';

        const filtered = filterChannel
            ? allReports.filter(r => r.channel === filterChannel)
            : allReports;

        filtered.forEach(r => _appendReportDom(r));
        _renderFilterBar();
    }

    function _renderFilterBar() {
        const bar = document.getElementById('reports-filter-bar');
        if (!bar) return;

        const counts = {};
        allReports.forEach(r => {
            counts[r.channel] = (counts[r.channel] || 0) + 1;
        });

        bar.innerHTML = '';

        const allBtn = document.createElement('button');
        allBtn.className = 'reports-filter-btn' + (!filterChannel ? ' active' : '');
        allBtn.textContent = 'All (' + allReports.length + ')';
        allBtn.addEventListener('click', () => { filterChannel = null; render(); });
        bar.appendChild(allBtn);

        const channels = ['spotrep', 'shelrep', 'sitrep', 'intsum', 'casrep'];
        for (const ch of channels) {
            if (!counts[ch]) continue;
            const btn = document.createElement('button');
            btn.className = 'reports-filter-btn' + (filterChannel === ch ? ' active' : '');
            btn.innerHTML = (CHANNEL_ICONS[ch] || '') + ' ' + (CHANNEL_LABELS[ch] || ch) + ' (' + counts[ch] + ')';
            btn.style.borderBottomColor = CHANNEL_COLORS[ch] || '#888';
            btn.addEventListener('click', () => { filterChannel = ch; render(); });
            bar.appendChild(btn);
        }
    }

    function _getReportPosition(report) {
        const sd = report.structured_data;
        if (!sd) return null;
        if (sd.lat != null && sd.lon != null) return { lat: sd.lat, lon: sd.lon };
        return null;
    }

    function _appendReportDom(report) {
        const container = document.getElementById('reports-list');
        if (!container) return;
        container.appendChild(_createReportEl(report));
    }

    function _prependReportDom(report) {
        const container = document.getElementById('reports-list');
        if (!container) return;
        container.prepend(_createReportEl(report));
    }

    function _createReportEl(report) {
        const icon = CHANNEL_ICONS[report.channel] || '📝';
        const label = CHANNEL_LABELS[report.channel] || (report.channel || '').toUpperCase();
        const color = CHANNEL_COLORS[report.channel] || '#888';

        const div = document.createElement('div');
        div.className = 'report-item';
        div.style.borderLeftColor = color;

        const pos = _getReportPosition(report);
        if (pos) {
            div.style.cursor = 'pointer';
            div.title = 'Click to center map (' + pos.lat.toFixed(4) + ', ' + pos.lon.toFixed(4) + ')';
        }

        const textHtml = _escHtml(report.text).replace(/\n/g, '<br>');

        div.innerHTML =
            '<div class="report-header">' +
                '<span class="report-channel" style="color:' + color + ';">' + icon + ' ' + label + '</span>' +
                '<span class="report-tick">Turn ' + (report.tick != null ? report.tick : '?') + '</span>' +
            '</div>' +
            '<div class="report-text">' + textHtml + '</div>';

        if (pos) {
            div.addEventListener('click', () => {
                const map = KMap.getMap();
                if (map) {
                    map.setView([pos.lat, pos.lon], Math.max(map.getZoom(), 14));
                }
            });
        }

        return div;
    }

    function setFilter(channel) {
        filterChannel = channel;
        render();
    }

    function exportReports() {
        if (allReports.length === 0) return;
        const lines = allReports.map(r => {
            const ch = (CHANNEL_LABELS[r.channel] || r.channel || '').padEnd(10);
            return '[Turn ' + String(r.tick).padStart(3) + '] [' + ch + '] ' + r.text;
        });
        const header = 'TDG Reports \u2014 Exported ' + new Date().toISOString() + '\n' + '\u2550'.repeat(60) + '\n\n';
        const blob = new Blob([header + lines.join('\n\n')], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'tdg_reports_' + new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19) + '.txt';
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

    function _bindExport() {
        const btn = document.getElementById('export-reports-btn');
        if (btn && !btn._bound) {
            btn.addEventListener('click', exportReports);
            btn._bound = true;
        }
    }
    document.addEventListener('DOMContentLoaded', _bindExport);
    setTimeout(_bindExport, 0);

    return { load, addReport, render, setFilter, exportReports };
})();
