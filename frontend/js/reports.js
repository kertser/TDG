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
    let _unreadCount = 0;
    let _isTabActive = false;

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
        spotrep:  'РАЗВЕДДОНЕСЕНИЕ',
        shelrep:  'ОБСТРЕЛ',
        sitrep:   'ОБСТАНОВКА',
        intsum:   'РАЗВЕД.СВОДКА',
        casrep:   'ПОТЕРИ',
        contactrep: 'КОНТАКТ',
        custom:   'ДОКЛАД',
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
        // Detect if Reports tab is currently active
        const tabBtn = document.querySelector('.tab-btn[data-tab="reports-tab"]');
        _isTabActive = tabBtn ? tabBtn.classList.contains('active') : false;
        _unreadCount = 0;
        _updateBadge();
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

    function setTabActive(active) {
        _isTabActive = active;
        if (active) {
            _unreadCount = 0;
            _updateBadge();
        }
    }

    function _updateBadge() {
        const tabBtn = document.querySelector('.tab-btn[data-tab="reports-tab"]');
        if (!tabBtn) return;
        let badge = tabBtn.querySelector('.reports-badge');
        if (_unreadCount > 0) {
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'reports-badge';
                tabBtn.appendChild(badge);
            }
            badge.textContent = _unreadCount > 99 ? '99+' : String(_unreadCount);
            badge.style.display = '';
        } else {
            if (badge) badge.style.display = 'none';
        }
    }

    function addReport(report) {
        allReports.unshift(report);
        if (allReports.length > 500) allReports.pop();
        if (!filterChannel || report.channel === filterChannel) {
            _prependReportDom(report);
        }
        _renderFilterBar();
        // Track unread when Reports tab is not visible
        if (!_isTabActive) {
            _unreadCount++;
            _updateBadge();
        }
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
        allBtn.textContent = KI18n.t('reports.all') + ' (' + allReports.length + ')';
        allBtn.addEventListener('click', () => { filterChannel = null; render(); });
        bar.appendChild(allBtn);

        const channels = ['spotrep', 'shelrep', 'sitrep', 'intsum', 'casrep'];
        for (const ch of channels) {
            if (!counts[ch]) continue;
            const btn = document.createElement('button');
            btn.className = 'reports-filter-btn' + (filterChannel === ch ? ' active' : '');
            btn.innerHTML = (CHANNEL_ICONS[ch] || '') + ' ' + _channelLabel(ch) + ' (' + counts[ch] + ')';
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
                '<span class="report-tick">' + KI18n.t('clock.turn') + ' ' + (report.tick != null ? report.tick : '?') + '</span>' +
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

    async function exportReports() {
        if (allReports.length === 0) return;

        // Lazy-load SheetJS if not available
        if (typeof XLSX === 'undefined') {
            await new Promise((resolve) => {
                const s = document.createElement('script');
                s.src = 'https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js';
                s.onload = () => resolve(true);
                s.onerror = () => resolve(false);
                document.head.appendChild(s);
            });
        }

        // Use Excel if SheetJS is available, otherwise fallback to text
        if (typeof XLSX !== 'undefined') {
            const wb = XLSX.utils.book_new();
            const rows = allReports.sort((a, b) => (a.tick || 0) - (b.tick || 0)).map(r => ({
                [KI18n.t('reports.turn')]: r.tick != null ? r.tick : '',
                [KI18n.t('reports.game_time')]: r.game_timestamp ? _fmtDT(r.game_timestamp) : (r.created_at ? _fmtDT(r.created_at) : ''),
                [KI18n.t('reports.channel')]: _channelLabel(r.channel).toUpperCase(),
                [KI18n.t('reports.side')]: r.to_side || '',
                [KI18n.t('reports.report_text')]: r.text || '',
            }));
            const ws = XLSX.utils.json_to_sheet(rows);
            ws['!cols'] = [{ wch: 6 }, { wch: 20 }, { wch: 12 }, { wch: 8 }, { wch: 80 }];
            XLSX.utils.book_append_sheet(wb, ws, 'Reports');
            const dateStr = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
            XLSX.writeFile(wb, `TDG_Reports_${dateStr}.xlsx`);
            return;
        }

        // Fallback: text export
        const lines = allReports.map(r => {
            const ch = _channelLabel(r.channel).padEnd(10);
            return '[' + KI18n.t('clock.turn') + ' ' + String(r.tick).padStart(3) + '] [' + ch + '] ' + r.text;
        });
        const header = KI18n.t('reports.export_header') + ' ' + new Date().toISOString() + '\n' + '\u2550'.repeat(60) + '\n\n';
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

    return { load, addReport, render, setFilter, exportReports, setTabActive };
})();
