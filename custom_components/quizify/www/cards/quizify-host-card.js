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
     * Phase -> primary action. Phase strings are GamePhase in game/state.py;
     * every message here is one admin.js already sends for the same phase. The
     * card must not invent transitions — an unknown phase falls through to a
     * disabled button rather than guessing.
     */
    var PHASE_ACTIONS = {
        LOBBY: { labelKey: 'start', msg: 'start_game' },
        QUESTION_ACTIVE: { labelKey: 'skip', msg: 'admin_skip' },
        ANSWER_REVEAL: { labelKey: 'next', msg: 'next_question' },
        PAUSED: { labelKey: 'resume', msg: 'resume_game' },
        FINALE: { labelKey: 'again', msg: 'play_again' },
        LIGHTNING: { labelKey: 'skip', msg: 'admin_skip' },
        LIGHTNING_RECAP: { labelKey: 'next', msg: 'next_question' }
    };

    var LABELS = {
        en: {
            title: 'Quizify', start: 'Start game', skip: 'Skip to answer',
            next: 'Next question', resume: 'Resume', again: 'Play again',
            reveal: 'Reveal', end: 'End game', reset: 'Reset', kick: 'Kick',
            players: 'players', round: 'Round', join: 'Join',
            waiting: 'Waiting for players', connecting: 'Connecting…',
            offline: 'Connection lost — reconnecting',
            noToken: 'Open Quizify once to link this dashboard',
            openAdmin: 'Open Quizify', resetConfirm: 'Reset the game?'
        },
        de: {
            title: 'Quizify', start: 'Spiel starten', skip: 'Zur Auflösung',
            next: 'Nächste Frage', resume: 'Weiter', again: 'Nochmal spielen',
            reveal: 'Auflösen', end: 'Spiel beenden', reset: 'Zurücksetzen', kick: 'Rauswerfen',
            players: 'Spieler', round: 'Runde', join: 'Beitreten',
            waiting: 'Warte auf Spieler', connecting: 'Verbinde…',
            offline: 'Verbindung verloren — versuche erneut',
            noToken: 'Öffne Quizify einmal, um dieses Dashboard zu verbinden',
            openAdmin: 'Quizify öffnen', resetConfirm: 'Spiel zurücksetzen?'
        },
        es: {
            title: 'Quizify', start: 'Empezar partida', skip: 'Ir a la respuesta',
            next: 'Siguiente pregunta', resume: 'Continuar', again: 'Jugar otra vez',
            reveal: 'Revelar', end: 'Terminar partida', reset: 'Reiniciar', kick: 'Expulsar',
            players: 'jugadores', round: 'Ronda', join: 'Unirse',
            waiting: 'Esperando jugadores', connecting: 'Conectando…',
            offline: 'Conexión perdida — reintentando',
            noToken: 'Abre Quizify una vez para enlazar este panel',
            openAdmin: 'Abrir Quizify', resetConfirm: '¿Reiniciar la partida?'
        }
    };

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
        '.split{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}',
        '.roster{flex:1 1 200px;display:flex;flex-direction:column;gap:2px;margin:0;padding:0;list-style:none}',
        '.roster li{display:flex;align-items:center;gap:8px;padding:6px 0;',
        'border-bottom:1px solid var(--divider-color)}',
        '.roster li:last-child{border-bottom:0}',
        '.roster .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
        '.roster .sc{font-variant-numeric:tabular-nums;color:var(--secondary-text-color)}',
        '.dot{width:8px;height:8px;border-radius:50%;flex:none;background:var(--success-color,#4caf50)}',
        '.dot.off{background:var(--disabled-text-color,#9e9e9e)}',
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
            // Only the display language is taken from hass; the game state comes
            // from Quizify's own socket, which is the authority on the game.
            var lang = (hass && (hass.language || (hass.locale && hass.locale.language))) || 'en';
            lang = String(lang).slice(0, 2);
            if (LABELS[lang] && lang !== this._lang) {
                this._lang = lang;
                this._render();
            }
        }

        connectedCallback() {
            this._connect();
        }

        disconnectedCallback() {
            if (this._retry) { clearTimeout(this._retry); this._retry = null; }
            this._attempts = MAX_RECONNECT;   // stop reconnecting once removed
            if (this._ws) { try { this._ws.close(); } catch (e) { /* already gone */ } }
            this._ws = null;
        }

        // ---- Wiring ------------------------------------------------------

        get _t() { return LABELS[this._lang] || LABELS.en; }

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
                self._attempts = 0;
                self._status = 'online';
                // Token first, out of the URL (#359), then the connect frame.
                ws.send(JSON.stringify({ type: 'admin_auth', token: self._token }));
                ws.send(JSON.stringify({ type: 'admin_connect' }));
            };

            ws.onmessage = function (evt) {
                var msg;
                try { msg = JSON.parse(evt.data); } catch (e) { return; }
                if (msg.admin_session_token && window.QuizifyUtils) {
                    window.QuizifyUtils.writeAdminToken(msg.admin_session_token);
                    self._token = msg.admin_session_token;
                }
                if (msg.type === 'game_state') {
                    self._state = msg;
                    self._render();
                }
            };

            ws.onclose = function () {
                self._ws = null;
                self._status = 'offline';
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

        // ---- Rendering ---------------------------------------------------

        _primary() {
            var phase = this._state && this._state.phase;
            var action = PHASE_ACTIONS[phase] || null;
            var players = (this._state && this._state.players) || [];
            // An empty lobby has nothing to start; say why rather than letting
            // the host tap a button that refuses.
            var blocked = phase === 'LOBBY' && players.length === 0;
            return {
                label: action ? this._t[action.labelKey] : this._t.connecting,
                msg: action ? action.msg : null,
                disabled: !action || blocked || this._status !== 'online',
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
                ? t.round + ' ' + (s.round || 0) + '/' + (s.total_rounds || 0) +
                  ' · ' + players.length + ' ' + t.players
                : t.connecting;

            var html = '<style>' + STYLE + '</style><ha-card>';
            html += '<div class="head"><span class="name">' + esc(title) + '</span>' +
                    '<span class="meta">' + esc(meta) + '</span></div>';

            if (expanded) {
                html += '<div class="split"><ul class="roster">';
                if (players.length === 0) {
                    html += '<li><span class="nm">' + esc(t.waiting) + '</span></li>';
                }
                for (var i = 0; i < players.length; i++) {
                    var p = players[i];
                    html += '<li>' +
                        '<span class="dot' + (p.connected ? '' : ' off') + '"></span>' +
                        '<span class="nm">' + esc(p.name) + '</span>' +
                        '<span class="sc">' + esc(p.score != null ? p.score : '—') + '</span>' +
                        '<button class="kick" data-kick="' + esc(p.name) + '" ' +
                        'title="' + esc(t.kick) + '" aria-label="' + esc(t.kick) + ' ' + esc(p.name) + '">×</button>' +
                        '</li>';
                }
                html += '</ul><div class="qr" id="qr"></div></div>';

                var total = (s && s.total_rounds) || 0;
                var done = (s && s.round) || 0;
                var pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
                html += '<div class="bar"><i style="width:' + pct + '%"></i></div>';
            }

            html += '<button class="primary" id="primary"' + (primary.disabled ? ' disabled' : '') + '>' +
                    esc(primary.label) + '</button>';

            if (expanded) {
                html += '<div class="row">' +
                    // Reveal is admin_skip: it cuts the timer short and shows the
                    // answer, which is what admin.js does behind the same label.
                    '<button data-msg="admin_skip">' + esc(t.reveal) + '</button>' +
                    '<button data-msg="end_game">' + esc(t.end) + '</button>' +
                    '<button data-reset="1">' + esc(t.reset) + '</button>' +
                    '</div>';
            }

            var joinUrl = (s && s.join_url) || '/quizify/player';
            var footNote = primary.blockedReason
                || (this._status === 'offline' ? t.offline : '');
            html += '<div class="foot"><a href="' + esc(joinUrl) + '">' + esc(t.join) + '</a>' +
                    '<span>' + esc(footNote) + '</span></div>';

            html += '</ha-card>';
            this.shadowRoot.innerHTML = html;
            this._bind(primary);
            if (expanded) this._drawQr(joinUrl);
        }

        _bind(primary) {
            var self = this;
            var t = this._t;
            var btn = this.shadowRoot.getElementById('primary');
            if (btn && primary.msg) {
                btn.addEventListener('click', function () { self._send(primary.msg); });
            }
            var others = this.shadowRoot.querySelectorAll('[data-msg]');
            for (var i = 0; i < others.length; i++) {
                (function (el) {
                    el.addEventListener('click', function () { self._send(el.dataset.msg); });
                })(others[i]);
            }
            var reset = this.shadowRoot.querySelector('[data-reset]');
            if (reset) {
                // The one destructive control — always ask.
                reset.addEventListener('click', function () {
                    if (window.confirm(t.resetConfirm)) self._send('reset_game');
                });
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
