/**
 * units.js – Fetch and render visible units on the map with military symbols.
 *            Left-click = select/deselect unit for orders.
 *            Right-click = open detail popup.
 */
const KUnits = (() => {
    let unitMarkers = {};
    let unitsLayer = null;
    let allUnitsData = [];
    let selectedUnitIds = new Set();

    function init(map) {
        unitsLayer = L.layerGroup().addTo(map);
    }

    async function load(sessionId, token) {
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/units`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) return;
            const units = await resp.json();
            allUnitsData = units;
            render(units);
            _updateSelectionUI();
        } catch (err) {
            console.warn('Units load failed:', err);
        }
    }

    function render(units) {
        if (!unitsLayer) return;
        unitsLayer.clearLayers();
        unitMarkers = {};
        allUnitsData = units;

        units.forEach(u => {
            if (u.lat == null || u.lon == null) return;
            if (u.is_destroyed) return;

            const icon = KSymbols.createIcon(u.sidc, {
                direction: u.heading_deg || 0,
            });

            const marker = L.marker([u.lat, u.lon], { icon });

            // Build detail popup content (shown on right-click)
            const popupHtml = _buildPopupHtml(u);
            marker.bindPopup(popupHtml);

            // Tooltip with unit name
            marker.bindTooltip(u.name, {
                permanent: false,
                direction: 'top',
                offset: [0, -20],
            });

            // LEFT-CLICK: toggle selection directly
            marker.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                _toggleSelect(u.id);
            });

            // RIGHT-CLICK: open detail popup
            marker.on('contextmenu', (e) => {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                // Update popup content (selection state may have changed)
                marker.setPopupContent(_buildPopupHtml(u));
                marker.openPopup();
            });

            unitsLayer.addLayer(marker);
            unitMarkers[u.id] = marker;

            // Apply selected visual
            if (selectedUnitIds.has(u.id)) {
                _applySelectedStyle(marker);
            }
        });
    }

    function _buildPopupHtml(u) {
        let html = `<b>${u.name}</b><br>`;
        html += `<span style="color:#888">${u.unit_type}</span><br>`;
        html += `Side: <b>${u.side}</b><br>`;

        if (u.strength != null) {
            const strengthPct = (u.strength * 100).toFixed(0);
            const strengthColor = u.strength > 0.6 ? '#4caf50' : u.strength > 0.3 ? '#ff9800' : '#f44336';
            html += `Strength: <span style="color:${strengthColor};font-weight:700">${strengthPct}%</span><br>`;
        }
        if (u.morale != null) {
            html += `Morale: ${(u.morale * 100).toFixed(0)}%<br>`;
        }
        if (u.ammo != null) {
            html += `Ammo: ${(u.ammo * 100).toFixed(0)}%<br>`;
        }
        if (u.suppression != null && u.suppression > 0) {
            html += `Suppression: ${(u.suppression * 100).toFixed(0)}%<br>`;
        }
        if (u.comms_status && u.comms_status !== 'operational') {
            html += `Comms: <span style="color:#ff9800">${u.comms_status}</span><br>`;
        }

        const isSelected = selectedUnitIds.has(u.id);
        const selectLabel = isSelected ? '✅ Deselect' : '☐ Select for order';
        html += `<button onclick="KUnits.toggleSelect('${u.id}')" style="margin-top:4px">${selectLabel}</button>`;
        return html;
    }

    function _toggleSelect(unitId) {
        if (selectedUnitIds.has(unitId)) {
            selectedUnitIds.delete(unitId);
        } else {
            selectedUnitIds.add(unitId);
        }
        render(allUnitsData);
        _updateSelectionUI();
    }

    function toggleSelect(unitId) {
        _toggleSelect(unitId);
        // Close popup if toggled via popup button
        const map = KMap.getMap();
        if (map) map.closePopup();
    }

    function _applySelectedStyle(marker) {
        const latlng = marker.getLatLng();
        const ring = L.circleMarker(latlng, {
            radius: 18,
            color: '#4fc3f7',
            weight: 3,
            fillColor: '#4fc3f7',
            fillOpacity: 0.12,
            interactive: false,
        });
        unitsLayer.addLayer(ring);
    }

    function getSelectedIds() {
        return Array.from(selectedUnitIds);
    }

    function clearSelection() {
        selectedUnitIds.clear();
        render(allUnitsData);
        _updateSelectionUI();
    }

    function _updateSelectionUI() {
        const selDisplay = document.getElementById('selected-units-display');
        if (!selDisplay) return;

        if (selectedUnitIds.size === 0) {
            selDisplay.innerHTML = '<span style="color:#888;font-size:11px;">No units selected</span>';
            return;
        }

        const names = allUnitsData
            .filter(u => selectedUnitIds.has(u.id))
            .map(u => u.name);

        selDisplay.innerHTML = names.map(n =>
            `<span class="selected-unit-tag">${n}</span>`
        ).join(' ');
    }

    function getAllUnits() {
        return allUnitsData;
    }

    function update(units) {
        render(units);
    }

    function getMarker(unitId) {
        return unitMarkers[unitId] || null;
    }

    return { init, load, update, render, getMarker, toggleSelect, getSelectedIds, clearSelection, getAllUnits };
})();
