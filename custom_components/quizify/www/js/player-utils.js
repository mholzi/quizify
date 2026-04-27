/**
 * Quizify Player - Utility Module
 * Shared state, view management, DOM helpers, connection indicator, QR, toast, collapsibles
 */

(function () {
    'use strict';

    var utils = window.QuizifyUtils || {};

    // ============================================
    // Shared Mutable State (used by all player modules)
    // ============================================

    var state = {
        ws: null,
        playerName: null,
        playerId: null,
        sessionToken: null,
        isAdmin: false,
        currentView: null,
        currentPhase: 'LOBBY',
        reconnectAttempts: 0,
        isReconnecting: false,
        intentionalLeave: false,
        playerColor: '',  // assigned by server on join
    };

    // ============================================
    // Constants
    // ============================================

    var MAX_RECONNECT_ATTEMPTS = 10;
    var MAX_RECONNECT_DELAY_MS = 30000;
    var MAX_NAME_LENGTH = 20;
    var SESSION_STORAGE_TOKEN = 'quizify_session_token';
    var SESSION_STORAGE_NAME = 'quizify_player_name';

    // ============================================
    // HTML Escaping
    // ============================================

    function escapeHtml(text) {
        return utils.escapeHtml(text);
    }

    // ============================================
    // View Management
    // ============================================

    var viewIds = [
        'loading-view', 'not-found-view', 'ended-view', 'in-progress-view',
        'join-view', 'lobby-view', 'game-view', 'reveal-view',
        'paused-view', 'end-view', 'connection-lost-view'
    ];

    function showView(viewId) {
        for (var i = 0; i < viewIds.length; i++) {
            var el = document.getElementById(viewIds[i]);
            if (el) {
                if (viewIds[i] === viewId) {
                    el.classList.remove('hidden');
                    el.classList.add('active');
                } else {
                    el.classList.add('hidden');
                    el.classList.remove('active');
                }
            }
        }
        state.currentView = viewId;

        // Auto-focus name input on join view
        if (viewId === 'join-view') {
            setTimeout(function () {
                var nameInput = document.getElementById('name-input');
                if (nameInput) nameInput.focus();
            }, 100);
        }
    }

    // ============================================
    // Formatting
    // ============================================

    function formatPoints(n) {
        return utils.formatPoints(n);
    }

    function formatTime(seconds) {
        return utils.formatTime(seconds);
    }

    // ============================================
    // WebSocket Factory
    // ============================================

    function createWebSocket(path, handlers, query) {
        var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        var url = proto + '//' + location.host + path;
        if (query && typeof query === 'object') {
            var parts = [];
            for (var k in query) {
                if (Object.prototype.hasOwnProperty.call(query, k) && query[k] != null) {
                    parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(query[k]));
                }
            }
            if (parts.length) url += '?' + parts.join('&');
        }

        var ws = new WebSocket(url);

        ws.onopen = function () {
            state.reconnectAttempts = 0;
            state.isReconnecting = false;
            hideReconnectingOverlay();
            updateConnectionIndicator('connected');
            if (handlers.onOpen) handlers.onOpen(ws);
        };

        ws.onmessage = function (evt) {
            try {
                var msg = JSON.parse(evt.data);
                if (handlers.onMessage) handlers.onMessage(msg);
            } catch (e) {
                console.error('[Quizify] Bad message:', e);
            }
        };

        ws.onclose = function () {
            if (state.intentionalLeave) {
                state.intentionalLeave = false;
                return;
            }
            if (state.playerName && state.reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                state.isReconnecting = true;
                state.reconnectAttempts++;
                showReconnectingOverlay();
                updateConnectionIndicator('reconnecting');
                var delay = getReconnectDelay();
                console.log('[Quizify] WS closed. Reconnecting in ' + delay + 'ms (attempt ' + state.reconnectAttempts + ')');
                setTimeout(function () {
                    if (handlers.onReconnect) handlers.onReconnect();
                }, delay);
            } else if (state.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
                state.isReconnecting = false;
                hideReconnectingOverlay();
                updateConnectionIndicator('disconnected');
                showView('connection-lost-view');
            }
            if (handlers.onClose) handlers.onClose();
        };

        ws.onerror = function () {
            if (ws) ws.close();
        };

        return ws;
    }

    function getReconnectDelay() {
        return Math.min(1000 * Math.pow(2, state.reconnectAttempts), MAX_RECONNECT_DELAY_MS);
    }

    // ============================================
    // Connection Indicator
    // ============================================

    function updateConnectionIndicator(status) {
        var el = document.getElementById('conn-status');
        if (!el) {
            el = document.createElement('div');
            el.id = 'conn-status';
            el.style.cssText = 'position:fixed;bottom:12px;right:12px;display:flex;align-items:center;gap:6px;font-size:0.75rem;color:#a4a4b8;z-index:100;';
            document.body.appendChild(el);
        }
        var colors = { connected: '#00b894', reconnecting: '#ffa502', disconnected: '#ff4757' };
        // Dot only — no text label
        el.innerHTML = '<span style="width:10px;height:10px;border-radius:50%;display:inline-block;background:' +
            (colors[status] || '#636e8a') + ';box-shadow:0 0 6px ' + (colors[status] || '#636e8a') + ';"></span>';
    }

    // ============================================
    // Reconnecting Overlay
    // ============================================

    function showReconnectingOverlay() {
        var overlay = document.getElementById('reconnecting-overlay');
        if (overlay) overlay.classList.remove('hidden');
    }

    function hideReconnectingOverlay() {
        var overlay = document.getElementById('reconnecting-overlay');
        if (overlay) overlay.classList.add('hidden');
    }

    // ============================================
    // Leaderboard Rendering
    // ============================================

    function renderLeaderboard(containerId, players, myName) {
        var container = typeof containerId === 'string'
            ? document.getElementById(containerId)
            : containerId;
        if (!container) return;

        container.innerHTML = players
            .map(function (p, i) {
                var rank = p.rank || i + 1;
                var rankClass = rank <= 3 ? ' rank-' + rank : '';
                var youBadge = (myName && p.name === myName)
                    ? '<span class="you-badge">(you)</span>' : '';
                return '<div class="leaderboard-row">' +
                    '<span class="leaderboard-rank' + rankClass + '">' + rank + '</span>' +
                    '<span class="leaderboard-name">' + escapeHtml(p.name) + youBadge + '</span>' +
                    '<span class="leaderboard-score">' + p.score + '</span>' +
                    (p.streak > 1 ? '<span class="leaderboard-streak">' + p.streak + 'x</span>' : '') +
                    '</div>';
            })
            .join('');
    }

    // ============================================
    // Player Cards Rendering
    // ============================================

    function renderPlayerCards(containerId, players) {
        var container = typeof containerId === 'string'
            ? document.getElementById(containerId)
            : containerId;
        if (!container) return;

        var list = Array.isArray(players) ? players : Object.values(players);

        container.innerHTML = list
            .map(function (p) {
                var name = typeof p === 'string' ? p : (p.name || p);
                var isYou = name === state.playerName;
                var isDisconnected = p.connected === false;
                var color = (p.color) || '';
                var classes = 'player-card' +
                    (isYou ? ' player-card--you' : '') +
                    (isDisconnected ? ' player-card--disconnected' : '');
                var colorStyle = color ? ' style="--player-color:' + color + ';border-left:4px solid ' + color + ';"' : '';
                var awayBadge = isDisconnected ? '<span class="away-badge">(away)</span>' : '';
                var youBadge = isYou ? '<span class="you-badge">(you)</span>' : '';
                return '<div class="' + classes + '"' + colorStyle + ' data-player="' + escapeHtml(name) + '">' +
                    '<span class="player-color-dot" style="background:' + (color || '#888') + '"></span>' +
                    '<span class="player-name">' + escapeHtml(name) + youBadge + awayBadge + '</span>' +
                    '</div>';
            })
            .join('');
    }

    // ============================================
    // Collapsibles
    // ============================================

    function setupCollapsibles() {
        var headers = document.querySelectorAll('.section-header-collapsible');
        for (var i = 0; i < headers.length; i++) {
            (function (header) {
                // Skip if already wired
                if (header.dataset.collapsibleInit) return;
                header.dataset.collapsibleInit = '1';

                header.addEventListener('click', function () {
                    var section = header.closest('.section-collapsible');
                    if (!section) return;

                    var isCollapsed = section.classList.contains('collapsed');
                    if (isCollapsed) {
                        section.classList.remove('collapsed');
                        header.setAttribute('aria-expanded', 'true');
                    } else {
                        section.classList.add('collapsed');
                        header.setAttribute('aria-expanded', 'false');
                    }
                });
            })(headers[i]);
        }
    }

    // ============================================
    // QR Code Generation
    // ============================================

    function generateQR(containerId, url) {
        var container = typeof containerId === 'string'
            ? document.getElementById(containerId)
            : containerId;
        if (!container || !url) return;

        container.innerHTML = '';

        if (typeof QRCode !== 'undefined') {
            new QRCode(container, {
                text: url,
                width: 128,
                height: 128,
                colorDark: '#000000',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M
            });
        } else {
            container.innerHTML = '<p class="status-error">QR code library not loaded</p>';
        }
    }

    // ============================================
    // Toast Notification
    // ============================================

    function showToast(message, duration) {
        duration = duration || 3000;
        var toast = document.getElementById('error-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'error-toast';
            toast.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);' +
                'background:#ff4757;color:white;padding:10px 20px;border-radius:10px;' +
                'font-size:0.85rem;z-index:9999;opacity:0;transition:opacity 0.3s;pointer-events:none;';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.style.opacity = '1';
        setTimeout(function () { toast.style.opacity = '0'; }, duration);
    }

    // ============================================
    // Session Storage Helpers
    // ============================================

    function saveSession(token, name) {
        try {
            sessionStorage.setItem(SESSION_STORAGE_TOKEN, token);
            sessionStorage.setItem(SESSION_STORAGE_NAME, name);
        } catch (e) { /* storage unavailable */ }
    }

    function getSession() {
        try {
            return {
                token: sessionStorage.getItem(SESSION_STORAGE_TOKEN),
                name: sessionStorage.getItem(SESSION_STORAGE_NAME)
            };
        } catch (e) {
            return { token: null, name: null };
        }
    }

    function clearSession() {
        try {
            sessionStorage.removeItem(SESSION_STORAGE_TOKEN);
            sessionStorage.removeItem(SESSION_STORAGE_NAME);
        } catch (e) { /* storage unavailable */ }
    }

    // ============================================
    // Name Validation
    // ============================================

    function validateName(name) {
        var trimmed = (name || '').trim();
        if (!trimmed) {
            return { valid: false, error: 'Please enter a name' };
        }
        if (trimmed.length > MAX_NAME_LENGTH) {
            return { valid: false, error: 'Name too long (max ' + MAX_NAME_LENGTH + ' characters)' };
        }
        return { valid: true, name: trimmed };
    }

    // ============================================
    // Score Animation
    // ============================================

    function easeOutQuart(t) {
        return 1 - Math.pow(1 - t, 4);
    }

    /**
     * Animate a numeric value in a DOM element from start to end
     */
    function animateValue(element, start, end, duration) {
        if (!element) return;
        if (start === end) { element.textContent = end; return; }
        var startTime = null;
        function step(timestamp) {
            if (!startTime) startTime = timestamp;
            var progress = Math.min((timestamp - startTime) / duration, 1);
            var value = Math.round(start + (end - start) * easeOutQuart(progress));
            element.textContent = value;
            if (progress < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }

    /**
     * Show a floating +N pts popup above a target element
     */
    function showPointsPopup(targetElement, points, options) {
        if (!targetElement || !points) return;
        options = options || {};
        var popup = document.createElement('div');
        popup.className = 'points-popup' + (options.isStreak ? ' points-popup--streak' : '');
        popup.textContent = (points > 0 ? '+' : '') + points;
        document.body.appendChild(popup);

        var rect = targetElement.getBoundingClientRect();
        popup.style.cssText = [
            'position:fixed',
            'left:' + (rect.left + rect.width / 2) + 'px',
            'top:' + (rect.top - 10) + 'px',
            'transform:translateX(-50%)',
            'pointer-events:none',
            'z-index:9999',
        ].join(';');

        popup.addEventListener('animationend', function () {
            if (popup.parentNode) popup.parentNode.removeChild(popup);
        });
    }

    // ============================================
    // Export
    // ============================================

    window.QuizifyPlayerUtils = {
        state: state,
        escapeHtml: escapeHtml,
        showView: showView,
        formatPoints: formatPoints,
        formatTime: formatTime,
        createWebSocket: createWebSocket,
        updateConnectionIndicator: updateConnectionIndicator,
        renderLeaderboard: renderLeaderboard,
        renderPlayerCards: renderPlayerCards,
        setupCollapsibles: setupCollapsibles,
        generateQR: generateQR,
        showToast: showToast,
        saveSession: saveSession,
        getSession: getSession,
        clearSession: clearSession,
        validateName: validateName,
        MAX_RECONNECT_ATTEMPTS: MAX_RECONNECT_ATTEMPTS,
        animateValue: animateValue,
        showPointsPopup: showPointsPopup
    };

})();
