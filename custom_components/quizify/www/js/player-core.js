/**
 * Quizify Player - Core Module (Entry Point)
 * WebSocket connection, session management, state routing, reconnect logic
 */

(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var lobby = window.QuizifyPlayerLobby;
    var game = window.QuizifyPlayerGame;
    var reveal = window.QuizifyPlayerReveal;
    var end = window.QuizifyPlayerEnd;
    var state = pu.state;

    // ============================================
    // DOM Refs
    // ============================================

    var els = {
        nameInput: document.getElementById('name-input'),
        joinBtn: document.getElementById('join-btn')
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

    var _wsOpenTimeout = null;

    function connect() {
        state.ws = pu.createWebSocket('/api/quizify/ws', {
            onOpen: function () {
                // Clear the connection timeout
                if (_wsOpenTimeout) { clearTimeout(_wsOpenTimeout); _wsOpenTimeout = null; }

                // Re-enable join button once WS is open
                if (els.joinBtn && !state.playerName) {
                    var nameVal = els.nameInput ? els.nameInput.value.trim() : '';
                    els.joinBtn.disabled = nameVal.length === 0;
                    if (els.joinBtn.textContent === 'Retry Connection') {
                        els.joinBtn.textContent = 'Join Game';
                    }
                }

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
            case 'reconnected':
                state.playerName = msg.player_id || state.playerName;
                state.playerId = msg.player_id;
                if (msg.session_token) {
                    state.sessionToken = msg.session_token;
                    pu.saveSession(msg.session_token, state.playerName);
                }
                if (msg.is_admin) state.isAdmin = true;
                // Request full state so we switch to the correct view immediately
                send('get_state', {});
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
                game.updateTimer(msg.remaining);
                break;

            case 'answer_result':
                game.handleAnswerResult(msg);
                // Streak milestone toast
                if (msg.correct && msg.new_streak) {
                    var milestones = { 3: '🔥 3 in a row!', 5: '🔥🔥 5 in a row!', 7: '🔥🔥🔥 7 in a row! On fire!' };
                    if (milestones[msg.new_streak]) {
                        pu.showToast(milestones[msg.new_streak], 2500);
                    }
                }
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

            case 'rematch_started':
                state.reconnectAttempts = 0;
                connect();
                break;

            case 'reaction':
                showFloatingReaction(msg.emoji, msg.player_name);
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
                if (!state.playerName) {
                    pu.showView('join-view');
                } else {
                    pu.showView('lobby-view');
                    lobby.renderLobby(msg);
                }
                break;

            case 'QUESTION_ACTIVE':
            case 'PLAYING':
                if (msg.question) {
                    handleQuestionStarted({
                        question_text: msg.question.text,
                        answers: msg.question.answers,
                        // Use time_remaining for mid-round joiners, fall back to time_limit
                        timer_duration: msg.question.time_remaining || msg.question.time_limit,
                        round_num: msg.round,
                        total_rounds: msg.total_rounds,
                        category: msg.question.category
                    });
                } else {
                    pu.showView('game-view');
                }
                break;

            case 'ANSWER_REVEAL':
            case 'REVEAL':
                pu.showView('reveal-view');
                reveal.updateRevealView(msg);
                break;

            case 'FINALE':
            case 'END':
                handleFinale(msg);
                break;

            case 'PAUSED':
                pu.showView('paused-view');
                updatePausedView(msg);
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

        // Render question and answers
        game.renderQuestion(msg);
        game.resetSubmissionState();

        // Round indicator
        var currentRound = document.getElementById('current-round');
        var totalRounds = document.getElementById('total-rounds');
        if (currentRound) currentRound.textContent = msg.round_num || 1;
        if (totalRounds) totalRounds.textContent = msg.total_rounds || 10;

        // Timer
        if (msg.timer_duration) {
            var deadline = Date.now() + (msg.timer_duration * 1000);
            game.startCountdown(deadline);
        }

        // Power-up
        game.renderPowerUp(myPowerUp);

        // Admin control bar during QUESTION_ACTIVE: End only (no Next)
        var adminBar = document.getElementById('admin-control-bar');
        if (adminBar) {
            adminBar.classList.toggle('hidden', !state.isAdmin);
        }
        var nextRoundAdminBtn = document.getElementById('next-round-admin-btn');
        var skipBtn = document.getElementById('skip-question-btn');
        if (nextRoundAdminBtn) nextRoundAdminBtn.classList.add('hidden');
        if (skipBtn) skipBtn.classList.add('hidden');

        // Hide reaction bar during game
        var reactionBar = document.getElementById('reaction-bar');
        if (reactionBar) reactionBar.classList.add('hidden');
    }

    // ============================================
    // Round Summary
    // ============================================

    function handleRoundSummary(msg) {
        state.currentPhase = 'ANSWER_REVEAL';
        game.stopCountdown();
        pu.showView('reveal-view');

        // Pass question context to reveal
        if (currentQuestion) {
            msg.question_text = currentQuestion.question_text;
            msg.question = { text: currentQuestion.question_text };
        }

        // Enrich all_answers with last answer result (speed/streak breakdown) for current player
        var lastResult = game.getLastAnswerResult ? game.getLastAnswerResult() : null;
        if (lastResult && msg.all_answers) {
            msg.all_answers = msg.all_answers.map(function(a) {
                if (a.player_name === state.playerName) {
                    return Object.assign({}, a, {
                        speed_bonus: lastResult.speed_bonus || 0,
                        streak_bonus: lastResult.streak_bonus || 0,
                        difficulty_multiplier: lastResult.difficulty_multiplier || 1.0,
                        round_score: lastResult.points_earned || 0,
                        streak: lastResult.new_streak || 0,
                    });
                }
                return a;
            });
        }

        reveal.updateRevealView(msg);

        game.clearLastAnswerResult();
        myPowerUp = null;

        // Show reaction bar during reveal
        var reactionBar = document.getElementById('reaction-bar');
        if (reactionBar) reactionBar.classList.remove('hidden');

        // Admin control bar during ANSWER_REVEAL: End only (Next Round is in reveal card)
        var adminBar = document.getElementById('admin-control-bar');
        if (adminBar) {
            adminBar.classList.toggle('hidden', !state.isAdmin);
        }
        var nextRoundAdminBtn2 = document.getElementById('next-round-admin-btn');
        var skipBtn2 = document.getElementById('skip-question-btn');
        if (nextRoundAdminBtn2) nextRoundAdminBtn2.classList.add('hidden');
        if (skipBtn2) skipBtn2.classList.add('hidden');
    }

    // ============================================
    // Finale
    // ============================================

    function handleFinale(msg) {
        state.currentPhase = 'FINALE';
        game.stopCountdown();
        pu.showView('end-view');

        end.updateEndView(msg);
        end.setupRematchButton(send);
        end.setupNewGameButton();

        // Hide bars
        var reactionBar = document.getElementById('reaction-bar');
        if (reactionBar) reactionBar.classList.add('hidden');
        var adminBar = document.getElementById('admin-control-bar');
        if (adminBar) adminBar.classList.add('hidden');
    }

    // ============================================
    // Paused View
    // ============================================

    function updatePausedView(data) {
        var messageEl = document.getElementById('pause-message');
        if (messageEl) {
            if (data.pause_reason === 'admin_disconnected') {
                messageEl.textContent = 'Waiting for host to reconnect...';
            } else {
                messageEl.textContent = 'Game paused';
            }
        }
    }

    // ============================================
    // Power-ups
    // ============================================

    function handlePowerUpApplied(msg) {
        if (msg.powerup_type === 'joker' && msg.joker_remove_index != null) {
            game.applyJoker(msg.joker_remove_index);
        } else if (msg.powerup_type === 'steal') {
            var pts = msg.stolen_points || 0;
            if (msg.source_player === state.playerName) {
                pu.showToast('🥷 Stolen +' + pts + ' pts from ' + (msg.target_player || 'opponent') + '!', 2500);
            } else if (msg.target_player === state.playerName) {
                pu.showToast('🥷 ' + (msg.source_player || 'Someone') + ' stole ' + pts + ' pts from you!', 2500);
            }
        } else if (msg.powerup_type === 'freeze' && msg.target_player === state.playerName) {
            pu.showToast('🧊 You were frozen for 5s!', 2000);
        }
        myPowerUp = null;
        var powerupBtn = document.getElementById('powerup-btn');
        if (powerupBtn) {
            powerupBtn.classList.add('used');
        }
    }

    // ============================================
    // Reactions
    // ============================================

    function showFloatingReaction(emoji, playerName) {
        var container = document.getElementById('reaction-container');
        if (!container) return;

        var el = document.createElement('div');
        el.className = 'floating-reaction';
        el.textContent = emoji;
        el.style.left = (20 + Math.random() * 60) + '%';
        container.appendChild(el);

        setTimeout(function () {
            if (el.parentNode) el.parentNode.removeChild(el);
        }, 2000);
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

        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            var joinMsg = { name: result.name };
            if (state.isAdmin) joinMsg.is_admin = true;
            send('join', joinMsg);
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
    // Admin Controls Wiring
    // ============================================

    function setupAdminControls() {
        // Skip question
        var skipBtn = document.getElementById('skip-question-btn');
        if (skipBtn) {
            skipBtn.addEventListener('click', function () {
                send('admin_skip', {});
            });
        }

        // Next round (from admin bar)
        var nextRoundAdminBtn = document.getElementById('next-round-admin-btn');
        if (nextRoundAdminBtn) {
            nextRoundAdminBtn.addEventListener('click', function () {
                send('next_round', {});
            });
        }

        // End game
        var endGameBtn = document.getElementById('end-game-btn');
        if (endGameBtn) {
            endGameBtn.addEventListener('click', function () {
                send('end_game', {});
            });
        }

        // Next round (from reveal view)
        var nextRoundBtn = document.getElementById('next-round-btn');
        if (nextRoundBtn) {
            nextRoundBtn.addEventListener('click', function () {
                nextRoundBtn.disabled = true;
                send('next_round', {});
            });
        }
    }

    // ============================================
    // Reaction Bar Wiring
    // ============================================

    function setupReactionBar() {
        var reactionBtns = document.querySelectorAll('.reaction-btn');
        reactionBtns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                var emoji = btn.dataset.emoji;
                if (emoji) {
                    send('reaction', { emoji: emoji });
                    btn.classList.add('reaction-btn--sent');
                    setTimeout(function () {
                        btn.classList.remove('reaction-btn--sent');
                    }, 500);
                }
            });
        });
    }

    // ============================================
    // Initialization
    // ============================================

    function init() {
        setupJoinForm();
        lobby.init(send);
        setupRetryConnection();
        setupAdminControls();
        setupReactionBar();
        pu.setupCollapsibles();

        // Answer button clicks
        var answerButtons = document.getElementById('answer-buttons');
        if (answerButtons) {
            answerButtons.addEventListener('click', function (e) {
                var btn = e.target.closest('.answer-btn');
                if (!btn || btn.disabled) return;
                var index = parseInt(btn.dataset.index, 10);
                if (!isNaN(index)) {
                    game.handleAnswerClick(index, send);
                }
            });
        }

        // Power-up button
        var powerupBtn = document.getElementById('powerup-btn');
        if (powerupBtn) {
            powerupBtn.addEventListener('click', function () {
                if (!myPowerUp) return;
                send('use_powerup', { target_player_id: null });
            });
        }

        // Auto-fill name from URL param ?name=... (admin self-join)
        var urlParams = new URLSearchParams(location.search);
        var prefilledName = urlParams.get('name');
        if (prefilledName && els.nameInput) {
            els.nameInput.value = prefilledName;
            els.joinBtn.disabled = false;
        }

        // Check if admin via URL param
        if (urlParams.get('admin') === 'true') {
            state.isAdmin = true;
        }

        // i18n init
        if (window.QuizifyI18n) {
            QuizifyI18n.init().then(function () {
                QuizifyI18n.initPageTranslations();
            });
        }

        // Show join form immediately — don't wait for WS to render UI.
        // The join button stays disabled until WS is open (set in onOpen).
        // This ensures the player sees a form even on slow connections (e.g. Nabu Casa).
        if (!prefilledName) {
            pu.showView('join-view');
            if (els.joinBtn) els.joinBtn.disabled = true;
        }

        // Connect WebSocket
        pu.updateConnectionIndicator('reconnecting');
        connect();

        // Fallback: if WS hasn't opened after 10s, re-enable join button with error hint
        _wsOpenTimeout = setTimeout(function () {
            if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
                if (els.joinBtn) {
                    els.joinBtn.disabled = false;
                    els.joinBtn.textContent = 'Retry Connection';
                    els.joinBtn.addEventListener('click', function retryOnce() {
                        els.joinBtn.removeEventListener('click', retryOnce);
                        els.joinBtn.disabled = true;
                        els.joinBtn.textContent = 'Join Game';
                        state.reconnectAttempts = 0;
                        connect();
                    }, { once: true });
                }
                pu.updateConnectionIndicator('disconnected');
            }
        }, 10000);

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
