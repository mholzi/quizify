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
    // The phone writes these on join and reads them on reload; the host page
    // writes the same two keys when it joins as a player, so that the redirect
    // to /quizify/player resumes the session instead of racing a fresh join
    // against its own still-open admin socket. Before this module the host
    // spelled the key names out as string literals of its own — renaming one
    // on the phone would have broken the host with nothing to catch it.

    var SESSION_TOKEN_KEY = 'quizify_session_token';
    var SESSION_NAME_KEY = 'quizify_player_name';

    function saveSession(token, name) {
        try {
            sessionStorage.setItem(SESSION_TOKEN_KEY, token);
            sessionStorage.setItem(SESSION_NAME_KEY, name);
        } catch (e) { /* storage unavailable */ }
    }

    function getSession() {
        try {
            return {
                token: sessionStorage.getItem(SESSION_TOKEN_KEY),
                name: sessionStorage.getItem(SESSION_NAME_KEY)
            };
        } catch (e) {
            return { token: null, name: null };
        }
    }

    function clearSession() {
        try {
            sessionStorage.removeItem(SESSION_TOKEN_KEY);
            sessionStorage.removeItem(SESSION_NAME_KEY);
        } catch (e) { /* storage unavailable */ }
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
     * Open a socket with the three rules every surface shares.
     *
     *   opts.onOpen(ws)     — after the socket opens.
     *   opts.onMessage(msg) — one parsed frame.
     *   opts.onClose()      — after close; the caller decides about retrying.
     *   opts.logPrefix      — tag for the bad-frame console line.
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

        ws.onopen = function () {
            if (opts.onOpen) opts.onOpen(ws);
        };

        ws.onmessage = function (evt) {
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
            'align-items:center;gap:6px;font-size:0.75rem;' +
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
        var key = 'connection.' + status;
        var msg = t(key);
        if (msg && msg !== key) {
            announce.setAttribute('data-i18n', key);
        } else {
            // An unknown status has no key to re-render from; leaving a stale
            // one would make the next sweep announce the wrong state.
            announce.removeAttribute('data-i18n');
            msg = status;
        }
        announce.textContent = msg;
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
            html += '<span aria-hidden="true" style="font-size:0.85rem;' +
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

    // The two key names and `socketUrl` stay private: saveSession /
    // getSession / clearSession are the only way in, which is the whole point
    // of #787 — one owner for the spelling — and `createSocket` is the only
    // caller a page ever needs.
    window.QuizifyClientCore = {
        saveSession: saveSession,
        getSession: getSession,
        clearSession: clearSession,
        backoffDelay: backoffDelay,
        createSocket: createSocket,
        updateConnectionIndicator: updateConnectionIndicator
    };
})();
