/**
 * map.js – Leaflet map initialization with multiple base layers,
 *          scale control, distance measurement, center-on-operation,
 *          and game clock display.
 *
 *          Middle-button (wheel click) drag → map panning.
 *          Left-click is reserved for tools / unit selection.
 */
const KMap = (() => {
    let map = null;
    let operationCenter = null;
    let operationZoom = 13;

    // ── Distance measurement state ──────────────────
    let measuring = false;
    let measurePoints = [];
    let measureGroup = null;
    let _previewLine = null;

    // ── Middle-button pan state ─────────────────────
    let _mdPanning = false;
    let _mdLastPt = null;

    // ── Game clock state ────────────────────────────
    let _clockEl = null;
    let _currentTick = 0;
    let _currentGameTime = null;

    function init(elementId = 'map', center = [49.0582, 4.49547], zoom = 13) {
        map = L.map(elementId, {
            center: center,
            zoom: zoom,
            zoomControl: true,
            contextmenu: false,
            dragging: false,          // ◄ disable default left-button drag
        });

        // Suppress browser context menu on the map container
        map.getContainer().addEventListener('contextmenu', (e) => e.preventDefault());

        // ── Middle-button map panning ──────────────────
        _initMiddleButtonPan();

        // ── Base layers ─────────────────────────────────
        const topoLayer = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
            maxZoom: 17,
        });

        const osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 19,
        });

        const esriSatLayer = L.tileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            attribution: '&copy; Esri, Maxar, Earthstar Geographics',
            maxZoom: 18,
        });

        // Default to topographic (shows contour lines / heights)
        topoLayer.addTo(map);

        // ── Layer control ───────────────────────────────
        const baseLayers = {
            '⛰ Topographic': topoLayer,
            '🗺 OpenStreetMap': osmLayer,
            '🛰 Satellite': esriSatLayer,
        };
        L.control.layers(baseLayers, {}, { position: 'topright' }).addTo(map);

        // ── Coordinate + Zoom info bar (bottom-left, on map) ──
        // Added BEFORE scale so scale stacks above it (Leaflet bottom-controls
        // use insertBefore → later-added = higher).
        const CoordInfoControl = L.Control.extend({
            options: { position: 'bottomleft' },
            onAdd: function () {
                const container = L.DomUtil.create('div', 'coord-info-control');
                container.innerHTML =
                    '<span id="snail-display" title="Snail address under cursor"></span>' +
                    '<span class="coord-sep">│</span>' +
                    '<span id="coord-display" title="Coordinates under cursor"></span>' +
                    '<span class="coord-sep">│</span>' +
                    '<span id="zoom-display" title="Current zoom level"></span>' +
                    '<span class="coord-sep terrain-sep" style="display:none">│</span>' +
                    '<span id="terrain-display" title="Terrain type under cursor"></span>' +
                    '<span class="coord-sep terrain-sep" style="display:none">│</span>' +
                    '<span id="elevation-display" title="Elevation under cursor"></span>';
                L.DomEvent.disableClickPropagation(container);
                return container;
            },
        });
        new CoordInfoControl().addTo(map);

        // ── Scale control (sits above coord bar) ────────────
        L.control.scale({
            metric: true,
            imperial: false,
            position: 'bottomleft',
            maxWidth: 150,
        }).addTo(map);

        // ── Game clock control (bottom-right) ───────────
        const GameClockControl = L.Control.extend({
            options: { position: 'bottomright' },
            onAdd: function () {
                const container = L.DomUtil.create('div', 'game-clock-control');
                container.innerHTML =
                    '<span class="game-clock-icon">🕐</span>' +
                    '<span id="game-clock-time" class="game-clock-time">--:--</span>' +
                    '<span id="game-clock-tick" class="game-clock-tick">Turn 0</span>';
                L.DomEvent.disableClickPropagation(container);
                return container;
            },
        });
        new GameClockControl().addTo(map);
        _clockEl = {
            time: document.getElementById('game-clock-time'),
            tick: document.getElementById('game-clock-tick'),
        };

        // ── Map control buttons are now in the topbar (index.html) ──

        // ── Measure layer group ─────────────────────────
        measureGroup = L.layerGroup().addTo(map);


        // ── Coordinate + Zoom display ───────────────────
        const zoomEl = document.getElementById('zoom-display');
        const coordEl = document.getElementById('coord-display');
        const terrainEl = document.getElementById('terrain-display');
        const elevationEl = document.getElementById('elevation-display');
        const terrainSeps = document.querySelectorAll('.terrain-sep');

        if (zoomEl) {
            zoomEl.textContent = `Z${map.getZoom()}`;
            map.on('zoomend', () => {
                zoomEl.textContent = `Z${map.getZoom()}`;
            });
        }

        if (coordEl) {
            map.on('mousemove', (e) => {
                coordEl.textContent = `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`;

                // Update terrain + elevation from cached terrain data (no API call)
                if (typeof KTerrain !== 'undefined' && terrainEl) {
                    const info = KTerrain.getTerrainAtPoint(e.latlng.lat, e.latlng.lng);
                    if (info) {
                        terrainEl.textContent = info.label;
                        terrainSeps.forEach(s => s.style.display = '');
                        if (elevationEl) {
                            elevationEl.textContent = info.elevation != null
                                ? `↑${Math.round(info.elevation)}m`
                                : '';
                            // Show/hide elevation separator based on whether we have elevation data
                            if (info.elevation == null && terrainSeps.length >= 2) {
                                terrainSeps[1].style.display = 'none';
                            }
                        }
                    } else {
                        terrainEl.textContent = '';
                        if (elevationEl) elevationEl.textContent = '';
                        terrainSeps.forEach(s => s.style.display = 'none');
                    }
                }
            });

            map.on('mouseout', () => {
                if (terrainEl) terrainEl.textContent = '';
                if (elevationEl) elevationEl.textContent = '';
                terrainSeps.forEach(s => s.style.display = 'none');
            });
        }

        return map;
    }

    // ── Middle-button (wheel click) pan ──────────────
    function _initMiddleButtonPan() {
        const container = map.getContainer();

        container.addEventListener('pointerdown', (e) => {
            if (e.button === 1) {
                e.preventDefault();
                e.stopPropagation();
                _mdPanning = true;
                _mdLastPt = { x: e.clientX, y: e.clientY };
                container.style.cursor = 'grabbing';
                container.setPointerCapture(e.pointerId);
            }
        });

        container.addEventListener('pointermove', (e) => {
            if (!_mdPanning) return;
            const dx = e.clientX - _mdLastPt.x;
            const dy = e.clientY - _mdLastPt.y;
            _mdLastPt = { x: e.clientX, y: e.clientY };
            map.panBy([-dx, -dy], { animate: false });
        });

        container.addEventListener('pointerup', (e) => {
            if (e.button === 1 && _mdPanning) {
                _mdPanning = false;
                container.style.cursor = '';
                container.releasePointerCapture(e.pointerId);
            }
        });

        container.addEventListener('auxclick', (e) => {
            if (e.button === 1) e.preventDefault();
        });
    }

    function getMap() { return map; }

    // ── Center on Operation ─────────────────────────
    function setOperationCenter(lat, lng, zoom) {
        operationCenter = [lat, lng];
        operationZoom = zoom || 13;
    }

    function centerOnOperation() {
        if (!map || !operationCenter) {
            console.warn('centerOnOperation: no map or no operationCenter set');
            return;
        }
        map.setView(operationCenter, operationZoom);
    }

    // ── Game Clock ──────────────────────────────────
    function setGameTime(tick, gameTimeISO) {
        _currentTick = tick;
        if (gameTimeISO) {
            _currentGameTime = new Date(gameTimeISO);
        }
        _updateClockDisplay();
    }

    function _updateClockDisplay() {
        if (!_clockEl) return;
        if (_clockEl.tick) {
            _clockEl.tick.textContent = `Turn ${_currentTick}`;
        }
        if (_clockEl.time) {
            if (_currentGameTime) {
                const h = String(_currentGameTime.getUTCHours()).padStart(2, '0');
                const m = String(_currentGameTime.getUTCMinutes()).padStart(2, '0');
                const dateStr = _currentGameTime.toISOString().split('T')[0];
                _clockEl.time.textContent = `${dateStr} ${h}:${m}`;
            } else {
                _clockEl.time.textContent = '--:--';
            }
        }
    }

    // ── Distance Measurement Tool ───────────────────
    function startMeasure() {
        if (measuring) stopMeasure();
        measuring = true;
        measurePoints = [];
        measureGroup.clearLayers();
        _previewLine = null;
        map.getContainer().style.cursor = 'crosshair';

        map.on('click', _onMeasureClick);
        map.on('mousemove', _onMeasureMouseMove);
        map.on('contextmenu', _onMeasureRightClick);
        map.on('keydown', _onMeasureKeyDown);
    }

    function stopMeasure() {
        measuring = false;
        map.getContainer().style.cursor = '';
        map.off('click', _onMeasureClick);
        map.off('mousemove', _onMeasureMouseMove);
        map.off('contextmenu', _onMeasureRightClick);
        map.off('keydown', _onMeasureKeyDown);
        if (_previewLine) {
            measureGroup.removeLayer(_previewLine);
            _previewLine = null;
        }
        // After measurement is done, make layers right-clickable for deletion
        _enableMeasureDelete();
    }

    function clearMeasure() {
        stopMeasure();
        measureGroup.clearLayers();
        measurePoints = [];
    }

    /** Make finished measurement layers interactive for right-click deletion. */
    function _enableMeasureDelete() {
        if (measureGroup.getLayers().length === 0) return;

        measureGroup.eachLayer(layer => {
            // Make segments and markers interactive for right-click
            if (layer instanceof L.CircleMarker || layer instanceof L.Polyline) {
                layer.options.interactive = true;
                if (layer._path) layer._path.style.pointerEvents = 'auto';
                layer.on('contextmenu', _onMeasureLayerRightClick);
            }
        });
    }

    function _onMeasureLayerRightClick(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        // Clear all measurements on right-click
        measureGroup.clearLayers();
        measurePoints = [];
    }

    function _onMeasureClick(e) {
        if (!measuring) return;
        measurePoints.push(e.latlng);

        const marker = L.circleMarker(e.latlng, {
            radius: 4, color: '#ffd740', fillColor: '#ffd740',
            fillOpacity: 1, weight: 2,
        });
        measureGroup.addLayer(marker);

        if (measurePoints.length > 1) {
            const prev = measurePoints[measurePoints.length - 2];
            const curr = measurePoints[measurePoints.length - 1];

            const segment = L.polyline([prev, curr], {
                color: '#ffd740', weight: 2, dashArray: '6,6', opacity: 0.9,
            });
            measureGroup.addLayer(segment);

            const segDist = map.distance(prev, curr);
            const midLat = (prev.lat + curr.lat) / 2;
            const midLng = (prev.lng + curr.lng) / 2;
            const label = L.tooltip({
                permanent: true, direction: 'center', className: 'measure-label',
            }).setLatLng([midLat, midLng]).setContent(_formatDist(segDist));
            measureGroup.addLayer(label);

            const totalDist = _totalDistance();
            const totalLabel = L.tooltip({
                permanent: true, direction: 'top',
                className: 'measure-total-label', offset: [0, -10],
            }).setLatLng(curr).setContent(`Σ ${_formatDist(totalDist)}`);
            measureGroup.addLayer(totalLabel);
        }
    }

    function _onMeasureMouseMove(e) {
        if (!measuring || measurePoints.length === 0) return;
        const last = measurePoints[measurePoints.length - 1];
        if (_previewLine) {
            _previewLine.setLatLngs([last, e.latlng]);
        } else {
            _previewLine = L.polyline([last, e.latlng], {
                color: '#ffd740', weight: 1, dashArray: '4,4', opacity: 0.5,
            });
            measureGroup.addLayer(_previewLine);
        }
    }

    function _onMeasureRightClick(e) {
        if (!measuring) return;
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);

        if (measurePoints.length >= 2) {
            const totalDist = _totalDistance();
            const last = measurePoints[measurePoints.length - 1];
            const finalLabel = L.tooltip({
                permanent: true, direction: 'top',
                className: 'measure-final-label', offset: [0, -14],
            }).setLatLng(last).setContent(`✓ ${_formatDist(totalDist)}`);
            measureGroup.addLayer(finalLabel);
        }
        stopMeasure();
    }

    function _onMeasureKeyDown(e) {
        if (e.originalEvent.key === 'Escape') clearMeasure();
    }

    function _totalDistance() {
        let total = 0;
        for (let i = 1; i < measurePoints.length; i++) {
            total += map.distance(measurePoints[i - 1], measurePoints[i]);
        }
        return total;
    }

    function _formatDist(meters) {
        if (meters >= 1000) return (meters / 1000).toFixed(2) + ' km';
        return Math.round(meters) + ' m';
    }

    function isMeasuring() { return measuring; }

    return {
        init, getMap,
        setOperationCenter, centerOnOperation,
        setGameTime,
        startMeasure, stopMeasure, clearMeasure, isMeasuring,
    };
})();
