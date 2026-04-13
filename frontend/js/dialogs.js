/**
 * dialogs.js – Themed custom dialogs replacing native confirm/alert/prompt.
 *
 * Provides:
 *   KDialogs.confirm(message, options)        → Promise<boolean>
 *   KDialogs.alert(message, options)          → Promise<void>
 *   KDialogs.prompt(message, defaultVal, options) → Promise<string|null>
 *   KDialogs.select(message, choices, options) → Promise<string|null>
 *
 * Options:
 *   title     — modal header text
 *   dangerous — red accent for destructive actions
 */
const KDialogs = (() => {

    let _overlay = null;
    let _resolveFunc = null;

    function _ensureOverlay() {
        if (_overlay) return _overlay;
        _overlay = document.createElement('div');
        _overlay.className = 'kdialog-overlay';
        _overlay.style.display = 'none';
        document.body.appendChild(_overlay);
        return _overlay;
    }

    function _show(html) {
        const overlay = _ensureOverlay();
        overlay.innerHTML = html;
        overlay.style.display = 'flex';
        // Close on overlay background click
        overlay.addEventListener('click', _onOverlayClick);
        // ESC to dismiss
        document.addEventListener('keydown', _onKeydown);
        // Focus first focusable element
        requestAnimationFrame(() => {
            const input = overlay.querySelector('.kdialog-input, .kdialog-select');
            if (input) { input.focus(); input.select && input.select(); }
            else {
                const btn = overlay.querySelector('.kdialog-btn-confirm, .kdialog-btn-ok');
                if (btn) btn.focus();
            }
        });
    }

    function _hide() {
        if (_overlay) {
            _overlay.style.display = 'none';
            _overlay.innerHTML = '';
            _overlay.removeEventListener('click', _onOverlayClick);
        }
        document.removeEventListener('keydown', _onKeydown);
    }

    function _onOverlayClick(e) {
        if (e.target === _overlay) {
            _resolve(null);
        }
    }

    function _onKeydown(e) {
        if (e.key === 'Escape') {
            _resolve(null);
        }
    }

    function _resolve(value) {
        _hide();
        if (_resolveFunc) {
            const fn = _resolveFunc;
            _resolveFunc = null;
            fn(value);
        }
    }

    function _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ── Confirm ──────────────────────────────────────

    function confirm(message, options = {}) {
        return new Promise((resolve) => {
            _resolveFunc = (val) => resolve(val === true);
            const title = options.title || (options.dangerous ? KI18n.t('dlg.confirm_danger') : KI18n.t('dlg.confirm'));
            const dangerousClass = options.dangerous ? ' kdialog-dangerous' : '';
            const confirmLabel = options.confirmLabel || (options.dangerous ? KI18n.t('dlg.confirm_yes_danger') : KI18n.t('dlg.confirm_yes'));
            const cancelLabel = options.cancelLabel || KI18n.t('dlg.cancel');
            const msgHtml = _escapeHtml(message).replace(/\n/g, '<br>');

            _show(`
                <div class="kdialog${dangerousClass}">
                    <div class="kdialog-header">${_escapeHtml(title)}</div>
                    <div class="kdialog-body">
                        <div class="kdialog-message">${msgHtml}</div>
                    </div>
                    <div class="kdialog-footer">
                        <button class="kdialog-btn kdialog-btn-cancel" data-action="cancel">${cancelLabel}</button>
                        <button class="kdialog-btn kdialog-btn-confirm${options.dangerous ? ' kdialog-btn-danger' : ''}" data-action="confirm">${confirmLabel}</button>
                    </div>
                </div>
            `);

            _overlay.querySelector('[data-action="confirm"]').addEventListener('click', () => _resolve(true));
            _overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => _resolve(false));
        });
    }

    // ── Alert ────────────────────────────────────────

    function alert(message, options = {}) {
        return new Promise((resolve) => {
            _resolveFunc = () => resolve();
            const title = options.title || KI18n.t('dlg.notice');
            const dangerousClass = options.dangerous ? ' kdialog-dangerous' : '';
            const msgHtml = _escapeHtml(message).replace(/\n/g, '<br>');

            _show(`
                <div class="kdialog${dangerousClass}">
                    <div class="kdialog-header">${_escapeHtml(title)}</div>
                    <div class="kdialog-body">
                        <div class="kdialog-message">${msgHtml}</div>
                    </div>
                    <div class="kdialog-footer">
                        <button class="kdialog-btn kdialog-btn-ok" data-action="ok">${KI18n.t('dlg.ok')}</button>
                    </div>
                </div>
            `);

            _overlay.querySelector('[data-action="ok"]').addEventListener('click', () => _resolve(true));
        });
    }

    // ── Prompt ───────────────────────────────────────

    function prompt(message, defaultValue = '', options = {}) {
        return new Promise((resolve) => {
            _resolveFunc = (val) => resolve(val);
            const title = options.title || KI18n.t('dlg.input');
            const msgHtml = _escapeHtml(message).replace(/\n/g, '<br>');
            const placeholder = options.placeholder || '';

            _show(`
                <div class="kdialog">
                    <div class="kdialog-header">${_escapeHtml(title)}</div>
                    <div class="kdialog-body">
                        <div class="kdialog-message">${msgHtml}</div>
                        <input type="text" class="kdialog-input" value="${_escapeHtml(defaultValue)}" placeholder="${_escapeHtml(placeholder)}" />
                    </div>
                    <div class="kdialog-footer">
                        <button class="kdialog-btn kdialog-btn-cancel" data-action="cancel">${KI18n.t('dlg.cancel')}</button>
                        <button class="kdialog-btn kdialog-btn-confirm" data-action="confirm">${KI18n.t('dlg.ok')}</button>
                    </div>
                </div>
            `);

            const input = _overlay.querySelector('.kdialog-input');
            _overlay.querySelector('[data-action="confirm"]').addEventListener('click', () => _resolve(input.value));
            _overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => _resolve(null));
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') _resolve(input.value);
                e.stopPropagation();
            });
        });
    }

    // ── Select (enhanced prompt with dropdown) ───────

    function select(message, choices, options = {}) {
        return new Promise((resolve) => {
            _resolveFunc = (val) => resolve(val);
            const title = options.title || KI18n.t('dlg.select');
            const dangerousClass = options.dangerous ? ' kdialog-dangerous' : '';
            const msgHtml = _escapeHtml(message).replace(/\n/g, '<br>');

            // choices: [{value, label}] or simple strings
            let optionsHtml = '';
            choices.forEach((c) => {
                const val = typeof c === 'string' ? c : c.value;
                const label = typeof c === 'string' ? c : c.label;
                optionsHtml += `<option value="${_escapeHtml(val)}">${_escapeHtml(label)}</option>`;
            });

            _show(`
                <div class="kdialog${dangerousClass}">
                    <div class="kdialog-header">${_escapeHtml(title)}</div>
                    <div class="kdialog-body">
                        <div class="kdialog-message">${msgHtml}</div>
                        <select class="kdialog-select" size="${Math.min(choices.length, 8)}">${optionsHtml}</select>
                    </div>
                    <div class="kdialog-footer">
                        <button class="kdialog-btn kdialog-btn-cancel" data-action="cancel">${KI18n.t('dlg.cancel')}</button>
                        <button class="kdialog-btn kdialog-btn-confirm" data-action="confirm">${KI18n.t('dlg.select_btn')}</button>
                    </div>
                </div>
            `);

            const sel = _overlay.querySelector('.kdialog-select');
            if (sel.options.length > 0) sel.selectedIndex = 0;
            _overlay.querySelector('[data-action="confirm"]').addEventListener('click', () => {
                _resolve(sel.value || null);
            });
            _overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => _resolve(null));
            sel.addEventListener('dblclick', () => _resolve(sel.value || null));
        });
    }

    return { confirm, alert, prompt, select };
})();


