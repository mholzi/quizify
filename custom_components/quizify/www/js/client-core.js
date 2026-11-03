/**
 * Quizify — shared WebSocket client core (#787).
 *
 * The television (`dashboard.html`), the host page (`js/admin.js`) and the
 * phone (`js/player-*.js`) each used to open their own socket, build their own
 * URL, parse their own frames and invent their own retry curve. Four copies of
 * four rules is four places for one of them to drift, and the drift is not
 * loud: a socket that reconnects on a different schedule looks fine until an
 * HA restart, and a page that forgets to wrap its handler in the parse
 * `try` turns one bad frame into a dead screen.
 *
 * So the parts that are genuinely the same live here, and each surface passes
 * its own policy in. What is deliberately NOT here: the reconnect *decision*
 * and everything it paints. The television retries forever every two seconds
 * with a pill in the corner, the host counts attempts and offers a manual
 * retry, the phone shows an overlay and eventually a connection-lost view.
 * Those are three different products, not three copies of one, and folding
 * them together would be a behaviour change dressed as a refactor.
 *
 * Loaded from `common.bundle.js` on all three pages, before anything that
 * uses it.
 */

(function () {
    'use strict';

    // ============================================
    // Player session (token + name)
    // ============================================
    //
    // The phone writes these on join and reads them on reload. The host page,
    // when it joins as a player, writes its seat (HOST_SEAT_KEYS, #1212) so
    // that the redirect to /quizify/player resumes the session instead of
    // racing a fresh join against its own still-open admin socket. Before
    // this module the host spelled the key names out as string literals of
    // its own — renaming one on the phone would have broken the host with
    // nothing to catch it.

    var PLAYER_KEYS = {
        token: 'quizify_session_token',
        name: 'quizify_player_name',
        saved: 'quizify_session_saved_at',
        // #1193: the game this seat last sat in ('' = the lobby before any
        // game started). A reload into the same game rejoins silently; a
        // stored seat whose game is over asks first. Absent on a session
        // saved before the key existed, which keeps the old silent
        // behaviour.
        game: 'quizify_session_game'
    };

    // #1212: the host's own seat ("Join as player" on /quizify/admin, then
    // the redirect to /quizify/player?admin=true) lives under keys of its
    // own, in sessionStorage only. It used to be written into PLAYER_KEYS in
    // localStorage, which every tab of the browser shares: a /quizify/player
    // tab opened later on the host's phone silently reconnected as the host
    // (same running game, so no #1193 prompt). sessionStorage belongs to the
    // tab and survives its reload and the same-tab redirect, which is all
    // the host seat needs. A player page never reads these keys; only a page
    // that called useHostSeat() does, and only from its own tab.
    var HOST_SEAT_KEYS = {
        token: 'quizify_host_seat_token',
        name: 'quizify_host_seat_name',
        saved: 'quizify_host_seat_saved_at',
        game: 'quizify_host_seat_game'
    };

    // #1183: localStorage, not sessionStorage. A guest who came by QR camera
    // has a tab without history; Android's back button closes it, and a
    // sessionStorage session died with the tab. The server will not hand a
    // disconnected name back after the lobby (player_registry, security rule
    // #7 — by token only, never by name), so the guest who scanned again
    // read "that name is taken", meant themselves, and started over at 0.
    // The token now outlives the tab. It still goes on every path that ends
    // the game for this phone (forgetIdentity: reset, kick, refused rejoin,
    // New game), and SESSION_MAX_AGE_MS keeps one from an earlier evening
    // from walking into a later game.
    var SESSION_MAX_AGE_MS = 12 * 60 * 60 * 1000;

    // #1212: set once by the host's player page (?admin=true); from then on
    // the four session functions below work on the host seat instead.
    var _hostSeat = false;

    function _durable() {
        try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch (e) { return null; }
    }

    function _tab() {
        try { return typeof sessionStorage !== 'undefined' ? sessionStorage : null; } catch (e) { return null; }
    }

    function _write(store, keys, token, name) {
        if (!store) return;
        try {
            // A different seat does not inherit the last one's game (#1193).
            if (store.getItem(keys.name) !== name) store.removeItem(keys.game);
            store.setItem(keys.token, token);
            store.setItem(keys.name, name);
            store.setItem(keys.saved, String(Date.now()));
        } catch (e) { /* storage unavailable */ }
    }

    function saveSession(token, name) {
        if (_hostSeat) { saveHostSeat(token, name); return; }
        _write(_durable() || _tab(), PLAYER_KEYS, token, name);
    }

    /** The host's seat, in this tab only (#1212). Never in localStorage. */
    function saveHostSeat(token, name) {
        _write(_tab(), HOST_SEAT_KEYS, token, name);
    }

    // #1193: written from every snapshot that carries `game_id` while this
    // phone holds a seat, so it follows the seat from the lobby into the
    // game and on into the next one after "Play again".
    function saveSessionGame(gameId) {
        var keys = _hostSeat ? HOST_SEAT_KEYS : PLAYER_KEYS;
        var store = _hostSeat ? _tab() : (_durable() || _tab());
        if (!store) return;
        try {
            if (!store.getItem(keys.token)) return;
            store.setItem(keys.game, gameId ? String(gameId) : '');
        } catch (e) { /* storage unavailable */ }
    }

    function _read(store, keys, checkAge) {
        var token = store.getItem(keys.token);
        var name = store.getItem(keys.name);
        if (!token || !name) return null;
        if (checkAge) {
            var savedAt = Number(store.getItem(keys.saved));
            if (!savedAt || Date.now() - savedAt > SESSION_MAX_AGE_MS) return null;
        }
        return { token: token, name: name, game: store.getItem(keys.game) };
    }

    function getSession() {
        try {
            if (_hostSeat) {
                // Only this tab's own host seat: never the shared player
                // session, which may be a guest's on this browser.
                var own = _tab();
                return (own && _read(own, HOST_SEAT_KEYS, true)) ||
                    { token: null, name: null, game: null };
            }
            var durable = _durable();
            var found = durable && _read(durable, PLAYER_KEYS, true);
            // A tab that was open across the update still holds its session
            // in sessionStorage; it may finish the evening on it.
            var tab = _tab();
            if (!found && tab && tab !== durable) found = _read(tab, PLAYER_KEYS, false);
            return found || { token: null, name: null, game: null };
        } catch (e) {
            return { token: null, name: null, game: null };
        }
    }

    function _clear(store, keys) {
        if (!store) return;
        try {
            store.removeItem(keys.token);
            store.removeItem(keys.name);
            store.removeItem(keys.saved);
            store.removeItem(keys.game);
        } catch (e) { /* storage unavailable */ }
    }

    function clearSession() {
        // The host seat page leaves the shared player session alone: it may
        // be a guest's on the same browser.
        if (_hostSeat) { _clear(_tab(), HOST_SEAT_KEYS); return; }
        [_durable(), _tab()].forEach(function (store) { _clear(store, PLAYER_KEYS); });
    }

    /** The host's player page (?admin=true) works on the host seat (#1212). */
    function useHostSeat() {
        _hostSeat = true;
    }

    // ============================================
    // Socket
    // ============================================

    /** ws:// under http, wss:// under https — same host, given path. */
    function socketUrl(path) {
        var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        return proto + '//' + location.host + path;
    }

    /**
     * The retry curve the host page and the phone both use: 1s, 2s, 4s, 8s,
     * 16s, then the cap, which keeps a client trying across the full 1-5
     * minutes of a Home Assistant restart (#290). `attempt` is the number of
     * failures SO FAR, so the first retry is one second.
     */
    function backoffDelay(attempt, capMs) {
        return Math.min(1000 * Math.pow(2, attempt), capMs);
    }

    /**
     * #1035: how long a live round may go without a single frame before the
     * socket is presumed dead. The Lightning Round ticks at 1 Hz, so five
     * seconds is five missed ticks: well clear of a slow network, well short
     * of the 30 s the server heartbeat needs to notice a dead peer.
     */
    var STALL_TIMEOUT_MS = 5000;
    var STALL_CHECK_MS = 1000;

    /**
     * Open a socket with the three rules every surface shares.
     *
     *   opts.onOpen(ws)     — after the socket opens.
     *   opts.onMessage(msg) — one parsed frame.
     *   opts.onClose()      — after close; the caller decides about retrying.
     *   opts.logPrefix      — tag for the bad-frame console line.
     *   opts.stallGuard()   — optional; true while the page expects a steady
     *                         stream of frames (see STALL_TIMEOUT_MS below).
     *
     * `onMessage` runs INSIDE the parse try/catch, which is how all three
     * surfaces already had it and is load-bearing: a renderer that throws on
     * one odd frame logs and the socket keeps delivering the next one, rather
     * than taking the page down mid-round.
     */
    function createSocket(path, opts) {
        opts = opts || {};
        var prefix = opts.logPrefix || '[Quizify]';
        var ws = new WebSocket(socketUrl(path));
        var lastFrameAt = Date.now();
        var stallTimer = null;

        function stopStallWatch() {
            if (stallTimer !== null) {
                clearInterval(stallTimer);
                stallTimer = null;
            }
        }

        // #1035: the stall watchdog. The host page and the television froze
        // on a Lightning question while the phones, served by the same
        // broadcast, carried on to the recap. Neither page had any liveness
        // check: they only react to onclose/onerror, and a half-open socket
        // (or a tab the browser froze and thawed) fires neither. While the
        // caller says a steady stream is due, silence longer than the window
        // is treated as a dead socket: drop it and hand over to the page's
        // own onClose, i.e. the reconnect path it already has, whose
        // get_state restores the right view.
        if (opts.stallGuard) {
            stallTimer = setInterval(function () {
                if (ws.readyState !== 1 /* OPEN */) return;
                if (!opts.stallGuard()) return;
                if (Date.now() - lastFrameAt < STALL_TIMEOUT_MS) return;
                stopStallWatch();
                if (window.console && console.warn) {
                    console.warn(prefix + ' No frame for ' + STALL_TIMEOUT_MS +
                        'ms during a live round, reconnecting (#1035)');
                }
                // Detach first: a half-open socket may take long to report
                // its close, and when it finally does it must not schedule a
                // second reconnect next to the one started here.
                ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
                try { ws.close(); } catch (e) { /* already gone */ }
                if (opts.onClose) opts.onClose();
            }, STALL_CHECK_MS);
        }

        ws.onopen = function () {
            lastFrameAt = Date.now();
            if (opts.onOpen) opts.onOpen(ws);
        };

        ws.onmessage = function (evt) {
            lastFrameAt = Date.now();
            try {
                var msg = JSON.parse(evt.data);
                if (opts.onMessage) opts.onMessage(msg);
            } catch (e) {
                if (window.console && console.error) {
                    console.error(prefix + ' Bad message:', e);
                }
            }
        };

        ws.onclose = function () {
            stopStallWatch();
            if (opts.onClose) opts.onClose();
        };

        // An error is always followed by a close in every browser that
        // matters, so closing here funnels both paths into onclose and the
        // retry policy lives in exactly one place per surface.
        ws.onerror = function () {
            if (ws) ws.close();
        };

        return ws;
    }

    // ============================================
    // Connection indicator
    // ============================================
    //
    // One dot, two surfaces (#983). The host page and the phone each carried
    // their own copy — same `#conn-status` id, same inline `cssText`, same
    // three hex values the stylesheet already had names for — and the copies
    // drifted apart in opposite directions: the phone got the accessibility
    // work from #424 (a shape next to the hue, and the polite live region),
    // the host got the manual retry from #290. Neither got the other's, so a
    // host could not hear a dropped connection and a guest could not force a
    // reconnect, and nobody had decided either.
    //
    // What the surface still owns is the retry *action*: the host resets its
    // own attempt counter and reopens its own socket, the phone resets its
    // own and goes back through the join screen. That comes in as
    // `opts.retryHandler`; leave it out and the dot is read-only, which is
    // what a phone the host has kicked wants (there is nothing to retry).

    var CONN_TONES = {
        // #1182 L9: the first connection of a page. Sky, not sun: nothing has
        // been lost, so the page must not say "re-" anything yet.
        connecting: {
            color: 'var(--color-accent-blue)',
            glow: 'var(--color-text-muted-glow)',
            glyph: '\u2026',
            label: 'states.connecting'
        },
        connected: {
            color: 'var(--color-success)',
            glow: 'var(--color-success-glow)'
        },
        reconnecting: {
            color: 'var(--color-warning)',
            glow: 'var(--color-warning-glow)',
            // Not hue alone (#424): a shape a color-blind guest can read.
            glyph: '\u2026',
            label: 'connection.reconnecting'
        },
        disconnected: {
            color: 'var(--color-error)',
            glow: 'var(--color-error-glow)',
            glyph: '\u2298',
            label: 'connection.disconnected',
            // With a handler the label doubles as the affordance (#290).
            retryLabel: 'connection.retryConnection'
        }
    };

    var CONN_TONE_UNKNOWN = {
        color: 'var(--color-text-muted)',
        glow: 'var(--color-text-muted-glow)'
    };

    function connIndicatorEl() {
        var el = document.getElementById('conn-status');
        if (el) return el;
        // The host page ships the element in its markup (inside the header,
        // placed by `.connection-indicator`); the phone does not, so the
        // fallback keeps the corner it has always had. An element that is
        // already on the page is never re-styled here.
        el = document.createElement('div');
        el.id = 'conn-status';
        el.style.cssText = 'position:fixed;bottom:12px;right:12px;display:flex;' +
            'align-items:center;gap:6px;font-size:var(--fs-1);' +
            'color:var(--color-text-muted);z-index:100;';
        document.body.appendChild(el);
        return el;
    }

    /**
     * Announce the state to assistive tech via the polite live region (#424).
     *
     * #783: the line is written from JS, so it has to carry the key it was
     * written from — a language switch re-renders every visible label off
     * `data-i18n` and used to walk straight past this one. It is `.sr-only`,
     * so the mismatch reached nobody except the people who depend on it: a
     * screen-reader user in an English game heard "Verbunden".
     */
    function announceConnection(status, t) {
        var announce = document.getElementById('conn-status-announce');
        if (!announce) return;
        var tone = CONN_TONES[status];
        var key = (tone && tone.label) || 'connection.' + status;
        var msg = t(key);
        var translated = !!msg && msg !== key;
        // #1034: a known status keeps its key even when t() has no word for
        // it yet. The socket can open before the bundle has loaded; the
        // raw status stands in until the sweep that follows i18n init
        // re-renders the line off `data-i18n`.
        if (translated || Object.prototype.hasOwnProperty.call(CONN_TONES, status)) {
            announce.setAttribute('data-i18n', key);
        } else {
            // An unknown status has no key to re-render from; leaving a stale
            // one would make the next sweep announce the wrong state.
            announce.removeAttribute('data-i18n');
        }
        announce.textContent = translated ? msg : status;
    }

    /**
     * Paint the connection dot.
     *
     *   status             'connected' | 'reconnecting' | 'disconnected'
     *   opts.retryHandler  optional: makes the disconnected dot tappable.
     */
    function updateConnectionIndicator(status, opts) {
        opts = opts || {};
        var el = connIndicatorEl();
        var tone = CONN_TONES[status] || CONN_TONE_UNKNOWN;
        var t = (window.QuizifyI18n && window.QuizifyI18n.t) ||
            function (k) { return k; };

        var retry = (status === 'disconnected' && typeof opts.retryHandler === 'function')
            ? opts.retryHandler : null;

        var html = '<span style="width:10px;height:10px;border-radius:50%;' +
            'display:inline-block;flex:none;background:' + tone.color +
            ';box-shadow:0 0 10px ' + tone.glow + ';"></span>';
        if (tone.glyph) {
            html += '<span aria-hidden="true" style="font-size:var(--fs-2);' +
                'line-height:1;color:' + tone.color + ';">' + tone.glyph + '</span>';
        }
        var labelKey = retry ? tone.retryLabel : tone.label;
        if (labelKey) {
            // A bare dot tells a host nothing about whether the tablet is
            // still trying (#290). The existing connection.* keys are reused.
            html += '<span data-i18n="' + labelKey + '">' + t(labelKey) + '</span>';
        }
        el.innerHTML = html;

        el.onclick = retry;
        el.style.cursor = retry ? 'pointer' : '';
        if (retry) {
            el.setAttribute('role', 'button');
        } else {
            // Removed, not left behind: a dot that says role="button" after
            // the socket is back is a control that does nothing.
            el.removeAttribute('role');
        }

        announceConnection(status, t);
    }

    // The key names and `socketUrl` stay private: saveSession /
    // getSession / clearSession (and saveHostSeat / useHostSeat for the
    // host's own seat, #1212) are the only way in, which is the whole point
    // of #787 — one owner for the spelling — and `createSocket` is the only
    // caller a page ever needs.
    window.QuizifyClientCore = {
        saveSession: saveSession,
        saveSessionGame: saveSessionGame,
        getSession: getSession,
        clearSession: clearSession,
        saveHostSeat: saveHostSeat,
        useHostSeat: useHostSeat,
        backoffDelay: backoffDelay,
        createSocket: createSocket,
        STALL_TIMEOUT_MS: STALL_TIMEOUT_MS,
        updateConnectionIndicator: updateConnectionIndicator
    };
})();
