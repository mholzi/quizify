/**
 * Quizify Player - Lobby Module
 * Lobby rendering, player list, QR code, admin controls, "How to Play" section
 */

(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var state = pu.state;

    // ============================================
    // Player List Rendering
    // ============================================

    var previousPlayers = [];

    // Variant B+D layout. Paints the hero (mega avatar + name + "DU BIST DRIN")
    // for this player, and the orbit chips for the OTHER players. The
    // count line reads "+ N weitere" or "Du wartest noch allein…". Pure
    // DOM writes — no state of its own; relies on state.playerName as
    // the source of truth for "who am I".
    function _renderHeroAndOrbits(players, data) {
        var hero = document.getElementById('pl-mega-avatar');
        var nameEl = document.getElementById('pl-mega-name');
        var alsoEl = document.getElementById('pl-also');
        var orbits = document.getElementById('player-list');
        var metaEl = document.getElementById('pl-meta');
        if (!orbits) return;  // legacy markup — nothing to paint

        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
        var myName = state.playerName || '';
        var me = null;
        var others = [];
        for (var i = 0; i < players.length; i++) {
            var p = players[i];
            if (p && p.name === myName) me = p;
            else others.push(p);
        }

        // Hero — only meaningful once we know our color from the server.
        if (hero && nameEl) {
            var initial = (myName || '?').charAt(0).toUpperCase();
            var color = (me && me.color) || state.playerColor || 'var(--color-accent-primary)';
            hero.textContent = initial;
            hero.style.background = color;
            hero.style.boxShadow = '0 0 0 6px ' + _hexWithAlpha(color, 0.22);
            nameEl.textContent = myName || '…';
        }

        // Meta line under the hero: difficulty + round count if known.
        if (metaEl) {
            var bits = [];
            var diffIcons = { 'easy': '🌱', 'medium': '🎯', 'hard': '🔥' };
            var diff = data && (data.difficulty || data.game_difficulty);
            if (diff && diffIcons[diff]) bits.push(diffIcons[diff] + ' ' + t('difficulties.' + diff));
            var total = data && (data.total_rounds || data.num_rounds);
            if (total) bits.push(total + ' ' + t('admin.summaryRoundsUnit'));
            metaEl.textContent = bits.length ? bits.join(' · ') : t('lobby.gameLobby');
        }

        // Companion line.
        if (alsoEl) {
            if (others.length === 0) {
                alsoEl.textContent = t('lobby.aloneWaiting');
            } else if (others.length === 1) {
                alsoEl.textContent = t('lobby.alsoOne');
            } else {
                alsoEl.textContent = t('lobby.alsoMany', { count: others.length });
            }
        }

        // Orbit chips — one per OTHER player. Uses the same color the
        // server assigned so chips match what each player sees on their
        // own phone.
        orbits.innerHTML = others.map(function (p) {
            var initial = (p.name || '?').charAt(0).toUpperCase();
            var bg = p.color || '#A89E89';
            var awayClass = p.connected === false ? ' is-away' : '';
            var hostClass = p.is_admin ? ' is-host' : '';
            var hostMark = p.is_admin ? '<span class="pl-orbit-host" aria-hidden="true">👑</span>' : '';
            return '<div class="pl-orbit' + awayClass + hostClass + '" data-player="' + _escape(p.name) + '">' +
                '<span class="pl-orbit-av" style="background:' + bg + '">' + _escape(initial) + '</span>' +
                '<span class="pl-orbit-nm">' + _escape(p.name || '') + '</span>' +
                hostMark +
            '</div>';
        }).join('');
    }

    // Tiny helpers — keep scope tight.
    function _escape(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function _hexWithAlpha(hex, alpha) {
        // Accepts #RRGGBB or already-rgba/var() — fallback to original.
        if (typeof hex !== 'string' || !hex.startsWith('#') || hex.length !== 7) return hex;
        var r = parseInt(hex.slice(1, 3), 16);
        var g = parseInt(hex.slice(3, 5), 16);
        var b = parseInt(hex.slice(5, 7), 16);
        return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
    }

    /**
     * Render the full lobby view from game state
     * @param {Object} data - game_state message
     */
    function renderLobby(data) {
        var players = data.players || [];

        // Player count badge
        var countBadgeEl = document.getElementById('player-count-badge');
        if (countBadgeEl) countBadgeEl.textContent = players.length;

        // Difficulty badge — was rendering as an empty pill because no
        // code wrote into it. Map server difficulty value to a localised
        // label via i18n + a small icon.
        var diffBadgeEl = document.getElementById('lobby-difficulty-badge');
        if (diffBadgeEl) {
            var diffIcons = { 'easy': '🌱', 'medium': '🎯', 'hard': '🔥' };
            var diff = data.difficulty || data.game_difficulty || '';
            if (diff && diffIcons[diff]) {
                diffBadgeEl.textContent = diffIcons[diff] + ' ' + t('difficulties.' + diff);
                diffBadgeEl.classList.remove('hidden');
            } else {
                // No difficulty info yet — hide the empty pill instead of
                // rendering a hollow placeholder.
                diffBadgeEl.classList.add('hidden');
                diffBadgeEl.textContent = '';
            }
        }

        // Players summary count
        var playersSummaryEl = document.getElementById('players-summary');
        if (playersSummaryEl) playersSummaryEl.textContent = players.length;

        // Empty state
        var playersEmptyEl = document.getElementById('players-empty');
        if (playersEmptyEl) playersEmptyEl.classList.toggle('hidden', players.length > 0);

        // Variant B+D layout: paint the hero (mega avatar + name + tag),
        // then render OTHER players as orbit chips. Falls back gracefully
        // on legacy markup — if the hero nodes aren't present, the orbit
        // call still writes into #player-list as before.
        _renderHeroAndOrbits(players, data);

        // Detect newly joined players and add animation class
        var previousNames = previousPlayers.map(function (p) { return p.name; });
        var listEl = document.getElementById('player-list');
        if (listEl) {
            var cards = listEl.querySelectorAll('.player-card');
            for (var i = 0; i < cards.length; i++) {
                var name = cards[i].getAttribute('data-player');
                if (name && previousNames.indexOf(name) === -1) {
                    cards[i].classList.add('is-new');
                }
            }
            // Remove animation class after transition
            setTimeout(function () {
                var newCards = listEl.querySelectorAll('.is-new');
                for (var j = 0; j < newCards.length; j++) {
                    newCards[j].classList.remove('is-new');
                }
            }, 2000);
        }

        previousPlayers = players.slice();

        // Admin controls
        updateAdminControls(players, data);

        // QR code / invite section
        if (data.join_url) {
            setupInviteSection(data.join_url);
        }
    }

    // ============================================
    // Player Joined / Left Handlers
    // ============================================

    function handlePlayerJoined(data) {
        var players = data.players || [];

        var countBadgeEl = document.getElementById('player-count-badge');
        if (countBadgeEl) countBadgeEl.textContent = players.length;

        var playersSummaryEl = document.getElementById('players-summary');
        if (playersSummaryEl) playersSummaryEl.textContent = players.length;

        var playersEmptyEl = document.getElementById('players-empty');
        if (playersEmptyEl) playersEmptyEl.classList.toggle('hidden', players.length > 0);

        _renderHeroAndOrbits(players, data);
        updateAdminControls(players, data);
    }

    function handlePlayerLeft(data) {
        var players = data.players || [];

        var countBadgeEl = document.getElementById('player-count-badge');
        if (countBadgeEl) countBadgeEl.textContent = players.length;

        var playersSummaryEl = document.getElementById('players-summary');
        if (playersSummaryEl) playersSummaryEl.textContent = players.length;

        var playersEmptyEl = document.getElementById('players-empty');
        if (playersEmptyEl) playersEmptyEl.classList.toggle('hidden', players.length > 0);

        _renderHeroAndOrbits(players, data);
        updateAdminControls(players, data);
    }

    // ============================================
    // Invite Section (QR + Dashboard Link)
    // ============================================

    var currentJoinUrl = null;

    function setupInviteSection(joinUrl) {
        if (!joinUrl) return;
        currentJoinUrl = joinUrl;

        // Render QR code
        pu.generateQR('player-qr-code', joinUrl);

        // Set dashboard link
        var dashLink = document.getElementById('player-dashboard-url');
        if (dashLink) {
            dashLink.href = window.location.origin + '/quizify/dashboard';
        }

        // Make QR clickable to open modal
        var qrContainer = document.getElementById('player-qr-code');
        if (qrContainer) {
            qrContainer.onclick = openQRModal;
            qrContainer.onkeydown = function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openQRModal();
                }
            };
        }
    }

    // ============================================
    // QR Modal
    // ============================================

    function openQRModal() {
        if (!currentJoinUrl) return;

        var modal = document.getElementById('qr-modal');
        var modalCode = document.getElementById('qr-modal-code');
        if (!modal || !modalCode) return;

        modalCode.innerHTML = '';
        if (typeof QRCode !== 'undefined') {
            new QRCode(modalCode, {
                text: currentJoinUrl,
                width: 256,
                height: 256,
                colorDark: '#000000',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M
            });
        }

        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';

        var closeBtn = document.getElementById('qr-modal-close');
        if (closeBtn) closeBtn.focus();
    }

    function closeQRModal() {
        var modal = document.getElementById('qr-modal');
        if (modal) {
            modal.classList.add('hidden');
            document.body.style.overflow = '';
        }
    }

    function setupQRModal() {
        var modal = document.getElementById('qr-modal');
        var backdrop = modal ? modal.querySelector('.qr-modal-backdrop') : null;
        var closeBtn = document.getElementById('qr-modal-close');

        if (backdrop) backdrop.addEventListener('click', closeQRModal);
        if (closeBtn) closeBtn.addEventListener('click', closeQRModal);

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
                closeQRModal();
            }
        });
    }

    // ============================================
    // Invite Modal (in-game sharing)
    // ============================================

    function openInviteModal() {
        if (!currentJoinUrl) return;

        var modal = document.getElementById('invite-modal');
        var modalCode = document.getElementById('invite-modal-code');
        var urlInput = document.getElementById('invite-modal-url');
        if (!modal || !modalCode) return;

        modalCode.innerHTML = '';
        if (typeof QRCode !== 'undefined') {
            new QRCode(modalCode, {
                text: currentJoinUrl,
                width: 256,
                height: 256,
                colorDark: '#000000',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M
            });
        }

        if (urlInput) urlInput.value = currentJoinUrl;

        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';

        var closeBtn = document.getElementById('invite-modal-close');
        if (closeBtn) closeBtn.focus();
    }

    function closeInviteModal() {
        var modal = document.getElementById('invite-modal');
        if (modal) {
            modal.classList.add('hidden');
            document.body.style.overflow = '';
        }
    }

    function copyJoinUrl() {
        var urlInput = document.getElementById('invite-modal-url');
        var feedback = document.getElementById('invite-copy-feedback');
        if (!urlInput || !currentJoinUrl) return;

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(currentJoinUrl).then(function () {
                showCopyFeedback(feedback);
            }).catch(function () {
                fallbackCopy(urlInput, feedback);
            });
        } else {
            fallbackCopy(urlInput, feedback);
        }
    }

    function fallbackCopy(urlInput, feedback) {
        urlInput.select();
        urlInput.setSelectionRange(0, 99999);
        try {
            document.execCommand('copy');
            showCopyFeedback(feedback);
        } catch (e) {
            console.warn('[Quizify] Copy failed:', e);
        }
    }

    function showCopyFeedback(feedback) {
        if (!feedback) return;
        feedback.classList.remove('hidden');
        setTimeout(function () { feedback.classList.add('hidden'); }, 2000);
    }

    function setupInviteModal() {
        var modal = document.getElementById('invite-modal');
        var backdrop = modal ? modal.querySelector('.invite-modal-backdrop') : null;
        var closeBtn = document.getElementById('invite-modal-close');
        var inviteBtn = document.getElementById('invite-players-btn');
        var copyBtn = document.getElementById('invite-copy-btn');

        if (backdrop) backdrop.addEventListener('click', closeInviteModal);
        if (closeBtn) closeBtn.addEventListener('click', closeInviteModal);
        if (inviteBtn) inviteBtn.addEventListener('click', openInviteModal);
        if (copyBtn) copyBtn.addEventListener('click', copyJoinUrl);

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
                closeInviteModal();
            }
        });
    }

    // ============================================
    // Lobby Music (host device only)
    // ============================================
    //
    // Ambient audio that loops on the HOST's phone while waiting in the
    // lobby. Gated three ways so it is inert by default:
    //   1. Only runs on the admin/host device (state.isAdmin) — we don't
    //      want every guest phone blasting overlapping audio.
    //   2. Only when the server advertised a `lobby_music_url` (i.e. the
    //      user pointed the option at an audio file they supplied). No
    //      audio asset ships with the integration.
    //   3. Browser autoplay policy: playback is armed but only actually
    //      starts after a user gesture. The host tapping the mute toggle
    //      counts; we also opportunistically retry play() on render in
    //      case an earlier gesture already unlocked audio.
    //
    // The host can mute/unmute via the toggle; the preference is remembered
    // for the session so re-renders don't fight the user's choice.

    var _musicUrl = null;        // current configured source (or null)
    var _musicMuted = false;     // host's explicit mute choice this session

    function _musicEls() {
        return {
            audio: document.getElementById('lobby-music-audio'),
            toggle: document.getElementById('lobby-music-toggle'),
            label: document.getElementById('lobby-music-toggle-label')
        };
    }

    function _updateMusicToggleUi() {
        var els = _musicEls();
        if (!els.toggle || !els.label) return;
        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
        var on = !_musicMuted;
        els.label.textContent = t(on ? 'lobby.musicOn' : 'lobby.musicOff');
        els.toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
    }

    // Reflect the configured source + admin status into the audio element.
    // Called from renderLobby/updateAdminControls. Safe to call repeatedly.
    function _syncLobbyMusic(data, isAdmin) {
        var els = _musicEls();
        if (!els.audio || !els.toggle) return;

        var url = (data && data.lobby_music_url) || null;
        _musicUrl = url;

        // Show the toggle only on the host device when a source exists AND we
        // are actually in the lobby (music is a waiting-room feature only).
        var inLobby = state.currentPhase === 'LOBBY' || state.currentPhase == null;
        var enabled = !!(url && isAdmin && inLobby);
        els.toggle.classList.toggle('hidden', !enabled);

        if (!enabled) {
            // Not the host, or no source — make sure nothing is playing.
            _stopLobbyMusic();
            return;
        }

        // Point the element at the source (only when it changed, so we
        // don't restart playback on every state push).
        if (els.audio.getAttribute('src') !== url) {
            els.audio.setAttribute('src', url);
        }

        _updateMusicToggleUi();

        // Try to play unless the host muted. play() may reject if no gesture
        // has happened yet — that's fine, the toggle tap will start it.
        if (!_musicMuted) {
            els.audio.muted = false;
            var p = els.audio.play();
            if (p && typeof p.catch === 'function') {
                p.catch(function () { /* awaiting user gesture */ });
            }
        }
    }

    function _stopLobbyMusic() {
        var els = _musicEls();
        if (els.audio) {
            try { els.audio.pause(); } catch (e) { /* ignore */ }
        }
    }

    function _setupLobbyMusicToggle() {
        var els = _musicEls();
        if (!els.toggle || !els.audio) return;
        els.toggle.addEventListener('click', function () {
            _musicMuted = !_musicMuted;
            if (_musicMuted) {
                _stopLobbyMusic();
            } else if (_musicUrl) {
                els.audio.muted = false;
                var p = els.audio.play();
                if (p && typeof p.catch === 'function') {
                    p.catch(function () { /* unexpected — gesture present */ });
                }
            }
            _updateMusicToggleUi();
        });
    }

    // ============================================
    // Admin Controls
    // ============================================

    function updateAdminControls(players, data) {
        var adminControls = document.getElementById('admin-controls');
        var lobbyStatus = document.getElementById('lobby-status');
        if (!adminControls) return;

        var currentPlayer = null;
        if (players && Array.isArray(players)) {
            for (var i = 0; i < players.length; i++) {
                if (players[i].name === state.playerName) {
                    currentPlayer = players[i];
                    break;
                }
            }
        }

        var playerIsAdmin = currentPlayer && currentPlayer.is_admin === true;
        state.isAdmin = playerIsAdmin;

        if (playerIsAdmin) {
            adminControls.classList.remove('hidden');
            if (lobbyStatus) lobbyStatus.classList.add('hidden');

            // Show start button when at least 1 player
            var startBtn = document.getElementById('start-game-btn');
            if (startBtn) {
                startBtn.disabled = players.length < 1;
            }
        } else {
            adminControls.classList.add('hidden');
            if (lobbyStatus) lobbyStatus.classList.remove('hidden');
        }

        // Lobby music follows admin status + configured source. `data` is the
        // game_state message; the last seen one is cached so join/leave
        // updates (which don't carry lobby_music_url) keep the right source.
        if (data && Object.prototype.hasOwnProperty.call(data, 'lobby_music_url')) {
            _lastMusicData = data;
        } else if (data) {
            // A state push without the key means music is off — clear cache.
            _lastMusicData = null;
        }
        _syncLobbyMusic(_lastMusicData, playerIsAdmin);
    }

    var _lastMusicData = null;

    function setupAdminControls(sendFn) {
        var startBtn = document.getElementById('start-game-btn');
        if (startBtn) {
            startBtn.addEventListener('click', function () {
                if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

                startBtn.disabled = true;
                startBtn.innerHTML = '<span class="btn-icon" aria-hidden="true">🎉</span><span>Starting...</span>';

                sendFn('start_game', {});
            });
        }
    }

    // ============================================
    // Init (called by core)
    // ============================================

    function init(sendFn) {
        setupQRModal();
        setupInviteModal();
        setupAdminControls(sendFn);
        _setupLobbyMusicToggle();
        pu.setupCollapsibles();
    }

    // ============================================
    // Export
    // ============================================

    window.QuizifyPlayerLobby = {
        init: init,
        renderLobby: renderLobby,
        handlePlayerJoined: handlePlayerJoined,
        handlePlayerLeft: handlePlayerLeft,
        setupInviteSection: setupInviteSection,
        updateAdminControls: updateAdminControls,
        stopLobbyMusic: _stopLobbyMusic,
        closeInviteModal: closeInviteModal
    };

})();
