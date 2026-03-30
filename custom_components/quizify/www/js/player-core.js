/**
 * Quizify Player - Core Module (Entry Point)
 * WebSocket connection, session management, state routing, reconnect logic
 */

(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var lobby = window.QuizifyPlayerLobby;
    var state = pu.state;

    // ============================================
    // Components (from global scripts)
    // ============================================

    var timerBar = new TimerBar('timer-bar', 15);
    var answerBtns = new AnswerButtons('answers-container');
    var revealBtns = new AnswerButtons('reveal-answers');
    var funFact = new FunFactReveal('fun-fact');

    // ============================================
    // DOM Refs
    // ============================================

    var els = {
        nameInput: document.getElementById('name-input'),
        joinBtn: document.getElementById('join-btn'),
        joinSection: document.getElementById('join-section'),
        waitingSection: document.getElementById('waiting-section'),
        timerBarContainer: document.getElementById('timer-bar'),
        roundIndicator: document.getElementById('round-indicator'),
        questionText: document.getElementById('question-text'),
        questionCategory: document.getElementById('question-category'),
        powerupBar: document.getElementById('powerup-bar'),
        powerupBtn: document.getElementById('powerup-btn'),
        powerupIcon: document.getElementById('powerup-icon'),
        powerupLabel: document.getElementById('powerup-label'),
        revealQuestion: document.getElementById('reveal-question'),
        pointsEarned: document.getElementById('points-earned'),
        scoreDetail: document.getElementById('score-detail'),
        streakBadge: document.getElementById('streak-badge'),
        rankDisplay: document.getElementById('rank-display'),
        podium: document.getElementById('podium'),
        finaleLeaderboard: document.getElementById('finale-leaderboard')
    };

    // ============================================
    // Local State
    // ============================================

    var currentQuestion = null;
    var myPowerUp = null;

    // ============================================
    // Send Helper
    // ============================================

    function send(type, payload) {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify(Object.assign({ type: type }, payload || {})));
        }
    }

    // ============================================
    // Connect WebSocket
    // ============================================

    function connect() {
        state.ws = pu.createWebSocket('/api/quizify/ws', {
            onOpen: function () {
                // Try session-based reconnect first
                var session = pu.getSession();
                if (session.token && session.name) {
                    send('reconnect', { session_token: session.token, name: session.name });
                } else if (state.playerName) {
                    var joinMsg = { name: state.playerName };
                    if (state.isAdmin) joinMsg.is_admin = true;
                    send('join', joinMsg);
                }
            },
            onMessage: handleMessage,
            onReconnect: connect,
            onClose: function () {
                state.ws = null;
            }
        });
    }

    // ============================================
    // Message Router
    // ============================================

    function handleMessage(msg) {
        switch (msg.type || msg.event) {
            case 'game_state':
                handleGameState(msg);
                break;

            case 'joined':
                state.playerName = msg.player_id || state.playerName;
                state.playerId = msg.player_id;
                if (msg.session_token) {
                    state.sessionToken = msg.session_token;
                    pu.saveSession(msg.session_token, state.playerName);
                }
                if (els.joinSection) els.joinSection.classList.add('hidden');
                if (els.waitingSection) els.waitingSection.classList.remove('hidden');
                break;

            case 'reconnected':
                state.playerName = msg.player_id || state.playerName;
                state.playerId = msg.player_id;
                if (msg.session_token) {
                    state.sessionToken = msg.session_token;
                    pu.saveSession(msg.session_token, state.playerName);
                }
                if (els.joinSection) els.joinSection.classList.add('hidden');
                if (els.waitingSection) els.waitingSection.classList.remove('hidden');
                break;

            case 'reconnect_failed':
                pu.clearSession();
                state.sessionToken = null;
                break;

            case 'player_joined':
                lobby.handlePlayerJoined(msg);
                break;

            case 'player_left':
                lobby.handlePlayerLeft(msg);
                break;

            case 'question_started':
                handleQuestionStarted(msg);
                break;

            case 'timer_tick':
                timerBar.update(msg.remaining);
                break;

            case 'answer_result':
                handleAnswerResult(msg);
                break;

            case 'round_summary':
                handleRoundSummary(msg);
                break;

            case 'finale':
                handleFinale(msg);
                break;

            case 'powerup_assigned':
                myPowerUp = msg.powerup_type;
                break;

            case 'powerup_applied':
                handlePowerUpApplied(msg);
                break;

            case 'error':
                handleError(msg);
                break;
        }
    }

    // ============================================
    // Game State Handler
    // ============================================

    function handleGameState(msg) {
        state.currentPhase = msg.phase;
        if (msg.players) lobby.handlePlayerJoined(msg);

        switch (msg.phase) {
            case 'LOBBY':
                pu.showView('lobby-view');
                lobby.renderLobby(msg);
                break;

            case 'QUESTION_ACTIVE':
                if (msg.question) {
                    handleQuestionStarted({
                        question_text: msg.question.text,
                        answers: msg.question.answers,
                        timer_duration: msg.question.time_limit,
                        round_num: msg.round,
                        total_rounds: msg.total_rounds,
                        category: msg.question.category
                    });
                }
                break;

            case 'ANSWER_REVEAL':
                if (msg.round_summary) {
                    pu.showView('reveal-view');
                }
                break;

            case 'FINALE':
                handleFinale(msg);
                break;
        }
    }

    // ============================================
    // Question Started
    // ============================================

    function handleQuestionStarted(msg) {
        state.currentPhase = 'QUESTION_ACTIVE';
        currentQuestion = msg;

        pu.showView('game-view');
        if (els.timerBarContainer) els.timerBarContainer.classList.remove('hidden');

        // Round indicator
        if (els.roundIndicator) {
            els.roundIndicator.textContent = 'Frage ' + msg.round_num + ' / ' + msg.total_rounds;
        }
        if (els.questionText) els.questionText.textContent = msg.question_text;
        if (els.questionCategory) els.questionCategory.textContent = msg.category || '';

        // Timer
        timerBar.start(msg.timer_duration);

        // Answers
        var answers = msg.answers.map(function (text, i) { return { text: text, index: i }; });
        answerBtns.setAnswers(answers);

        // Power-up
        if (myPowerUp) {
            if (els.powerupBar) els.powerupBar.classList.remove('hidden');
            renderPowerUp(myPowerUp);
        } else {
            if (els.powerupBar) els.powerupBar.classList.add('hidden');
        }
    }

    // ============================================
    // Answer Result
    // ============================================

    function handleAnswerResult(msg) {
        window._lastResult = msg;
    }

    // ============================================
    // Round Summary
    // ============================================

    function handleRoundSummary(msg) {
        state.currentPhase = 'ANSWER_REVEAL';
        timerBar.stop();
        if (els.timerBarContainer) els.timerBarContainer.classList.add('hidden');
        pu.showView('reveal-view');

        // Question echo
        if (currentQuestion) {
            if (els.revealQuestion) els.revealQuestion.textContent = currentQuestion.question_text;

            var answers = currentQuestion.answers.map(function (text, i) { return { text: text, index: i }; });
            revealBtns.setAnswers(answers);
            revealBtns.showResult(msg.correct_answer_index != null ? msg.correct_answer_index : -1);
        }

        // Score
        var result = window._lastResult || {};
        var pts = result.points_earned || 0;
        if (els.pointsEarned) {
            els.pointsEarned.textContent = pts > 0 ? '+' + pts : '0';
            els.pointsEarned.classList.toggle('zero', pts === 0);
        }

        if (els.scoreDetail) {
            els.scoreDetail.textContent = result.correct
                ? 'Richtig!'
                : 'Falsch \u2014 die Antwort war: ' + (msg.correct_answer || '');
        }

        // Streak
        if (els.streakBadge) {
            if (result.new_streak && result.new_streak > 1) {
                els.streakBadge.classList.remove('hidden');
                els.streakBadge.textContent = result.new_streak + 'x Serie';
            } else {
                els.streakBadge.classList.add('hidden');
            }
        }

        // Rank
        if (msg.leaderboard && state.playerName && els.rankDisplay) {
            for (var i = 0; i < msg.leaderboard.length; i++) {
                if (msg.leaderboard[i].name === state.playerName) {
                    els.rankDisplay.textContent = 'Platz ' + msg.leaderboard[i].rank + ' \u2014 ' + msg.leaderboard[i].score + ' Punkte';
                    break;
                }
            }
        }

        // Fun fact
        if (msg.fun_fact) {
            funFact.show(msg.fun_fact);
        }

        myPowerUp = null;
    }

    // ============================================
    // Finale
    // ============================================

    function handleFinale(msg) {
        state.currentPhase = 'FINALE';
        timerBar.stop();
        if (els.timerBarContainer) els.timerBarContainer.classList.add('hidden');
        pu.showView('end-view');

        // Podium
        var podium = msg.podium || [];
        renderPodium(podium);

        // Full leaderboard
        var lb = msg.leaderboard || msg.all_players || [];
        pu.renderLeaderboard(els.finaleLeaderboard, lb, state.playerName);

        // Awards / Superlatives
        renderAwards(msg.superlatives || []);

        // Share card
        var shareTexts = msg.share_texts || {};
        var myShareText = shareTexts[state.playerName];
        var copyBtn = document.getElementById('copy-result-btn');
        if (copyBtn && myShareText) {
            copyBtn.style.display = 'inline-flex';
            copyBtn.onclick = function () {
                navigator.clipboard.writeText(myShareText).then(function () {
                    copyBtn.textContent = 'Kopiert!';
                    setTimeout(function () { copyBtn.textContent = 'Ergebnis kopieren'; }, 2000);
                }).catch(function () {});
            };
        }
    }

    // ============================================
    // Podium
    // ============================================

    function renderPodium(podium) {
        if (!els.podium) return;
        // Reorder: [2nd, 1st, 3rd]
        var ordered = [];
        if (podium[1]) ordered.push(Object.assign({}, podium[1], { place: 2 }));
        if (podium[0]) ordered.push(Object.assign({}, podium[0], { place: 1 }));
        if (podium[2]) ordered.push(Object.assign({}, podium[2], { place: 3 }));

        var medals = { 1: '\uD83E\uDD47', 2: '\uD83E\uDD48', 3: '\uD83E\uDD49' };
        var barClass = { 1: 'first', 2: 'second', 3: 'third' };

        els.podium.innerHTML = ordered
            .map(function (p) {
                return '<div class="podium-place">' +
                    '<div class="podium-avatar">' + (medals[p.place] || '') + '</div>' +
                    '<div class="podium-name">' + pu.escapeHtml(p.name) + '</div>' +
                    '<div class="podium-score">' + p.score + ' Pkt.</div>' +
                    '<div class="podium-bar ' + (barClass[p.place] || '') + '">' + p.place + '</div>' +
                    '</div>';
            })
            .join('');
    }

    // ============================================
    // Awards
    // ============================================

    function renderAwards(superlatives) {
        var section = document.getElementById('awards-section');
        if (!section || !superlatives || !superlatives.length) {
            if (section) section.classList.add('hidden');
            return;
        }
        section.classList.remove('hidden');
        section.innerHTML = '<div class="awards-title">Awards</div><div class="awards-grid"></div>';
        var grid = section.querySelector('.awards-grid');

        superlatives.forEach(function (s, i) {
            var card = document.createElement('div');
            card.className = 'award-card';
            card.style.animationDelay = (i * 0.5) + 's';
            card.innerHTML =
                '<div class="award-icon">' + s.icon + '</div>' +
                '<div class="award-name">' + pu.escapeHtml(s.award) + '</div>' +
                '<div class="award-winner">' + pu.escapeHtml(s.winner) + '</div>' +
                '<div class="award-detail">' + pu.escapeHtml(s.detail) + '</div>';
            grid.appendChild(card);
        });
    }

    // ============================================
    // Power-ups
    // ============================================

    var POWERUP_META = {
        joker: { icon: '\uD83C\uDCCF', label: 'Joker' },
        double_points: { icon: '2x', label: 'Doppelt' },
        freeze: { icon: '\uD83E\uDDCA', label: 'Einfrieren' },
        time_boost: { icon: '\u23F1\uFE0F', label: '+5 Sek.' }
    };

    function renderPowerUp(type) {
        var meta = POWERUP_META[type] || { icon: '?', label: type };
        if (els.powerupIcon) els.powerupIcon.textContent = meta.icon;
        if (els.powerupLabel) els.powerupLabel.textContent = meta.label;
        if (els.powerupBtn) els.powerupBtn.classList.remove('used');
    }

    function handlePowerUpApplied(msg) {
        if (msg.powerup_type === 'joker' && msg.joker_remove_index != null) {
            answerBtns.eliminate(msg.joker_remove_index);
        }
        myPowerUp = null;
        if (els.powerupBtn) els.powerupBtn.classList.add('used');
        if (els.powerupBar) els.powerupBar.classList.add('hidden');
    }

    // ============================================
    // Error Handler
    // ============================================

    function handleError(msg) {
        console.warn('[Quizify] Error:', msg.code, msg.message);
        pu.showToast(msg.message || msg.code);
    }

    // ============================================
    // Join Form
    // ============================================

    function setupJoinForm() {
        if (!els.nameInput || !els.joinBtn) return;

        els.nameInput.addEventListener('input', function () {
            var result = pu.validateName(this.value);
            els.joinBtn.disabled = !result.valid;
        });

        els.joinBtn.addEventListener('click', handleJoinClick);

        els.nameInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !els.joinBtn.disabled) {
                els.joinBtn.click();
            }
        });
    }

    function handleJoinClick() {
        var result = pu.validateName(els.nameInput.value);
        if (!result.valid) return;

        state.playerName = result.name;
        els.joinBtn.disabled = true;
        els.joinBtn.textContent = 'Joining...';

        if (els.joinSection) els.joinSection.classList.add('hidden');
        if (els.waitingSection) els.waitingSection.classList.remove('hidden');

        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            send('join', { name: result.name });
        }
    }

    // ============================================
    // Retry Connection
    // ============================================

    function setupRetryConnection() {
        var retryBtn = document.getElementById('retry-connection-btn');
        if (retryBtn) {
            retryBtn.addEventListener('click', function () {
                state.reconnectAttempts = 0;
                pu.showView('loading-view');
                connect();
            });
        }
    }

    // ============================================
    // Initialization
    // ============================================

    function init() {
        setupJoinForm();
        lobby.init(send);
        setupRetryConnection();

        // Answer callback
        answerBtns.onAnswer(function (index) {
            send('submit_answer', { answer_index: index });
        });

        // Power-up button
        if (els.powerupBtn) {
            els.powerupBtn.addEventListener('click', function () {
                if (!myPowerUp) return;
                send('use_powerup', { target_player_id: null });
            });
        }

        // Timer expired
        timerBar.onExpired(function () {
            answerBtns.disable();
        });

        // Auto-fill name from URL param ?name=... (admin self-join)
        var urlParams = new URLSearchParams(location.search);
        var prefilledName = urlParams.get('name');
        if (prefilledName && els.nameInput) {
            els.nameInput.value = prefilledName;
            els.joinBtn.disabled = false;
        }

        // i18n init
        if (window.QuizifyI18n) {
            QuizifyI18n.init().then(function () {
                QuizifyI18n.initPageTranslations();
            });
        }

        // Connect WebSocket
        pu.updateConnectionIndicator('reconnecting');
        connect();

        // Auto-join if name was pre-filled
        if (prefilledName) {
            var autoJoinInterval = setInterval(function () {
                if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                    clearInterval(autoJoinInterval);
                    els.joinBtn.click();
                }
            }, 200);
            setTimeout(function () { clearInterval(autoJoinInterval); }, 5000);
        }

        // iOS Safari reconnect on foreground
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') {
                var ws = state.ws;
                if (!ws || ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED) {
                    if (state.playerName) {
                        console.log('[Quizify] Page visible, WS dead \u2014 reconnecting.');
                        state.reconnectAttempts = 0;
                        connect();
                    }
                }
            }
        });

        // Refresh / retry buttons
        var refreshBtn = document.getElementById('refresh-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', function () {
                pu.showView('loading-view');
                connect();
            });
        }
        var retryBtn = document.getElementById('retry-btn');
        if (retryBtn) {
            retryBtn.addEventListener('click', function () {
                pu.showView('loading-view');
                connect();
            });
        }
    }

    // ============================================
    // Boot
    // ============================================

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // ============================================
    // Export
    // ============================================

    window.QuizifyPlayer = {
        init: init,
        send: send
    };

})();
