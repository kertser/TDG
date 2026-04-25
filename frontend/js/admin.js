/**
 * admin.js – Full admin/game-master panel (floating window).
 *
 * Sub-tabs: Session | Monitor | Builder | CoC | Users | Types
 *   Session  – participants, tick controls, reset, event injection, grid
 *   Monitor  – god-view toggle, unit dashboard, all orders
 *   Builder  – scenario builder toggle, scenario list with edit
 *   CoC      – chain of command tree, assign units to parents
 *   Users    – manage registered users (add/rename/delete/bulk-delete/assign-to-session)
 *   Types    – manage unit type definitions
 *
 * Admin tab is locked behind a password (ADMIN_PASSWORD in settings).
 * Admin selects a session via a dropdown — not dependent on user's joined session.
 */
const KAdmin = (() => {

    let _godViewEnabled = false;
    let _adminUnlocked = false;
    let _adminSelectedSessionId = null;  // admin-chosen session (independent of user's session)
    let _pickingGridOrigin = false;      // map-click pick mode for grid origin
    let _pendingGodViewEnable = false;   // flag to auto-enable god view once session is available

    function init() {
        // ── Admin lock gate ────────────────────────────
        _bind('admin-unlock-btn', 'click', _unlockAdmin);
        const pwInput = document.getElementById('admin-pw-input');
        if (pwInput) {
            pwInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') _unlockAdmin();
            });
        }

        // ── Admin topbar button ─────────────────────────
        _bind('admin-topbar-btn', 'click', _onAdminTopbarClick);

        // ── Admin floating window close ─────────────────
        _bind('admin-window-close', 'click', () => {
            _closeAdminWindow();
        });

        // ── Draggable window header ─────────────────────
        _initDraggableWindow();

        // Sub-tab switching inside admin tab (with auto-load)
        document.querySelectorAll('.admin-subtab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.admin-subtab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.admin-subtab-panel').forEach(p => p.style.display = 'none');
                btn.classList.add('active');
                const panel = document.getElementById(btn.dataset.panel);
                if (panel) panel.style.display = 'block';

                // Auto-load data for the relevant sub-tab
                const panelId = btn.dataset.panel;
                if (panelId === 'admin-users-panel') _loadUsers();
                else if (panelId === 'admin-coc-panel') _loadChainOfCommand();
                else if (panelId === 'admin-monitor-panel') _loadUnitDashboard();
                else if (panelId === 'admin-session-panel') { _refreshSessions(); _loadParticipants(); _populateSessionScenarioDropdown(); }
                else if (panelId === 'admin-builder-panel') refreshScenarioList();
                else if (panelId === 'admin-types-panel') _renderUnitTypes();
                else if (panelId === 'admin-terrain-panel') { _loadTerrainStats(); _updateCellEstimate(); }
                else if (panelId === 'admin-objects-panel') { _initObjectsPanel(); }
                else if (panelId === 'admin-redai-panel') { _loadRedAgents(); }
            });
        });

        // ── Admin Session Selector ──────────────────────
        _bind('admin-refresh-session-selector', 'click', _loadAdminSessions);
        const selector = document.getElementById('admin-session-selector');
        if (selector) {
            selector.addEventListener('change', () => {
                _adminSelectedSessionId = selector.value || null;
                const info = document.getElementById('admin-selected-session-info');
                if (info) info.textContent = _adminSelectedSessionId
                    ? `Selected: ${_adminSelectedSessionId.substring(0, 8)}...`
                    : 'No session selected';
                // Auto-enable god view if pending
                _tryAutoEnableGodView();
                // Load grid for the selected session on the map
                _tryLoadAdminSessionGrid();
                // Pre-fill the game-time input with the selected session's current_time
                _populateSessionTimeInput(_adminSelectedSessionId);
            });
        }

        // ── Builder sub-tab ─────────────────────────
        _bind('sb-toggle-btn', 'click', _toggleBuilder);
        _bind('sb-confirm-unit', 'click', () => KScenarioBuilder.confirmUnit());
        _bind('sb-cancel-unit', 'click', () => KScenarioBuilder.hideUnitForm());
        _bind('sb-save-scenario', 'click', () => KScenarioBuilder.saveScenario());
        _bind('sb-unit-type', 'change', () => KScenarioBuilder.onTypeChange());

        // Populate unit type dropdown
        _populateUnitTypeDropdown();

        // ── Scenario list ───────────────────────────
        _bind('admin-list-scenarios', 'click', refreshScenarioList);
        _bind('admin-delete-all-scenarios', 'click', _deleteAllScenarios);

        // ── Session sub-tab ─────────────────────────
        _bind('admin-delete-all-sessions', 'click', _deleteAllSessions);
        _bind('admin-refresh-sessions', 'click', _refreshSessions);
        _bind('admin-pause-session', 'click', _pauseSession);
        _bind('admin-reset-session', 'click', _resetSession);
        _bind('admin-apply-turn-interval', 'click', _applyTurnInterval);
        _bind('admin-set-session-time', 'click', _setSessionTime);
        _bind('admin-load-participants', 'click', _loadParticipants);
        _bind('admin-inject-event', 'click', _injectEvent);
        _bind('admin-apply-grid', 'click', _applyGrid);
        _bind('admin-grid-from-session', 'click', _loadGridFromSession);
        _bind('admin-grid-pick-map', 'click', _pickGridFromMap);

        // ── Scenario selection for active session ────
        _bind('admin-apply-scenario', 'click', _applyScenarioToSession);

        // ── Monitor sub-tab ─────────────────────────
        _bind('admin-god-view-toggle', 'click', _toggleGodView);
        _bind('admin-load-dashboard', 'click', _loadUnitDashboard);
        _bind('admin-load-orders', 'click', _loadAllOrders);
        _bind('admin-db-stats', 'click', _loadDbStats);
        _bind('admin-debug-log-toggle', 'click', _toggleDebugLog);
        _bind('admin-debug-log-view', 'click', _viewDebugLog);
        _bind('admin-debug-log-clear', 'click', _clearDebugLog);
        _checkDebugLogStatus();  // check initial status

        // ── Users sub-tab ───────────────────────────
        _bind('admin-load-users', 'click', _loadUsers);
        _bind('admin-add-user-btn', 'click', _addUser);
        _bind('admin-bulk-delete-users', 'click', _bulkDeleteUsers);
        const addUserInput = document.getElementById('admin-add-user-name');
        if (addUserInput) addUserInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') _addUser(); });

        // Select-all checkbox for users
        const selectAllCb = document.getElementById('admin-users-select-all');
        if (selectAllCb) {
            selectAllCb.addEventListener('change', () => {
                document.querySelectorAll('.admin-user-cb').forEach(cb => {
                    cb.checked = selectAllCb.checked;
                });
            });
        }

        // ── Chain of Command sub-tab ────────────────
        _bind('admin-load-coc', 'click', _loadChainOfCommand);

        // ── Public CoC tab refresh ──────────────────
        _bind('coc-refresh-btn', 'click', loadPublicCoC);

        // ── Terrain sub-tab ────────────────────────
        _bind('terrain-analyze-btn', 'click', () => _analyzeTerrain(false));
        _bind('terrain-analyze-force-btn', 'click', () => _analyzeTerrain(true));
        _bind('terrain-clear-btn', 'click', _clearTerrain);
        _bind('terrain-paint-start-btn', 'click', _startTerrainPaint);
        _bind('terrain-paint-stop-btn', 'click', _stopTerrainPaint);
        _bind('terrain-show-btn', 'click', () => KTerrain.toggle());
        _bind('terrain-elev-btn', 'click', () => KTerrain.toggleElevation());
        _bind('terrain-legend-btn', 'click', () => KTerrain.toggleLegend());

        // Depth change → update cell count estimate
        const depthSel = document.getElementById('terrain-analyze-depth');
        if (depthSel) {
            depthSel.addEventListener('change', _updateCellEstimate);
            // Initial estimate on panel open
            setTimeout(_updateCellEstimate, 500);
        }

        // ── Initialize modals ─────────────────────────
        _initAssignModal();
        _initCocPickerModal();
        _initCocUserAssignModal();
        _initUnitEditModal();
        _initSessionWizard();
        _initUnitTypes();
        _initRedAI();
    }

    function _bind(id, evt, fn) {
        const el = document.getElementById(id);
        if (el) el.addEventListener(evt, fn);
    }

    function _getToken() { return KSessionUI.getToken(); }
    function _getUserSessionId() { return KSessionUI.getSessionId(); }
    /** Admin session: prefer the admin dropdown, fall back to user's session */
    function _getAdminSessionId() { return _adminSelectedSessionId || _getUserSessionId(); }

    // ══════════════════════════════════════════════════
    // ── Admin Floating Window (draggable) ────────────
    // ══════════════════════════════════════════════════

    function _initDraggableWindow() {
        const win = document.getElementById('admin-window');
        const header = document.getElementById('admin-window-header');
        if (!win || !header) return;

        let isDragging = false;
        let startX, startY, startLeft, startTop;

        header.addEventListener('pointerdown', (e) => {
            if (e.target.id === 'admin-window-close') return;
            isDragging = true;
            startX = e.clientX;
            startY = e.clientY;
            const rect = win.getBoundingClientRect();
            startLeft = rect.left;
            startTop = rect.top;
            header.setPointerCapture(e.pointerId);
            e.preventDefault();
        });

        header.addEventListener('pointermove', (e) => {
            if (!isDragging) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            win.style.left = (startLeft + dx) + 'px';
            win.style.top = (startTop + dy) + 'px';
            win.style.right = 'auto';
        });

        header.addEventListener('pointerup', () => {
            isDragging = false;
        });
    }

    // ══════════════════════════════════════════════════
    // ── Admin Topbar Button ─────────────────────────
    // ══════════════════════════════════════════════════

    function _onAdminTopbarClick() {
        const win = document.getElementById('admin-window');
        if (!win) return;
        // Toggle visibility
        if (win.style.display === 'none' || !win.style.display) {
            win.style.display = 'flex';
            // Apply locked class if not yet unlocked
            if (!_adminUnlocked) {
                win.classList.add('admin-locked');
            } else {
                win.classList.remove('admin-locked');
            }
            // Re-enable admin drag when admin window is reopened
            if (_adminUnlocked) {
                try { KUnits.setAdminDrag(true); } catch(e) {}
                try { KMapObjects.render(); } catch(e) {}
                // Refresh god view if already enabled (user toggled it earlier)
                if (_godViewEnabled) {
                    _refreshGodView();
                }
                // NOTE: god-view is NOT auto-enabled on admin panel open.
                // Admin must toggle it manually via the God View button.
            }
        } else {
            _closeAdminWindow();
        }
    }

    /** Close admin window — disable god view and admin drag, then redraw normal view. */
    async function _closeAdminWindow() {
        const win = document.getElementById('admin-window');
        if (win) {
            win.style.display = 'none';
        }

        // NOTE: we do NOT re-lock admin here — once unlocked, it stays unlocked
        // for the entire browser session (until page reload).

        // Deactivate scenario builder if it's active
        try { if (KScenarioBuilder.isActive()) KScenarioBuilder.deactivate(); } catch(e) {}

        // Close any open unit context menu (admin items would be stale)
        const ctxMenu = document.getElementById('unit-ctx-menu');
        if (ctxMenu) ctxMenu.style.display = 'none';

        // Disable admin drag-and-drop when admin window is closed
        try { KUnits.setAdminDrag(false); } catch(e) {}

        // Re-render map objects so draggable markers become non-draggable
        try { KMapObjects.disableAdminMode(); } catch(e) {}

        // Disable god view if it was on
        if (_godViewEnabled) {
            _godViewEnabled = false;
            _godViewRefreshPending = false;
            clearTimeout(_godViewRefreshTimer);
            const btn = document.getElementById('admin-god-view-toggle');
            if (btn) {
                btn.textContent = '👁 God View OFF';
                btn.classList.remove('admin-btn-active');
            }
            _removeGodViewBanner();

            // Reload normal fog-of-war view
            const token = _getToken();
            const userSid = _getUserSessionId();
            if (userSid && token) {
                try {
                    await KUnits.load(userSid, token);
                    await KContacts.load(userSid, token);
                } catch (e) {
                    console.warn('Admin close — reload normal view error:', e);
                }
            }
        }
    }

    // ══════════════════════════════════════════════════
    // ── Admin Password Gate ─────────────────────────
    // ══════════════════════════════════════════════════

    async function _unlockAdmin() {
        const pw = document.getElementById('admin-pw-input');
        if (!pw) return;
        const password = pw.value.trim();
        if (!password) { await KDialogs.alert('Enter admin password'); return; }

        try {
            const resp = await fetch('/api/admin/verify-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password }),
            });
            if (resp.ok) {
                _applyAdminUnlock();
                pw.value = '';
            } else {
                const data = await resp.json().catch(() => ({}));
                await KDialogs.alert(data.detail || 'Incorrect password');
            }
        } catch (err) {
            await KDialogs.alert('Error: ' + err.message);
        }
    }

    /** Apply admin unlock state to UI (shared by password unlock and session restore). */
    function _applyAdminUnlock() {
        _adminUnlocked = true;
        const gate = document.getElementById('admin-lock-gate');
        const content = document.getElementById('admin-content');
        if (gate) gate.style.display = 'none';
        if (content) content.style.display = 'block';
        // Remove locked class — expand to full size
        const win = document.getElementById('admin-window');
        if (win) win.classList.remove('admin-locked');
        // Show admin topbar button
        const topbarBtn = document.getElementById('admin-topbar-btn');
        if (topbarBtn) topbarBtn.style.display = '';
        // Load admin sessions dropdown
        _loadAdminSessions();
        // Enable admin drag-and-drop on unit markers
        try { KUnits.setAdminDrag(true); } catch(e) {}
        // Re-render map objects so draggable markers become draggable
        try { KMapObjects.render(); } catch(e) {}
        // NOTE: god-view is NOT auto-enabled on admin unlock.
        // Admin must toggle it manually via the God View button.
    }

    function isUnlocked() { return _adminUnlocked; }

    // ══════════════════════════════════════════════════
    // ── Admin Session Selector ──────────────────────
    // ══════════════════════════════════════════════════

    async function _loadAdminSessions() {
        try {
            const token = _getToken();
            const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
            // Try admin endpoint first, fall back to user endpoint
            let resp = await fetch('/api/admin/sessions', { headers });
            if (!resp.ok) {
                resp = await fetch('/api/sessions', { headers });
            }
            if (!resp.ok) return;
            const sessions = await resp.json();
            const sel = document.getElementById('admin-session-selector');
            if (!sel) return;

            const prev = sel.value;
            sel.innerHTML = '<option value="">— Select session —</option>';
            sessions.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.id;
                const statusIcon = s.status === 'running' ? '🟢' : s.status === 'paused' ? '🟡' : s.status === 'lobby' ? '⚪' : '🔴';
                const displayName = s.name || s.id.substring(0, 8) + '…';
                opt.textContent = `${statusIcon} ${displayName} [${s.status}] T${s.tick} (${s.participant_count}p)`;
                sel.appendChild(opt);
            });

            // Restore previous selection or auto-select user's session
            if (prev && sessions.find(s => s.id === prev)) {
                sel.value = prev;
            } else if (_getUserSessionId()) {
                sel.value = _getUserSessionId();
            } else if (sessions.length > 0 && !sel.value) {
                // Auto-select first session if nothing else is available
                sel.value = sessions[0].id;
            }
            _adminSelectedSessionId = sel.value || null;

            // Show session count and per-session delete list
            const info = document.getElementById('admin-selected-session-info');
            if (info) info.textContent = _adminSelectedSessionId
                ? `Selected: ${_adminSelectedSessionId.substring(0, 8)}...`
                : `${sessions.length} session(s) available`;

            // Render session list with delete buttons below selector
            _renderAdminSessionList(sessions);

            // Auto-enable god view if pending and a session is now selected
            _tryAutoEnableGodView();
            // Auto-load grid for selected session
            _tryLoadAdminSessionGrid();
        } catch (err) {
            console.warn('Admin sessions load:', err);
        }
    }

    /** Render admin session list with individual delete and rename buttons. */
    function _renderAdminSessionList(sessions) {
        const listEl = document.getElementById('admin-session-list');
        if (!listEl) return;

        if (sessions.length === 0) {
            listEl.innerHTML = '<div class="admin-info">No sessions</div>';
            return;
        }

        let html = '';
        sessions.forEach(s => {
            const statusIcon = s.status === 'running' ? '🟢' : s.status === 'paused' ? '🟡' : s.status === 'lobby' ? '⚪' : '🔴';
            const isCurrent = s.id === _getUserSessionId();
            const border = isCurrent ? 'border-left:3px solid #4fc3f7;' : '';
            const currentTag = isCurrent ? '<span style="color:#4fc3f7;font-size:9px;"> (active)</span>' : '';
            const displayName = s.name || s.id.substring(0, 8) + '…';
            html += `<div class="admin-item" style="${border}">
                <div style="flex:1;min-width:0;">
                    <div style="font-size:11px;">${statusIcon} <b>${displayName}</b>${currentTag}</div>
                    <div style="font-size:10px;color:#888;">Turn ${s.tick} | ${s.participant_count} participant(s) | ${s.status}</div>
                </div>
                <div style="display:flex;gap:2px;flex-shrink:0;">
                    <button class="admin-btn" onclick="KAdmin.enterSession('${s.id}')" style="padding:2px 6px;font-size:10px;background:#0d3460;color:#4fc3f7;" title="Enter this session">▶</button>
                    <button class="admin-btn" onclick="KAdmin.renameSession('${s.id}','${(displayName).replace(/'/g, "\\'")}')" style="padding:2px 6px;font-size:10px;" title="Rename this session">✏</button>
                    <button class="admin-btn admin-btn-danger" onclick="KAdmin.deleteSession('${s.id}')" style="padding:2px 6px;font-size:10px;" title="Delete this session">✕</button>
                </div>
            </div>`;
        });
        listEl.innerHTML = html;
    }

    /** Rename a session. */
    async function renameSession(sessionId, currentName) {
        const newName = await KDialogs.prompt('Rename session:', currentName);
        if (!newName || newName.trim() === currentName) return;
        const token = _getToken();
        try {
            const resp = await fetch(`/api/admin/sessions/${sessionId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ name: newName.trim() }),
            });
            if (resp.ok) {
                _loadAdminSessions();
                KSessionUI.loadSessions();
                KGameLog.addEntry(`Session renamed to "${newName.trim()}"`, 'info');
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Rename failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    /** Enter a session from admin list (auto-join + switch). */
    async function enterSession(sessionId) {
        const token = _getToken();
        if (!token) return;

        // Deactivate scenario builder to avoid duplicate units/grids
        try { if (KScenarioBuilder.isActive()) KScenarioBuilder.deactivate(); } catch(e) {}

        // Try to join (may already be joined)
        try {
            await fetch(`/api/sessions/${sessionId}/join`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ side: 'blue', role: 'commander' }),
            });
        } catch {}

        // Fetch session data for joinAndEnter
        try {
            const resp = await fetch(`/api/sessions/${sessionId}`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                const sessData = await resp.json();
                // Use KSessionUI's joinAndEnter if available
                if (KSessionUI.joinAndEnter) {
                    await KSessionUI.joinAndEnter(sessionId, sessData);
                } else if (window.onSessionJoined) {
                    await window.onSessionJoined(sessionId, token);
                }
            }
        } catch (err) {
            console.warn('Enter session:', err);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Scenario Builder Toggle ──────────────────────
    // ══════════════════════════════════════════════════

    async function _toggleBuilder() {
        if (KScenarioBuilder.isActive()) {
            KScenarioBuilder.deactivate();
        } else {
            await KScenarioBuilder.activate();
        }
    }

    function _populateUnitTypeDropdown() {
        const sel = document.getElementById('sb-unit-type');
        if (!sel) return;
        sel.innerHTML = '';
        const types = KScenarioBuilder.getUnitTypes();
        for (const [key, info] of Object.entries(types)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = info.label;
            sel.appendChild(opt);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Scenario Management ──────────────────────────
    // ══════════════════════════════════════════════════

    async function refreshScenarioList() {
        try {
            const resp = await fetch('/api/scenarios');
            const scenarios = await resp.json();
            const listEl = document.getElementById('admin-scenario-list');
            if (!listEl) return;

            if (scenarios.length === 0) {
                listEl.innerHTML = '<div class="admin-info">No scenarios</div>';
                return;
            }

            let html = '';
            scenarios.forEach(s => {
                const unitCount = _countUnits(s.initial_units);
                const descPreview = s.description ? s.description.substring(0, 40) + (s.description.length > 40 ? '…' : '') : '<i style="color:#555">no description</i>';
                html += `<div class="admin-item">
                    <div>
                        <b>${s.title || 'Untitled'}</b>
                        <span class="admin-item-meta">${unitCount} units</span>
                        <div style="font-size:9px;color:#777;margin-top:2px;">${descPreview}</div>
                    </div>
                    <div style="display:flex;gap:4px;">
                        <button class="admin-btn" onclick="KAdmin.createSessionFromScenario('${s.id}')" style="padding:2px 8px;font-size:10px;background:#1b5e20;color:#a5d6a7;" title="Create a new game session from this scenario">🎮 Session</button>
                        <button class="admin-btn" onclick="KAdmin.editScenarioDetails('${s.id}')" style="padding:2px 8px;font-size:10px;background:#0d3b66;color:#90caf9;" title="Edit scenario description & task">📝 Details</button>
                        <button class="admin-btn" onclick="KAdmin.editScenario('${s.id}')" style="padding:2px 8px;font-size:10px;" title="Edit this scenario">✏ Edit</button>
                        <button class="admin-btn admin-btn-danger" onclick="KAdmin.deleteScenario('${s.id}')" style="padding:2px 8px;font-size:10px;" title="Delete this scenario">✕</button>
                    </div>
                </div>`;
            });
            listEl.innerHTML = html;
        } catch (err) {
            _showInfo('admin-scenario-list', `✗ ${err.message}`, 'error');
        }
    }

    function _countUnits(initialUnits) {
        if (!initialUnits) return 0;
        return (initialUnits.blue || []).length + (initialUnits.red || []).length;
    }

    /** Save current session state (units, grid) back to the linked scenario. */
    async function saveSessionToScenario() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { await KDialogs.alert('Select a session first'); return; }

        if (!await KDialogs.confirm(
            '💾 Overwrite the linked scenario with the current session state?\n\n' +
            'This will save all current unit positions, stats, and grid settings\n' +
            'back to the scenario as its new baseline.\n\n' +
            'The existing scenario data will be replaced.',
            {dangerous: true}
        )) return;

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/save-to-scenario`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                const data = await resp.json();
                KGameLog.addEntry(`Scenario "${data.scenario_title}" updated from session (${data.blue_units}B + ${data.red_units}R units)`, 'info');
                await KDialogs.alert(`✓ ${data.message}`);
                refreshScenarioList();
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert('Save failed: ' + (d.detail || resp.status));
            }
        } catch (err) {
            await KDialogs.alert('Save error: ' + err.message);
        }
    }

    async function editScenario(scenarioId) {
        await KScenarioBuilder.activate(scenarioId);

        // Auto-select the session that uses this scenario so CoC, terrain, and
        // other session-dependent features in the admin panel use the right session.
        // Do this AFTER builder activation so the session grid / units are correct.
        try {
            const token = _getToken();
            if (token) {
                const resp = await fetch('/api/sessions', {
                    headers: { 'Authorization': `Bearer ${token}` },
                });
                if (resp.ok) {
                    const sessions = await resp.json();
                    const match = sessions.find(s => s.scenario_id === scenarioId);
                    if (match) {
                        _adminSelectedSessionId = match.id;
                        const sel = document.getElementById('admin-session-selector');
                        if (sel) {
                            sel.value = match.id;
                            const info = document.getElementById('admin-selected-session-info');
                            if (info) info.textContent = `Selected: ${match.id.substring(0, 8)}...`;
                        }
                    }
                    // If no matching session exists yet the builder works on the
                    // scenario template only — CoC will show "Select a session first".
                }
            }
        } catch(e) {
            // Non-critical: admin can manually pick the session if needed
        }

        // Switch to builder sub-tab
        document.querySelectorAll('.admin-subtab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.admin-subtab-panel').forEach(p => p.style.display = 'none');
        const btn = document.querySelector('[data-panel="admin-builder-panel"]');
        if (btn) btn.classList.add('active');
        const panel = document.getElementById('admin-builder-panel');
        if (panel) panel.style.display = 'block';
    }

    async function deleteScenario(scenarioId) {
        if (!await KDialogs.confirm('Delete this scenario?', {dangerous: true})) return;
        const token = _getToken();
        try {
            await fetch(`/api/admin/scenarios/${scenarioId}`, {
                method: 'DELETE',
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            refreshScenarioList();
        } catch (err) {
            await KDialogs.alert('Delete failed: ' + err.message);
        }
    }

    /** Create a new session from a scenario — opens wizard modal. */
    async function createSessionFromScenario(scenarioId) {
        const token = _getToken();
        if (!token) { await KDialogs.alert('Not logged in'); return; }
        _openSessionWizard(scenarioId);
    }

    /** Edit scenario description and task text in a modal dialog. */
    async function editScenarioDetails(scenarioId) {
        const token = _getToken();
        if (!token) { await KDialogs.alert('Not logged in'); return; }
        try {
            const resp = await fetch(`/api/scenarios/${scenarioId}`, {
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (!resp.ok) throw new Error('Failed to load scenario');
            const scenario = await resp.json();

            // Build modal HTML
            const objectives = scenario.objectives || {};
            const taskText = objectives.task_text || objectives.task || '';
            const environment = scenario.environment || {};

            // Environment field helpers
            const weatherOpts = ['clear', 'cloudy', 'overcast', 'rain', 'heavy_rain', 'snow', 'fog', 'storm', 'hail'];
            const visibilityOpts = ['excellent', 'good', 'moderate', 'poor', 'very_poor', 'zero'];
            const windOpts = ['calm', 'light', 'moderate', 'strong', 'gale'];
            const precipOpts = ['none', 'light_rain', 'rain', 'heavy_rain', 'drizzle', 'snow', 'sleet', 'hail'];
            const lightOpts = ['daylight', 'dawn', 'dusk', 'twilight', 'night', 'moonlit_night', 'dark_night'];

            function _optHtml(opts, selected) {
                return opts.map(o => `<option value="${o}" ${o === selected ? 'selected' : ''}>${o.replace(/_/g, ' ')}</option>`).join('');
            }

            let overlay = document.getElementById('scenario-details-overlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.id = 'scenario-details-overlay';
                overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:12000;display:flex;align-items:center;justify-content:center;';
                document.body.appendChild(overlay);
            }
            overlay.style.display = 'flex';
            overlay.innerHTML = `
                <div style="background:#0b1122;border:1px solid rgba(79,195,247,0.3);border-radius:10px;padding:20px;width:560px;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.6);">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
                        <h3 style="color:#4fc3f7;margin:0;font-size:14px;">📝 Scenario Details</h3>
                        <button id="sd-close" style="background:none;border:none;color:#888;font-size:18px;cursor:pointer;padding:2px 6px;">✕</button>
                    </div>
                    <div style="margin-bottom:10px;">
                        <label style="display:block;font-size:10px;color:#90caf9;margin-bottom:3px;font-weight:600;">Title</label>
                        <input type="text" id="sd-title" value="${(scenario.title || '').replace(/"/g, '&quot;')}" style="width:100%;padding:6px 10px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:4px;font-size:12px;box-sizing:border-box;" />
                    </div>
                    <div style="margin-bottom:10px;">
                        <label style="display:block;font-size:10px;color:#90caf9;margin-bottom:3px;font-weight:600;">Description</label>
                        <textarea id="sd-description" rows="5" style="width:100%;padding:6px 10px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:4px;font-size:11px;resize:vertical;line-height:1.5;box-sizing:border-box;">${_escHtml(scenario.description || '')}</textarea>
                    </div>
                    <div style="margin-bottom:10px;">
                        <label style="display:block;font-size:10px;color:#90caf9;margin-bottom:3px;font-weight:600;">Task / Mission Briefing</label>
                        <textarea id="sd-task" rows="5" style="width:100%;padding:6px 10px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:4px;font-size:11px;resize:vertical;line-height:1.5;box-sizing:border-box;">${_escHtml(taskText)}</textarea>
                    </div>
                    <div style="margin-bottom:10px;">
                        <label style="display:block;font-size:10px;color:#90caf9;margin-bottom:6px;font-weight:600;">🌤 Environment Conditions</label>
                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;background:rgba(15,52,96,0.2);border:1px solid rgba(79,195,247,0.1);border-radius:6px;padding:10px;">
                            <div>
                                <label style="display:block;font-size:9px;color:#78909c;margin-bottom:2px;">Weather</label>
                                <select id="sd-env-weather" style="width:100%;padding:4px 6px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:3px;font-size:11px;">
                                    <option value="">(not set)</option>
                                    ${_optHtml(weatherOpts, environment.weather)}
                                </select>
                            </div>
                            <div>
                                <label style="display:block;font-size:9px;color:#78909c;margin-bottom:2px;">Visibility</label>
                                <select id="sd-env-visibility" style="width:100%;padding:4px 6px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:3px;font-size:11px;">
                                    <option value="">(not set)</option>
                                    ${_optHtml(visibilityOpts, environment.visibility)}
                                </select>
                            </div>
                            <div>
                                <label style="display:block;font-size:9px;color:#78909c;margin-bottom:2px;">Wind</label>
                                <select id="sd-env-wind" style="width:100%;padding:4px 6px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:3px;font-size:11px;">
                                    <option value="">(not set)</option>
                                    ${_optHtml(windOpts, environment.wind)}
                                </select>
                            </div>
                            <div>
                                <label style="display:block;font-size:9px;color:#78909c;margin-bottom:2px;">Precipitation</label>
                                <select id="sd-env-precipitation" style="width:100%;padding:4px 6px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:3px;font-size:11px;">
                                    <option value="">(not set)</option>
                                    ${_optHtml(precipOpts, environment.precipitation)}
                                </select>
                            </div>
                            <div>
                                <label style="display:block;font-size:9px;color:#78909c;margin-bottom:2px;">Light Level</label>
                                <select id="sd-env-light" style="width:100%;padding:4px 6px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:3px;font-size:11px;">
                                    <option value="">(not set)</option>
                                    ${_optHtml(lightOpts, environment.light_level)}
                                </select>
                            </div>
                            <div>
                                <label style="display:block;font-size:9px;color:#78909c;margin-bottom:2px;">Temperature (°C)</label>
                                <input type="number" id="sd-env-temp" value="${environment.temperature != null ? environment.temperature : ''}" min="-50" max="60" step="1" style="width:100%;padding:4px 6px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:3px;font-size:11px;box-sizing:border-box;" placeholder="e.g. 18" />
                            </div>
                            <div style="grid-column: 1 / -1;">
                                <label style="display:block;font-size:9px;color:#78909c;margin-bottom:2px;">⏰ Operation Start Time</label>
                                <input type="datetime-local" id="sd-env-start-time" value="${environment.start_time ? new Date(environment.start_time).toISOString().slice(0,16) : ''}" style="width:100%;padding:4px 6px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:3px;font-size:11px;box-sizing:border-box;" title="Default in-game operation start date and time for new sessions" />
                                <div style="font-size:8px;color:#555;margin-top:2px;">Sessions created from this scenario will use this as the initial game clock.</div>
                            </div>
                        </div>
                    </div>
                    <div style="display:flex;gap:8px;justify-content:flex-end;">
                        <button id="sd-cancel" class="admin-btn" style="padding:6px 16px;font-size:11px;">Cancel</button>
                        <button id="sd-save" class="admin-btn" style="padding:6px 16px;font-size:11px;background:#1b5e20;color:#a5d6a7;font-weight:600;">💾 Save</button>
                    </div>
                </div>
            `;

            const close = () => { overlay.style.display = 'none'; };
            overlay.querySelector('#sd-close').addEventListener('click', close);
            overlay.querySelector('#sd-cancel').addEventListener('click', close);
            overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

            overlay.querySelector('#sd-save').addEventListener('click', async () => {
                const newTitle = document.getElementById('sd-title').value.trim();
                const newDesc = document.getElementById('sd-description').value.trim();
                const newTask = document.getElementById('sd-task').value.trim();

                // Build environment object from form fields
                const newEnv = { ...(environment || {}) };
                const envWeather = document.getElementById('sd-env-weather').value;
                const envVisibility = document.getElementById('sd-env-visibility').value;
                const envWind = document.getElementById('sd-env-wind').value;
                const envPrecip = document.getElementById('sd-env-precipitation').value;
                const envLight = document.getElementById('sd-env-light').value;
                const envTempRaw = document.getElementById('sd-env-temp').value.trim();
                if (envWeather) newEnv.weather = envWeather; else delete newEnv.weather;
                if (envVisibility) newEnv.visibility = envVisibility; else delete newEnv.visibility;
                if (envWind) newEnv.wind = envWind; else delete newEnv.wind;
                if (envPrecip) newEnv.precipitation = envPrecip; else delete newEnv.precipitation;
                if (envLight) newEnv.light_level = envLight; else delete newEnv.light_level;
                if (envTempRaw !== '') newEnv.temperature = parseFloat(envTempRaw); else delete newEnv.temperature;
                const envStartTime = document.getElementById('sd-env-start-time')?.value || '';
                if (envStartTime) newEnv.start_time = new Date(envStartTime).toISOString(); else delete newEnv.start_time;

                const newObjectives = { ...(scenario.objectives || {}), task_text: newTask };

                try {
                    const updateResp = await fetch(`/api/scenarios/${scenarioId}`, {
                        method: 'PUT',
                        headers: {
                            'Content-Type': 'application/json',
                            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
                        },
                        body: JSON.stringify({
                            title: newTitle || undefined,
                            description: newDesc,
                            objectives: newObjectives,
                            environment: Object.keys(newEnv).length ? newEnv : null,
                        }),
                    });
                    if (!updateResp.ok) throw new Error('Save failed');
                    close();
                    refreshScenarioList();
                    _showInfo('admin-scenario-list', '✓ Scenario details saved', 'ok');

                    // Also update the cached scenario description for the briefing modal
                    if (typeof KSessionUI !== 'undefined' && KSessionUI.updateScenarioCache) {
                        KSessionUI.updateScenarioCache(newTitle, newDesc, newObjectives, newEnv);
                    }

                    // If start_time was set and there is an active session, propagate to session clock
                    if (newEnv.start_time) {
                        const activeSid = _getAdminSessionId();
                        const activeToken = _getToken();
                        if (activeSid && activeToken) {
                            try {
                                const stResp = await fetch(`/api/admin/sessions/${activeSid}/set-time`, {
                                    method: 'PUT',
                                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${activeToken}` },
                                    body: JSON.stringify({ current_time: newEnv.start_time }),
                                });
                                if (stResp.ok) {
                                    const stData = await stResp.json();
                                    // Update game clock display
                                    KMap.setGameTime(stData.tick, stData.current_time);
                                    // Update the admin game-time input
                                    const dtInput = document.getElementById('admin-session-time');
                                    if (dtInput) {
                                        try {
                                            const d = new Date(newEnv.start_time);
                                            const pad = n => String(n).padStart(2, '0');
                                            dtInput.value = `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
                                        } catch(e) {}
                                    }
                                }
                            } catch(e) { /* non-critical */ }
                        }
                    }
                } catch (err) {
                    await KDialogs.alert('Save failed: ' + err.message);
                }
            });
        } catch (err) {
            await KDialogs.alert('Failed to load scenario: ' + err.message);
        }
    }

    function _escHtml(s) {
        if (!s) return '';
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    // ══════════════════════════════════════════════════
    // ── Session Creation Wizard ──────────────────────
    // ══════════════════════════════════════════════════

    let _wizardScenarioId = null;
    let _wizardCreatedSessionId = null;
    let _wizardStep = 1;
    let _wizardParticipants = []; // {user_id, display_name, side, role}
    let _wizardUsers = [];

    function _initSessionWizard() {
        _bind('wizard-next-btn', 'click', _wizardNextStep);
        _bind('wizard-prev-btn', 'click', _wizardPrevStep);
        _bind('wizard-create-btn', 'click', _wizardCreate);
        _bind('wizard-terrain-btn', 'click', _wizardAnalyzeTerrain);
        _bind('wizard-terrain-skip-btn', 'click', _wizardSkipTerrain);
        _bind('wizard-done-btn', 'click', _wizardDone);
        _bind('wizard-cancel-btn', 'click', _closeWizard);
        _bind('wizard-close', 'click', _closeWizard);
        _bind('wizard-add-participant-btn', 'click', _wizardAddParticipant);

        // Close on overlay click
        const overlay = document.getElementById('session-wizard-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) _closeWizard();
            });
        }

        // Side change → update role options
        const sideEl = document.getElementById('wizard-p-side');
        if (sideEl) {
            sideEl.addEventListener('change', () => {
                const roleEl = document.getElementById('wizard-p-role');
                if (!roleEl) return;
                roleEl.innerHTML = '';
                if (sideEl.value === 'observer') {
                    roleEl.innerHTML = '<option value="observer">Observer</option>';
                } else {
                    roleEl.innerHTML = '<option value="commander">Commander</option><option value="officer">Officer</option><option value="observer">Observer</option>';
                }
            });
        }
    }

    async function _openSessionWizard(scenarioId) {
        _wizardScenarioId = scenarioId;
        _wizardCreatedSessionId = null;
        _wizardStep = 1;
        _wizardParticipants = [];

        // Fetch scenario info
        try {
            const resp = await fetch(`/api/scenarios/${scenarioId}`);
            if (resp.ok) {
                const sc = await resp.json();
                _setVal('wizard-session-name', sc.title || 'New Session');
                _setVal('wizard-turn-interval', 1);
                _setVal('wizard-turn-limit', sc.objectives?.turn_limit || 0);
                // Pre-fill operation start time from scenario environment
                const envStartTime = sc.environment?.start_time;
                if (envStartTime) {
                    try {
                        const dt = new Date(envStartTime);
                        const pad = n => String(n).padStart(2, '0');
                        const dtLocal = `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth()+1)}-${pad(dt.getUTCDate())}T${pad(dt.getUTCHours())}:${pad(dt.getUTCMinutes())}`;
                        _setVal('wizard-operation-datetime', dtLocal);
                    } catch(e) { _setVal('wizard-operation-datetime', ''); }
                } else {
                    _setVal('wizard-operation-datetime', '');
                }
                const infoEl = document.getElementById('wizard-scenario-info');
                if (infoEl) {
                    const unitCount = ((sc.initial_units?.blue || []).length + (sc.initial_units?.red || []).length);
                    infoEl.textContent = `${sc.title || 'Untitled'} — ${unitCount} units`;
                }
            }
        } catch {}

        // Load available users
        try {
            const resp = await fetch('/api/admin/users');
            if (resp.ok) _wizardUsers = await resp.json();
        } catch {}
        _wizardPopulateUserSelect();

        // Show modal
        _wizardShowStep(1);
        const modal = document.getElementById('session-wizard-modal');
        if (modal) modal.style.display = 'flex';
    }

    function _closeWizard() {
        const modal = document.getElementById('session-wizard-modal');
        if (modal) modal.style.display = 'none';
        _wizardScenarioId = null;
    }

    function _wizardShowStep(step) {
        _wizardStep = step;
        for (let i = 1; i <= 4; i++) {
            const el = document.getElementById(`wizard-step-${i}`);
            if (el) el.style.display = i === step ? 'block' : 'none';
            const ind = document.getElementById(`wizard-ind-${i}`);
            if (ind) {
                ind.classList.toggle('active', i === step);
                ind.classList.toggle('done', i < step);
            }
        }
        // Button visibility
        const prevBtn = document.getElementById('wizard-prev-btn');
        const nextBtn = document.getElementById('wizard-next-btn');
        const createBtn = document.getElementById('wizard-create-btn');
        const terrainBtn = document.getElementById('wizard-terrain-btn');
        const terrainSkipBtn = document.getElementById('wizard-terrain-skip-btn');
        const doneBtn = document.getElementById('wizard-done-btn');
        if (prevBtn) prevBtn.style.display = step === 2 ? '' : 'none';
        if (nextBtn) nextBtn.style.display = step === 1 ? '' : 'none';
        if (createBtn) createBtn.style.display = step === 2 ? '' : 'none';
        if (terrainBtn) terrainBtn.style.display = step === 3 ? '' : 'none';
        if (terrainSkipBtn) terrainSkipBtn.style.display = step === 3 ? '' : 'none';
        if (doneBtn) doneBtn.style.display = step === 4 ? '' : 'none';
    }

    async function _wizardNextStep() {
        if (_wizardStep === 1) {
            const name = document.getElementById('wizard-session-name')?.value?.trim();
            if (!name) { await KDialogs.alert('Session name is required'); return; }
            _wizardShowStep(2);
        }
    }

    function _wizardPrevStep() {
        if (_wizardStep > 1) _wizardShowStep(_wizardStep - 1);
    }

    function _wizardPopulateUserSelect() {
        const sel = document.getElementById('wizard-p-user');
        if (!sel) return;
        sel.innerHTML = '';
        _wizardUsers.forEach(u => {
            // Skip users already added as participants
            if (_wizardParticipants.find(p => p.user_id === u.id)) return;
            const opt = document.createElement('option');
            opt.value = u.id;
            opt.textContent = u.display_name;
            sel.appendChild(opt);
        });
    }

    function _wizardAddParticipant() {
        const userEl = document.getElementById('wizard-p-user');
        const sideEl = document.getElementById('wizard-p-side');
        const roleEl = document.getElementById('wizard-p-role');
        if (!userEl || !sideEl || !roleEl || !userEl.value) return;

        const user = _wizardUsers.find(u => u.id === userEl.value);
        if (!user) return;

        _wizardParticipants.push({
            user_id: user.id,
            display_name: user.display_name,
            side: sideEl.value,
            role: roleEl.value,
        });

        _wizardPopulateUserSelect();
        _wizardRenderParticipants();
    }

    function _wizardRemoveParticipant(idx) {
        _wizardParticipants.splice(idx, 1);
        _wizardPopulateUserSelect();
        _wizardRenderParticipants();
    }

    function _wizardRenderParticipants() {
        const el = document.getElementById('wizard-participants-list');
        if (!el) return;
        if (_wizardParticipants.length === 0) {
            el.innerHTML = '<div style="color:#888;font-size:11px;padding:4px;">No participants added yet</div>';
            return;
        }
        let html = '';
        _wizardParticipants.forEach((p, i) => {
            const sideColor = p.side === 'blue' ? '#4fc3f7' : p.side === 'red' ? '#ef5350' : '#aaa';
            html += `<div class="admin-item" style="border-left:3px solid ${sideColor};padding:4px 6px;">
                <div style="flex:1;">
                    <b>${p.display_name}</b>
                    <span class="admin-item-meta">${p.side} / ${p.role}</span>
                </div>
                <button class="admin-btn admin-btn-danger" onclick="KAdmin.wizardRemoveParticipant(${i})" style="padding:1px 6px;font-size:9px;">✕</button>
            </div>`;
        });
        el.innerHTML = html;
    }

    async function _wizardCreate() {
        const token = _getToken();
        if (!token || !_wizardScenarioId) return;

        const name = document.getElementById('wizard-session-name')?.value?.trim();
        const interval = parseInt(document.getElementById('wizard-turn-interval')?.value) || 1;
        const turnLimit = parseInt(document.getElementById('wizard-turn-limit')?.value) || 0;
        const opDatetime = document.getElementById('wizard-operation-datetime')?.value || '';

        const statusEl = document.getElementById('wizard-create-status');
        if (statusEl) { statusEl.textContent = '⏳ Creating session...'; statusEl.className = 'admin-info'; }

        try {
            // 1. Create session
            const resp = await fetch('/api/admin/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ scenario_id: _wizardScenarioId }),
            });
            if (!resp.ok) {
                const d = await resp.json().catch(() => ({}));
                if (statusEl) { statusEl.textContent = `✗ ${d.detail || 'Creation failed'}`; statusEl.className = 'admin-info admin-error'; }
                return;
            }
            const sessionData = await resp.json();
            _wizardCreatedSessionId = sessionData.id;

            // 2. Rename session
            if (name && name !== sessionData.name) {
                await fetch(`/api/admin/sessions/${sessionData.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ name }),
                });
            }

            // 3. Set tick interval
            if (interval !== 1) {
                await fetch(`/api/admin/sessions/${sessionData.id}/tick-interval`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ tick_interval: interval * 60 }),
                });
            }

            // 3a. Save turn limit to session settings
            if (turnLimit > 0) {
                await fetch(`/api/admin/sessions/${sessionData.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ settings: { turn_limit: turnLimit } }),
                });
            }

            // 3b. Set operation datetime if provided
            if (opDatetime) {
                const isoTime = new Date(opDatetime).toISOString();
                await fetch(`/api/admin/sessions/${sessionData.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ current_time: isoTime }),
                });

                // Auto-set light_level on the scenario environment based on time of day
                const dt = new Date(opDatetime);
                const hour = dt.getHours();
                let lightLevel = 'day';
                if (hour >= 21 || hour < 5) lightLevel = 'night';
                else if ((hour >= 5 && hour < 7) || (hour >= 19 && hour < 21)) lightLevel = 'twilight';

                try {
                    // Fetch current scenario environment and update light_level
                    const scenResp = await fetch(`/api/scenarios/${_wizardScenarioId}`, {
                        headers: token ? { 'Authorization': `Bearer ${token}` } : {},
                    });
                    if (scenResp.ok) {
                        const scenData = await scenResp.json();
                        const env = scenData.environment || {};
                        env.light_level = lightLevel;
                        await fetch(`/api/scenarios/${_wizardScenarioId}`, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
                            body: JSON.stringify({ environment: env }),
                        });
                    }
                } catch (e) {
                    console.warn('Auto-set light level failed:', e);
                }
            }

            // 4. Add participants
            let addedCount = 0;
            for (const p of _wizardParticipants) {
                try {
                    const pResp = await fetch(`/api/admin/sessions/${sessionData.id}/add-participant`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                        body: JSON.stringify({ user_id: p.user_id, side: p.side, role: p.role }),
                    });
                    if (pResp.ok) addedCount++;
                } catch {}
            }

            if (statusEl) {
                statusEl.textContent = `✓ Session "${name}" created with ${addedCount} participant(s)`;
                statusEl.className = 'admin-info admin-success';
            }

            // Update admin session list and select new session
            _loadAdminSessions();
            KSessionUI.loadSessions();

            setTimeout(() => {
                const sel = document.getElementById('admin-session-selector');
                if (sel) {
                    sel.value = sessionData.id;
                    _adminSelectedSessionId = sessionData.id;
                }
            }, 300);

            KGameLog.addEntry(`Session "${name}" created via wizard (${addedCount} participants)`, 'info');

            // Set terrain session context so KTerrain knows which session to analyze
            KTerrain.setSession(sessionData.id);

            // Move to step 3 (terrain analysis)
            _wizardShowStep(3);

        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'admin-info admin-error'; }
        }
    }

    async function _wizardAnalyzeTerrain() {
        if (!_wizardCreatedSessionId) return;

        const depth = parseInt(document.getElementById('wizard-terrain-depth')?.value || '3');
        const skipElev = document.getElementById('wizard-terrain-skip-elev')?.checked || false;
        const statusEl = document.getElementById('wizard-terrain-status');
        const progressContainer = document.getElementById('wizard-terrain-progress');
        const progressFill = document.getElementById('wizard-terrain-progress-fill');
        const progressText = document.getElementById('wizard-terrain-progress-text');
        const terrainBtn = document.getElementById('wizard-terrain-btn');
        const skipBtn = document.getElementById('wizard-terrain-skip-btn');

        // Show progress
        if (progressContainer) progressContainer.style.display = 'block';
        if (progressFill) { progressFill.style.width = '0%'; progressFill.classList.remove('error'); }
        if (progressText) progressText.textContent = 'Starting analysis...';
        if (statusEl) { statusEl.textContent = ''; statusEl.className = 'admin-info'; }
        if (terrainBtn) terrainBtn.disabled = true;
        if (skipBtn) skipBtn.disabled = true;

        // Make sure KTerrain knows the session
        KTerrain.setSession(_wizardCreatedSessionId);

        const result = await KTerrain.analyzeWithProgress(depth, false, skipElev, (event) => {
            if (progressFill && event.progress >= 0) {
                progressFill.style.width = `${Math.round(event.progress * 100)}%`;
            }
            if (progressText && event.message) {
                progressText.textContent = event.message;
            }
            if (event.step === 'error') {
                if (progressFill) progressFill.classList.add('error');
                if (statusEl) {
                    statusEl.textContent = `❌ ${event.message}`;
                    statusEl.className = 'admin-info admin-error';
                }
            }
        });

        if (terrainBtn) terrainBtn.disabled = false;
        if (skipBtn) skipBtn.disabled = false;

        if (result) {
            if (progressFill) progressFill.style.width = '100%';
            if (progressText) progressText.textContent = `✅ Done in ${result.duration_s}s`;
            if (statusEl) {
                statusEl.textContent = `✅ ${result.cells_created} cells created, ${result.cells_updated} updated. OSM: ${result.osm_features} features. ${result.cell_size_m}m resolution.`;
                statusEl.className = 'admin-info admin-success';
            }
            KGameLog.addEntry(`Terrain analyzed: ${result.cells_created} cells at depth ${depth}`, 'info');

            // Auto-advance to Done after a short delay
            setTimeout(() => _wizardShowStep(4), 1500);
        } else if (!statusEl?.textContent?.includes('❌')) {
            if (statusEl) { statusEl.textContent = '❌ Analysis failed. You can retry or skip.'; statusEl.className = 'admin-info admin-error'; }
        }
    }

    function _wizardSkipTerrain() {
        _wizardShowStep(4);
    }

    async function _wizardDone() {
        _closeWizard();
        // Deactivate builder to prevent duplicate grid/units
        try { if (KScenarioBuilder.isActive()) KScenarioBuilder.deactivate(); } catch(e) {}
        // Auto-enter the created session so grid + units load on map
        if (_wizardCreatedSessionId) {
            try {
                await enterSession(_wizardCreatedSessionId);
            } catch (e) {
                console.warn('Auto-enter session after wizard:', e);
            }
            // Switch to CoC sub-tab for hierarchy setup
            document.querySelectorAll('.admin-subtab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.admin-subtab-panel').forEach(p => p.style.display = 'none');
            const cocBtn = document.querySelector('[data-panel="admin-coc-panel"]');
            if (cocBtn) cocBtn.classList.add('active');
            const cocPanel = document.getElementById('admin-coc-panel');
            if (cocPanel) cocPanel.style.display = 'block';
            _loadChainOfCommand();
        }
    }

    async function _deleteAllScenarios() {
        if (!await KDialogs.confirm('⚠ Delete ALL scenarios?', {dangerous: true})) return;
        try {
            const resp = await fetch('/api/scenarios');
            const scenarios = await resp.json();
            const token = _getToken();
            let deleted = 0;
            for (const s of scenarios) {
                const del = await fetch(`/api/admin/scenarios/${s.id}`, {
                    method: 'DELETE',
                    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
                });
                if (del.ok || del.status === 204) deleted++;
            }
            _showInfo('admin-scenario-list', `✓ Deleted ${deleted}/${scenarios.length}`, 'success');
            refreshScenarioList();
        } catch (err) {
            _showInfo('admin-scenario-list', `✗ ${err.message}`, 'error');
        }
    }

    // ══════════════════════════════════════════════════
    // ── Session Management ───────────────────────────
    // ══════════════════════════════════════════════════

    async function _deleteAllSessions() {
        const token = _getToken();
        if (!token) { _showInfo('admin-session-count', 'Not logged in', 'error'); return; }
        if (!await KDialogs.confirm('⚠ Delete ALL sessions?', {dangerous: true})) return;

        try {
            const resp = await fetch('/api/sessions', {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                _showInfo('admin-session-count', '✓ All sessions deleted', 'success');
                _adminSelectedSessionId = null;
                KWebSocket.disconnect();
                document.getElementById('session-info').textContent = '';
                const exitBtn0 = document.getElementById('exit-session-btn');
                const turnBtn = document.getElementById('turn-btn');
                if (exitBtn0) exitBtn0.style.display = 'none';
                if (turnBtn) turnBtn.style.display = 'none';
                KSessionUI.loadSessions();
                KGameLog.addEntry('All sessions deleted (admin)', 'info');
                _loadAdminSessions();
            } else {
                const data = await resp.json().catch(() => ({}));
                _showInfo('admin-session-count', `✗ ${data.detail || resp.status}`, 'error');
            }
        } catch (err) {
            _showInfo('admin-session-count', `✗ ${err.message}`, 'error');
        }
    }

    /** Delete a single session by ID (from admin session list). */
    async function deleteSession(sessionId) {
        const token = _getToken();
        if (!token) return;
        if (!await KDialogs.confirm(`Delete session ${sessionId.substring(0, 8)}…?`, {dangerous: true})) return;

        try {
            const resp = await fetch(`/api/admin/sessions/${sessionId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                // If deleting the user's active session, clean up
                if (sessionId === _getUserSessionId()) {
                    KWebSocket.disconnect();
                    document.getElementById('session-info').textContent = '';
                    const exitBtn1 = document.getElementById('exit-session-btn');
                    const turnBtn = document.getElementById('turn-btn');
                    if (exitBtn1) exitBtn1.style.display = 'none';
                    if (turnBtn) turnBtn.style.display = 'none';
                    try { KUnits.clearAll(); } catch(e) {}
                    try { KContacts.clearAll(); } catch(e) {}
                    try { KGrid.clearAll(); } catch(e) {}
                    try { KOverlays.clearAll(); } catch(e) {}
                    try { KMapObjects.clearAll(); } catch(e) {}
                }
                if (sessionId === _adminSelectedSessionId) {
                    _adminSelectedSessionId = null;
                }
                _loadAdminSessions();
                KSessionUI.loadSessions();
                KGameLog.addEntry(`Session ${sessionId.substring(0, 8)}… deleted (admin)`, 'info');
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Delete session failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    async function _refreshSessions() {
        const token = _getToken();
        if (!token) { _showInfo('admin-session-count', 'Not logged in', 'error'); return; }
        try {
            const resp = await fetch('/api/sessions', {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const sessions = await resp.json();
            _showInfo('admin-session-count', `${sessions.length} session(s)`);
            KSessionUI.loadSessions();
            _loadAdminSessions();
        } catch (err) {
            _showInfo('admin-session-count', `✗ ${err.message}`, 'error');
        }
    }

    async function _pauseSession() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-session-status', 'Select a session first', 'error'); return; }
        try {
            const resp = await fetch(`/api/sessions/${sid}/pause`, {
                method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
            });
            const data = await resp.json();
            _showInfo('admin-session-status', `Paused at turn ${data.tick}`, 'success');
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    async function _resetSession() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-session-status', 'Select a session first', 'error'); return; }
        if (!await KDialogs.confirm('⚠ Reset session to turn 0?\n\nUnits, orders, events, chat and reports will be cleared.\nMap objects and terrain data are preserved.', {dangerous: true})) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/reset`, {
                method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
            });
            const data = await resp.json();
            _showInfo('admin-session-status', `✓ ${data.message}`, 'success');
            KGameLog.addEntry('Session reset to turn 0 (admin)', 'info');
            // Full data refresh for the active session
            const userSid = _getUserSessionId();
            if (sid === userSid || _godViewEnabled) {
                const map = KMap.getMap();
                try { await KGrid.load(map, _getAdminSessionId()); } catch(e) {}
                await refreshMapUnits();
                // Refresh all supporting data
                try { KContacts.clearAll(); await KContacts.load(sid, token); } catch(e) {}
                try { KOverlays.clearAll(); await KOverlays.loadFromServer(); } catch(e) {}
                try { KEvents.load(sid, token); } catch(e) {}
                try { KReports.load(sid, token); } catch(e) {}
                try { KMapObjects.clearAll(); KMapObjects.load(sid, token); } catch(e) {}
                try { KTerrain.load(sid, token); } catch(e) {}
                try { KOrders.clearRadio(); } catch(e) {}
                try { KReplay.clearData(); } catch(e) {}
                // Reset game clock to the reset time from server
                KMap.setGameTime(0, data.current_time || null);
                // Update the admin game-time input to reflect reset time
                if (data.current_time) {
                    const dtInput = document.getElementById('admin-session-time');
                    if (dtInput) {
                        try {
                            const d = new Date(data.current_time);
                            const pad = n => String(n).padStart(2, '0');
                            dtInput.value = `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
                        } catch(e) {}
                    }
                }
            }
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    async function _applyTurnInterval() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-session-status', 'Select a session first', 'error'); return; }
        const minutes = parseInt(document.getElementById('admin-turn-interval').value);
        if (!minutes || minutes < 1) { await KDialogs.alert('Invalid interval'); return; }
        const seconds = minutes * 60;  // Convert minutes to seconds for backend
        try {
            await fetch(`/api/admin/sessions/${sid}/tick-interval`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ tick_interval: seconds }),
            });
            _showInfo('admin-session-status', `Turn interval: ${minutes} min`, 'success');
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    /** Populate the admin-session-time input with the session's current_time. */
    async function _populateSessionTimeInput(sid) {
        const dtInput = document.getElementById('admin-session-time');
        if (!dtInput || !sid) return;
        const token = _getToken();
        if (!token) return;
        try {
            const resp = await fetch(`/api/sessions/${sid}`, { headers: { 'Authorization': `Bearer ${token}` } });
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.current_time) {
                const d = new Date(data.current_time);
                const pad = n => String(n).padStart(2, '0');
                dtInput.value = `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
            }
        } catch(e) { /* ignore */ }
    }

    async function _setSessionTime() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-session-status', 'Select a session first', 'error'); return; }
        const dtInput = document.getElementById('admin-session-time');
        if (!dtInput || !dtInput.value) { _showInfo('admin-session-status', 'Enter a date/time first', 'error'); return; }
        try {
            const isoTime = new Date(dtInput.value).toISOString();
            const resp = await fetch(`/api/admin/sessions/${sid}/set-time`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ current_time: isoTime }),
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || resp.status);
            const data = await resp.json();
            _showInfo('admin-session-status', `✓ Game time set to ${dtInput.value}`, 'success');
            // Update the game clock display
            KMap.setGameTime(data.tick, data.current_time);
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    // ── Grid Management ─────────────────────────────

    // ── Scenario Selection for Active Session ────────

    async function _populateSessionScenarioDropdown() {
        const sel = document.getElementById('admin-session-scenario');
        if (!sel) return;
        const token = _getToken();
        if (!token) return;
        try {
            const resp = await fetch('/api/scenarios', { headers: { 'Authorization': `Bearer ${token}` } });
            if (!resp.ok) return;
            const scenarios = await resp.json();
            // Preserve first "current" option
            sel.innerHTML = '<option value="">— current —</option>';
            scenarios.forEach(sc => {
                const opt = document.createElement('option');
                opt.value = sc.id;
                opt.textContent = sc.title || sc.id.substring(0, 8);
                sel.appendChild(opt);
            });
        } catch (e) { /* ignore */ }
    }

    async function _applyScenarioToSession() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-scenario-change-status', 'Select a session first', 'error'); return; }
        const scenarioId = document.getElementById('admin-session-scenario').value;
        if (!scenarioId) { _showInfo('admin-scenario-change-status', 'Select a scenario first', 'error'); return; }

        if (!await KDialogs.confirm('⚠ Change scenario for this session?\nThis will RESET all units and grid to the selected scenario.\nAll current progress will be lost.', {dangerous: true})) return;

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/apply-scenario`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ scenario_id: scenarioId }),
            });
            if (resp.ok) {
                const data = await resp.json();
                _showInfo('admin-scenario-change-status', `✓ ${data.message || 'Scenario applied'}`, 'success');
                KGameLog.addEntry('Scenario changed (admin)', 'info');
                // Full data refresh
                const userSid = _getUserSessionId();
                if (sid === userSid || _godViewEnabled) {
                    const map = KMap.getMap();
                    try { await KGrid.load(map, _getAdminSessionId()); } catch(e) {}
                    await refreshMapUnits();
                    try { KContacts.clearAll(); await KContacts.load(sid, token); } catch(e) {}
                    try { KOverlays.clearAll(); await KOverlays.loadFromServer(); } catch(e) {}
                    try { KEvents.load(sid, token); } catch(e) {}
                    try { KReports.load(sid, token); } catch(e) {}
                    try { KMapObjects.clearAll(); KMapObjects.load(sid, token); } catch(e) {}
                    try { KTerrain.load(sid, token); } catch(e) {}
                    try { KOrders.clearRadio(); } catch(e) {}
                    try { KReplay.clearData(); } catch(e) {}
                    KMap.setGameTime(0, null);
                }
                await _loadUnitDashboard();
            } else {
                const d = await resp.json().catch(() => ({}));
                _showInfo('admin-scenario-change-status', `✗ ${d.detail || 'Failed'}`, 'error');
            }
        } catch (err) {
            _showInfo('admin-scenario-change-status', `✗ ${err.message}`, 'error');
        }
    }

    async function _applyGrid() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-grid-status', 'Select a session first', 'error'); return; }
        const lat = parseFloat(document.getElementById('admin-grid-origin-lat').value);
        const lon = parseFloat(document.getElementById('admin-grid-origin-lon').value);
        const cols = Math.max(1, Math.min(20, parseInt(document.getElementById('admin-grid-cols').value) || 8));
        const rows = Math.max(1, Math.min(20, parseInt(document.getElementById('admin-grid-rows').value) || 8));
        const size = Math.max(100, Math.min(10000, parseInt(document.getElementById('admin-grid-size').value) || 1000));
        if (isNaN(lat) || isNaN(lon)) { _showInfo('admin-grid-status', 'Invalid origin coordinates', 'error'); return; }

        const payload = JSON.stringify({
            origin_lat: lat, origin_lon: lon,
            columns: cols, rows: rows,
            base_square_size_m: size,
        });
        const headers = { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` };

        try {
            // Try PUT first, fall back to POST if 405
            let resp = await fetch(`/api/admin/sessions/${sid}/grid`, {
                method: 'PUT', headers, body: payload,
            });
            if (resp.status === 405) {
                resp = await fetch(`/api/admin/sessions/${sid}/grid`, {
                    method: 'POST', headers, body: payload,
                });
            }
            if (resp.ok) {
                _showInfo('admin-grid-status', '✓ Grid updated', 'success');
                // Reload grid on map — always reload for user's active session
                const map = KMap.getMap();
                const userSid = _getUserSessionId();
                if (sid === userSid || !userSid) {
                    try {
                        await KGrid.load(map, sid);
                        // If scenario builder is active, clear its preview grid
                        // (session grid takes precedence)
                        if (KScenarioBuilder.isActive()) {
                            try { KScenarioBuilder.clearGridPreview && KScenarioBuilder.clearGridPreview(); } catch(e) {}
                        }
                        // Re-center map on grid if it loaded
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
                            map.setView([cLat, cLng], map.getZoom());
                        }
                    } catch (e) {
                        console.warn('Grid reload after admin apply:', e);
                    }
                }
            } else {
                const d = await resp.json().catch(() => ({}));
                _showInfo('admin-grid-status', `✗ ${d.detail || resp.status}`, 'error');
            }
        } catch (err) {
            _showInfo('admin-grid-status', `✗ ${err.message}`, 'error');
        }
    }

    /** Load grid settings from the currently selected session's existing grid. */
    async function _loadGridFromSession() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-grid-status', 'Select a session first', 'error'); return; }
        try {
            const resp = await fetch(`/api/sessions/${sid}/grid/meta`);
            if (!resp.ok) {
                _showInfo('admin-grid-status', 'No grid defined for this session', 'error');
                return;
            }
            const meta = await resp.json();
            const latEl = document.getElementById('admin-grid-origin-lat');
            const lonEl = document.getElementById('admin-grid-origin-lon');
            const colsEl = document.getElementById('admin-grid-cols');
            const rowsEl = document.getElementById('admin-grid-rows');
            const sizeEl = document.getElementById('admin-grid-size');
            if (latEl) latEl.value = meta.origin_lat != null ? meta.origin_lat.toFixed(5) : '';
            if (lonEl) lonEl.value = meta.origin_lon != null ? meta.origin_lon.toFixed(5) : '';
            if (colsEl) colsEl.value = meta.columns || 8;
            if (rowsEl) rowsEl.value = meta.rows || 8;
            if (sizeEl) sizeEl.value = meta.base_square_size_m || 1000;
            _showInfo('admin-grid-status', '✓ Loaded from session grid', 'success');
        } catch (err) {
            _showInfo('admin-grid-status', `✗ ${err.message}`, 'error');
        }
    }

    /** Enter map-click pick mode to set grid origin from a map click. */
    function _pickGridFromMap() {
        const map = KMap.getMap();
        if (!map) { _showInfo('admin-grid-status', 'Map not ready', 'error'); return; }
        if (_pickingGridOrigin) return;

        _pickingGridOrigin = true;
        map.getContainer().classList.add('pick-mode-active');

        // Show banner
        const banner = document.createElement('div');
        banner.className = 'pick-mode-banner';
        banner.id = 'pick-mode-banner';
        banner.textContent = '🖱 Click on map to set grid origin — ESC to cancel';
        document.body.appendChild(banner);

        const _cancelPick = () => {
            _pickingGridOrigin = false;
            map.getContainer().classList.remove('pick-mode-active');
            const b = document.getElementById('pick-mode-banner');
            if (b) b.remove();
            map.off('click', _onPickClick);
            document.removeEventListener('keydown', _onPickKey);
        };

        const _onPickClick = (e) => {
            const latEl = document.getElementById('admin-grid-origin-lat');
            const lonEl = document.getElementById('admin-grid-origin-lon');
            if (latEl) latEl.value = e.latlng.lat.toFixed(5);
            if (lonEl) lonEl.value = e.latlng.lng.toFixed(5);
            _showInfo('admin-grid-status', `✓ Origin set: ${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`, 'success');
            _cancelPick();
        };

        const _onPickKey = (e) => {
            if (e.key === 'Escape') {
                _cancelPick();
                _showInfo('admin-grid-status', 'Pick cancelled');
            }
        };

        map.once('click', _onPickClick);
        document.addEventListener('keydown', _onPickKey, { once: true });

        _showInfo('admin-grid-status', 'Click on map to set origin…');
    }

    // ── Participants ─────────────────────────────────

    async function _loadParticipants() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-participants-list', 'Select a session first'); return; }
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/participants`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const participants = await resp.json();
            const el = document.getElementById('admin-participants-list');
            if (!el) return;

            if (participants.length === 0) {
                el.innerHTML = '<div class="admin-info">No participants</div>';
                return;
            }

            let html = '';
            participants.forEach(p => {
                const sideColor = p.side === 'blue' ? '#4fc3f7' : p.side === 'red' ? '#ef5350' : '#aaa';
                html += `<div class="admin-item" style="border-left:3px solid ${sideColor};">
                    <div>
                        <b>${p.display_name}</b>
                        <span class="admin-item-meta">${p.side} / ${p.role}</span>
                    </div>
                    <button class="admin-btn admin-btn-danger" onclick="KAdmin.kickParticipant('${p.id}')" style="padding:2px 6px;font-size:10px;" title="Remove participant from session">Kick</button>
                </div>`;
            });
            el.innerHTML = html;
        } catch (err) {
            _showInfo('admin-participants-list', `✗ ${err.message}`, 'error');
        }
    }

    async function kickParticipant(participantId) {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;
        if (!await KDialogs.confirm('Kick this participant?', {dangerous: true})) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/participants/${participantId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                _loadParticipants();
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Kick failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    // ── Event Injection ──────────────────────────────

    async function _injectEvent() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-event-status', 'Select a session first', 'error'); return; }
        const text = document.getElementById('admin-event-text').value.trim();
        const type = document.getElementById('admin-event-type').value || 'custom';
        if (!text) { await KDialogs.alert('Enter event text'); return; }
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/events`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ event_type: type, text_summary: text, visibility: 'all' }),
            });
            if (resp.ok) {
                document.getElementById('admin-event-text').value = '';
                _showInfo('admin-event-status', '✓ Event injected', 'success');
            }
        } catch (err) {
            _showInfo('admin-event-status', `✗ ${err.message}`, 'error');
        }
    }

    // ══════════════════════════════════════════════════
    // ── Monitor: God View ────────────────────────────
    // ══════════════════════════════════════════════════

    async function _toggleGodView() {
        const token = _getToken();
        const sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-god-status', 'Select a session first', 'error'); return; }

        _godViewEnabled = !_godViewEnabled;
        const btn = document.getElementById('admin-god-view-toggle');
        if (btn) {
            btn.textContent = _godViewEnabled ? '👁 God View ON' : '👁 God View OFF';
            btn.classList.toggle('admin-btn-active', _godViewEnabled);
        }

        if (_godViewEnabled) {
            _showGodViewBanner();
            await _refreshGodView();
        } else {
            _removeGodViewBanner();
            // Reload normal fog-of-war view — use user's session, fall back to admin session
            const userSid = _getUserSessionId() || sid;
            if (userSid && token) {
                try {
                    await KUnits.load(userSid, token);
                    await KContacts.load(userSid, token);
                } catch (e) {
                    console.warn('God view OFF reload error:', e);
                }
            }
            _showInfo('admin-god-status', 'Normal view restored (blue fog-of-war)');
        }
    }

    /** Show a prominent banner on the map warning that god view is active. */
    function _showGodViewBanner() {
        _removeGodViewBanner();
        const banner = document.createElement('div');
        banner.id = 'god-view-banner';
        banner.className = 'god-view-banner';
        banner.innerHTML = '👁 GOD VIEW — All units visible (fog-of-war disabled)';
        banner.addEventListener('click', () => {
            _toggleGodView();
        });
        banner.title = 'Click to disable God View';
        document.body.appendChild(banner);
    }

    function _removeGodViewBanner() {
        const existing = document.getElementById('god-view-banner');
        if (existing) existing.remove();
    }

    let _godViewRefreshPending = false;
    let _godViewRefreshTimer = null;

    /** Fetch and render all units (god view). Called on toggle and on state_update.
     *  Debounced to prevent overlapping fetch+render calls from rapid state_updates. */
    async function _refreshGodView() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const units = await resp.json();
            // Use update() so viewshed cache is properly invalidated for moved units
            KUnits.update(units);
            _showInfo('admin-god-status', `Showing all ${units.length} units`, 'success');
        } catch (err) {
            _showInfo('admin-god-status', `✗ ${err.message}`, 'error');
        }
        _godViewRefreshPending = false;
    }

    function isGodViewEnabled() { return _godViewEnabled; }

    /**
     * Unified map refresh — respects god view state.
     * Call this instead of KUnits.load() anywhere units might need refreshing.
     * If god view is ON, fetches all units via admin endpoint.
     * Otherwise, loads fog-of-war filtered units normally.
     */
    async function refreshMapUnits() {
        if (_godViewEnabled) {
            await _refreshGodView();
        } else {
            const token = _getToken();
            const sid = _getUserSessionId();
            if (sid && token) {
                try { await KUnits.load(sid, token); } catch(e) { console.warn('refreshMapUnits:', e); }
            }
        }
    }

    /** Called by app.js when a state_update arrives via WebSocket.
     *  If god view is on, re-fetch admin units instead of using fog-of-war data.
     *  Debounced: skips if a refresh is already in flight, throttles to max 1/500ms. */
    async function onStateUpdate(data) {
        if (_godViewEnabled) {
            if (_godViewRefreshPending) return; // skip if already in flight
            _godViewRefreshPending = true;
            clearTimeout(_godViewRefreshTimer);
            _godViewRefreshTimer = setTimeout(() => {
                _refreshGodView();
            }, 200);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Unit Dashboard ───────────────────────────────

    let _dashboardSort = { field: 'side_name', asc: true }; // default: side then name

    function _sortDashboardUnits(units) {
        const f = _dashboardSort.field;
        const dir = _dashboardSort.asc ? 1 : -1;
        return [...units].sort((a, b) => {
            if (f === 'side_name') {
                // Sort by side first (blue < red), then by name
                if (a.side !== b.side) return (a.side === 'blue' ? -1 : 1);
                return (a.name || '').localeCompare(b.name || '') * dir;
            }
            if (f === 'name') return (a.name || '').localeCompare(b.name || '') * dir;
            if (f === 'side') return (a.side || '').localeCompare(b.side || '') * dir;
            if (f === 'status') return (a.unit_status || 'idle').localeCompare(b.unit_status || 'idle') * dir;
            if (f === 'strength') return ((a.strength || 0) - (b.strength || 0)) * dir;
            if (f === 'morale') return ((a.morale || 0) - (b.morale || 0)) * dir;
            if (f === 'ammo') return ((a.ammo || 0) - (b.ammo || 0)) * dir;
            return 0;
        });
    }

    function _sortArrow(field) {
        if (_dashboardSort.field === field) {
            return `<span class="sort-arrow">${_dashboardSort.asc ? '▲' : '▼'}</span>`;
        }
        if (_dashboardSort.field === 'side_name' && (field === 'side' || field === 'name')) {
            return `<span class="sort-arrow">▲</span>`;
        }
        return '';
    }

    function _setDashboardSort(field) {
        if (_dashboardSort.field === field) {
            _dashboardSort.asc = !_dashboardSort.asc;
        } else {
            _dashboardSort.field = field;
            _dashboardSort.asc = true;
        }
        _renderDashboardTable();
    }

    function _renderDashboardTable() {
        const el = document.getElementById('admin-unit-dashboard');
        if (!el || !_dashboardUnits.length) return;

        const sorted = _sortDashboardUnits(_dashboardUnits);

        const statusIcons = {
            idle: '⏸', moving: '🚶', engaging: '⚔', defending: '🛡',
            retreating: '↩', observing: '👁', suppressed: '💥',
            broken: '💔', destroyed: '☠', supporting: '🤝',
        };

        let html = `<table class="admin-dashboard-table"><tr>
            <th class="sortable ${_dashboardSort.field === 'name' || _dashboardSort.field === 'side_name' ? 'sort-active' : ''}" data-sort="name">Unit${_sortArrow('name')}</th>
            <th class="sortable ${_dashboardSort.field === 'side' || _dashboardSort.field === 'side_name' ? 'sort-active' : ''}" data-sort="side">Side${_sortArrow('side')}</th>
            <th class="sortable ${_dashboardSort.field === 'status' ? 'sort-active' : ''}" data-sort="status">Status${_sortArrow('status')}</th>
            <th class="sortable ${_dashboardSort.field === 'strength' ? 'sort-active' : ''}" data-sort="strength">Str${_sortArrow('strength')}</th>
            <th class="sortable ${_dashboardSort.field === 'morale' ? 'sort-active' : ''}" data-sort="morale">Mor${_sortArrow('morale')}</th>
            <th class="sortable ${_dashboardSort.field === 'ammo' ? 'sort-active' : ''}" data-sort="ammo">Ammo${_sortArrow('ammo')}</th>
            <th>Comms</th><th></th></tr>`;

        sorted.forEach(u => {
            const sideClr = u.side === 'blue' ? '#4fc3f7' : '#ef5350';
            const strPct = u.strength != null ? (u.strength * 100).toFixed(0) : '?';
            const morPct = u.morale != null ? (u.morale * 100).toFixed(0) : '?';
            const ammPct = u.ammo != null ? (u.ammo * 100).toFixed(0) : '?';
            const strClr = u.strength > 0.6 ? '#4caf50' : u.strength > 0.3 ? '#ff9800' : '#f44336';
            const status = u.unit_status || 'idle';
            const statusIcon = statusIcons[status] || '•';

            html += `<tr>
                <td style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"><span class="dashboard-unit-name" data-unit-id="${u.id}" data-lat="${u.lat || ''}" data-lon="${u.lon || ''}" title="${u.name} — click to center map">${u.name}</span></td>
                <td style="color:${sideClr};font-weight:700;">${u.side}</td>
                <td style="font-size:10px;" title="${status}">${statusIcon} ${status}</td>
                <td><span style="color:${strClr}">${strPct}%</span></td>
                <td>${morPct}%</td>
                <td>${ammPct}%</td>
                <td style="font-size:10px;">${u.comms_status || '—'}</td>
                <td style="white-space:nowrap;">
                    <button class="admin-btn" onclick="KAdmin.editUnit('${u.id}')" style="padding:1px 5px;font-size:9px;" title="Edit unit settings">✏</button>
                    <button class="admin-btn" onclick="KAdmin.adminSplitUnit('${u.id}')" style="padding:1px 5px;font-size:9px;" title="Split unit into two">✂</button>
                    <button class="admin-btn" onclick="KAdmin.adminMergeUnit('${u.id}')" style="padding:1px 5px;font-size:9px;" title="Merge with nearby same-type unit">🔗</button>
                    <button class="admin-btn" onclick="KAdmin.focusUnit('${u.id}')" style="padding:1px 5px;font-size:9px;" title="Center map on unit">📍</button>
                    <button class="admin-btn admin-btn-danger" onclick="KAdmin.deleteUnit('${u.id}','${u.name.replace(/'/g, "\\'")}')" style="padding:1px 5px;font-size:9px;" title="Delete unit">✕</button>
                </td>
            </tr>`;
        });
        html += '</table>';
        el.innerHTML = html;

        // Bind sort header clicks
        el.querySelectorAll('th.sortable').forEach(th => {
            th.addEventListener('click', () => _setDashboardSort(th.dataset.sort));
        });

        // Bind unit name clicks → center map
        el.querySelectorAll('.dashboard-unit-name').forEach(nameEl => {
            nameEl.addEventListener('click', () => {
                const lat = parseFloat(nameEl.dataset.lat);
                const lon = parseFloat(nameEl.dataset.lon);
                if (!isNaN(lat) && !isNaN(lon)) {
                    const map = KMap.getMap();
                    if (map) map.setView([lat, lon], Math.max(map.getZoom(), 14));
                }
            });
        });
    }

    /** Delete ALL units in the admin-selected session. */
    async function deleteAllUnits() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { await KDialogs.alert('Select a session first'); return; }
        if (!await KDialogs.confirm('⚠ Delete ALL units in this session?\nThis cannot be undone.', {dangerous: true})) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                const data = await resp.json();
                _dashboardUnits = [];
                const el = document.getElementById('admin-unit-dashboard');
                if (el) el.innerHTML = '<div class="admin-info">No units</div>';
                KGameLog.addEntry(`All units deleted: ${data.deleted} (admin)`, 'info');
                KUnits.invalidateAllViewsheds();
                try { await refreshMapUnits(); } catch(e) {}
                try { KContacts.clearAll(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Delete failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    async function _loadUnitDashboard() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-unit-dashboard', 'Select a session first'); return; }

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const units = await resp.json();
            const el = document.getElementById('admin-unit-dashboard');
            if (!el) return;

            if (units.length === 0) {
                el.innerHTML = '<div class="admin-info">No units</div>';
                return;
            }

            // Store units data for editing and sorting
            _dashboardUnits = units;
            _renderDashboardTable();
        } catch (err) {
            _showInfo('admin-unit-dashboard', `✗ ${err.message}`, 'error');
        }
    }

    let _dashboardUnits = [];

    /** Focus map on a unit's position. */
    async function focusUnit(unitId) {
        const unit = _dashboardUnits.find(u => u.id === unitId);
        if (!unit || unit.lat == null || unit.lon == null) {
            await KDialogs.alert('Unit has no position');
            return;
        }
        const map = KMap.getMap();
        if (map) map.setView([unit.lat, unit.lon], Math.max(map.getZoom(), 14));
    }

    /** Delete a unit (admin). */
    async function deleteUnit(unitId, unitName) {
        if (!await KDialogs.confirm(`Delete unit "${unitName}"?`, {dangerous: true})) return;
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units/${unitId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                // Optimistically remove from local dashboard data immediately
                _dashboardUnits = _dashboardUnits.filter(u => u.id !== unitId);
                _renderDashboardTable();
                KUnits.invalidateAllViewsheds();
                // Refresh map units (god-view-aware)
                try { await refreshMapUnits(); } catch(e) { console.warn('Unit refresh after delete:', e); }
                // Re-fetch dashboard from server to confirm
                await _loadUnitDashboard();
                KGameLog.addEntry(`Unit "${unitName}" deleted (admin)`, 'info');
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Delete failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    /** Add a unit mid-session (admin) — opens the edit modal for creation. */
    async function addUnit() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { await KDialogs.alert('Select a session first'); return; }

        // Get map center for default position
        const map = KMap.getMap();
        const center = map ? map.getCenter() : { lat: 49.05, lng: 4.5 };

        const modal = document.getElementById('admin-unit-edit-modal');
        if (!modal) return;

        // Populate unit type dropdown
        const typeEl = document.getElementById('admin-ue-unit-type');
        if (typeEl) {
            typeEl.innerHTML = '';
            const types = typeof KScenarioBuilder !== 'undefined' ? KScenarioBuilder.getUnitTypes() : {};
            for (const [key, info] of Object.entries(types)) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = info.label || key;
                typeEl.appendChild(opt);
            }
            // Auto-update detection range & speed when unit type changes
            typeEl.onchange = () => {
                const info = types[typeEl.value];
                if (info) {
                    if (info.det) _setVal('admin-ue-detection', info.det);
                    if (info.speed) _setVal('admin-ue-speed', info.speed);
                }
                _updateUnitEditPreview();
            };
        }

        // Pre-fill with defaults for new unit
        _setVal('admin-ue-name', 'New Unit');
        _setVal('admin-ue-side', 'blue');
        _setVal('admin-ue-unit-type', 'infantry_platoon');
        _setVal('admin-ue-strength', 100);
        _setVal('admin-ue-morale', 90);
        _setVal('admin-ue-ammo', 100);
        _setVal('admin-ue-suppression', 0);
        _setVal('admin-ue-detection', 1500);
        _setVal('admin-ue-speed', 4);
        _setVal('admin-ue-heading', 0);
        _setVal('admin-ue-comms', 'operational');
        _setVal('admin-ue-lat', center.lat.toFixed(6));
        _setVal('admin-ue-lon', center.lng.toFixed(6));

        // Destroyed checkbox
        const destroyedEl = document.getElementById('admin-ue-destroyed');
        if (destroyedEl) destroyedEl.checked = false;

        // Fire range slider visual updates
        _fireRangeUpdate('admin-ue-strength');
        _fireRangeUpdate('admin-ue-morale');
        _fireRangeUpdate('admin-ue-ammo');
        _fireRangeUpdate('admin-ue-suppression');

        // Label and ID
        const label = document.getElementById('admin-ue-label');
        if (label) label.textContent = 'New Unit';
        const idDisplay = document.getElementById('admin-ue-id-display');
        if (idDisplay) idDisplay.textContent = 'will be assigned on save';

        // Title
        const titleEl = document.getElementById('admin-ue-title');
        if (titleEl) titleEl.textContent = '➕ New Unit';

        const statusEl = document.getElementById('admin-ue-status');
        if (statusEl) { statusEl.textContent = ''; statusEl.className = 'ue-status'; }

        // Symbol preview
        _updateUnitEditPreview();

        // Use __new__ marker so save knows to POST instead of PUT
        modal.dataset.unitId = '__new__';
        modal.classList.remove('ue-dragged');
        modal.style.left = '50%';
        modal.style.top = '50%';
        modal.style.transform = 'translate(-50%, -50%)';
        modal.style.display = 'flex';
    }

    /** Show edit modal for a unit. Also searches KUnits data if not in dashboard. */
    async function editUnit(unitId) {
        let unit = _dashboardUnits.find(u => u.id === unitId);
        // Fall back to map units if not found in dashboard
        if (!unit && typeof KUnits !== 'undefined') {
            try {
                const mapUnits = KUnits.getAllUnits ? KUnits.getAllUnits() : [];
                unit = mapUnits.find(u => u.id === unitId);
            } catch(e) {}
        }
        if (!unit) {
            // Try reloading dashboard first, then try again
            _loadUnitDashboard().then(async () => {
                const u2 = _dashboardUnits.find(u => u.id === unitId);
                if (u2) editUnit(unitId);
                else await KDialogs.alert('Unit not found — try reloading the dashboard');
            });
            return;
        }

        const modal = document.getElementById('admin-unit-edit-modal');
        if (!modal) return;

        // Populate unit type dropdown from scenario builder registry
        const typeEl = document.getElementById('admin-ue-unit-type');
        if (typeEl) {
            typeEl.innerHTML = '';
            const types = typeof KScenarioBuilder !== 'undefined' ? KScenarioBuilder.getUnitTypes() : {};
            for (const [key, info] of Object.entries(types)) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = info.label || key;
                typeEl.appendChild(opt);
            }
            // Auto-update detection range & speed when unit type changes
            typeEl.onchange = () => {
                const info = types[typeEl.value];
                if (info) {
                    if (info.det) _setVal('admin-ue-detection', info.det);
                    if (info.speed) _setVal('admin-ue-speed', info.speed);
                }
                _updateUnitEditPreview();
            };
            // Add current type if not in list
            if (unit.unit_type && !types[unit.unit_type]) {
                const opt = document.createElement('option');
                opt.value = unit.unit_type;
                opt.textContent = unit.unit_type;
                typeEl.appendChild(opt);
            }
            typeEl.value = unit.unit_type || '';
        }

        // Populate fields
        _setVal('admin-ue-name', unit.name);
        _setVal('admin-ue-side', unit.side || 'blue');
        _setVal('admin-ue-strength', unit.strength != null ? Math.round(unit.strength * 100) : 100);
        _setVal('admin-ue-morale', unit.morale != null ? Math.round(unit.morale * 100) : 90);
        _setVal('admin-ue-ammo', unit.ammo != null ? Math.round(unit.ammo * 100) : 100);
        _setVal('admin-ue-suppression', unit.suppression != null ? Math.round(unit.suppression * 100) : 0);
        _setVal('admin-ue-detection', unit.detection_range_m || 1500);
        _setVal('admin-ue-speed', unit.move_speed_mps || 4);
        _setVal('admin-ue-heading', unit.heading_deg != null ? unit.heading_deg : 0);
        _setVal('admin-ue-comms', unit.comms_status || 'operational');
        _setVal('admin-ue-lat', unit.lat != null ? unit.lat.toFixed(6) : '');
        _setVal('admin-ue-lon', unit.lon != null ? unit.lon.toFixed(6) : '');

        // Destroyed checkbox
        const destroyedEl = document.getElementById('admin-ue-destroyed');
        if (destroyedEl) destroyedEl.checked = !!unit.is_destroyed;

        // Fire range slider visual updates
        _fireRangeUpdate('admin-ue-strength');
        _fireRangeUpdate('admin-ue-morale');
        _fireRangeUpdate('admin-ue-ammo');
        _fireRangeUpdate('admin-ue-suppression');

        // Label and ID
        const label = document.getElementById('admin-ue-label');
        if (label) label.textContent = unit.name;
        const idDisplay = document.getElementById('admin-ue-id-display');
        if (idDisplay) idDisplay.textContent = unitId.substring(0, 8) + '…';

        // Title
        const titleEl = document.getElementById('admin-ue-title');
        if (titleEl) titleEl.textContent = '✏ Edit Unit';

        // Status
        const statusEl = document.getElementById('admin-ue-status');
        if (statusEl) { statusEl.textContent = ''; statusEl.className = 'ue-status'; }

        // Symbol preview
        _updateUnitEditPreview();

        modal.dataset.unitId = unitId;
        modal.classList.remove('ue-dragged');
        modal.style.left = '50%';
        modal.style.top = '50%';
        modal.style.transform = 'translate(-50%, -50%)';
        modal.style.display = 'flex';
    }

    function _setVal(id, val) {
        const el = document.getElementById(id);
        if (el && val != null) el.value = val;
    }

    async function _saveUnitEdit() {
        const modal = document.getElementById('admin-unit-edit-modal');
        if (!modal) return;
        const unitId = modal.dataset.unitId;
        if (!unitId) return;

        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;

        const statusEl = document.getElementById('admin-ue-status');

        const body = {};
        const nameVal = document.getElementById('admin-ue-name').value.trim();
        if (nameVal) body.name = nameVal;

        // Side and unit type
        const sideEl = document.getElementById('admin-ue-side');
        const typeEl = document.getElementById('admin-ue-unit-type');
        if (sideEl && sideEl.value) body.side = sideEl.value;
        if (typeEl && typeEl.value) {
            body.unit_type = typeEl.value;
            // Update SIDC based on side and type
            const types = typeof KScenarioBuilder !== 'undefined' ? KScenarioBuilder.getUnitTypes() : {};
            const info = types[typeEl.value];
            if (info) {
                const side = sideEl ? sideEl.value : 'blue';
                body.sidc = side === 'red' ? (info.sidc_red || '') : (info.sidc_blue || '');
            }
        }

        // Range slider values (0-100 → 0.0-1.0)
        const str = parseFloat(document.getElementById('admin-ue-strength').value);
        if (!isNaN(str)) body.strength = Math.max(0, Math.min(1, str / 100));

        const mor = parseFloat(document.getElementById('admin-ue-morale').value);
        if (!isNaN(mor)) body.morale = Math.max(0, Math.min(1, mor / 100));

        const amm = parseFloat(document.getElementById('admin-ue-ammo').value);
        if (!isNaN(amm)) body.ammo = Math.max(0, Math.min(1, amm / 100));

        const sup = parseFloat(document.getElementById('admin-ue-suppression').value);
        if (!isNaN(sup)) body.suppression = Math.max(0, Math.min(1, sup / 100));

        // Numeric fields
        const det = parseFloat(document.getElementById('admin-ue-detection').value);
        if (!isNaN(det)) body.detection_range_m = Math.max(0, det);

        const spd = parseFloat(document.getElementById('admin-ue-speed').value);
        if (!isNaN(spd)) body.move_speed_mps = Math.max(0, spd);

        const hdg = parseFloat(document.getElementById('admin-ue-heading').value);
        if (!isNaN(hdg)) body.heading_deg = ((hdg % 360) + 360) % 360;

        // Comms status
        const commsEl = document.getElementById('admin-ue-comms');
        if (commsEl && commsEl.value) body.comms_status = commsEl.value;

        // Position (lat/lon)
        const latEl = document.getElementById('admin-ue-lat');
        const lonEl = document.getElementById('admin-ue-lon');
        if (latEl && lonEl) {
            const lat = parseFloat(latEl.value);
            const lon = parseFloat(lonEl.value);
            if (!isNaN(lat) && !isNaN(lon)) {
                body.lat = lat;
                body.lon = lon;
            }
        }

        // Destroyed
        const destroyedEl = document.getElementById('admin-ue-destroyed');
        if (destroyedEl) body.is_destroyed = destroyedEl.checked;

        try {
            const isNew = unitId === '__new__';
            const url = isNew
                ? `/api/admin/sessions/${sid}/units`
                : `/api/admin/sessions/${sid}/units/${unitId}`;
            const method = isNew ? 'POST' : 'PUT';

            const resp = await fetch(url, {
                method,
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify(body),
            });
            if (resp.ok) {
                const verb = isNew ? 'Created' : 'Saved';
                if (statusEl) { statusEl.textContent = `✓ ${verb}`; statusEl.className = 'ue-status admin-success'; }
                setTimeout(async () => {
                    _closeUnitEdit();
                    _loadUnitDashboard();
                    // Refresh map units if this is the active session
                    const userSid = _getUserSessionId();
                    if (sid === userSid || _godViewEnabled) {
                        try {
                            // Clear viewshed cache so new/updated units get fresh LoS
                            KUnits.invalidateAllViewsheds();
                            if (_godViewEnabled) await _refreshGodView();
                            else await KUnits.load(userSid, token);
                        } catch(e) { console.warn('Unit refresh after save:', e); }
                    }
                }, 300);
            } else {
                const d = await resp.json().catch(() => ({}));
                if (statusEl) { statusEl.textContent = `✗ ${d.detail || 'Failed'}`; statusEl.className = 'ue-status admin-error'; }
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'ue-status admin-error'; }
        }
    }

    // ══════════════════════════════════════════════════
    // ── All Orders ───────────────────────────────────

    async function _loadAllOrders() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-orders-list', 'Select a session first'); return; }

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/orders`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const orders = await resp.json();
            const el = document.getElementById('admin-orders-list');
            if (!el) return;

            if (orders.length === 0) {
                el.innerHTML = '<div class="admin-info">No orders</div>';
                return;
            }

            let html = '';
            orders.forEach(o => {
                const sideClr = o.issued_by_side === 'blue' ? '#4fc3f7' : '#ef5350';
                html += `<div class="admin-item" style="border-left:3px solid ${sideClr};">
                    <div style="flex:1;min-width:0;">
                        <div style="font-size:11px;color:#aaa;">[${o.status || '?'}] ${o.order_type || ''}</div>
                        <div style="font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${o.original_text || '—'}</div>
                    </div>
                </div>`;
            });
            el.innerHTML = html;
        } catch (err) {
            _showInfo('admin-orders-list', `✗ ${err.message}`, 'error');
        }
    }

    // ── DB Stats ─────────────────────────────────────

    async function _loadDbStats() {
        const el = document.getElementById('admin-db-info');
        if (!el) return;
        // Toggle: if already showing stats, collapse
        if (el.innerHTML.trim()) {
            el.innerHTML = '';
            return;
        }
        const token = _getToken();
        if (!token) { _showInfo('admin-db-info', 'Not logged in', 'error'); return; }
        try {
            const resp = await fetch('/api/admin/stats', {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                const stats = await resp.json();
                let html = '<table class="admin-stats-table">';
                for (const [table, count] of Object.entries(stats)) {
                    html += `<tr><td>${table}</td><td>${count}</td></tr>`;
                }
                html += '</table>';
                el.innerHTML = html;
            } else {
                _showInfo('admin-db-info', 'Stats endpoint not available');
            }
        } catch (err) {
            _showInfo('admin-db-info', `✗ ${err.message}`, 'error');
        }
    }

    // ══════════════════════════════════════════════════
    // ── Debug Log Management ────────────────────────
    // ══════════════════════════════════════════════════

    let _debugLogEnabled = false;

    async function _checkDebugLogStatus() {
        try {
            const resp = await fetch('/api/admin/debug-log/status');
            if (resp.ok) {
                const data = await resp.json();
                _debugLogEnabled = data.enabled;
                _updateDebugLogUI();
            }
        } catch (e) { /* ignore */ }
    }

    function _updateDebugLogUI() {
        const btn = document.getElementById('admin-debug-log-toggle');
        const viewBtn = document.getElementById('admin-debug-log-view');
        const clearBtn = document.getElementById('admin-debug-log-clear');
        const status = document.getElementById('admin-debug-log-status');
        if (btn) {
            btn.textContent = _debugLogEnabled ? '📝 Debug Log ON' : '📝 Debug Log OFF';
            btn.style.background = _debugLogEnabled ? '#1b5e20' : '';
            btn.style.color = _debugLogEnabled ? '#a5d6a7' : '';
        }
        if (viewBtn) viewBtn.style.display = _debugLogEnabled ? '' : 'none';
        if (clearBtn) clearBtn.style.display = _debugLogEnabled ? '' : 'none';
        if (status) status.textContent = _debugLogEnabled ? 'Recording tick data to debug_log.txt' : '';
    }

    async function _toggleDebugLog() {
        try {
            const endpoint = _debugLogEnabled ? '/api/admin/debug-log/disable' : '/api/admin/debug-log/enable';
            const resp = await fetch(endpoint, { method: 'POST' });
            if (resp.ok) {
                const data = await resp.json();
                _debugLogEnabled = !!data.enabled;
                _updateDebugLogUI();
                if (_debugLogEnabled && data.path) {
                    _showInfo('admin-debug-log-status', `Recording → ${data.path}`);
                }
            }
        } catch (err) {
            _showInfo('admin-debug-log-status', `✗ ${err.message}`, 'error');
        }
    }

    async function _viewDebugLog() {
        const viewer = document.getElementById('admin-debug-log-viewer');
        const contents = document.getElementById('admin-debug-log-contents');
        if (!viewer || !contents) return;
        // Toggle visibility
        if (viewer.style.display !== 'none') {
            viewer.style.display = 'none';
            return;
        }
        try {
            const resp = await fetch('/api/admin/debug-log/contents?tail=500');
            if (resp.ok) {
                const data = await resp.json();
                contents.value = data.contents || '(empty)';
                viewer.style.display = 'block';
                // Auto-scroll to bottom
                contents.scrollTop = contents.scrollHeight;
            }
        } catch (err) {
            contents.value = `Error: ${err.message}`;
            viewer.style.display = 'block';
        }
    }

    async function _clearDebugLog() {
        try {
            await fetch('/api/admin/debug-log/clear', { method: 'POST' });
            _showInfo('admin-debug-log-status', 'Log cleared');
            const contents = document.getElementById('admin-debug-log-contents');
            if (contents) contents.value = '(cleared)';
        } catch (err) {
            _showInfo('admin-debug-log-status', `✗ ${err.message}`, 'error');
        }
    }

    // ══════════════════════════════════════════════════
    // ── User Management ─────────────────────────────
    // ══════════════════════════════════════════════════

    async function _loadUsers() {
        try {
            const resp = await fetch('/api/admin/users');
            if (!resp.ok) { _showInfo('admin-users-list', 'Failed to load', 'error'); return; }
            const users = await resp.json();
            const el = document.getElementById('admin-users-list');
            if (!el) return;

            // Reset select-all
            const selectAllCb = document.getElementById('admin-users-select-all');
            if (selectAllCb) selectAllCb.checked = false;

            if (users.length === 0) {
                el.innerHTML = '<div class="admin-info">No registered users</div>';
                return;
            }

            let html = '';
            users.forEach(u => {
                const created = u.created_at ? new Date(u.created_at).toLocaleDateString() : '';
                html += `<div class="admin-item">
                    <div style="display:flex;align-items:center;gap:6px;flex:1;min-width:0;">
                        <input type="checkbox" class="admin-user-cb" data-user-id="${u.id}" style="cursor:pointer;flex-shrink:0;" />
                        <div style="min-width:0;">
                            <b title="User ID: ${u.id}">${u.display_name}</b>
                            <span class="admin-item-meta" title="Registered on ${created}">${created}</span>
                        </div>
                    </div>
                    <div style="display:flex;gap:3px;">
                        <button class="admin-btn" onclick="KAdmin.assignUserToSession('${u.id}','${u.display_name.replace(/'/g, "\\'")}')" style="padding:2px 6px;font-size:10px;" title="Assign this user to the selected session">🎯</button>
                        <button class="admin-btn" onclick="KAdmin.renameUser('${u.id}','${u.display_name.replace(/'/g, "\\'")}')" style="padding:2px 6px;font-size:10px;" title="Rename this user">✏</button>
                        <button class="admin-btn admin-btn-danger" onclick="KAdmin.deleteUser('${u.id}','${u.display_name.replace(/'/g, "\\'")}')" style="padding:2px 6px;font-size:10px;" title="Delete this user">✕</button>
                    </div>
                </div>`;
            });
            el.innerHTML = html;
        } catch (err) {
            _showInfo('admin-users-list', `✗ ${err.message}`, 'error');
        }
    }

    async function _bulkDeleteUsers() {
        const checkboxes = document.querySelectorAll('.admin-user-cb:checked');
        if (checkboxes.length === 0) { await KDialogs.alert('No users selected'); return; }
        if (!await KDialogs.confirm(`⚠ Delete ${checkboxes.length} selected user(s)?`, {dangerous: true})) return;

        const userIds = Array.from(checkboxes).map(cb => cb.dataset.userId);
        try {
            const resp = await fetch('/api/admin/users/bulk-delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_ids: userIds }),
            });
            if (resp.ok) {
                const data = await resp.json();
                _showInfo('admin-users-list', `✓ Deleted ${data.deleted} user(s)`, 'success');
                setTimeout(_loadUsers, 500);
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Bulk delete failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    async function _addUser() {
        const nameEl = document.getElementById('admin-add-user-name');
        if (!nameEl) return;
        const name = nameEl.value.trim();
        if (!name) { await KDialogs.alert('Enter a display name'); return; }

        try {
            const resp = await fetch('/api/admin/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: name }),
            });
            if (resp.ok) {
                nameEl.value = '';
                _loadUsers();
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    async function renameUser(userId, currentName) {
        const newName = await KDialogs.prompt('New display name:', currentName);
        if (!newName || newName.trim() === currentName) return;
        try {
            await fetch(`/api/admin/users/${userId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: newName.trim() }),
            });
            _loadUsers();
        } catch (err) { await KDialogs.alert(err.message); }
    }

    async function deleteUser(userId, name) {
        if (!await KDialogs.confirm(`Delete user "${name}"? This will also remove them from all sessions.`, {dangerous: true})) return;
        const token = _getToken();
        try {
            const resp = await fetch(`/api/admin/users/${userId}`, {
                method: 'DELETE',
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            if (resp.ok || resp.status === 204) {
                _loadUsers();
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Delete failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    // ── Assign User to Session (from Users panel) ────

    let _assignPendingUserId = null;
    let _assignPendingDisplayName = null;

    async function assignUserToSession(userId, displayName) {
        const sid = _getAdminSessionId();
        if (!sid) { await KDialogs.alert('Select a session first in the admin session selector.'); return; }

        _assignPendingUserId = userId;
        _assignPendingDisplayName = displayName;

        // Populate modal
        const label = document.getElementById('admin-assign-user-label');
        if (label) label.textContent = `Assign "${displayName}" to session ${sid.substring(0, 8)}…`;

        // Reset to defaults
        const sideEl = document.getElementById('admin-assign-side');
        const roleEl = document.getElementById('admin-assign-role');
        if (sideEl) sideEl.value = 'blue';
        _updateRoleOptions();  // update role options based on side
        if (roleEl) roleEl.value = 'commander';

        const statusEl = document.getElementById('admin-assign-status');
        if (statusEl) statusEl.textContent = '';

        // Show modal
        const modal = document.getElementById('admin-assign-modal');
        if (modal) modal.style.display = 'flex';
    }

    function _initAssignModal() {
        _bind('admin-assign-confirm', 'click', _doAssignUser);
        _bind('admin-assign-cancel', 'click', _closeAssignModal);
        _bind('admin-assign-modal-close', 'click', _closeAssignModal);

        // Restrict role options based on side selection
        const sideEl = document.getElementById('admin-assign-side');
        if (sideEl) {
            sideEl.addEventListener('change', _updateRoleOptions);
        }

        // Close on overlay click
        const overlay = document.getElementById('admin-assign-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) _closeAssignModal();
            });
        }
    }

    /** Update role dropdown based on selected side. Observer can only be observer. */
    function _updateRoleOptions() {
        const sideEl = document.getElementById('admin-assign-side');
        const roleEl = document.getElementById('admin-assign-role');
        if (!sideEl || !roleEl) return;

        const side = sideEl.value;
        roleEl.innerHTML = '';

        if (side === 'observer') {
            const opt = document.createElement('option');
            opt.value = 'observer';
            opt.textContent = 'Observer';
            roleEl.appendChild(opt);
        } else {
            ['commander', 'officer', 'observer'].forEach(role => {
                const opt = document.createElement('option');
                opt.value = role;
                opt.textContent = role.charAt(0).toUpperCase() + role.slice(1);
                roleEl.appendChild(opt);
            });
        }
    }

    function _closeAssignModal() {
        const modal = document.getElementById('admin-assign-modal');
        if (modal) modal.style.display = 'none';
        _assignPendingUserId = null;
        _assignPendingDisplayName = null;
    }

    async function _doAssignUser() {
        if (!_assignPendingUserId) return;
        const sid = _getAdminSessionId();
        const token = _getToken();
        if (!sid || !token) return;

        const side = document.getElementById('admin-assign-side').value;
        const role = document.getElementById('admin-assign-role').value;
        const statusEl = document.getElementById('admin-assign-status');

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/add-participant`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ user_id: _assignPendingUserId, side, role }),
            });
            if (resp.ok) {
                if (statusEl) { statusEl.textContent = `✓ ${_assignPendingDisplayName} added as ${side} / ${role}`; statusEl.className = 'admin-info admin-success'; }
                _loadParticipants();
                setTimeout(_closeAssignModal, 800);
            } else {
                const d = await resp.json().catch(() => ({}));
                if (statusEl) { statusEl.textContent = `✗ ${d.detail || 'Failed'}`; statusEl.className = 'admin-info admin-error'; }
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'admin-info admin-error'; }
        }
    }

    // ══════════════════════════════════════════════════
    // ── Chain of Command (CoC) – Admin Sub-Tab ──────
    // ══════════════════════════════════════════════════

    async function _loadChainOfCommand() {
        const token = _getToken();
        const sid = _getAdminSessionId();
        if (!token || !sid) {
            _showInfo('admin-coc-tree', 'Select a session first');
            return;
        }

        const el = document.getElementById('admin-coc-tree');

        try {
            // Load participants for user-assign modal (admin endpoint)
            await _loadParticipantsForAdminCoC(sid);

            const resp = await fetch(`/api/admin/sessions/${sid}/unit-hierarchy`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({}));
                const errMsg = errData.detail || `HTTP ${resp.status}`;
                if (el) el.innerHTML = `<div class="admin-info admin-error">✗ ${errMsg}</div>`;
                return;
            }
            const units = await resp.json();
            if (units.length === 0) {
                if (el) el.innerHTML = '<div class="admin-info">No units in this session (is it started?)</div>';
                return;
            }
            _renderCoCTree(units, 'admin-coc-tree', true, units);
        } catch (err) {
            if (el) el.innerHTML = `<div class="admin-info admin-error">✗ ${err.message}</div>`;
        }
    }

    /** Load participants for admin CoC using admin endpoint. */
    async function _loadParticipantsForAdminCoC(sessionId) {
        const token = _getToken();
        if (!token || !sessionId) return [];
        try {
            const resp = await fetch(`/api/admin/sessions/${sessionId}/participants`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                _cachedParticipants = await resp.json();
                return _cachedParticipants;
            }
        } catch (err) {
            console.warn('Load participants for admin CoC:', err);
        }
        return [];
    }

    // ══════════════════════════════════════════════════
    // ── Chain of Command – Public Tab ────────────────
    // ══════════════════════════════════════════════════

    /** Cached participants for the current session (for CoC user picker). */
    let _cachedParticipants = [];
    /** Preserve the last selected user in bulk assign dropdown across re-renders. */
    let _lastBulkUserId = '';

    async function _loadParticipantsForCoC(sessionId) {
        const token = _getToken();
        if (!token || !sessionId) return [];
        try {
            const resp = await fetch(`/api/sessions/${sessionId}/participants`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok) {
                _cachedParticipants = await resp.json();
                return _cachedParticipants;
            }
        } catch (err) {
            console.warn('Load participants for CoC:', err);
        }
        return [];
    }

    async function loadPublicCoC() {
        const token = _getToken();
        const sid = _getUserSessionId();
        const el = document.getElementById('coc-tree-public');

        if (!token || !sid) {
            if (el) el.innerHTML = '<div class="admin-info">Join a session first</div>';
            return;
        }

        try {
            // Load participants for the user-assign modal
            await _loadParticipantsForCoC(sid);

            // Use the public hierarchy endpoint (works for any participant)
            let units = null;
            const resp = await fetch(`/api/sessions/${sid}/units/hierarchy`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });

            if (resp.ok) {
                units = await resp.json();
                // Public CoC should only show own-side units (unless admin panel is open)
                if (!_adminUnlocked) {
                    const mySide = typeof KSessionUI !== 'undefined' && KSessionUI.getSide ? KSessionUI.getSide() : 'blue';
                    if (mySide && mySide !== 'admin' && mySide !== 'observer') {
                        units = units.filter(u => u.side === mySide);
                    }
                }
            } else {
                // Fallback to admin endpoint if available
                const adminResp = await fetch(`/api/admin/sessions/${sid}/unit-hierarchy`, {
                    headers: { 'Authorization': `Bearer ${token}` },
                });
                if (adminResp.ok) {
                    const allUnits = await adminResp.json();
                    units = _adminUnlocked ? allUnits : allUnits.filter(u => u.side === 'blue');
                }
            }

            if (!units || units.length === 0) {
                if (el) el.innerHTML = '<div class="admin-info">No units available (is the session started?)</div>';
                return;
            }

            // Editable if user has any assigned units (commander) or is admin
            _renderCoCTree(units, 'coc-tree-public', true, units);
        } catch (err) {
            if (el) el.innerHTML = `<div class="admin-info admin-error">✗ ${err.message}</div>`;
        }
    }

    // ══════════════════════════════════════════════════
    // ── CoC Tree Rendering (shared) ─────────────────
    // ══════════════════════════════════════════════════

    /**
     * Check if the current user has command authority over a unit.
     * User can manage a unit if:
     *   1. They are directly assigned to a proper ancestor (they command the parent unit).
     *   2. They are directly assigned to this unit (they can reassign it).
     *   3. The unit is assigned to a subordinate user (a user whose own units
     *      are descendants of a unit the current user commands).
     * They should NOT be able to manage units above them in the hierarchy.
     */
    function _userCanAssign(unit, allUnitMap) {
        const userId = KSessionUI.getUserId();
        if (!userId) return false;
        const mySide = KSessionUI.getSide();
        const myRole = KSessionUI.getRole();
        // Observers cannot assign
        if (mySide === 'observer' || myRole === 'observer') return false;

        // Case 0: If no units on the same side have any assigned_user_ids,
        // any same-side player has implicit authority (no CoC configured yet)
        const sameSideUnits = Object.values(allUnitMap).filter(u => u.side === unit.side);
        const anyAssigned = sameSideUnits.some(u => u.assigned_user_ids && u.assigned_user_ids.length > 0);
        if (!anyAssigned) {
            // Any same-side user (or admin) can assign when no CoC is set up
            if (mySide === 'admin' || mySide === unit.side) return true;
            return false;
        }

        // Case 1: User is directly assigned to this unit (can manage/reassign)
        if (unit.assigned_user_ids && unit.assigned_user_ids.includes(userId)) {
            return true;
        }

        // Case 2: User is assigned to a proper ancestor (has downward authority)
        let parentId = unit.parent_unit_id;
        const visited = new Set();
        while (parentId && allUnitMap[parentId]) {
            if (visited.has(parentId)) break;
            visited.add(parentId);

            const parent = allUnitMap[parentId];
            if (parent.assigned_user_ids && parent.assigned_user_ids.includes(userId)) {
                return true;
            }
            parentId = parent.parent_unit_id;
        }

        // Case 3: Subordinate user authority — unit is assigned to a user who is subordinate
        if (unit.assigned_user_ids && unit.assigned_user_ids.length > 0) {
            for (const assignedUid of unit.assigned_user_ids) {
                if (assignedUid === userId) continue;
                // Check if assignedUid is subordinate to userId
                const subUnits = Object.values(allUnitMap).filter(u =>
                    u.assigned_user_ids && u.assigned_user_ids.includes(assignedUid)
                );
                for (const subUnit of subUnits) {
                    let pid = subUnit.parent_unit_id;
                    const vis2 = new Set();
                    while (pid && allUnitMap[pid]) {
                        if (vis2.has(pid)) break;
                        vis2.add(pid);
                        const p = allUnitMap[pid];
                        if (p.assigned_user_ids && p.assigned_user_ids.includes(userId)) {
                            return true;
                        }
                        pid = p.parent_unit_id;
                    }
                }
            }
        }

        return false;
    }

    // ── Drag-and-drop state for CoC tree ──────────
    let _cocDragUnitId = null;
    let _cocDragSide = null;

    function _renderCoCTree(units, targetElId, editable, allUnits) {
        const el = document.getElementById(targetElId);
        if (!el) return;

        if (units.length === 0) {
            el.innerHTML = '<div class="admin-info">No units in session</div>';
            return;
        }

        // Build a lookup map from ALL units (including ones not in 'units')
        // so we can resolve parent names even if parent is filtered out
        const allUnitMap = {};
        (allUnits || units).forEach(u => { allUnitMap[u.id] = u; });

        // Build tree structure from the visible units
        const unitMap = {};
        units.forEach(u => { unitMap[u.id] = { ...u, children: [] }; });

        const roots = [];
        units.forEach(u => {
            if (u.parent_unit_id && unitMap[u.parent_unit_id]) {
                unitMap[u.parent_unit_id].children.push(unitMap[u.id]);
            } else {
                roots.push(unitMap[u.id]);
            }
        });

        // Sort: blue first, then red, then by name
        const sortFn = (a, b) => {
            if (a.side !== b.side) return a.side === 'blue' ? -1 : 1;
            return (a.name || '').localeCompare(b.name || '');
        };
        roots.sort(sortFn);
        Object.values(unitMap).forEach(u => u.children.sort(sortFn));

        // Determine if the current user can modify hierarchy
        const canModifyHierarchy = _adminUnlocked || (
            KSessionUI.getSide() !== 'observer' && KSessionUI.getRole() !== 'observer'
        );

        // Render tree
        let html = '';

        // Bulk action bar (admin only)
        if (_adminUnlocked) {
            html += `<div class="coc-bulk-bar">
                <label style="font-size:10px;color:#aaa;cursor:pointer;display:flex;align-items:center;gap:3px;">
                    <input type="checkbox" id="coc-bulk-select-all" style="cursor:pointer;" /> Select All
                </label>
                <select id="coc-bulk-user-select" style="flex:1;min-width:80px;padding:2px 4px;font-size:10px;background:#1a1a2e;color:#e0e0e0;border:1px solid #0f3460;border-radius:3px;">
                    <option value="">— Select user —</option>
                </select>
                <button class="admin-btn" id="coc-bulk-assign-btn" style="padding:2px 8px;font-size:10px;background:#1b5e20;color:#a5d6a7;" title="Assign selected user to all checked units">👤 Assign</button>
                <button class="admin-btn admin-btn-danger" id="coc-bulk-unassign-btn" style="padding:2px 8px;font-size:10px;" title="Unassign all checked units">✕ Unassign</button>
            </div>`;
        }

        // Drag hint for hierarchy
        if (canModifyHierarchy) {
            html += `<div class="coc-drag-hint">💡 Drag units onto others to set parent. Use ⬆⬇ to reorder.</div>`;
        }

        html += '<div class="coc-tree">';

        const blueRoots = roots.filter(u => u.side === 'blue');
        const redRoots = roots.filter(u => u.side === 'red');

        if (blueRoots.length > 0) {
            html += '<div class="coc-side-header" style="color:#4fc3f7;">BLUE FORCE</div>';
            blueRoots.forEach(u => { html += _renderCoCNode(u, 0, units, editable, allUnitMap, canModifyHierarchy); });
        }
        if (redRoots.length > 0) {
            html += '<div class="coc-side-header" style="color:#ef5350;margin-top:8px;">RED FORCE</div>';
            redRoots.forEach(u => { html += _renderCoCNode(u, 0, units, editable, allUnitMap, canModifyHierarchy); });
        }

        html += '</div>';
        el.innerHTML = html;

        // ── Bind hierarchy structure buttons ──────────
        if (editable) {
            // Parent picker button (⬆ assign to parent)
            el.querySelectorAll('.coc-assign-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const unitId = btn.dataset.unitId;
                    _showParentPicker(unitId, units);
                });
            });

            // Detach from parent (✕)
            el.querySelectorAll('.coc-unassign-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const unitId = btn.dataset.unitId;
                    _setCoCParent(unitId, null);
                });
            });

            // Move up within siblings
            el.querySelectorAll('.coc-move-up-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    // Move up is conceptual reordering — we'll detach from parent to make it a root
                    // (since ordering is by name, true reorder would need a sort_order field;
                    //  instead, "move up" changes parent to grandparent)
                    const unitId = btn.dataset.unitId;
                    const unit = allUnitMap[unitId];
                    if (unit && unit.parent_unit_id) {
                        const parent = allUnitMap[unit.parent_unit_id];
                        const grandparentId = parent ? parent.parent_unit_id : null;
                        _setCoCParent(unitId, grandparentId || null);
                    }
                });
            });

            // Move down = become child of the sibling above
            el.querySelectorAll('.coc-move-down-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const unitId = btn.dataset.unitId;
                    const unit = allUnitMap[unitId];
                    if (!unit) return;
                    // Find siblings (same parent, same side)
                    const siblings = units.filter(u =>
                        u.id !== unitId && u.side === unit.side && u.parent_unit_id === unit.parent_unit_id
                    );
                    if (siblings.length > 0) {
                        // Pick first sibling as new parent (nearest in hierarchy)
                        _showQuickParentPicker(unitId, siblings, allUnitMap);
                    }
                });
            });
        }

        // Bind user-assignment buttons (assign commander to unit)
        el.querySelectorAll('.coc-user-assign-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const unitId = btn.dataset.unitId;
                const unit = allUnitMap[unitId];
                if (unit) _showUserAssignModal(unit);
            });
        });

        // Bind click-on-name to center map on unit position
        el.querySelectorAll('.coc-name[data-unit-id]').forEach(nameEl => {
            nameEl.addEventListener('click', (e) => {
                e.stopPropagation();
                const lat = parseFloat(nameEl.dataset.lat);
                const lon = parseFloat(nameEl.dataset.lon);
                if (!isNaN(lat) && !isNaN(lon)) {
                    const map = KMap.getMap();
                    if (map) map.setView([lat, lon], Math.max(map.getZoom(), 14));
                }
            });
        });

        // ── Drag-and-drop for hierarchy ──────────────
        if (canModifyHierarchy) {
            _bindCoCDragAndDrop(el, allUnitMap, units);
        }

        // Bind bulk action bar (admin only)
        // NOTE: use el.querySelector instead of document.getElementById because
        // both admin and public CoC trees share _renderCoCTree and may create
        // duplicate IDs when _adminUnlocked is true. Scoping to `el` ensures
        // we bind to the correct container's elements.
        if (_adminUnlocked) {
            // Populate bulk user select from cached participants (exclude observers)
            const bulkUserSel = el.querySelector('#coc-bulk-user-select');
            if (bulkUserSel && _cachedParticipants.length > 0) {
                bulkUserSel.innerHTML = '<option value="">— Select user —</option>';
                _cachedParticipants.filter(p => p.side !== 'observer' && p.role !== 'observer').forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.user_id;
                    const sideIcon = p.side === 'blue' ? '🔵' : p.side === 'red' ? '🔴' : '⚪';
                    opt.textContent = `${sideIcon} ${p.display_name} (${p.role})`;
                    bulkUserSel.appendChild(opt);
                });
                // Restore last selected user
                if (_lastBulkUserId) {
                    bulkUserSel.value = _lastBulkUserId;
                }
                // Track selection changes
                bulkUserSel.addEventListener('change', () => {
                    _lastBulkUserId = bulkUserSel.value;
                });
            } else if (bulkUserSel && _cachedParticipants.length === 0) {
                // Participants not loaded yet — try loading them now
                const sid = _getAdminSessionId() || _getUserSessionId();
                if (sid) {
                    _loadParticipantsForAdminCoC(sid).then(() => {
                        if (_cachedParticipants.length > 0) {
                            bulkUserSel.innerHTML = '<option value="">— Select user —</option>';
                            _cachedParticipants.filter(p => p.side !== 'observer' && p.role !== 'observer').forEach(p => {
                                const opt = document.createElement('option');
                                opt.value = p.user_id;
                                const sideIcon = p.side === 'blue' ? '🔵' : p.side === 'red' ? '🔴' : '⚪';
                                opt.textContent = `${sideIcon} ${p.display_name} (${p.role})`;
                                bulkUserSel.appendChild(opt);
                            });
                            if (_lastBulkUserId) bulkUserSel.value = _lastBulkUserId;
                            bulkUserSel.addEventListener('change', () => { _lastBulkUserId = bulkUserSel.value; });
                        }
                    });
                }
            }

            // Select-all checkbox (scoped to this container)
            // Side-aware: when a user is selected in dropdown, only select units matching user's side
            const selectAllCb = el.querySelector('#coc-bulk-select-all');
            if (selectAllCb) {
                selectAllCb.addEventListener('change', () => {
                    const bulkSel = el.querySelector('#coc-bulk-user-select');
                    const selUserId = bulkSel ? bulkSel.value : '';
                    const selParticipant = selUserId ? _cachedParticipants.find(p => p.user_id === selUserId) : null;
                    const filterSide = (selParticipant && selParticipant.side !== 'admin') ? selParticipant.side : null;
                    el.querySelectorAll('.coc-bulk-cb').forEach(cb => {
                        if (filterSide && cb.dataset.side !== filterSide) {
                            cb.checked = false; // Don't select units from wrong side
                        } else {
                            cb.checked = selectAllCb.checked;
                        }
                    });
                });
            }

            // Bulk assign button (scoped to this container)
            const bulkAssignBtn = el.querySelector('#coc-bulk-assign-btn');
            if (bulkAssignBtn) bulkAssignBtn.addEventListener('click', _doBulkAssign);

            // Bulk unassign button (scoped to this container)
            const bulkUnassignBtn = el.querySelector('#coc-bulk-unassign-btn');
            if (bulkUnassignBtn) bulkUnassignBtn.addEventListener('click', _doBulkUnassign);
        }
    }

    /** Bind drag-and-drop events on CoC tree nodes. */
    function _bindCoCDragAndDrop(container, allUnitMap, allUnits) {
        const nodes = container.querySelectorAll('.coc-node[draggable="true"]');

        nodes.forEach(node => {
            node.addEventListener('dragstart', (e) => {
                _cocDragUnitId = node.dataset.unitId;
                _cocDragSide = node.dataset.side;
                node.classList.add('coc-dragging');
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', _cocDragUnitId);
            });

            node.addEventListener('dragend', () => {
                _cocDragUnitId = null;
                _cocDragSide = null;
                node.classList.remove('coc-dragging');
                // Clear all drop targets
                container.querySelectorAll('.coc-drop-target, .coc-drop-target-above, .coc-drop-target-invalid').forEach(n => {
                    n.classList.remove('coc-drop-target', 'coc-drop-target-above', 'coc-drop-target-invalid');
                });
            });

            node.addEventListener('dragover', (e) => {
                e.preventDefault();
                if (!_cocDragUnitId || _cocDragUnitId === node.dataset.unitId) return;
                if (_cocDragSide !== node.dataset.side) {
                    node.classList.add('coc-drop-target-invalid');
                    e.dataTransfer.dropEffect = 'none';
                    return;
                }
                e.dataTransfer.dropEffect = 'move';

                // Determine drop zone: top 25% = "above" (become sibling), bottom 75% = "inside" (become child)
                const rect = node.getBoundingClientRect();
                const y = e.clientY - rect.top;
                const ratio = y / rect.height;
                node.classList.remove('coc-drop-target', 'coc-drop-target-above');
                if (ratio < 0.3) {
                    node.classList.add('coc-drop-target-above');
                } else {
                    node.classList.add('coc-drop-target');
                }
            });

            node.addEventListener('dragleave', () => {
                node.classList.remove('coc-drop-target', 'coc-drop-target-above', 'coc-drop-target-invalid');
            });

            node.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                node.classList.remove('coc-drop-target', 'coc-drop-target-above', 'coc-drop-target-invalid');
                if (!_cocDragUnitId || _cocDragUnitId === node.dataset.unitId) return;
                if (_cocDragSide !== node.dataset.side) return;

                const targetUnitId = node.dataset.unitId;
                const targetUnit = allUnitMap[targetUnitId];

                // Determine drop zone
                const rect = node.getBoundingClientRect();
                const y = e.clientY - rect.top;
                const ratio = y / rect.height;

                if (ratio < 0.3 && targetUnit) {
                    // Drop above = become sibling (share same parent as target)
                    _setCoCParent(_cocDragUnitId, targetUnit.parent_unit_id || null);
                } else {
                    // Drop inside = become child of target
                    _setCoCParent(_cocDragUnitId, targetUnitId);
                }
            });
        });

        // Allow dropping on the tree container itself (to make root-level)
        container.addEventListener('dragover', (e) => {
            if (e.target === container || e.target.classList.contains('coc-tree') || e.target.classList.contains('coc-side-header')) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
            }
        });
        container.addEventListener('drop', (e) => {
            if (e.target === container || e.target.classList.contains('coc-tree') || e.target.classList.contains('coc-side-header')) {
                e.preventDefault();
                if (_cocDragUnitId) {
                    _setCoCParent(_cocDragUnitId, null);
                }
            }
        });
    }

    /** Quick parent picker inline — shows a small dropdown for "move down" action. */
    function _showQuickParentPicker(unitId, candidates, allUnitMap) {
        if (candidates.length === 1) {
            // Only one candidate — just set it
            _setCoCParent(unitId, candidates[0].id);
            return;
        }
        // Use the existing parent picker modal
        _cocPickerPendingUnitId = unitId;
        const label = document.getElementById('admin-coc-picker-unit-label');
        const unit = allUnitMap[unitId];
        if (label) label.textContent = `Make "${unit ? unit.name : unitId}" subordinate to:`;
        const sel = document.getElementById('admin-coc-picker-select');
        if (sel) {
            sel.innerHTML = '';
            candidates.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                opt.textContent = `${c.name} (${c.unit_type})`;
                sel.appendChild(opt);
            });
            sel.value = candidates[0].id;
        }
        const modal = document.getElementById('admin-coc-picker-modal');
        if (modal) modal.style.display = 'flex';
    }

    function _renderCoCNode(unit, depth, allUnits, editable, allUnitMap, canModifyHierarchy) {
        const indent = depth * 20;
        const sideColor = unit.side === 'blue' ? '#4fc3f7' : '#ef5350';
        const strPct = unit.strength != null ? (unit.strength * 100).toFixed(0) + '%' : '?';
        const strClr = unit.strength > 0.6 ? '#4caf50' : unit.strength > 0.3 ? '#ff9800' : '#f44336';
        const hasChildren = unit.children && unit.children.length > 0;
        const expandIcon = hasChildren ? '▼' : '·';

        // Find assigned users for this unit
        let userBadge = '';
        if (unit.assigned_user_names && unit.assigned_user_names.length > 0) {
            const names = unit.assigned_user_names.join(', ');
            userBadge = `<span class="coc-user-badge" title="Assigned to: ${names}">⭐ ${names}</span>`;
        }

        // Commanding officer info (from hierarchy)
        let cmdInfo = '';
        if (unit.commanding_user_name) {
            const isSelfCO = unit.assigned_user_names
                && unit.assigned_user_names.includes(unit.commanding_user_name);
            if (!isSelfCO) {
                // CO is inherited from parent chain
                cmdInfo = `<span class="coc-cmd" title="CO: ${unit.commanding_user_name}">⬆ ${unit.commanding_user_name}</span>`;
            }
        }

        // Build tooltip with rich info
        let tooltipParts = [unit.unit_type, `Str: ${strPct}`];
        if (unit.commanding_user_name) tooltipParts.push(`CO: ${unit.commanding_user_name}`);
        if (unit.parent_unit_id && allUnitMap && allUnitMap[unit.parent_unit_id]) {
            tooltipParts.push(`Parent: ${allUnitMap[unit.parent_unit_id].name}`);
        }
        const tooltip = tooltipParts.join(' — ');

        // Admin bulk checkbox
        const bulkCb = _adminUnlocked
            ? `<input type="checkbox" class="coc-bulk-cb" data-unit-id="${unit.id}" data-side="${unit.side}" style="cursor:pointer;flex-shrink:0;margin-right:3px;" />`
            : '';

        // Can this user edit this unit's hierarchy?
        const canEdit = _adminUnlocked || (canModifyHierarchy && _userCanAssign(unit, allUnitMap));
        const isDraggable = canEdit ? 'true' : 'false';

        let html = `<div class="coc-node${canEdit ? ' coc-draggable' : ''}" style="margin-left:${indent}px;" title="${tooltip}" draggable="${isDraggable}" data-unit-id="${unit.id}" data-side="${unit.side}">
            ${bulkCb}
            <span class="coc-expand">${expandIcon}</span>
            <span class="coc-connector" style="background:${sideColor};"></span>
            <span class="coc-name" style="color:#e0e0e0;" data-unit-id="${unit.id}" data-lat="${unit.lat || ''}" data-lon="${unit.lon || ''}">${unit.name}</span>
            ${userBadge}
            ${cmdInfo}
            <span class="coc-type" title="${unit.unit_type}">${unit.unit_type}</span>
            <span class="coc-str" style="color:${strClr};" title="Strength">${strPct}</span>`;

        // User-assign button: shown if current user can manage this unit
        const canUserAssign = _adminUnlocked || _userCanAssign(unit, allUnitMap);
        if (canUserAssign) {
            html += `<button class="coc-user-assign-btn" data-unit-id="${unit.id}" title="Assign a commander to this unit"><svg viewBox="0 0 16 16" width="12" height="12" style="vertical-align:-1px;"><circle cx="8" cy="5" r="3" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M3 14c0-3 2.5-5 5-5s5 2 5 5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M11 3l2-1.5v3L11 3z" fill="currentColor" opacity="0.7"/></svg></button>`;
        }

        // Hierarchy adjustment buttons: shown for authorized users
        if (editable && canEdit) {
            // Move up (detach from parent → become sibling of parent = go to grandparent level)
            if (unit.parent_unit_id) {
                html += `<button class="coc-move-up-btn coc-hier-btn" data-unit-id="${unit.id}" title="Move up in hierarchy (detach from parent)">
                    <svg viewBox="0 0 12 12" width="10" height="10"><path d="M6 2L2 6h3v4h2V6h3z" fill="currentColor"/></svg>
                </button>`;
            }
            // Move down (become child of a sibling)
            html += `<button class="coc-move-down-btn coc-hier-btn" data-unit-id="${unit.id}" title="Move down (become subordinate of a sibling)">
                <svg viewBox="0 0 12 12" width="10" height="10"><path d="M6 10L2 6h3V2h2v4h3z" fill="currentColor"/></svg>
            </button>`;
            // Parent picker
            html += `<button class="coc-assign-btn coc-hier-btn" data-unit-id="${unit.id}" title="Assign to a specific parent unit">
                <svg viewBox="0 0 12 12" width="10" height="10"><path d="M6 1v4H2l4 4 4-4H7V1z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><line x1="2" y1="11" x2="10" y2="11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
            </button>`;
            // Detach from parent
            if (unit.parent_unit_id) {
                html += `<button class="coc-unassign-btn coc-hier-btn" data-unit-id="${unit.id}" title="Detach from parent (make independent)">✕</button>`;
            }
        }

        html += `</div>`;

        if (hasChildren) {
            unit.children.forEach(child => {
                html += _renderCoCNode(child, depth + 1, allUnits, editable, allUnitMap, canModifyHierarchy);
            });
        }
        return html;
    }

    // ── Bulk Assign / Unassign ─────────────────────────

    async function _doBulkAssign() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;

        // Scope to admin CoC container to avoid picking up public CoC elements
        const adminCoCContainer = document.getElementById('admin-coc-tree');
        const bulkUserSel = adminCoCContainer
            ? adminCoCContainer.querySelector('#coc-bulk-user-select')
            : document.getElementById('coc-bulk-user-select');
        const userId = bulkUserSel ? bulkUserSel.value : '';
        if (!userId) { await KDialogs.alert('Select a user to assign'); return; }

        const checkedBoxes = adminCoCContainer
            ? adminCoCContainer.querySelectorAll('.coc-bulk-cb:checked')
            : document.querySelectorAll('.coc-bulk-cb:checked');
        if (checkedBoxes.length === 0) { await KDialogs.alert('No units selected'); return; }

        const unitIds = Array.from(checkedBoxes).map(cb => cb.dataset.unitId);
        const userName = bulkUserSel.options[bulkUserSel.selectedIndex]?.textContent || '';

        // ── Side-matching validation ──
        // Find the selected user's side from cached participants
        const selectedParticipant = _cachedParticipants.find(p => p.user_id === userId);
        const userSide = selectedParticipant ? selectedParticipant.side : null;
        if (userSide && userSide !== 'admin') {
            // Check that all selected units match the user's side
            const mismatchedUnits = Array.from(checkedBoxes).filter(cb => {
                const unitSide = cb.dataset.side;
                return unitSide && unitSide !== userSide;
            });
            if (mismatchedUnits.length > 0) {
                const sideLabel = userSide === 'blue' ? 'Blue' : 'Red';
                await KDialogs.alert(`Cannot assign ${sideLabel} commander to ${mismatchedUnits.length} unit(s) from the other side.\n\nCommanders can only be assigned to units on their own side.`);
                return;
            }
        }

        if (!await KDialogs.confirm(`Assign ${userName} to ${unitIds.length} unit(s)?`)) return;

        let success = 0;
        for (const unitId of unitIds) {
            try {
                const resp = await fetch(`/api/admin/sessions/${sid}/units/${unitId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ assigned_user_ids: [userId] }),
                });
                if (resp.ok) success++;
            } catch {}
        }

        KGameLog.addEntry(`Bulk assigned ${userName} to ${success}/${unitIds.length} units`, 'info');
        _loadChainOfCommand();
        loadPublicCoC();
        // Reload units on map to reflect assignment changes
        const token2 = _getToken();
        const userSid2 = _getUserSessionId();
        if (userSid2 && token2) {
            try {
                if (_godViewEnabled) await _refreshGodView();
                else await KUnits.load(userSid2, token2);
            } catch(e) {}
        }
    }

    async function _doBulkUnassign() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;

        // Scope to admin CoC container to avoid picking up public CoC elements
        const adminCoCContainer = document.getElementById('admin-coc-tree');
        const checkedBoxes = adminCoCContainer
            ? adminCoCContainer.querySelectorAll('.coc-bulk-cb:checked')
            : document.querySelectorAll('.coc-bulk-cb:checked');
        if (checkedBoxes.length === 0) { await KDialogs.alert('No units selected'); return; }

        const unitIds = Array.from(checkedBoxes).map(cb => cb.dataset.unitId);

        if (!await KDialogs.confirm(`Unassign commanders from ${unitIds.length} unit(s)?`)) return;

        let success = 0;
        for (const unitId of unitIds) {
            try {
                const resp = await fetch(`/api/admin/sessions/${sid}/units/${unitId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ assigned_user_ids: [] }),
                });
                if (resp.ok) success++;
            } catch {}
        }

        KGameLog.addEntry(`Bulk unassigned ${success}/${unitIds.length} units`, 'info');
        _loadChainOfCommand();
        loadPublicCoC();
        // Reload units on map to reflect unassignment changes
        const token3 = _getToken();
        const userSid3 = _getUserSessionId();
        if (userSid3 && token3) {
            try {
                if (_godViewEnabled) await _refreshGodView();
                else await KUnits.load(userSid3, token3);
            } catch(e) {}
        }
    }

    let _cocPickerPendingUnitId = null;

    async function _showParentPicker(unitId, allUnits) {
        const unit = allUnits.find(u => u.id === unitId);
        if (!unit) return;

        const candidates = allUnits.filter(u =>
            u.id !== unitId && u.side === unit.side
        );

        if (candidates.length === 0) {
            await KDialogs.alert('No available parent units');
            return;
        }

        _cocPickerPendingUnitId = unitId;

        // Populate modal
        const label = document.getElementById('admin-coc-picker-unit-label');
        if (label) label.textContent = `Assign "${unit.name}" to which commander?`;

        const sel = document.getElementById('admin-coc-picker-select');
        if (sel) {
            sel.innerHTML = '';
            candidates.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.id;
                const sideIcon = c.side === 'blue' ? '🔵' : c.side === 'red' ? '🔴' : '⚪';
                opt.textContent = `${sideIcon} ${c.name} (${c.unit_type})`;
                sel.appendChild(opt);
            });
            if (candidates.length > 0) sel.value = candidates[0].id;
        }

        // Show modal
        const modal = document.getElementById('admin-coc-picker-modal');
        if (modal) modal.style.display = 'flex';
    }

    function _initCocPickerModal() {
        _bind('admin-coc-picker-confirm', 'click', () => {
            const sel = document.getElementById('admin-coc-picker-select');
            if (!sel || !sel.value || !_cocPickerPendingUnitId) return;
            _setUnitParent(_cocPickerPendingUnitId, sel.value);
            _closeCocPickerModal();
        });
        _bind('admin-coc-picker-cancel', 'click', _closeCocPickerModal);
        _bind('admin-coc-picker-close', 'click', _closeCocPickerModal);

        // Close on overlay click
        const overlay = document.getElementById('admin-coc-picker-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) _closeCocPickerModal();
            });
        }

        // Double-click to confirm
        const sel = document.getElementById('admin-coc-picker-select');
        if (sel) {
            sel.addEventListener('dblclick', () => {
                if (sel.value && _cocPickerPendingUnitId) {
                    _setUnitParent(_cocPickerPendingUnitId, sel.value);
                    _closeCocPickerModal();
                }
            });
        }
    }

    function _closeCocPickerModal() {
        const modal = document.getElementById('admin-coc-picker-modal');
        if (modal) modal.style.display = 'none';
        _cocPickerPendingUnitId = null;
    }

    // ══════════════════════════════════════════════════
    // ── CoC User Assignment Modal (for commanders) ───
    // ══════════════════════════════════════════════════

    let _userAssignPendingUnit = null;

    function _showUserAssignModal(unit) {
        _userAssignPendingUnit = unit;

        const label = document.getElementById('coc-user-assign-unit-label');
        if (label) label.textContent = `Assign commander for "${unit.name}" (${unit.unit_type})`;

        const statusEl = document.getElementById('coc-user-assign-status');
        if (statusEl) statusEl.textContent = '';

        // Populate participant select — filter to same side, exclude observers (they don't control units)
        const sel = document.getElementById('coc-user-assign-select');
        if (sel) {
            sel.innerHTML = '';
            const sameSideParticipants = _cachedParticipants.filter(
                p => (p.side === unit.side || p.side === 'admin') && p.side !== 'observer' && p.role !== 'observer'
            );
            if (sameSideParticipants.length === 0) {
                const opt = document.createElement('option');
                opt.textContent = '(no participants available)';
                opt.disabled = true;
                sel.appendChild(opt);
            } else {
                sameSideParticipants.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.user_id;
                    const sideIcon = p.side === 'blue' ? '🔵' : p.side === 'red' ? '🔴' : '👁';
                    const currentMark = (unit.assigned_user_ids && unit.assigned_user_ids.includes(p.user_id))
                        ? ' ⭐' : '';
                    opt.textContent = `${sideIcon} ${p.display_name} (${p.role})${currentMark}`;
                    sel.appendChild(opt);
                });
                // Pre-select the currently assigned user if any
                if (unit.assigned_user_ids && unit.assigned_user_ids.length > 0) {
                    sel.value = unit.assigned_user_ids[0];
                }
            }
        }

        // Show modal
        const modal = document.getElementById('coc-user-assign-modal');
        if (modal) modal.style.display = 'flex';
    }

    function _closeCocUserAssignModal() {
        const modal = document.getElementById('coc-user-assign-modal');
        if (modal) modal.style.display = 'none';
        _userAssignPendingUnit = null;
    }

    function _initCocUserAssignModal() {
        _bind('coc-user-assign-confirm', 'click', _doCocUserAssign);
        _bind('coc-user-assign-unassign', 'click', _doCocUserUnassign);
        _bind('coc-user-assign-cancel', 'click', _closeCocUserAssignModal);
        _bind('coc-user-assign-close', 'click', _closeCocUserAssignModal);

        // Close on overlay click
        const overlay = document.getElementById('coc-user-assign-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) _closeCocUserAssignModal();
            });
        }

        // Double-click to confirm
        const sel = document.getElementById('coc-user-assign-select');
        if (sel) {
            sel.addEventListener('dblclick', () => {
                if (sel.value && _userAssignPendingUnit) {
                    _doCocUserAssign();
                }
            });
        }
    }

    async function _doCocUserAssign() {
        if (!_userAssignPendingUnit) return;
        const sel = document.getElementById('coc-user-assign-select');
        const statusEl = document.getElementById('coc-user-assign-status');
        if (!sel || !sel.value) {
            if (statusEl) { statusEl.textContent = 'Select a participant'; statusEl.className = 'admin-info admin-error'; }
            return;
        }

        const token = _getToken();
        // Admin should use admin session; user uses their own session
        const sid = _adminUnlocked ? (_getAdminSessionId() || _getUserSessionId()) : (_getUserSessionId() || _getAdminSessionId());
        if (!token || !sid) return;

        const unitId = _userAssignPendingUnit.id;
        const userId = sel.value;

        try {
            // Use admin endpoint for assignment (bypasses permission checks)
            const endpoint = _adminUnlocked
                ? `/api/admin/sessions/${sid}/units/${unitId}`
                : `/api/sessions/${sid}/units/${unitId}/assign`;
            const method = _adminUnlocked ? 'PUT' : 'PUT';
            const body = _adminUnlocked
                ? { assigned_user_ids: [userId] }
                : { assigned_user_ids: [userId] };

            const resp = await fetch(endpoint, {
                method: method,
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify(body),
            });
            if (resp.ok) {
                const participant = _cachedParticipants.find(p => p.user_id === userId);
                const name = participant ? participant.display_name : userId.substring(0, 8);
                if (statusEl) { statusEl.textContent = `✓ ${name} assigned as commander`; statusEl.className = 'admin-info admin-success'; }
                // Refresh CoC trees and reload map units after a brief delay
                setTimeout(async () => {
                    _closeCocUserAssignModal();
                    loadPublicCoC();
                    if (_adminUnlocked) _loadChainOfCommand();
                    // Reload units on map to show updated assignment names
                    const reloadToken = _getToken();
                    const userSid = _getUserSessionId();
                    if (userSid && reloadToken) {
                        try {
                            if (_godViewEnabled) {
                                await _refreshGodView();
                            } else {
                                await KUnits.load(userSid, reloadToken);
                            }
                        } catch(e) {}
                    }
                }, 600);
            } else {
                const d = await resp.json().catch(() => ({}));
                if (statusEl) { statusEl.textContent = `✗ ${d.detail || 'Assignment failed'}`; statusEl.className = 'admin-info admin-error'; }
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'admin-info admin-error'; }
        }
    }

    async function _doCocUserUnassign() {
        if (!_userAssignPendingUnit) return;
        const statusEl = document.getElementById('coc-user-assign-status');

        const token = _getToken();
        const sid = _adminUnlocked ? (_getAdminSessionId() || _getUserSessionId()) : (_getUserSessionId() || _getAdminSessionId());
        if (!token || !sid) return;

        const unitId = _userAssignPendingUnit.id;

        try {
            const endpoint = _adminUnlocked
                ? `/api/admin/sessions/${sid}/units/${unitId}`
                : `/api/sessions/${sid}/units/${unitId}/assign`;
            const body = _adminUnlocked
                ? { assigned_user_ids: [] }
                : { assigned_user_ids: [] };

            const resp = await fetch(endpoint, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify(body),
            });
            if (resp.ok) {
                if (statusEl) { statusEl.textContent = '✓ Unit unassigned'; statusEl.className = 'admin-info admin-success'; }
                setTimeout(async () => {
                    _closeCocUserAssignModal();
                    loadPublicCoC();
                    if (_adminUnlocked) _loadChainOfCommand();
                    // Reload units on map to show updated assignment
                    const reloadToken = _getToken();
                    const sid = _getUserSessionId();
                    if (sid && reloadToken) {
                        try {
                            if (_godViewEnabled) {
                                await _refreshGodView();
                            } else {
                                await KUnits.load(sid, reloadToken);
                            }
                        } catch(e) {}
                    }
                }, 600);
            } else {
                const d = await resp.json().catch(() => ({}));
                if (statusEl) { statusEl.textContent = `✗ ${d.detail || 'Failed'}`; statusEl.className = 'admin-info admin-error'; }
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'admin-info admin-error'; }
        }
    }

    async function _setCoCParent(unitId, parentId) {
        const token = _getToken();
        if (!token) return;

        // Use admin endpoint when admin panel is open, otherwise use public endpoint
        const sid = _adminUnlocked
            ? (_getAdminSessionId() || _getUserSessionId())
            : (_getUserSessionId() || _getAdminSessionId());
        if (!sid) return;

        try {
            const endpoint = _adminUnlocked
                ? `/api/admin/sessions/${sid}/units/${unitId}/parent`
                : `/api/sessions/${sid}/units/${unitId}/parent`;
            const resp = await fetch(endpoint, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ parent_unit_id: parentId }),
            });
            if (resp.ok) {
                // Refresh both CoC trees
                _loadChainOfCommand();
                loadPublicCoC();
            } else {
                const d = await resp.json().catch(() => ({}));
                KGameLog.addEntry(`Hierarchy change failed: ${d.detail || 'unknown error'}`, 'error');
            }
        } catch (err) {
            KGameLog.addEntry(`Hierarchy change failed: ${err.message}`, 'error');
        }
    }

    // Keep backward-compatible alias for admin-specific calls
    async function _setUnitParent(unitId, parentId) {
        return _setCoCParent(unitId, parentId);
    }

    // ══════════════════════════════════════════════════
    // ── Admin Split / Merge ──────────────────────────
    // ══════════════════════════════════════════════════

    async function adminSplitUnit(unitId) {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { await KDialogs.alert('Select a session first'); return; }
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units/${unitId}/split`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ ratio: 0.5 }),
            });
            if (resp.ok) {
                const data = await resp.json();
                KGameLog.addEntry(`Admin split: ${data.original.name} + ${data.new_unit.name}`, 'info');
                await _loadUnitDashboard();
                KUnits.invalidateAllViewsheds();
                const userSid = _getUserSessionId();
                if (userSid || _godViewEnabled) {
                    try {
                        if (_godViewEnabled) await _refreshGodView();
                        else await KUnits.load(userSid, token);
                    } catch(e) { console.warn('Unit refresh after split:', e); }
                }
                try { _loadChainOfCommand(); } catch(e) {}
                try { loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Split failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    async function adminMergeUnit(unitId) {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { await KDialogs.alert('Select a session first'); return; }

        const unit = _dashboardUnits.find(u => u.id === unitId);
        if (!unit) { await KDialogs.alert('Unit not found in dashboard'); return; }

        const principalType = _getPrincipalType(unit.unit_type);
        const nearby = _dashboardUnits.filter(ou => {
            if (ou.id === unit.id || ou.side !== unit.side || ou.is_destroyed) return false;
            if (_getPrincipalType(ou.unit_type) !== principalType) return false;
            return true;
        });

        if (nearby.length === 0) {
            await KDialogs.alert(`No compatible units for "${unit.name}" (type: ${principalType})`);
            return;
        }

        const choices = nearby.map((ou, i) => {
            const strPct = ou.strength != null ? Math.round(ou.strength * 100) + '%' : '?';
            let distInfo = '';
            if (unit.lat != null && ou.lat != null) {
                const dist = Math.round(_haversineDist(unit.lat, unit.lon, ou.lat, ou.lon));
                distInfo = `, ${dist}m`;
            }
            return { value: String(i), label: `${ou.name} (${strPct}${distInfo})` };
        });
        const choice = await KDialogs.select(`Select unit to absorb into "${unit.name}":`, choices, {title: 'Merge Units'});
        if (choice == null) return;
        const idx = parseInt(choice);
        if (isNaN(idx) || idx < 0 || idx >= nearby.length) return;

        const mergeTarget = nearby[idx];
        if (!await KDialogs.confirm(`Merge "${mergeTarget.name}" into "${unit.name}"?\nThe merged unit will be removed.`, {dangerous: true})) return;

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units/${unitId}/merge`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ merge_with_unit_id: mergeTarget.id }),
            });
            if (resp.ok) {
                const data = await resp.json();
                KGameLog.addEntry(`Admin merge: ${mergeTarget.name} → ${unit.name}`, 'info');
                await _loadUnitDashboard();
                KUnits.invalidateAllViewsheds();
                const userSid = _getUserSessionId();
                if (userSid || _godViewEnabled) {
                    try {
                        if (_godViewEnabled) await _refreshGodView();
                        else await KUnits.load(userSid, token);
                    } catch(e) { console.warn('Unit refresh after merge:', e); }
                }
                try { _loadChainOfCommand(); } catch(e) {}
                try { loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Merge failed');
            }
        } catch (err) { await KDialogs.alert(err.message); }
    }

    function _getPrincipalType(unitType) {
        if (!unitType) return '';
        const suffixes = ['_battalion', '_company', '_battery', '_platoon', '_section', '_squad', '_team', '_post', '_unit'];
        for (const s of suffixes) {
            if (unitType.endsWith(s)) return unitType.slice(0, -s.length);
        }
        return unitType;
    }

    function _haversineDist(lat1, lon1, lat2, lon2) {
        const R = 6371000;
        const toRad = (d) => d * Math.PI / 180;
        const dLat = toRad(lat2 - lat1);
        const dLon = toRad(lon2 - lon1);
        const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
        return 2 * R * Math.asin(Math.sqrt(a));
    }

    // ══════════════════════════════════════════════════
    // ── Unit Edit Modal ──────────────────────────────
    // ══════════════════════════════════════════════════

    function _initUnitEditModal() {
        _bind('admin-ue-save', 'click', _saveUnitEdit);
        _bind('admin-ue-cancel', 'click', _closeUnitEdit);
        _bind('admin-ue-close', 'click', _closeUnitEdit);
        _bind('admin-ue-pick-map', 'click', _pickUnitPositionOnMap);

        const sliders = [
            { id: 'admin-ue-strength',    valId: 'admin-ue-strength-val',    color: '#4caf50', lowColor: '#f44336' },
            { id: 'admin-ue-morale',      valId: 'admin-ue-morale-val',      color: '#2196f3', lowColor: '#ff9800' },
            { id: 'admin-ue-ammo',        valId: 'admin-ue-ammo-val',        color: '#ff9800', lowColor: '#f44336' },
            { id: 'admin-ue-suppression', valId: 'admin-ue-suppression-val', color: '#f44336', lowColor: '#f44336' },
        ];
        sliders.forEach(s => {
            const range = document.getElementById(s.id);
            const valEl = document.getElementById(s.valId);
            if (range && valEl) {
                const updateSlider = () => {
                    const v = parseInt(range.value);
                    valEl.textContent = v + '%';
                    if (s.id === 'admin-ue-suppression') {
                        valEl.style.color = v > 50 ? '#f44336' : v > 20 ? '#ff9800' : '#4caf50';
                    } else {
                        valEl.style.color = v > 60 ? '#4caf50' : v > 30 ? '#ff9800' : '#f44336';
                    }
                    const pct = v;
                    const fillColor = s.id === 'admin-ue-suppression'
                        ? (v > 50 ? '#f44336' : v > 20 ? '#ff9800' : '#4caf50')
                        : (v > 60 ? s.color : v > 30 ? '#ff9800' : s.lowColor);
                    range.style.background = `linear-gradient(to right, ${fillColor} 0%, ${fillColor} ${pct}%, #1a1a2e ${pct}%, #1a1a2e 100%)`;
                };
                range.addEventListener('input', updateSlider);
                range.addEventListener('change', updateSlider);
            }
        });

        const sideEl = document.getElementById('admin-ue-side');
        if (sideEl) sideEl.addEventListener('change', _updateUnitEditPreview);

        _initUnitEditDrag();
    }

    function _closeUnitEdit() {
        const modal = document.getElementById('admin-unit-edit-modal');
        if (modal) {
            modal.style.display = 'none';
            modal.classList.remove('ue-dragged');
            modal.style.left = '50%';
            modal.style.top = '50%';
            modal.style.right = '';
            modal.style.transform = 'translate(-50%, -50%)';
        }
    }

    function _initUnitEditDrag() {
        const win = document.getElementById('admin-unit-edit-modal');
        const header = document.getElementById('admin-ue-header');
        if (!win || !header) return;

        let isDragging = false;
        let startX, startY, startLeft, startTop;

        header.addEventListener('pointerdown', (e) => {
            if (e.target.classList.contains('ue-float-close')) return;
            isDragging = true;
            const rect = win.getBoundingClientRect();
            startX = e.clientX;
            startY = e.clientY;
            startLeft = rect.left;
            startTop = rect.top;
            header.setPointerCapture(e.pointerId);
            e.preventDefault();
        });

        header.addEventListener('pointermove', (e) => {
            if (!isDragging) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            win.classList.add('ue-dragged');
            win.style.transform = 'none';
            win.style.left = (startLeft + dx) + 'px';
            win.style.top = (startTop + dy) + 'px';
            win.style.right = 'auto';
        });

        header.addEventListener('pointerup', () => { isDragging = false; });
    }

    function _updateUnitEditPreview() {
        const previewEl = document.getElementById('admin-ue-symbol-preview');
        if (!previewEl) return;
        const sideVal = document.getElementById('admin-ue-side')?.value || 'blue';
        const typeVal = document.getElementById('admin-ue-unit-type')?.value || '';
        const types = typeof KScenarioBuilder !== 'undefined' ? KScenarioBuilder.getUnitTypes() : {};
        const info = types[typeVal];
        let sidc = '';
        if (info) {
            sidc = sideVal === 'red' ? (info.sidc_red || '') : (info.sidc_blue || '');
        }
        if (sidc && window.ms) {
            try {
                const sym = new ms.Symbol(sidc, { size: 40 });
                previewEl.innerHTML = sym.asSVG();
                return;
            } catch(e) {}
        }
        const emoji = sideVal === 'red' ? '🔴' : '🔵';
        previewEl.innerHTML = `<span style="font-size:28px;">${emoji}</span>`;
    }

    function _pickUnitPositionOnMap() {
        const map = KMap.getMap();
        if (!map) return;
        const modal = document.getElementById('admin-unit-edit-modal');
        if (!modal) return;

        modal.style.opacity = '0.3';
        modal.style.pointerEvents = 'none';
        map.getContainer().classList.add('pick-mode-active');

        const banner = document.createElement('div');
        banner.className = 'pick-mode-banner';
        banner.id = 'ue-pick-banner';
        banner.textContent = '🖱 Click map to set unit position — ESC to cancel';
        document.body.appendChild(banner);

        const _cancel = () => {
            modal.style.opacity = '';
            modal.style.pointerEvents = '';
            map.getContainer().classList.remove('pick-mode-active');
            const b = document.getElementById('ue-pick-banner');
            if (b) b.remove();
            map.off('click', _onClick);
            document.removeEventListener('keydown', _onKey);
        };

        const _onClick = (e) => {
            _setVal('admin-ue-lat', e.latlng.lat.toFixed(6));
            _setVal('admin-ue-lon', e.latlng.lng.toFixed(6));
            _cancel();
        };

        const _onKey = (e) => {
            if (e.key === 'Escape') _cancel();
        };

        map.once('click', _onClick);
        document.addEventListener('keydown', _onKey, { once: true });
    }

    function _fireRangeUpdate(id) {
        const el = document.getElementById(id);
        if (el) el.dispatchEvent(new Event('input'));
    }

    // ── Session Context Update ───────────────────────

    function updateSessionContext() {
        const sid = _getUserSessionId();
        const statusEl = document.getElementById('admin-session-status');
        if (statusEl) {
            statusEl.textContent = sid ? `Active: ${sid.substring(0, 8)}...` : 'No active session';
        }
        if (sid && !_adminSelectedSessionId) {
            _adminSelectedSessionId = sid;
            const sel = document.getElementById('admin-session-selector');
            if (sel) sel.value = sid;
            // Pre-fill game-time input for the newly selected session
            _populateSessionTimeInput(sid);
        }
        if (_adminUnlocked) {
            _loadAdminSessions();
            _tryAutoEnableGodView();
        }
    }

    // ── Helpers ──────────────────────────────────────

    async function _tryAutoEnableGodView() {
        if (!_pendingGodViewEnable || _godViewEnabled) return;
        const sid = _getAdminSessionId();
        const token = _getToken();
        if (sid && token) {
            _pendingGodViewEnable = false;
            try {
                await _toggleGodView();
            } catch (e) {
                console.warn('Auto-enable god view failed:', e);
            }
        }
    }

    async function _tryLoadAdminSessionGrid() {
        const sid = _getAdminSessionId();
        if (!sid) return;
        const map = KMap.getMap();
        if (!map) return;
        try {
            await KGrid.load(map, sid);
        } catch (e) {
            console.warn('Admin grid load for selected session:', e);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Unit Types Management ────────────────────────
    // ══════════════════════════════════════════════════

    function _initUnitTypes() {
        _bind('admin-add-unit-type', 'click', _addUnitType);
        _bind('admin-reset-unit-types', 'click', _resetUnitTypes);
        _bind('admin-utype-save', 'click', _saveUnitTypeEdit);
        _bind('admin-utype-cancel', 'click', _closeUnitTypeEdit);
        _bind('admin-utype-close', 'click', _closeUnitTypeEdit);
        _initUnitTypeDrag();

        // Live preview: update on any SIDC or stats input change
        ['utype-sidc-blue', 'utype-sidc-red', 'utype-label', 'utype-det', 'utype-fire', 'utype-speed', 'utype-personnel'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', _updateUnitTypePreview);
        });
    }

    function _renderUnitTypes() {
        const el = document.getElementById('admin-types-list');
        if (!el) return;
        const types = typeof KScenarioBuilder !== 'undefined' ? KScenarioBuilder.getUnitTypes() : {};
        const entries = Object.entries(types);

        if (entries.length === 0) {
            el.innerHTML = '<div class="admin-info">No unit types defined</div>';
            return;
        }

        let html = '';
        entries.forEach(([key, info]) => {
            const hqClass = info.isHQ ? ' is-hq' : '';
            const hqBadge = info.isHQ ? '<span style="color:#ff9800;font-size:9px;font-weight:700;"> HQ</span>' : '';
            const previewSidc = info.sidc_blue || '';

            // Generate a tiny preview icon if milsymbol is available
            let previewHtml = '<span style="font-size:16px;">🔲</span>';
            if (previewSidc && window.ms) {
                try {
                    const sym = new ms.Symbol(previewSidc, { size: 22 });
                    previewHtml = sym.asSVG();
                } catch(e) {}
            }

            html += `<div class="unit-type-item${hqClass}" data-key="${key}">
                <div class="unit-type-preview">${previewHtml}</div>
                <div class="unit-type-info">
                    <div class="unit-type-label">${info.label || key}${hqBadge}</div>
                    <div class="unit-type-stats">
                        👁${info.det || 0}m 🎯${info.fire || 0}m ⚡${info.speed || 0}m/s 👥${info.personnel || 0}
                    </div>
                </div>
                <div class="unit-type-actions">
                    <button class="admin-btn" onclick="KAdmin.editUnitType('${key}')" style="padding:1px 5px;font-size:9px;" title="Edit type">✏</button>
                    <button class="admin-btn admin-btn-danger" onclick="KAdmin.removeUnitType('${key}')" style="padding:1px 5px;font-size:9px;" title="Remove type">✕</button>
                </div>
            </div>`;
        });
        el.innerHTML = html;
    }

    let _utypeEditingKey = null;  // null = adding new, string = editing existing

    function _addUnitType() {
        _utypeEditingKey = null;
        _openUnitTypeEditor({
            key: '',
            label: '',
            sidc_blue: '10031000141211000000',
            sidc_red: '10061000141211000000',
            speed: 4.0,
            det: 1500,
            fire: 600,
            personnel: 20,
            isHQ: false,
        }, 'New Unit Type');
    }

    async function editUnitType(key) {
        const types = KScenarioBuilder.getUnitTypes();
        const info = types[key];
        if (!info) { await KDialogs.alert('Type not found'); return; }
        _utypeEditingKey = key;
        _openUnitTypeEditor({ key, ...info }, `Edit: ${info.label || key}`);
    }

    function _openUnitTypeEditor(data, title) {
        const modal = document.getElementById('admin-utype-modal');
        if (!modal) return;

        // Set title
        const titleEl = document.getElementById('admin-utype-title');
        if (titleEl) titleEl.textContent = `✏ ${title}`;

        // Populate fields
        _setVal('utype-key', data.key || '');
        _setVal('utype-label', data.label || '');
        _setVal('utype-det', data.det || 1500);
        _setVal('utype-fire', data.fire || 600);
        _setVal('utype-speed', data.speed || 4.0);
        _setVal('utype-personnel', data.personnel || 30);
        _setVal('utype-sidc-blue', data.sidc_blue || '');
        _setVal('utype-sidc-red', data.sidc_red || '');
        const hqEl = document.getElementById('utype-is-hq');
        if (hqEl) hqEl.checked = !!data.isHQ;

        // Key field: editable only when adding new
        const keyEl = document.getElementById('utype-key');
        if (keyEl) {
            keyEl.readOnly = _utypeEditingKey !== null;
            keyEl.style.opacity = _utypeEditingKey !== null ? '0.6' : '1';
        }

        // Clear status
        const statusEl = document.getElementById('utype-status');
        if (statusEl) { statusEl.textContent = ''; statusEl.className = 'ue-status'; }

        // Show modal
        modal.style.display = 'flex';

        // Update preview
        _updateUnitTypePreview();
    }

    function _closeUnitTypeEdit() {
        const modal = document.getElementById('admin-utype-modal');
        if (modal) {
            modal.style.display = 'none';
            modal.classList.remove('ue-dragged');
            modal.style.left = '50%';
            modal.style.top = '50%';
            modal.style.right = '';
            modal.style.transform = 'translate(-50%, -50%)';
        }
        _utypeEditingKey = null;
    }

    function _saveUnitTypeEdit() {
        const key = (document.getElementById('utype-key')?.value || '').trim();
        const label = (document.getElementById('utype-label')?.value || '').trim();
        const det = parseInt(document.getElementById('utype-det')?.value) || 1500;
        const fire = parseInt(document.getElementById('utype-fire')?.value) || 600;
        const speed = parseFloat(document.getElementById('utype-speed')?.value) || 4.0;
        const personnel = parseInt(document.getElementById('utype-personnel')?.value) || 30;
        const sidcBlue = (document.getElementById('utype-sidc-blue')?.value || '').trim();
        const sidcRed = (document.getElementById('utype-sidc-red')?.value || '').trim();
        const isHQ = document.getElementById('utype-is-hq')?.checked || false;
        const statusEl = document.getElementById('utype-status');

        // Validate
        if (!key) {
            if (statusEl) { statusEl.textContent = '❌ Key is required'; statusEl.className = 'ue-status admin-error'; }
            return;
        }
        if (!label) {
            if (statusEl) { statusEl.textContent = '❌ Display name is required'; statusEl.className = 'ue-status admin-error'; }
            return;
        }
        if (sidcBlue && sidcBlue.length !== 20) {
            if (statusEl) { statusEl.textContent = '❌ Blue SIDC must be exactly 20 characters'; statusEl.className = 'ue-status admin-error'; }
            return;
        }
        if (sidcRed && sidcRed.length !== 20) {
            if (statusEl) { statusEl.textContent = '❌ Red SIDC must be exactly 20 characters'; statusEl.className = 'ue-status admin-error'; }
            return;
        }

        const types = KScenarioBuilder.getUnitTypes();

        // Check for duplicate key when adding new
        if (_utypeEditingKey === null && types[key]) {
            if (statusEl) { statusEl.textContent = '❌ Type key already exists'; statusEl.className = 'ue-status admin-error'; }
            return;
        }

        // Save
        types[key] = {
            label,
            sidc_blue: sidcBlue,
            sidc_red: sidcRed,
            speed,
            det,
            fire,
            personnel,
            isHQ,
        };

        _renderUnitTypes();
        try { _populateUnitTypeDropdown(); } catch(e) {}
        _closeUnitTypeEdit();
    }

    function _updateUnitTypePreview() {
        const sidcBlue = (document.getElementById('utype-sidc-blue')?.value || '').trim();
        const sidcRed = (document.getElementById('utype-sidc-red')?.value || '').trim();
        const det = document.getElementById('utype-det')?.value || '0';
        const fire = document.getElementById('utype-fire')?.value || '0';
        const speed = document.getElementById('utype-speed')?.value || '0';
        const personnel = document.getElementById('utype-personnel')?.value || '0';

        // Blue preview
        const blueBox = document.getElementById('utype-preview-blue');
        const blueSidcText = document.getElementById('utype-preview-blue-sidc');
        if (blueBox) {
            if (sidcBlue.length === 20 && window.ms) {
                try {
                    blueBox.innerHTML = new ms.Symbol(sidcBlue, { size: 48 }).asSVG();
                } catch(e) { blueBox.innerHTML = '<span style="font-size:32px;">🔵</span>'; }
            } else {
                blueBox.innerHTML = '<span style="font-size:32px;">🔵</span>';
            }
        }
        if (blueSidcText) blueSidcText.textContent = sidcBlue || '—';

        // Red preview
        const redBox = document.getElementById('utype-preview-red');
        const redSidcText = document.getElementById('utype-preview-red-sidc');
        if (redBox) {
            if (sidcRed.length === 20 && window.ms) {
                try {
                    redBox.innerHTML = new ms.Symbol(sidcRed, { size: 48 }).asSVG();
                } catch(e) { redBox.innerHTML = '<span style="font-size:32px;">🔴</span>'; }
            } else {
                redBox.innerHTML = '<span style="font-size:32px;">🔴</span>';
            }
        }
        if (redSidcText) redSidcText.textContent = sidcRed || '—';

        // Stats preview
        const statsEl = document.getElementById('utype-preview-stats');
        if (statsEl) {
            statsEl.innerHTML = `
                <div class="utype-stat-line"><span>👁 Detection</span><span class="utype-stat-val">${det}m</span></div>
                <div class="utype-stat-line"><span>🎯 Fire Range</span><span class="utype-stat-val">${fire}m</span></div>
                <div class="utype-stat-line"><span>⚡ Speed</span><span class="utype-stat-val">${speed} m/s</span></div>
                <div class="utype-stat-line"><span>👥 Personnel</span><span class="utype-stat-val">${personnel}</span></div>`;
        }
    }

    function _initUnitTypeDrag() {
        const win = document.getElementById('admin-utype-modal');
        const header = document.getElementById('admin-utype-header');
        if (!win || !header) return;

        let isDragging = false;
        let startX, startY, startLeft, startTop;

        header.addEventListener('pointerdown', (e) => {
            if (e.target.classList.contains('ue-float-close')) return;
            isDragging = true;
            const rect = win.getBoundingClientRect();
            startX = e.clientX;
            startY = e.clientY;
            startLeft = rect.left;
            startTop = rect.top;
            header.setPointerCapture(e.pointerId);
            e.preventDefault();
        });

        header.addEventListener('pointermove', (e) => {
            if (!isDragging) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            win.classList.add('ue-dragged');
            win.style.transform = 'none';
            win.style.left = (startLeft + dx) + 'px';
            win.style.top = (startTop + dy) + 'px';
            win.style.right = 'auto';
        });

        header.addEventListener('pointerup', () => { isDragging = false; });
    }

    async function removeUnitType(key) {
        if (!await KDialogs.confirm(`Remove unit type "${key}"?`, {dangerous: true})) return;
        const types = KScenarioBuilder.getUnitTypes();
        delete types[key];
        _renderUnitTypes();
        try { _populateUnitTypeDropdown(); } catch(e) {}
    }

    async function _resetUnitTypes() {
        if (!await KDialogs.confirm('Reset all unit types to defaults from config file? Custom types will be lost.', {dangerous: true})) return;
        try {
            KScenarioBuilder.resetUnitTypes();
            _renderUnitTypes();
            try { _populateUnitTypeDropdown(); } catch(e) {}
        } catch(e) {
            await KDialogs.alert('Reset failed. Please reload the page.');
        }
    }

    /** Refresh the unit type dropdown in the admin unit edit modal. */
    function _populateUnitTypeDropdown() {
        const typeEl = document.getElementById('admin-ue-unit-type');
        if (!typeEl) return;
        const currentVal = typeEl.value;
        typeEl.innerHTML = '';
        const types = typeof KScenarioBuilder !== 'undefined' ? KScenarioBuilder.getUnitTypes() : {};
        for (const [key, info] of Object.entries(types)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = info.label || key;
            typeEl.appendChild(opt);
        }
        if (currentVal) typeEl.value = currentVal;
    }

    function _showInfo(elementId, message, type = '') {
        const el = document.getElementById(elementId);
        if (!el) return;
        el.textContent = message;
        el.className = 'admin-info';
        if (type === 'success') el.classList.add('admin-success');
        if (type === 'error') el.classList.add('admin-error');
    }

    /** Reset admin state on logout — re-lock admin, close window, clear god view. */
    function resetOnLogout() {
        _adminUnlocked = false;
        _godViewEnabled = false;
        _adminSelectedSessionId = null;
        _pendingGodViewEnable = false;
        _godViewRefreshPending = false;
        clearTimeout(_godViewRefreshTimer);

        // Disable admin drag-and-drop
        try { KUnits.setAdminDrag(false); } catch(e) {}

        // Re-lock admin panel
        const gate = document.getElementById('admin-lock-gate');
        const content = document.getElementById('admin-content');
        if (gate) gate.style.display = 'block';
        if (content) content.style.display = 'none';

        // Clear password input
        const pw = document.getElementById('admin-pw-input');
        if (pw) pw.value = '';

        // Reset god view button and remove banner
        const godBtn = document.getElementById('admin-god-view-toggle');
        if (godBtn) {
            godBtn.textContent = '👁 God View OFF';
            godBtn.classList.remove('admin-btn-active');
        }
        _removeGodViewBanner();

        // Close admin window
        const win = document.getElementById('admin-window');
        if (win) win.style.display = 'none';
    }

    // ── Terrain Admin Functions ──────────────────────────

    async function _updateCellEstimate() {
        const depthSel = document.getElementById('terrain-analyze-depth');
        const estimateEl = document.getElementById('terrain-cell-estimate');
        if (!depthSel || !estimateEl) return;
        const depth = parseInt(depthSel.value || '3');
        const estimate = await KTerrain.estimateCellCount(depth);
        if (estimate) {
            const warn = estimate.total_cells > 50000 ? ' ⚠' : '';
            estimateEl.textContent = `≈ ${estimate.total_cells.toLocaleString()} cells (${estimate.cell_size_m}m)${warn}`;
            estimateEl.style.color = estimate.total_cells > 50000 ? '#ff9800' : '#4fc3f7';
        } else {
            estimateEl.textContent = '';
        }
    }

    async function _analyzeTerrain(force = false) {
        const sid = _getAdminSessionId();
        if (!sid) { await KDialogs.alert('No session selected'); return; };

        const depth = parseInt(document.getElementById('terrain-analyze-depth')?.value || '3');
        let skipElev = document.getElementById('terrain-skip-elevation')?.checked || false;
        const statusEl = document.getElementById('terrain-analyze-status');
        const progressContainer = document.getElementById('terrain-progress-container');
        const progressFill = document.getElementById('terrain-progress-fill');
        const progressText = document.getElementById('terrain-progress-text');

        // Auto-skip elevation for extremely high depths only if no rasterio
        if (depth >= 4 && !skipElev) {
            if (!await KDialogs.confirm(`Depth ${depth} generates many cells. This may take a few minutes.\n\nContinue?`, {title: 'Terrain Analysis'})) {
                return;
            }
        }

        // Show progress bar
        if (progressContainer) progressContainer.style.display = 'block';
        if (progressFill) { progressFill.style.width = '0%'; progressFill.classList.remove('error'); }
        if (progressText) progressText.textContent = 'Starting analysis...';
        if (statusEl) { statusEl.textContent = ''; statusEl.className = 'admin-info'; }

        // Disable buttons during analysis
        const analyzeBtn = document.getElementById('terrain-analyze-btn');
        const forceBtn = document.getElementById('terrain-analyze-force-btn');
        if (analyzeBtn) analyzeBtn.disabled = true;
        if (forceBtn) forceBtn.disabled = true;

        const result = await KTerrain.analyzeWithProgress(depth, force, skipElev, (event) => {
            // Update progress bar in real time
            if (progressFill && event.progress >= 0) {
                progressFill.style.width = `${Math.round(event.progress * 100)}%`;
            }
            if (progressText && event.message) {
                progressText.textContent = event.message;
            }
            if (event.step === 'error') {
                if (progressFill) progressFill.classList.add('error');
                if (statusEl) {
                    statusEl.textContent = `❌ ${event.message}`;
                    statusEl.className = 'admin-info admin-error';
                }
            }
        });

        // Re-enable buttons
        if (analyzeBtn) analyzeBtn.disabled = false;
        if (forceBtn) forceBtn.disabled = false;

        if (result) {
            if (progressFill) progressFill.style.width = '100%';
            if (progressText) progressText.textContent = `✅ Done in ${result.duration_s}s`;
            if (statusEl) {
                statusEl.textContent = `✅ ${result.cells_created} created, ${result.cells_updated} updated, ` +
                    `${result.cells_skipped} skipped. OSM: ${result.osm_features} features. ` +
                    `${result.cell_size_m}m resolution. Time: ${result.duration_s}s`;
                statusEl.className = 'admin-info admin-success';
            }
            _loadTerrainStats();

            // Auto-hide progress bar after 5 seconds
            setTimeout(() => {
                if (progressContainer) progressContainer.style.display = 'none';
            }, 5000);
        } else if (!statusEl?.textContent?.includes('❌')) {
            if (statusEl) { statusEl.textContent = '❌ Analysis failed. Check console.'; statusEl.className = 'admin-info admin-error'; }
        }
    }

    async function _clearTerrain() {
        if (!await KDialogs.confirm('Clear all auto-analyzed terrain? Manual cells will be preserved.', {dangerous: true})) return;
        await KTerrain.clearTerrain(true);
        const statusEl = document.getElementById('terrain-analyze-status');
        if (statusEl) { statusEl.textContent = '🗑 Terrain cleared (manual cells preserved)'; statusEl.className = 'admin-info'; }
        _loadTerrainStats();
    }

    let _terrainPaintChangeHandler = null;

    function _startTerrainPaint() {
        const type = document.getElementById('terrain-paint-type')?.value || 'forest';
        KTerrain.startPaintMode(type);
        // Make sure terrain layer is visible
        if (!KTerrain.isVisible()) KTerrain.toggle();
        const startBtn = document.getElementById('terrain-paint-start-btn');
        const stopBtn = document.getElementById('terrain-paint-stop-btn');
        if (startBtn) startBtn.style.display = 'none';
        if (stopBtn) stopBtn.style.display = '';

        // Listen for type changes — remove old listener first to prevent accumulation
        const sel = document.getElementById('terrain-paint-type');
        if (sel) {
            if (_terrainPaintChangeHandler) {
                sel.removeEventListener('change', _terrainPaintChangeHandler);
            }
            _terrainPaintChangeHandler = () => KTerrain.setPaintType(sel.value);
            sel.addEventListener('change', _terrainPaintChangeHandler);
        }
    }

    function _stopTerrainPaint() {
        KTerrain.stopPaintMode();
        const startBtn = document.getElementById('terrain-paint-start-btn');
        const stopBtn = document.getElementById('terrain-paint-stop-btn');
        if (startBtn) startBtn.style.display = '';
        if (stopBtn) stopBtn.style.display = 'none';
        // Clean up terrain paint type change listener
        if (_terrainPaintChangeHandler) {
            const sel = document.getElementById('terrain-paint-type');
            if (sel) sel.removeEventListener('change', _terrainPaintChangeHandler);
            _terrainPaintChangeHandler = null;
        }
    }

    async function _loadTerrainStats() {
        const container = document.getElementById('terrain-stats-container');
        if (!container) return;
        const stats = await KTerrain.getStats();
        if (!stats || stats.total_cells === 0) {
            container.innerHTML = '<span style="color:#888;">No terrain data. Run analysis first.</span>';
            return;
        }
        const colors = KTerrain.getTerrainColors();
        const labels = KTerrain.getTerrainLabels();
        let html = `<div style="margin-bottom:4px;"><b style="color:#4fc3f7;">Total cells:</b> ${stats.total_cells}</div>`;

        // Type breakdown chips
        html += '<div class="terrain-stats-bar">';
        for (const [type, count] of Object.entries(stats.by_type || {}).sort((a, b) => b[1] - a[1])) {
            const color = colors[type] || '#90EE90';
            const label = (labels[type] || type).replace(/^[^\w]+ /, '');
            html += `<span class="terrain-stat-chip" style="background:${color};">${label}: ${count}</span>`;
        }
        html += '</div>';

        // Source breakdown
        html += '<div style="margin-top:6px;font-size:10px;color:#aaa;">';
        html += '<b>Sources:</b> ';
        for (const [src, count] of Object.entries(stats.by_source || {})) {
            html += `${src}: ${count}  `;
        }
        html += '</div>';

        container.innerHTML = html;
    }

    // ══════════════════════════════════════════════════════════
    // ── Map Objects (Obstacles & Structures) Admin Panel ─────
    // ══════════════════════════════════════════════════════════

    let _objectsPanelInitialized = false;

    async function _initObjectsPanel() {
        const sid = _getAdminSessionId();
        if (!sid) return;

        // Load definitions from server
        await KMapObjects.loadDefinitions(sid);
        const defs = KMapObjects.getDefinitions();
        if (!defs) return;

        // Only render buttons once
        if (!_objectsPanelInitialized) {
            _renderObjectButtons(defs);
            _bindObjectsPanelEvents();
            _objectsPanelInitialized = true;
        }

        // Refresh object list
        _refreshObjectsList();
    }

    function _renderObjectButtons(defs) {
        const obstacleContainer = document.getElementById('admin-obstacle-buttons');
        const structureContainer = document.getElementById('admin-structure-buttons');
        const effectContainer = document.getElementById('admin-effect-buttons');
        if (!obstacleContainer || !structureContainer) return;

        const ICONS = {
            barbed_wire: '🪡', concertina_wire: '🔪', minefield: '💣',
            at_minefield: '🎯', entrenchment: '⛏', anti_tank_ditch: '🕳',
            dragons_teeth: '🦷', roadblock: '🚧',
            pillbox: '🛡', observation_tower: '👁',
            field_hospital: '✚', command_post_structure: '⚑',
            fuel_depot: '⛽', airfield: '✈️', supply_cache: '🗃',
            bridge_structure: '🌁',
            smoke: '🌫', fog_effect: '🌁', fire_effect: '🔥', chemical_cloud: '☣',
        };
        const GEOM_HINTS = {
            LineString: '(draw line, right-click to finish)',
            Polygon: '(draw polygon, right-click to finish)',
            Point: '(click to place)',
        };

        let obstacleHtml = '';
        let structureHtml = '';
        let effectHtml = '';

        for (const [key, defn] of Object.entries(defs)) {
            const icon = ICONS[key] || '⬟';
            const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            const color = defn.color || '#888';
            const hint = GEOM_HINTS[defn.geometry_type] || '';
            const btn = `<button class="map-obj-place-btn" data-objtype="${key}" title="${defn.description || label}\n${hint}" style="border-left:3px solid ${color};">
                <span class="map-obj-btn-icon">${icon}</span>
                <span class="map-obj-btn-label">${label}</span>
            </button>`;

            if (defn.category === 'obstacle') {
                obstacleHtml += btn;
            } else if (defn.category === 'effect') {
                effectHtml += btn;
            } else {
                structureHtml += btn;
            }
        }

        obstacleContainer.innerHTML = obstacleHtml;
        structureContainer.innerHTML = structureHtml;
        if (effectContainer) effectContainer.innerHTML = effectHtml;

        // Bind click handlers
        document.querySelectorAll('.map-obj-place-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const objType = btn.dataset.objtype;
                document.querySelectorAll('.map-obj-place-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                KMapObjects.startPlacement(objType);

                const stats = document.getElementById('admin-objects-stats');
                if (stats) stats.innerHTML = `<span style="color:#4fc3f7;">🎯 Placing: ${objType.replace(/_/g, ' ')}</span>`;
            });
        });
    }

    function _bindObjectsPanelEvents() {
        _bind('admin-objects-refresh', 'click', _refreshObjectsList);
        _bind('admin-objects-clear-all', 'click', async () => {
            const sid = _getAdminSessionId();
            if (!sid) return;
            if (!await KDialogs.confirm('Delete ALL map objects for this session?', {dangerous: true})) return;
            const token = _getToken();
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = `Bearer ${token}`;
            try {
                await fetch(`/api/sessions/${sid}/map-objects`, { method: 'DELETE', headers });
                KMapObjects.clearAll();
                _refreshObjectsList();
            } catch (e) { console.warn('Clear all objects failed:', e); }
        });
        _bind('admin-objects-toggle-vis', 'click', () => {
            const visible = KMapObjects.toggle();
            const btn = document.getElementById('admin-objects-toggle-vis');
            if (btn) btn.textContent = visible ? '👁 Hide' : '👁 Show';
        });
    }

    async function _refreshObjectsList() {
        const sid = _getAdminSessionId();
        if (!sid) return;
        const token = _getToken();
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};

        try {
            const resp = await fetch(`/api/sessions/${sid}/map-objects`, { headers });
            if (!resp.ok) return;
            const objects = await resp.json();

            const listEl = document.getElementById('admin-objects-list');
            const statsEl = document.getElementById('admin-objects-stats');
            if (!listEl) return;

            if (objects.length === 0) {
                listEl.innerHTML = '<div style="color:#888;padding:4px;">No objects placed yet</div>';
                if (statsEl) statsEl.innerHTML = '';
                return;
            }

            const ICONS = {
                barbed_wire: '🪡', concertina_wire: '🔪', minefield: '💣',
                at_minefield: '🎯', entrenchment: '⛏', anti_tank_ditch: '🕳',
                dragons_teeth: '🦷', roadblock: '🚧',
                pillbox: '🛡', observation_tower: '👁',
                field_hospital: '✚', command_post_structure: '⚑',
                fuel_depot: '⛽', airfield: '✈️', supply_cache: '🗃',
                bridge_structure: '🌁',
            };

            let html = '';
            for (const obj of objects) {
                const icon = ICONS[obj.object_type] || '⬟';
                const label = obj.label || obj.object_type.replace(/_/g, ' ');
                const status = obj.is_active ? '✓' : '✗';
                const statusColor = obj.is_active ? '#4caf50' : '#f44336';
                const health = obj.health !== undefined ? ` HP:${Math.round(obj.health * 100)}%` : '';
                const color = (obj.definition && obj.definition.color) || '#888';

                html += `<div class="map-obj-list-item" style="border-left:3px solid ${color};">
                    <span>${icon} ${label}</span>
                    <span style="color:${statusColor};font-size:10px;">${status}${health}</span>
                    <button class="map-obj-list-del" data-objid="${obj.id}" title="Delete">🗑</button>
                </div>`;
            }
            listEl.innerHTML = html;

            // Bind delete buttons
            listEl.querySelectorAll('.map-obj-list-del').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const objId = btn.dataset.objid;
                    const tok = _getToken();
                    const h = { 'Content-Type': 'application/json' };
                    if (tok) h['Authorization'] = `Bearer ${tok}`;
                    try {
                        await fetch(`/api/sessions/${sid}/map-objects/${objId}`, { method: 'DELETE', headers: h });
                        _refreshObjectsList();
                        KMapObjects.onObjectDeleted({ id: objId });
                    } catch (e) { console.warn('Delete object failed:', e); }
                });
            });

            // Stats summary
            if (statsEl) {
                const obstCount = objects.filter(o => o.object_category === 'obstacle').length;
                const structCount = objects.filter(o => o.object_category === 'structure').length;
                const activeCount = objects.filter(o => o.is_active).length;
                statsEl.innerHTML = `<span style="color:#4fc3f7;">${objects.length} objects</span> (${obstCount} obstacles, ${structCount} structures, ${activeCount} active)`;
            }
        } catch (e) {
            console.warn('Failed to refresh objects list:', e);
        }
    }

    // ══════════════════════════════════════════════════
    // ── Red AI Management ─────────────────────────────
    // ══════════════════════════════════════════════════

    function _initRedAI() {
        _bind('redai-create-btn', 'click', _createRedAgent);
        _bind('redai-refresh-btn', 'click', _loadRedAgents);
    }

    function _redEscHtml(s) {
        const d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    async function _createRedAgent() {
        const sid = _getAdminSessionId();
        const token = _getToken();
        if (!sid || !token) return;

        const name = (document.getElementById('redai-name')?.value || 'Red Commander').trim();
        const posture = document.getElementById('redai-posture')?.value || 'balanced';
        const missionType = document.getElementById('redai-mission')?.value || 'hold';

        try {
            const resp = await fetch(`/api/sessions/${sid}/red-agents`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({
                    name,
                    risk_posture: posture,
                    mission_intent: { type: missionType },
                }),
            });
            if (resp.ok) {
                KGameLog.addEntry(`🤖 Red AI agent '${name}' created (${posture})`, 'info');
                _loadRedAgents();
            } else {
                const d = await resp.json().catch(() => ({}));
                await KDialogs.alert(d.detail || 'Failed to create Red AI agent');
            }
        } catch (e) {
            console.error('Create Red agent failed:', e);
        }
    }

    async function _loadRedAgents() {
        const sid = _getAdminSessionId();
        const token = _getToken();
        const listEl = document.getElementById('redai-agents-list');
        if (!sid || !token || !listEl) return;

        try {
            const resp = await fetch(`/api/sessions/${sid}/red-agents`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) { listEl.innerHTML = '<div style="color:#ef5350;">Failed to load agents</div>'; return; }
            const agents = await resp.json();

            if (agents.length === 0) {
                listEl.innerHTML = '<div style="color:#888;font-style:italic;">No Red AI agents configured. Create one above.</div>';
                return;
            }

            listEl.innerHTML = agents.map(a => {
                const postureIcons = { aggressive: '🔥', balanced: '⚖', cautious: '🛡', defensive: '🏰' };
                const icon = postureIcons[a.risk_posture] || '⚖';
                const missionType = a.mission_intent?.type || 'hold';
                const unitCount = a.controlled_unit_ids ? a.controlled_unit_ids.length : '?';
                const lastTick = a.last_decision_tick || 0;
                const decisions = a.decision_state?.decisions_count || 0;
                const contacts = a.decision_state?.contacts_known || 0;

                return `<div style="border:1px solid #333;border-radius:4px;padding:8px;margin-bottom:6px;background:#1a1a2e;">
                    <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
                        <span style="font-weight:600;">${icon} ${_redEscHtml(a.name)}</span>
                        <span style="color:#888;font-size:10px;">${a.risk_posture}</span>
                        <span style="color:#888;font-size:10px;">| mission: ${missionType}</span>
                    </div>
                    <div style="font-size:10px;color:#aaa;">
                        Units: ${unitCount} | Last decision: tick ${lastTick} | Orders: ${decisions} | Contacts: ${contacts}
                    </div>
                    <div style="margin-top:6px;display:flex;gap:4px;">
                        <select class="redai-posture-edit" data-id="${a.id}" style="padding:2px 4px;background:#0d1117;border:1px solid #333;color:#e0e0e0;border-radius:3px;font-size:10px;">
                            <option value="aggressive" ${a.risk_posture==='aggressive'?'selected':''}>🔥 Aggressive</option>
                            <option value="balanced" ${a.risk_posture==='balanced'?'selected':''}>⚖ Balanced</option>
                            <option value="cautious" ${a.risk_posture==='cautious'?'selected':''}>🛡 Cautious</option>
                            <option value="defensive" ${a.risk_posture==='defensive'?'selected':''}>🏰 Defensive</option>
                        </select>
                        <select class="redai-mission-edit" data-id="${a.id}" style="padding:2px 4px;background:#0d1117;border:1px solid #333;color:#e0e0e0;border-radius:3px;font-size:10px;">
                            <option value="hold" ${missionType==='hold'?'selected':''}>Hold</option>
                            <option value="patrol" ${missionType==='patrol'?'selected':''}>Patrol</option>
                            <option value="attack" ${missionType==='attack'?'selected':''}>Attack</option>
                            <option value="defend" ${missionType==='defend'?'selected':''}>Defend</option>
                            <option value="withdraw" ${missionType==='withdraw'?'selected':''}>Withdraw</option>
                        </select>
                        <button class="admin-btn redai-update" data-id="${a.id}" style="font-size:10px;padding:2px 8px;">💾 Save</button>
                        <button class="admin-btn redai-force" data-id="${a.id}" style="font-size:10px;padding:2px 8px;">⚡ Force Decide</button>
                        <button class="admin-btn redai-delete" data-id="${a.id}" style="font-size:10px;padding:2px 8px;background:#c62828;">🗑</button>
                    </div>
                </div>`;
            }).join('');

            // Bind buttons
            listEl.querySelectorAll('.redai-update').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const agentId = btn.dataset.id;
                    const posture = listEl.querySelector(`.redai-posture-edit[data-id="${agentId}"]`)?.value;
                    const mission = listEl.querySelector(`.redai-mission-edit[data-id="${agentId}"]`)?.value;
                    try {
                        await fetch(`/api/sessions/${sid}/red-agents/${agentId}`, {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                            body: JSON.stringify({
                                risk_posture: posture,
                                mission_intent: { type: mission },
                            }),
                        });
                        KGameLog.addEntry(`🤖 Red AI agent updated`, 'info');
                        _loadRedAgents();
                    } catch (e) { console.error('Update Red agent failed:', e); }
                });
            });

            listEl.querySelectorAll('.redai-force').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const agentId = btn.dataset.id;
                    btn.disabled = true;
                    btn.textContent = '⏳...';
                    try {
                        const resp = await fetch(`/api/sessions/${sid}/red-agents/${agentId}/force-decide`, {
                            method: 'POST',
                            headers: { 'Authorization': `Bearer ${token}` },
                        });
                        if (resp.ok) {
                            const result = await resp.json();
                            KGameLog.addEntry(`🤖 Red AI forced: ${result.orders_created} orders created`, 'info');
                            _loadRedAgents();
                        } else {
                            const d = await resp.json().catch(() => ({}));
                            await KDialogs.alert(d.detail || 'Force decision failed');
                        }
                    } catch (e) { console.error('Force Red decision failed:', e); }
                    finally { btn.disabled = false; btn.textContent = '⚡ Force Decide'; }
                });
            });

            listEl.querySelectorAll('.redai-delete').forEach(btn => {
                btn.addEventListener('click', async () => {
                    if (!await KDialogs.confirm('Delete this Red AI agent?', {dangerous: true})) return;
                    const agentId = btn.dataset.id;
                    try {
                        await fetch(`/api/sessions/${sid}/red-agents/${agentId}`, {
                            method: 'DELETE',
                            headers: { 'Authorization': `Bearer ${token}` },
                        });
                        KGameLog.addEntry(`🤖 Red AI agent deleted`, 'info');
                        _loadRedAgents();
                    } catch (e) { console.error('Delete Red agent failed:', e); }
                });
            });

        } catch (e) {
            console.error('Load Red agents failed:', e);
            listEl.innerHTML = '<div style="color:#ef5350;">Error loading agents</div>';
        }
    }

    return {
        init, updateSessionContext, refreshScenarioList, isUnlocked, isGodViewEnabled,
        isWindowOpen: () => {
            const win = document.getElementById('admin-window');
            return _adminUnlocked && win && win.style.display !== 'none';
        },
        getAdminSessionId: _getAdminSessionId,
        onStateUpdate, refreshMapUnits, resetOnLogout,
        editScenario, editScenarioDetails, deleteScenario, deleteSession, createSessionFromScenario,
        renameSession, enterSession,
        kickParticipant,
        renameUser, deleteUser, assignUserToSession,
        loadPublicCoC,
        editUnit, focusUnit, deleteUnit, deleteAllUnits, addUnit, adminSplitUnit, adminMergeUnit,
        saveSessionToScenario,
        editUnitType, removeUnitType,
        wizardRemoveParticipant: _wizardRemoveParticipant,
    };
})();
