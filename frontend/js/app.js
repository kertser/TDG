/**
 * app.js – Main entry point: initialize all modules.
 */
(async function () {
    'use strict';

    // Initialize map — default center near Reims area (updated per scenario)
    const map = KMap.init('map', [49.0582, 4.49547], 13);

    // Initialize UI
    KUI.init();
    KUI.addMapControls(map);
    KSessionUI.init();
    await KScenarioBuilder.init(map);  // async — loads unit_types.json
    KAdmin.init();

    // Initialize map layers
    await KUnits.init(map);  // async — loads units_config.json + builds type maps
    KContacts.init(map);
    KOverlays.init(map);
    KTerrain.init(map);
    KMapObjects.init(map);

    // Fetch and display app version
    fetch('/api/version').then(r => r.json()).then(data => {
        const vEl = document.getElementById('app-version');
        if (vEl && data.version) vEl.textContent = `v${data.version}`;
    }).catch(() => {});

    // Callback when user joins a session
    window.onSessionJoined = async (sessionId, token) => {
        console.log('Joined session:', sessionId);

        // Clear existing layers to prevent duplicates
        try { KGrid.clearAll(); } catch(e) {}
        try { KUnits.clearAll(); } catch(e) {}
        try { KContacts.clearAll(); } catch(e) {}

        // Deactivate scenario builder if active (prevents double grid/units)
        try { if (KScenarioBuilder.isActive()) KScenarioBuilder.deactivate(); } catch(e) {}

        // Load grid overlay
        try {
            await KGrid.load(map, sessionId);
            KGrid.setupMouseTracker(map);
        } catch (err) {
            console.warn('Grid load error:', err);
        }

        // Compute operation center from grid bounding box
        try {
            const gridGJ = KGrid.getGridGeoJson();
            if (gridGJ && gridGJ.features && gridGJ.features.length > 0) {
                let minLat = 90, maxLat = -90, minLng = 180, maxLng = -180;
                gridGJ.features.forEach(f => {
                    if (f.geometry && f.geometry.coordinates) {
                        f.geometry.coordinates[0].forEach(c => {
                            if (c[1] < minLat) minLat = c[1];
                            if (c[1] > maxLat) maxLat = c[1];
                            if (c[0] < minLng) minLng = c[0];
                            if (c[0] > maxLng) maxLng = c[0];
                        });
                    }
                });
                const centerLat = (minLat + maxLat) / 2;
                const centerLng = (minLng + maxLng) / 2;
                KMap.setOperationCenter(centerLat, centerLng, 13);
                map.setView([centerLat, centerLng], 13);
            }
        } catch (err) {
            console.warn('Grid center error:', err);
        }

        // Fetch session data to initialize game clock
        try {
            const sessResp = await fetch(`/api/sessions/${sessionId}`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (sessResp.ok) {
                const sessData = await sessResp.json();
                KMap.setGameTime(sessData.tick || 0, sessData.current_time || null);
            }
        } catch (err) {
            console.warn('Failed to fetch session for clock:', err);
        }

        // Load units
        try { await KUnits.load(sessionId, token); } catch (err) { console.warn('Units load error:', err); }

        // Load contacts
        try { await KContacts.load(sessionId, token); } catch (err) { console.warn('Contacts load error:', err); }

        // Setup overlays for this session
        KOverlays.setSession(sessionId, token);
        try { await KOverlays.loadFromServer(); } catch (err) { console.warn('Overlays load error:', err); }

        // Load terrain overlay (if analyzed) — non-blocking, don't hold up WS/events
        try {
            KTerrain.setSession(sessionId);
            KTerrain.load(sessionId, token);  // intentionally not awaited
        } catch (err) { console.warn('Terrain load error:', err); }

        // Load map objects (obstacles, structures)
        try {
            KMapObjects.setSession(sessionId);
            KMapObjects.loadDefinitions(sessionId);
            KMapObjects.load(sessionId, token);
        } catch (err) { console.warn('Map objects load error:', err); }

        // Initialize orders panel
        try { KOrders.init(sessionId, token); } catch (err) { console.warn('Orders init error:', err); }

        // Load events
        try { await KEvents.load(sessionId, token); } catch (err) { console.warn('Events load error:', err); }

        // Connect WebSocket
        KWebSocket.connect(sessionId, token);

        // Register WS handlers
        KWebSocket.on('state_update', (data) => {
            // If god view is enabled, re-fetch all units from admin endpoint
            // instead of using fog-of-war filtered data
            if (KAdmin.isGodViewEnabled()) {
                KAdmin.onStateUpdate(data);
            } else {
                if (data.units) KUnits.update(data.units, data.tick);
            }
            if (data.contacts) KContacts.render(data.contacts);
            // Update game clock from state update
            if (data.tick !== undefined) {
                KMap.setGameTime(data.tick, data.game_time || null);
                // Invalidate viewshed cache on tick change
                KUnits.invalidateViewshedCache(data.tick);
            }
        });

        KWebSocket.on('overlay_created', (data) => {
            KOverlays.onOverlayCreated(data);
        });

        KWebSocket.on('overlay_updated', (data) => {
            KOverlays.onOverlayUpdated(data);
        });

        KWebSocket.on('overlay_deleted', (data) => {
            KOverlays.onOverlayDeleted(data);
        });

        KWebSocket.on('order_status', (data) => {
            KGameLog.addEntry(`Order [${data.status}]: ${data.original_text || data.id}`, 'order');
            KOrders.onOrderStatus(data);
        });

        KWebSocket.on('chat_message', (data) => {
            KOrders.onChatMessage(data);
        });

        KWebSocket.on('participant_joined', (data) => {
            KGameLog.addEntry(`${data.display_name} joined (${data.side})`, 'info');
        });

        KWebSocket.on('participant_left', () => {
            KGameLog.addEntry(`Player left`, 'info');
        });

        KWebSocket.on('event_new', (data) => {
            KGameLog.addEntry(data.text_summary || data.event_type, 'event');
            KEvents.addEvent(data);
        });

        KWebSocket.on('report_new', (data) => {
            KGameLog.addEntry(`[${data.channel}] ${data.text}`, 'report');
        });

        KWebSocket.on('map_object_created', (data) => {
            KMapObjects.onObjectCreated(data);
        });

        KWebSocket.on('map_object_updated', (data) => {
            KMapObjects.onObjectUpdated(data);
        });

        KWebSocket.on('map_object_deleted', (data) => {
            KMapObjects.onObjectDeleted(data);
        });

        KWebSocket.on('tick_update', (data) => {
            KGameLog.addEntry(`Turn ${data.tick}`, 'info');
            // Update game clock on turn
            KMap.setGameTime(data.tick, data.game_time || null);
            // Refresh command panel datetime
            try { KOrders.refreshMeta(); } catch(e) {}
        });

        KGameLog.addEntry('Connected to session', 'info');

        // Update admin panel context
        KAdmin.updateSessionContext();

        // Load public chain of command
        KAdmin.loadPublicCoC();
    };
})();
