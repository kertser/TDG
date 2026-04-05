/**
 * app.js – Main entry point: initialize all modules.
 */
(function () {
    'use strict';

    // Initialize map — default center near Reims area (updated per scenario)
    const map = KMap.init('map', [49.0582, 4.49547], 13);

    // Initialize UI
    KUI.init();
    KSessionUI.init();
    KAdmin.init();

    // Initialize map layers
    KUnits.init(map);
    KContacts.init(map);
    KOverlays.init(map);

    // Callback when user joins a session
    window.onSessionJoined = async (sessionId, token) => {
        console.log('Joined session:', sessionId);

        // Load grid overlay
        await KGrid.load(map, sessionId);
        KGrid.setupMouseTracker(map);

        // Compute operation center from grid bounding box
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
            // Fly to operation area immediately
            map.setView([centerLat, centerLng], 13);
        }

        // Load units
        await KUnits.load(sessionId, token);

        // Load contacts
        await KContacts.load(sessionId, token);

        // Setup overlays for this session
        KOverlays.setSession(sessionId, token);
        await KOverlays.loadFromServer();

        // Initialize orders panel
        KOrders.init(sessionId, token);

        // Load events
        await KEvents.load(sessionId, token);

        // Connect WebSocket
        KWebSocket.connect(sessionId, token);

        // Register WS handlers
        KWebSocket.on('state_update', (data) => {
            if (data.units) KUnits.update(data.units);
            if (data.contacts) KContacts.render(data.contacts);
        });

        KWebSocket.on('overlay_created', (data) => {
            KOverlays.onOverlayCreated(data);
            KGameLog.addEntry('Overlay created', 'info');
        });

        KWebSocket.on('overlay_updated', (data) => {
            KOverlays.onOverlayUpdated(data);
        });

        KWebSocket.on('overlay_deleted', (data) => {
            KOverlays.onOverlayDeleted(data);
            KGameLog.addEntry('Overlay deleted', 'info');
        });

        KWebSocket.on('order_status', (data) => {
            KGameLog.addEntry(`Order [${data.status}]: ${data.original_text || data.id}`, 'order');
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

        KWebSocket.on('tick_update', (data) => {
            KGameLog.addEntry(`Tick ${data.tick}`, 'info');
        });

        KGameLog.addEntry('Connected to session', 'info');

        // Update admin panel context
        KAdmin.updateSessionContext();
    };
})();
