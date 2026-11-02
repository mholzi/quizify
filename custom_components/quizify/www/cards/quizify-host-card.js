/*
 * quizify-host-card — host controls as a native Lovelace card (#278).
 *
 * Two densities in one resource, per the locked design:
 *   mode: compact  (default, "Remote Control") — one morphing button
 *   mode: expanded ("Cockpit")                 — roster, QR, progress, all controls
 *
 * The card speaks the SAME WebSocket protocol as www/js/admin.js: connect to
 * /api/quizify/ws?role=admin, send `admin_auth` as the FIRST frame (never
 * ?token= in the URL, #359), then `admin_connect`, and render from the
 * `game_state` broadcast. No new server endpoint and no new message type.
 *
 * Quizify is served from hass.http.app.router, i.e. the SAME origin as the
 * Home Assistant frontend, so this card can read the very admin token the
 * admin page stored. It does that through QuizifyUtils.readAdminToken() rather
 * than touching localStorage directly — utils.js is the one place allowed to
 * name the storage key (see tests/test_admin_token_durable_storage.py).
 *
 * That sharing has a consequence worth stating plainly: the token only exists
 * once the admin page has run on this browser. On a tablet that never opened
 * /quizify/admin the card cannot authenticate, so it renders an explicit link
 * instead of dead buttons. #530 was exactly that failure mode — a silent empty
 * surface — and it cost a release candidate.
 */
(function () {
    'use strict';

    var TAG = 'quizify-host-card';
    if (window.customElements && window.customElements.get(TAG)) return;

    var UTILS_URL = '/quizify/static/js/utils.js';
    var QR_URL = '/quizify/static/js/vendor/qrcode.min.js';
    var ADMIN_URL = '/quizify/admin';
    var WS_PATH = '/api/quizify/ws?role=admin';

    var MAX_RECONNECT = 10;

    /*
     * Phase -> primary action. Phase strings are GamePhase in
     * game/phase_controller.py; every message here is one admin.js already
     * sends for the same phase. The card must not invent transitions.
     *
     * EVERY phase gets an entry, including the ones the host cannot act on
     * (#801). An absent phase used to fall through to a disabled button
     * reading 'Connecting…', so a host on a wall tablet watched a lie for the
     * whole wager window and the whole Hot Seat auction while the game was
     * running perfectly well. A phase with `msg: null` is the honest version
     * of the same button: still disabled, but it says what the room is doing.
     * tests/test_host_card_278.py asserts the map covers GamePhase, so the
     * next phase added to the game cannot silently reintroduce 'Connecting…'.
     *
     * What each `msg` costs on the server (server/websocket.py):
     *   admin_skip  — WAGER_ACTIVE closes the betting window and asks the
     *                 question; QUESTION_ACTIVE evaluates the round now.
     *                 Anywhere else it falls through to _handle_next_question.
     *   next_question — accepted in LOBBY (started), ANSWER_REVEAL,
     *                 LIGHTNING_RECAP and HOT_SEAT_REVEAL; refused with
     *                 ERR_INVALID_ACTION everywhere else.
     */
    var PHASE_ACTIONS = {
        LOBBY: { labelKey: 'start', msg: 'start_game' },
        // #656: the question has not been asked yet, so "skip" here means
        // stop waiting for bets — anyone who has not bet has no bet.
        WAGER_ACTIVE: { labelKey: 'closeBets', msg: 'admin_skip' },
        QUESTION_ACTIVE: { labelKey: 'skip', msg: 'admin_skip' },
        ANSWER_REVEAL: { labelKey: 'next', msg: 'next_question' },
        PAUSED: { labelKey: 'resume', msg: 'resume_game' },
        FINALE: { labelKey: 'again', msg: 'play_again' },
        // The lightning loop is server-driven and has no host step: admin_skip
        // reaches _handle_next_question, which refuses LIGHTNING with
        // ERR_INVALID_ACTION. Say so rather than offering a button that errors.
        LIGHTNING: { labelKey: 'lightningRunning', msg: null },
        LIGHTNING_RECAP: { labelKey: 'next', msg: 'next_question' },
        // #616: the auction and the seat question run themselves to a close;
        // only the reveal hands the game back to the host.
        HOT_SEAT_AUCTION: { labelKey: 'auctionRunning', msg: null },
        HOT_SEAT: { labelKey: 'seatAnswering', msg: null },
        HOT_SEAT_REVEAL: { labelKey: 'next', msg: 'next_question' }
    };

    /*
     * Phases in which the expanded card's extra 'Reveal' button (admin_skip)
     * actually does something. Outside these two the server answers it with
     * ERR_INVALID_ACTION, so the button is hidden rather than shown dead
     * (#801) — during the Hot Seat auction it was pure noise.
     */
    var SKIPPABLE_PHASES = { QUESTION_ACTIVE: true, WAGER_ACTIVE: true };

    /*
     * #1177: the card speaks the app's own words. It used to carry 34 labels
     * × 3 languages of its own, and they had drifted from the host page
     * (Kick / Rauswerfen / Expulsar for Remove, "Zur Auflösung" for Reveal,
     * "Der Hot Seat antwortet"). Now each label names its key in
     * /quizify/static/i18n/{lang}.json — the bundle the host page loads — and
     * the card fetches that file. FALLBACK is English and only covers the time
     * before the bundle arrives, or a dashboard that cannot reach it.
     */
    var I18N_URL = '/quizify/static/i18n/';
    var SUPPORTED_LANGS = { en: true, de: true, es: true };
    var KEYS = {
        start: 'admin.startGame', skip: 'admin.reveal', next: 'admin.nextQuestion',
        resume: 'admin.resume', again: 'admin.playAgainSame', closeBets: 'admin.closeBets',
        lightningRunning: 'hostCard.lightningRunning', auctionRunning: 'hostCard.auctionRunning',
        seatAnswering: 'hostCard.seatAnswering', reveal: 'admin.reveal', end: 'admin.endGame',
        reset: 'admin.resetGame', kick: 'admin.kickConfirmBtn',
        scoringOut: 'admin.scoringOut', scoringBack: 'admin.scoringBack',
        scoringSection: 'admin.scoringSection', scoringPodium: 'admin.scoringPodium',
        meta: 'hostCard.meta', join: 'admin.joinModalBtnJoin', waiting: 'lobby.marqueeWaiting',
        connecting: 'common.connecting', offline: 'connection.reconnecting',
        noToken: 'hostCard.noToken', openAdmin: 'launcher.openQuizify',
        resetConfirm: 'admin.resetGameConfirm'
    };
    var FALLBACK = {
        start: 'Start game', skip: 'Reveal', next: 'Next question', resume: 'Resume',
        again: 'Play again', closeBets: 'Close wagers',
        lightningRunning: 'Lightning round running', auctionRunning: 'Bidding for the hot seat',
        seatAnswering: 'Hot seat: {name} is answering', reveal: 'Reveal', end: 'End game',
        reset: 'Reset game', kick: 'Remove',
        scoringOut: 'Out of scoring', scoringBack: 'Bring back',
        scoringSection: 'Not in the scoring', scoringPodium: 'Otherwise on the podium',
        meta: 'Question {current} of {total} · {count, plural, one{1 player} other{# players}}',
        join: 'Join', waiting: 'Waiting for players…', connecting: 'Connecting to the game…',
        offline: 'Reconnecting…', noToken: 'Open Quizify once to link this dashboard',
        openAdmin: 'Open Quizify', resetConfirm: 'Reset game?'
    };
    var BUNDLES = {};   // lang -> parsed bundle, once fetched
    var LOADING = {};   // lang -> in-flight promise

    function loadBundle(lang) {
        if (BUNDLES[lang]) return Promise.resolve(BUNDLES[lang]);
        if (!LOADING[lang]) {
            LOADING[lang] = fetch(I18N_URL + lang + '.json')
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (json) {
                    if (json) BUNDLES[lang] = json;
                    return json;
                })
                .catch(function () { return null; })
                .then(function (json) { delete LOADING[lang]; return json; });
        }
        return LOADING[lang];
    }

    function lookup(bundle, key) {
        var node = bundle;
        var parts = key.split('.');
        for (var i = 0; i < parts.length; i++) {
            if (!node || typeof node !== 'object') return undefined;
            node = node[parts[i]];
        }
        return typeof node === 'string' ? node : undefined;
    }

    /*
     * The bundles' template syntax, reduced to what the card's labels use:
     * {param} and one level of {count, plural, one{…} other{…}} with # for
     * the count. i18n.js is not loaded here — it would claim window.t on the
     * Home Assistant page.
     */
    function fmt(template, params, lang) {
        var text = String(template);
        params = params || {};
        text = text.replace(/\{(\w+), plural,((?:[^{}]|\{[^{}]*\})*)\}/g, function (_m, name, body) {
            var count = Number(params[name]);
            var branches = {};
            body.replace(/(=?\w+)\{([^{}]*)\}/g, function (_b, sel, val) { branches[sel] = val; return ''; });
            var cat = 'other';
            try { cat = new Intl.PluralRules(lang).select(count); } catch (e) { cat = count === 1 ? 'one' : 'other'; }
            var chosen = branches['=' + count] || branches[cat] || branches.other || '';
            return chosen.replace(/#/g, String(count));
        });
        return text.replace(/\{(\w+)\}/g, function (m, name) {
            return params[name] != null ? String(params[name]) : m;
        });
    }

    var STYLE = [
        ':host{display:block}',
        'ha-card{padding:16px;display:flex;flex-direction:column;gap:12px}',
        '.head{display:flex;align-items:baseline;justify-content:space-between;gap:8px}',
        '.head .name{font-weight:600}',
        '.head .meta{color:var(--secondary-text-color);font-size:.85em}',
        '.primary{width:100%;min-height:48px;border:0;border-radius:10px;cursor:pointer;',
        'font:inherit;font-weight:600;font-size:1.05em;padding:12px 16px;',
        'background:var(--primary-color);color:var(--text-primary-color)}',
        '.primary[disabled]{opacity:.5;cursor:default}',
        '.row{display:flex;gap:8px;flex-wrap:wrap}',
        '.row button{flex:1 1 auto;min-height:48px;min-width:88px;border-radius:10px;cursor:pointer;',
        'font:inherit;padding:10px 12px;background:transparent;',
        'border:1px solid var(--divider-color);color:var(--primary-text-color)}',
        '.row button[disabled]{opacity:.5;cursor:default}',
        '.foot{display:flex;justify-content:space-between;align-items:center;gap:8px;',
        'color:var(--secondary-text-color);font-size:.85em}',
        '.foot a{color:var(--primary-color)}',
        '.foot .note{flex:1;text-align:center}',
        // The compact card\'s only secondary action. Deliberately text-weight:
        // ending a game is rare next to the primary button, and a second solid
        // button would compete with it.
        '.linky{border:0;background:none;padding:4px 0;cursor:pointer;font:inherit;',
        'color:var(--secondary-text-color);text-decoration:underline}',
        '.linky:hover{color:var(--primary-text-color)}',
        '.split{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}',
        '.roster{flex:1 1 200px;display:flex;flex-direction:column;gap:2px;margin:0;padding:0;list-style:none}',
        '.roster li{display:flex;align-items:center;gap:8px;padding:6px 0;',
        'border-bottom:1px solid var(--divider-color)}',
        '.roster li:last-child{border-bottom:0}',
        '.roster .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
        '.roster .sc{font-variant-numeric:tabular-nums;color:var(--secondary-text-color)}',
        '.dot{width:8px;height:8px;border-radius:50%;flex:none;background:var(--success-color,#4caf50)}',
        '.dot.off{background:var(--disabled-text-color,#9e9e9e)}',
        '.roster li.wrap{flex-wrap:wrap}',
        '.roster .hint{flex-basis:100%;color:var(--secondary-text-color);font-size:.9em}',
        '.roster .section{padding:8px 0 2px;color:var(--secondary-text-color);font-size:.8em;',
        'letter-spacing:.08em;text-transform:uppercase;border-bottom:0}',
        '.scoring{min-height:44px;padding:0 12px;border:1px solid var(--divider-color);border-radius:10px;',
        'background:transparent;color:var(--primary-text-color);font:inherit;font-weight:600;cursor:pointer;white-space:nowrap}',
        '.kick{border:0;background:transparent;cursor:pointer;color:var(--secondary-text-color);',
        'font:inherit;min-height:32px;padding:0 6px}',
        '.qr{flex:0 0 auto}',
        '.qr img,.qr canvas{display:block;border-radius:6px}',
        '.bar{height:4px;border-radius:999px;background:var(--divider-color);overflow:hidden}',
        '.bar > i{display:block;height:100%;background:var(--primary-color)}',
        '.note{color:var(--secondary-text-color);font-size:.9em;margin:0}',
        '@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}'
    ].join('');

    function loadScript(url) {
        return new Promise(function (resolve, reject) {
            var existing = document.querySelector('script[data-quizify-src="' + url + '"]');
            if (existing) {
                if (existing.dataset.loaded === '1') return resolve();
                existing.addEventListener('load', function () { resolve(); });
                existing.addEventListener('error', reject);
                return;
            }
            var s = document.createElement('script');
            s.src = url;
            s.dataset.quizifySrc = url;
            s.addEventListener('load', function () { s.dataset.loaded = '1'; resolve(); });
            s.addEventListener('error', reject);
            document.head.appendChild(s);
        });
    }

    function esc(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    class QuizifyHostCard extends HTMLElement {
        constructor() {
            super();
            this.attachShadow({ mode: 'open' });
            this._config = { mode: 'compact' };
            this._state = null;
            this._ws = null;
            this._status = 'connecting';
            this._token = undefined;   // undefined = not looked up yet, null = none
            this._attempts = 0;
            this._retry = null;
            this._lang = 'en';
            this._qrDrawnFor = null;
        }

        // ---- Lovelace contract -------------------------------------------

        setConfig(config) {
            var mode = (config && config.mode) || 'compact';
            if (mode !== 'compact' && mode !== 'expanded') {
                throw new Error("quizify-host-card: mode must be 'compact' or 'expanded'");
            }
            this._config = {
                mode: mode,
                title: (config && config.title) || null
            };
            this._render();
        }

        getCardSize() {
            return this._config.mode === 'expanded' ? 5 : 2;
        }

        static getStubConfig() {
            return { type: 'custom:quizify-host-card', mode: 'compact' };
        }

        set hass(hass) {
            // The game state comes from Quizify's own socket, which is the
            // authority on the game. hass is kept for exactly two things: the
            // display language, and calling the quizify.start_game SERVICE
            // (#744) — see _startGame.
            this._hass = hass;
            var lang = (hass && (hass.language || (hass.locale && hass.locale.language))) || 'en';
            lang = String(lang).slice(0, 2);
            if (SUPPORTED_LANGS[lang] && lang !== this._lang) {
                this._lang = lang;
                this._render();
                this._loadLabels();
            }
        }

        connectedCallback() {
            // Lovelace detaches and re-attaches cards on view switches and
            // reflows; a re-attached card gets its full retry budget back.
            this._attempts = 0;
            this._loadLabels();
            this._connect();
        }

        disconnectedCallback() {
            if (this._retry) { clearTimeout(this._retry); this._retry = null; }
            this._attempts = MAX_RECONNECT;   // stop reconnecting once removed
            var ws = this._ws;
            this._ws = null;
            if (ws) {
                // Detach the handlers first (#1090): the close event fires
                // later, possibly after a re-attach opened a new socket, and
                // must not null that one.
                ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
                try { ws.close(); } catch (e) { /* already gone */ }
            }
        }

        // ---- Wiring ------------------------------------------------------

        // The labels in the card's language: the app bundle's words where it
        // has arrived, the English FALLBACK where it has not (#1177).
        get _t() {
            var bundle = BUNDLES[this._lang] || null;
            var out = {};
            Object.keys(KEYS).forEach(function (k) {
                var v = bundle ? lookup(bundle, KEYS[k]) : undefined;
                out[k] = v !== undefined ? v : FALLBACK[k];
            });
            return out;
        }

        _loadLabels() {
            var self = this;
            var lang = this._lang;
            if (BUNDLES[lang]) return;
            loadBundle(lang).then(function (bundle) {
                if (bundle && lang === self._lang) self._render();
            });
        }

        _connect() {
            var self = this;
            var ensureUtils = window.QuizifyUtils
                ? Promise.resolve()
                : loadScript(UTILS_URL).catch(function () { /* fall through to no-token */ });

            ensureUtils.then(function () {
                self._token = (window.QuizifyUtils && window.QuizifyUtils.readAdminToken())
                    ? window.QuizifyUtils.readAdminToken()
                    : null;

                if (!self._token) {
                    // No credential on this browser yet. Say so — never render a
                    // control surface that silently does nothing (#530).
                    self._status = 'no-token';
                    self._render();
                    return;
                }
                self._open();
            });
        }

        _open() {
            var self = this;
            var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            var ws;
            try {
                ws = new WebSocket(proto + '//' + location.host + WS_PATH);
            } catch (e) {
                this._scheduleRetry();
                return;
            }
            this._ws = ws;

            ws.onopen = function () {
                if (self._ws !== ws) return;
                self._attempts = 0;
                if (!self._refused) self._status = 'online';
                // Token first, out of the URL (#359), then the connect frame.
                ws.send(JSON.stringify({ type: 'admin_auth', token: self._token }));
                ws.send(JSON.stringify({ type: 'admin_connect' }));
            };

            // Every handler ignores a socket that has been superseded (#1090):
            // a stale close would otherwise null the live socket and leave
            // the buttons rendering game_state but sending nothing.
            ws.onmessage = function (evt) {
                if (self._ws !== ws) return;
                var msg;
                try { msg = JSON.parse(evt.data); } catch (e) { return; }
                if (msg.admin_session_token && window.QuizifyUtils) {
                    window.QuizifyUtils.writeAdminToken(msg.admin_session_token);
                    self._token = msg.admin_session_token;
                    if (self._refused) { self._refused = false; self._status = 'online'; }
                }
                // The server refused this browser's token (#1155): "Admin only"
                // on the handshake, ADMIN_REQUIRED on a command. Same note as
                // having no token, never live buttons that fail silently. Sticky
                // until the server grants a token, since game_state is still
                // broadcast to a refused connection.
                if ((msg.type || msg.event) === 'error' && (msg.code === 'ADMIN_REQUIRED'
                        || (msg.code === 'INVALID_ACTION' && msg.message === 'Admin only'))) {
                    self._refused = true;
                    self._status = 'no-token';
                    self._state = null;
                    self._render();
                    return;
                }
                if (self._refused) return;
                if (msg.type === 'game_state') {
                    self._state = msg;
                    self._render();
                }
                if (msg.type === 'scoring_status') {
                    self._scoring = msg;
                    self._render();
                }
            };

            ws.onclose = function () {
                if (self._ws !== ws) return;
                self._ws = null;
                if (!self._refused) self._status = 'offline';
                self._render();
                self._scheduleRetry();
            };

            ws.onerror = function () { /* onclose does the reconnecting */ };
        }

        _scheduleRetry() {
            if (this._attempts >= MAX_RECONNECT) return;
            var self = this;
            var delay = Math.min(1000 * Math.pow(2, this._attempts), 30000);
            this._attempts += 1;
            this._retry = setTimeout(function () { self._open(); }, delay);
        }

        _send(type, data) {
            if (!this._ws || this._ws.readyState !== 1) return;
            var payload = data || {};
            payload.type = type;
            this._ws.send(JSON.stringify(payload));
        }

        /*
         * Starting goes through the quizify.start_game SERVICE, not the socket
         * (#744).
         *
         * The card has no setup screen, so its start_game frame carried no
         * settings at all — and the admin path reads an absent field and an
         * explicit null the same way, so the server could only answer it with
         * the factory game: mixed packs, medium, 10 rounds, German, narrator
         * off. The service is the surface that knows what "no settings given"
         * means: it replays the host's last game. Falls back to the socket when
         * hass is not available (the card can be rendered outside a Lovelace
         * view), so the button is never dead.
         */
        _startGame() {
            var hass = this._hass;
            if (hass && typeof hass.callService === 'function') {
                try {
                    hass.callService('quizify', 'start_game', {});
                    return;
                } catch (e) { /* fall through to the socket */ }
            }
            this._send('start_game');
        }

        // ---- Rendering ---------------------------------------------------

        /*
         * The one morphing button.
         *
         * Three outcomes, and the middle one is the point of #801:
         *   - a known phase with a message  -> live button
         *   - a known phase without one     -> disabled button naming the
         *     phase ("Bidding for the hot seat"), because the game is running
         *     and the host is simply not the one who advances it
         *   - genuinely no state yet        -> 'Connecting…', which is now
         *     only ever true when it is true
         */
        _primary() {
            var phase = this._state && this._state.phase;
            var action = PHASE_ACTIONS[phase] || null;
            var players = (this._state && this._state.players) || [];
            // An empty lobby has nothing to start; say why rather than letting
            // the host tap a button that refuses.
            var blocked = phase === 'LOBBY' && players.length === 0;
            var msg = (action && action.msg) || null;
            var label = action ? this._t[action.labelKey] : this._t.connecting;
            if (phase === 'HOT_SEAT') {
                var seat = (this._state && this._state.hot_seat) || {};
                label = fmt(label, { name: seat.entrant || seat.winner || '…' }, this._lang);
            }
            return {
                label: label,
                msg: msg,
                disabled: !msg || blocked || this._status !== 'online',
                blockedReason: blocked ? this._t.waiting : null
            };
        }

        _render() {
            if (!this.shadowRoot) return;
            var t = this._t;
            var s = this._state;
            var expanded = this._config.mode === 'expanded';
            var title = this._config.title || t.title;

            if (this._status === 'no-token') {
                this.shadowRoot.innerHTML =
                    '<style>' + STYLE + '</style>' +
                    '<ha-card>' +
                    '<div class="head"><span class="name">' + esc(title) + '</span></div>' +
                    '<p class="note">' + esc(t.noToken) + '</p>' +
                    '<div class="foot"><a href="' + ADMIN_URL + '">' + esc(t.openAdmin) + '</a></div>' +
                    '</ha-card>';
                return;
            }

            var players = (s && s.players) || [];
            var primary = this._primary();
            var meta = s
                ? fmt(t.meta, {
                    current: s.round || 0,
                    total: s.total_rounds || 0,
                    count: players.length
                }, this._lang)
                : t.connecting;

            var html = '<style>' + STYLE + '</style><ha-card>';
            html += '<div class="head"><span class="name">' + esc(title) + '</span>' +
                    '<span class="meta">' + esc(meta) + '</span></div>';

            if (expanded) {
                html += '<div class="split"><ul class="roster">';
                if (players.length === 0) {
                    html += '<li><span class="nm">' + esc(t.waiting) + '</span></li>';
                }
                // Kicking is lobby-only on the server (#1150), and an × that
                // can only be refused is a lie. During a game the card lists
                // the ranked rows, so a team has one button, as on the host page.
                html += s && s.phase !== 'LOBBY'
                    ? this._gameRosterHtml(players)
                    : this._lobbyRosterHtml(players);
                html += '</ul><div class="qr" id="qr"></div></div>';

                var total = (s && s.total_rounds) || 0;
                var done = (s && s.round) || 0;
                var pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
                html += '<div class="bar"><i style="width:' + pct + '%"></i></div>';
            }

            html += '<button class="primary" id="primary"' + (primary.disabled ? ' disabled' : '') + '>' +
                    esc(primary.label) + '</button>';

            if (expanded) {
                html += '<div class="row">';
                // Reveal is admin_skip: it cuts the timer short and shows the
                // answer, which is what admin.js does behind the same label.
                // Only two phases accept it (#801) — elsewhere the server
                // answers ERR_INVALID_ACTION, so the button is not offered.
                if (s && SKIPPABLE_PHASES[s.phase]) {
                    html += '<button data-msg="admin_skip">' + esc(t.reveal) + '</button>';
                }
                html += '<button data-msg="end_game">' + esc(t.end) + '</button>' +
                    '<button data-reset="1">' + esc(t.reset) + '</button>' +
                    '</div>';
            }

            var joinUrl = (s && s.join_url) || '/quizify/player';
            var footNote = primary.blockedReason
                || (this._status === 'offline' ? t.offline : '');
            html += '<div class="foot"><a href="' + esc(joinUrl) + '">' + esc(t.join) + '</a>' +
                    '<span class="note">' + esc(footNote) + '</span>';
            // End game lives in the compact footer (#278 spec). Expanded already
            // carries it in the control row, and there is nothing to end while
            // the lobby is still open — so it appears only where it can act.
            if (!expanded && s && s.phase && s.phase !== 'LOBBY') {
                html += '<button class="linky" data-msg="end_game">' +
                        esc(t.end) + '</button>';
            }
            html += '</div>';

            html += '</ha-card>';
            this.shadowRoot.innerHTML = html;
            this._bind(primary);
            if (expanded) this._drawQr(joinUrl);
        }

        _lobbyRosterHtml(players) {
            var t = this._t;
            return players.map(function (p) {
                return '<li>' +
                    '<span class="dot' + (p.connected ? '' : ' off') + '"></span>' +
                    '<span class="nm">' + esc(p.name) + '</span>' +
                    '<span class="sc">' + esc(p.score != null ? p.score : '—') + '</span>' +
                    '<button class="kick" data-kick="' + esc(p.name) + '" ' +
                    'title="' + esc(t.kick) + '" aria-label="' + esc(t.kick) + ' ' + esc(p.name) + '">×</button>' +
                    '</li>';
            }).join('');
        }

        _gameRosterHtml(players) {
            var t = this._t;
            var status = this._scoring || { out: [], back: [], flags: {} };
            var connected = {};
            players.forEach(function (p) { connected[p.name] = p.connected; });
            var out = {};
            status.out.forEach(function (r) { out[r.entrant_id] = true; });
            var board = this._state.leaderboard || players;
            var rows = board
                .filter(function (r) { return !out[r.entrant_id || r.name]; })
                .concat(status.back.filter(function (r) {
                    return !board.some(function (b) { return b.entrant_id === r.entrant_id; });
                }))
                .sort(function (a, b) { return b.score - a.score; });
            var html = rows.map(function (r) {
                var id = r.entrant_id || r.name;
                var flag = status.flags[id];
                var on = id in connected ? connected[id] : !(flag && flag.present === 0);
                return '<li class="wrap">' +
                    '<span class="dot' + (on ? '' : ' off') + '"></span>' +
                    '<span class="nm">' + esc(r.name) + '</span>' +
                    '<span class="sc">' + esc(r.score) + '</span>' +
                    (flag && flag.can_take_out
                        ? '<button class="scoring" data-scoring-out="' + esc(id) + '">' + esc(t.scoringOut) + '</button>'
                        : '') +
                    (flag && flag.on_podium ? '<span class="hint">' + esc(t.scoringPodium) + '</span>' : '') +
                    '</li>';
            }).join('');
            if (status.out.length) {
                html += '<li class="section">' + esc(t.scoringSection) + '</li>';
                html += status.out.map(function (r) {
                    return '<li>' +
                        '<span class="dot off"></span>' +
                        '<span class="nm">' + esc(r.name) + '</span>' +
                        '<span class="sc">' + esc(r.score) + '</span>' +
                        '<button class="scoring" data-scoring-back="' + esc(r.entrant_id) + '">' + esc(t.scoringBack) + '</button>' +
                        '</li>';
                }).join('');
            }
            return html;
        }

        // One place decides socket vs service, so every button reaches start_game
        // the same way no matter which control fired it (#744).
        _dispatch(msg) {
            if (msg === 'start_game') { this._startGame(); return; }
            this._send(msg);
        }

        _bind(primary) {
            var self = this;
            var t = this._t;
            var btn = this.shadowRoot.getElementById('primary');
            if (btn && primary.msg) {
                btn.addEventListener('click', function () { self._dispatch(primary.msg); });
            }
            var others = this.shadowRoot.querySelectorAll('[data-msg]');
            for (var i = 0; i < others.length; i++) {
                (function (el) {
                    el.addEventListener('click', function () { self._dispatch(el.dataset.msg); });
                })(others[i]);
            }
            var reset = this.shadowRoot.querySelector('[data-reset]');
            if (reset) {
                // The one destructive control — always ask.
                reset.addEventListener('click', function () {
                    if (window.confirm(t.resetConfirm)) self._send('reset_game');
                });
            }
            var scoring = this.shadowRoot.querySelectorAll('[data-scoring-out],[data-scoring-back]');
            for (var c = 0; c < scoring.length; c++) {
                (function (el) {
                    var takeOut = el.hasAttribute('data-scoring-out');
                    el.addEventListener('click', function () {
                        self._send('set_scoring', {
                            entrant_id: el.getAttribute(takeOut ? 'data-scoring-out' : 'data-scoring-back'),
                            out: takeOut
                        });
                    });
                })(scoring[c]);
            }
            var kicks = this.shadowRoot.querySelectorAll('[data-kick]');
            for (var k = 0; k < kicks.length; k++) {
                (function (el) {
                    el.addEventListener('click', function () {
                        self._send('kick_player', { name: el.dataset.kick });
                    });
                })(kicks[k]);
            }
        }

        _drawQr(joinUrl) {
            var box = this.shadowRoot.getElementById('qr');
            if (!box) return;
            var absolute = new URL(joinUrl, location.origin).href;
            var self = this;
            loadScript(QR_URL).then(function () {
                if (typeof QRCode === 'undefined') return;
                if (self._qrDrawnFor === absolute && box.firstChild) return;
                box.innerHTML = '';
                new QRCode(box, {
                    text: absolute, width: 96, height: 96,
                    correctLevel: QRCode.CorrectLevel.M
                });
                self._qrDrawnFor = absolute;
            }).catch(function () { /* no QR is survivable; the link stays */ });
        }
    }

    window.customElements.define(TAG, QuizifyHostCard);

    // Card picker entry, so the card is discoverable in the UI editor rather
    // than only via YAML.
    window.customCards = window.customCards || [];
    window.customCards.push({
        type: TAG,
        name: 'Quizify Host',
        description: 'Start, advance and end a Quizify game from a dashboard.',
        preview: false
    });
})();
