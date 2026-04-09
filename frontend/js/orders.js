/**
 * orders.js – Bottom command panel: order input & tactical radio chat.
 *             Orders tab in sidebar is history-only.
 */
const KOrders = (() => {
    let _sessionId = null;
    let _token = null;
    let _orders = [];      // cached order list
    let _chatMessages = []; // chat message history
    let _participants = []; // session participants (commanders only)
    let _radioUnread = 0;  // count of unread radio messages
    let _radioChannel = 'all'; // 'all', 'chat', 'operative'

    /** Get localStorage key for last-read timestamp. */
    function _lastReadKey() {
        const uid = typeof KSessionUI !== 'undefined' ? KSessionUI.getUserId() : '';
        return `radio_last_read_${_sessionId}_${uid}`;
    }
    /** Return the last-read timestamp (epoch ms) or 0. */
    function _getLastRead() {
        try { return parseInt(localStorage.getItem(_lastReadKey()) || '0', 10) || 0; }
        catch { return 0; }
    }
    /** Persist current time as last-read. */
    function _setLastRead() {
        try { localStorage.setItem(_lastReadKey(), String(Date.now())); }
        catch { /* ignore */ }
    }

    function init(sessionId, token) {
        _sessionId = sessionId;
        _token = token;
        _chatMessages = [];
        _radioUnread = 0;

        // Show command panel
        const panel = document.getElementById('command-panel');
        if (panel) panel.style.display = '';

        // ── Tab switching inside command panel ──
        document.querySelectorAll('.cmd-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.cmd-tab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.cmd-tab-panel').forEach(p => p.classList.remove('active'));
                btn.classList.add('active');
                const tabPanel = document.getElementById(btn.dataset.cmdTab);
                if (tabPanel) tabPanel.classList.add('active');
                // Clear unread when switching to radio tab + scroll to bottom
                if (btn.dataset.cmdTab === 'cmd-radio') {
                    _radioUnread = 0;
                    _setLastRead();
                    _updateRadioLed();
                    _scrollRadioToBottom();
                }
            });
        });

        // ── Auto-scroll radio messages when panel becomes visible (hover/expand) ──
        // ── Hover zone with margin: collapse only when mouse moves 30px+ away for 800ms ──
        let _collapseTimer = null;
        const HOVER_MARGIN = 30;   // px beyond panel edge before collapse starts
        const COLLAPSE_DELAY = 800; // ms after leaving margin zone

        function _isMouseNearPanel(e) {
            const rect = panel.getBoundingClientRect();
            return (
                e.clientX >= rect.left - HOVER_MARGIN &&
                e.clientX <= rect.right + HOVER_MARGIN &&
                e.clientY >= rect.top - HOVER_MARGIN &&
                e.clientY <= rect.bottom + HOVER_MARGIN
            );
        }

        document.addEventListener('mousemove', (e) => {
            if (panel.classList.contains('expanded')) return; // pinned
            if (panel.style.display === 'none') return;

            if (_isMouseNearPanel(e)) {
                // Mouse inside margin zone — cancel any pending collapse
                if (_collapseTimer) { clearTimeout(_collapseTimer); _collapseTimer = null; }
            } else {
                // Mouse outside margin zone — start collapse countdown
                if (!_collapseTimer) {
                    _collapseTimer = setTimeout(() => {
                        _collapseTimer = null;
                        if (panel.classList.contains('expanded')) return;
                        const focused = panel.querySelector(':focus');
                        if (focused) focused.blur();
                    }, COLLAPSE_DELAY);
                }
            }
        });

        panel.addEventListener('mouseenter', () => {
            if (_collapseTimer) { clearTimeout(_collapseTimer); _collapseTimer = null; }
            _scrollRadioToBottom();
        });

        // ── Pin / unpin toggle (auto-collapse vs stay-open) ──
        const toggleBtn = document.getElementById('cmd-panel-toggle');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                const isExpanded = panel.classList.contains('expanded');
                panel.classList.toggle('expanded', !isExpanded);
                toggleBtn.innerHTML = isExpanded
                    ? '<svg viewBox="0 0 16 16" width="10" height="10"><path d="M8 2L8 10M4 6L8 2L12 6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
                    : '<svg viewBox="0 0 16 16" width="10" height="10"><path d="M3 4.5L8 1L13 4.5M3 8L8 4.5L13 8" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
                toggleBtn.title = isExpanded ? 'Pin panel open' : 'Auto-collapse';
            });
        }

        // ── Resize by dragging top edge ──
        _initResizeHandle(panel);


        // ── Radio channel sub-tabs ──
        document.querySelectorAll('.radio-ch-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.radio-ch-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                _radioChannel = btn.dataset.radioCh || 'all';
                _renderRadioMessages();
            });
        });

        // ── Order submit ──
        const submitBtn = document.getElementById('submit-order-btn');
        const textArea = document.getElementById('order-text');
        const clearSelBtn = document.getElementById('clear-unit-selection-btn');

        if (submitBtn) {
            submitBtn.addEventListener('click', () => _submitOrder());
        }
        if (textArea) {
            textArea.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    _submitOrder();
                }
            });
            // No auto-resize for orders textarea — it fills available space via CSS flex
        }
        if (clearSelBtn) {
            clearSelBtn.addEventListener('click', () => {
                KUnits.clearSelection();
                updateSelectedDisplay([]);
            });
        }
        // ── All Units button ──
        const allUnitsBtn = document.getElementById('select-all-units-btn');
        if (allUnitsBtn) {
            allUnitsBtn.addEventListener('click', () => {
                if (typeof KUnits !== 'undefined' && KUnits.selectAllCommandable) {
                    KUnits.selectAllCommandable();
                    updateSelectedDisplay(KUnits.getSelectedIds());
                }
            });
        }

        // ── Radio send ──
        const radioSendBtn = document.getElementById('radio-send-btn');
        const radioText = document.getElementById('radio-text');
        if (radioSendBtn) {
            radioSendBtn.addEventListener('click', () => _sendRadioMessage());
        }
        if (radioText) {
            radioText.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    _sendRadioMessage();
                }
            });
            radioText.addEventListener('input', () => _autoResize(radioText));
        }

        // Update meta info
        _updateMeta();

        // Load participants for radio
        _loadParticipants();

        // Load existing orders
        _loadOrders();

        // Load chat history from server
        _loadChatHistory();

        // Render initial radio state
        _renderRadioMessages();
    }

    function _autoResize(ta) {
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, 400) + 'px';
    }

    /** Initialize top-edge resize handle for the command panel. */
    function _initResizeHandle(panel) {
        // Remove existing resize handle if re-initialized
        const existing = panel.querySelector('.cmd-resize-handle');
        if (existing) existing.remove();

        // Create invisible grab zone at top edge
        const handle = document.createElement('div');
        handle.className = 'cmd-resize-handle';
        panel.insertBefore(handle, panel.firstChild);

        let _resizing = false;
        let _startY = 0;
        let _startH = 0;
        let _wasPinned = false;

        handle.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            e.preventDefault();
            e.stopPropagation();
            _resizing = true;
            _startY = e.clientY;
            // Get current panel height
            _startH = panel.offsetHeight;
            // Remember if user had it pinned before resize
            _wasPinned = panel.classList.contains('expanded');
            document.body.style.cursor = 'ns-resize';
            document.body.style.userSelect = 'none';
            // Pin panel open during resize
            panel.classList.add('expanded');
            panel.classList.add('resizing');
        });

        document.addEventListener('mousemove', (e) => {
            if (!_resizing) return;
            const dy = _startY - e.clientY; // dragging up = positive = taller
            const newH = Math.max(80, Math.min(window.innerHeight * 0.85, _startH + dy));
            // Apply custom height via CSS variable
            panel.style.setProperty('--cmd-panel-height', newH + 'px');
        });

        document.addEventListener('mouseup', () => {
            if (!_resizing) return;
            _resizing = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            // Step 1: remove resizing to re-enable CSS transitions
            panel.classList.remove('resizing');
            // Step 2: wait one frame so transitions are active before collapsing
            requestAnimationFrame(() => {
                if (!_wasPinned) {
                    panel.classList.remove('expanded');
                }
                // Blur any focused element to prevent :hover trap
                const focused = panel.querySelector(':focus');
                if (focused) focused.blur();
            });
        });
    }

    /** Scroll the radio messages container to the bottom, with retries for CSS transitions. */
    function _scrollRadioToBottom() {
        const container = document.getElementById('radio-messages');
        if (!container) return;
        // Immediate attempt
        container.scrollTop = container.scrollHeight;
        // Retry after animation frame (layout may not be ready)
        requestAnimationFrame(() => {
            container.scrollTop = container.scrollHeight;
        });
        // Retry after CSS transition completes (~300ms)
        setTimeout(() => {
            container.scrollTop = container.scrollHeight;
        }, 320);
    }

    /** Switch to Radio tab → Units (operative) channel, and scroll to bottom. */
    function _switchToRadioUnits() {
        // Activate Radio tab
        document.querySelectorAll('.cmd-tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.cmd-tab-panel').forEach(p => p.classList.remove('active'));
        const radioTabBtn = document.querySelector('.cmd-tab-btn[data-cmd-tab="cmd-radio"]');
        if (radioTabBtn) radioTabBtn.classList.add('active');
        const radioPanel = document.getElementById('cmd-radio');
        if (radioPanel) radioPanel.classList.add('active');

        // Switch to operative (Units) channel
        document.querySelectorAll('.radio-ch-btn').forEach(b => b.classList.remove('active'));
        const unitsChBtn = document.querySelector('.radio-ch-btn[data-radio-ch="operative"]');
        if (unitsChBtn) unitsChBtn.classList.add('active');
        _radioChannel = 'operative';
        _renderRadioMessages();

        // Clear unread
        _radioUnread = 0;
        _setLastRead();
        _updateRadioLed();
    }

    function _updateMeta() {
        const authorEl = document.getElementById('cmd-author');
        const dtEl = document.getElementById('cmd-datetime');
        if (authorEl) {
            const name = typeof KSessionUI !== 'undefined' ? KSessionUI.getUserName() : '';
            const side = typeof KSessionUI !== 'undefined' ? KSessionUI.getSide() : '';
            const sideIcon = side === 'red' ? '🔴' : side === 'blue' ? '🔵' : '👁';
            authorEl.textContent = name ? `${sideIcon} ${name}` : '';
        }
        if (dtEl) {
            // Try to get game time from the game clock
            const clockEl = document.querySelector('.game-clock-time');
            dtEl.textContent = clockEl ? clockEl.textContent : '';
        }
    }

    /** Periodically update game datetime in header */
    function refreshMeta() {
        _updateMeta();
    }

    // ── Participants (for Radio) ──
    async function _loadParticipants() {
        if (!_sessionId || !_token) return;
        try {
            const resp = await fetch(`/api/sessions/${_sessionId}/participants`, {
                headers: { 'Authorization': `Bearer ${_token}` },
            });
            if (!resp.ok) return;
            const all = await resp.json();
            // Filter to non-observers (commanders, officers, admins)
            _participants = all.filter(p => p.role !== 'observer' && p.side !== 'observer');
            _populateRecipientSelect();
        } catch (e) {
            console.warn('Failed to load participants for radio:', e);
        }
    }

    function _populateRecipientSelect() {
        const sel = document.getElementById('radio-recipient');
        if (!sel) return;
        const myId = typeof KSessionUI !== 'undefined' ? KSessionUI.getUserId() : null;
        sel.innerHTML = '<option value="all">📢 All Commanders</option>';
        _participants.forEach(p => {
            if (p.user_id === myId) return; // don't list self
            const sideIcon = p.side === 'red' ? '🔴' : p.side === 'blue' ? '🔵' : '⚪';
            const opt = document.createElement('option');
            opt.value = p.user_id;
            opt.textContent = `${sideIcon} ${p.display_name} (${p.role})`;
            sel.appendChild(opt);
        });
    }

    // ── Load Chat History ──
    async function _loadChatHistory() {
        if (!_sessionId || !_token) return;
        try {
            const resp = await fetch(`/api/sessions/${_sessionId}/chat`, {
                headers: { 'Authorization': `Bearer ${_token}` },
            });
            if (resp.ok) {
                const messages = await resp.json();
                _chatMessages = messages;
                _renderRadioMessages();

                // Count messages from others that arrived after last read
                const lastRead = _getLastRead();
                const radioTabBtn = document.querySelector('.cmd-tab-btn[data-cmd-tab="cmd-radio"]');
                const isRadioActive = radioTabBtn && radioTabBtn.classList.contains('active');
                if (!isRadioActive && lastRead > 0) {
                    const unread = messages.filter(m => {
                        if (m.own) return false;
                        const ts = m.timestamp ? new Date(m.timestamp).getTime() : 0;
                        return ts > lastRead;
                    }).length;
                    if (unread > 0) {
                        _radioUnread = unread;
                        _updateRadioLed();
                    }
                } else if (!isRadioActive && lastRead === 0 && messages.some(m => !m.own)) {
                    // First visit — treat all others' messages as unread
                    _radioUnread = messages.filter(m => !m.own).length;
                    _updateRadioLed();
                }
            }
        } catch (err) {
            console.warn('Failed to load chat history:', err);
        }
    }

    /** Update the glowing LED indicator on the Radio tab button. */
    function _updateRadioLed() {
        const radioBtns = document.querySelectorAll('.cmd-tab-btn');
        radioBtns.forEach(btn => {
            if (btn.dataset.cmdTab === 'cmd-radio') {
                let led = btn.querySelector('.radio-led');
                if (_radioUnread > 0) {
                    if (!led) {
                        led = document.createElement('span');
                        led.className = 'radio-led';
                        btn.appendChild(led);
                    }
                    led.textContent = _radioUnread > 9 ? '9+' : _radioUnread;
                    led.style.display = '';
                } else if (led) {
                    led.style.display = 'none';
                }
            }
        });
    }

    // ── Order Submit ──
    async function _submitOrder() {
        const textArea = document.getElementById('order-text');
        if (!textArea) return;
        const text = textArea.value.trim();
        if (!text) return;

        const selectedIds = KUnits.getSelectedIds();
        const submitBtn = document.getElementById('submit-order-btn');

        if (submitBtn) submitBtn.disabled = true;

        try {
            const resp = await fetch(`/api/sessions/${_sessionId}/orders`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${_token}`,
                },
                body: JSON.stringify({
                    original_text: text,
                    target_unit_ids: selectedIds.length > 0 ? selectedIds : null,
                }),
            });
            const result = await resp.json();

            if (resp.ok) {
                textArea.value = '';
                textArea.style.height = '';
                const unitNames = selectedIds.length > 0
                    ? ` → [${KUnits.getAllUnits().filter(u => selectedIds.includes(u.id)).map(u => u.name).join(', ')}]`
                    : '';
                KGameLog.addEntry(`Order issued: ${text}${unitNames}`, 'order');
                // Enrich with local user info for radio log display
                result.issuer_name = typeof KSessionUI !== 'undefined' ? KSessionUI.getUserName() : '';
                _orders.unshift(result);
                _renderOrders();
                KUnits.clearSelection();
                updateSelectedDisplay([]);

                // Auto-switch to Radio → Units channel to see the unit response
                _switchToRadioUnits();
            } else {
                const msg = result.detail || 'Order submission failed';
                KGameLog.addEntry(`⚠ Order failed: ${msg}`, 'info');
            }
        } catch (err) {
            console.error('Order submit failed:', err);
            KGameLog.addEntry('⚠ Order submission error', 'info');
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    }

    // ── Radio Messages ──
    function _sendRadioMessage() {
        const textArea = document.getElementById('radio-text');
        if (!textArea) return;
        const text = textArea.value.trim();
        if (!text) return;

        const recipientSel = document.getElementById('radio-recipient');
        const recipient = recipientSel ? recipientSel.value : 'all';

        // Send via WebSocket
        KWebSocket.send('chat_message', {
            text: text,
            recipient: recipient,  // 'all' or user_id
        });

        // Add to local immediately (optimistic)
        const myId = typeof KSessionUI !== 'undefined' ? KSessionUI.getUserId() : '';
        const myName = typeof KSessionUI !== 'undefined' ? KSessionUI.getUserName() : '';
        _chatMessages.push({
            sender_id: myId,
            sender_name: myName,
            text: text,
            recipient: recipient,
            timestamp: new Date().toISOString(),
            own: true,
        });
        _renderRadioMessages();

        textArea.value = '';
        textArea.style.height = '';
    }

    /** Called from app.js when a chat_message arrives via WS */
    function onChatMessage(data) {
        const myId = typeof KSessionUI !== 'undefined' ? KSessionUI.getUserId() : '';
        const isUnitResponse = data.is_unit_response || false;
        const isOrder = data.is_order || false;
        _chatMessages.push({
            sender_id: data.sender_id,
            sender_name: data.sender_name || 'Unknown',
            text: data.text,
            recipient: data.recipient || 'all',
            timestamp: data.timestamp || new Date().toISOString(),
            own: data.sender_id === myId,
            is_unit_response: isUnitResponse,
            is_order: isOrder,
            response_type: data.response_type || null,
        });
        _renderRadioMessages();

        // Check if radio tab is active; if not, increment unread
        const radioTabBtn = document.querySelector('.cmd-tab-btn[data-cmd-tab="cmd-radio"]');
        const isRadioActive = radioTabBtn && radioTabBtn.classList.contains('active');
        if (!isRadioActive) {
            _radioUnread++;
            _updateRadioLed();
        }

        // Also add to game log
        const recipientLabel = data.recipient === 'all' ? '(all)' : '(DM)';
        const prefix = isUnitResponse ? '' : '📻 ';
        KGameLog.addEntry(`${prefix}${data.sender_name} ${recipientLabel}: ${data.text}`, 'info');
    }

    function _renderRadioMessages() {
        const container = document.getElementById('radio-messages');
        if (!container) return;

        // Filter by channel
        let filtered = _chatMessages;
        if (_radioChannel === 'chat') {
            filtered = _chatMessages.filter(m => !m.is_unit_response && !m.is_order);
        } else if (_radioChannel === 'operative') {
            filtered = _chatMessages.filter(m => m.is_unit_response || m.is_order);
        }

        if (filtered.length === 0) {
            const emptyMsg = _radioChannel === 'operative'
                ? 'No unit radio traffic yet. Issue orders and units will respond here.'
                : _radioChannel === 'chat'
                ? 'No commander messages yet. Select a recipient and start communicating.'
                : 'No messages yet. Select a recipient and start communicating.';
            container.innerHTML = `<div class="radio-empty-hint">${emptyMsg}</div>`;
            return;
        }

        container.innerHTML = filtered.map(msg => {
            const isUnit = msg.is_unit_response || false;
            const isOrder = msg.is_order || false;
            const cls = isOrder ? 'msg-order' : isUnit ? 'msg-unit' : (msg.own ? 'msg-own' : 'msg-other');
            const time = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
            const recipientTag = msg.recipient !== 'all' && !msg.own ? ' (DM)' : '';
            return `<div class="radio-msg ${cls}">
                <div class="radio-msg-sender">${_escHtml(msg.sender_name)}${recipientTag}</div>
                <div class="radio-msg-text">${_escHtml(msg.text)}</div>
                <div class="radio-msg-time">${time}</div>
            </div>`;
        }).join('');

        // Scroll to bottom
        _scrollRadioToBottom();
    }

    // ── Orders History (sidebar tab) ──
    async function _loadOrders() {
        if (!_sessionId || !_token) return;
        try {
            const resp = await fetch(`/api/sessions/${_sessionId}/orders`, {
                headers: { 'Authorization': `Bearer ${_token}` },
            });
            if (resp.ok) {
                _orders = await resp.json();
                _orders.sort((a, b) => (b.issued_at || '').localeCompare(a.issued_at || ''));
                _renderOrders();
            }
        } catch (err) {
            console.warn('Failed to load orders:', err);
        }
    }

    function _renderOrders() {
        const list = document.getElementById('order-list');
        const countBadge = document.getElementById('orders-count');
        if (!list) return;

        if (countBadge) {
            countBadge.textContent = _orders.length > 0 ? `${_orders.length}` : '';
        }

        if (_orders.length === 0) {
            list.innerHTML = '<div class="order-radio-empty">No radio traffic yet. Issue orders from the command bar below.</div>';
            return;
        }

        const allUnits = typeof KUnits !== 'undefined' ? KUnits.getAllUnits() : [];
        const myName = typeof KSessionUI !== 'undefined' ? KSessionUI.getUserName() : 'Unknown';

        list.innerHTML = _orders.map(order => {
            const side = order.issued_by_side || 'blue';
            const sideCls = side === 'red' ? 'side-red' : '';

            // Get sender name
            const senderName = order.issuer_name || myName;

            // Get target unit names
            const unitIds = order.target_unit_ids || [];
            const unitNames = unitIds.map(id => {
                const u = allUnits.find(x => x.id === id);
                return u ? u.name : id.slice(0, 8);
            });
            const targetStr = unitNames.length > 0 ? unitNames.join(', ') : 'All units';

            // Format game time or wall-clock time
            let timeStr = '';
            if (order.game_timestamp) {
                const d = new Date(order.game_timestamp);
                timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            } else if (order.issued_at) {
                const d = new Date(order.issued_at);
                timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            }

            const status = order.status || 'pending';
            const statusIcons = {
                pending: '⏳', validated: '✓', executing: '⚙', completed: '✅', failed: '✗', cancelled: '—'
            };

            // Classification badge (from LLM)
            const classIcons = {
                command: '📋', status_request: '❓', acknowledgment: '✅',
                status_report: '📊', unclear: '⚠️'
            };
            const classification = order.classification || (order.parsed_order && order.parsed_order.classification);
            const classIcon = classification ? (classIcons[classification] || '') : '';
            const classBadge = classification
                ? `<span class="order-class-badge" title="Classification: ${classification}">${classIcon} ${classification}</span>`
                : '';

            // Confidence indicator
            const confidence = order.confidence || (order.parsed_order && order.parsed_order.confidence);
            const confBadge = confidence != null
                ? `<span class="order-conf-badge" title="Confidence: ${Math.round(confidence * 100)}%">${Math.round(confidence * 100)}%</span>`
                : '';

            // Processing spinner
            const isProcessing = order.processing && status === 'pending';
            const processingHtml = isProcessing
                ? '<span class="order-processing" title="AI analyzing...">⏳ analyzing...</span>'
                : '';

            // Language badge
            const lang = order.language || (order.parsed_order && order.parsed_order.language);
            const langBadge = lang ? `<span class="order-lang-badge">${lang.toUpperCase()}</span>` : '';

            return `<div class="order-radio-entry ${sideCls}">
                <div class="order-radio-header">
                    <span class="order-radio-time">${timeStr}</span>
                    <span class="order-radio-callsign ${sideCls}">${_escHtml(senderName)}</span>
                    <span class="order-radio-arrow">→</span>
                    <span class="order-radio-target">${_escHtml(targetStr)}</span>
                    ${langBadge}
                </div>
                <div class="order-radio-text">${_escHtml(order.original_text || '')}</div>
                <div class="order-radio-footer">
                    <span class="order-radio-status ${status}">${statusIcons[status] || ''} ${status}</span>
                    ${classBadge}${confBadge}${processingHtml}
                </div>
            </div>`;
        }).join('');
    }

    /** Update the selected units display with chip-style badges (in command panel). */
    function updateSelectedDisplay(selectedIds) {
        const container = document.getElementById('selected-units-display');
        if (!container) return;

        if (!selectedIds || selectedIds.length === 0) {
            container.innerHTML = '<span class="cmd-hint">Select units on the map</span>';
            return;
        }

        const allUnits = typeof KUnits !== 'undefined' ? KUnits.getAllUnits() : [];
        const chips = selectedIds.map(id => {
            const u = allUnits.find(x => x.id === id);
            const name = u ? u.name : id.slice(0, 8);
            return `<span class="orders-unit-chip" title="${name}">
                <span class="chip-dot" style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${u && u.side === 'red' ? '#ef5350' : '#42a5f5'};"></span>
                ${_escHtml(name)}
            </span>`;
        });
        container.innerHTML = chips.join('');
    }

    /** Receive order status update from WebSocket. */
    function onOrderStatus(data) {
        if (!data || !data.id) return;
        const orderId = data.id || data.order_id;
        const idx = _orders.findIndex(o => o.id === orderId);
        if (idx >= 0) {
            // Merge all new fields into existing order
            Object.assign(_orders[idx], data);
        } else {
            // New order we haven't seen — add it (but only if not a dupe by text+time)
            _orders.unshift(data);
        }
        _renderOrders();

        // Only show a gamelog entry for meaningful status transitions (not initial pending)
        if (data.status && data.status !== 'pending') {
            const statusLabels = { validated: '✓ Validated', executing: '⚙ Executing', completed: '✅ Completed', failed: '✗ Failed', cancelled: '— Cancelled' };
            const label = statusLabels[data.status] || data.status;
            const brief = data.original_text ? data.original_text.substring(0, 60) : data.id.slice(0,8);
            KGameLog.addEntry(`${label}: ${brief}`, data.status === 'failed' ? 'error' : 'order');
        }

        // Highlight resolved locations on the map
        if (data.resolved_locations && data.resolved_locations.length > 0) {
            _highlightLocations(data.resolved_locations);
        }
    }

    /** Temporarily highlight resolved locations on the map. */
    function _highlightLocations(locations) {
        if (typeof L === 'undefined' || typeof KMap === 'undefined') return;
        const map = KMap.getMap();
        if (!map) return;

        for (const loc of locations) {
            if (loc.lat == null || loc.lon == null) continue;
            const marker = L.circleMarker([loc.lat, loc.lon], {
                radius: 8, color: '#FF6600', fillColor: '#FF9933',
                fillOpacity: 0.5, weight: 2,
            }).addTo(map);

            // Add label
            if (loc.normalized_ref) {
                marker.bindTooltip(loc.normalized_ref, {
                    permanent: true, direction: 'top', className: 'location-tooltip'
                });
            }

            // Remove after 10 seconds
            setTimeout(() => {
                map.removeLayer(marker);
            }, 10000);
        }
    }

    /** Hide command panel (on logout). */
    function hide() {
        const panel = document.getElementById('command-panel');
        if (panel) panel.style.display = 'none';
        _chatMessages = [];
        _orders = [];
        _participants = [];
    }

    function _escHtml(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    return { init, updateSelectedDisplay, onOrderStatus, onChatMessage, refreshMeta, hide };
})();
