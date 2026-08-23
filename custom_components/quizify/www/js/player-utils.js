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
    // Name limit lives in utils.js (QuizifyUtils.MAX_NAME_LENGTH) so the
    // player flow and the admin modal share one rule. Mirror it locally
    // for any legacy reference; validateName() delegates to the shared one.
    var MAX_NAME_LENGTH = (window.QuizifyUtils && window.QuizifyUtils.MAX_NAME_LENGTH) || 20;
    var SESSION_STORAGE_TOKEN = 'quizify_session_token';
    var SESSION_STORAGE_NAME = 'quizify_player_name';

    // ============================================
    // HTML Escaping
    // ============================================

    // One place for the "translate or fall back to the key" shape this file
    // uses; three copies of the same line invite the fourth to be forgotten.
    function _tt(key) {
        var fn = window.QuizifyI18n && window.QuizifyI18n.t;
        return fn ? fn(key) : key;
    }

    function escapeHtml(text) {
        return utils.escapeHtml(text);
    }

    // ============================================
    // View Management
    // ============================================

    var viewIds = [
        'loading-view', 'not-found-view', 'ended-view', 'in-progress-view',
        'join-view', 'lobby-view', 'game-view', 'reveal-view',
        'paused-view', 'end-view', 'connection-lost-view',
        // Lightning Round (#42) views. These MUST be registered here or
        // showView() can never reveal them: a view is shown by ADDING the
        // 'active' class (`.view.active { display:flex }`), and showView only
        // touches IDs in this list. Without them, showView('lightning-view')
        // hides every other view but never un-hides the lightning one, so on
        // a reconnect into a live LIGHTNING round both #lightning-view and
        // #lightning-splash-view stay `class="view hidden"` → blank screen
        // (#239, cousin of #221's data-path fix).
        'lightning-splash-view', 'lightning-view', 'lightning-recap-view'
    ];

    function showView(viewId) {
        var revealedEl = null;
        for (var i = 0; i < viewIds.length; i++) {
            var el = document.getElementById(viewIds[i]);
            if (el) {
                if (viewIds[i] === viewId) {
                    el.classList.remove('hidden');
                    el.classList.add('active');
                    revealedEl = el;
                } else {
                    el.classList.add('hidden');
                    el.classList.remove('active');
                }
            }
        }
        state.currentView = viewId;

        // Round indicator next to the wordmark — only visible on the
        // reveal view. Text itself is populated by renderFinaleReveal.
        var headerRound = document.getElementById('player-header-round');
        if (headerRound) {
            if (viewId === 'reveal-view') headerRound.removeAttribute('hidden');
            else headerRound.setAttribute('hidden', '');
        }

        // Re-run i18n on the view we just revealed. The page-wide
        // initPageTranslations runs once on load, but buttons inside views
        // that start hidden (#next-round-btn in #reveal-view, etc.) skip
        // translation if our translator looks at offsetParent — and even
        // when it doesn't, swapping languages later won't reach them until
        // we re-translate. Cheap to do per view-show.
        if (revealedEl && window.QuizifyI18n && window.QuizifyI18n.isReady()) {
            window.QuizifyI18n.initPageTranslations(revealedEl);
        }

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

    function createWebSocket(path, handlers) {
        var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        var url = proto + '//' + location.host + path;

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
            el.style.cssText = 'position:fixed;bottom:12px;right:12px;display:flex;align-items:center;gap:6px;font-size:0.75rem;color:#6E6A5C;z-index:100;';
            document.body.appendChild(el);
        }
        // Soft Parlor palette: connected = sage, warning = sun, error = warm brick.
        var colors = { connected: '#7FA897', reconnecting: '#E8C47F', disconnected: '#D66A6A' };
        var glow = { connected: 'rgba(127,168,151,0.45)', reconnecting: 'rgba(232,196,127,0.45)', disconnected: 'rgba(214,106,106,0.45)' };
        var color = colors[status] || '#6E6A5C';
        var glowColor = glow[status] || 'rgba(110,106,92,0.25)';
        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
        // A bare colored dot is invisible to screen readers and ambiguous for
        // color-blind users (#424). Not-connected states get a shape/label,
        // not hue alone: reconnecting shows an "…" glyph, disconnected an
        // "offline" slash glyph next to the dot.
        var glyph = { reconnecting: '…', disconnected: '⊘' };
        var mark = glyph[status]
            ? '<span aria-hidden="true" style="font-size:0.85rem;line-height:1;color:' + color + ';">' + glyph[status] + '</span>'
            : '';
        el.innerHTML = '<span style="width:10px;height:10px;border-radius:50%;display:inline-block;background:' +
            color + ';box-shadow:0 0 10px ' + glowColor + ';"></span>' + mark;
        // Announce the state to assistive tech via the polite live region.
        var announce = document.getElementById('conn-status-announce');
        if (announce) {
            var msg = t('connection.' + status);
            if (!msg || msg === 'connection.' + status) msg = status;
            announce.textContent = msg;
        }
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
                // #625: the audit named only player-game.js, but the same
                // literal sits twice more in this file. `lobby.you` exists in
                // all three bundles.
                var youBadge = (myName && p.name === myName)
                    ? '<span class="you-badge">(' + _tt('lobby.you') + ')</span>'
                    : '';
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
                // `(away)` was hardcoded English too, and `lobby.away` was
                // already sitting there unused.
                var awayBadge = isDisconnected
                    ? '<span class="away-badge">(' + _tt('lobby.away') + ')</span>'
                    : '';
                var youBadge = isYou
                    ? '<span class="you-badge">(' + _tt('lobby.you') + ')</span>'
                    : '';
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

    // Paint the static UI emoji-icon spans with the shared Rounded Duotone
    // SVG set (#225 P4). Any element carrying both `.qz-icon` and a
    // `data-ui-icon="<name>"` gets its emoji glyph swapped for the matching
    // window.QuizifyIcons.uiIcon() <svg>. The existing layout class
    // (.section-icon / .control-icon / .btn-icon / .paused-icon / etc.) and
    // the qz-icon--<tint> class stay put — we only replace innerHTML. Safe to
    // call more than once; already-painted spans just get re-filled.
    function paintUiIcons(root) {
        var Icons = window.QuizifyIcons;
        if (!Icons || !Icons.uiIcon) return;
        var scope = root || document;
        var slots = scope.querySelectorAll('.qz-icon[data-ui-icon]');
        for (var i = 0; i < slots.length; i++) {
            var name = slots[i].getAttribute('data-ui-icon');
            var svg = Icons.uiIcon(name);
            if (svg) slots[i].innerHTML = svg;
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
    // Feedback Icons (#220 P3)
    // ============================================

    // Maps a reveal-feedback / toast i18n key to its SVG glyph + tint. The
    // emoji that used to live INSIDE the translated string (✅ Richtig, 🔥
    // {count}er-Serie!, …) is pulled out into a text-only string, and the
    // consuming JS renders the mapped glyph beside the text as a small
    // inline .qz-icon. Tints follow the approved meaning→tint pairing
    // (check=sage, cross/heartbreak=coral, bolt/flame/joker=sun, …).
    var FEEDBACK_ICON = {
        'reveal.correct':          { glyph: 'check',      tint: 'sage'  },
        'reveal.wrong':            { glyph: 'cross',      tint: 'coral' },
        'reveal.speedBonus':       { glyph: 'bolt',       tint: 'sun'   },
        'reveal.difficulty':       { glyph: 'target',     tint: 'sage'  },
        'reveal.streakBonus':      { glyph: 'flame',      tint: 'sun'   },
        'reveal.streakActive':     { glyph: 'flame',      tint: 'sun'   },
        'reveal.streakLost':       { glyph: 'heartbreak', tint: 'coral' },
        'game.stoleFromYou':       { glyph: 'steal',      tint: 'sky'   },
        'game.stoleFromOpponent':  { glyph: 'steal',      tint: 'sky'   },
        'game.frozen':             { glyph: 'freeze',     tint: 'sky'   },
        'game.opponentUsedJoker':  { glyph: 'joker',      tint: 'sun'   },
        'game.streakToast3':       { glyph: 'flame',      tint: 'sun'   },
        'game.streakToast5':       { glyph: 'flame',      tint: 'sun'   },
        'game.streakToast7':       { glyph: 'flame',      tint: 'sun'   },
        'powerups.stealHint':      { glyph: 'bulb',       tint: 'sun'   },
        'leaderboard.thanksEmoji': { glyph: 'party',      tint: 'coral' },
        'wager.bonusFromReaction': { glyph: 'party',      tint: 'coral' }
    };

    // Returns the inline <span class="qz-icon feedback-icon qz-icon--TINT">
    // <svg>…</svg></span> markup for a feedback glyph name, or '' if the
    // shared icon set / glyph is unavailable (so a missing glyph paints
    // nothing rather than breaking the surrounding markup).
    function feedbackIconHtml(glyph, tint) {
        var Icons = window.QuizifyIcons;
        if (!Icons || !Icons.uiIcon) return '';
        var svg = Icons.uiIcon(glyph);
        if (!svg) return '';
        return '<span class="qz-icon feedback-icon qz-icon--' + (tint || 'mix') + '" aria-hidden="true">' + svg + '</span>';
    }

    // Renders the icon mapped to an i18n key, prepended to an already-built
    // (escaped) label string. Used by the reveal chips so each feedback row
    // shows its glyph beside the text. If the key has no mapped glyph the
    // label is returned unchanged.
    function feedbackLabel(key, escapedLabel) {
        var spec = FEEDBACK_ICON[key];
        if (!spec) return escapedLabel;
        var iconHtml = feedbackIconHtml(spec.glyph, spec.tint);
        return iconHtml ? (iconHtml + escapedLabel) : escapedLabel;
    }

    // ============================================
    // Toast Notification
    // ============================================

    // showToast(message, duration[, iconKey]) — when iconKey maps to a
    // feedback glyph (#220 P3), the toast renders that SVG icon before the
    // text (innerHTML); otherwise it stays a plain text-only toast.
    function showToast(message, duration, iconKey) {
        duration = duration || 3000;
        var toast = document.getElementById('error-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'error-toast';
            toast.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);' +
                'background:#D65858;color:white;padding:10px 20px;border-radius:10px;' +
                'font-size:0.85rem;z-index:9999;opacity:0;transition:opacity 0.3s;pointer-events:none;';
            document.body.appendChild(toast);
        }
        var spec = iconKey && FEEDBACK_ICON[iconKey];
        var iconHtml = spec ? feedbackIconHtml(spec.glyph, spec.tint) : '';
        if (iconHtml) {
            toast.classList.add('toast--with-icon');
            toast.innerHTML = iconHtml + '<span class="toast-text">' + escapeHtml(message) + '</span>';
        } else {
            toast.classList.remove('toast--with-icon');
            toast.textContent = message;
        }
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
        // Delegate to the shared, i18n-aware validator (utils.js). Kept as
        // a thin wrapper so player-core's pu.validateName() calls are
        // unchanged. Falls back to a local check only if utils.js somehow
        // didn't load (it's a hard dependency, loaded before this bundle).
        if (utils && utils.validateName) {
            return utils.validateName(name);
        }
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
    // Shared standings rows — medal cards (Standings A, approved 2026-06-10).
    // One rounded row card per player: a 30px round medal disc with the rank
    // number (top-3 tinted gold/silver/bronze, rank 4+ neutral), the name, a
    // small coral "DU"/"YOU" tag for the current player, and a right-aligned
    // score. Reused by the lightning recap "Totals" (#246) and the end-screen
    // "Gesamtwertung" standings (#248) so both screens read identically.
    //
    // rows: [{ rank, name, score, isYou }] — already sorted by score desc.
    // opts: { youLabel } — localized "DU"/"YOU" tag text.
    // ============================================
    function renderMedalStandings(target, rows, opts) {
        var container = (typeof target === 'string')
            ? document.getElementById(target) : target;
        if (!container) return;
        opts = opts || {};
        var youLabel = opts.youLabel || 'DU';
        var medalClass = { 1: 'mstand-disc--gold', 2: 'mstand-disc--silver', 3: 'mstand-disc--bronze' };

        container.innerHTML = (rows || []).map(function (r) {
            var rank = r.rank;
            var discCls = 'mstand-disc' + (medalClass[rank] ? ' ' + medalClass[rank] : '');
            var rowCls = 'mstand-row' + (r.isYou ? ' mstand-row--me' : '');
            var youTag = r.isYou
                ? '<span class="mstand-you">' + escapeHtml(youLabel) + '</span>' : '';
            return '<div class="' + rowCls + '">' +
                '<span class="' + discCls + '">' + rank + '</span>' +
                '<span class="mstand-name">' + escapeHtml(r.name || '') + youTag + '</span>' +
                '<span class="mstand-score">' + r.score + '</span>' +
            '</div>';
        }).join('');
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
        paintUiIcons: paintUiIcons,
        feedbackIconHtml: feedbackIconHtml,
        feedbackLabel: feedbackLabel,
        generateQR: generateQR,
        showToast: showToast,
        saveSession: saveSession,
        getSession: getSession,
        clearSession: clearSession,
        validateName: validateName,
        MAX_RECONNECT_ATTEMPTS: MAX_RECONNECT_ATTEMPTS,
        animateValue: animateValue,
        showPointsPopup: showPointsPopup,
        renderMedalStandings: renderMedalStandings
    };

})();
