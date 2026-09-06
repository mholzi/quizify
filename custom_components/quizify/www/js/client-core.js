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

    window.QuizifyClientCore = {
        SESSION_TOKEN_KEY: SESSION_TOKEN_KEY,
        SESSION_NAME_KEY: SESSION_NAME_KEY,
        saveSession: saveSession,
        getSession: getSession,
        clearSession: clearSession,
        socketUrl: socketUrl,
        backoffDelay: backoffDelay,
        createSocket: createSocket
    };
})();
