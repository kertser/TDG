/**
 * session_ui.js – Session lobby: register/login/logout, create/list/join/start sessions,
 *                 admin controls (delete all sessions).
 */
const KSessionUI = (() => {
    let currentToken = null;
    let currentUserId = null;
    let currentUserName = null;
    let currentSessionId = null;
    let _currentRole = null;       // user's role in current session
    let _currentSide = null;       // user's side in current session
    let _canAdvanceTurn = false;   // whether user can advance turns
    let _scenarioTitle = null;
    let _scenarioDescription = null;

    function getToken() { return currentToken; }
    function getUserId() { return currentUserId; }
    function getSessionId() { return currentSessionId; }
    function getRole() { return _currentRole; }
    function getSide() { return _currentSide; }
    function canAdvanceTurn() { return _canAdvanceTurn; }

    async function init() {
        const registerBtn = document.getElementById('register-btn');
        const nameInput = document.getElementById('display-name-input');
        const startBtn = document.getElementById('start-session-btn');
        const turnBtn = document.getElementById('turn-btn');

        registerBtn.addEventListener('click', () => _doLogin(nameInput.value.trim()));
        nameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') _doLogin(nameInput.value.trim());
        });


        // ── User Dropdown Menu ──────────────────
        const userInfo = document.getElementById('user-info');
        const userDropdown = document.getElementById('user-dropdown');
        if (userInfo && userDropdown) {
            userInfo.addEventListener('click', (e) => {
                e.stopPropagation();
                // Update dropdown header info
                _updateDropdownHeader();
                userDropdown.style.display = userDropdown.style.display === 'none' ? 'block' : 'none';
            });
            document.addEventListener('click', () => { userDropdown.style.display = 'none'; });
            userDropdown.addEventListener('click', (e) => e.stopPropagation());

            const renameBtn = document.getElementById('user-menu-rename');
            if (renameBtn) renameBtn.addEventListener('click', _renameCurrentUser);
            const settingsBtn = document.getElementById('user-menu-settings');
            if (settingsBtn) settingsBtn.addEventListener('click', _openSettings);
            const menuLogout = document.getElementById('user-menu-logout');
            if (menuLogout) menuLogout.addEventListener('click', () => { userDropdown.style.display = 'none'; _doLogout(); });
        }

        // ── User Settings Modal ──────────────────
        _initSettingsModal();



        // ── Clickable session name → show scenario description ──
        const sessionInfoEl = document.getElementById('session-info');
        if (sessionInfoEl) {
            sessionInfoEl.addEventListener('click', _showScenarioDescription);
        }

        // ── Scenario description modal close ──
        const descClose = document.getElementById('scenario-desc-close');
        const descOk = document.getElementById('scenario-desc-ok');
        const descModal = document.getElementById('scenario-desc-modal');
        if (descClose) descClose.addEventListener('click', () => { if (descModal) descModal.style.display = 'none'; });
        if (descOk) descOk.addEventListener('click', () => { if (descModal) descModal.style.display = 'none'; });
        if (descModal) descModal.addEventListener('click', (e) => { if (e.target === descModal) descModal.style.display = 'none'; });

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
                    if (turnBtn && _canAdvanceTurn) turnBtn.style.display = 'block';

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
                    if (!resp.ok) {
                        const errData = await resp.json().catch(() => ({}));
                        KGameLog.addEntry(`Turn failed: ${errData.detail || resp.status}`, 'error');
                        return;
                    }
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

    function _showScenarioDescription() {
        if (!_scenarioDescription && !_scenarioTitle) return;
        const modal = document.getElementById('scenario-desc-modal');
        const titleEl = document.getElementById('scenario-desc-title');
        const contentEl = document.getElementById('scenario-desc-content');
        if (!modal) return;

        if (titleEl) titleEl.textContent = `📋 ${_scenarioTitle || 'Scenario Briefing'}`;
        if (contentEl) {
            contentEl.textContent = _scenarioDescription || 'No description available.';
        }
        modal.style.display = 'flex';
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
        _currentRole = null;
        _currentSide = null;
        _canAdvanceTurn = false;
        _scenarioTitle = null;
        _scenarioDescription = null;

        // Reset UI
        document.getElementById('user-info').textContent = '';
        document.getElementById('auth-panel').style.display = 'block';
        document.getElementById('session-panel').style.display = 'none';
        document.getElementById('session-info').textContent = '';
        document.getElementById('display-name-input').value = '';


        // Hide admin topbar button and close admin window
        const adminTopBtn = document.getElementById('admin-topbar-btn');
        if (adminTopBtn) adminTopBtn.style.display = 'none';
        const adminWindow = document.getElementById('admin-window');
        if (adminWindow) adminWindow.style.display = 'none';

        // Hide toolbar and session controls
        const drawToolbar = document.getElementById('draw-toolbar');
        if (drawToolbar) drawToolbar.style.display = 'none';

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
                const displayName = s.name || 'Session ' + s.id.substring(0, 8) + '...';
                card.innerHTML = `
                    <div class="title">${statusIcon} ${displayName}</div>
                    <div class="meta">Status: ${s.status} | Turn: ${s.tick} | Players: ${s.participant_count}</div>
                `;
                card.addEventListener('click', () => joinAndEnter(s.id, s));
                listEl.appendChild(card);
            });
        } catch (err) {
            console.error('Load sessions failed:', err);
        }
    }

    async function joinAndEnter(sessionId, sessionData) {
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
        const displayName = (sessionData && sessionData.name) || sessionId.substring(0, 8) + '...';
        document.getElementById('session-info').textContent = `📋 ${displayName}`;

        // Fetch user's role in this session
        try {
            const roleResp = await fetch(`/api/sessions/${sessionId}/my-role`, {
                headers: { 'Authorization': `Bearer ${currentToken}` },
            });
            if (roleResp.ok) {
                const roleData = await roleResp.json();
                _currentRole = roleData.role;
                _currentSide = roleData.side;
                _canAdvanceTurn = roleData.can_advance_turn;
            } else {
                // Fallback: assume commander
                _currentRole = 'commander';
                _currentSide = 'blue';
                _canAdvanceTurn = true;
            }
        } catch {
            _currentRole = 'commander';
            _canAdvanceTurn = true;
        }

        // Fetch scenario description
        try {
            const sessResp = await fetch(`/api/sessions/${sessionId}`, {
                headers: { 'Authorization': `Bearer ${currentToken}` },
            });
            if (sessResp.ok) {
                const sessData = await sessResp.json();
                _scenarioTitle = sessData.scenario_title || sessData.name;
                _scenarioDescription = sessData.scenario_description;
            }
        } catch {}

        // Show session control buttons based on status AND role
        const startBtn = document.getElementById('start-session-btn');
        const turnBtn = document.getElementById('turn-btn');
        const status = sessionData && sessionData.status;

        if (status === 'running') {
            if (startBtn) startBtn.style.display = 'none';
            if (turnBtn) turnBtn.style.display = _canAdvanceTurn ? 'block' : 'none';
        } else if (status === 'paused') {
            if (startBtn && _canAdvanceTurn) { startBtn.style.display = 'block'; startBtn.textContent = '▶ Resume Session'; }
            else if (startBtn) startBtn.style.display = 'none';
            if (turnBtn) turnBtn.style.display = _canAdvanceTurn ? 'block' : 'none';
        } else {
            if (startBtn && _canAdvanceTurn) { startBtn.style.display = 'block'; startBtn.textContent = 'Start Session'; }
            else if (startBtn) startBtn.style.display = 'none';
            if (turnBtn) turnBtn.style.display = 'none';
        }

        // Show map control buttons are now always visible on the map
        // (rendered as a Leaflet control in top-right corner)

        // Notify app to initialize map layers
        if (window.onSessionJoined) {
            try {
                await window.onSessionJoined(sessionId, currentToken);
            } catch (err) {
                console.error('onSessionJoined error:', err);
            }
        }
    }

    async function _renameCurrentUser() {
        const newName = prompt('Enter new display name:', currentUserName);
        if (!newName || newName.trim() === currentUserName) return;
        try {
            const resp = await fetch(`/api/admin/users/${currentUserId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: newName.trim() }),
            });
            if (resp.ok) {
                currentUserName = newName.trim();
                document.getElementById('user-info').textContent = `👤 ${currentUserName}`;
                const dropdown = document.getElementById('user-dropdown');
                if (dropdown) dropdown.style.display = 'none';
            } else {
                alert('Rename failed');
            }
        } catch (err) { alert(err.message); }
    }

    /** Update the dropdown header with current user/session info. */
    function _updateDropdownHeader() {
        const nameEl = document.getElementById('user-dropdown-name');
        const sessionEl = document.getElementById('user-dropdown-session');
        if (nameEl) nameEl.textContent = currentUserName || 'Unknown';
        if (sessionEl) {
            if (currentSessionId) {
                const roleInfo = _currentRole ? ` (${_currentRole})` : '';
                sessionEl.textContent = `🟢 In session${roleInfo}`;
            } else {
                sessionEl.textContent = '⚪ No session';
            }
        }
    }

    /** Load settings from localStorage. */
    function _loadSettings() {
        const defaults = {
            showCoords: true,
            showSnail: true,
            showZoom: true,
            unitTooltips: true,
            hoverRanges: true,
            eventSound: false,
        };
        try {
            const saved = JSON.parse(localStorage.getItem('kshu_settings') || '{}');
            return { ...defaults, ...saved };
        } catch { return defaults; }
    }

    /** Save settings to localStorage and apply. */
    function _saveSettings(settings) {
        localStorage.setItem('kshu_settings', JSON.stringify(settings));
        _applySettings(settings);
    }

    /** Apply settings to the UI. */
    function _applySettings(settings) {
        const coordEl = document.getElementById('coord-display');
        const snailEl = document.getElementById('snail-display');
        const zoomEl = document.getElementById('zoom-display');
        if (coordEl) coordEl.style.display = settings.showCoords ? '' : 'none';
        if (snailEl) snailEl.style.display = settings.showSnail ? '' : 'none';
        if (zoomEl) zoomEl.style.display = settings.showZoom ? '' : 'none';
    }

    function _initSettingsModal() {
        const modal = document.getElementById('user-settings-modal');
        const closeBtn = document.getElementById('settings-modal-close');
        const saveBtn = document.getElementById('settings-save-btn');
        const cancelBtn = document.getElementById('settings-cancel-btn');

        if (closeBtn) closeBtn.addEventListener('click', () => { if (modal) modal.style.display = 'none'; });
        if (cancelBtn) cancelBtn.addEventListener('click', () => { if (modal) modal.style.display = 'none'; });
        if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) modal.style.display = 'none'; });

        if (saveBtn) {
            saveBtn.addEventListener('click', () => {
                const settings = {
                    showCoords: document.getElementById('setting-show-coords')?.checked ?? true,
                    showSnail: document.getElementById('setting-show-snail')?.checked ?? true,
                    showZoom: document.getElementById('setting-show-zoom')?.checked ?? true,
                    unitTooltips: document.getElementById('setting-unit-tooltips')?.checked ?? true,
                    hoverRanges: document.getElementById('setting-hover-ranges')?.checked ?? true,
                    eventSound: document.getElementById('setting-event-sound')?.checked ?? false,
                };
                _saveSettings(settings);
                if (modal) modal.style.display = 'none';
            });
        }

        // Apply saved settings on init
        _applySettings(_loadSettings());
    }

    function _openSettings() {
        const dropdown = document.getElementById('user-dropdown');
        if (dropdown) dropdown.style.display = 'none';

        const settings = _loadSettings();
        const setCb = (id, val) => { const el = document.getElementById(id); if (el) el.checked = val; };
        setCb('setting-show-coords', settings.showCoords);
        setCb('setting-show-snail', settings.showSnail);
        setCb('setting-show-zoom', settings.showZoom);
        setCb('setting-unit-tooltips', settings.unitTooltips);
        setCb('setting-hover-ranges', settings.hoverRanges);
        setCb('setting-event-sound', settings.eventSound);

        const modal = document.getElementById('user-settings-modal');
        if (modal) modal.style.display = 'flex';
    }

    /** Get a specific setting value. */
    function getSetting(key) {
        const settings = _loadSettings();
        return settings[key];
    }

    return {
        init, getToken, getUserId, getUserName: () => currentUserName,
        getSessionId, getRole, getSide, canAdvanceTurn,
        loadSessions, joinAndEnter, getSetting,
    };
})();
