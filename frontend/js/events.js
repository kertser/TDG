/**
 * events.js – Event log panel: scrolling timeline with type filtering.
 *             Clicking an event re-centers the map on its position.
 */
const KEvents = (() => {
    let allEvents = [];
    let filterType = null;

    const EVENT_ICONS = {
        movement: '🚶',
        combat: '⚔️',
        unit_destroyed: '💥',
        contact_new: '👁️',
        contact_lost: '❓',
        order_issued: '📋',
        order_completed: '✅',
        morale_break: '🏳️',
        comms_change: '📡',
        ammo_depleted: '🔴',
    };

    async function load(sessionId, token) {
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/events`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) return;
            allEvents = await resp.json();
            render(allEvents);
        } catch (err) {
            console.warn('Events load failed:', err);
        }
    }

    function addEvent(event) {
        allEvents.push(event);
        if (!filterType || event.event_type === filterType) {
            _appendEventDom(event);
        }
    }

    function render(events) {
        const container = document.getElementById('events-list');
        if (!container) return;
        container.innerHTML = '';

        const filtered = filterType
            ? events.filter(e => e.event_type === filterType)
            : events;

        // Show most recent first
        filtered.slice().reverse().forEach(e => _appendEventDom(e));
    }

    function _getEventPosition(event) {
        // Try to extract lat/lon from the event payload
        if (!event.payload) return null;
        const p = event.payload;
        if (p.lat != null && p.lon != null) return { lat: p.lat, lon: p.lon };
        if (p.position && p.position.lat != null && p.position.lon != null) return p.position;
        if (p.location && p.location.lat != null && p.location.lon != null) return p.location;
        if (p.to && p.to.lat != null && p.to.lon != null) return p.to;
        if (p.from && p.from.lat != null && p.from.lon != null) return p.from;
        if (p.actor_lat != null && p.actor_lon != null) return { lat: p.actor_lat, lon: p.actor_lon };
        if (p.target_lat != null && p.target_lon != null) return { lat: p.target_lat, lon: p.target_lon };
        return null;
    }

    function _appendEventDom(event) {
        const container = document.getElementById('events-list');
        if (!container) return;

        const icon = EVENT_ICONS[event.event_type] || '📌';
        const div = document.createElement('div');
        div.className = `log-item event`;

        const pos = _getEventPosition(event);
        if (pos) {
            div.style.cursor = 'pointer';
            div.title = `Click to center map on event (${pos.lat.toFixed(4)}, ${pos.lon.toFixed(4)})`;
        }

        div.innerHTML = `
            <span style="font-size:11px;color:#888;">Tick ${event.tick || '?'}</span>
            ${icon} ${event.text_summary || event.event_type}
        `;

        if (pos) {
            div.addEventListener('click', () => {
                const map = KMap.getMap();
                if (map) {
                    map.setView([pos.lat, pos.lon], map.getZoom());
                }
            });
        }

        container.prepend(div);
    }

    function setFilter(type) {
        filterType = type;
        render(allEvents);
    }

    return { load, addEvent, render, setFilter };
})();
