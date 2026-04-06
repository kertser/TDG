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
        const startBtn = document.getElementById('start-session-btn');
        const turnBtn = document.getElementById('turn-btn');

        registerBtn.addEventListener('click', () => _doLogin(nameInput.value.trim()));
        nameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') _doLogin(nameInput.value.trim());
        });

        if (logoutBtn) {
            logoutBtn.addEventListener('click', _doLogout);
        }



        if (startBtn) {
            startBtn.addEventListener('click', async () => {
                if (!currentSessionId || !currentToken) return;
                try {
                    const resp = await fetch(`/api/sessions/${currentSessionId}/start`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${currentToken}` },
                    });
                    if (!resp.ok) {
                        const errData = await resp.json().catch(() => ({}));
                        KGameLog.addEntry(`Start failed: ${errData.detail || resp.status}`, 'error');
                        return;
                    }
                    const data = await resp.json();
                    KGameLog.addEntry(`Session started (Turn ${data.tick})`, 'info');
                    startBtn.style.display = 'none';
                    if (turnBtn) turnBtn.style.display = 'block';

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

                    // Refresh chain of command tree after start
                    try { KAdmin.loadPublicCoC(); } catch(e) {}
                } catch (err) {
                    console.error('Start session failed:', err);
                }
            });
        }

        if (turnBtn) {
            turnBtn.addEventListener('click', async () => {
                if (!currentSessionId || !currentToken) return;
                try {
                    const resp = await fetch(`/api/sessions/${currentSessionId}/tick`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${currentToken}` },
                    });
                    const data = await resp.json();
                    KGameLog.addEntry(
                        `Turn ${data.tick}: ${data.events_count} events, ${data.units_alive} alive`,
                        'info'
                    );
                    // Reload units and contacts after turn
                    await KUnits.load(currentSessionId, currentToken);
                    await KContacts.load(currentSessionId, currentToken);
                    await KEvents.load(currentSessionId, currentToken);

                    // Refresh chain of command tree after turn
                    try { KAdmin.loadPublicCoC(); } catch(e) {}
                } catch (err) {
                    console.error('Turn advance failed:', err);
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

            // Show admin topbar button (any logged-in user can see it;
            // the password gate inside the admin tab handles security)
            const adminTopBtn = document.getElementById('admin-topbar-btn');
            if (adminTopBtn) adminTopBtn.style.display = '';

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

        // Hide admin topbar button and close admin window
        const adminTopBtn = document.getElementById('admin-topbar-btn');
        if (adminTopBtn) adminTopBtn.style.display = 'none';
        const adminWindow = document.getElementById('admin-window');
        if (adminWindow) adminWindow.style.display = 'none';

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
        const turnBtn = document.getElementById('turn-btn');
        if (startBtn) startBtn.style.display = 'none';
        if (turnBtn) turnBtn.style.display = 'none';

        // Clear session list
        const listEl = document.getElementById('session-list');
        if (listEl) listEl.innerHTML = '';

        // Clear map layers (units, contacts, overlays, grid)
        try { KUnits.clearAll(); } catch(e) {}
        try { KContacts.clearAll(); } catch(e) {}
        try { KOverlays.clearAll(); } catch(e) {}
        try { KGrid.clearAll(); } catch(e) {}

        // Clear sidebar panels content
        const cocTree = document.getElementById('coc-tree-public');
        if (cocTree) cocTree.innerHTML = '';
        const eventsList = document.getElementById('events-list');
        if (eventsList) eventsList.innerHTML = '';
        const orderList = document.getElementById('order-list');
        if (orderList) orderList.innerHTML = '';
        const gameLog = document.getElementById('game-log');
        if (gameLog) gameLog.innerHTML = '';
        const selectedUnits = document.getElementById('selected-units-display');
        if (selectedUnits) selectedUnits.innerHTML = '<span style="color:#888;font-size:11px;">No units selected</span>';
        const participantsPanel = document.getElementById('participants-panel');
        if (participantsPanel) participantsPanel.innerHTML = '';

        // Reset sidebar to session tab
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
        const sessionTabBtn = document.querySelector('[data-tab="session-tab"]');
        if (sessionTabBtn) sessionTabBtn.classList.add('active');
        const sessionTab = document.getElementById('session-tab');
        if (sessionTab) sessionTab.classList.add('active');

        // Deactivate scenario builder if active
        try { if (KScenarioBuilder.isActive()) KScenarioBuilder.deactivate(); } catch(e) {}

        // Reset admin state (re-lock, close window, clear god view)
        try { KAdmin.resetOnLogout(); } catch(e) {}
    }

    async function _createSession() {
        // Session creation is admin-only. This function is a no-op.
        // Use the admin panel to create sessions and assign users.
        console.warn('Session creation is admin-only. Use Admin panel.');
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
                listEl.innerHTML = '<div style="color:#888;font-size:12px;padding:8px;">No sessions available. Ask admin to create one and assign you.</div>';
            }

            sessions.forEach(s => {
                const card = document.createElement('div');
                card.className = 'session-card';
                const statusIcon = s.status === 'running' ? '🟢' : s.status === 'paused' ? '🟡' : s.status === 'lobby' ? '⚪' : '🔴';
                card.innerHTML = `
                    <div class="title">${statusIcon} Session ${s.id.substring(0, 8)}...</div>
                    <div class="meta">Status: ${s.status} | Turn: ${s.tick} | Players: ${s.participant_count}</div>
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
        const turnBtn = document.getElementById('turn-btn');
        if (startBtn) startBtn.style.display = 'block';
        if (turnBtn) turnBtn.style.display = 'block';

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
