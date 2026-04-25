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
        // code wrote into it. Map server difficulty value to user-facing
        // German label and show.
        var diffBadgeEl = document.getElementById('lobby-difficulty-badge');
        if (diffBadgeEl) {
            var diffLabels = {
                'easy': '🌱 Einfach',
                'medium': '🎯 Mittel',
                'hard': '🔥 Schwer',
            };
            var diff = data.difficulty || data.game_difficulty || '';
            if (diff && diffLabels[diff]) {
                diffBadgeEl.textContent = diffLabels[diff];
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

        // Render player cards
        pu.renderPlayerCards('player-list', players);

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
        updateAdminControls(players);

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

        pu.renderPlayerCards('player-list', players);
        updateAdminControls(players);
    }

    function handlePlayerLeft(data) {
        var players = data.players || [];

        var countBadgeEl = document.getElementById('player-count-badge');
        if (countBadgeEl) countBadgeEl.textContent = players.length;

        var playersSummaryEl = document.getElementById('players-summary');
        if (playersSummaryEl) playersSummaryEl.textContent = players.length;

        var playersEmptyEl = document.getElementById('players-empty');
        if (playersEmptyEl) playersEmptyEl.classList.toggle('hidden', players.length > 0);

        pu.renderPlayerCards('player-list', players);
        updateAdminControls(players);
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
    // Admin Controls
    // ============================================

    function updateAdminControls(players) {
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
    }

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
        closeInviteModal: closeInviteModal
    };

})();
