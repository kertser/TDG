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
    let _measureDismissHandler = null;  // click-anywhere-to-dismiss after finalize

    // ── LOS check state ──────────────────────────────
    let _losChecking = false;
    let _losPoints = [];
    let _losGroup = null;
    let _losPreviewLine = null;

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
                const _t = typeof KI18n !== 'undefined' ? KI18n.t.bind(KI18n) : (k) => k;
                const container = L.DomUtil.create('div', 'coord-info-control');
                container.innerHTML =
                    `<span id="snail-display" data-i18n-title="tip.snail_display" title="${_t('tip.snail_display')}"></span>` +
                    '<span class="coord-sep">│</span>' +
                    `<span id="coord-display" data-i18n-title="tip.coord_display" title="${_t('tip.coord_display')}"></span>` +
                    '<span class="coord-sep">│</span>' +
                    `<span id="zoom-display" data-i18n-title="tip.zoom_display" title="${_t('tip.zoom_display')}"></span>` +
                    '<span class="coord-sep terrain-sep" style="display:none">│</span>' +
                    `<span id="terrain-display" data-i18n-title="tip.terrain_display" title="${_t('tip.terrain_display')}"></span>` +
                    '<span class="coord-sep terrain-sep" style="display:none">│</span>' +
                    `<span id="elevation-display" data-i18n-title="tip.elevation_display" title="${_t('tip.elevation_display')}"></span>`;
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
                const _t = typeof KI18n !== 'undefined' ? KI18n.t.bind(KI18n) : (k) => k;
                const container = L.DomUtil.create('div', 'game-clock-control');
                container.innerHTML =
                    '<span class="game-clock-icon">🕐</span>' +
                    '<span id="game-clock-time" class="game-clock-time">--:--</span>' +
                    `<span id="game-clock-tick" class="game-clock-tick">${_t('clock.turn')} 0</span>`;
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

        // ── LOS Check layer group (on high-z pane so it overlays units/objects) ──
        map.createPane('losPane');
        map.getPane('losPane').style.zIndex = 700;
        map.getPane('losPane').style.pointerEvents = 'none';  // clicks pass through
        _losGroup = L.layerGroup({ pane: 'losPane' }).addTo(map);


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
            // Ensure timezone — treat naive datetimes as UTC
            let iso = String(gameTimeISO);
            if (!iso.endsWith('Z') && !iso.includes('+') && !/\d{2}:\d{2}$/.test(iso.slice(-6))) {
                iso += 'Z';
            }
            _currentGameTime = new Date(iso);
        }
        _updateClockDisplay();
    }

    /** Parse game time as UTC and return {date, h, m, s} without timezone conversion. */
    function _parseGameTimeUTC(dt) {
        const h = String(dt.getUTCHours()).padStart(2, '0');
        const m = String(dt.getUTCMinutes()).padStart(2, '0');
        const s = String(dt.getUTCSeconds()).padStart(2, '0');
        const dateStr = dt.toISOString().split('T')[0];
        return { dateStr, h, m, s };
    }

    function _updateClockDisplay() {
        if (!_clockEl) return;
        if (_clockEl.tick) {
            const _turnLabel = typeof KI18n !== 'undefined' ? KI18n.t('clock.turn') : 'Turn';
            _clockEl.tick.textContent = `${_turnLabel} ${_currentTick}`;
        }
        if (_clockEl.time) {
            if (_currentGameTime) {
                const { dateStr, h, m } = _parseGameTimeUTC(_currentGameTime);
                _clockEl.time.textContent = `${dateStr} ${h}:${m}`;
                // Store ISO string (always UTC) for other modules to access
                _clockEl.time.dataset.isoTime = _currentGameTime.toISOString();
            } else {
                _clockEl.time.textContent = '--:--';
                _clockEl.time.dataset.isoTime = '';
            }
        }
    }

    // ── Distance Measurement Tool ───────────────────
    function startMeasure() {
        if (measuring) stopMeasure();
        if (_losChecking) stopLOSCheck();
        measuring = true;
        measurePoints = [];
        measureGroup.clearLayers();
        _previewLine = null;
        // Remove any previous dismiss handler
        if (_measureDismissHandler) {
            map.off('click', _measureDismissHandler);
            _measureDismissHandler = null;
        }
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
        if (_measureDismissHandler) {
            map.off('click', _measureDismissHandler);
            _measureDismissHandler = null;
        }
    }

    /** Make finished measurement layers interactive for right-click deletion,
     *  and register a click-anywhere-to-dismiss handler. */
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

        // Click anywhere on the map to dismiss the measurement result
        _measureDismissHandler = () => {
            measureGroup.clearLayers();
            measurePoints = [];
            map.off('click', _measureDismissHandler);
            _measureDismissHandler = null;
        };
        // Delay to avoid the finalizing right-click from immediately triggering
        setTimeout(() => {
            if (_measureDismissHandler) {
                map.on('click', _measureDismissHandler);
            }
        }, 300);
    }

    function _onMeasureLayerRightClick(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        // Clear all measurements on right-click
        measureGroup.clearLayers();
        measurePoints = [];
        // Also remove the click-to-dismiss handler
        if (_measureDismissHandler) {
            map.off('click', _measureDismissHandler);
            _measureDismissHandler = null;
        }
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

    // ── LOS (Line-of-Sight) Check Tool ──────────────

    let _losResultDismissHandler = null;  // stored so we can remove it

    function startLOSCheck() {
        if (_losChecking) stopLOSCheck();
        _losChecking = true;
        _losPoints = [];
        if (!_losGroup) {
            _losGroup = L.layerGroup({ pane: 'losPane' }).addTo(map);
        }
        _losGroup.clearLayers();
        _losPreviewLine = null;
        // Remove any previous dismiss handler
        if (_losResultDismissHandler) {
            map.off('click', _losResultDismissHandler);
            _losResultDismissHandler = null;
        }
        document.body.classList.add('map-los-checking');
        map.getContainer().style.cursor = 'crosshair';
        map.on('click', _onLOSClick);
        map.on('mousemove', _onLOSMouseMove);
        map.on('contextmenu', _onLOSRightClick);
        document.addEventListener('keydown', _onLOSKeyDown);
    }

    function stopLOSCheck() {
        _losChecking = false;
        document.body.classList.remove('map-los-checking');
        map.getContainer().style.cursor = '';
        map.off('click', _onLOSClick);
        map.off('mousemove', _onLOSMouseMove);
        map.off('contextmenu', _onLOSRightClick);
        document.removeEventListener('keydown', _onLOSKeyDown);
        if (_losPreviewLine && _losGroup) {
            _losGroup.removeLayer(_losPreviewLine);
            _losPreviewLine = null;
        }
        // Results live on a non-interactive pane (losPane with pointer-events:none).
        // They will be dismissed by click-to-dismiss handler set after results load.
    }

    function clearLOSCheck() {
        stopLOSCheck();
        if (_losResultDismissHandler) {
            map.off('click', _losResultDismissHandler);
            _losResultDismissHandler = null;
        }
        if (_losGroup) _losGroup.clearLayers();
        _losPoints = [];
    }

    function _onLOSClick(e) {
        if (!_losChecking) return;
        _losPoints.push(e.latlng);

        // Draw point marker (on losPane)
        const color = _losPoints.length === 1 ? '#4fc3f7' : '#ffb74d';
        const marker = L.circleMarker(e.latlng, {
            radius: 6, color: color, fillColor: color,
            fillOpacity: 0.9, weight: 2, interactive: false, pane: 'losPane',
        });
        _losGroup.addLayer(marker);

        if (_losPoints.length === 2) {
            // Have both points — run LOS check
            const p1 = _losPoints[0];
            const p2 = _losPoints[1];

            // Draw the line immediately (pending result)
            const pendingLine = L.polyline([p1, p2], {
                color: '#aaa', weight: 2.5, dashArray: '6,4', opacity: 0.6,
                interactive: false, pane: 'losPane',
            });
            _losGroup.addLayer(pendingLine);

            // Show loading label at midpoint
            const midLat = (p1.lat + p2.lat) / 2;
            const midLng = (p1.lng + p2.lng) / 2;
            const loadingLabel = L.tooltip({
                permanent: true, direction: 'center', className: 'los-label-pending',
                pane: 'losPane',
            }).setLatLng([midLat, midLng]).setContent('⏳ Checking LOS…');
            _losGroup.addLayer(loadingLabel);

            // Call API
            const sid = typeof KSessionUI !== 'undefined' ? KSessionUI.getSessionId() : null;
            const token = typeof KSessionUI !== 'undefined' ? KSessionUI.getToken() : null;
            if (sid && token) {
                const url = `/api/sessions/${sid}/los-check?from_lat=${p1.lat}&from_lon=${p1.lng}&to_lat=${p2.lat}&to_lon=${p2.lng}`;
                fetch(url, { headers: { 'Authorization': `Bearer ${token}` } })
                    .then(r => r.json())
                    .then(data => {
                        // Clear pending visuals
                        _losGroup.clearLayers();

                        const hasLOS = data.has_los;
                        const dist = data.distance_m || 0;
                        const lineColor = hasLOS ? '#66bb6a' : '#ef5350';
                        const accentColor = hasLOS ? '#a5d6a7' : '#ef9a9a';

                        // ── Endpoint markers (ring + dot) ──
                        [p1, p2].forEach((pt, i) => {
                            // Outer ring glow
                            _losGroup.addLayer(L.circleMarker(pt, {
                                radius: 10, color: lineColor, fillColor: 'transparent',
                                fillOpacity: 0, weight: 1.5, opacity: 0.3,
                                interactive: false, pane: 'losPane',
                            }));
                            // Inner filled dot
                            _losGroup.addLayer(L.circleMarker(pt, {
                                radius: 5,
                                color: i === 0 ? '#4fc3f7' : '#ffb74d',
                                fillColor: i === 0 ? '#4fc3f7' : '#ffb74d',
                                fillOpacity: 0.9, weight: 2, opacity: 1,
                                interactive: false, pane: 'losPane',
                            }));
                        });

                        // ── Result line (soft glow + crisp inner) ──
                        // Wide soft glow
                        _losGroup.addLayer(L.polyline([p1, p2], {
                            color: lineColor, weight: 8, opacity: 0.12,
                            lineCap: 'round',
                            interactive: false, pane: 'losPane',
                        }));
                        // Medium glow
                        _losGroup.addLayer(L.polyline([p1, p2], {
                            color: lineColor, weight: 4, opacity: 0.25,
                            lineCap: 'round',
                            interactive: false, pane: 'losPane',
                        }));
                        // Inner crisp line
                        _losGroup.addLayer(L.polyline([p1, p2], {
                            color: accentColor, weight: 2, opacity: 0.85,
                            dashArray: hasLOS ? null : '10,6',
                            lineCap: 'round',
                            interactive: false, pane: 'losPane',
                        }));

                        // ── Build result text (compact, icon-based) ──
                        let heading = hasLOS ? '✓ Clear' : '✕ Blocked';
                        let details = _formatDist(dist);
                        if (data.from_elevation_m != null && data.to_elevation_m != null) {
                            const diff = data.to_elevation_m - data.from_elevation_m;
                            const arrow = diff > 0 ? '↗' : diff < 0 ? '↘' : '→';
                            details += `   ${data.from_elevation_m}m ${arrow} ${data.to_elevation_m}m`;
                        }
                        let blockInfo = '';
                        if (!hasLOS && data.blocking_terrain) {
                            blockInfo = `${data.blocking_terrain}`;
                            if (data.blocking_elevation_m != null) blockInfo += ` ${data.blocking_elevation_m}m`;
                        }

                        const resultLabel = L.tooltip({
                            permanent: true, direction: 'top',
                            className: hasLOS ? 'los-label-clear' : 'los-label-blocked',
                            offset: [0, -12],
                            pane: 'losPane',
                        }).setLatLng([midLat, midLng]).setContent(
                            `<span class="los-heading">${heading}</span>` +
                            `<span class="los-details">${details}</span>` +
                            (blockInfo ? `<span class="los-block-info">${blockInfo}</span>` : '')
                        );
                        _losGroup.addLayer(resultLabel);

                        // ── Click anywhere to dismiss result ──
                        _losResultDismissHandler = () => {
                            if (_losGroup) _losGroup.clearLayers();
                            _losPoints = [];
                            map.off('click', _losResultDismissHandler);
                            _losResultDismissHandler = null;
                        };
                        // Delay to avoid immediate dismiss from the same click
                        setTimeout(() => {
                            if (_losResultDismissHandler) {
                                map.on('click', _losResultDismissHandler);
                            }
                        }, 300);
                    })
                    .catch(err => {
                        _losGroup.clearLayers();
                        const errLabel = L.tooltip({
                            permanent: true, direction: 'center', className: 'los-label-blocked',
                            pane: 'losPane',
                        }).setLatLng([midLat, midLng]).setContent('⚠ LOS check failed');
                        _losGroup.addLayer(errLabel);
                        // Also dismiss on click
                        _losResultDismissHandler = () => {
                            if (_losGroup) _losGroup.clearLayers();
                            _losPoints = [];
                            map.off('click', _losResultDismissHandler);
                            _losResultDismissHandler = null;
                        };
                        setTimeout(() => {
                            if (_losResultDismissHandler) map.on('click', _losResultDismissHandler);
                        }, 300);
                    });
            }

            // Stop the tool (2 points collected)
            stopLOSCheck();
            // Clear active button state
            document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
        }
    }

    function _onLOSMouseMove(e) {
        if (!_losChecking || _losPoints.length === 0) return;
        const last = _losPoints[_losPoints.length - 1];
        if (_losPreviewLine) {
            _losPreviewLine.setLatLngs([last, e.latlng]);
        } else {
            _losPreviewLine = L.polyline([last, e.latlng], {
                color: '#4fc3f7', weight: 1.5, dashArray: '4,4', opacity: 0.5,
                interactive: false, pane: 'losPane',
            });
            _losGroup.addLayer(_losPreviewLine);
        }
    }

    function _onLOSRightClick(e) {
        if (!_losChecking) return;
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        clearLOSCheck();
        document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
    }

    function _onLOSKeyDown(e) {
        if (e.key === 'Escape') {
            clearLOSCheck();
            document.querySelectorAll('.draw-btn').forEach(b => b.classList.remove('active'));
        }
    }

    function isLOSChecking() { return _losChecking; }

    return {
        init, getMap,
        setOperationCenter, centerOnOperation,
        setGameTime,
        startMeasure, stopMeasure, clearMeasure, isMeasuring,
        startLOSCheck, stopLOSCheck, clearLOSCheck, isLOSChecking,
    };
})();
