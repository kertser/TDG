/**
 * i18n.js — Internationalization module for KShU/TDG.
 * Supports English (en) and Russian (ru).
 * Language selector in user settings; applies translations in real-time.
 *
 * Usage:
 *   KI18n.t('key')            — get translated string
 *   KI18n.setLang('ru')       — switch language & re-apply all
 *   KI18n.getLang()            — current language code
 *
 * HTML attributes:
 *   data-i18n="key"             — set textContent
 *   data-i18n-title="key"       — set title (tooltip)
 *   data-i18n-placeholder="key" — set placeholder
 *   data-i18n-html="key"        — set innerHTML
 */
const KI18n = (() => {
    'use strict';

    let _lang = 'en';

    /* ═══════════════════════════════════════════════════════
     *  TRANSLATION DICTIONARIES
     * ═══════════════════════════════════════════════════════ */
    const T = {
        /* ─── English ──────────────────────────────────── */
        en: {
            // Auth panel
            'auth.welcome': 'Welcome, Commander',
            'auth.subtitle': 'Enter your callsign and password',
            'auth.callsign_ph': 'Callsign',
            'auth.password_ph': 'Password',
            'auth.hint': 'Min. 4 characters for password',

            // Auth errors (used in JS)
            'auth.callsign_required': 'Callsign required',
            'auth.password_min': 'Password must be at least 4 characters',
            'auth.password_required': 'Password required',
            'auth.connection_error': 'Connection error',

            // Settings modal
            'settings.title': '⚙️ Settings',
            'settings.section.map': 'Map',
            'settings.show_coords': 'Show coordinates in topbar',
            'settings.show_snail': 'Show snail address in topbar',
            'settings.show_zoom': 'Show zoom level',
            'settings.section.units': 'Units',
            'settings.unit_tooltips': 'Show unit tooltips on hover',
            'settings.hover_ranges': 'Show range circles on hover',
            'settings.section.notifications': 'Notifications',
            'settings.event_sound': 'Sound on new event',
            'settings.section.language': 'Language',
            'settings.language_label': 'Interface language',

            // Sidebar
            'sidebar.events': 'Events',
            'sidebar.reports': 'Reports',
            'sidebar.coc': 'Chain of Command',
            'sidebar.coc_desc': 'Unit hierarchy for the current session. Commanders can edit assignments.',
            'sidebar.applog': 'App Log',
            'sidebar.sessions_title': 'Sessions',
            'sidebar.no_sessions': 'No sessions available. Ask admin to create one and assign you.',

            // Command panel
            'cmd.select_units': 'Select units on the map',
            'cmd.order_ph': 'Type your order… (Ctrl+Enter to send)',
            'cmd.radio_ph': 'Type message… (Enter to send)',
            'cmd.all_commanders': '📢 All Commanders',

            // Radio channels
            'radio.all': 'All',
            'radio.chat': '💬 Chat',
            'radio.units': '📡 Units',
            'radio.all_tip': 'All messages',
            'radio.chat_tip': 'Human chat only',
            'radio.units_tip': 'Unit radio responses',

            // Tooltips – topbar
            'tip.session_info': 'Click to view scenario description',
            'tip.rules': 'Rules & Instructions',
            'tip.turn_btn': 'Submit your orders and advance to the next turn',
            'tip.user_menu': 'Click for user menu',
            'tip.admin': 'Admin Panel',

            // Tooltips – command panel
            'tip.orders_tab': 'Issue orders to selected units',
            'tip.radio_tab': 'Tactical radio — send messages to commanders',
            'tip.pin_panel': 'Pin panel open',
            'tip.select_all': 'Select all your units',
            'tip.clear_sel': 'Clear selection',
            'tip.cancel_orders': 'Cancel all orders for selected units (or all units)',
            'tip.pick_coords': 'Pick coordinates from map (click map to insert grid reference)',
            'tip.submit_order': 'Issue Order (Ctrl+Enter)',
            'tip.send_message': 'Send Message (Enter)',
            'tip.select_recipient': 'Select recipient',
            'tip.clear_radio': 'Clear all chat messages',

            // Tooltips – map controls
            'tip.hide_controls': 'Hide map controls',
            'tip.show_controls': 'Show map controls',
            'tip.center': 'Center on operation area',
            'tip.grid': 'Show/hide grid',
            'tip.units': 'Show/hide units',
            'tip.overlays': 'Show/hide overlays',
            'tip.terrain': 'Show/hide terrain',
            'tip.objects': 'Show/hide tactical objects',

            // Tooltips – draw tools
            'tip.arrow': 'Curved Arrow (right-click to finish)',
            'tip.los': 'LOS Check (click 2 points)',
            'tip.rect': 'Rectangle (dashed)',
            'tip.marker': 'Marker',
            'tip.ellipse': 'Ellipse (dashed)',
            'tip.measure': 'Measure (right-click to finish)',

            // Scenario briefing
            'brief.situation': 'Situation',
            'brief.mission': 'Mission / Task',
            'brief.op_start': 'Operation Start',
            'brief.environment': 'Environment Conditions',
            'brief.no_desc': 'No description available.',
            'brief.weather': '☁ Weather',
            'brief.visibility': '👁 Visibility',
            'brief.wind': '💨 Wind',
            'brief.precipitation': '🌧 Precipitation',
            'brief.light': '🔆 Light',
            'brief.temperature': '🌡 Temperature',

            // Turn button
            'turn.executing': '⏳ Executing...',
            'turn.pending': '⚠ {count} order(s) have not received radio confirmation from units yet:\n\n{units}\n\nProceed with turn execution anyway?',

            // Overlay context menu
            'ctx.label': 'Label',
            'ctx.color': 'Color',
            'ctx.width': 'Width',
            'ctx.thin': 'Thin',
            'ctx.medium': 'Medium',
            'ctx.thick': 'Thick',
            'ctx.edit_shape': 'Edit Shape',
            'ctx.delete': 'Delete',
            'ctx.enter_text': 'Enter text…',

            // User dropdown
            'user.no_session': '⚪ No session',
            'user.in_session': '🟢 In session',

            // Rules modal title
            'rules.title': '📖 Rules & Instructions',

            // ── Coordinate info bar (bottom-left) ──
            'tip.snail_display': 'Snail address under cursor',
            'tip.coord_display': 'Coordinates under cursor',
            'tip.zoom_display': 'Current zoom level',
            'tip.terrain_display': 'Terrain type under cursor',
            'tip.elevation_display': 'Elevation under cursor',

            // ── Game clock ──
            'clock.turn': 'Turn',

            // ── Sidebar toggle ──
            'tip.sidebar_toggle': 'Toggle sidebar',

            // ── Auth buttons ──
            'tip.register': 'Create a new account with this callsign and password',
            'tip.login': 'Login with existing callsign and password',

            // ── Session buttons ──
            'tip.start_session': 'Start the session (creates units from scenario)',

            // ── Export buttons ──
            'tip.export_reports': 'Export reports as Excel file',
            'tip.export_log': 'Export app log as text file',

            // ── CoC sidebar ──
            'tip.coc_refresh': 'Refresh chain of command',

            // ── Command panel badges ──
            'tip.sender': 'Sender',
            'tip.game_time': 'Game time',

            // ── Unit info tooltips ──
            'tip.detection_range': 'Detection range',
            'tip.fire_range': 'Fire range',
            'tip.move_speed': 'Movement speed',
            'tip.no_path': 'No terrain path found — unit using direct route',
            'tip.strength': 'Strength',

            // ── Order status in command panel ──
            'order.classification': 'Classification',
            'order.confidence': 'Confidence',
            'order.analyzing': 'AI analyzing...',

            // ── Color names (overlay context menu) ──
            'color.blue': 'Blue',
            'color.red': 'Red',
            'color.green': 'Green',
            'color.yellow': 'Yellow',
            'color.orange': 'Orange',
            'color.white': 'White',
            'color.black': 'Black',

            // ═══ Admin panel ═══
            'tip.admin_close': 'Close admin panel',
            'tip.admin_pw': 'Enter the admin password configured in server settings',
            'tip.admin_unlock': 'Verify admin password and unlock admin panel',
            'tip.admin_refresh_sessions': 'Refresh session list',

            // Admin sub-tabs
            'tip.admin_tab_session': 'Session controls & participants',
            'tip.admin_tab_monitor': 'God view, unit dashboard, orders',
            'tip.admin_tab_builder': 'Scenario builder & management',
            'tip.admin_tab_coc': 'Unit chain of command hierarchy',
            'tip.admin_tab_users': 'Manage registered users',
            'tip.admin_tab_types': 'Manage unit types and icons',
            'tip.admin_tab_terrain': 'Terrain analysis & painting',
            'tip.admin_tab_objects': 'Tactical obstacles & structures',
            'tip.admin_tab_redai': 'Red AI commander agents',

            // Admin builder
            'tip.sb_toggle': 'Toggle interactive scenario builder mode',
            'tip.sb_save_session': 'Save current session state (units, grid, agents) to the linked scenario',
            'tip.sb_turn_limit': '0 = unlimited',
            'tip.sb_grid_cols': 'Columns (1–20)',
            'tip.sb_grid_rows': 'Rows (1–20)',
            'tip.sb_grid_size': 'Square size (m)',
            'tip.sb_strength': 'Strength percentage (0-100%)',
            'tip.sb_ammo': 'Ammo percentage (0-100%)',
            'tip.sb_morale': 'Morale percentage (0-100%)',
            'tip.sb_detection': 'Detection range in meters',
            'tip.sb_speed': 'Movement speed in m/s',
            'tip.sb_list_scenarios': 'Refresh the list of saved scenarios',
            'tip.sb_delete_all_scenarios': 'Permanently delete all saved scenarios',

            // Admin session controls
            'tip.admin_delete_all_sessions': 'Permanently delete all game sessions',
            'tip.admin_refresh_sessions_list': 'Refresh session list and count',
            'tip.admin_pause': 'Pause the running session',
            'tip.admin_reset': 'Reset session to turn 0 (recreates units from scenario; preserves participants)',
            'tip.admin_turn_interval': 'Minutes of game time per turn',
            'tip.admin_apply_interval': 'Apply new turn interval to current session',
            'tip.admin_session_time': 'Override the in-game clock for this session',
            'tip.admin_set_time': 'Apply game clock override',
            'tip.admin_apply_scenario': 'Change scenario for the active session (reloads units & grid)',
            'tip.admin_load_participants': 'Load list of session participants',
            'tip.admin_inject_event': 'Inject a custom event into the session timeline',

            // Admin monitor
            'tip.admin_god_view': 'Toggle god view – see all units from both sides',
            'tip.admin_load_dashboard': 'Load all unit status data',
            'tip.admin_add_unit': 'Add a new unit to the session',
            'tip.admin_delete_all_units': 'Delete ALL units in this session',
            'tip.admin_load_orders': 'Load all orders from both sides',
            'tip.admin_db_stats': 'Show database table row counts',
            'tip.admin_debug_toggle': 'Toggle debug logging to file',
            'tip.admin_debug_view': 'View debug log contents',
            'tip.admin_debug_clear': 'Clear debug log file',

            // Admin grid
            'tip.admin_grid_from_session': 'Load grid origin and settings from current session',
            'tip.admin_grid_pick_map': 'Click on map to set grid origin',
            'tip.admin_apply_grid': 'Apply grid settings to the selected session',

            // Admin users
            'tip.admin_add_user_ph': 'Enter display name for new user',
            'tip.admin_add_user': 'Create a new user account',
            'tip.admin_load_users': 'Refresh the list of registered users',
            'tip.admin_select_all_users': 'Select/deselect all users',
            'tip.admin_bulk_delete_users': 'Delete all selected users',

            // Admin types
            'tip.admin_add_type': 'Add a new custom unit type',
            'tip.admin_reset_types': 'Reset all types to defaults',

            // Admin terrain
            'tip.terrain_analyze': 'Run terrain analysis (OSM + elevation)',
            'tip.terrain_force': 'Force re-analyze (overwrite existing)',
            'tip.terrain_clear': 'Clear auto-analyzed terrain (keeps manual)',
            'tip.terrain_paint_start': 'Start painting mode',
            'tip.terrain_paint_stop': 'Stop painting',
            'tip.terrain_show': 'Show/hide terrain overlay on map',
            'tip.terrain_elev': 'Show/hide elevation heatmap',
            'tip.terrain_legend': 'Show/hide terrain legend',

            // Admin objects
            'tip.objects_refresh': 'Refresh object list',
            'tip.objects_clear': 'Delete all map objects',
            'tip.objects_toggle': 'Show/hide objects layer',

            // Admin CoC
            'tip.admin_load_coc': 'Load unit hierarchy for the current session',

            // Admin dashboard unit actions
            'tip.unit_edit': 'Edit unit settings',
            'tip.unit_split': 'Split unit into two subunits',
            'tip.unit_merge': 'Merge with nearby unit',
            'tip.unit_focus': 'Center map on unit',
            'tip.unit_delete': 'Delete this unit',

            // Admin scenario actions
            'tip.scenario_create_session': 'Create a new game session from this scenario',
            'tip.scenario_edit_details': 'Edit scenario title, description, and settings',
            'tip.scenario_edit': 'Edit this scenario on map',
            'tip.scenario_delete': 'Delete this scenario',
            'tip.session_enter': 'Enter this session as admin',
            'tip.session_rename': 'Rename this session',
            'tip.session_delete': 'Delete this session',

            // Admin participants
            'tip.kick_participant': 'Remove this participant from the session',

            // Admin CoC tree
            'tip.coc_assign_commander': 'Assign a commander to this unit',
            'tip.coc_move_up': 'Move up in hierarchy (detach from parent)',
            'tip.coc_move_down': 'Move down (become subordinate of a sibling)',
            'tip.coc_assign_parent': 'Assign to a specific parent unit',
            'tip.coc_detach': 'Detach from parent (make independent)',
            'tip.coc_bulk_assign': 'Assign checked units to the selected commander',
            'tip.coc_bulk_unassign': 'Unassign all checked units from their commanders',

            // Admin unit editor
            'tip.ue_close': 'Close',
            'tip.ue_preview': 'Live symbol preview',
            'tip.ue_lat': 'Latitude',
            'tip.ue_lon': 'Longitude',
            'tip.ue_pick_map': 'Pick position on map',

            // Wizard
            'tip.wizard_interval': 'Minutes of game time per turn',
            'tip.wizard_turn_limit': 'Turn limit (0 = no limit)',
            'tip.wizard_op_time': 'In-game operation start date and time',

            // Scenario builder context menu
            'tip.sb_ctx_edit': 'Edit unit properties',
            'tip.sb_ctx_duplicate': 'Create a copy of this unit nearby',
            'tip.sb_ctx_toggle_side': 'Switch unit to opposite side',
            'tip.sb_ctx_center': 'Pan map to this unit',
            'tip.sb_ctx_str100': 'Set strength to 100%',
            'tip.sb_ctx_str50': 'Set strength to 50%',
            'tip.sb_ctx_str25': 'Set strength to 25%',
            'tip.sb_ctx_delete': 'Remove this unit',

            // Admin effects / env start time
            'tip.env_start_time': 'Operation start date and time',

            // ══════════════════════════════════════════════════
            // RUNTIME TRANSLATIONS (used in JS code via KI18n.t())
            // ══════════════════════════════════════════════════

            // ── Contact popup ──
            'contact.title': 'Enemy Contact',
            'contact.type': 'Type',
            'contact.confidence': 'Confidence',
            'contact.source': 'Source',
            'contact.accuracy': 'Accuracy',
            'contact.stale': '⚠ Stale contact — no recent observation',

            // ── Unit tooltips & info card ──
            'unit.identified': '[IDENTIFIED]',
            'unit.enemy': '[ENEMY]',
            'unit.strength_label': 'strength',
            'unit.enemy_contact': 'Enemy contact',
            'unit.personnel': 'personnel',
            'unit.est_condition': 'Estimated condition',
            'unit.full_strength': 'Full strength',
            'unit.reduced': 'Reduced',
            'unit.weakened': 'Weakened',
            'unit.critical': 'Critical',
            'unit.task_label': 'Task',
            'unit.salvos': 'salvos',
            'unit.no_path_warn': 'No terrain path found — using direct route',
            'unit.comms': 'Comms',
            'unit.formation_label': 'Formation',
            'unit.heading': 'Heading',
            'unit.co': 'CO',
            'unit.you': '(you)',
            'unit.assigned': 'Assigned',
            'unit.unassigned': 'Unassigned',
            'unit.part_of': 'Part of',

            // ── Unit context menu ──
            'unit.select': '☐ Select',
            'unit.deselect': '✓ Deselect',
            'unit.rename': '✏ Rename',
            'unit.formation': '🔲 Formation ▸',
            'unit.set_move': '🚶 Set Move ▸',
            'unit.stop': '⏹ Stop',
            'unit.split': '✂ Split Unit',
            'unit.merge': '🔗 Merge Unit ▸',
            'unit.delete': '🗑 Delete Unit',
            'unit.disband': '⛔ Disband Unit',
            'unit.fire_smoke': '🌫 Fire Smoke',
            'unit.assign_me': '+ Assign to me',
            'unit.unassign_me': '✕ Unassign me',

            // ── Strength estimates (fog-of-war) ──
            'strength.full': 'full',
            'strength.reduced': 'reduced',
            'strength.weakened': 'weakened',
            'strength.critical': 'critical',

            // ── Toggle button states ──
            'toggle.grid_show': 'Show grid',
            'toggle.grid_hide': 'Hide grid',
            'toggle.units_show': 'Show units',
            'toggle.units_hide': 'Hide units',
            'toggle.overlays_show': 'Show overlays',
            'toggle.overlays_hide': 'Hide overlays',
            'toggle.terrain_show': 'Show terrain',
            'toggle.terrain_hide': 'Hide terrain',
            'toggle.objects_show': 'Show tactical objects',
            'toggle.objects_hide': 'Hide tactical objects',

            // ── Terrain tooltip ──
            'terrain.source': 'Source',
            'terrain.elevation': 'Elevation',
            'terrain.slope': 'Slope',
            'terrain.aspect': 'Aspect',
            'terrain.move': 'Move',
            'terrain.vis': 'Vis',
            'terrain.prot': 'Prot',
            'terrain.atk': 'Atk',

            // ── Map objects tooltip & context menu ──
            'obj.dissipates': 'Dissipates in',
            'obj.min': 'min',
            'obj.burns_out': 'Burns out in',
            'obj.damages_units': '⚠ Damages units inside',
            'obj.toxic': '⚠ Toxic — heavy damage',
            'obj.active': '✓ Active',
            'obj.inactive': '✗ Inactive',
            'obj.prot': 'Prot',
            'obj.activate': '🟢 Activate',
            'obj.deactivate': '🔴 Deactivate',
            'obj.revealed': 'Revealed',
            'obj.hidden': 'Hidden',
            'obj.reveal': 'Reveal',
            'obj.hide': 'Hide',
            'obj.delete': '🗑 Delete',

            // ── Reports ──
            'reports.all': 'All',
            'reports.click_center': 'Click to center map',
            'reports.turn': 'Turn',
            'reports.game_time': 'Game Time',
            'reports.channel': 'Channel',
            'reports.side': 'Side',
            'reports.report_text': 'Report Text',
            'reports.export_header': 'TDG Reports — Exported',

            // ── Channel names ──
            'channel.spotrep': 'SPOTREP',
            'channel.shelrep': 'SHELREP',
            'channel.sitrep': 'SITREP',
            'channel.intsum': 'INTSUM',
            'channel.casrep': 'CASREP',
            'channel.contactrep': 'CONTACT',
            'channel.custom': 'REPORT',

            // ── Game log ──
            'log.no_entries': 'No log entries to export.',
            'log.header': 'TDG Application Log',
            'log.session': 'Session',
            'log.exported': 'Exported',

            // ── Dialogs ──
            'dlg.confirm': 'Confirm',
            'dlg.confirm_danger': '⚠ Confirm',
            'dlg.confirm_yes': '✓ Confirm',
            'dlg.confirm_yes_danger': '⚠ Yes, proceed',
            'dlg.cancel': 'Cancel',
            'dlg.notice': 'Notice',
            'dlg.ok': 'OK',
            'dlg.input': 'Input',
            'dlg.select': 'Select',
            'dlg.select_btn': '✓ Select',

            // ── Settings buttons ──
            'settings.save': '✓ Save',
            'settings.close': 'Close',
        },

        /* ─── Russian ──────────────────────────────────── */
        ru: {
            // Auth panel
            'auth.welcome': 'Добро пожаловать, Командир',
            'auth.subtitle': 'Введите позывной и пароль',
            'auth.callsign_ph': 'Позывной',
            'auth.password_ph': 'Пароль',
            'auth.hint': 'Минимум 4 символа для пароля',

            // Auth errors
            'auth.callsign_required': 'Необходимо ввести позывной',
            'auth.password_min': 'Пароль должен содержать минимум 4 символа',
            'auth.password_required': 'Необходимо ввести пароль',
            'auth.connection_error': 'Ошибка подключения',

            // Settings modal
            'settings.title': '⚙️ Настройки',
            'settings.section.map': 'Карта',
            'settings.show_coords': 'Показывать координаты на панели',
            'settings.show_snail': 'Показывать адрес «улитки» на панели',
            'settings.show_zoom': 'Показывать уровень масштаба',
            'settings.section.units': 'Подразделения',
            'settings.unit_tooltips': 'Подсказки при наведении на юниты',
            'settings.hover_ranges': 'Круги дальности при наведении',
            'settings.section.notifications': 'Уведомления',
            'settings.event_sound': 'Звук при новом событии',
            'settings.section.language': 'Язык',
            'settings.language_label': 'Язык интерфейса',

            // Sidebar
            'sidebar.events': 'События',
            'sidebar.reports': 'Донесения',
            'sidebar.coc': 'Боевой порядок',
            'sidebar.coc_desc': 'Иерархия подразделений текущей сессии. Командиры могут редактировать назначения.',
            'sidebar.applog': 'Журнал',
            'sidebar.sessions_title': 'Сессии',
            'sidebar.no_sessions': 'Нет доступных сессий. Обратитесь к администратору.',

            // Command panel
            'cmd.select_units': 'Выберите юниты на карте',
            'cmd.order_ph': 'Введите приказ… (Ctrl+Enter для отправки)',
            'cmd.radio_ph': 'Введите сообщение… (Enter для отправки)',
            'cmd.all_commanders': '📢 Все командиры',

            // Radio channels
            'radio.all': 'Все',
            'radio.chat': '💬 Чат',
            'radio.units': '📡 Юниты',
            'radio.all_tip': 'Все сообщения',
            'radio.chat_tip': 'Только сообщения командиров',
            'radio.units_tip': 'Радиообмен подразделений',

            // Tooltips – topbar
            'tip.session_info': 'Нажмите для просмотра описания сценария',
            'tip.rules': 'Правила и инструкции',
            'tip.turn_btn': 'Отправить приказы и перейти к следующему ходу',
            'tip.user_menu': 'Меню пользователя',
            'tip.admin': 'Панель администратора',

            // Tooltips – command panel
            'tip.orders_tab': 'Отдать приказы выбранным юнитам',
            'tip.radio_tab': 'Тактическое радио — сообщения командирам',
            'tip.pin_panel': 'Закрепить панель',
            'tip.select_all': 'Выбрать все ваши юниты',
            'tip.clear_sel': 'Снять выделение',
            'tip.cancel_orders': 'Отменить все приказы для выбранных юнитов (или всех)',
            'tip.pick_coords': 'Выбрать координаты на карте (нажмите на карту для вставки координат)',
            'tip.submit_order': 'Отдать приказ (Ctrl+Enter)',
            'tip.send_message': 'Отправить (Enter)',
            'tip.select_recipient': 'Выбрать получателя',
            'tip.clear_radio': 'Очистить все сообщения',

            // Tooltips – map controls
            'tip.hide_controls': 'Скрыть элементы управления',
            'tip.show_controls': 'Показать элементы управления',
            'tip.center': 'Центрировать на район операции',
            'tip.grid': 'Показать/скрыть сетку',
            'tip.units': 'Показать/скрыть юниты',
            'tip.overlays': 'Показать/скрыть наложения',
            'tip.terrain': 'Показать/скрыть рельеф',
            'tip.objects': 'Показать/скрыть тактические объекты',

            // Tooltips – draw tools
            'tip.arrow': 'Кривая стрелка (ПКМ для завершения)',
            'tip.los': 'Проверка прямой видимости (2 точки)',
            'tip.rect': 'Прямоугольник (пунктир)',
            'tip.marker': 'Маркер',
            'tip.ellipse': 'Эллипс (пунктир)',
            'tip.measure': 'Измерение (ПКМ для завершения)',

            // Scenario briefing
            'brief.situation': 'Обстановка',
            'brief.mission': 'Задача',
            'brief.op_start': 'Начало операции',
            'brief.environment': 'Условия обстановки',
            'brief.no_desc': 'Описание отсутствует.',
            'brief.weather': '☁ Погода',
            'brief.visibility': '👁 Видимость',
            'brief.wind': '💨 Ветер',
            'brief.precipitation': '🌧 Осадки',
            'brief.light': '🔆 Освещение',
            'brief.temperature': '🌡 Температура',

            // Turn button
            'turn.executing': '⏳ Выполняется...',
            'turn.pending': '⚠ {count} приказ(ов) не получил(и) подтверждения от подразделений:\n\n{units}\n\nПродолжить выполнение?',

            // Overlay context menu
            'ctx.label': 'Надпись',
            'ctx.color': 'Цвет',
            'ctx.width': 'Толщина',
            'ctx.thin': 'Тонкая',
            'ctx.medium': 'Средняя',
            'ctx.thick': 'Толстая',
            'ctx.edit_shape': 'Редактировать фигуру',
            'ctx.delete': 'Удалить',
            'ctx.enter_text': 'Введите текст…',

            // User dropdown
            'user.no_session': '⚪ Нет сессии',
            'user.in_session': '🟢 В сессии',

            // Rules modal title
            'rules.title': '📖 Правила и инструкции',

            // ── Coordinate info bar (bottom-left) ──
            'tip.snail_display': 'Адрес «улитки» под курсором',
            'tip.coord_display': 'Координаты под курсором',
            'tip.zoom_display': 'Текущий масштаб',
            'tip.terrain_display': 'Тип местности под курсором',
            'tip.elevation_display': 'Высота над уровнем моря',

            // ── Game clock ──
            'clock.turn': 'Ход',

            // ── Sidebar toggle ──
            'tip.sidebar_toggle': 'Показать/скрыть боковую панель',

            // ── Auth buttons ──
            'tip.register': 'Создать новый аккаунт с этим позывным и паролем',
            'tip.login': 'Войти с существующим позывным и паролем',

            // ── Session buttons ──
            'tip.start_session': 'Запустить сессию (создаёт юниты из сценария)',

            // ── Export buttons ──
            'tip.export_reports': 'Экспорт донесений в Excel',
            'tip.export_log': 'Экспорт журнала приложения в текстовый файл',

            // ── CoC sidebar ──
            'tip.coc_refresh': 'Обновить боевой порядок',

            // ── Command panel badges ──
            'tip.sender': 'Отправитель',
            'tip.game_time': 'Игровое время',

            // ── Unit info tooltips ──
            'tip.detection_range': 'Дальность обнаружения',
            'tip.fire_range': 'Дальность стрельбы',
            'tip.move_speed': 'Скорость движения',
            'tip.no_path': 'Маршрут не найден — юнит движется по прямой',
            'tip.strength': 'Численность',

            // ── Order status in command panel ──
            'order.classification': 'Классификация',
            'order.confidence': 'Уверенность',
            'order.analyzing': 'ИИ анализирует...',

            // ── Color names (overlay context menu) ──
            'color.blue': 'Синий',
            'color.red': 'Красный',
            'color.green': 'Зелёный',
            'color.yellow': 'Жёлтый',
            'color.orange': 'Оранжевый',
            'color.white': 'Белый',
            'color.black': 'Чёрный',

            // ═══ Admin panel ═══
            'tip.admin_close': 'Закрыть панель администратора',
            'tip.admin_pw': 'Введите пароль администратора, заданный в настройках сервера',
            'tip.admin_unlock': 'Проверить пароль и разблокировать панель',
            'tip.admin_refresh_sessions': 'Обновить список сессий',

            // Admin sub-tabs
            'tip.admin_tab_session': 'Управление сессией и участниками',
            'tip.admin_tab_monitor': 'Обзор всех юнитов, панель управления',
            'tip.admin_tab_builder': 'Конструктор сценариев',
            'tip.admin_tab_coc': 'Иерархия подразделений',
            'tip.admin_tab_users': 'Управление пользователями',
            'tip.admin_tab_types': 'Типы юнитов и символы',
            'tip.admin_tab_terrain': 'Анализ и раскраска местности',
            'tip.admin_tab_objects': 'Тактические объекты и сооружения',
            'tip.admin_tab_redai': 'ИИ-командиры противника',

            // Admin builder
            'tip.sb_toggle': 'Включить/выключить конструктор сценариев',
            'tip.sb_save_session': 'Сохранить текущее состояние сессии (юниты, сетка, агенты) в связанный сценарий',
            'tip.sb_turn_limit': '0 = без ограничений',
            'tip.sb_grid_cols': 'Столбцы (1–20)',
            'tip.sb_grid_rows': 'Строки (1–20)',
            'tip.sb_grid_size': 'Размер квадрата (м)',
            'tip.sb_strength': 'Численность в процентах (0-100%)',
            'tip.sb_ammo': 'Боеприпасы в процентах (0-100%)',
            'tip.sb_morale': 'Боевой дух в процентах (0-100%)',
            'tip.sb_detection': 'Дальность обнаружения в метрах',
            'tip.sb_speed': 'Скорость движения в м/с',
            'tip.sb_list_scenarios': 'Обновить список сценариев',
            'tip.sb_delete_all_scenarios': 'Удалить все сохранённые сценарии',

            // Admin session controls
            'tip.admin_delete_all_sessions': 'Удалить все игровые сессии',
            'tip.admin_refresh_sessions_list': 'Обновить список сессий и счётчик',
            'tip.admin_pause': 'Приостановить текущую сессию',
            'tip.admin_reset': 'Сбросить сессию к ходу 0 (пересоздаёт юниты из сценария; участники сохраняются)',
            'tip.admin_turn_interval': 'Минуты игрового времени за ход',
            'tip.admin_apply_interval': 'Применить интервал хода к текущей сессии',
            'tip.admin_session_time': 'Переопределить игровые часы для данной сессии',
            'tip.admin_set_time': 'Применить переопределение игрового времени',
            'tip.admin_apply_scenario': 'Сменить сценарий для активной сессии (перезагружает юниты и сетку)',
            'tip.admin_load_participants': 'Загрузить список участников сессии',
            'tip.admin_inject_event': 'Вставить пользовательское событие в хронологию сессии',

            // Admin monitor
            'tip.admin_god_view': 'Переключить режим бога — видеть все юниты обеих сторон',
            'tip.admin_load_dashboard': 'Загрузить данные о всех юнитах',
            'tip.admin_add_unit': 'Добавить новый юнит в сессию',
            'tip.admin_delete_all_units': 'Удалить ВСЕ юниты в этой сессии',
            'tip.admin_load_orders': 'Загрузить все приказы обеих сторон',
            'tip.admin_db_stats': 'Показать количество строк в таблицах БД',
            'tip.admin_debug_toggle': 'Включить/выключить запись отладочного журнала',
            'tip.admin_debug_view': 'Просмотреть отладочный журнал',
            'tip.admin_debug_clear': 'Очистить файл отладочного журнала',

            // Admin grid
            'tip.admin_grid_from_session': 'Загрузить настройки сетки из текущей сессии',
            'tip.admin_grid_pick_map': 'Выбрать начало сетки на карте',
            'tip.admin_apply_grid': 'Применить настройки сетки к выбранной сессии',

            // Admin users
            'tip.admin_add_user_ph': 'Введите имя нового пользователя',
            'tip.admin_add_user': 'Создать новый аккаунт пользователя',
            'tip.admin_load_users': 'Обновить список зарегистрированных пользователей',
            'tip.admin_select_all_users': 'Выбрать/снять выбор со всех пользователей',
            'tip.admin_bulk_delete_users': 'Удалить всех выбранных пользователей',

            // Admin types
            'tip.admin_add_type': 'Добавить новый тип юнита',
            'tip.admin_reset_types': 'Сбросить все типы к значениям по умолчанию',

            // Admin terrain
            'tip.terrain_analyze': 'Запустить анализ местности (OSM + высоты)',
            'tip.terrain_force': 'Принудительный повторный анализ (перезапись)',
            'tip.terrain_clear': 'Очистить автоматический анализ (сохранить ручные)',
            'tip.terrain_paint_start': 'Начать режим рисования',
            'tip.terrain_paint_stop': 'Остановить рисование',
            'tip.terrain_show': 'Показать/скрыть слой местности на карте',
            'tip.terrain_elev': 'Показать/скрыть карту высот',
            'tip.terrain_legend': 'Показать/скрыть легенду местности',

            // Admin objects
            'tip.objects_refresh': 'Обновить список объектов',
            'tip.objects_clear': 'Удалить все объекты на карте',
            'tip.objects_toggle': 'Показать/скрыть слой объектов',

            // Admin CoC
            'tip.admin_load_coc': 'Загрузить иерархию подразделений текущей сессии',

            // Admin dashboard unit actions
            'tip.unit_edit': 'Редактировать настройки юнита',
            'tip.unit_split': 'Разделить юнит на два подразделения',
            'tip.unit_merge': 'Объединить с ближайшим юнитом',
            'tip.unit_focus': 'Центрировать карту на юните',
            'tip.unit_delete': 'Удалить юнит',

            // Admin scenario actions
            'tip.scenario_create_session': 'Создать новую игровую сессию из этого сценария',
            'tip.scenario_edit_details': 'Изменить название, описание и настройки сценария',
            'tip.scenario_edit': 'Редактировать сценарий на карте',
            'tip.scenario_delete': 'Удалить сценарий',
            'tip.session_enter': 'Войти в сессию как администратор',
            'tip.session_rename': 'Переименовать сессию',
            'tip.session_delete': 'Удалить сессию',

            // Admin participants
            'tip.kick_participant': 'Исключить участника из сессии',

            // Admin CoC tree
            'tip.coc_assign_commander': 'Назначить командира для этого подразделения',
            'tip.coc_move_up': 'Повысить в иерархии (отсоединить от родителя)',
            'tip.coc_move_down': 'Понизить (сделать подчинённым соседнего подразделения)',
            'tip.coc_assign_parent': 'Назначить конкретное головное подразделение',
            'tip.coc_detach': 'Отсоединить от родителя (сделать независимым)',
            'tip.coc_bulk_assign': 'Назначить выбранные юниты указанному командиру',
            'tip.coc_bulk_unassign': 'Снять назначение со всех выбранных юнитов',

            // Admin unit editor
            'tip.ue_close': 'Закрыть',
            'tip.ue_preview': 'Предпросмотр символа',
            'tip.ue_lat': 'Широта',
            'tip.ue_lon': 'Долгота',
            'tip.ue_pick_map': 'Выбрать положение на карте',

            // Wizard
            'tip.wizard_interval': 'Минуты игрового времени за ход',
            'tip.wizard_turn_limit': 'Лимит ходов (0 = без ограничений)',
            'tip.wizard_op_time': 'Дата и время начала операции в игре',

            // Scenario builder context menu
            'tip.sb_ctx_edit': 'Изменить свойства юнита',
            'tip.sb_ctx_duplicate': 'Создать копию этого юнита рядом',
            'tip.sb_ctx_toggle_side': 'Переключить юнит на другую сторону',
            'tip.sb_ctx_center': 'Центрировать карту на юните',
            'tip.sb_ctx_str100': 'Установить численность 100%',
            'tip.sb_ctx_str50': 'Установить численность 50%',
            'tip.sb_ctx_str25': 'Установить численность 25%',
            'tip.sb_ctx_delete': 'Удалить юнит',

            // Admin effects / env start time
            'tip.env_start_time': 'Дата и время начала операции',

            // ══════════════════════════════════════════════════
            // RUNTIME TRANSLATIONS (used in JS code via KI18n.t())
            // ══════════════════════════════════════════════════

            // ── Contact popup ──
            'contact.title': 'Контакт с противником',
            'contact.type': 'Тип',
            'contact.confidence': 'Достоверность',
            'contact.source': 'Источник',
            'contact.accuracy': 'Точность',
            'contact.stale': '⚠ Устаревший контакт — нет свежих данных',

            // ── Unit tooltips & info card ──
            'unit.identified': '[ОПОЗНАН]',
            'unit.enemy': '[ПРОТИВНИК]',
            'unit.strength_label': 'численность',
            'unit.enemy_contact': 'Контакт с противником',
            'unit.personnel': 'л/с',
            'unit.est_condition': 'Оценка состояния',
            'unit.full_strength': 'Полная численность',
            'unit.reduced': 'Потери',
            'unit.weakened': 'Ослаблен',
            'unit.critical': 'Критический',
            'unit.task_label': 'Задача',
            'unit.salvos': 'залпов',
            'unit.no_path_warn': 'Маршрут не найден — движение по прямой',
            'unit.comms': 'Связь',
            'unit.formation_label': 'Построение',
            'unit.heading': 'Курс',
            'unit.co': 'Командир',
            'unit.you': '(вы)',
            'unit.assigned': 'Назначен',
            'unit.unassigned': 'Не назначен',
            'unit.part_of': 'Входит в',

            // ── Unit context menu ──
            'unit.select': '☐ Выбрать',
            'unit.deselect': '✓ Снять выбор',
            'unit.rename': '✏ Переименовать',
            'unit.formation': '🔲 Построение ▸',
            'unit.set_move': '🚶 Движение ▸',
            'unit.stop': '⏹ Стоп',
            'unit.split': '✂ Разделить',
            'unit.merge': '🔗 Объединить ▸',
            'unit.delete': '🗑 Удалить',
            'unit.disband': '⛔ Расформировать',
            'unit.fire_smoke': '🌫 Дымовая завеса',
            'unit.assign_me': '+ Взять командование',
            'unit.unassign_me': '✕ Снять командование',

            // ── Strength estimates (fog-of-war) ──
            'strength.full': 'полная',
            'strength.reduced': 'потери',
            'strength.weakened': 'ослаблен',
            'strength.critical': 'критический',

            // ── Toggle button states ──
            'toggle.grid_show': 'Показать сетку',
            'toggle.grid_hide': 'Скрыть сетку',
            'toggle.units_show': 'Показать юниты',
            'toggle.units_hide': 'Скрыть юниты',
            'toggle.overlays_show': 'Показать графику',
            'toggle.overlays_hide': 'Скрыть графику',
            'toggle.terrain_show': 'Показать рельеф',
            'toggle.terrain_hide': 'Скрыть рельеф',
            'toggle.objects_show': 'Показать объекты',
            'toggle.objects_hide': 'Скрыть объекты',

            // ── Terrain tooltip ──
            'terrain.source': 'Источник',
            'terrain.elevation': 'Высота',
            'terrain.slope': 'Уклон',
            'terrain.aspect': 'Экспозиция',
            'terrain.move': 'Движ.',
            'terrain.vis': 'Вид.',
            'terrain.prot': 'Защ.',
            'terrain.atk': 'Атк.',

            // ── Map objects tooltip & context menu ──
            'obj.dissipates': 'Рассеется через',
            'obj.min': 'мин',
            'obj.burns_out': 'Догорит через',
            'obj.damages_units': '⚠ Наносит урон',
            'obj.toxic': '⚠ Отравляющее вещество',
            'obj.active': '✓ Активен',
            'obj.inactive': '✗ Неактивен',
            'obj.prot': 'Защ.',
            'obj.activate': '🟢 Активировать',
            'obj.deactivate': '🔴 Деактивировать',
            'obj.revealed': 'Обнаружен',
            'obj.hidden': 'Скрыт',
            'obj.reveal': 'Показать',
            'obj.hide': 'Скрыть',
            'obj.delete': '🗑 Удалить',

            // ── Reports ──
            'reports.all': 'Все',
            'reports.click_center': 'Центрировать на карте',
            'reports.turn': 'Ход',
            'reports.game_time': 'Игр. время',
            'reports.channel': 'Канал',
            'reports.side': 'Сторона',
            'reports.report_text': 'Текст донесения',
            'reports.export_header': 'Донесения TDG — Экспорт',

            // ── Channel names (military terminology) ──
            'channel.spotrep': 'РАЗВЕДДОНЕСЕНИЕ',
            'channel.shelrep': 'ОБСТРЕЛ',
            'channel.sitrep': 'ОБСТАНОВКА',
            'channel.intsum': 'РАЗВЕД.СВОДКА',
            'channel.casrep': 'ПОТЕРИ',
            'channel.contactrep': 'КОНТАКТ',
            'channel.custom': 'ДОКЛАД',

            // ── Game log ──
            'log.no_entries': 'Нет записей для экспорта.',
            'log.header': 'Журнал приложения TDG',
            'log.session': 'Сессия',
            'log.exported': 'Экспортировано',

            // ── Dialogs ──
            'dlg.confirm': 'Подтверждение',
            'dlg.confirm_danger': '⚠ Подтверждение',
            'dlg.confirm_yes': '✓ Подтвердить',
            'dlg.confirm_yes_danger': '⚠ Да, выполнить',
            'dlg.cancel': 'Отмена',
            'dlg.notice': 'Уведомление',
            'dlg.ok': 'OK',
            'dlg.input': 'Ввод',
            'dlg.select': 'Выбор',
            'dlg.select_btn': '✓ Выбрать',

            // ── Settings buttons ──
            'settings.save': '✓ Сохранить',
            'settings.close': 'Закрыть',
        },
    };

    /* ═══════════════════════════════════════════════════════
     *  PUBLIC API
     * ═══════════════════════════════════════════════════════ */

    /** Get translation for a key. Falls back to English, then to key itself. */
    function t(key, params) {
        let str = (T[_lang] && T[_lang][key]) || T.en[key] || key;
        if (params) {
            Object.keys(params).forEach(k => {
                str = str.replace(new RegExp('\\{' + k + '\\}', 'g'), params[k]);
            });
        }
        return str;
    }

    function getLang() { return _lang; }

    function setLang(lang) {
        if (lang !== 'en' && lang !== 'ru') return;
        _lang = lang;
        try { localStorage.setItem('kshu_language', lang); } catch (e) {}
        applyAll();
    }

    function init() {
        try { _lang = localStorage.getItem('kshu_language') || 'en'; } catch (e) {}
        if (_lang !== 'en' && _lang !== 'ru') _lang = 'en';
        applyAll();
    }

    /** Re-apply all translations to the DOM. */
    function applyAll() {
        // ── data-i18n attributes ──
        document.querySelectorAll('[data-i18n]').forEach(el => {
            el.textContent = t(el.dataset.i18n);
        });
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            el.title = t(el.dataset.i18nTitle);
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            el.placeholder = t(el.dataset.i18nPlaceholder);
        });
        document.querySelectorAll('[data-i18n-html]').forEach(el => {
            el.innerHTML = t(el.dataset.i18nHtml);
        });

        // ── Toggle Rules modal language ──
        const rulesEn = document.getElementById('rules-content-en');
        const rulesRu = document.getElementById('rules-content-ru');
        if (rulesEn) rulesEn.style.display = _lang === 'en' ? '' : 'none';
        if (rulesRu) rulesRu.style.display = _lang === 'ru' ? '' : 'none';

        // ── Update language selector if open ──
        const langSel = document.getElementById('setting-language');
        if (langSel) langSel.value = _lang;

        // ── Update html lang attribute ──
        document.documentElement.lang = _lang === 'ru' ? 'ru' : 'en';
    }

    return { init, t, getLang, setLang, applyAll };
})();

