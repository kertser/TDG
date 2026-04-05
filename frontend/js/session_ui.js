/**
 * session_ui.js – Session lobby: register/login/logout, create/list/join/start sessions,
 *                 admin controls (delete all sessions).
 */
const KSessionUI = (() => {
    let currentToken = null;
    let currentUserId = null;
    let currentUserName = null;
    let currentSessionId = null;

    function getToken() { return currentToken; }
    function getUserId() { return currentUserId; }
    function getSessionId() { return currentSessionId; }

    async function init() {
        const registerBtn = document.getElementById('register-btn');
        const nameInput = document.getElementById('display-name-input');
        const logoutBtn = document.getElementById('logout-btn');
        const createBtn = document.getElementById('create-session-btn');
        const startBtn = document.getElementById('start-session-btn');
        const tickBtn = document.getElementById('tick-btn');

        registerBtn.addEventListener('click', () => _doLogin(nameInput.value.trim()));
        nameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') _doLogin(nameInput.value.trim());
        });

        if (logoutBtn) {
            logoutBtn.addEventListener('click', _doLogout);
        }

        createBtn.addEventListener('click', _createSession);


        if (startBtn) {
            startBtn.addEventListener('click', async () => {
                if (!currentSessionId || !currentToken) return;
                try {
                    const resp = await fetch(`/api/sessions/${currentSessionId}/start`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${currentToken}` },
                    });
                    const data = await resp.json();
                    KGameLog.addEntry(`Session started (tick ${data.tick})`, 'info');
                    startBtn.style.display = 'none';
                    if (tickBtn) tickBtn.style.display = 'block';

                    // Reload grid + units + contacts after session start
                    // (grid & units are created on start from scenario data)
                    const map = KMap.getMap();
                    await KGrid.load(map, currentSessionId);

                    // Center map on grid area
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
                        const cLat = (minLat + maxLat) / 2;
                        const cLng = (minLng + maxLng) / 2;
                        KMap.setOperationCenter(cLat, cLng, 13);
                        map.setView([cLat, cLng], 13);
                    }

                    await KUnits.load(currentSessionId, currentToken);
                    await KContacts.load(currentSessionId, currentToken);
                } catch (err) {
                    console.error('Start session failed:', err);
                }
            });
        }

        if (tickBtn) {
            tickBtn.addEventListener('click', async () => {
                if (!currentSessionId || !currentToken) return;
                try {
                    const resp = await fetch(`/api/sessions/${currentSessionId}/tick`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${currentToken}` },
                    });
                    const data = await resp.json();
                    KGameLog.addEntry(
                        `Tick ${data.tick}: ${data.events_count} events, ${data.units_alive} alive`,
                        'info'
                    );
                    // Reload units and contacts after tick
                    await KUnits.load(currentSessionId, currentToken);
                    await KContacts.load(currentSessionId, currentToken);
                    await KEvents.load(currentSessionId, currentToken);
                } catch (err) {
                    console.error('Tick failed:', err);
                }
            });
        }
    }

    async function _doLogin(name) {
        if (!name) return;

        try {
            // Try login first, then register
            let resp = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: name }),
            });
            if (!resp.ok) {
                resp = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ display_name: name }),
                });
            }
            const data = await resp.json();
            currentToken = data.token;
            currentUserId = data.user_id;
            currentUserName = data.display_name;

            document.getElementById('user-info').textContent = `👤 ${data.display_name}`;
            document.getElementById('auth-panel').style.display = 'none';
            document.getElementById('session-panel').style.display = 'block';

            // Show logout button
            const logoutBtn = document.getElementById('logout-btn');
            if (logoutBtn) logoutBtn.style.display = 'inline-block';

            loadSessions();
        } catch (err) {
            console.error('Auth failed:', err);
        }
    }

    function _doLogout() {
        // Disconnect WebSocket
        KWebSocket.disconnect();

        // Reset state
        currentToken = null;
        currentUserId = null;
        currentUserName = null;
        currentSessionId = null;

        // Reset UI
        document.getElementById('user-info').textContent = '';
        document.getElementById('auth-panel').style.display = 'block';
        document.getElementById('session-panel').style.display = 'none';
        document.getElementById('session-info').textContent = '';
        document.getElementById('display-name-input').value = '';

        const logoutBtn = document.getElementById('logout-btn');
        if (logoutBtn) logoutBtn.style.display = 'none';

        // Hide toolbar and session controls
        const drawToolbar = document.getElementById('draw-toolbar');
        if (drawToolbar) drawToolbar.style.display = 'none';
        const centerBtn = document.getElementById('center-btn');
        if (centerBtn) centerBtn.style.display = 'none';
        const gridToggleBtn = document.getElementById('grid-toggle-btn');
        if (gridToggleBtn) gridToggleBtn.style.display = 'none';
        const unitsToggleBtn = document.getElementById('units-toggle-btn');
        if (unitsToggleBtn) unitsToggleBtn.style.display = 'none';
        const overlaysToggleBtn = document.getElementById('overlays-toggle-btn');
        if (overlaysToggleBtn) overlaysToggleBtn.style.display = 'none';

        // Reset game clock
        KMap.setGameTime(0, null);

        const startBtn = document.getElementById('start-session-btn');
        const tickBtn = document.getElementById('tick-btn');
        if (startBtn) startBtn.style.display = 'none';
        if (tickBtn) tickBtn.style.display = 'none';

        // Clear session list
        const listEl = document.getElementById('session-list');
        if (listEl) listEl.innerHTML = '';
    }

    async function _createSession() {
        if (!currentToken) return;
        try {
            // First ensure a scenario exists
            let scenResp = await fetch('/api/scenarios');
            let scenarios = await scenResp.json();

            if (scenarios.length === 0) {
                // Create a default scenario with units in the Reims operational area
                scenResp = await fetch('/api/scenarios', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: 'Training Exercise Alpha',
                        description: 'Combined-arms training exercise near Reims area',
                        map_center_lat: 49.0582,
                        map_center_lon: 4.49547,
                        map_zoom: 13,
                        grid_settings: {
                            origin_lat: 49.025,
                            origin_lon: 4.44,
                            orientation_deg: 0,
                            base_square_size_m: 1000,
                            columns: 8,
                            rows: 8,
                            labeling_scheme: 'alphanumeric',
                        },
                        initial_units: {
                            blue: [
                                { name: '1st Platoon, A Company', unit_type: 'infantry_platoon', sidc: '10031000151211000000', lat: 49.035, lon: 4.465, strength: 1.0, ammo: 1.0, morale: 0.9, move_speed_mps: 4.0, detection_range_m: 1500, capabilities: { has_atgm: false } },
                                { name: '2nd Platoon, A Company', unit_type: 'infantry_platoon', sidc: '10031000151211000000', lat: 49.035, lon: 4.475, strength: 1.0, ammo: 1.0, morale: 0.9, move_speed_mps: 4.0, detection_range_m: 1500, capabilities: { has_atgm: false } },
                                { name: '3rd Platoon, A Company', unit_type: 'infantry_platoon', sidc: '10031000151211000000', lat: 49.033, lon: 4.470, strength: 1.0, ammo: 1.0, morale: 0.9, move_speed_mps: 4.0, detection_range_m: 1500, capabilities: { has_atgm: false } },
                                { name: 'Mortar Section', unit_type: 'mortar_section', sidc: '10031000151215000000', lat: 49.032, lon: 4.468, strength: 1.0, ammo: 1.0, morale: 0.85, move_speed_mps: 3.0, detection_range_m: 1000, capabilities: { has_mortar: true } },
                                { name: 'Recon Team', unit_type: 'recon_team', sidc: '10031000151213000000', lat: 49.038, lon: 4.472, strength: 1.0, ammo: 0.8, morale: 0.95, move_speed_mps: 5.0, detection_range_m: 3000, capabilities: { is_recon: true } },
                            ],
                            red: [
                                { name: '1st Red Platoon', unit_type: 'infantry_platoon', sidc: '10061000151211000000', lat: 49.055, lon: 4.490, strength: 1.0, ammo: 1.0, morale: 0.8, move_speed_mps: 4.0, detection_range_m: 1500, capabilities: {} },
                                { name: 'Red AT Group', unit_type: 'at_team', sidc: '10061000151211004000', lat: 49.060, lon: 4.500, strength: 1.0, ammo: 0.9, morale: 0.75, move_speed_mps: 3.5, detection_range_m: 2000, capabilities: { has_atgm: true } },
                                { name: 'Red Observation Post', unit_type: 'observation_post', sidc: '10061000151213000000', lat: 49.065, lon: 4.485, strength: 0.5, ammo: 0.5, morale: 0.7, move_speed_mps: 5.0, detection_range_m: 4000, capabilities: { is_recon: true } },
                            ],
                            red_agents: [
                                { name: 'Red Company Commander', doctrine_profile: { aggression: 0.4, caution: 0.7, initiative: 0.5 }, mission_intent: { objective: 'Defend assigned sector' }, risk_posture: 'cautious', controlled_units: ['1st Red Platoon', 'Red AT Group', 'Red Observation Post'] },
                            ],
                        },
                    }),
                });
                const newScen = await scenResp.json();
                scenarios = [newScen];
            }

            // Create session from most recent scenario
            const resp = await fetch('/api/sessions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${currentToken}`,
                },
                body: JSON.stringify({ scenario_id: scenarios[scenarios.length - 1].id }),
            });
            const session = await resp.json();
            await loadSessions();
            joinAndEnter(session.id);
        } catch (err) {
            console.error('Create session failed:', err);
        }
    }


    async function loadSessions() {
        if (!currentToken) return;
        try {
            const resp = await fetch('/api/sessions', {
                headers: { 'Authorization': `Bearer ${currentToken}` },
            });
            const sessions = await resp.json();
            const listEl = document.getElementById('session-list');
            listEl.innerHTML = '';

            if (sessions.length === 0) {
                listEl.innerHTML = '<div style="color:#888;font-size:12px;padding:8px;">No sessions yet. Create one!</div>';
            }

            sessions.forEach(s => {
                const card = document.createElement('div');
                card.className = 'session-card';
                card.innerHTML = `
                    <div class="title">Session ${s.id.substring(0, 8)}...</div>
                    <div class="meta">Status: ${s.status} | Tick: ${s.tick} | Players: ${s.participant_count}</div>
                `;
                card.addEventListener('click', () => joinAndEnter(s.id));
                listEl.appendChild(card);
            });
        } catch (err) {
            console.error('Load sessions failed:', err);
        }
    }

    async function joinAndEnter(sessionId) {
        try {
            // Try to join (may already be joined)
            await fetch(`/api/sessions/${sessionId}/join`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${currentToken}`,
                },
                body: JSON.stringify({ side: 'blue', role: 'commander' }),
            });
        } catch {}

        currentSessionId = sessionId;
        document.getElementById('session-info').textContent = `Session: ${sessionId.substring(0, 8)}...`;

        // Show session control buttons
        const startBtn = document.getElementById('start-session-btn');
        const tickBtn = document.getElementById('tick-btn');
        if (startBtn) startBtn.style.display = 'block';
        if (tickBtn) tickBtn.style.display = 'block';

        // Notify app to initialize map layers
        if (window.onSessionJoined) {
            try {
                await window.onSessionJoined(sessionId, currentToken);
            } catch (err) {
                console.error('onSessionJoined error:', err);
            }
        }
    }

    return { init, getToken, getUserId, getSessionId, loadSessions };
})();
