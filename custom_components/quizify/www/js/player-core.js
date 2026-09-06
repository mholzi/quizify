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
    var hotSeat = window.QuizifyPlayerHotSeat;
    var team = window.QuizifyPlayerTeam;
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

    // Returns whether the message actually went out (#621).
    //
    // The phone had the same silent drop as the admin page, and here it is
    // worse: a guest taps an answer while the socket is down, the tile lights
    // up, and the round closes without them. Nothing on screen ever said so.
    function send(type, payload) {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify(Object.assign({ type: type }, payload || {})));
            return true;
        }
        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
        if (pu && pu.showToast) pu.showToast(t('connection.reconnecting'), 2500);
        if (window.console && console.warn) {
            console.warn('[quizify] command not sent, socket not open:', type);
        }
        return false;
    }

    // ============================================
    // Connect WebSocket
    // ============================================

    var _wsOpenTimeout = null;

    function connect() {
        // Guard against reconnecting on top of a live socket — a stray
        // connect() while state.ws is OPEN/CONNECTING would orphan the
        // existing socket and double up message handlers.
        if (state.ws && (state.ws.readyState === WebSocket.OPEN ||
                         state.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
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
                    if (state.isAdmin) {
                        joinMsg.is_admin = true;
                        // #358: attach the admin session token (if this tab
                        // holds one) so the server can authorise a crown
                        // transfer from a stale admin slot. Optional + fail-soft
                        // — a missing token just means no auto-crown, never a
                        // rejected join, so the DESIGN.md trust model stands.
                        var _at = QuizifyUtils.readAdminToken();
                        if (_at) joinMsg.admin_token = _at;
                    }
                    // #729: arm the answer timeout for this auto-join too —
                    // a refusal here (a stale name taken mid-game) has to
                    // reach the guest, not vanish into the reconnect loop.
                    beginJoinPending();
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
            case 'WAGER_ACTIVE':
                // #656: without this the betting window fell to the default
                // and the tab said "Join" mid-game.
                title = t('page.titleWager');
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
                endJoinPending();
                pu.clearSession();
                state.sessionToken = null;
                state.playerName = null;
                state.playerId = null;
                state.isAdmin = false;
                // Clear rank-delta memos (issue #257) so the next game's
                // first leaderboard/reveal doesn't show phantom ▲/▼ deltas
                // computed against the wiped game's standings.
                if (game && game.resetRankMemo) game.resetRankMemo();
                if (reveal && reveal.resetRankMemo) reveal.resetRankMemo();
                // #322: kill any in-flight freeze countdown so its interval
                // doesn't leak past a full session wipe.
                if (game && game.stopFrozenOverlay) game.stopFrozenOverlay();
                // #838: the join screen is not a game screen. Everything the
                // game hung outside the view stack goes with the game.
                clearGameChrome();
                pu.showView('join-view');
                break;

            case 'kicked':
                // #750: the host removed us from the lobby. The server sends
                // this and then closes the socket, so the ONLY difference
                // between being removed and losing wifi is this message —
                // without handling it the phone just went quiet.
                //
                // Clearing playerName first also disarms the reconnect ladder
                // in createWebSocket's onclose (it only retries while a name
                // is set), so we don't spend five backoff rounds climbing
                // back into a lobby we were just thrown out of.
                pu.clearSession();
                state.sessionToken = null;
                state.playerName = null;
                state.playerId = null;
                state.isAdmin = false;
                if (game && game.stopFrozenOverlay) game.stopFrozenOverlay();
                if (pu.hideReconnectingOverlay) pu.hideReconnectingOverlay();
                pu.updateConnectionIndicator('disconnected');
                // #838: same leftovers, same screen furniture — the reaction
                // bar and the last toast are fixed to the page, not to the
                // view we are leaving.
                clearGameChrome();
                pu.showView('kicked-view');
                break;

            case 'joined':
            case 'reconnected':
                endJoinPending();
                state.playerName = msg.player_id || state.playerName;
                state.playerId = msg.player_id;
                if (msg.session_token) {
                    state.sessionToken = msg.session_token;
                    pu.saveSession(msg.session_token, state.playerName);
                }
                if (msg.is_admin) state.isAdmin = true;
                // #288: restore the assigned power-up on (re)join. The server
                // sends the current power-up in `msg.powerup`; without this a
                // reconnecting player whose power-up was assigned earlier never
                // sees the button (it was wiped at the previous reveal).
                if (msg.powerup !== undefined) {
                    myPowerUp = msg.powerup;
                    if (game && game.renderPowerUp) game.renderPowerUp(myPowerUp);
                }
                // #371 variant A: own all-time standing, sent once on
                // join/reconnect. `undefined` (older server) leaves whatever
                // is on screen; `null` (first-timer) clears the line.
                if (msg.all_time !== undefined && lobby && lobby.renderAllTime) {
                    lobby.renderAllTime(msg.all_time);
                }
                if (msg.color) {
                    state.playerColor = msg.color;
                    // Apply as CSS custom property on root for global use
                    document.documentElement.style.setProperty('--my-player-color', msg.color);
                }
                // No get_state needed: the server already pushes a per-player
                // PROJECTED game_state snapshot immediately after joined /
                // reconnected (own shuffled answer order, own timer, flat
                // reveal — #253). A redundant get_state would just re-deliver
                // the same projected snapshot (#286). The handleGameState case
                // below switches us to the correct view from that push.
                break;

            case 'reconnect_failed':
                // The stored session token no longer maps to a joinable game
                // (game ended/reset, or token expired). Drop the stale token
                // and route back to the join screen — without this the client
                // is left with every view hidden → blank screen (issue #227,
                // same family as #207/#221). Mirror the deterministic return
                // used by game_reset above.
                pu.clearSession();
                state.sessionToken = null;
                pu.showView('join-view');
                break;

            case 'host_presence':
                // #842: the frame that exists so this phone does not have to
                // guess. An admin-only host arriving or leaving produces
                // nothing else — no roster row, no pause, no phase change —
                // and guessing from the roster is what armed the escape hatch
                // under a host who was sitting right there (#834).
                _rememberHostFlag(msg);
                refreshStageReset();
                break;

            case 'player_joined':
                lobby.handlePlayerJoined(msg);
                _rememberRoster(msg);
                refreshStageReset();
                break;

            case 'player_left':
                lobby.handlePlayerLeft(msg);
                // #803: this is the frame that says the host's phone just
                // died. Nothing else follows it while the room waits on a
                // results screen — no game_state, no tick — so re-deciding
                // the escape hatch here is what catches a host who drops
                // DURING a waiting stage rather than before it.
                _rememberRoster(msg);
                refreshStageReset();
                break;

            // Teams (#365). `teams_update` is the room's view and arrives on
            // every formation change; `team_joined` / `team_left` are the
            // confirmations to the player who acted.
            case 'teams_update':
                if (team) team.handleTeamsUpdate(msg);
                break;

            case 'team_joined':
                if (team) team.handleTeamJoined(msg);
                break;

            case 'team_left':
                if (team) team.handleTeamLeft(msg);
                break;

            case 'team_answer':
                if (team) team.handleTeamAnswer(msg);
                break;

            case 'wager_window':
                // #656: the final round's betting window. Arrives BEFORE the
                // question — that is the whole fix — so there is no flourish
                // to play here; the 3·2·1 still runs on question_started.
                handleWagerWindow(msg);
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

            // #619: who the room is still waiting for. renderSubmissionTracker
            // has existed since the tracker markup landed; its only caller was
            // updateGameView(), which nothing ever invoked, so the row stayed
            // empty for every game ever played.
            // #624: the season standing, sent once the finished game has
            // actually been written to analytics — which happens after the
            // finale, not with it.
            case 'all_time_update':
                if (msg.all_time !== undefined && lobby && lobby.renderAllTime) {
                    lobby.renderAllTime(msg.all_time, 'end-alltime');
                }
                break;

            case 'answer_progress':
                game.renderSubmissionTracker(msg.players);
                break;

            case 'answer_result':
                game.handleAnswerResult(msg);
                // Streak milestone toast
                if (msg.correct && msg.new_streak) {
                    var streakKeys = { 3: 'game.streakToast3', 5: 'game.streakToast5', 7: 'game.streakToast7' };
                    if (streakKeys[msg.new_streak]) {
                        pu.showToast(t(streakKeys[msg.new_streak]), 2500, streakKeys[msg.new_streak]);
                    }
                }
                break;

            case 'round_summary':
                handleRoundSummary(msg);
                // #736: the reveal is the one stretch of the round with an idle
                // network, and the server has told us which picture comes next.
                // Warm it here so `question_started` finds it in cache instead
                // of starting a 21-client burst with the countdown running.
                if (msg.next_image_url && window.QuizifyPlayerGame
                    && QuizifyPlayerGame.preloadNextImage) {
                    QuizifyPlayerGame.preloadNextImage(msg.next_image_url);
                }
                break;

            case 'finale':
                handleFinale(msg);
                break;

            case 'powerup_assigned':
                // #288: render immediately. powerup_assigned arrives AFTER
                // question_started, so handleQuestionStarted's renderPowerUp
                // ran while myPowerUp was still null — only the final round
                // (its flourish delays the render) ever showed the button.
                // Set + render here so the button appears in every round.
                myPowerUp = msg.powerup_type;
                game.renderPowerUp(myPowerUp);
                break;

            case 'powerup_applied':
                handlePowerUpApplied(msg);
                break;

            // (Removed dead 'rematch_started' case: the server's
            // _handle_play_again broadcasts a 'game_state' message, never
            // 'rematch_started', so this branch was unreachable. The rematch
            // flow is driven entirely by the game_state phase transition.)

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

            // ---- Hot Seat auction (issue #616) ----
            case 'hot_seat_auction_you':
                if (hotSeat) hotSeat.handleAuctionYou(msg);
                break;

            case 'hot_seat_bid_count':
                if (hotSeat) hotSeat.handleBidCount(msg);
                break;

            case 'hot_seat_bid_accepted':
            case 'hot_seat_bet_accepted':
            case 'hot_seat_answer_accepted':
                // Server confirmed. The panel already locked itself on tap —
                // this is the acknowledgement, not the state change.
                break;

            case 'hot_seat_awarded':
                if (hotSeat) hotSeat.handleAwarded(msg);
                break;

            case 'hot_seat_no_bids':
                if (hotSeat) hotSeat.handleNoBids();
                break;

            case 'hot_seat_question':
                if (hotSeat) hotSeat.handleQuestion(msg);
                break;

            case 'hot_seat_tick':
                if (hotSeat) hotSeat.handleTick(msg);
                break;

            case 'hot_seat_result':
                if (hotSeat) hotSeat.handleResult(msg);
                // #803: the settlement leaves the room on HOT_SEAT_REVEAL,
                // which only a host tap leaves. If the host was the seat
                // holder and their phone died, the clock settles the stake
                // (#653) and stops here — with no way out for anyone.
                setResetStage('HOT_SEAT_REVEAL');
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
            case 'lightning_team_answer':
                // The team's standing lightning answer (#552) — same idea as
                // `team_answer` in a normal round.
                if (lightning) lightning.handleLightningTeamAnswer(msg);
                break;
            case 'lightning_recap':
                if (lightning) lightning.handleLightningRecap(msg);
                // #803: the recap waits for the host's "Continue game" and
                // nothing else. Same dead-end as the reveal, same hatch.
                setResetStage('LIGHTNING_RECAP');
                break;

            case 'guess_accepted':
                // #275/#750: an estimate round has no per-answer
                // answer_result, so this ack is the only word the server ever
                // says about the guess. The slider greys out on tap to stop a
                // double submit, but the "Submitted!" tick waits for this —
                // otherwise a rejected guess left a confirmed-looking screen
                // over a round the player was never in.
                if (game && game.confirmGuess) game.confirmGuess();
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
            var rbIcon = pu.feedbackIconHtml ? pu.feedbackIconHtml('party', 'coral') : '';
            if (rbIcon) {
                toast.classList.add('toast--with-icon');
                toast.innerHTML = rbIcon + '<span class="toast-text">' + pu.escapeHtml(t('wager.bonusFromReaction', { from: from })) + '</span>';
            } else {
                toast.textContent = t('wager.bonusFromReaction', { from: from });
            }
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

        // Teams ride along on every snapshot (#365), so a phone that
        // reconnects mid-game gets its team indicator back instead of
        // believing it is playing alone.
        if (team && msg.teams) team.handleTeamsUpdate(msg);

        // Keep the in-game leaderboard panel current from ANY game_state that
        // carries a leaderboard (the round-start refresh and the ANSWER_REVEAL
        // broadcast both do). Without this the panel was never fed during a
        // live question — only the reveal's own `reveal-leaderboard-list` was —
        // so #leaderboard-list/#leaderboard-summary sat at "--" mid-round (#235).
        if (msg.leaderboard && game && game.updateLeaderboard) {
            game.updateLeaderboard({ leaderboard: msg.leaderboard }, 'leaderboard-list');
        }

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

        // Sync UI language with the server-side game language so a German game
        // shows German labels even if the player's browser is English — unless
        // this player picked a language themselves (#492). See
        // _syncServerLanguage; it returns false when it did not take over, in
        // which case the title still needs updating here.
        if (!_syncServerLanguage(msg)) {
            updatePageTitle(msg.phase, msg);
        }

        if (msg.players) lobby.handlePlayerJoined(msg);

        // #299/#803: the host-gone reset escape hatch. A snapshot always
        // carries the roster, so this is the authoritative reading; leaving a
        // waiting stage drops the hatch (e.g. the host came back and resumed),
        // and entering one re-arms it if the host is still missing.
        if (msg.phase !== 'PAUSED') disarmResetAffordance('paused-reset-btn');
        // #842: a snapshot is what a phone gets on join, on reconnect and on
        // get_state, so it is where one that was not listening when the host
        // arrived or left catches up.
        _rememberHostFlag(msg);
        _rememberRoster(msg);
        setResetStage(STAGE_RESET_AFFORDANCES[msg.phase] ? msg.phase : null);

        switch (msg.phase) {
            case 'LOBBY':
                if (!state.playerName) {
                    pu.showView('join-view');
                } else {
                    pu.showView('lobby-view');
                    lobby.renderLobby(msg);
                }
                break;

            case 'WAGER_ACTIVE':
                // #656: reconnect (or first snapshot) landing in the betting
                // window. The snapshot carries category and the room's
                // remaining seconds — never the question text, so a phone that
                // drops mid-window cannot come back knowing what it is
                // betting on. The bank comes from the leaderboard, which the
                // snapshot already carries.
                if (msg.wager) {
                    handleWagerWindow({
                        round_num: msg.round,
                        total_rounds: msg.total_rounds,
                        category: msg.wager.category,
                        difficulty: msg.wager.difficulty,
                        window_duration: msg.wager.window_remaining,
                        player_score: _myScore(msg)
                    });
                } else {
                    pu.showView('game-view');
                }
                break;

            case 'QUESTION_ACTIVE':
            case 'PLAYING':
                if (msg.question) {
                    handleQuestionStarted(questionStartedFromSnapshot(msg));

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
                // The hatch itself is armed by setResetStage above, which
                // reads this same phase off the snapshot.
                disarmResetAffordance('paused-reset-btn');
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

            case 'HOT_SEAT_AUCTION':
            case 'HOT_SEAT':
            case 'HOT_SEAT_REVEAL':
                // #664: the detour is driven by one-shot events, so a reload
                // used to fall through to the default case and land on the
                // lobby with the auction still running. For the seat holder
                // that was expensive: an unanswered question forfeits the
                // stake (#653), and they could not answer what they could not
                // see.
                pu.showView('game-view');
                if (hotSeat && msg.hot_seat) hotSeat.restoreFromSnapshot(msg.hot_seat);
                // #697: the detour is entered from ANSWER_REVEAL, which hides
                // the control bar, and nothing here brought it back. A host
                // who plays along — the default, since the admin tab redirects
                // to this page on start — had no Next, no Skip and no End for
                // the rest of the game: HOT_SEAT_REVEAL is left only by an
                // explicit next_round. End and Skip stay; Pause does not,
                // because the detour owns its own clock.
                var adminBarHS = document.getElementById('admin-control-bar');
                if (adminBarHS) adminBarHS.classList.toggle('hidden', !state.isAdmin);
                var nextRoundHS = document.getElementById('next-round-admin-btn');
                var skipHS = document.getElementById('skip-question-btn');
                var pauseHS = document.getElementById('pause-game-btn');
                var resumeHS = document.getElementById('resume-game-btn');
                var inReveal = msg.phase === 'HOT_SEAT_REVEAL';
                if (nextRoundHS) nextRoundHS.classList.toggle('hidden', !inReveal);
                if (skipHS) skipHS.classList.toggle('hidden', inReveal);
                if (pauseHS) pauseHS.classList.add('hidden');
                if (resumeHS) resumeHS.classList.add('hidden');
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

            default:
                // Safety net (issue #227, same reasoning as #221): an unknown
                // or unmapped phase must never leave the player with zero
                // visible views (blank screen). Fall back to the lobby if we
                // already have an identity, otherwise the join screen.
                pu.showView(state.playerName ? 'lobby-view' : 'join-view');
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

    /**
     * This player's score out of a game_state snapshot's leaderboard (#656).
     * The wager slider bets against it, so a reconnect that guessed wrong
     * would show the player a bank they do not have.
     */
    function _myScore(msg) {
        var board = msg.leaderboard || msg.players || [];
        for (var i = 0; i < board.length; i++) {
            if (board[i] && board[i].name === state.playerName) {
                return board[i].score || 0;
            }
        }
        return 0;
    }

    /**
     * A snapshot's ``question`` block, in the shape ``question_started`` has
     * (#730/#731).
     *
     * This used to be an object literal that re-listed, by hand, every field
     * worth forwarding — so every field added to the live payload had to be
     * remembered a second time here, and twice it was not. #275 caught
     * question_type / estimate / image_url only after estimate rounds had been
     * rendering as an A/B/C grid, and reveal_style (#434) was never carried at
     * all: a phone that reloaded during a progressive-reveal question got the
     * picture sharp and could read the answer off it (#731).
     *
     * So it forwards the frame instead of re-listing it. Every key the server
     * puts in ``snapshot.question`` rides along untouched, and only the fields
     * that genuinely differ between "the round just started" and "the round is
     * half over and this phone just came back" are named below. Adding a field
     * to the live path now carries it here for free — and
     * tests/test_snapshot_restore_parity_730_731.py fails if the snapshot ever
     * stops carrying one of them.
     */
    function questionStartedFromSnapshot(msg) {
        var q = msg.question || {};
        var live = {};
        for (var k in q) {
            if (Object.prototype.hasOwnProperty.call(q, k)) live[k] = q[k];
        }
        // The live event's name for the same string.
        live.question_text = q.text;
        // The clock is the one thing a restore must NOT copy: the round is
        // already running, so the countdown gets what is left of it rather
        // than a fresh full round. time_limit is the fallback for a snapshot
        // taken before the phase controller has a deadline.
        live.timer_duration = q.time_remaining || q.time_limit;
        // ...but the progressive blur is a fraction of the WHOLE round
        // (remaining / duration). Handed time_remaining it would compute 1.0
        // and restart the blur at maximum instead of resuming it two thirds
        // of the way through — which is a different bug, not a fix (#731).
        live.reveal_duration = q.time_limit || q.time_remaining;
        // Not in the question block: the snapshot carries these one level up.
        live.round_num = msg.round;
        live.total_rounds = msg.total_rounds;
        live.player_score = _myScore(msg);
        return live;
    }

    /**
     * The final round's betting window (#656).
     *
     * Same view as the question, minus the question: category, bank, slider,
     * and the window's own countdown. Nothing here starts the answer clock —
     * that begins with the question_started that follows.
     */
    function handleWagerWindow(msg) {
        state.currentPhase = 'WAGER_ACTIVE';
        pu.showView('game-view');
        game.resetSubmissionState();
        game.stopFrozenOverlay();
        if (team) team.resetRound();

        var currentRound = document.getElementById('current-round');
        var totalRounds = document.getElementById('total-rounds');
        if (currentRound) currentRound.textContent = msg.round_num || 1;
        if (totalRounds) totalRounds.textContent = msg.total_rounds || 10;

        var banner = document.getElementById('last-round-banner');
        if (banner) banner.classList.remove('hidden');

        // Admin control bar: End + Skip, no Pause. Pausing a window that has
        // no timer does nothing (the backend refuses it), and Skip is the one
        // useful host action here — it closes the window and asks the
        // question instead of waiting the deadline out.
        var adminBar = document.getElementById('admin-control-bar');
        if (adminBar) adminBar.classList.toggle('hidden', !state.isAdmin);
        var nextRoundAdminBtn = document.getElementById('next-round-admin-btn');
        var skipBtn = document.getElementById('skip-question-btn');
        var pauseBtn = document.getElementById('pause-game-btn');
        var resumeBtn = document.getElementById('resume-game-btn');
        if (nextRoundAdminBtn) nextRoundAdminBtn.classList.add('hidden');
        if (skipBtn) skipBtn.classList.toggle('hidden', !state.isAdmin);
        if (pauseBtn) pauseBtn.classList.add('hidden');
        if (resumeBtn) resumeBtn.classList.add('hidden');

        var reactionBar = document.getElementById('reaction-bar');
        if (reactionBar) reactionBar.classList.add('hidden');

        game.renderWagerWindow(msg);
    }

    function handleQuestionStarted(msg) {
        state.currentPhase = 'QUESTION_ACTIVE';
        currentQuestion = msg;

        pu.showView('game-view');

        // Render question and answers
        game.renderQuestion(msg);
        game.resetSubmissionState();
        // The standing team answer does not carry over into the next round.
        if (team) team.resetRound();

        // #322: a new question always clears any lingering freeze overlay.
        // Guards the edge case where the lockout outlives the round (admin
        // skip / fast round) — without this the player could land on the next
        // question still behind the Ice Card. stopFrozenOverlay is a no-op if
        // it isn't showing, and it clears the countdown interval (no leak).
        game.stopFrozenOverlay();

        // Round indicator
        var currentRound = document.getElementById('current-round');
        var totalRounds = document.getElementById('total-rounds');
        if (currentRound) currentRound.textContent = msg.round_num || 1;
        if (totalRounds) totalRounds.textContent = msg.total_rounds || 10;

        // #706: the wager window raises the "Final Round!" pill and the only
        // line that lowered it again lives in updateGameView, which nothing
        // has called since #619. Play again keeps the phones on this page, so
        // round 1 of game 2 — and every round after it — wore the pill until
        // somebody reloaded. Taken down here for any round that is not the
        // last; raising it stays with the wager window, as before.
        if (!isFinalRound(msg)) {
            var lastRoundBanner = document.getElementById('last-round-banner');
            if (lastRoundBanner) lastRoundBanner.classList.add('hidden');
        }

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
        // QUESTION_ACTIVE → the admin (admin-as-player) can skip a live or
        // broken question. The backend (#318) now evaluates the round on
        // admin_skip during QUESTION_ACTIVE, so this path is reachable.
        // Stays hidden for non-admins and outside an active question.
        if (skipBtn) skipBtn.classList.toggle('hidden', !state.isAdmin);
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
        // #322: reveal ends the answering window — drop the freeze overlay
        // (and its timer) so it can never bleed onto the result screen.
        game.stopFrozenOverlay();
        _clearFinaleCountdown();
        // #434: the round is over, so the picture stops hiding. The reveal
        // view reuses the banner element, so leaving the blur on would show
        // the correct answer next to an image nobody can read.
        if (game.clearRevealBlur) game.clearRevealBlur();
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
    // Leaving the game view for good (#838)
    // ============================================

    // The reaction bar, the admin bar and the toast are fixed to the page and
    // sit OUTSIDE the view stack, so showView() cannot take them with it. A
    // reset therefore returned the phone to the join screen with five live
    // reaction buttons under the Join button, the last in-game toast ("3 in a
    // row!", "Time expired") still on screen, and — invisible but read aloud —
    // the timer's polite region still holding "5 seconds left" (#839).
    //
    // handleFinale has always torn the first two down by hand; this is that
    // teardown in one place, for every exit that is not the finale.
    function clearGameChrome() {
        var reactionBar = document.getElementById('reaction-bar');
        if (reactionBar) reactionBar.classList.add('hidden');
        var adminBar = document.getElementById('admin-control-bar');
        if (adminBar) adminBar.classList.add('hidden');
        if (pu.clearToast) pu.clearToast();
        if (game && game.clearTimeAnnouncement) game.clearTimeAnnouncement();
        // The game is over for this phone: a hatch armed on the screen we just
        // left must not fire a minute later on the join screen, and the roster
        // it was judged against belongs to a game that no longer exists.
        setResetStage(null);
        _lastRoster = [];
        _hostSeenInRoster = false;
        _hostConnectedFlag = null;
    }

    // ============================================
    // Finale
    // ============================================

    function handleFinale(msg) {
        state.currentPhase = 'FINALE';
        // #782: updatePageTitle has always known the FINALE case, but the two
        // paths that reach the end screen — the live `finale` event and a
        // FINALE game_state — both routed straight here, so nothing ever
        // called it. The tab kept "Quizify — Question 5" while the screen said
        // Game Over, which is the one place the phone still claimed a game was
        // running: it is what a player reads in the tab strip and in the app
        // switcher.
        updatePageTitle('FINALE', msg);
        game.stopCountdown();
        _clearFinaleCountdown();
        // #803: the game is over — the end screen has its own New game button,
        // and a hatch armed on the stage we just left must not surface here a
        // minute later.
        setResetStage(null);
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

        // #299: host-permanently-gone escape hatch. When the admin device
        // dies for good, the server lets ANY client reset after 60s (#207),
        // but until now no non-admin view exposed a button → the game was a
        // dead-end. Arm a 60s timer on an admin_disconnected pause that
        // reveals the reset button; a non-admin then has a way out. Admins
        // already have their own resume/end bar, so skip it for them.
        if (isDisconnect && !state.isAdmin) {
            armResetAffordance('paused-reset-btn');
        } else {
            disarmResetAffordance('paused-reset-btn');
        }
    }

    // ============================================
    // Host-permanently-gone reset affordance (#299)
    // ============================================

    // ~60s — mirrors the server's #207 grace window before it authorizes a
    // reset_game from any client.
    var RESET_AFFORDANCE_DELAY_MS = 60000;
    var _resetAffordanceTimers = {};

    // wrapperId (optional) is an extra container revealed alongside the
    // button — used by the reveal view so its bordered slot stays hidden
    // until the timer actually fires.
    function armResetAffordance(btnId, wrapperId) {
        // Already armed/shown for this view — don't restart the clock (a
        // repeated PAUSED/REVEAL push shouldn't reset the wait).
        if (_resetAffordanceTimers[btnId]) return;
        var btn = document.getElementById(btnId);
        if (btn && !btn.classList.contains('hidden')) return;
        _resetAffordanceTimers[btnId] = setTimeout(function () {
            _resetAffordanceTimers[btnId] = null;
            var b = document.getElementById(btnId);
            if (b) {
                b.disabled = false;
                b.classList.remove('hidden');
            }
            if (wrapperId) {
                var w = document.getElementById(wrapperId);
                if (w) w.classList.remove('hidden');
            }
        }, RESET_AFFORDANCE_DELAY_MS);
    }

    function disarmResetAffordance(btnId, wrapperId) {
        if (_resetAffordanceTimers[btnId]) {
            clearTimeout(_resetAffordanceTimers[btnId]);
            _resetAffordanceTimers[btnId] = null;
        }
        var btn = document.getElementById(btnId);
        if (btn) btn.classList.add('hidden');
        if (wrapperId) {
            var w = document.getElementById(wrapperId);
            if (w) w.classList.add('hidden');
        }
    }

    // ------------------------------------------------------------------
    // Between-round stages that wait for a host tap (#299, #803)
    // ------------------------------------------------------------------
    //
    // Each of these phases ends only when the host advances it: the reveal and
    // the two detour results are all left by `next_question`. If the host's
    // phone dies while one is on screen, nothing on the wire will ever move
    // again — the server's grace pause refuses every phase but QUESTION_ACTIVE
    // — so every one of them needs the #207 escape hatch.
    //
    // #299 gave it to the reveal only. The lightning recap and the Hot Seat
    // result arrived later and inherited the hole: the server would have
    // accepted `reset_game` from any of the guests sitting there, but no view
    // offered it, so a whole room sat on a results screen forever. Listing the
    // stages in one table is what stops the next between-round phase
    // inheriting it a third time.
    var STAGE_RESET_AFFORDANCES = {
        'ANSWER_REVEAL': ['reveal-reset-btn', 'reveal-reset-controls'],
        'REVEAL': ['reveal-reset-btn', 'reveal-reset-controls'],
        'LIGHTNING_RECAP': ['lightning-recap-reset-btn', 'lightning-recap-reset-controls'],
        'HOT_SEAT_REVEAL': ['hotseat-reset-btn', 'hotseat-reset-controls']
    };

    // The roster from the most recent frame that carried one.
    //
    // "Is a host still connected" is the whole decision, and the events that
    // OPEN two of these stages — `lightning_recap`, `hot_seat_result` — carry
    // no player list at all, while the frame that says the host just vanished
    // (`player_left`) carries nothing else. Remembering the last roster lets
    // both kinds of frame ask the same question.
    var _lastRoster = [];

    // What the SERVER says about the host (#842), or null when it has not
    // said anything: an older integration that does not send the flag, or a
    // phone that has not received a frame carrying it yet. The roster reading
    // below is the fallback for exactly that case, so a phone from this
    // version and a server from the last one still behave the way they did.
    //
    // When it IS present it wins outright, because it is the only reading that
    // can see a host at /quizify/admin who never joined as a player — the
    // room's most common shape, and the one the roster is blind to.
    var _hostConnectedFlag = null;

    // Whether any roster this game has ever named the host (#834). A host who
    // runs the evening from /quizify/admin without ever taking a player slot
    // is in no roster at all, so their absence from one proves nothing; a host
    // who WAS a player and has since been dropped by the disconnect grace
    // period is absent from the same roster and proves everything. This flag
    // is the only thing that tells the two apart. Cleared with the game.
    var _hostSeenInRoster = false;

    // Which stage the phone is sitting on, or null. Deliberately not
    // state.currentPhase: the two detour results are announced by one-shot
    // events that must not be allowed to rewrite the phase everything else
    // reads.
    var _resetStage = null;

    // Any frame may carry the flag; only a frame that actually does may
    // change it. The leaderboard-refresh `game_state` (#221) is built by a
    // different builder and carries no host key at all, so testing for the
    // key rather than its truthiness is what stops it wiping the answer.
    function _rememberHostFlag(msg) {
        if (msg && typeof msg.host_connected === 'boolean') {
            _hostConnectedFlag = msg.host_connected;
        }
    }

    function _rememberRoster(msg) {
        if (!msg || !Array.isArray(msg.players)) return;
        _lastRoster = msg.players;
        if (_lastRoster.some(function (p) { return p && p.is_admin; })) {
            _hostSeenInRoster = true;
        }
    }

    // Three answers, not two (#834). "No connected host in this roster" and
    // "the host is gone" are different statements, and reading the first as
    // the second is what put a red Reset button on every guest phone during
    // the lightning recap while the host sat looking at that same recap: the
    // host was hosting from /quizify/admin without joining as a player, so no
    // roster all evening carried an `is_admin` row and the hatch read the
    // silence as a death. It is the same blind spot #726 closed on the server,
    // where a live `?role=admin` socket now refuses a guest `reset_game` —
    // the phone has no equivalent signal on the wire, so the honest answer
    // here is 'unknown', and 'unknown' never arms anything.
    //
    // Returns 'connected' | 'gone' | 'unknown'.
    function _hostPresence() {
        if (_hostConnectedFlag !== null) {
            return _hostConnectedFlag ? 'connected' : 'gone';
        }
        var named = false;
        for (var i = 0; i < _lastRoster.length; i++) {
            var p = _lastRoster[i];
            if (!p || !p.is_admin) continue;
            named = true;
            if (p.connected !== false) return 'connected';
        }
        // A host row that has vanished from the roster entirely is the removal
        // that follows the disconnect grace period, not a host who never was a
        // player — so a host we have seen once stays "gone" rather than
        // decaying into "unknown" and taking the armed hatch with it.
        return (named || _hostSeenInRoster) ? 'gone' : 'unknown';
    }

    // Enter (or leave) a waiting stage. Leaving takes the escape hatch with
    // it — a hatch left armed across a phase change would pop up 60s later on
    // a screen where it means nothing.
    function setResetStage(stage) {
        _resetStage = stage || null;
        var mine = _resetStage ? STAGE_RESET_AFFORDANCES[_resetStage][0] : null;
        Object.keys(STAGE_RESET_AFFORDANCES).forEach(function (key) {
            var ids = STAGE_RESET_AFFORDANCES[key];
            // ANSWER_REVEAL and REVEAL are two names for one control, so
            // compare the button, not the phase — otherwise the alias would
            // disarm the stage we are standing on.
            if (ids[0] === mine) return;
            disarmResetAffordance(ids[0], ids[1]);
        });
        refreshStageReset();
    }

    // Re-decide the current stage's hatch against the latest roster. Called
    // again on every roster frame, so a host who dies *during* the stage is
    // caught, not just one who was already gone when it opened.
    function refreshStageReset() {
        var ids = _resetStage && STAGE_RESET_AFFORDANCES[_resetStage];
        if (!ids) return;
        // The admin has their own bar with Next Round on every one of these
        // screens; a reset button next to it is an invitation to a misfire.
        // Everything else needs POSITIVE evidence that the host is gone
        // (#834) — an unknown host is treated exactly like a present one.
        if (state.isAdmin || _hostPresence() !== 'gone') {
            disarmResetAffordance(ids[0], ids[1]);
        } else {
            // The wrapper is kept hidden until the timer fires — see
            // armResetAffordance's wrapperId reveal — so a host-gone stage
            // doesn't show an empty bordered slot for 60s.
            armResetAffordance(ids[0], ids[1]);
        }
    }

    function setupResetAffordance() {
        [
            'paused-reset-btn',
            'reveal-reset-btn',
            'lightning-recap-reset-btn',
            'hotseat-reset-btn'
        ].forEach(function (id) {
            var btn = document.getElementById(id);
            if (btn) {
                btn.addEventListener('click', function () {
                    btn.disabled = true;
                    send('reset_game', {});
                });
            }
        });
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
            pu.showToast(tJk('game.opponentUsedJoker', { name: msg.source_player }), 2000, 'game.opponentUsedJoker');
        } else if (msg.powerup_type === 'steal') {
            var tPwr = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            var pts = msg.stolen_points || 0;
            if (msg.source_player === state.playerName) {
                pu.showToast(tPwr('game.stoleFromOpponent', { points: pts, name: msg.target_player || tPwr('lobby.you') }), 2500, 'game.stoleFromOpponent');
            } else if (msg.target_player === state.playerName) {
                pu.showToast(tPwr('game.stoleFromYou', { name: msg.source_player || tPwr('lobby.you'), points: pts }), 2500, 'game.stoleFromYou');
            }
        } else if (msg.powerup_type === 'freeze' && msg.target_player === state.playerName) {
            // #322: full blocking "Ice Card" overlay + live countdown instead
            // of the old 2s toast. The server carries freeze_duration only to
            // the target (effect_data in websocket.py ~L1045). Fall back to a
            // sane default if it's somehow missing so the overlay still tears
            // itself down rather than hanging forever.
            var freezeSecs = Number(msg.freeze_duration) || 5;
            game.startFrozenOverlay(freezeSecs);
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
        var t = _t();
        var code = msg.code || 'UNKNOWN';

        // #729: a join is in flight — the error is a refusal, and the join
        // form owns it. This used to be gated on `!state.playerName`, which
        // handleJoinClick sets BEFORE the join goes out, so the branch was
        // dead for every single refusal: the guest was left with a disabled
        // button reading "Joining…", no reason and no way back.
        // The old `!state.playerName` condition is kept as a second trigger —
        // it still catches an error that lands on the join form outside a
        // tracked join — but `joinPending` is the one that actually fires.
        if (state.joinPending || (!state.playerName && els.joinBtn)) {
            pu.showToast(showJoinRefusal(code, msg.message));
            return;
        }

        var key = 'errors.' + code;
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

        // #750: an estimate guess that was refused must give the slider back.
        // Only for the codes where a second try can actually succeed —
        // ALREADY_SUBMITTED / ROUND_EXPIRED / NOT_IN_GAME mean the round is
        // gone for us, and re-enabling there would just invite a second
        // refusal.
        if (game && game.releaseGuess) game.releaseGuess(msg.code);

        // A refused team action is not a generic failure — the lobby has an
        // answer for it (teams are set / that team dissolved), and showing it
        // there is what keeps the player from asking the host (#365).
        if (msg.code === 'TEAM_CLOSED' && team) team.handleTeamError();
    }

    // ============================================
    // Join Refusals (#729)
    // ============================================

    // How long a join may sit unanswered before we tell the guest something
    // is wrong. A refusal comes back in milliseconds; this only fires when
    // nothing comes back at all — the per-IP connection cap answers the
    // upgrade with a plain HTTP 429 (server/websocket.py), so the socket
    // never opens and no `error` frame is ever sent. Before #729 that left
    // the button on "Joining…" with nothing else on screen.
    var JOIN_ANSWER_TIMEOUT_MS = 10000;
    var _joinTimeout = null;

    // Refusals that invalidate the name we hold. Without clearing it, the
    // reconnect loop in player-utils keeps firing (it is gated on
    // state.playerName) and re-sends the very name the server just refused,
    // so the guest watches a silent retry storm instead of a join form.
    var JOIN_REFUSALS_CLEARING_NAME = [
        'NAME_TAKEN', 'NAME_INVALID', 'GAME_FULL', 'GAME_ENDED', 'ALREADY_JOINED'
    ];

    function _t() {
        return (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
    }

    // Prefer the join-specific wording (`join.refused.<CODE>`), which tells
    // the guest what to DO. `errors.<CODE>` is the terse label used for
    // in-game toasts ("Name already taken") and is only a fallback here.
    function joinRefusalText(code, serverMessage) {
        var t = _t();
        var candidates = ['join.refused.' + code, 'errors.' + code];
        for (var i = 0; i < candidates.length; i++) {
            var value = t(candidates[i]);
            if (value && value !== candidates[i]) return value;
        }
        if (serverMessage) return serverMessage;
        return t('join.refused.UNKNOWN');
    }

    function beginJoinPending() {
        state.joinPending = true;
        if (_joinTimeout) clearTimeout(_joinTimeout);
        _joinTimeout = setTimeout(function () {
            _joinTimeout = null;
            if (state.joinPending) showJoinRefusal('NO_CONNECTION', null);
        }, JOIN_ANSWER_TIMEOUT_MS);
    }

    function endJoinPending() {
        state.joinPending = false;
        if (_joinTimeout) { clearTimeout(_joinTimeout); _joinTimeout = null; }
    }

    // Put the join form back in a usable state AND leave the reason on
    // screen. The toast alone fades after three seconds and takes the reason
    // with it (#426); the button alone says nothing at all (#729).
    // Returns the text shown so the caller can reuse it for the toast.
    function showJoinRefusal(code, serverMessage) {
        endJoinPending();
        var t = _t();
        var text = joinRefusalText(code, serverMessage);

        if (JOIN_REFUSALS_CLEARING_NAME.indexOf(code) !== -1) {
            state.playerName = null;
            state.playerId = null;
            state.sessionToken = null;
            pu.clearSession();
            // A refusal can arrive on an auto-rejoin, with the guest sitting
            // behind the reconnecting overlay or on the lobby. Put them back
            // on the form the message belongs to.
            if (pu.hideReconnectingOverlay) pu.hideReconnectingOverlay();
            state.isReconnecting = false;
            state.reconnectAttempts = 0;
            pu.showView('join-view');
        }

        if (els.joinBtn) {
            els.joinBtn.disabled = false;
            els.joinBtn.textContent = code === 'NO_CONNECTION'
                ? t('connection.retryConnection')
                : t('join.joinButton');
        }
        if (els.nameInput) els.nameInput.style.borderColor = '#D65858';
        // The input's aria-describedby target: persistent and announced to
        // screen readers, cleared on the next keystroke (setupJoinForm).
        var vmsg = document.getElementById('name-validation-msg');
        if (vmsg) {
            vmsg.textContent = text;
            vmsg.classList.remove('hidden');
        }
        return text;
    }

    // ============================================
    // Join Form
    // ============================================

    function setupJoinForm() {
        if (!els.nameInput || !els.joinBtn) return;

        els.nameInput.addEventListener('input', function () {
            var result = pu.validateName(this.value);
            els.joinBtn.disabled = !result.valid;
            // Clear a stale join-error reason (#426) once the user edits the
            // name again, and drop the red border set by handleError.
            var vmsg = document.getElementById('name-validation-msg');
            if (vmsg && vmsg.textContent) {
                vmsg.textContent = '';
                vmsg.classList.add('hidden');
            }
            this.style.borderColor = '';
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

        // Setting the name here is load-bearing: the onOpen handler below
        // auto-sends the join off state.playerName when the socket wasn't
        // open at click time, and player-utils gates its whole reconnect
        // loop on it. So the ordering stays; what changes (#729) is that the
        // refusal path no longer *infers* "we are joining" from the absence
        // of a name — beginJoinPending() says so outright.
        beginJoinPending();
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
            if (state.isAdmin) {
                joinMsg.is_admin = true;
                // #358: attach the admin session token (fail-soft) so a crown
                // transfer from a stale admin slot can be authorised.
                var _at = QuizifyUtils.readAdminToken();
                if (_at) joinMsg.admin_token = _at;
            }
            send('join', joinMsg);
        } else {
            // WS not open at click time (initial connect still pending, or
            // dropped) — kick off a (re)connect. state.playerName is now
            // set, so the onOpen handler auto-sends the join. Without this
            // the join button would hang on "Joining…" forever.
            connect();
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

    // Header toggles paint their own aria-label from JS, so they are invisible
    // to initPageTranslations() (which only walks data-i18n* attributes). i18n
    // loads asynchronously and init() runs first, so at first paint both
    // buttons announce the raw key — a screen reader literally says
    // "game dot sound mute". These refs let _flushToggleLabels() repaint them
    // once translations land, the same way _flushPendingTitle() repairs the
    // page title. (#372)
    var _renderSoundToggle = null;
    var _renderA11yToggle = null;

    // ============================================
    // Per-player UI language (#492)
    // ============================================

    // Set only when the player taps a flag chip. Its presence — not its value
    // — is what marks the language as *chosen* rather than *detected*, and
    // that distinction is the whole feature: see _syncServerLanguage below.
    var PLAYER_LANG_KEY = 'quizify-player-lang';

    function _storedPlayerLang() {
        try {
            return window.localStorage.getItem(PLAYER_LANG_KEY) || null;
        } catch (e) {
            return null;
        }
    }

    function _markActiveLangChip() {
        var group = document.getElementById('player-lang-chips');
        if (!group || !window.QuizifyI18n) return;
        var active = QuizifyI18n.getLanguage();
        group.querySelectorAll('.chip').forEach(function (chip) {
            chip.classList.toggle('active', chip.dataset.value === active);
            chip.setAttribute('aria-pressed', chip.dataset.value === active ? 'true' : 'false');
        });
    }

    // Apply the server's game language to this phone — unless the player has
    // picked one. Before #492 this was unconditional, with the reasoning that
    // "a German game shows German labels even if the player's browser is
    // English". That is still the right default, but it also meant every
    // incoming game_state would stamp over a deliberate choice within
    // milliseconds, so a picker without this guard would look broken rather
    // than absent.
    function _syncServerLanguage(msg) {
        if (!msg.language || !window.QuizifyI18n) return false;
        if (_storedPlayerLang()) return false;
        if (QuizifyI18n.getLanguage() === msg.language) return false;
        QuizifyI18n.setLanguage(msg.language).then(function () {
            QuizifyI18n.initPageTranslations();
            _markActiveLangChip();
            updatePageTitle(msg.phase, msg);
        });
        return true;
    }

    function setupLanguagePicker() {
        var group = document.getElementById('player-lang-chips');
        if (!group) return;
        // An unsubstituted token means the page was served by something that
        // doesn't know about {{UI_LANGUAGE_CHIPS}}; hide the row rather than
        // showing the raw braces.
        if (group.textContent.indexOf('{{') !== -1) {
            group.classList.add('hidden');
            return;
        }
        if (!group.querySelector('.chip') || !window.QuizifyI18n) {
            group.classList.add('hidden');
            return;
        }

        group.addEventListener('click', function (e) {
            var chip = e.target.closest('.chip');
            if (!chip || !chip.dataset.value) return;
            var code = chip.dataset.value;
            if (code === QuizifyI18n.getLanguage()) return;
            try {
                window.localStorage.setItem(PLAYER_LANG_KEY, code);
            } catch (_e) { /* private mode: choice holds for this page view */ }
            QuizifyI18n.setLanguage(code).then(function () {
                QuizifyI18n.initPageTranslations();
                _markActiveLangChip();
                _flushToggleLabels();
            });
        });
    }

    function _flushToggleLabels() {
        if (_renderSoundToggle) _renderSoundToggle();
        if (_renderA11yToggle) _renderA11yToggle();
    }

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
        _renderSoundToggle = render;
        render();
    }

    // Accessibility mode (#372). Mirrors setupSoundToggle above: same header
    // furniture, same aria-pressed contract, preference owned by the module.
    function setupA11yToggle() {
        var btn = document.getElementById('a11y-toggle-btn');
        var a11y = window.QuizifyA11y;
        if (!btn) return;
        if (!a11y) { btn.classList.add('hidden'); return; }

        function render() {
            var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            var on = a11y.isEnabled();
            btn.classList.toggle('is-on', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            var label = on ? t('a11y.modeOff') : t('a11y.modeOn');
            btn.setAttribute('aria-label', label);
            btn.setAttribute('title', label);
        }

        btn.addEventListener('click', function () {
            a11y.toggle();
            render();
        });
        _renderA11yToggle = render;
        render();
    }

    function init() {
        pu.paintUiIcons();
        setupJoinForm();
        lobby.init(send);
        if (team) team.setupLobby(send);
        setupRetryConnection();
        setupAdminControls();
        setupReactionBar();
        setupSoundToggle();
        setupA11yToggle();
        setupLanguagePicker();
        setupResetAffordance();
        pu.setupCollapsibles();
        if (lightning) { lightning.setSend(send); lightning.init(); }

        // Answer button clicks
        var answerButtons = document.getElementById('answer-buttons');
        if (answerButtons) {
            answerButtons.addEventListener('click', function (e) {
                var btn = e.target.closest('.answer-btn');
                if (!btn || btn.disabled) return;
                var index = parseInt(btn.dataset.index, 10);
                if (isNaN(index)) return;
                // #696: while the seat holder is answering, the same grid
                // means hot_seat_answer. This used to be an inline onclick on
                // replacement buttons, which shadowed this handler and left
                // the grid unusable for every later round.
                if (hotSeat && hotSeat.handleSeatAnswerClick &&
                    hotSeat.handleSeatAnswerClick(index)) {
                    return;
                }
                game.handleAnswerClick(index, send);
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

        // i18n init. A stored per-player choice (#492) wins over browser
        // detection — init() falls back to detectBrowserLanguage() when given
        // nothing, which is what every player without a choice still gets.
        if (window.QuizifyI18n) {
            QuizifyI18n.init(_storedPlayerLang() || undefined).then(function () {
                QuizifyI18n.initPageTranslations();
                _markActiveLangChip();
                // game_state may have already arrived before i18n loaded;
                // re-fire the title update so we don't sit on the stale
                // "— Beitreten" forever (see updatePageTitle for the why).
                _flushPendingTitle();
                // Same race for the header toggles, whose accessible names are
                // set from JS rather than data-i18n attributes (#372).
                _flushToggleLabels();
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
                // #729: say WHY, persistently. The per-IP connection cap
                // refuses the upgrade with an HTTP 429 the WebSocket API
                // never surfaces, so a relabelled button was the guest's
                // only clue that anything had gone wrong.
                var vmsgConn = document.getElementById('name-validation-msg');
                if (vmsgConn) {
                    vmsgConn.textContent = joinRefusalText('NO_CONNECTION', null);
                    vmsgConn.classList.remove('hidden');
                }
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

        // #750: back to a usable join screen after a kick. A full reload, not
        // showView('join-view') — the server closed our socket, so the join
        // button on a re-shown form would have nothing to send on. The query
        // string is dropped on purpose: a leftover ?name=/?reconnect=1 would
        // aim the fresh page straight back at the identity we just discarded.
        var kickedRejoinBtn = document.getElementById('kicked-rejoin-btn');
        if (kickedRejoinBtn) {
            kickedRejoinBtn.addEventListener('click', function () {
                location.href = location.pathname;
            });
        }

        // Safety watchdog against a blank screen. Several edges can leave the
        // player with no visible view: a dead-reconnect URL
        // (?name=X&admin=true&reconnect=1) whose join yields no game_state
        // (neither reconnect_failed #227 nor the game_state default #228 fire),
        // OR a reconnect into a live LIGHTNING round where the lightning view
        // is never shown (game_state phase is set but every .view stays
        // hidden). A few seconds after boot, if NO real view has rendered
        // (still on loading or nothing) — regardless of phase — fall back to
        // the join screen so there's always a way forward instead of blank.
        setTimeout(function () {
            var visible = [].slice.call(document.querySelectorAll('.view'))
                .find(function (e) { return e.offsetParent !== null; });
            var stuck = !visible || visible.id === 'loading-view';
            if (stuck) {
                pu.showView('join-view');
            }
        }, 4000);
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
            case 'HOT_SEAT_AUCTION':
            case 'HOT_SEAT':
            case 'PAUSED':
                return false;
            default:
                // LOBBY, ANSWER_REVEAL/REVEAL, LIGHTNING_RECAP, FINALE/END,
                // and the pre-join state.
                return true;
        }
    };

})();
