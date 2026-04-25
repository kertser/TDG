/**
 * app.js – Main entry point: initialize all modules.
 */
(async function () {
    'use strict';

    // Initialize i18n (translations) first — reads stored language preference
    KI18n.init();

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
    KReplay.init();

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
        try { KMapObjects.clearAll(); } catch(e) {}
        try { KTerrain.hide(); } catch(e) {}

        // Deactivate scenario builder if active (prevents double grid/units)
        try { if (KScenarioBuilder.isActive()) KScenarioBuilder.deactivate(); } catch(e) {}

        // Load grid overlay first (needed for map centering)
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
                // fitBounds (non-animated → synchronous) lets Leaflet pick
                // the correct zoom for any grid size, from tiny 300 m cells
                // to large 2 km grids.  Padding keeps labels visible.
                map.fitBounds(
                    [[minLat, minLng], [maxLat, maxLng]],
                    { padding: [40, 40], animate: false }
                );
                // Store center + computed zoom so the "⊕ Center" button works.
                KMap.setOperationCenter(centerLat, centerLng, map.getZoom());
            }
        } catch (err) {
            console.warn('Grid center error:', err);
        }

        // Setup overlays session context (needed before loadFromServer)
        KOverlays.setSession(sessionId, token);

        // Initialize orders panel (sync DOM setup, its internal fetches are fire-and-forget)
        try { KOrders.init(sessionId, token); } catch (err) { console.warn('Orders init error:', err); }

        // Set replay session context
        try { KReplay.setSession(sessionId, token); } catch(e) {}

        // Fire off non-blocking loads (terrain, map objects) — don't await
        try {
            KTerrain.setSession(sessionId);
            KTerrain.load(sessionId, token);
        } catch (err) { console.warn('Terrain load error:', err); }

        try {
            KMapObjects.setSession(sessionId);
            KMapObjects.loadDefinitions(sessionId);
            KMapObjects.load(sessionId, token);
        } catch (err) { console.warn('Map objects load error:', err); }

        // ── Parallel data loading ─────────────────────────
        // All of these are independent — run them concurrently
        const sessPromise = fetch(`/api/sessions/${sessionId}`, {
            headers: { 'Authorization': `Bearer ${token}` },
        }).then(r => r.ok ? r.json() : null).catch(() => null);

        const unitsPromise = (async () => {
            if (typeof KAdmin !== 'undefined' && KAdmin.isGodViewEnabled()) {
                await KAdmin.refreshMapUnits();
            } else {
                await KUnits.load(sessionId, token);
            }
        })().catch(err => console.warn('Units load error:', err));

        const results = await Promise.all([
            sessPromise,
            unitsPromise,
            KContacts.load(sessionId, token).catch(err => console.warn('Contacts load error:', err)),
            KOverlays.loadFromServer().catch(err => console.warn('Overlays load error:', err)),
            KEvents.load(sessionId, token).catch(err => console.warn('Events load error:', err)),
            KReports.load(sessionId, token).catch(err => console.warn('Reports load error:', err)),
        ]);

        // Apply session data (from parallel fetch)
        const sessData = results[0];
        if (sessData) {
            KMap.setGameTime(sessData.tick || 0, sessData.current_time || null);
        }

        // Update command panel meta (game time) now that clock is set
        try { KOrders.refreshMeta(); } catch(e) {}

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
            KEvents.addEvent(data);
            // Show combat impact visual effects
            if (data.event_type === 'combat' || data.event_type === 'unit_destroyed') {
                const p = data.payload || {};
                if (p.target_lat && p.target_lon) {
                    const impactType = data.event_type === 'unit_destroyed' ? 'artillery' : 'combat';
                    try { KMapObjects.showImpact(p.target_lat, p.target_lon, impactType); } catch(e) {}
                }
            }
        });

        KWebSocket.on('report_new', (data) => {
            KReports.addReport(data);
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
            // Clear pending orders — they've been executed by the tick
            try { KUnits.clearPendingOrders(); } catch(e) {}
            // Refresh command panel datetime
            try { KOrders.refreshMeta(); } catch(e) {}
            // Update turn button badge (delayed to allow DB commit to complete)
            try {
                const turnBtn = document.getElementById('turn-btn');
                if (turnBtn) turnBtn.classList.remove('has-pending');
                setTimeout(() => { try { KSessionUI.updateTurnBadge(); } catch(e) {} }, 1500);
            } catch(e) {}
            // Reload events for the new tick
            try { KEvents.load(sessionId, token); } catch(e) {}
            // Show combat impact visual effects
            if (data.combat_impacts && Array.isArray(data.combat_impacts)) {
                for (const imp of data.combat_impacts) {
                    let impactType;
                    if (imp.type === 'unit_destroyed') impactType = 'artillery';
                    else if (imp.is_artillery) impactType = 'artillery';
                    else impactType = 'combat';
                    try { KMapObjects.showImpact(imp.lat, imp.lon, impactType); } catch(e) {}
                }
            }
            // Check for game_finished events
            if (data.events && Array.isArray(data.events)) {
                for (const evt of data.events) {
                    if (evt.event_type === 'game_finished') {
                        const payload = evt.payload || {};
                        const summary = evt.text_summary || 'Game Over';
                        const winner = payload.winner;
                        const detail = payload.detail || '';
                        let msg = `🏁 ${summary}`;
                        if (winner) msg += `\n\n🏆 Winner: ${winner.toUpperCase()}`;
                        if (detail) msg += `\n${detail}`;
                        setTimeout(() => {
                            try { KDialogs.alert(msg); } catch(e) { alert(msg); }
                        }, 500);
                        KGameLog.addEntry(summary, 'important');
                    }
                }
            }
        });

        KGameLog.addEntry('Connected to session', 'info');

        // Update admin panel context
        KAdmin.updateSessionContext();

        // Load public chain of command
        KAdmin.loadPublicCoC();
    };
})();
