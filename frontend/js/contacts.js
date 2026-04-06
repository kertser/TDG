/**
 * contacts.js – Render detected enemy contacts on the map.
 * Shows contacts as uncertainty circles with estimated type info.
 */
const KContacts = (() => {
    let contactsLayer = null;
    let _visible = true;
    let _map = null;

    function init(map) {
        _map = map;
        contactsLayer = L.layerGroup().addTo(map);
    }

    async function load(sessionId, token) {
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/contacts`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) return;
            const contacts = await resp.json();
            render(contacts);
        } catch (err) {
            console.warn('Contacts load failed:', err);
        }
    }

    function render(contacts) {
        if (!contactsLayer) return;
        contactsLayer.clearLayers();

        contacts.forEach(c => {
            if (c.lat == null || c.lon == null) return;

            // Uncertainty circle
            const accuracy = c.location_accuracy_m || 500;
            const isStale = c.is_stale;
            const confidence = c.confidence || 0.5;

            const circleStyle = {
                color: isStale ? '#888' : '#e53935',
                weight: isStale ? 1 : 2,
                dashArray: isStale ? '5,5' : null,
                fillColor: '#e53935',
                fillOpacity: isStale ? 0.05 : 0.1 * confidence,
            };

            const circle = L.circle([c.lat, c.lon], {
                radius: accuracy,
                ...circleStyle,
            });

            // Center marker (red diamond)
            const diamondHtml = `<div style="
                width: 12px; height: 12px;
                background: ${isStale ? '#888' : '#e53935'};
                transform: rotate(45deg);
                border: 1px solid #fff;
                opacity: ${isStale ? 0.5 : 0.9};
            "></div>`;
            const marker = L.marker([c.lat, c.lon], {
                icon: L.divIcon({
                    className: '',
                    html: diamondHtml,
                    iconSize: [12, 12],
                    iconAnchor: [6, 6],
                }),
            });

            // Popup
            let popupHtml = `<b>Contact</b><br>`;
            if (c.estimated_type) popupHtml += `Type: ${c.estimated_type}<br>`;
            if (c.estimated_size) popupHtml += `Size: ${c.estimated_size}<br>`;
            popupHtml += `Confidence: ${(confidence * 100).toFixed(0)}%<br>`;
            popupHtml += `Source: ${c.source || 'unknown'}<br>`;
            if (isStale) popupHtml += `<span style="color:#ff9800">⚠ STALE</span><br>`;
            popupHtml += `Accuracy: ~${accuracy}m`;

            marker.bindPopup(popupHtml);

            contactsLayer.addLayer(circle);
            contactsLayer.addLayer(marker);
        });
    }

    /** Toggle contacts layer visibility. Returns new state. */
    function toggle() {
        _visible = !_visible;
        if (_map || (contactsLayer && contactsLayer._map)) {
            const m = _map || contactsLayer._map;
            if (_visible) {
                if (contactsLayer && !m.hasLayer(contactsLayer)) m.addLayer(contactsLayer);
            } else {
                if (contactsLayer && m.hasLayer(contactsLayer)) m.removeLayer(contactsLayer);
            }
        }
        return _visible;
    }

    /** Clear all contacts from map (used on logout). */
    function clearAll() {
        if (contactsLayer) contactsLayer.clearLayers();
    }

    return { init, load, render, clearAll, toggle };
})();
