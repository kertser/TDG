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
            // Re-enable admin drag when admin window is reopened
            if (_adminUnlocked) {
                try { KUnits.setAdminDrag(true); } catch(e) {}
            }
        } else {
            _closeAdminWindow();
        }
    }

    /** Close admin window — disable god view and admin drag, then redraw normal view. */
    async function _closeAdminWindow() {
        const win = document.getElementById('admin-window');
        if (win) win.style.display = 'none';

        // Disable admin drag-and-drop when admin window is closed
        try { KUnits.setAdminDrag(false); } catch(e) {}

        // Disable god view if it was on
        if (_godViewEnabled) {
            _godViewEnabled = false;
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
        if (!password) { alert('Enter admin password'); return; }

        try {
            const resp = await fetch('/api/admin/verify-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password }),
            });
            if (resp.ok) {
                _adminUnlocked = true;
                const gate = document.getElementById('admin-lock-gate');
                const content = document.getElementById('admin-content');
                if (gate) gate.style.display = 'none';
                if (content) content.style.display = 'block';
                pw.value = '';
                // Show admin topbar button
                const topbarBtn = document.getElementById('admin-topbar-btn');
                if (topbarBtn) topbarBtn.style.display = '';
                // Load admin sessions dropdown
                _loadAdminSessions();
                // Enable admin drag-and-drop on unit markers
                try { KUnits.setAdminDrag(true); } catch(e) {}
                // Mark god view for auto-enable once a session is available
                if (!_godViewEnabled) {
                    _pendingGodViewEnable = true;
                    // Try immediately if session already exists
                    _tryAutoEnableGodView();
                }
            } else {
                const data = await resp.json().catch(() => ({}));
                alert(data.detail || 'Incorrect password');
            }
        } catch (err) {
            alert('Error: ' + err.message);
        }
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
        const newName = prompt('Rename session:', currentName);
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
                alert(d.detail || 'Rename failed');
            }
        } catch (err) { alert(err.message); }
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

    function _toggleBuilder() {
        if (KScenarioBuilder.isActive()) {
            KScenarioBuilder.deactivate();
        } else {
            KScenarioBuilder.activate();
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
                html += `<div class="admin-item">
                    <div>
                        <b>${s.title || 'Untitled'}</b>
                        <span class="admin-item-meta">${unitCount} units</span>
                    </div>
                    <div style="display:flex;gap:4px;">
                        <button class="admin-btn" onclick="KAdmin.createSessionFromScenario('${s.id}')" style="padding:2px 8px;font-size:10px;background:#1b5e20;color:#a5d6a7;" title="Create a new game session from this scenario">🎮 Session</button>
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

    async function editScenario(scenarioId) {
        KScenarioBuilder.activate(scenarioId);
        // Switch to builder sub-tab
        document.querySelectorAll('.admin-subtab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.admin-subtab-panel').forEach(p => p.style.display = 'none');
        const btn = document.querySelector('[data-panel="admin-builder-panel"]');
        if (btn) btn.classList.add('active');
        const panel = document.getElementById('admin-builder-panel');
        if (panel) panel.style.display = 'block';
    }

    async function deleteScenario(scenarioId) {
        if (!confirm('Delete this scenario?')) return;
        const token = _getToken();
        try {
            await fetch(`/api/admin/scenarios/${scenarioId}`, {
                method: 'DELETE',
                headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            });
            refreshScenarioList();
        } catch (err) {
            alert('Delete failed: ' + err.message);
        }
    }

    /** Create a new session from a scenario — opens wizard modal. */
    async function createSessionFromScenario(scenarioId) {
        const token = _getToken();
        if (!token) { alert('Not logged in'); return; }
        _openSessionWizard(scenarioId);
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
        for (let i = 1; i <= 3; i++) {
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
        const doneBtn = document.getElementById('wizard-done-btn');
        if (prevBtn) prevBtn.style.display = step > 1 && step < 3 ? '' : 'none';
        if (nextBtn) nextBtn.style.display = step === 1 ? '' : 'none';
        if (createBtn) createBtn.style.display = step === 2 ? '' : 'none';
        if (doneBtn) doneBtn.style.display = step === 3 ? '' : 'none';
    }

    function _wizardNextStep() {
        if (_wizardStep === 1) {
            const name = document.getElementById('wizard-session-name')?.value?.trim();
            if (!name) { alert('Session name is required'); return; }
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

            // 3b. Set operation datetime if provided
            if (opDatetime) {
                const isoTime = new Date(opDatetime).toISOString();
                await fetch(`/api/admin/sessions/${sessionData.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ current_time: isoTime }),
                });
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

            // Move to step 3 (done)
            _wizardShowStep(3);

        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'admin-info admin-error'; }
        }
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
        if (!confirm('⚠ Delete ALL scenarios?')) return;
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
        if (!confirm('⚠ Delete ALL sessions?')) return;

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
                const startBtn = document.getElementById('start-session-btn');
                const turnBtn = document.getElementById('turn-btn');
                if (startBtn) startBtn.style.display = 'none';
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
        if (!confirm(`Delete session ${sessionId.substring(0, 8)}…?`)) return;

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
                    const startBtn = document.getElementById('start-session-btn');
                    const turnBtn = document.getElementById('turn-btn');
                    if (startBtn) startBtn.style.display = 'none';
                    if (turnBtn) turnBtn.style.display = 'none';
                    try { KUnits.clearAll(); } catch(e) {}
                    try { KContacts.clearAll(); } catch(e) {}
                    try { KGrid.clearAll(); } catch(e) {}
                    try { KOverlays.clearAll(); } catch(e) {}
                }
                if (sessionId === _adminSelectedSessionId) {
                    _adminSelectedSessionId = null;
                }
                _loadAdminSessions();
                KSessionUI.loadSessions();
                KGameLog.addEntry(`Session ${sessionId.substring(0, 8)}… deleted (admin)`, 'info');
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Delete session failed');
            }
        } catch (err) { alert(err.message); }
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
        if (!confirm('⚠ Reset session to turn 0? All progress will be lost.')) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/reset`, {
                method: 'POST', headers: { 'Authorization': `Bearer ${token}` },
            });
            const data = await resp.json();
            _showInfo('admin-session-status', `✓ ${data.message}`, 'success');
            KGameLog.addEntry('Session reset to turn 0 (admin)', 'info');
            // Reload grid and units on the map if this is the active session
            const userSid = _getUserSessionId();
            if (sid === userSid) {
                const map = KMap.getMap();
                try { await KGrid.load(map, userSid); } catch(e) {}
                try { await KUnits.load(userSid, _getToken()); } catch(e) {}
            }
        } catch (err) {
            _showInfo('admin-session-status', `✗ ${err.message}`, 'error');
        }
    }

    async function _applyTurnInterval() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-session-status', 'Select a session first', 'error'); return; }
        const minutes = parseInt(document.getElementById('admin-turn-interval').value);
        if (!minutes || minutes < 1) { alert('Invalid interval'); return; }
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

        if (!confirm('⚠ Change scenario for this session?\nThis will RESET all units and grid to the selected scenario.\nAll current progress will be lost.')) return;

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
                // Reload grid and units on the map
                const userSid = _getUserSessionId();
                if (sid === userSid) {
                    const map = KMap.getMap();
                    try { await KGrid.load(map, userSid); } catch(e) {}
                    try { await KUnits.load(userSid, token); } catch(e) {}
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
        if (!confirm('Kick this participant?')) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/participants/${participantId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                _loadParticipants();
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Kick failed');
            }
        } catch (err) { alert(err.message); }
    }

    // ── Event Injection ──────────────────────────────

    async function _injectEvent() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { _showInfo('admin-event-status', 'Select a session first', 'error'); return; }
        const text = document.getElementById('admin-event-text').value.trim();
        const type = document.getElementById('admin-event-type').value || 'custom';
        if (!text) { alert('Enter event text'); return; }
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

    /** Fetch and render all units (god view). Called on toggle and on state_update. */
    async function _refreshGodView() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            const units = await resp.json();
            KUnits.render(units);
            _showInfo('admin-god-status', `Showing all ${units.length} units`, 'success');
        } catch (err) {
            _showInfo('admin-god-status', `✗ ${err.message}`, 'error');
        }
    }

    function isGodViewEnabled() { return _godViewEnabled; }

    /** Called by app.js when a state_update arrives via WebSocket.
     *  If god view is on, re-fetch admin units instead of using fog-of-war data. */
    async function onStateUpdate(data) {
        if (_godViewEnabled) {
            await _refreshGodView();
        }
    }

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
    function focusUnit(unitId) {
        const unit = _dashboardUnits.find(u => u.id === unitId);
        if (!unit || unit.lat == null || unit.lon == null) {
            alert('Unit has no position');
            return;
        }
        const map = KMap.getMap();
        if (map) map.setView([unit.lat, unit.lon], Math.max(map.getZoom(), 14));
    }

    /** Delete a unit (admin). */
    async function deleteUnit(unitId, unitName) {
        if (!confirm(`Delete unit "${unitName}"?`)) return;
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units/${unitId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (resp.ok || resp.status === 204) {
                _loadUnitDashboard();
                // Reload units on map
                const userSid = _getUserSessionId();
                if (sid === userSid) {
                    try { await KUnits.load(userSid, token); } catch(e) {}
                }
                KGameLog.addEntry(`Unit "${unitName}" deleted (admin)`, 'info');
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Delete failed');
            }
        } catch (err) { alert(err.message); }
    }

    /** Add a unit mid-session (admin) — opens the edit modal for creation. */
    async function addUnit() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { alert('Select a session first'); return; }

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
        }

        // Pre-fill with defaults for new unit
        _setVal('admin-ue-name', 'New Unit');
        _setVal('admin-ue-side', 'blue');
        _setVal('admin-ue-unit-type', 'infantry_platoon');
        _setVal('admin-ue-strength', 100);
        _setVal('admin-ue-morale', 90);
        _setVal('admin-ue-ammo', 100);
        _setVal('admin-ue-detection', 1500);
        _setVal('admin-ue-speed', 4);
        _setVal('admin-ue-lat', center.lat.toFixed(6));
        _setVal('admin-ue-lon', center.lng.toFixed(6));

        const label = document.getElementById('admin-ue-label');
        if (label) label.textContent = 'Create New Unit';

        const header = modal.querySelector('.admin-modal-header span');
        if (header) header.textContent = '➕ New Unit';

        const statusEl = document.getElementById('admin-ue-status');
        if (statusEl) statusEl.textContent = '';

        // Use __new__ marker so save knows to POST instead of PUT
        modal.dataset.unitId = '__new__';
        modal.style.display = 'flex';
    }

    /** Show edit modal for a unit. Also searches KUnits data if not in dashboard. */
    function editUnit(unitId) {
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
            _loadUnitDashboard().then(() => {
                const u2 = _dashboardUnits.find(u => u.id === unitId);
                if (u2) editUnit(unitId);
                else alert('Unit not found — try reloading the dashboard');
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
        _setVal('admin-ue-detection', unit.detection_range_m || 1500);
        _setVal('admin-ue-speed', unit.move_speed_mps || 4);
        _setVal('admin-ue-lat', unit.lat != null ? unit.lat.toFixed(6) : '');
        _setVal('admin-ue-lon', unit.lon != null ? unit.lon.toFixed(6) : '');

        const label = document.getElementById('admin-ue-label');
        if (label) label.textContent = `Edit: ${unit.name}`;

        const statusEl = document.getElementById('admin-ue-status');
        if (statusEl) statusEl.textContent = '';

        modal.dataset.unitId = unitId;
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

        const str = parseFloat(document.getElementById('admin-ue-strength').value);
        if (!isNaN(str)) body.strength = Math.max(0, Math.min(1, str / 100));

        const mor = parseFloat(document.getElementById('admin-ue-morale').value);
        if (!isNaN(mor)) body.morale = Math.max(0, Math.min(1, mor / 100));

        const amm = parseFloat(document.getElementById('admin-ue-ammo').value);
        if (!isNaN(amm)) body.ammo = Math.max(0, Math.min(1, amm / 100));

        const det = parseFloat(document.getElementById('admin-ue-detection').value);
        if (!isNaN(det)) body.detection_range_m = Math.max(0, det);

        const spd = parseFloat(document.getElementById('admin-ue-speed').value);
        if (!isNaN(spd)) body.move_speed_mps = Math.max(0, spd);

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
                if (statusEl) { statusEl.textContent = `✓ ${verb}`; statusEl.className = 'admin-info admin-success'; }
                setTimeout(() => {
                    modal.style.display = 'none';
                    // Reset modal header
                    const header = modal.querySelector('.admin-modal-header span');
                    if (header) header.textContent = '✏ Edit Unit';
                    _loadUnitDashboard();
                    // Refresh map units if this is the active session
                    const userSid = _getUserSessionId();
                    if (sid === userSid || _godViewEnabled) {
                        try {
                            if (_godViewEnabled) _refreshGodView();
                            else KUnits.load(userSid, token);
                        } catch(e) {}
                    }
                }, 200);
            } else {
                const d = await resp.json().catch(() => ({}));
                if (statusEl) { statusEl.textContent = `✗ ${d.detail || 'Failed'}`; statusEl.className = 'admin-info admin-error'; }
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = `✗ ${err.message}`; statusEl.className = 'admin-info admin-error'; }
        }
    }

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
                document.getElementById('admin-db-info').innerHTML = html;
            } else {
                _showInfo('admin-db-info', 'Stats endpoint not available');
            }
        } catch (err) {
            _showInfo('admin-db-info', `✗ ${err.message}`, 'error');
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
        if (checkboxes.length === 0) { alert('No users selected'); return; }
        if (!confirm(`⚠ Delete ${checkboxes.length} selected user(s)?`)) return;

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
                alert(d.detail || 'Bulk delete failed');
            }
        } catch (err) { alert(err.message); }
    }

    async function _addUser() {
        const nameEl = document.getElementById('admin-add-user-name');
        if (!nameEl) return;
        const name = nameEl.value.trim();
        if (!name) { alert('Enter a display name'); return; }

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
                alert(d.detail || 'Failed');
            }
        } catch (err) { alert(err.message); }
    }

    async function renameUser(userId, currentName) {
        const newName = prompt('New display name:', currentName);
        if (!newName || newName.trim() === currentName) return;
        try {
            await fetch(`/api/admin/users/${userId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ display_name: newName.trim() }),
            });
            _loadUsers();
        } catch (err) { alert(err.message); }
    }

    async function deleteUser(userId, name) {
        if (!confirm(`Delete user "${name}"? This will also remove them from all sessions.`)) return;
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
                alert(d.detail || 'Delete failed');
            }
        } catch (err) { alert(err.message); }
    }

    // ── Assign User to Session (from Users panel) ────

    let _assignPendingUserId = null;
    let _assignPendingDisplayName = null;

    function assignUserToSession(userId, displayName) {
        const sid = _getAdminSessionId();
        if (!sid) { alert('Select a session first in the admin session selector.'); return; }

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

        html += '<div class="coc-tree">';

        const blueRoots = roots.filter(u => u.side === 'blue');
        const redRoots = roots.filter(u => u.side === 'red');

        if (blueRoots.length > 0) {
            html += '<div class="coc-side-header" style="color:#4fc3f7;">BLUE FORCE</div>';
            blueRoots.forEach(u => { html += _renderCoCNode(u, 0, units, editable, allUnitMap); });
        }
        if (redRoots.length > 0) {
            html += '<div class="coc-side-header" style="color:#ef5350;margin-top:8px;">RED FORCE</div>';
            redRoots.forEach(u => { html += _renderCoCNode(u, 0, units, editable, allUnitMap); });
        }

        html += '</div>';
        el.innerHTML = html;

        // Bind admin assign/unassign (hierarchy structure) buttons
        if (editable) {
            el.querySelectorAll('.coc-assign-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const unitId = btn.dataset.unitId;
                    _showParentPicker(unitId, units);
                });
            });

            el.querySelectorAll('.coc-unassign-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const unitId = btn.dataset.unitId;
                    _setUnitParent(unitId, null);
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

        // Bind bulk action bar (admin only)
        if (_adminUnlocked) {
            // Populate bulk user select from cached participants (exclude observers)
            const bulkUserSel = document.getElementById('coc-bulk-user-select');
            if (bulkUserSel && _cachedParticipants.length > 0) {
                bulkUserSel.innerHTML = '<option value="">— Select user —</option>';
                _cachedParticipants.filter(p => p.side !== 'observer' && p.role !== 'observer').forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.user_id;
                    const sideIcon = p.side === 'blue' ? '🔵' : p.side === 'red' ? '🔴' : '⚪';
                    opt.textContent = `${sideIcon} ${p.display_name} (${p.role})`;
                    bulkUserSel.appendChild(opt);
                });
            }

            // Select-all checkbox
            const selectAllCb = document.getElementById('coc-bulk-select-all');
            if (selectAllCb) {
                selectAllCb.addEventListener('change', () => {
                    el.querySelectorAll('.coc-bulk-cb').forEach(cb => { cb.checked = selectAllCb.checked; });
                });
            }

            // Bulk assign button
            const bulkAssignBtn = document.getElementById('coc-bulk-assign-btn');
            if (bulkAssignBtn) bulkAssignBtn.addEventListener('click', _doBulkAssign);

            // Bulk unassign button
            const bulkUnassignBtn = document.getElementById('coc-bulk-unassign-btn');
            if (bulkUnassignBtn) bulkUnassignBtn.addEventListener('click', _doBulkUnassign);
        }
    }

    function _renderCoCNode(unit, depth, allUnits, editable, allUnitMap) {
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

        let html = `<div class="coc-node" style="margin-left:${indent}px;" title="${tooltip}">
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

        // Admin-only: parent hierarchy buttons
        if (editable && _adminUnlocked) {
            html += `<button class="coc-assign-btn" data-unit-id="${unit.id}" title="Assign to a parent commander">⬆</button>`;
            if (unit.parent_unit_id) {
                html += `<button class="coc-unassign-btn" data-unit-id="${unit.id}" title="Remove from parent chain of command">✕</button>`;
            }
        }

        html += `</div>`;

        if (hasChildren) {
            unit.children.forEach(child => {
                html += _renderCoCNode(child, depth + 1, allUnits, editable, allUnitMap);
            });
        }
        return html;
    }

    // ── Bulk Assign / Unassign ─────────────────────────

    async function _doBulkAssign() {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;

        const bulkUserSel = document.getElementById('coc-bulk-user-select');
        const userId = bulkUserSel ? bulkUserSel.value : '';
        if (!userId) { alert('Select a user to assign'); return; }

        const checkedBoxes = document.querySelectorAll('.coc-bulk-cb:checked');
        if (checkedBoxes.length === 0) { alert('No units selected'); return; }

        const unitIds = Array.from(checkedBoxes).map(cb => cb.dataset.unitId);
        const userName = bulkUserSel.options[bulkUserSel.selectedIndex]?.textContent || '';

        if (!confirm(`Assign ${userName} to ${unitIds.length} unit(s)?`)) return;

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

        const checkedBoxes = document.querySelectorAll('.coc-bulk-cb:checked');
        if (checkedBoxes.length === 0) { alert('No units selected'); return; }

        const unitIds = Array.from(checkedBoxes).map(cb => cb.dataset.unitId);

        if (!confirm(`Unassign commanders from ${unitIds.length} unit(s)?`)) return;

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

    function _showParentPicker(unitId, allUnits) {
        const unit = allUnits.find(u => u.id === unitId);
        if (!unit) return;

        const candidates = allUnits.filter(u =>
            u.id !== unitId && u.side === unit.side
        );

        if (candidates.length === 0) {
            alert('No available parent units');
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
                const sideIcon = c.side === 'blue' ? '🔵' : '🔴';
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
                    const token = _getToken();
                    const userSid = _getUserSessionId();
                    if (userSid && token) {
                        try {
                            if (_godViewEnabled) {
                                await _refreshGodView();
                            } else {
                                await KUnits.load(userSid, token);
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
                    const token2 = _getToken();
                    const userSid2 = _getUserSessionId();
                    if (userSid2 && token2) {
                        try {
                            if (_godViewEnabled) {
                                await _refreshGodView();
                            } else {
                                await KUnits.load(userSid2, token2);
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

    async function _setUnitParent(unitId, parentId) {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) return;

        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units/${unitId}/parent`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ parent_unit_id: parentId }),
            });
            if (resp.ok) {
                _loadChainOfCommand();
                loadPublicCoC();
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Failed');
            }
        } catch (err) { alert(err.message); }
    }

    // ── Unit Edit Modal ──────────────────────────────

    /** Admin split: splits a unit from the dashboard (via admin endpoint, no auth check). */
    async function adminSplitUnit(unitId) {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { alert('Select a session first'); return; }
        try {
            const resp = await fetch(`/api/admin/sessions/${sid}/units/${unitId}/split`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ ratio: 0.5 }),
            });
            if (resp.ok) {
                const data = await resp.json();
                KGameLog.addEntry(`Admin split: ${data.original.name} + ${data.new_unit.name}`, 'info');
                // Refresh everything
                await _loadUnitDashboard();
                const userSid = _getUserSessionId();
                if (userSid) {
                    try {
                        if (_godViewEnabled) await _refreshGodView();
                        else await KUnits.load(userSid, token);
                    } catch(e) {}
                }
                try { _loadChainOfCommand(); } catch(e) {}
                try { loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Split failed');
            }
        } catch (err) { alert(err.message); }
    }

    /** Admin merge: show picker of nearby same-type units and merge. */
    async function adminMergeUnit(unitId) {
        const token = _getToken(), sid = _getAdminSessionId();
        if (!token || !sid) { alert('Select a session first'); return; }

        const unit = _dashboardUnits.find(u => u.id === unitId);
        if (!unit) { alert('Unit not found in dashboard'); return; }

        // Find mergeable units (same principal type, same side — no distance restriction for admin)
        const principalType = _getPrincipalType(unit.unit_type);
        const nearby = _dashboardUnits.filter(ou => {
            if (ou.id === unit.id || ou.side !== unit.side || ou.is_destroyed) return false;
            if (_getPrincipalType(ou.unit_type) !== principalType) return false;
            return true;
        });

        if (nearby.length === 0) {
            alert(`No compatible units for "${unit.name}" (type: ${principalType})`);
            return;
        }

        // Show a simple selection prompt
        let msg = `Merge into "${unit.name}".\nSelect unit to absorb:\n\n`;
        nearby.forEach((ou, i) => {
            const strPct = ou.strength != null ? Math.round(ou.strength * 100) + '%' : '?';
            let distInfo = '';
            if (unit.lat != null && ou.lat != null) {
                const dist = Math.round(_haversineDist(unit.lat, unit.lon, ou.lat, ou.lon));
                distInfo = `, ${dist}m`;
            }
            msg += `${i + 1}. ${ou.name} (${strPct}${distInfo})\n`;
        });
        msg += '\nEnter number (1-' + nearby.length + '):';
        const choice = prompt(msg);
        if (!choice) return;
        const idx = parseInt(choice) - 1;
        if (isNaN(idx) || idx < 0 || idx >= nearby.length) { alert('Invalid choice'); return; }

        const mergeTarget = nearby[idx];
        if (!confirm(`Merge "${mergeTarget.name}" into "${unit.name}"?\nThe merged unit will be removed.`)) return;

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
                const userSid = _getUserSessionId();
                if (userSid) {
                    try {
                        if (_godViewEnabled) await _refreshGodView();
                        else await KUnits.load(userSid, token);
                    } catch(e) {}
                }
                try { _loadChainOfCommand(); } catch(e) {}
                try { loadPublicCoC(); } catch(e) {}
            } else {
                const d = await resp.json().catch(() => ({}));
                alert(d.detail || 'Merge failed');
            }
        } catch (err) { alert(err.message); }
    }

    /** Extract principal type from unit_type by stripping size suffixes. */
    function _getPrincipalType(unitType) {
        if (!unitType) return '';
        const suffixes = ['_battalion', '_company', '_battery', '_platoon', '_section', '_squad', '_team', '_post', '_unit'];
        for (const s of suffixes) {
            if (unitType.endsWith(s)) return unitType.slice(0, -s.length);
        }
        return unitType;
    }

    /** Haversine distance in meters. */
    function _haversineDist(lat1, lon1, lat2, lon2) {
        const R = 6371000;
        const toRad = (d) => d * Math.PI / 180;
        const dLat = toRad(lat2 - lat1);
        const dLon = toRad(lon2 - lon1);
        const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
        return 2 * R * Math.asin(Math.sqrt(a));
    }

    function _initUnitEditModal() {
        _bind('admin-ue-save', 'click', _saveUnitEdit);
        _bind('admin-ue-cancel', 'click', () => {
            const modal = document.getElementById('admin-unit-edit-modal');
            if (modal) modal.style.display = 'none';
        });
        _bind('admin-ue-close', 'click', () => {
            // Auto-save on close
            _saveUnitEdit();
        });
        const overlay = document.getElementById('admin-unit-edit-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) {
                    // Auto-save on backdrop click
                    _saveUnitEdit();
                }
            });
        }
    }

    // ── Session Context Update ───────────────────────

    function updateSessionContext() {
        const sid = _getUserSessionId();
        const statusEl = document.getElementById('admin-session-status');
        if (statusEl) {
            statusEl.textContent = sid ? `Active: ${sid.substring(0, 8)}...` : 'No active session';
        }
        // Auto-select in admin dropdown if no selection
        if (sid && !_adminSelectedSessionId) {
            _adminSelectedSessionId = sid;
            const sel = document.getElementById('admin-session-selector');
            if (sel) sel.value = sid;
        }
        // Refresh admin sessions list
        if (_adminUnlocked) {
            _loadAdminSessions();
            _tryAutoEnableGodView();
        }
    }

    // ── Helpers ──────────────────────────────────────

    /** Try to auto-enable god view (called when sessions become available). */
    function _tryAutoEnableGodView() {
        if (!_pendingGodViewEnable || _godViewEnabled) return;
        const sid = _getAdminSessionId();
        const token = _getToken();
        if (sid && token) {
            _pendingGodViewEnable = false;
            _toggleGodView();
        }
    }

    /** Load the grid for the admin-selected session onto the map. */
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

    function _addUnitType() {
        const key = prompt('Unit type key (e.g. "heavy_mortar"):');
        if (!key || !key.trim()) return;
        const label = prompt('Display label:', key);
        if (!label) return;

        const types = KScenarioBuilder.getUnitTypes();
        if (types[key.trim()]) { alert('Type already exists'); return; }

        types[key.trim()] = {
            label: label.trim(),
            sidc_blue: '10031000151211000000',
            sidc_red: '10061000151211000000',
            speed: 4.0,
            det: 1500,
            fire: 600,
            personnel: 20,
        };
        _renderUnitTypes();
        // Refresh unit type dropdown in builder
        try { _populateUnitTypeDropdown(); } catch(e) {}
    }

    function editUnitType(key) {
        const types = KScenarioBuilder.getUnitTypes();
        const info = types[key];
        if (!info) { alert('Type not found'); return; }

        const label = prompt('Label:', info.label);
        if (label !== null) info.label = label.trim();

        const det = prompt('Detection range (m):', info.det);
        if (det !== null && !isNaN(parseInt(det))) info.det = parseInt(det);

        const fire = prompt('Fire range (m):', info.fire);
        if (fire !== null && !isNaN(parseInt(fire))) info.fire = parseInt(fire);

        const speed = prompt('Speed (m/s):', info.speed);
        if (speed !== null && !isNaN(parseFloat(speed))) info.speed = parseFloat(speed);

        const personnel = prompt('Personnel:', info.personnel);
        if (personnel !== null && !isNaN(parseInt(personnel))) info.personnel = parseInt(personnel);

        const sidc = prompt('SIDC (Blue):', info.sidc_blue);
        if (sidc !== null && sidc.trim().length === 20) info.sidc_blue = sidc.trim();

        const sidcRed = prompt('SIDC (Red):', info.sidc_red);
        if (sidcRed !== null && sidcRed.trim().length === 20) info.sidc_red = sidcRed.trim();

        const isHQ = confirm('Is this an HQ unit type?');
        info.isHQ = isHQ;

        _renderUnitTypes();
        try { _populateUnitTypeDropdown(); } catch(e) {}
    }

    function removeUnitType(key) {
        if (!confirm(`Remove unit type "${key}"?`)) return;
        const types = KScenarioBuilder.getUnitTypes();
        delete types[key];
        _renderUnitTypes();
        try { _populateUnitTypeDropdown(); } catch(e) {}
    }

    function _resetUnitTypes() {
        if (!confirm('Reset all unit types to defaults? Custom types will be lost.')) return;
        alert('Please reload the page to reset unit types to defaults.');
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
        const depth = parseInt(depthSel.value || '2');
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
        if (!sid) return alert('No session selected');

        const depth = parseInt(document.getElementById('terrain-analyze-depth')?.value || '2');
        let skipElev = document.getElementById('terrain-skip-elevation')?.checked || false;
        const statusEl = document.getElementById('terrain-analyze-status');
        const progressContainer = document.getElementById('terrain-progress-container');
        const progressFill = document.getElementById('terrain-progress-fill');
        const progressText = document.getElementById('terrain-progress-text');

        // Auto-skip elevation for very high depths (too many API calls)
        if (depth >= 4 && !skipElev) {
            if (!confirm(`Depth ${depth} generates many cells. Elevation API will be very slow.\n\nSkip elevation? (Cancel = include elevation)`)) {
                skipElev = true;
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
        if (!confirm('Clear all auto-analyzed terrain? Manual cells will be preserved.')) return;
        await KTerrain.clearTerrain(true);
        const statusEl = document.getElementById('terrain-analyze-status');
        if (statusEl) { statusEl.textContent = '🗑 Terrain cleared (manual cells preserved)'; statusEl.className = 'admin-info'; }
        _loadTerrainStats();
    }

    function _startTerrainPaint() {
        const type = document.getElementById('terrain-paint-type')?.value || 'forest';
        KTerrain.startPaintMode(type);
        // Make sure terrain layer is visible
        if (!KTerrain.isVisible()) KTerrain.toggle();
        const startBtn = document.getElementById('terrain-paint-start-btn');
        const stopBtn = document.getElementById('terrain-paint-stop-btn');
        if (startBtn) startBtn.style.display = 'none';
        if (stopBtn) stopBtn.style.display = '';

        // Listen for type changes
        const sel = document.getElementById('terrain-paint-type');
        if (sel) sel.addEventListener('change', () => KTerrain.setPaintType(sel.value));
    }

    function _stopTerrainPaint() {
        KTerrain.stopPaintMode();
        const startBtn = document.getElementById('terrain-paint-start-btn');
        const stopBtn = document.getElementById('terrain-paint-stop-btn');
        if (startBtn) startBtn.style.display = '';
        if (stopBtn) stopBtn.style.display = 'none';
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

    return {
        init, updateSessionContext, refreshScenarioList, isUnlocked, isGodViewEnabled,
        onStateUpdate, resetOnLogout,
        editScenario, deleteScenario, deleteSession, createSessionFromScenario,
        renameSession, enterSession,
        kickParticipant,
        renameUser, deleteUser, assignUserToSession,
        loadPublicCoC,
        editUnit, focusUnit, deleteUnit, addUnit, adminSplitUnit, adminMergeUnit,
        editUnitType, removeUnitType,
        wizardRemoveParticipant: _wizardRemoveParticipant,
    };
})();
