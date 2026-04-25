/**
 * Quizify — Admin panel client.
 * Manages game setup, lobby, live monitoring, and game flow control.
 */

(function () {
    'use strict';

    // ---- State ----
    let ws = null;
    let reconnectAttempts = 0;
    const MAX_RECONNECT = 5;
    let _redirecting = false;

    // Settings (from chips)
    let selectedCategory = 'mixed';
    let selectedCategories = [];
    let selectedDifficulty = 'medium';
    let selectedRounds = 10;
    let selectedLanguage = 'de';

    // Game state
    let currentPhase = 'LOBBY';
    let playerCount = 0;

    // ---- Simple inline timer ----
    var adminTimerEl = null;
    var adminTimerInterval = null;
    var adminTimer = {
        start: function(duration) {
            adminTimerEl = document.getElementById('admin-timer-bar');
            clearInterval(adminTimerInterval);
        },
        update: function(remaining) {
            if (!adminTimerEl) adminTimerEl = document.getElementById('admin-timer-bar');
            if (adminTimerEl) adminTimerEl.textContent = Math.ceil(remaining) + 's';
        },
        stop: function() {
            clearInterval(adminTimerInterval);
            if (adminTimerEl) adminTimerEl.textContent = '';
        }
    };

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
        languageChips: document.getElementById('language-chips'),
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
        revealAnswersSection: document.getElementById('reveal-answers-section'),
        revealResultsCards: document.getElementById('reveal-results-cards'),
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

    // ---- Redirect helper ----
    function redirectToPlayer(name) {
        _redirecting = true;
        sessionStorage.setItem('quizify_admin_name', name);
        var url = new URL('/quizify/player', location.href);
        url.searchParams.set('name', name);
        url.searchParams.set('admin', 'true');
        window.location.href = url.toString();
    }

    // ---- View management ----
    function showView(name) {
        if (_redirecting) return;
        Object.values(views).forEach(function (v) { if (v) v.classList.remove('active'); });
        if (views[name]) views[name].classList.add('active');
    }

    // ---- Collapsible sections ----
    function setupCollapsibles() {
        document.addEventListener('click', function (e) {
            var header = e.target.closest('.section-header-collapsible');
            if (!header) return;
            var section = header.closest('.section-collapsible');
            if (section) {
                section.classList.toggle('collapsed');
                header.setAttribute('aria-expanded', String(!section.classList.contains('collapsed')));
            }
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
    setupChips(els.languageChips, function (v) {
        selectedLanguage = v;
        // Show/hide category chips based on selected language
        if (els.categoryChips) {
            var chips = els.categoryChips.querySelectorAll('.chip[data-lang]');
            chips.forEach(function (chip) {
                chip.style.display = (chip.dataset.lang === v) ? '' : 'none';
            });
            // Reset to mixed if current category doesn't match language
            var activeChip = els.categoryChips.querySelector('.chip.active');
            if (activeChip && activeChip.dataset.lang && activeChip.dataset.lang !== v) {
                activeChip.classList.remove('active');
                var mixedChip = els.categoryChips.querySelector('.chip[data-value="mixed"]');
                if (mixedChip) mixedChip.classList.add('active');
                selectedCategory = 'mixed';
                selectedCategories = [];
            }
        }
        updateSettingsSummary();
    });
    // Init: hide English category chips on load
    if (els.categoryChips) {
        els.categoryChips.querySelectorAll('.chip[data-lang="en"]').forEach(function (chip) {
            chip.style.display = 'none';
        });
    }
    setupCollapsibles();

    // ---- WebSocket ----
    function connect() {
        var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        var savedToken = sessionStorage.getItem('quizify_admin_session_token');
        var url = proto + '//' + location.host + '/api/quizify/ws?role=admin';
        if (savedToken) url += '&token=' + encodeURIComponent(savedToken);
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
                // Translate server error codes to user-friendly German.
                // Raw server messages can leak into toasts (e.g. "Admin only")
                // when they shouldn't be visible to the user (#22 in review).
                var errorTranslations = {
                    'INVALID_ACTION': 'Aktion nicht erlaubt',
                    'GAME_ALREADY_STARTED': 'Spiel l\u00E4uft bereits',
                    'GAME_NOT_STARTED': 'Kein aktives Spiel',
                    'ROUND_EXPIRED': 'Zeit abgelaufen',
                    'ALREADY_SUBMITTED': 'Bereits geantwortet',
                    'NAME_TAKEN': 'Name bereits vergeben',
                    'NAME_INVALID': 'Ung\u00FCltiger Name',
                    'GAME_FULL': 'Spiel ist voll',
                    'NOT_IN_GAME': 'Nicht im Spiel',
                    'NO_QUESTIONS_REMAINING': 'Keine Fragen mehr',
                };
                var userMsg = errorTranslations[msg.code] || msg.message || 'Fehler';
                // Suppress the noisy "Admin only" ping from initial admin_connect
                // attempt \u2014 it's expected when not yet authenticated.
                if (msg.code === 'INVALID_ACTION' && msg.message === 'Admin only') {
                    break;
                }
                showErrorToast(userMsg);
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

        // If game is running and we have a stored name, redirect to player.html
        if (msg.phase !== 'LOBBY' && msg.phase !== 'FINALE') {
            var savedName = sessionStorage.getItem('quizify_admin_name');
            if (savedName && !_redirecting) {
                redirectToPlayer(savedName);
                return;
            }
        } else {
            // Game is in lobby or over — clear any stale name so we don't get stuck
            sessionStorage.removeItem('quizify_admin_name');
        }

        switch (msg.phase) {
            case 'LOBBY':
                if (views.lobby.classList.contains('active') ||
                    views.game.classList.contains('active')) {
                    showView('lobby');
                }
                break;
            case 'QUESTION_ACTIVE':
                if (msg.question) {
                    handleQuestionStarted({
                        question_text: msg.question.text,
                        correct_answer: '',
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
        if (_redirecting) return;
        currentPhase = 'QUESTION_ACTIVE';
        showView('game');

        els.adminRound.textContent = 'Frage ' + msg.round_num + ' / ' + msg.total_rounds;
        els.adminQuestion.textContent = msg.question_text;
        // Never show correct answer during active question
        if (els.adminCorrect) {
            els.adminCorrect.textContent = '';
            els.adminCorrect.style.display = 'none';
        }

        adminTimer.start(msg.timer_duration);
        if (els.nextQuestionBtn) els.nextQuestionBtn.classList.add('hidden');
        if (els.endGameBtn) els.endGameBtn.classList.add('hidden');
    }

    function handleRoundSummary(msg) {
        if (_redirecting) return;
        currentPhase = 'ANSWER_REVEAL';
        adminTimer.stop();
        showView('reveal');
        showReveal(msg);
    }

    function showReveal(msg) {
        var summary = msg.round_summary || msg;
        if (els.revealRound) els.revealRound.textContent = 'Frage ' + (msg.round || '') + ' / ' + (msg.total_rounds || '');
        if (els.revealQuestion) els.revealQuestion.textContent = summary.question_text || (msg.question ? msg.question.text : '') || '';
        if (els.revealCorrect) els.revealCorrect.textContent = 'Richtig: ' + (summary.correct_answer || '');
        if (els.adminCorrect) {
            els.adminCorrect.textContent = 'Richtig: ' + (summary.correct_answer || '');
            els.adminCorrect.style.display = '';
        }
        if (els.endGameBtn) els.endGameBtn.classList.remove('hidden');

        var funFactText = summary.fun_fact || '';
        if (els.revealFunFact) {
            if (funFactText) {
                els.revealFunFact.classList.remove('hidden');
                els.revealFunFact.classList.add('visible');
                var ffText = els.revealFunFact.querySelector('.fun-fact-text');
                if (ffText) ffText.textContent = funFactText;
            } else {
                els.revealFunFact.classList.add('hidden');
            }
        }

        // ---- Answer distribution chart ----
        renderAnswerDistribution(summary, msg);

        // Per-player result cards (Beatify-style)
        var allAnswers = summary.all_answers || msg.all_answers || [];
        if (allAnswers.length && els.revealResultsCards) {
            if (els.revealAnswersSection) els.revealAnswersSection.style.display = '';
            var sorted = allAnswers.slice().sort(function(a, b) { return (b.points_earned || 0) - (a.points_earned || 0); });
            els.revealResultsCards.innerHTML = '<div class="results-cards-scroll">' + sorted.map(function(p) {
                var ok = p.correct;
                var noAns = p.no_answer;
                var pts = p.points_earned || 0;
                var spd = p.speed_bonus || 0;
                var str = p.streak_bonus || 0;
                var dbl = p.double_points || false;
                var diff = p.difficulty_multiplier || 1.0;
                var streak = p.streak || 0;
                var scoreClass = ok && pts >= 1000 ? 'is-score-high' : ok ? 'is-score-medium' : 'is-score-zero';
                var bonuses = '';
                if (ok && spd > 0) bonuses += '<div class="card-bonus">⚡ +' + spd + ' Speed</div>';
                if (ok && str > 0) bonuses += '<div class="card-bonus">🔥 +' + str + ' ' + streak + 'x Streak</div>';
                if (ok && diff > 1.0) bonuses += '<div class="card-bonus">⭐ ' + diff.toFixed(1) + 'x Schwierigkeitsgrad</div>';
                if (dbl) bonuses += '<div class="card-bonus">✨ 2x Double Points</div>';
                return '<div class="result-card ' + scoreClass + '">' +
                    '<div class="card-name">' + escapeHtml(p.player_name) + '</div>' +
                    '<div class="card-guess">' + escapeHtml(p.answer_text || '—') + '</div>' +
                    '<div class="card-accuracy">' + (ok ? '✅ Richtig' : noAns ? '⏱️ Keine Antwort' : '❌ Falsch') + '</div>' +
                    bonuses +
                    '<div class="card-score">' + (pts > 0 ? '+' + pts : '0') + '</div>' +
                    '</div>';
            }).join('') + '</div>';
        } else if (els.revealAnswersSection) {
            els.revealAnswersSection.style.display = 'none';
        }

        var lb = summary.leaderboard || msg.leaderboard || [];
        renderLeaderboard(els.revealLeaderboard, lb);
    }

    function renderAnswerDistribution(summary, msg) {
        var container = document.getElementById('reveal-distribution');
        if (!container) return;

        var distribution = summary.answer_distribution || msg.answer_distribution || [];
        var answerTexts = [];

        // Build answer text array from all_answers entries (pick first occurrence per index)
        var allAnswers = summary.all_answers || msg.all_answers || [];
        allAnswers.forEach(function(a) {
            if (typeof a.answer_index === 'number' && !answerTexts[a.answer_index]) {
                answerTexts[a.answer_index] = a.answer_text || '';
            }
        });

        // Also try msg.question.answers for authoritative labels
        if (msg.question && Array.isArray(msg.question.answers)) {
            msg.question.answers.forEach(function(a, i) {
                answerTexts[i] = a.text || answerTexts[i] || '';
            });
        }

        var correctIndex = summary.correct_answer_index;
        if (typeof correctIndex === 'undefined') correctIndex = msg.correct_answer_index;

        if (!distribution.length) {
            container.style.display = 'none';
            return;
        }
        container.style.display = '';

        var html = '<div class="distribution-chart">';
        distribution.forEach(function(item) {
            if (item.no_answer) return; // skip timeout row
            var isCorrect = item.index === correctIndex;
            var barColor = isCorrect ? '#7FA897' : '#E5DFCF';  // sage for correct, cream hairline for rest
            var label = answerTexts[item.index] || ('Antwort ' + (item.index + 1));
            var pct = item.percent || 0;
            var count = item.count || 0;
            html +=
                '<div class="dist-row">' +
                    '<div class="dist-label" title="' + escapeHtml(label) + '">' +
                        (isCorrect ? '<span class="dist-correct-icon">✓</span>' : '') +
                        escapeHtml(label) +
                    '</div>' +
                    '<div class="dist-bar-wrap">' +
                        '<div class="dist-bar" style="width:' + Math.max(pct, 2) + '%;background:' + barColor + ';"></div>' +
                    '</div>' +
                    '<div class="dist-meta">' + count + ' (' + pct + '%)</div>' +
                '</div>';
        });
        html += '</div>';
        container.innerHTML = html;
    }

    function handleFinale(msg) {
        if (_redirecting) return;
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
        sessionStorage.removeItem('quizify_admin_name');
        _redirecting = false;
        showView('setup');
    }

    // ---- Renderers ----

    function renderLobbyPlayers(players) {
        var list = Array.isArray(players) ? players : Object.values(players);
        playerCount = list.length;
        if (els.lobbyPlayerCount) els.lobbyPlayerCount.textContent = playerCount;

        if (els.startGameplayBtn) {
            els.startGameplayBtn.classList.toggle('hidden', playerCount < 1);
        }
        if (els.lobbyPlayersEmpty) {
            els.lobbyPlayersEmpty.classList.toggle('hidden', playerCount > 0);
        }
        if (els.lobbyPlayerChips) {
            els.lobbyPlayerChips.innerHTML = list
                .map(function (p) {
                    var name = typeof p === 'string' ? p : (p.name || p);
                    return '<span class="player-chip"><span class="dot"></span>' + escapeHtml(name) + '</span>';
                })
                .join('');
        }
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
        if (!container) return;
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
                text: url, width: 180, height: 180,
                colorDark: '#0b0e1a', colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M,
            });
        } else {
            container.innerHTML = '<div style="padding:20px;word-break:break-all;font-size:12px;">' + url + '</div>';
        }
    }

    // ---- Admin Join Modal (Start Game flow) ----

    function openAdminJoinModal() {
        if (els.adminJoinModal) els.adminJoinModal.classList.remove('hidden');
        if (els.adminNameInput) {
            els.adminNameInput.value = '';
            setTimeout(function() { els.adminNameInput.focus(); }, 100);
        }
        if (els.adminJoinBtn) els.adminJoinBtn.disabled = true;
    }

    function closeAdminJoinModal() {
        if (els.adminJoinModal) els.adminJoinModal.classList.add('hidden');
    }

    function doStartGame() {
        var name = els.adminNameInput ? els.adminNameInput.value.trim() : '';
        if (!name) {
            if (els.adminNameInput) {
                els.adminNameInput.style.border = '2px solid #D65858';
                setTimeout(function() { els.adminNameInput.style.border = ''; }, 1500);
            }
            return;
        }

        // Disable button to prevent double-click (#17 in logical review).
        if (els.adminJoinBtn) {
            els.adminJoinBtn.disabled = true;
            els.adminJoinBtn.textContent = '\u25B6\uFE0F Starting\u2026';
        }

        var categoryPayload = selectedCategory === 'mixed'
            ? null
            : selectedCategory === 'multi'
                ? selectedCategories
                : selectedCategory;

        // Pre-save the name so the game_state handler's auto-redirect fires
        // as soon as the server confirms the phase change (#5 in logical
        // review: wait for ACK before navigating).
        sessionStorage.setItem('quizify_admin_name', name);

        send('start_game', {
            category: categoryPayload,
            difficulty: selectedDifficulty === 'mixed' ? null : selectedDifficulty,
            num_rounds: selectedRounds,
            language: selectedLanguage,
        });

        // Safety timeout: if for any reason the server doesn't respond in
        // 3s, fall back to the old behavior so the user isn't stuck.
        setTimeout(function () {
            if (!_redirecting) {
                redirectToPlayer(name);
            }
        }, 3000);

        closeAdminJoinModal();
        // NB: do NOT call redirectToPlayer(name) synchronously here \u2014 the
        // handleGameState handler will call it once the server broadcasts
        // the phase transition, guaranteeing start_game was processed.
    }

    function setupAdminJoinModal() {
        // Participate button — also opens the modal (for admin joining mid-lobby)
        on(els.participateBtn, 'click', openAdminJoinModal);
        on(els.adminCancelBtn, 'click', closeAdminJoinModal);

        var backdrop = els.adminJoinModal ? els.adminJoinModal.querySelector('.modal-backdrop') : null;
        if (backdrop) backdrop.addEventListener('click', closeAdminJoinModal);

        on(els.adminNameInput, 'input', function () {
            var name = this.value.trim();
            if (els.adminJoinBtn) els.adminJoinBtn.disabled = !name || name.length > 20;
        });

        on(els.adminNameInput, 'keydown', function (e) {
            if (e.key === 'Enter' && els.adminJoinBtn && !els.adminJoinBtn.disabled) {
                doStartGame();
            }
        });

        on(els.adminJoinBtn, 'click', doStartGame);
    }

    // ---- Safe event binding helper ----
    function on(id, event, fn) {
        var el = typeof id === 'string' ? document.getElementById(id) : id;
        if (el) el.addEventListener(event, fn);
    }

    // ---- Event listeners ----

    // Setup screen → Lobby screen (shows QR, waits for players)
    on(els.startGameBtn, 'click', function () {
        showView('lobby');
        initJoinUrl();
    });

    // Lobby: Start Gameplay button → open modal to get admin name, then start
    on(els.startGameplayBtn, 'click', function () {
        openAdminJoinModal();
    });

    // In-game controls (only shown if admin stays on admin.html without redirect)
    // Button-disable on click to prevent double-advance (#17 in logical review).
    // Re-enabled by handleGameState / handleQuestionStarted when the next phase arrives.
    function _debouncedSend(btn, msgType) {
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        send(msgType, {});
        // Re-enable after 1.5s as a safety net (in case server doesn't respond).
        setTimeout(function () { btn.disabled = false; }, 1500);
    }
    on(els.nextQuestionBtn, 'click', function () { _debouncedSend(els.nextQuestionBtn, 'next_question'); });
    on(els.continueBtn, 'click', function () { _debouncedSend(els.continueBtn, 'next_question'); });

    on(els.endGameBtn, 'click', function () {
        var modal = document.getElementById('end-game-modal');
        if (modal) modal.classList.remove('hidden');
    });

    on('end-game-confirm-btn', 'click', function () {
        send('end_game', {});
        var modal = document.getElementById('end-game-modal');
        if (modal) modal.classList.add('hidden');
    });
    on('end-game-cancel-btn', 'click', function () {
        var modal = document.getElementById('end-game-modal');
        if (modal) modal.classList.add('hidden');
    });
    var endBackdrop = document.querySelector('#end-game-modal .modal-backdrop');
    if (endBackdrop) on(endBackdrop, 'click', function () {
        var modal = document.getElementById('end-game-modal');
        if (modal) modal.classList.add('hidden');
    });

    on(els.newGameBtn, 'click', function () { send('reset_game', {}); });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            closeAdminJoinModal();
            var endModal = document.getElementById('end-game-modal');
            if (endModal) endModal.classList.add('hidden');
        }
    });

    // ---- Generate join URL ----
    function initJoinUrl() {
        var joinUrl = window.location.origin + '/quizify/player';
        if (els.joinUrl) els.joinUrl.textContent = joinUrl;
        generateQR(joinUrl);
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
            toast.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#D65858;color:white;padding:10px 20px;border-radius:10px;font-size:0.85rem;z-index:9999;opacity:0;transition:opacity 0.3s;pointer-events:none;';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.style.opacity = '1';
        setTimeout(function () { toast.style.opacity = '0'; }, 3000);
    }

    // ---- Connection status ----
    // Soft Parlor palette: connected = sage (success/correct), warning = sun, error = warm brick.
    function updateConnectionStatus(status) {
        var indicator = document.getElementById('conn-status');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'conn-status';
            indicator.style.cssText = 'position:fixed;bottom:12px;right:12px;display:flex;align-items:center;gap:6px;font-size:0.75rem;color:#6E6A5C;z-index:100;';
            document.body.appendChild(indicator);
        }
        var colors = { connected: '#7FA897', reconnecting: '#E8C47F', disconnected: '#D66A6A' };
        var glow = { connected: 'rgba(127,168,151,0.45)', reconnecting: 'rgba(232,196,127,0.45)', disconnected: 'rgba(214,106,106,0.45)' };
        var color = colors[status] || '#6E6A5C';
        var glowColor = glow[status] || 'rgba(110,106,92,0.25)';
        indicator.innerHTML = '<span style="width:10px;height:10px;border-radius:50%;display:inline-block;background:' +
            color + ';box-shadow:0 0 10px ' + glowColor + ';"></span>';
    }

    // ---- Update modal title for start game flow ----
    var modalTitle = document.getElementById('admin-join-modal-title');
    if (modalTitle) modalTitle.textContent = 'Spiel starten';
    var modalSubtitle = els.adminJoinModal ? els.adminJoinModal.querySelector('p') : null;
    if (modalSubtitle) modalSubtitle.textContent = 'Wie heißt du? Du spielst mit und kannst das Spiel steuern.';
    if (els.adminJoinBtn) els.adminJoinBtn.textContent = '▶️ Starten & Beitreten';

    // ---- Init ----
    setupAdminJoinModal();

    if (window.QuizifyI18n) {
        QuizifyI18n.init().then(function () {
            QuizifyI18n.initPageTranslations();
        });
    }

    connect();
    updateConnectionStatus('reconnecting');

    // ---- Question pack update check ----
    checkPackUpdates();
})();

/**
 * Fetch /api/quizify/packs/updates and show a banner if any packs have updates.
 * Runs once on page load; silently does nothing if the request fails or GitHub
 * is unreachable (e.g. fully offline HA setup).
 */
async function checkPackUpdates() {
    try {
        const resp = await fetch('/api/quizify/packs/updates');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.upstream_available || !data.updates || data.updates.length === 0) return;

        const updates = data.updates;
        const names = updates.map(u => u.name + ' (' + u.installed_version + ' → ' + u.upstream_version + ')').join(', ');

        // Build banner
        const banner = document.createElement('div');
        banner.id = 'pack-update-banner';
        // Soft Parlor: white surface + coral left-accent + warm ink text on cream-ground page
        banner.style.cssText = [
            'background:#FFFFFF',
            'border:1px solid #E5DFCF',
            'border-left:3px solid #E88A7F',
            'border-radius:10px',
            'padding:12px 16px',
            'margin:12px 0',
            'display:flex',
            'align-items:flex-start',
            'gap:10px',
            'font-size:0.9rem',
            'color:#2A2820',
            'box-shadow:0 2px 8px rgba(42, 40, 32, 0.08)',
            'position:relative',
        ].join(';');

        banner.innerHTML =
            '<span style="font-size:1.2rem;flex-shrink:0">📦</span>' +
            '<div style="flex:1">' +
                '<strong style="color:#E88A7F;font-family:\'Cabinet Grotesk\',sans-serif;font-weight:700">Question pack updates available</strong>' +
                '<div style="margin-top:3px;color:#2A2820">' + names + '</div>' +
                '<div style="margin-top:5px;font-size:0.8rem;color:#6E6A5C">' +
                    'Update your packs by replacing the JSON files in ' +
                    '<code style="background:#F3EEDF;padding:1px 4px;border-radius:3px;font-family:\'JetBrains Mono\',monospace;color:#2A2820">custom_components/quizify/questions/</code>' +
                    ' and restarting Home Assistant.' +
                '</div>' +
            '</div>' +
            '<button onclick="document.getElementById(\'pack-update-banner\').remove()" ' +
                'style="background:none;border:none;color:#6E6A5C;cursor:pointer;font-size:1rem;padding:0;flex-shrink:0" ' +
                'title="Dismiss">✕</button>';

        // Insert at top of setup screen, before first section
        const setupScreen = document.getElementById('setup-screen');
        if (setupScreen) {
            setupScreen.insertBefore(banner, setupScreen.firstChild);
        }
    } catch (_e) {
        // Silently ignore — offline or HA not ready
    }
}
