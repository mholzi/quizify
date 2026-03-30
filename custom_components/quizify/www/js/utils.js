/**
 * Quizify — Shared utility module.
 */
window.QuizifyUtils = (function() {
    'use strict';

    /**
     * Escape HTML to prevent XSS.
     * @param {string} str
     * @returns {string}
     */
    function escapeHtml(str) {
        var el = document.createElement('span');
        el.textContent = str || '';
        return el.innerHTML;
    }

    /**
     * Create a WebSocket connection with auto-reconnect.
     * @param {string} path - WebSocket path (e.g., '/api/quizify/ws')
     * @param {Object} opts - { onOpen, onMessage, onClose, maxRetries, queryParams }
     * @returns {Object} - { send(type, payload), close(), getWs() }
     */
    function createWebSocket(path, opts) {
        opts = opts || {};
        var ws = null;
        var reconnectAttempts = 0;
        var maxRetries = opts.maxRetries || 5;
        var closed = false;

        function connect() {
            var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            var url = proto + '//' + location.host + path;
            if (opts.queryParams) {
                url += (url.indexOf('?') === -1 ? '?' : '&') + opts.queryParams;
            }
            ws = new WebSocket(url);

            ws.onopen = function() {
                reconnectAttempts = 0;
                if (opts.onOpen) opts.onOpen(ws);
            };

            ws.onmessage = function(evt) {
                try {
                    var msg = JSON.parse(evt.data);
                    if (opts.onMessage) opts.onMessage(msg);
                } catch (e) {
                    console.error('[QuizifyWS] Bad message:', e);
                }
            };

            ws.onclose = function() {
                ws = null;
                if (opts.onClose) opts.onClose();
                if (!closed && reconnectAttempts < maxRetries) {
                    reconnectAttempts++;
                    setTimeout(connect, 1000 * reconnectAttempts);
                }
            };

            ws.onerror = function() {
                if (ws) ws.close();
            };
        }

        function send(type, payload) {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify(Object.assign({ type: type }, payload || {})));
            }
        }

        function close() {
            closed = true;
            if (ws) ws.close();
        }

        function getWs() {
            return ws;
        }

        connect();

        return { send: send, close: close, getWs: getWs, reconnect: connect };
    }

    /**
     * Show one view, hide all others.
     * @param {Object} views - Map of view name to DOM element
     * @param {string} name - View to show
     */
    function showView(views, name) {
        Object.values(views).forEach(function(v) {
            if (v) v.classList.remove('active');
        });
        if (views[name]) views[name].classList.add('active');
    }

    /**
     * Format points with + prefix.
     * @param {number} n
     * @returns {string}
     */
    function formatPoints(n) {
        if (n > 0) return '+' + n;
        return '' + n;
    }

    /**
     * Format seconds as "0:15" style.
     * @param {number} seconds
     * @returns {string}
     */
    function formatTime(seconds) {
        var m = Math.floor(seconds / 60);
        var s = Math.ceil(seconds % 60);
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    /**
     * Get initials from a name (for avatar placeholders).
     * @param {string} name
     * @returns {string}
     */
    function getInitials(name) {
        if (!name) return '?';
        var parts = name.trim().split(/\s+/);
        if (parts.length >= 2) {
            return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
        }
        return name.substring(0, 2).toUpperCase();
    }

    return {
        escapeHtml: escapeHtml,
        createWebSocket: createWebSocket,
        showView: showView,
        formatPoints: formatPoints,
        formatTime: formatTime,
        getInitials: getInitials
    };
})();
