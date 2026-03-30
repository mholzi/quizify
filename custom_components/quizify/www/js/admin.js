/**
 * Quizify — Admin panel client.
 * Manages game setup, lobby, live monitoring, and game flow control.
 * Mirrors Beatify admin UX: Setup → Lobby → In-Game → Reveal → Finale.
 */

(function () {
    'use strict';

    // ---- State ----
    let ws = null;
    let reconnectAttempts = 0;
    const MAX_RECONNECT = 5;

    // Settings (from chips)
    let selectedCategory = 'mixed';
    let selectedCategories = []; // for multi-select mode
    let selectedDifficulty = 'medium';
    let selectedRounds = 10;

    // Game state
    let currentPhase = 'LOBBY';
    let playerCount = 0;

    // ---- Components ----
    const adminTimer = new TimerBar('admin-timer-bar', 15);

    // ---- DOM refs ----
    const views = {
        setup: document.getElementById('setup-screen'),
        lobby: document.getElementById('lobby-screen'),
        game: document.getElementById('game-view'),
        reveal: document.getElementById('admin-reveal-view'),
        finale: document.getElementById('admin-finale-view'),
    };

    const els = {
        categoryChips: document.getElementById('category-chips'),
        categorySummary: document.getElementById('category-summary'),
        difficultyChips: document.getElementById('difficulty-chips'),
        roundsChips: document.getElementById('rounds-chips'),
        gameSettingsSummary: document.getElementById('game-settings-summary'),
        qrContainer: document.getElementById('qr-container'),
        joinUrl: document.getElementById('join-url'),
        dashboardLink: document.getElementById('dashboard-link'),
        lobbyPlayerCount: document.getElementById('lobby-player-count'),
        lobbyPlayerChips: document.getElementById('lobby-player-chips'),
        lobbyPlayersEmpty: document.getElementById('lobby-players-empty'),
        startGameBtn: document.getElementById('start-game-btn'),
        startGameplayBtn: document.getElementById('start-gameplay-btn'),
        participateBtn: document.getElementById('participate-btn'),
        // In-game
        adminRound: document.getElementById('admin-round'),
        adminQuestion: document.getElementById('admin-question'),
        adminCorrect: document.getElementById('admin-correct'),
        gameLeaderboard: document.getElementById('game-leaderboard'),
        nextQuestionBtn: document.getElementById('next-question-btn'),
        endGameBtn: document.getElementById('end-game-btn'),
        // Reveal
        revealRound: document.getElementById('reveal-round'),
        revealQuestion: document.getElementById('reveal-question'),
        revealCorrect: document.getElementById('reveal-correct'),
        revealFunFact: document.getElementById('reveal-fun-fact'),
        revealLeaderboard: document.getElementById('reveal-leaderboard'),
        continueBtn: document.getElementById('continue-btn'),
        // Finale
        adminPodium: document.getElementById('admin-podium'),
        adminFinaleLeaderboard: document.getElementById('admin-finale-leaderboard'),
        newGameBtn: document.getElementById('new-game-btn'),
        // Admin join modal
        adminJoinModal: document.getElementById('admin-join-modal'),
        adminNameInput: document.getElementById('admin-name-input'),
        adminJoinBtn: document.getElementById('admin-join-btn'),
        adminCancelBtn: document.getElementById('admin-cancel-btn'),
    };

    // ---- View management ----
    function showView(name) {
        Object.values(views).forEach(function (v) { if (v) v.classList.remove('active'); });
        if (views[name]) views[name].classList.add('active');
    }

    // ---- Collapsible sections (Beatify pattern) ----
    function setupCollapsibles() {
        document.querySelectorAll('.section-header-collapsible').forEach(function (header) {
            header.addEventListener('click', function () {
                var section = header.closest('.section-collapsible');
                if (section) {
                    section.classList.toggle('collapsed');
                    header.setAttribute('aria-expanded', !section.classList.contains('collapsed'));
                }
            });
        });
    }

    // ---- Chip selectors ----
    function setupChips(container, onSelect) {
        if (!container) return;
        container.addEventListener('click', function (e) {
            var chip = e.target.closest('.chip');
            if (!chip) return;
            container.querySelectorAll('.chip').forEach(function (c) { c.classList.remove('active'); });
            chip.classList.add('active');
            onSelect(chip.dataset.value);
        });
    }

    // Category: Gemischt = single select, others = multi-select
    function setupCategoryChips(container) {
        if (!container) return;
        container.addEventListener('click', function (e) {
            var chip = e.target.closest('.chip');
            if (!chip) return;
            var val = chip.dataset.value;

            if (val === 'mixed') {
                container.querySelectorAll('.chip').forEach(function (c) { c.classList.remove('active'); });
                chip.classList.add('active');
                selectedCategory = 'mixed';
                selectedCategories = [];
            } else {
                var mixedChip = container.querySelector('.chip[data-value="mixed"]');
                if (mixedChip) mixedChip.classList.remove('active');

                chip.classList.toggle('active');

                selectedCategories = Array.from(container.querySelectorAll('.chip.active'))
                    .map(function (c) { return c.dataset.value; });

                if (selectedCategories.length === 0) {
                    if (mixedChip) mixedChip.classList.add('active');
                    selectedCategory = 'mixed';
                } else if (selectedCategories.length === 1) {
                    selectedCategory = selectedCategories[0];
                } else {
                    selectedCategory = 'multi';
                }
            }
            updateCategorySummary();
        });
    }

    function updateCategorySummary() {
        if (!els.categorySummary) return;
        if (selectedCategory === 'mixed') {
            els.categorySummary.textContent = 'Gemischt';
        } else if (selectedCategory === 'multi') {
            els.categorySummary.textContent = selectedCategories.length + ' Kategorien';
        } else {
            var activeChip = els.categoryChips.querySelector('.chip.active');
            els.categorySummary.textContent = activeChip ? activeChip.textContent : selectedCategory;
        }
    }

    function updateSettingsSummary() {
        if (!els.gameSettingsSummary) return;
        var diffChip = els.difficultyChips ? els.difficultyChips.querySelector('.chip.active') : null;
        var diffLabel = diffChip ? diffChip.textContent : 'Mittel';
        els.gameSettingsSummary.innerHTML = diffLabel + ' &bull; ' + selectedRounds + ' Runden';
    }

    setupCategoryChips(els.categoryChips);
    setupChips(els.difficultyChips, function (v) {
        selectedDifficulty = v;
        updateSettingsSummary();
    });
    setupChips(els.roundsChips, function (v) {
        selectedRounds = parseInt(v, 10);
        updateSettingsSummary();
    });
    setupCollapsibles();

    // ---- WebSocket ----
    function connect() {
        var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        var savedToken = sessionStorage.getItem('quizify_admin_session_token');
        var url = proto + '//' + location.host + '/api/quizify/ws?role=admin';
        if (savedToken) {
            url += '&token=' + encodeURIComponent(savedToken);
        }
        ws = new WebSocket(url);

        ws.onopen = function () {
            reconnectAttempts = 0;
            updateConnectionStatus('connected');
            send('admin_connect', {});
        };

        ws.onmessage = function (evt) {
            try {
                var msg = JSON.parse(evt.data);
                handleMessage(msg);
            } catch (e) {
                console.error('[Quizify Admin] Bad message:', e);
            }
        };

        ws.onclose = function () {
            ws = null;
            if (reconnectAttempts < MAX_RECONNECT) {
                reconnectAttempts++;
                updateConnectionStatus('reconnecting');
                setTimeout(connect, 1000 * reconnectAttempts);
            } else {
                updateConnectionStatus('disconnected');
            }
        };

        ws.onerror = function () { if (ws) ws.close(); };
    }

    function send(type, payload) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(Object.assign({ type: type }, payload)));
        }
    }

    // ---- Message router ----
    function handleMessage(msg) {
        switch (msg.type || msg.event) {
            case 'game_state':
                handleGameState(msg);
                break;
            case 'player_joined':
            case 'player_left':
                if (msg.players) renderLobbyPlayers(msg.players);
                break;
            case 'question_started':
                handleQuestionStarted(msg);
                break;
            case 'round_summary':
            case 'round_evaluated':
                handleRoundSummary(msg);
                break;
            case 'timer_tick':
                adminTimer.update(msg.remaining);
                break;
            case 'finale':
            case 'game_ended':
                handleFinale(msg);
                break;
            case 'game_reset':
                handleGameReset();
                break;
            case 'error':
                console.warn('[Quizify Admin] Error:', msg.code, msg.message);
                showErrorToast(msg.message || msg.code);
                break;
        }
    }

    // ---- Phase handlers ----

    function handleGameState(msg) {
        currentPhase = msg.phase;
        if (msg.admin_session_token) {
            sessionStorage.setItem('quizify_admin_session_token', msg.admin_session_token);
        }
        if (msg.players) renderLobbyPlayers(msg.players);

        switch (msg.phase) {
            case 'LOBBY':
                // If we're still on setup screen, stay there until user clicks Start Game
                // If we already transitioned to lobby, stay on lobby
                if (views.lobby.classList.contains('active') ||
                    views.game.classList.contains('active')) {
                    showView('lobby');
                }
                break;
            case 'QUESTION_ACTIVE':
                if (msg.question) {
                    handleQuestionStarted({
                        question_text: msg.question.text,
                        correct_answer: msg.question.answers ? findCorrectAnswer(msg.question) : '',
                        timer_duration: msg.question.time_limit,
                        round_num: msg.round,
                        total_rounds: msg.total_rounds,
                    });
                }
                if (msg.leaderboard) renderLeaderboard(els.gameLeaderboard, msg.leaderboard);
                break;
            case 'ANSWER_REVEAL':
                showView('reveal');
                if (msg.round_summary) showReveal(msg);
                if (msg.leaderboard) renderLeaderboard(els.revealLeaderboard, msg.leaderboard);
                break;
            case 'FINALE':
                handleFinale(msg);
                break;
        }
    }

    function handleQuestionStarted(msg) {
        currentPhase = 'QUESTION_ACTIVE';
        showView('game');

        els.adminRound.textContent = 'Frage ' + msg.round_num + ' / ' + msg.total_rounds;
        els.adminQuestion.textContent = msg.question_text;
        els.adminCorrect.textContent = 'Richtig: ' + (msg.correct_answer || '');

        adminTimer.start(msg.timer_duration);
        els.nextQuestionBtn.classList.add('hidden');
    }

    function handleRoundSummary(msg) {
        currentPhase = 'ANSWER_REVEAL';
        adminTimer.stop();
        showView('reveal');
        showReveal(msg);
    }

    function showReveal(msg) {
        var summary = msg.round_summary || msg;
        els.revealRound.textContent = 'Frage ' + (msg.round || '') + ' / ' + (msg.total_rounds || '');
        els.revealQuestion.textContent = summary.question_text || (msg.question ? msg.question.text : '') || '';
        els.revealCorrect.textContent = 'Richtig: ' + (summary.correct_answer || '');

        var funFactText = summary.fun_fact || '';
        if (funFactText) {
            els.revealFunFact.classList.remove('hidden');
            els.revealFunFact.classList.add('visible');
            els.revealFunFact.querySelector('.fun-fact-text').textContent = funFactText;
        } else {
            els.revealFunFact.classList.add('hidden');
        }

        var lb = summary.leaderboard || msg.leaderboard || [];
        renderLeaderboard(els.revealLeaderboard, lb);
    }

    function handleFinale(msg) {
        currentPhase = 'FINALE';
        adminTimer.stop();
        showView('finale');

        var podium = msg.podium || [];
        renderPodium(els.adminPodium, podium);

        var lb = msg.leaderboard || msg.all_players || [];
        renderLeaderboard(els.adminFinaleLeaderboard, lb);
    }

    function handleGameReset() {
        currentPhase = 'LOBBY';
        showView('setup');
    }

    // ---- Renderers ----

    function renderLobbyPlayers(players) {
        var list = Array.isArray(players) ? players : Object.values(players);
        playerCount = list.length;
        els.lobbyPlayerCount.textContent = playerCount;

        // Toggle "Start Gameplay" button visibility
        if (playerCount >= 1) {
            els.startGameplayBtn.classList.remove('hidden');
        } else {
            els.startGameplayBtn.classList.add('hidden');
        }

        // Toggle empty state
        if (els.lobbyPlayersEmpty) {
            els.lobbyPlayersEmpty.classList.toggle('hidden', playerCount > 0);
        }

        els.lobbyPlayerChips.innerHTML = list
            .map(function (p) {
                var name = typeof p === 'string' ? p : (p.name || p);
                return '<span class="player-chip"><span class="dot"></span>' + escapeHtml(name) + '</span>';
            })
            .join('');
    }

    function renderLeaderboard(container, players) {
        if (!container) return;
        container.innerHTML = players
            .map(function (p, i) {
                var rank = p.rank || i + 1;
                var rankClass = rank <= 3 ? ' rank-' + rank : '';
                return '<div class="leaderboard-row">' +
                    '<span class="leaderboard-rank' + rankClass + '">' + rank + '</span>' +
                    '<span class="leaderboard-name">' + escapeHtml(p.name) + '</span>' +
                    '<span class="leaderboard-score">' + p.score + '</span>' +
                    (p.streak > 1 ? '<span class="leaderboard-streak">' + p.streak + 'x</span>' : '') +
                    '</div>';
            })
            .join('');
    }

    function renderPodium(container, podium) {
        var ordered = [];
        if (podium[1]) ordered.push(Object.assign({}, podium[1], { place: 2 }));
        if (podium[0]) ordered.push(Object.assign({}, podium[0], { place: 1 }));
        if (podium[2]) ordered.push(Object.assign({}, podium[2], { place: 3 }));

        var medals = { 1: '🥇', 2: '🥈', 3: '🥉' };
        var barClass = { 1: 'first', 2: 'second', 3: 'third' };

        container.innerHTML = ordered
            .map(function (p) {
                return '<div class="podium-place">' +
                    '<div class="podium-avatar">' + (medals[p.place] || '') + '</div>' +
                    '<div class="podium-name">' + escapeHtml(p.name) + '</div>' +
                    '<div class="podium-score">' + p.score + ' Pkt.</div>' +
                    '<div class="podium-bar ' + (barClass[p.place] || '') + '">' + p.place + '</div>' +
                    '</div>';
            })
            .join('');
    }

    function findCorrectAnswer(question) {
        if (question.correct_answer) return question.correct_answer;
        if (question.answers && Array.isArray(question.answers)) {
            for (var i = 0; i < question.answers.length; i++) {
                var a = question.answers[i];
                if (typeof a === 'object' && a.correct) return a.text;
            }
        }
        return '';
    }

    function escapeHtml(text) {
        return QuizifyUtils.escapeHtml(text);
    }

    // ---- QR Code ----
    var _qrInstance = null;
    function generateQR(url) {
        var container = document.getElementById('qr-container');
        if (!container) return;
        container.innerHTML = '';

        if (typeof QRCode !== 'undefined') {
            _qrInstance = new QRCode(container, {
                text: url,
                width: 180,
                height: 180,
                colorDark: '#0b0e1a',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M,
            });
        } else {
            container.innerHTML = '<div style="padding:20px;word-break:break-all;font-size:12px;">' + url + '</div>';
        }
    }

    // ---- Admin Join Modal (Beatify pattern) ----

    function openAdminJoinModal() {
        els.adminJoinModal.classList.remove('hidden');
        els.adminNameInput.value = '';
        els.adminJoinBtn.disabled = true;
        var errorMsg = document.getElementById('admin-name-error');
        if (errorMsg) errorMsg.classList.add('hidden');
        els.adminNameInput.focus();
    }

    function closeAdminJoinModal() {
        els.adminJoinModal.classList.add('hidden');
        els.adminNameInput.value = '';
        els.adminJoinBtn.disabled = true;
    }

    function handleAdminJoin() {
        var name = els.adminNameInput.value.trim();
        if (!name) return;

        els.adminJoinBtn.disabled = true;
        els.adminJoinBtn.textContent = 'Beitritt...';

        // Send join message over the existing admin WS with is_admin flag
        send('join', { name: name, is_admin: true });

        // Close modal — admin stays on admin page
        closeAdminJoinModal();
        els.adminJoinBtn.textContent = 'Beitreten';

        // Hide participate button — admin can only join once
        if (els.participateBtn) {
            els.participateBtn.disabled = true;
            els.participateBtn.style.opacity = '0.4';
            els.participateBtn.style.pointerEvents = 'none';
            els.participateBtn.textContent = '✓ Beigetreten als ' + name;
        }
    }

    function setupAdminJoinModal() {
        els.participateBtn.addEventListener('click', openAdminJoinModal);
        els.adminCancelBtn.addEventListener('click', closeAdminJoinModal);

        // Close on backdrop click
        var backdrop = els.adminJoinModal.querySelector('.modal-backdrop');
        if (backdrop) backdrop.addEventListener('click', closeAdminJoinModal);

        // Enable/disable join button based on input
        els.adminNameInput.addEventListener('input', function () {
            var name = this.value.trim();
            els.adminJoinBtn.disabled = !name || name.length > 20;
        });

        // Enter key submits
        els.adminNameInput.addEventListener('keypress', function (e) {
            if (e.key === 'Enter' && !els.adminJoinBtn.disabled) {
                handleAdminJoin();
            }
        });

        els.adminJoinBtn.addEventListener('click', handleAdminJoin);
    }

    // ---- Event listeners ----

    // Setup screen → Lobby screen (Start Game creates the game session)
    els.startGameBtn.addEventListener('click', function () {
        showView('lobby');
        initJoinUrl();
    });

    // Lobby → Start Gameplay (actually starts the quiz rounds)
    els.startGameplayBtn.addEventListener('click', function () {
        var categoryPayload = selectedCategory === 'mixed'
            ? null
            : selectedCategory === 'multi'
                ? selectedCategories
                : selectedCategory;
        send('start_game', {
            category: categoryPayload,
            difficulty: selectedDifficulty === 'mixed' ? null : selectedDifficulty,
            num_rounds: selectedRounds,
        });
    });

    // Next question (from reveal or in-game skip)
    els.nextQuestionBtn.addEventListener('click', function () {
        send('next_question', {});
    });

    els.continueBtn.addEventListener('click', function () {
        send('next_question', {});
    });

    // End game — use modal
    els.endGameBtn.addEventListener('click', function () {
        var modal = document.getElementById('end-game-modal');
        if (modal) modal.classList.remove('hidden');
    });

    // End game modal confirm/cancel
    document.getElementById('end-game-confirm-btn').addEventListener('click', function () {
        send('end_game', {});
        document.getElementById('end-game-modal').classList.add('hidden');
    });
    document.getElementById('end-game-cancel-btn').addEventListener('click', function () {
        document.getElementById('end-game-modal').classList.add('hidden');
    });
    // Close end-game modal on backdrop click
    (function () {
        var backdrop = document.querySelector('#end-game-modal .modal-backdrop');
        if (backdrop) backdrop.addEventListener('click', function () {
            document.getElementById('end-game-modal').classList.add('hidden');
        });
    })();

    // New game (from finale)
    els.newGameBtn.addEventListener('click', function () {
        send('reset_game', {});
    });

    // Escape key closes modals
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            if (!els.adminJoinModal.classList.contains('hidden')) {
                closeAdminJoinModal();
            }
            var endModal = document.getElementById('end-game-modal');
            if (endModal && !endModal.classList.contains('hidden')) {
                endModal.classList.add('hidden');
            }
        }
    });

    // ---- Generate join URL ----
    function initJoinUrl() {
        var joinUrl = window.location.origin + '/quizify/player';
        els.joinUrl.textContent = joinUrl;
        generateQR(joinUrl);

        // Dashboard link
        if (els.dashboardLink) {
            els.dashboardLink.href = window.location.origin + '/quizify/dashboard';
        }
    }

    // ---- Error toast ----
    function showErrorToast(message) {
        var toast = document.getElementById('error-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'error-toast';
            toast.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#ff4757;color:white;padding:10px 20px;border-radius:10px;font-size:0.85rem;z-index:9999;opacity:0;transition:opacity 0.3s;pointer-events:none;';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.style.opacity = '1';
        setTimeout(function () { toast.style.opacity = '0'; }, 3000);
    }

    // ---- Connection status indicator ----
    function updateConnectionStatus(status) {
        var indicator = document.getElementById('conn-status');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'conn-status';
            indicator.style.cssText = 'position:fixed;bottom:12px;right:12px;display:flex;align-items:center;gap:6px;font-size:0.75rem;color:#a4a4b8;z-index:100;';
            document.body.appendChild(indicator);
        }
        var colors = { connected: '#00b894', reconnecting: '#ffa502', disconnected: '#ff4757' };
        indicator.innerHTML = '<span style="width:8px;height:8px;border-radius:50%;background:' + (colors[status] || '#636e8a') + ';"></span>' + status;
    }

    // ---- Init ----
    setupAdminJoinModal();

    // i18n init
    if (window.QuizifyI18n) {
        QuizifyI18n.init().then(function () {
            QuizifyI18n.initPageTranslations();
        });
    }

    connect();
    updateConnectionStatus('reconnecting');
})();
