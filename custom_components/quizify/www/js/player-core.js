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
    var lightning = window.QuizifyPlayerLightning;
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

                // Re-enable join button once WS is open. Compare via i18n
                // so a German player whose button reads "Erneut verbinden"
                // also gets flipped back to "Beitreten".
                if (els.joinBtn && !state.playerName) {
                    var tConn = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
                    var nameVal = els.nameInput ? els.nameInput.value.trim() : '';
                    els.joinBtn.disabled = nameVal.length === 0;
                    if (els.joinBtn.textContent === tConn('connection.retryConnection')) {
                        els.joinBtn.textContent = tConn('join.joinButton');
                    }
                }

                // Try session-based reconnect first — but skip for admin
                // self-join (?admin=true) to avoid a stale session token
                // from a prior game blocking the fresh join. The exception
                // is `?reconnect=1`, which the admin lobby sets when it
                // redirects after joining-as-player on the admin WS: the
                // token in sessionStorage was JUST written, so we trust it
                // and reconnect instead of doing a fresh join (which would
                // race against the still-open admin WS and fail with
                // "Name bereits vergeben").
                var session = pu.getSession();
                var params = new URLSearchParams(location.search);
                var isAdminSelfJoin = params.get('admin') === 'true';
                var forceReconnect = params.get('reconnect') === '1';
                var useReconnect = (!isAdminSelfJoin || forceReconnect) && session.token && session.name;
                if (useReconnect) {
                    send('reconnect', { session_token: session.token, name: session.name });
                } else if (state.playerName) {
                    var joinMsg = { name: state.playerName };
                    // Admin self-join: send `is_admin: true` in the join
                    // message. Server trusts the flag (Beatify pattern).
                    // No more cryptographic token threading through join
                    // — the persisted admin token is only validated for
                    // the pure admin-dashboard WS connect, not for player
                    // joins. See DESIGN.md for the trust model.
                    if (isAdminSelfJoin) {
                        state.isAdmin = true;
                    }
                    if (state.isAdmin) joinMsg.is_admin = true;
                    send('join', joinMsg);
                }
                // else: waiting for auto-join interval or user to click join
            },
            onMessage: handleMessage,
            onReconnect: connect,
            onClose: function () {
                state.ws = null;
            }
        });
    }

    // ============================================
    // Document title — reflects current phase so users with multiple
    // tabs open can tell which one is active. Was previously stuck on
    // "Quizify - Join Game" forever.
    // ============================================

    // Snapshot of the most-recent phase/msg so updatePageTitle can fire
    // again once i18n finishes loading. Without this, an admin-self-join
    // redirect that lands while game_state is already QUESTION_ACTIVE
    // would lose its title-update call (i18n.isReady() was still false
    // at that moment) and the tab title stuck on "Quizify — Beitreten"
    // for the entire round.
    var _lastTitlePhase = null;
    var _lastTitleMsg = null;

    // ============================================
    // Screen Wake Lock
    // Phones lock the screen mid-question if the player isn't tapping —
    // by the time they look up the next question is on. Hold a wake
    // lock during QUESTION_ACTIVE only (don't drain battery in lobby
    // or reveal). The lock auto-releases when the tab goes hidden;
    // we re-acquire on visibilitychange below.
    // ============================================
    var _wakeLock = null;

    async function _acquireWakeLock() {
        if (!('wakeLock' in navigator)) return;  // unsupported browser
        if (_wakeLock) return;  // already held
        try {
            _wakeLock = await navigator.wakeLock.request('screen');
            _wakeLock.addEventListener('release', function () {
                // OS released it (tab hidden, low battery, etc.) —
                // clear so visibilitychange handler will re-acquire.
                _wakeLock = null;
            });
        } catch (e) {
            // NotAllowedError on iframes/permissions — silent.
            _wakeLock = null;
        }
    }

    function _releaseWakeLock() {
        if (_wakeLock) {
            try { _wakeLock.release(); } catch (e) { /* ignore */ }
            _wakeLock = null;
        }
    }

    function updatePageTitle(phase, msg) {
        _lastTitlePhase = phase;
        _lastTitleMsg = msg;
        if (!window.QuizifyI18n || !window.QuizifyI18n.isReady()) return;
        var t = window.QuizifyI18n.t;
        var title;
        switch (phase) {
            case 'LOBBY':
                title = state.playerName ? t('page.titleLobby') : t('page.titleJoin');
                break;
            case 'QUESTION_ACTIVE':
            case 'PLAYING':
                title = t('page.titleQuestion', { current: (msg && msg.round) || 1 });
                break;
            case 'ANSWER_REVEAL':
            case 'REVEAL':
                title = t('page.titleReveal');
                break;
            case 'FINALE':
            case 'END':
                title = t('page.titleFinale');
                break;
            default:
                title = t('page.titleJoin');
        }
        if (title && title !== document.title) document.title = title;
    }

    // Re-fire any pending title update once i18n has finished loading.
    function _flushPendingTitle() {
        if (_lastTitlePhase) updatePageTitle(_lastTitlePhase, _lastTitleMsg);
    }

    // ============================================
    // Message Router
    // ============================================

    function handleMessage(msg) {
        switch (msg.type || msg.event) {
            case 'game_state':
                handleGameState(msg);
                break;

            case 'game_reset':
                // Admin wiped the whole session (issue #207). Drop our
                // identity + session token so the now-stale token can't
                // resurrect us, and return to the join screen explicitly.
                // The server also closes our socket right after this, but
                // handling the broadcast directly makes the return to the
                // join screen deterministic instead of relying on the
                // close + reconnect_failed race.
                pu.clearSession();
                state.sessionToken = null;
                state.playerName = null;
                state.playerId = null;
                state.isAdmin = false;
                pu.showView('join-view');
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
                if (msg.color) {
                    state.playerColor = msg.color;
                    // Apply as CSS custom property on root for global use
                    document.documentElement.style.setProperty('--my-player-color', msg.color);
                }
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
                // On the final round, play a brief dramatic flourish + 3·2·1
                // countdown on the big screen BEFORE revealing the question.
                // Pure tension/UX — works regardless of finale type. If timing
                // data is missing or anything goes wrong, we reveal instantly.
                if (isFinalRound(msg)) {
                    playFinaleCountdown(function () { handleQuestionStarted(msg); });
                } else {
                    handleQuestionStarted(msg);
                }
                break;

            case 'timer_tick':
                game.updateTimer(msg.remaining);
                break;

            case 'answer_result':
                game.handleAnswerResult(msg);
                // Streak milestone toast
                if (msg.correct && msg.new_streak) {
                    var streakKeys = { 3: 'game.streakToast3', 5: 'game.streakToast5', 7: 'game.streakToast7' };
                    if (streakKeys[msg.new_streak]) {
                        pu.showToast(t(streakKeys[msg.new_streak]), 2500);
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

            case 'reaction_bonus':
                // Server awarded +1 to one or more correct answerers
                // (gameplay idea #11). If I'm one of the recipients,
                // flash a "+1 from X 🎉" toast. Either way the
                // leaderboard payload updates scores.
                handleReactionBonus(msg);
                break;

            case 'wager_accepted':
                // Server confirmed our final-round wager. Nothing to
                // do — the local UI already collapsed the panel on
                // submit. Useful as a hook for analytics later.
                break;

            // ---- Lightning Round (issue #42) ----
            case 'lightning_splash':
                if (lightning) lightning.handleLightningSplash(msg);
                break;

            case 'lightning_question':
                if (lightning) lightning.handleLightningQuestion(msg);
                break;
            case 'lightning_tick':
                if (lightning) lightning.handleLightningTick(msg);
                break;
            case 'lightning_answer_result':
                if (lightning) lightning.handleLightningAnswerResult(msg);
                break;
            case 'lightning_recap':
                if (lightning) lightning.handleLightningRecap(msg);
                break;

            case 'error':
                handleError(msg);
                break;
        }
    }

    function handleReactionBonus(msg) {
        var to = msg.to_players || [];
        var from = msg.from_player || '';
        if (state.playerName && to.indexOf(state.playerName) !== -1 && from) {
            var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            var toast = document.createElement('div');
            toast.className = 'reaction-bonus-toast';
            toast.textContent = t('wager.bonusFromReaction', { from: from });
            document.body.appendChild(toast);
            setTimeout(function () { toast.remove(); }, 1600);
        }
        // If the server included an updated leaderboard, refresh it.
        if (msg.leaderboard && game && game.updateLeaderboard) {
            game.updateLeaderboard({ leaderboard: msg.leaderboard }, 'reveal-leaderboard-list');
        }
    }

    // ============================================
    // Game State Handler
    // ============================================

    function handleGameState(msg) {
        state.currentPhase = msg.phase;

        // Wake lock: only hold during active question. Cheap battery,
        // doesn't fight the OS on lobby/reveal/finale screens where
        // a sleeping screen costs nothing.
        if (msg.phase === 'QUESTION_ACTIVE' || msg.phase === 'PLAYING') {
            _acquireWakeLock();
        } else {
            _releaseWakeLock();
            // Drop any in-flight finale flourish when leaving an active round
            // (pause / reveal / reset / reconnect into a non-question phase).
            _clearFinaleCountdown();
        }

        // Sync UI language with the server-side game language so a
        // German game shows German labels even if the player's
        // browser is English. Only triggers a reload of translations
        // when the language actually changes.
        if (msg.language && window.QuizifyI18n &&
            window.QuizifyI18n.getLanguage() !== msg.language) {
            window.QuizifyI18n.setLanguage(msg.language).then(function () {
                window.QuizifyI18n.initPageTranslations();
                updatePageTitle(msg.phase, msg);
            });
        } else {
            updatePageTitle(msg.phase, msg);
        }

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

                    // #14: if we're reconnecting mid-round and server thinks
                    // we've already submitted, lock the UI accordingly so we
                    // don't get ERR_ALREADY_SUBMITTED toasts on re-tap.
                    if (msg.leaderboard && state.playerName) {
                        var me = msg.leaderboard.find(function (p) {
                            return p && p.name === state.playerName;
                        });
                        if (me && me.submitted) {
                            game.lockSubmitted();
                        }
                    }
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

            case 'LIGHTNING':
                // Reconnect / fresh snapshot landing into a live lightning
                // round. Server pushes per-question lightning_question events
                // for the live flow; this just gets us onto the right view
                // with the current question if present.
                if (lightning && msg.lightning && msg.lightning.splash_pending) {
                    // Round armed but the intro splash (#201) is still up.
                    lightning.handleLightningSplash({
                        num_questions: msg.lightning.num_questions,
                        seconds_per_question: msg.lightning.seconds_per_question
                    });
                } else if (lightning && msg.lightning) {
                    var lq = msg.lightning.question;
                    lightning.handleLightningQuestion({
                        question_text: lq ? lq.text : '',
                        answers: lq ? lq.answers : [],
                        index: msg.lightning.index,
                        num_questions: msg.lightning.num_questions,
                        seconds: msg.lightning.time_remaining,
                        category: lq ? lq.category : '',
                        image_url: lq ? lq.image_url : ''
                    });
                } else {
                    pu.showView('lightning-view');
                }
                break;

            case 'LIGHTNING_RECAP':
                if (lightning) lightning.handleLightningRecap({ recap: msg.lightning_recap });
                break;

            case 'PAUSED':
                pu.showView('paused-view');
                updatePausedView(msg);
                // Admin's control bar stays visible during pause so they
                // can resume / end. Swap Pause ↔ Resume.
                var adminBarP = document.getElementById('admin-control-bar');
                if (adminBarP) adminBarP.classList.toggle('hidden', !state.isAdmin);
                var pauseBtnP = document.getElementById('pause-game-btn');
                var resumeBtnP = document.getElementById('resume-game-btn');
                if (pauseBtnP) pauseBtnP.classList.add('hidden');
                if (resumeBtnP) resumeBtnP.classList.remove('hidden');
                break;
        }
    }

    // ============================================
    // Question Started
    // ============================================

    // ============================================
    // Finale Countdown (dramatic flourish before the final question)
    // ============================================

    var _finaleCountdownTimers = [];

    // True when this question_started marks the last round. Prefer the
    // explicit server flag; fall back to round_num >= total_rounds so the
    // countdown still fires even if is_final_round is ever absent.
    function isFinalRound(msg) {
        if (!msg) return false;
        if (msg.is_final_round === true) return true;
        var rn = msg.round_num;
        var tr = msg.total_rounds;
        return typeof rn === 'number' && typeof tr === 'number' && tr > 0 && rn >= tr;
    }

    function _clearFinaleCountdown() {
        for (var i = 0; i < _finaleCountdownTimers.length; i++) {
            clearTimeout(_finaleCountdownTimers[i]);
        }
        _finaleCountdownTimers = [];
        var overlay = document.getElementById('finale-countdown-overlay');
        if (overlay) {
            overlay.classList.add('hidden');
            overlay.setAttribute('aria-hidden', 'true');
        }
    }

    // Show "Finale!" then 3 · 2 · 1 (~2.4s total), then run `done`. The reveal
    // is always invoked exactly once, even if something throws — the final
    // question must never be swallowed by this purely cosmetic flourish.
    function playFinaleCountdown(done) {
        var fired = false;
        function finish() {
            if (fired) return;
            fired = true;
            _clearFinaleCountdown();
            try { done(); } catch (e) { /* reveal already in progress */ }
        }

        var overlay = document.getElementById('finale-countdown-overlay');
        var numberEl = document.getElementById('finale-countdown-number');
        if (!overlay || !numberEl) {
            // No overlay element (older cached HTML) — skip straight to reveal.
            finish();
            return;
        }

        try {
            _clearFinaleCountdown();
            numberEl.textContent = '';
            overlay.classList.remove('hidden');
            overlay.setAttribute('aria-hidden', 'false');

            // Flourish shows alone for ~0.7s, then 3 · 2 · 1 at 0.6s each.
            var digits = [3, 2, 1];
            digits.forEach(function (d, idx) {
                _finaleCountdownTimers.push(setTimeout(function () {
                    numberEl.textContent = String(d);
                    // Re-trigger the pop animation by toggling the class.
                    numberEl.classList.remove('tick');
                    // Force reflow so the animation restarts on each digit.
                    void numberEl.offsetWidth;
                    numberEl.classList.add('tick');
                }, 700 + idx * 600));
            });

            // After the last digit finishes, reveal the question.
            _finaleCountdownTimers.push(setTimeout(finish, 700 + digits.length * 600));
        } catch (e) {
            finish();
        }
    }

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

        // Admin control bar during QUESTION_ACTIVE: End + Pause (no Next)
        var adminBar = document.getElementById('admin-control-bar');
        if (adminBar) {
            adminBar.classList.toggle('hidden', !state.isAdmin);
        }
        var nextRoundAdminBtn = document.getElementById('next-round-admin-btn');
        var skipBtn = document.getElementById('skip-question-btn');
        var pauseBtn = document.getElementById('pause-game-btn');
        var resumeBtn = document.getElementById('resume-game-btn');
        if (nextRoundAdminBtn) nextRoundAdminBtn.classList.add('hidden');
        if (skipBtn) skipBtn.classList.add('hidden');
        // QUESTION_ACTIVE → Pause is the relevant CTA, Resume hidden.
        if (pauseBtn) pauseBtn.classList.remove('hidden');
        if (resumeBtn) resumeBtn.classList.add('hidden');

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
        _clearFinaleCountdown();
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

        // Hide the global admin-control-bar (Pause + Ende) on ANSWER_REVEAL.
        // The redesigned result page owns its own sticky bar with Ende +
        // Nächste Runde — Pause makes no sense between rounds, and having
        // two stacked sticky bars covered the standings.
        var adminBar = document.getElementById('admin-control-bar');
        if (adminBar) adminBar.classList.add('hidden');
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
        _clearFinaleCountdown();
        pu.showView('end-view');

        end.updateEndView(msg);
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
        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
        var titleEl = document.getElementById('pause-title');
        var messageEl = document.getElementById('pause-message');
        var isDisconnect = data.pause_reason === 'admin_disconnected';
        var titleKey = isDisconnect ? 'admin.pausedHostDisconnected' : 'admin.pausedTitle';
        var hintKey = isDisconnect ? 'admin.pausedHostHint' : 'admin.pausedHint';
        if (titleEl) titleEl.textContent = t(titleKey);
        if (messageEl) messageEl.textContent = t(hintKey);
    }

    // ============================================
    // Power-ups
    // ============================================

    function handlePowerUpApplied(msg) {
        if (msg.powerup_type === 'joker' && msg.joker_remove_index != null) {
            // Private send to source with the removed-answer index.
            game.applyJoker(msg.joker_remove_index);
        } else if (msg.powerup_type === 'joker' && msg.source_player !== state.playerName) {
            // Public broadcast — surface opponent's joker use to other
            // players (no removed-index since shuffle is per-player).
            var tJk = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            pu.showToast(tJk('game.opponentUsedJoker', { name: msg.source_player }), 2000);
        } else if (msg.powerup_type === 'steal') {
            var tPwr = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            var pts = msg.stolen_points || 0;
            if (msg.source_player === state.playerName) {
                pu.showToast(tPwr('game.stoleFromOpponent', { points: pts, name: msg.target_player || tPwr('lobby.you') }), 2500);
            } else if (msg.target_player === state.playerName) {
                pu.showToast(tPwr('game.stoleFromYou', { name: msg.source_player || tPwr('lobby.you'), points: pts }), 2500);
            }
        } else if (msg.powerup_type === 'freeze' && msg.target_player === state.playerName) {
            var tFrz = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            pu.showToast(tFrz('game.frozen'), 2000);
        }
        // Only the source's local power-up button needs clearing. Previously
        // this was unconditional — for STEAL/FREEZE that meant a third party
        // who happened to hold a power-up would lose their UI state when an
        // unrelated event broadcasted. Now the only player whose myPowerUp
        // gets reset is the one who just used theirs.
        if (msg.source_player === state.playerName) {
            myPowerUp = null;
            var powerupBtn = document.getElementById('powerup-btn');
            if (powerupBtn) {
                powerupBtn.classList.add('used');
            }
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
        // Translate via i18n so the player's chosen locale wins. Previous
        // inline map was German-only \u2014 English-speaking players saw "Aktion
        // nicht erlaubt" regardless of language setting. Lookup pattern:
        //   t('errors.<CODE>') returns the localized string OR the key
        //   itself if missing. We treat key-as-result as "no translation"
        //   and fall back to server message, then to errors.UNKNOWN.
        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
        var key = 'errors.' + (msg.code || 'UNKNOWN');
        var translated = t(key);
        var userMsg;
        if (translated && translated !== key) {
            userMsg = translated;
        } else if (msg.message) {
            userMsg = msg.message;
        } else {
            userMsg = t('errors.UNKNOWN');
        }
        pu.showToast(userMsg);

        // If the error happened during join, reset the button so the user can retry
        if (!state.playerName && els.joinBtn) {
            els.joinBtn.disabled = false;
            els.joinBtn.textContent = t('join.joinButton');
            if (els.nameInput) els.nameInput.style.borderColor = '#D65858';
        }
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
        els.joinBtn.textContent = t('join.joining');

        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            var joinMsg = { name: result.name };
            // Admin self-join: server trusts `is_admin: true` in the
            // join message (Beatify pattern). No token validation in
            // this path — see player-core.js connect() for rationale.
            var isAdminSelfJoin = new URLSearchParams(location.search).get('admin') === 'true';
            if (isAdminSelfJoin) {
                state.isAdmin = true;
            }
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

        // Pause / resume — toggled by handleGameState based on phase.
        var pauseBtn = document.getElementById('pause-game-btn');
        if (pauseBtn) {
            pauseBtn.addEventListener('click', function () { send('pause_game', {}); });
        }
        var resumeBtn = document.getElementById('resume-game-btn');
        if (resumeBtn) {
            resumeBtn.addEventListener('click', function () { send('resume_game', {}); });
        }

        // Next round (from reveal view)
        var nextRoundBtn = document.getElementById('next-round-btn');
        if (nextRoundBtn) {
            nextRoundBtn.addEventListener('click', function () {
                nextRoundBtn.disabled = true;
                send('next_round', {});
            });
        }

        // End game from the reveal page's sticky admin bar. Same server
        // semantics as end-game-btn — added because the redesign moved
        // the admin actions to a bottom-sticky bar on the reveal page.
        var endFromReveal = document.getElementById('end-game-from-reveal-btn');
        if (endFromReveal) {
            endFromReveal.addEventListener('click', function () {
                send('end_game', {});
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

    // ============================================
    // Sound Toggle (header speaker button)
    // ============================================

    function setupSoundToggle() {
        var btn = document.getElementById('sound-toggle-btn');
        var snd = window.QuizifyPlayerSound;
        if (!btn) return;
        if (!snd) { btn.classList.add('hidden'); return; }

        function render() {
            var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            var muted = snd.isMuted();
            btn.textContent = muted ? '🔇' : '🔊';
            btn.classList.toggle('is-muted', muted);
            btn.setAttribute('aria-pressed', muted ? 'true' : 'false');
            var label = muted ? t('game.soundUnmute') : t('game.soundMute');
            btn.setAttribute('aria-label', label);
            btn.setAttribute('title', label);
        }

        btn.addEventListener('click', function () {
            snd.toggleMute();
            render();
        });
        render();
    }

    function init() {
        setupJoinForm();
        lobby.init(send);
        setupRetryConnection();
        setupAdminControls();
        setupReactionBar();
        setupSoundToggle();
        pu.setupCollapsibles();
        if (lightning) { lightning.setSend(send); lightning.init(); }

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

        // Power-up button — freeze/steal open a target picker; the rest fire
        // immediately. Server still falls back to a random opponent if
        // target_player_id is null, so even older clients keep working.
        var powerupBtn = document.getElementById('powerup-btn');
        if (powerupBtn) {
            powerupBtn.addEventListener('click', function () {
                if (!myPowerUp) return;
                if (game && game.powerupNeedsTarget && game.powerupNeedsTarget(myPowerUp)) {
                    game.openTargetPicker(myPowerUp, function (targetName) {
                        send('use_powerup', { target_player_id: targetName });
                    });
                } else {
                    send('use_powerup', { target_player_id: null });
                }
            });
        }

        // Auto-fill name from URL param ?name=... (admin self-join)
        var urlParams = new URLSearchParams(location.search);
        var prefilledName = urlParams.get('name');
        if (prefilledName && els.nameInput) {
            els.nameInput.value = prefilledName;
            els.joinBtn.disabled = false;
            // Set state.playerName so the auto-join path fires on WS open
            // (otherwise the user has to click the button manually).
            state.playerName = prefilledName;
        }

        // Check if admin via URL param
        if (urlParams.get('admin') === 'true') {
            state.isAdmin = true;
        }

        // i18n init
        if (window.QuizifyI18n) {
            QuizifyI18n.init().then(function () {
                QuizifyI18n.initPageTranslations();
                // game_state may have already arrived before i18n loaded;
                // re-fire the title update so we don't sit on the stale
                // "— Beitreten" forever (see updatePageTitle for the why).
                _flushPendingTitle();
            });
        }

        // Always show join-view immediately — don't wait for WS.
        // For pre-filled names (admin self-join), the form is visible but
        // auto-submit fires once WS opens. Prevents a blank screen on slow
        // connections such as Nabu Casa remote access.
        pu.showView('join-view');
        if (els.joinBtn) els.joinBtn.disabled = true;

        // Connect WebSocket
        pu.updateConnectionIndicator('reconnecting');
        connect();

        // Fallback: if WS hasn't opened after 10s, re-enable join button with error hint
        _wsOpenTimeout = setTimeout(function () {
            if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
                if (els.joinBtn) {
                    var tRetry = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
                    els.joinBtn.disabled = false;
                    els.joinBtn.textContent = tRetry('connection.retryConnection');
                    els.joinBtn.addEventListener('click', function retryOnce() {
                        els.joinBtn.removeEventListener('click', retryOnce);
                        els.joinBtn.disabled = true;
                        els.joinBtn.textContent = tRetry('join.joinButton');
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
                // The OS releases wake locks when the tab hides \u2014 re-acquire
                // when the player tabs back in mid-question.
                if (state.currentPhase === 'QUESTION_ACTIVE' || state.currentPhase === 'PLAYING') {
                    _acquireWakeLock();
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

    // #215: hook for sw-update.js — is the player screen idle enough that a
    // service-worker-triggered reload won't interrupt anything? Idle = join,
    // lobby, the per-round reveal, the lightning recap, and the finale end
    // screen. NOT idle during a live question, a live lightning round, or a
    // paused game (resuming reloads straight back into a question).
    window.quizifyIsIdleForReload = function () {
        switch (state.currentPhase) {
            case 'QUESTION_ACTIVE':
            case 'PLAYING':
            case 'LIGHTNING':
            case 'PAUSED':
                return false;
            default:
                // LOBBY, ANSWER_REVEAL/REVEAL, LIGHTNING_RECAP, FINALE/END,
                // and the pre-join state.
                return true;
        }
    };

})();
