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
    let _scenarioEnvironment = null;
    let _scenarioObjectives = null;

    function getToken() { return currentToken; }
    function getUserId() { return currentUserId; }
    function getSessionId() { return currentSessionId; }
    function getRole() { return _currentRole; }
    function getSide() { return _currentSide; }
    function canAdvanceTurn() { return _canAdvanceTurn; }

    async function init() {
        const registerBtn = document.getElementById('register-btn');
        const loginBtn = document.getElementById('login-btn');
        const nameInput = document.getElementById('display-name-input');
        const pwInput = document.getElementById('password-input');
        const exitBtn = document.getElementById('exit-session-btn');
        const turnBtn = document.getElementById('turn-btn');

        if (registerBtn) registerBtn.addEventListener('click', () => _doRegister());
        if (loginBtn) loginBtn.addEventListener('click', () => _doLogin());
        if (pwInput) pwInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') _doLogin();
        });
        if (nameInput) nameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                if (pwInput) pwInput.focus();
            }
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

        // ── Rules & Instructions Modal ──────────
        _initRulesModal();

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

        if (exitBtn) {
            exitBtn.addEventListener('click', () => _exitSession());
        }

        if (turnBtn) {
            turnBtn.addEventListener('click', async () => {
                if (!currentSessionId || !currentToken) return;

                // ── Check for unconfirmed orders ──
                // Orders that are pending/validated but not yet executing (no unit radio confirmation)
                try {
                    const ordResp = await fetch(`/api/sessions/${currentSessionId}/orders?status=pending,validated`, {
                        headers: { 'Authorization': `Bearer ${currentToken}` },
                    });
                    if (ordResp.ok) {
                        const pendingOrders = await ordResp.json();
                        // Filter to only orders still awaiting confirmation (pending or validated, not executing)
                        const unconfirmed = pendingOrders.filter(o =>
                            o.status === 'pending' || o.status === 'validated'
                        );
                        if (unconfirmed.length > 0) {
                            const unitNames = unconfirmed.map(o => {
                                const ids = o.target_unit_ids || o.matched_unit_ids || [];
                                if (ids.length > 0 && typeof KUnits !== 'undefined') {
                                    const allU = KUnits.getAllUnits();
                                    return ids.map(id => {
                                        const u = allU.find(x => x.id === id);
                                        return u ? u.name : id.slice(0, 8);
                                    }).join(', ');
                                }
                                return o.original_text ? o.original_text.slice(0, 30) : 'order';
                            });
                            const _t = typeof KI18n !== 'undefined' ? KI18n.t.bind(KI18n) : (k) => k;
                            const warningText = _t('turn.pending', { count: unconfirmed.length, units: unitNames.join('\n') });
                            if (typeof KDialogs !== 'undefined') {
                                const proceed = await KDialogs.confirm(warningText, { dangerous: false });
                                if (!proceed) return;
                            }
                        }
                    }
                } catch (e) {
                    // Ignore check errors — proceed with execution
                }

                turnBtn.disabled = true;
                turnBtn.textContent = typeof KI18n !== 'undefined' ? KI18n.t('turn.executing') : '⏳ Executing...';
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

                    // ── Update game clock immediately from HTTP response ──
                    // Don't rely on WebSocket tick_update/state_update which may be
                    // delayed (Redis pub/sub latency) or lost (WS reconnecting).
                    if (data.tick !== undefined) {
                        KMap.setGameTime(data.tick, data.game_time || null);
                    }
                    try { KOrders.refreshMeta(); } catch(e) {}

                    KGameLog.addEntry(
                        `Turn ${data.tick}: ${data.events_count} events`,
                        'info'
                    );
                    // Clear pending orders — they've been executed
                    try { KUnits.clearPendingOrders(); } catch(e) {}

                    // Reload units and contacts after turn
                    // Use god-view-aware refresh to avoid overwriting all-units with fog-of-war data
                    if (typeof KAdmin !== 'undefined' && KAdmin.isGodViewEnabled()) {
                        await KAdmin.refreshMapUnits();
                    } else {
                        await KUnits.load(currentSessionId, currentToken);
                    }
                    await KContacts.load(currentSessionId, currentToken);
                    await KEvents.load(currentSessionId, currentToken);

                    // Refresh chain of command tree after turn
                    try { KAdmin.loadPublicCoC(); } catch(e) {}

                    // Clear badge immediately — orders have been executed.
                    // Don't query server right away: the DB commit may not have
                    // completed yet (FastAPI commits after response is sent).
                    turnBtn.classList.remove('has-pending');

                    // Delayed badge refresh to catch any remaining orders
                    // (gives the DB transaction time to commit)
                    setTimeout(() => _updateTurnBadge(), 1500);
                } catch (err) {
                    console.error('Turn advance failed:', err);
                } finally {
                    turnBtn.disabled = false;
                    turnBtn.classList.remove('has-pending');
                    turnBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px;"><path d="M12 2 L12 10"/><path d="M8 4 L12 2 L16 4"/><rect x="6" y="10" width="12" height="10" rx="2"/><path d="M10 15 L14 15"/><path d="M10 18 L13 18"/></svg>Execute Orders';
                }
            });
        }
    }

    function _showScenarioDescription() {
        if (!_scenarioDescription && !_scenarioTitle && !_scenarioObjectives && !_scenarioEnvironment) return;
        const modal = document.getElementById('scenario-desc-modal');
        const titleEl = document.getElementById('scenario-desc-title');
        const contentEl = document.getElementById('scenario-desc-content');
        if (!modal) return;

        const _t = typeof KI18n !== 'undefined' ? KI18n.t.bind(KI18n) : (k) => k;

        if (titleEl) titleEl.textContent = `📋 ${_scenarioTitle || 'Scenario Briefing'}`;
        if (contentEl) {
            // Build rich HTML briefing
            let html = '';

            // Description
            const desc = _scenarioDescription || '';
            if (desc) {
                html += `<div style="margin-bottom:14px;"><div style="font-size:10px;color:#4fc3f7;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">${_escBriefing(_t('brief.situation'))}</div><div style="color:#ccc;font-size:13px;line-height:1.6;white-space:pre-wrap;">${_escBriefing(desc)}</div></div>`;
            }

            // Task / Mission
            const objectives = _scenarioObjectives || {};
            const taskText = objectives.task_text || objectives.task || '';
            if (taskText) {
                html += `<div style="margin-bottom:14px;"><div style="font-size:10px;color:#ff9800;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">${_escBriefing(_t('brief.mission'))}</div><div style="color:#e0e0e0;font-size:13px;line-height:1.6;white-space:pre-wrap;background:rgba(255,152,0,0.06);border-left:3px solid #ff9800;padding:8px 12px;border-radius:0 4px 4px 0;">${_escBriefing(taskText)}</div></div>`;
            }

            // Operation Start Time
            const env = _scenarioEnvironment || {};
            if (env.start_time) {
                try {
                    const startDate = new Date(env.start_time);
                    const locale = typeof KI18n !== 'undefined' && KI18n.getLang() === 'ru' ? 'ru-RU' : 'en-US';
                    const dateStr = startDate.toLocaleDateString(locale, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
                    const timeStr = startDate.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: false });
                    html += `<div style="margin-bottom:14px;"><div style="font-size:10px;color:#ce93d8;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">${_escBriefing(_t('brief.op_start'))}</div><div style="color:#e0e0e0;font-size:13px;line-height:1.6;background:rgba(206,147,216,0.08);border-left:3px solid #ce93d8;padding:8px 12px;border-radius:0 4px 4px 0;"><span style="font-size:12px;color:#b0b0b0;">📅</span> ${_escBriefing(dateStr)} &nbsp;&nbsp;<span style="font-size:12px;color:#b0b0b0;">⏰</span> ${_escBriefing(timeStr)}</div></div>`;
                } catch (e) {}
            }

            // Environment
            const envKeys = ['weather', 'visibility', 'wind', 'precipitation', 'light_level', 'temperature'];
            const hasEnv = envKeys.some(k => env[k] != null);
            if (hasEnv) {
                const envLabels = {
                    weather: _t('brief.weather'),
                    visibility: _t('brief.visibility'),
                    wind: _t('brief.wind'),
                    precipitation: _t('brief.precipitation'),
                    light_level: _t('brief.light'),
                    temperature: _t('brief.temperature')
                };
                let envHtml = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;">';
                envKeys.forEach(k => {
                    if (env[k] != null) {
                        let val = String(env[k]).replace(/_/g, ' ');
                        if (k === 'temperature') val += '°C';
                        envHtml += `<div style="display:flex;justify-content:space-between;align-items:center;padding:2px 0;"><span style="color:#78909c;font-size:11px;">${envLabels[k] || k}</span><span style="color:#e0e0e0;font-size:12px;font-weight:600;">${_escBriefing(val)}</span></div>`;
                    }
                });
                envHtml += '</div>';
                html += `<div style="margin-bottom:14px;"><div style="font-size:10px;color:#81c784;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">${_escBriefing(_t('brief.environment'))}</div><div style="background:rgba(129,199,132,0.06);border:1px solid rgba(129,199,132,0.15);border-radius:6px;padding:10px 14px;">${envHtml}</div></div>`;
            }

            if (!html) {
                html = `<div style="color:#888;font-style:italic;">${_escBriefing(_t('brief.no_desc'))}</div>`;
            }

            contentEl.innerHTML = html;
        }
        modal.style.display = 'flex';
    }

    function _escBriefing(s) {
        if (!s) return '';
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function _clearAuthError() {
        const errEl = document.getElementById('auth-error');
        if (errEl) errEl.textContent = '';
    }

    function _showAuthError(msg) {
        const errEl = document.getElementById('auth-error');
        if (errEl) errEl.textContent = msg;
    }

    async function _doRegister() {
        const name = (document.getElementById('display-name-input')?.value || '').trim();
        const password = (document.getElementById('password-input')?.value || '');
        const _t = typeof KI18n !== 'undefined' ? KI18n.t.bind(KI18n) : (k) => k;
        _clearAuthError();
        if (!name) { _showAuthError(_t('auth.callsign_required')); return; }
        if (!password || password.length < 4) { _showAuthError(_t('auth.password_min')); return; }

        try {
            const resp = await fetch('/api/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: name, password }),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                _showAuthError(err.detail || 'Registration failed');
                return;
            }
            const data = await resp.json();
            _onAuthSuccess(data);
        } catch (err) {
            _showAuthError(_t('auth.connection_error'));
            console.error('Register failed:', err);
        }
    }

    async function _doLogin() {
        const name = (document.getElementById('display-name-input')?.value || '').trim();
        const password = (document.getElementById('password-input')?.value || '');
        const _t = typeof KI18n !== 'undefined' ? KI18n.t.bind(KI18n) : (k) => k;
        _clearAuthError();
        if (!name) { _showAuthError(_t('auth.callsign_required')); return; }
        if (!password) { _showAuthError(_t('auth.password_required')); return; }

        try {
            const resp = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: name, password }),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                _showAuthError(err.detail || 'Login failed');
                return;
            }
            const data = await resp.json();
            _onAuthSuccess(data);
        } catch (err) {
            _showAuthError(_t('auth.connection_error'));
            console.error('Login failed:', err);
        }
    }

    function _onAuthSuccess(data) {
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
        _scenarioEnvironment = null;
        _scenarioObjectives = null;

        // Reset UI
        document.getElementById('user-info').textContent = '';
        document.getElementById('auth-panel').style.display = 'block';
        document.getElementById('session-panel').style.display = 'none';
        document.getElementById('session-info').textContent = '';
        document.getElementById('display-name-input').value = '';
        const pwField = document.getElementById('password-input');
        if (pwField) pwField.value = '';
        const authErr = document.getElementById('auth-error');
        if (authErr) authErr.textContent = '';


        // Hide admin topbar button and close admin window
        const adminTopBtn = document.getElementById('admin-topbar-btn');
        if (adminTopBtn) adminTopBtn.style.display = 'none';
        const adminWindow = document.getElementById('admin-window');
        if (adminWindow) adminWindow.style.display = 'none';

        // Hide drawing tools group and session controls
        const drawGroup = document.getElementById('map-draw-group');
        if (drawGroup) drawGroup.style.display = 'none';

        // Reset game clock
        KMap.setGameTime(0, null);

        const exitBtn = document.getElementById('exit-session-btn');
        const turnBtn = document.getElementById('turn-btn');
        if (exitBtn) exitBtn.style.display = 'none';
        if (turnBtn) turnBtn.style.display = 'none';

        // Clear session list
        const listEl = document.getElementById('session-list');
        if (listEl) listEl.innerHTML = '';

        // Clear map layers (units, contacts, overlays, grid, map objects, terrain)
        try { KUnits.clearAll(); } catch(e) {}
        try { KContacts.clearAll(); } catch(e) {}
        try { KOverlays.clearAll(); } catch(e) {}
        try { KGrid.clearAll(); } catch(e) {}
        try { KMapObjects.clearAll(); } catch(e) {}
        try { KTerrain.hide(); } catch(e) {}

        // Clear sidebar panels content
        const cocTree = document.getElementById('coc-tree-public');
        if (cocTree) cocTree.innerHTML = '';
        const eventsList = document.getElementById('events-list');
        if (eventsList) eventsList.innerHTML = '';
        const orderList = document.getElementById('order-list');
        if (orderList) orderList.innerHTML = '';
        const gameLog = document.getElementById('game-log');
        if (gameLog) gameLog.innerHTML = '';
        const participantsPanel = document.getElementById('participants-panel');
        if (participantsPanel) participantsPanel.innerHTML = '';

        // Hide command panel
        try { KOrders.hide(); } catch(e) {}

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

    /** Exit the current session (back to session list), without logging out. */
    function _exitSession() {
        // Disconnect WebSocket
        KWebSocket.disconnect();

        // Reset session state
        currentSessionId = null;
        _currentRole = null;
        _currentSide = null;
        _canAdvanceTurn = false;
        _scenarioTitle = null;
        _scenarioDescription = null;
        _scenarioEnvironment = null;
        _scenarioObjectives = null;

        // Reset UI
        document.getElementById('session-info').textContent = '';

        // Hide session controls
        const exitBtn = document.getElementById('exit-session-btn');
        const turnBtn = document.getElementById('turn-btn');
        if (exitBtn) exitBtn.style.display = 'none';
        if (turnBtn) turnBtn.style.display = 'none';

        // Show session list again
        const sessionList = document.getElementById('session-list');
        if (sessionList) sessionList.style.display = '';

        // Reset game clock
        KMap.setGameTime(0, null);

        // Hide admin topbar button and close admin window
        const adminWindow = document.getElementById('admin-window');
        if (adminWindow) adminWindow.style.display = 'none';

        // Hide drawing tools group
        const drawGroup = document.getElementById('map-draw-group');
        if (drawGroup) drawGroup.style.display = 'none';

        // Clear map layers
        try { KUnits.clearAll(); } catch(e) {}
        try { KContacts.clearAll(); } catch(e) {}
        try { KOverlays.clearAll(); } catch(e) {}
        try { KGrid.clearAll(); } catch(e) {}
        try { KMapObjects.clearAll(); } catch(e) {}
        try { KTerrain.hide(); } catch(e) {}

        // Clear sidebar panels content
        const cocTree = document.getElementById('coc-tree-public');
        if (cocTree) cocTree.innerHTML = '';
        const eventsList = document.getElementById('events-list');
        if (eventsList) eventsList.innerHTML = '';
        const orderList = document.getElementById('order-list');
        if (orderList) orderList.innerHTML = '';
        const gameLog = document.getElementById('game-log');
        if (gameLog) gameLog.innerHTML = '';

        // Hide command panel
        try { KOrders.hide(); } catch(e) {}

        // Reset sidebar to session tab
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
        const sessionTabBtn = document.querySelector('[data-tab="session-tab"]');
        if (sessionTabBtn) sessionTabBtn.classList.add('active');
        const sessionTab = document.getElementById('session-tab');
        if (sessionTab) sessionTab.classList.add('active');

        // Deactivate scenario builder if active
        try { if (KScenarioBuilder.isActive()) KScenarioBuilder.deactivate(); } catch(e) {}

        // Reset admin state
        try { KAdmin.resetOnLogout(); } catch(e) {}

        // Reload session list
        loadSessions();
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
                const noSessionsMsg = typeof KI18n !== 'undefined' ? KI18n.t('sidebar.no_sessions') : 'No sessions available. Ask admin to create one and assign you.';
                listEl.innerHTML = `<div style="color:#888;font-size:12px;padding:8px;">${noSessionsMsg}</div>`;
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

        // Fetch role + session data in parallel (they're independent)
        const authHeaders = { 'Authorization': `Bearer ${currentToken}` };
        const [roleData, sessInfo] = await Promise.all([
            fetch(`/api/sessions/${sessionId}/my-role`, { headers: authHeaders })
                .then(r => r.ok ? r.json() : null).catch(() => null),
            fetch(`/api/sessions/${sessionId}`, { headers: authHeaders })
                .then(r => r.ok ? r.json() : null).catch(() => null),
        ]);

        if (roleData) {
            _currentRole = roleData.role;
            _currentSide = roleData.side;
            _canAdvanceTurn = roleData.can_advance_turn;
        } else {
            _currentRole = 'commander';
            _currentSide = 'blue';
            _canAdvanceTurn = true;
        }

        if (sessInfo) {
            _scenarioTitle = sessInfo.scenario_title || sessInfo.name;
            _scenarioDescription = sessInfo.scenario_description;
            _scenarioEnvironment = sessInfo.scenario_environment || null;
            _scenarioObjectives = sessInfo.scenario_objectives || null;
        }

        // Auto-start the session if it's in lobby or paused
        const status = sessionData && sessionData.status;
        const turnBtn = document.getElementById('turn-btn');
        const exitBtn = document.getElementById('exit-session-btn');

        if (status === 'lobby' || status === 'paused') {
            try {
                const resp = await fetch(`/api/sessions/${currentSessionId}/start`, {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${currentToken}` },
                });
                if (resp.ok) {
                    const data = await resp.json();
                    KGameLog.addEntry(`Session started (Turn ${data.tick})`, 'info');
                    KMap.setGameTime(data.tick || 0, data.current_time || null);
                }
            } catch (err) {
                console.warn('Auto-start session failed:', err);
            }
        }

        // Show turn button for commanders, show exit button, hide session list
        if (turnBtn) turnBtn.style.display = _canAdvanceTurn ? 'inline-block' : 'none';
        if (exitBtn) exitBtn.style.display = 'block';
        const sessionList = document.getElementById('session-list');
        if (sessionList) sessionList.style.display = 'none';

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
        const newName = await KDialogs.prompt('Enter new display name:', currentUserName);
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
                await KDialogs.alert('Rename failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    /** Update the dropdown header with current user/session info. */
    function _updateDropdownHeader() {
        const nameEl = document.getElementById('user-dropdown-name');
        const sessionEl = document.getElementById('user-dropdown-session');
        if (nameEl) nameEl.textContent = currentUserName || 'Unknown';
        if (sessionEl) {
            const _t = typeof KI18n !== 'undefined' ? KI18n.t.bind(KI18n) : (k) => k;
            if (currentSessionId) {
                const roleInfo = _currentRole ? ` (${_currentRole})` : '';
                sessionEl.textContent = _t('user.in_session') + roleInfo;
            } else {
                sessionEl.textContent = _t('user.no_session');
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

        // Hide/show separators intelligently based on visible items
        const control = document.querySelector('.coord-info-control');
        if (control) {
            const seps = control.querySelectorAll('.coord-sep');
            const items = [settings.showSnail, settings.showCoords, settings.showZoom];
            // Separators sit between items; show only if both neighbors are visible
            if (seps[0]) seps[0].style.display = (items[0] && items[1]) ? '' : 'none';
            if (seps[1]) seps[1].style.display = (items[1] && items[2]) ? '' : 'none';
        }
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

                // Apply language change in real-time
                const langSelect = document.getElementById('setting-language');
                if (langSelect && typeof KI18n !== 'undefined') {
                    KI18n.setLang(langSelect.value);
                }

                if (modal) modal.style.display = 'none';
            });
        }

        // Live language switch (instant, no save needed)
        const langSelect = document.getElementById('setting-language');
        if (langSelect) {
            langSelect.addEventListener('change', () => {
                if (typeof KI18n !== 'undefined') {
                    KI18n.setLang(langSelect.value);
                }
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

        // Set language selector to current language
        const langSelect = document.getElementById('setting-language');
        if (langSelect && typeof KI18n !== 'undefined') {
            langSelect.value = KI18n.getLang();
        }

        const modal = document.getElementById('user-settings-modal');
        if (modal) modal.style.display = 'flex';
    }

    /** Get a specific setting value. */
    function getSetting(key) {
        const settings = _loadSettings();
        return settings[key];
    }

    function _initRulesModal() {
        const btn = document.getElementById('rules-topbar-btn');
        const modal = document.getElementById('rules-modal');
        const closeBtn = document.getElementById('rules-modal-close');
        const okBtn = document.getElementById('rules-modal-ok');

        const hideModal = () => { if (modal) modal.style.display = 'none'; };

        if (btn) btn.addEventListener('click', () => { if (modal) modal.style.display = 'flex'; });
        if (closeBtn) closeBtn.addEventListener('click', hideModal);
        if (okBtn) okBtn.addEventListener('click', hideModal);
        if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) hideModal(); });
    }

    /** Update pending orders badge on the turn button. */
    async function _updateTurnBadge() {
        const turnBtn = document.getElementById('turn-btn');
        if (!turnBtn || !currentSessionId || !currentToken) return;

        const svgIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px;"><path d="M12 2 L12 10"/><path d="M8 4 L12 2 L16 4"/><rect x="6" y="10" width="12" height="10" rx="2"/><path d="M10 15 L14 15"/><path d="M10 18 L13 18"/></svg>';

        try {
            // Use local count from KUnits pending orders
            const localPending = typeof KUnits !== 'undefined' ? KUnits.getPendingOrdersCount() : 0;

            // Also fetch server-side count for accuracy
            const resp = await fetch(`/api/sessions/${currentSessionId}/pending-orders-count`, {
                headers: { 'Authorization': `Bearer ${currentToken}` },
            });
            const serverPending = resp.ok ? (await resp.json()).count : 0;

            const total = Math.max(localPending, serverPending);

            // Update button text with badge
            if (total > 0) {
                turnBtn.innerHTML = `${svgIcon}Execute Orders (${total})`;
                turnBtn.classList.add('has-pending');
            } else {
                turnBtn.innerHTML = `${svgIcon}Execute Orders`;
                turnBtn.classList.remove('has-pending');
            }
        } catch (e) {
            // Ignore badge update errors
        }
    }

    /** Update cached scenario data (called when admin edits scenario details). */
    function updateScenarioCache(title, description, objectives, environment) {
        if (title) _scenarioTitle = title;
        if (description !== undefined) _scenarioDescription = description;
        if (objectives !== undefined) _scenarioObjectives = objectives;
        if (environment !== undefined) _scenarioEnvironment = environment;
    }

    return {
        init, getToken, getUserId, getUserName: () => currentUserName,
        getSessionId, getRole, getSide, canAdvanceTurn,
        loadSessions, joinAndEnter, getSetting, updateTurnBadge: _updateTurnBadge,
        updateScenarioCache,
    };
})();
