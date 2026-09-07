/**
 * Quizify — the television's page script (#829).
 *
 * This was 1,771 lines inline in `dashboard.html`. Inline meant it was the one
 * piece of shipped client code that did not pass through
 * `scripts/build_bundle.py`, so the CI `drift` job could not keep it honest and
 * every test that wanted to read it had to slice an 8,000-line HTML file to get
 * at it. Nothing here changed in the move — same IIFE, same order, same
 * globals.
 *
 * Loaded from `dashboard.html` after `i18n.js`, `utils.js`, `common.bundle.js`
 * (QuizifyClientCore / QuizifyRenderShared) and the QR vendor bundle, all of
 * which it reads at call time.
 */

(function() {
    'use strict';

    // ---- State ----
    var ws = null;
    var timerDuration = 30;
    var timerRemaining = 0;
    var currentAnswers = [];
    // #296: while paused the server stops sending timer_tick, so we must
    // also ignore the local board's last tick and never animate the bar
    // down. This flag gates handleTimerTick so a frozen game's bar holds.
    var isPaused = false;
    // #425: the lightning round's per-question duration. Seeded from the
    // lightning_splash / game_state(LIGHTNING) payload's seconds_per_question
    // so a non-15s round drives the shared timer bar with the right divisor.
    var lightningSeconds = 15;
    // #741: which phase the board believes it is in. The reveal is the
    // only phase reactions are allowed over, and nothing on this page
    // tracked the phase before — handleGameState read msg.phase and threw
    // it away. Mirrors admin.js's currentPhase, set from the same events.
    var currentPhase = 'LOBBY';

    // ---- DOM ----
    var views = {
        waiting: document.getElementById('waiting-view'),
        question: document.getElementById('question-view'),
        finale: document.getElementById('finale-view'),
        lightning: document.getElementById('lightning-view'),
        lightningRecap: document.getElementById('lightning-recap-view'),
    };
    var els = {
        // #421: connection-lost pill toggled by ws.onclose / ws.onopen.
        reconnectPill: document.getElementById('reconnect-pill'),
        // #374: TV lobby join QR + live player roster.
        lobbyQrCode: document.getElementById('lobby-qr-code'),
        lobbyJoinUrlEl: document.getElementById('lobby-join-url'),
        lobbyPlayers: document.getElementById('lobby-players'),
        roundIndicator: document.getElementById('round-indicator'),
        // #741: power-up strip stack + floating-reaction layer.
        powerupBanners: document.getElementById('powerup-banners'),
        reactionLayer: document.getElementById('reaction-layer'),
        timerFill: document.getElementById('timer-fill'),
        answerProgress: document.getElementById('answer-progress'),
        eveningTally: document.getElementById('evening-tally'),
        lobbyH2h: document.getElementById('lobby-h2h'),
        endH2h: document.getElementById('end-h2h'),
        questionCategory: document.getElementById('question-category'),
        questionImage: document.getElementById('question-image'),
        questionMedia: document.getElementById('question-media'),
        dashboardLeft: document.getElementById('dashboard-left'),
        questionText: document.getElementById('question-text'),
        answersGrid: document.getElementById('answers-grid'),
        dashboardEstimate: document.getElementById('dashboard-estimate'),
        leaderboard: document.getElementById('leaderboard'),
        funFact: document.getElementById('fun-fact'),
        funFactText: document.getElementById('fun-fact-text'),
        podium: document.getElementById('podium'),
        finaleLeaderboard: document.getElementById('finale-leaderboard'),
        pausedOverlay: document.getElementById('paused-overlay'),
        pausedTitle: document.getElementById('paused-title'),
        pausedHint: document.getElementById('paused-hint'),
        pausedEscape: document.getElementById('paused-escape'),
        lightningSplash: document.getElementById('lightning-splash'),
        lightningSplashRules: document.getElementById('lightning-splash-rules'),
        lightningQuestionSection: document.getElementById('lightning-question-section'),
        lightningProgress: document.getElementById('lightning-progress'),
        lightningQuestionText: document.getElementById('lightning-question-text'),
        lightningAnswersGrid: document.getElementById('lightning-answers-grid'),
        lightningLeaderboard: document.getElementById('lightning-leaderboard'),
        lightningRecapGrid: document.getElementById('lightning-recap-grid'),
        lightningRecapLeaderboard: document.getElementById('lightning-recap-leaderboard'),
    };

    // i18n helper — returns the translation or the supplied fallback when
    // the bundle isn't loaded yet / the key is missing.
    function t(key, fallback) {
        if (window.QuizifyI18n && window.QuizifyI18n.t) {
            var v = window.QuizifyI18n.t(key);
            if (v && v !== key) return v;
        }
        return fallback != null ? fallback : key;
    }

    function showView(name) {
        Object.values(views).forEach(function(v) { v.classList.remove('active'); });
        if (views[name]) views[name].classList.add('active');
        // #706: the tally sits in the header, outside every view, and only
        // question_started ever took it down. The reveal, the lightning
        // round, the finale and game_reset all left "3/3" in coral on the
        // board — through the next lobby, where it hung above the QR code.
        // Every view change away from the question clears it, so a new
        // path cannot forget again. The hot-seat frame shows the question
        // view and sets its own tally afterwards, so it is unaffected.
        if (name !== 'question' && els.answerProgress) {
            els.answerProgress.textContent = '';
            els.answerProgress.classList.add('hidden');
            els.answerProgress.classList.remove('is-complete');
        }
        // #807: the same shape, one line further down. `#evening-tally`
        // also sits in the header outside every view and
        // `handleEveningTally` was its only writer — it hid itself only
        // when a payload arrived with no leaders, and the server sends it
        // once per finished game from the second game of a sitting. So
        // from game three the room read "Tonight · Anna 2 wins · Ben 1
        // win" above the QR code in the lobby and above the timer bar on
        // every question and reveal, out of the 549.8px the 720p left
        // column has (#680/#688 measured that budget without it). The
        // finale is the screen #612 asked for; nothing else needs it.
        if (name !== 'finale' && els.eveningTally) {
            els.eveningTally.classList.add('hidden');
        }
        // #850: the third panel on this board with a writer and no
        // clearer, after #706's answer tally and #807's evening leaders.
        // `handleRoundSummary` is the only thing that ever fills
        // `#fun-fact`, and only `question_started` ever took the class
        // off again — so the last fact of a game survived the finale, the
        // reset and the whole next lobby with its text still in it, and
        // every other door into the question view put it back on screen:
        // a QUESTION_ACTIVE snapshot with no question block, a
        // WAGER_ACTIVE or HOT_SEAT snapshot with no detour block, and the
        // ANSWER_REVEAL rebuild all call showView('question') without
        // touching it. The room then read the previous game's fact — in
        // the previous game's language, because the `data-i18n` label had
        // already switched — through the first question of the next game,
        // when it is looking hardest at the screen. Clearing on every
        // view change means a new path cannot forget again: the reveal
        // writes the fact without a view change, and the reconnect path
        // re-renders it from the snapshot immediately afterwards.
        if (els.funFact) {
            els.funFact.classList.remove('visible');
            if (els.funFactText) els.funFactText.textContent = '';
        }
    }

    // ---- #374: TV lobby join QR + live player roster ----
    // The join URL is derived exactly like admin.js's initJoinUrl():
    // origin + '/quizify/player' (same origin as this dashboard). The QR
    // is rendered once (the URL never changes for a running server) and the
    // roster refreshes on every LOBBY snapshot / player_joined / player_left.
    var _lobbyQrRendered = false;
    function lobbyJoinUrl() {
        return window.location.origin + '/quizify/player';
    }
    function renderLobbyQr() {
        if (_lobbyQrRendered) return;
        var container = els.lobbyQrCode;
        if (!container) return;
        container.innerHTML = '';
        var url = lobbyJoinUrl();
        // Written whether or not the QR renders: the text is the fallback
        // for an unreachable address, which the QR itself cannot signal.
        //
        // Composed from location.host rather than stripped off `url` with a
        // scheme regex. #540's guard bans that shape in render sites, and
        // rightly so even here where it would only be cosmetic: a reader
        // cannot tell a display strip from the scheme test that caused #540.
        if (els.lobbyJoinUrlEl) {
            els.lobbyJoinUrlEl.textContent =
                window.location.host + '/quizify/player';
        }
        if (typeof QRCode !== 'undefined') {
            // Large, high-contrast code so it scans from across the room.
            new QRCode(container, {
                text: url, width: 280, height: 280,
                colorDark: '#0b0e1a', colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M,
            });
            _lobbyQrRendered = true;
        } else {
            // Defensive fallback if the vendor lib failed to load — show
            // the raw URL so players can still type it in.
            // #428: the body is cream (#FAF6EC), so a white URL was invisible
            // when the vendor lib failed. Use the dark-ink token + bigger type.
            container.innerHTML = '<div style="padding:20px;word-break:break-all;' +
                'font-size:20px;color:var(--dash-text-white);">' + escapeHtml(url) + '</div>';
        }
    }
    // The teams as of the last roster/snapshot (#365). Kept here rather
    // than derived, because "who is playing with whom" is exactly what the
    // TV lobby exists to answer — and the alternative is the room shouting
    // it across the sofa.
    var lobbyTeams = [];
    var lastLobbyPlayers = [];

    function renderLobbyPlayers(players) {
        var container = els.lobbyPlayers;
        if (!container) return;
        var list = window.QuizifyRenderShared.rosterList(players);

        function chip(p) {
            var name = (p && p.name != null) ? p.name : '';
            // 👑 marks the host, mirroring player-lobby.js's is_admin chip.
            var crown = (p && p.is_admin)
                ? '<span aria-hidden="true">👑</span> ' : '';
            return '<div class="dashboard-player-chip">' + crown +
                '<span>' + escapeHtml(name) + '</span></div>';
        }

        if (!lobbyTeams.length) {
            container.innerHTML = list.map(chip).join('');
            return;
        }

        // Teamed players are shown under their team; everyone else keeps
        // their own chip. Shared with the host page since #787 — the grouping
        // was written twice and admin.js's copy carried a comment saying so.
        container.innerHTML = window.QuizifyRenderShared.teamGroupedRosterHtml(
            list,
            lobbyTeams,
            {
                entry: chip,
                groupClass: 'dashboard-team-group',
                nameClass: 'dashboard-team-name',
                membersClass: 'dashboard-team-members'
            }
        );
    }
    // Called on every LOBBY snapshot: paint the QR once, refresh the roster.
    function renderLobby(players) {
        renderLobbyQr();
        lastLobbyPlayers = players;
        renderLobbyPlayers(players);
    }

    // #892: ~60s — the server's #207 grace window, after which it
    // authorizes a reset_game from ANY client. The phones arm the same
    // clock (player-core.js armResetAffordance), so the television and the
    // sofa reach the way out in the same second.
    var PAUSED_ESCAPE_DELAY_MS = 60000;
    var pausedEscapeTimer = null;

    // Both lines of the scrim, re-keyed. The element carries data-i18n, so
    // the attribute has to move with the text — otherwise the next
    // language sweep (syncServerLanguage → initPageTranslations) paints the
    // old key back over it.
    function setPausedLine(el, key) {
        if (!el) return;
        el.setAttribute('data-i18n', key);
        el.textContent = t(key);
    }

    // #296: the paused scrim sits on top of whatever view is active.
    // Pause also freezes the timer; resume clears the flag so the next
    // timer_tick (or question) animates the bar again.
    //
    // #892: `reason` is the snapshot's pause_reason. A host who tapped
    // pause is coming back; a host whose phone dropped may not be, and the
    // room was being told the same sentence for both — "The game will
    // resume when the host returns" — while the phones had already
    // switched to the disconnect wording and were counting down to a reset
    // button nobody in the room had been told about.
    function setPaused(on, reason) {
        isPaused = !!on;
        if (els.pausedOverlay) els.pausedOverlay.classList.toggle('active', isPaused);
        var hostGone = isPaused && reason === 'admin_disconnected';
        setPausedLine(els.pausedTitle,
            hostGone ? 'admin.pausedHostDisconnected' : 'game.paused');
        setPausedLine(els.pausedHint,
            hostGone ? 'admin.pausedHostHint' : 'game.pausedHint');
        if (hostGone) armPausedEscape(); else disarmPausedEscape();
    }

    // Arming is idempotent: a repeated PAUSED snapshot (they arrive on
    // every reconnect) must not restart the wait, or a television that
    // resubscribes every 30s would never reach the sixtieth second.
    function armPausedEscape() {
        if (pausedEscapeTimer !== null) return;
        if (els.pausedEscape && !els.pausedEscape.hidden) return;
        pausedEscapeTimer = setTimeout(function() {
            pausedEscapeTimer = null;
            if (!isPaused) return;   // the host came back mid-wait
            if (els.pausedEscape) {
                els.pausedEscape.hidden = false;
                els.pausedEscape.textContent = t('game.pausedResetHint');
            }
        }, PAUSED_ESCAPE_DELAY_MS);
    }

    function disarmPausedEscape() {
        if (pausedEscapeTimer !== null) {
            clearTimeout(pausedEscapeTimer);
            pausedEscapeTimer = null;
        }
        if (els.pausedEscape) els.pausedEscape.hidden = true;
    }

    // Issue #25 + #183 (Variant 1 split layout): render the optional
    // question image as a side-by-side focal point. Which URLs count as
    // safe is decided in one place, QuizifyUtils.safeImageUrl (#540) —
    // absolute http(s), or the integration's own static mount (#536).
    // Absent/invalid → the image pane is hidden and
    // .has-image is removed, so the column collapses cleanly to the
    // original full-width text-only layout. If the image fails to load
    // we fall back to the same text-only layout (graceful fallback).
    function setImageLayout(on) {
        if (els.dashboardLeft) els.dashboardLeft.classList.toggle('has-image', !!on);
        if (els.questionMedia) els.questionMedia.hidden = !on;
    }
    // #434: progressive reveal. Shared with the phone since #787 — the
    // arithmetic and the CSS custom property are identical on both, only
    // the canvas size and the element list differ, so both live in
    // QuizifyRenderShared.createProgressiveReveal and this passes its own.
    var REVEAL_MAX_BLUR_PX = 28;
    var reveal = window.QuizifyRenderShared.createProgressiveReveal({
        maxBlurPx: REVEAL_MAX_BLUR_PX,
        targets: function () { return [els.questionImage]; }
    });

    function setRevealBlur(remaining, duration) {
        reveal.set(remaining, duration);
    }

    function clearRevealBlur() {
        reveal.clear();
    }

    function renderQuestionImage(url, revealStyle) {
        var img = els.questionImage;
        if (!img) return;
        var safe = (window.QuizifyUtils && window.QuizifyUtils.safeImageUrl)
            ? window.QuizifyUtils.safeImageUrl(url) : '';
        // Reset first: the element is reused across rounds, so a stale
        // blur from the previous question must not survive into this one.
        clearRevealBlur();
        if (safe && revealStyle === 'progressive') {
            reveal.arm();
        }
        img.onerror = function() {
            // Image failed → collapse to text-only, no broken-image icon.
            img.removeAttribute('src');
            setImageLayout(false);
        };
        if (safe) {
            // #467: localized generic alt so the TV image question isn't
            // an unlabelled graphic for assistive tech.
            var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            img.src = safe;
            img.alt = t('game.questionImageAlt');
            setImageLayout(true);
        } else {
            img.removeAttribute('src');
            img.alt = '';
            setImageLayout(false);
        }
    }

    // Issue #736: warm the NEXT round's picture during the reveal.
    //
    // renderQuestionImage above only ever runs on `question_started`, by
    // which point player-core has already stamped the countdown deadline —
    // so the image downloads while the clock drains, and every phone in the
    // room is pulling the same file at the same instant. `round_summary`
    // carries the hint; the reveal is when nothing else is on the wire.
    //
    // Shared with the phone since #787: the sanitizer, the detached
    // `new Image()` and the reason it must never be els.questionImage are
    // the same argument on both surfaces.
    function preloadNextImage(url) {
        window.QuizifyRenderShared.preloadNextImage(url);
    }

    // ---- WebSocket ----
    // The URL rule, the parse-and-dispatch and the close-on-error live in
    // QuizifyClientCore (#787) — the same three the host page and the
    // phone use. The retry POLICY stays here on purpose: a television
    // retries forever on a flat two seconds, because there is nobody
    // standing at it to press anything and the room notices a dark board
    // long before it notices a missing frame.
    var DASHBOARD_RETRY_MS = 2000;

    function connect() {
        ws = window.QuizifyClientCore.createSocket('/api/quizify/ws?role=dashboard', {
            logPrefix: '[Dashboard]',
            onOpen: function () {
                // #421: socket is back — hide the reconnect pill. State is
                // re-requested below, so the stale frame is replaced promptly.
                if (els.reconnectPill) els.reconnectPill.hidden = true;
                ws.send(JSON.stringify({ type: 'get_state' }));
            },
            onMessage: handleMessage,
            onClose: function () {
                ws = null;
                // #421: surface the dead socket so a mid-question freeze doesn't
                // look like a live-but-stalled board. Hidden again in onopen.
                if (els.reconnectPill) els.reconnectPill.hidden = false;
                setTimeout(connect, DASHBOARD_RETRY_MS);
            }
        });
    }

    // ---- Message handler ----
    function handleMessage(msg) {
        switch (msg.type || msg.event) {
            case 'game_state':
                handleGameState(msg);
                break;
            case 'wager_progress':
                handleWagerProgress(msg);
                break;
            case 'question_started':
                // Clear last round's count before the new one arrives —
                // otherwise the TV shows "5/5" over a fresh question.
                if (els.answerProgress) {
                    els.answerProgress.textContent = '';
                    els.answerProgress.classList.add('hidden');
                    els.answerProgress.classList.remove('is-complete');
                }
                handleQuestionStarted(msg);
                break;
            case 'timer_tick':
                handleTimerTick(msg);
                break;
            // #664: the Hot Seat detour (#616) never reached this screen.
            // The room ran an auction on their phones while the TV kept
            // the previous round's reveal frozen on it — which does not
            // read as "something else is happening", it reads as a crash.
            case 'hot_seat_auction':
                // The payload carries seconds + player count, not the
                // round numbers — the frame keeps whatever the last round
                // put in the indicator.
                handleHotSeatAuction({ seconds: msg.seconds });
                handleHotSeatBidCount({ count: 0, total: msg.players || 0 });
                break;
            case 'hot_seat_bid_count':
                handleHotSeatBidCount(msg);
                break;
            case 'hot_seat_awarded':
                handleHotSeatAwarded(msg);
                break;
            case 'hot_seat_question':
                handleHotSeatQuestion(msg);
                break;
            case 'hot_seat_no_bids':
                handleHotSeatNoBids();
                break;
            case 'hot_seat_result':
                handleHotSeatResult(msg);
                break;
            case 'hot_seat_tick':
                handleTimerTick({ remaining: msg.remaining });
                break;
            case 'round_summary':
            case 'round_evaluated':
                handleRoundSummary(msg);
                // #736: same preload as the phones. The TV is the 21st
                // client in the burst and the only one showing the picture
                // full-screen, so it has the most to lose from a late one.
                preloadNextImage(msg.next_image_url);
                break;
            case 'leaderboard_update':
                // Kept: nothing has ever sent this (#619 found zero
                // senders), but a live TV cached from an older build may
                // still be listening, and the handler costs nothing.
                if (msg.leaderboard) renderLeaderboard(els.leaderboard, msg.leaderboard);
                break;
            case 'answer_progress':
                handleAnswerProgress(msg);
                break;
            case 'evening_tally':
                handleEveningTally(msg);
                break;
            case 'head_to_head':
                handleHeadToHead(msg);
                break;
            case 'finale':
            case 'game_ended':
                handleFinale(msg);
                break;
            case 'game_reset':
                currentPhase = 'LOBBY';
                setPaused(false);
                showView('waiting');
                // #374: back to the lobby — repaint QR + roster (a reset
                // may carry an already-trimmed player list, else empty).
                renderLobby(msg.players);
                els.roundIndicator.textContent = '';
                els.timerFill.style.width = '0%';
                // #807: showView('waiting') above already took the tally
                // down, but the markup would still be there for the next
                // finale to flash before its own payload lands. A reset
                // starts an evening over on this board.
                if (els.eveningTally) els.eveningTally.textContent = '';
                break;
            // #296: lightning round — the server broadcasts these events
            // to dashboards just like to the admin. Mirror admin.js's
            // handlers for the big screen.
            case 'lightning_splash':
                handleLightningSplash(msg);
                break;
            case 'lightning_question':
                handleLightningQuestion(msg);
                break;
            case 'lightning_tick':
                handleLightningTick(msg);
                break;
            case 'lightning_recap':
                handleLightningRecap(msg.recap || {});
                break;
            case 'player_joined':
            case 'player_left':
                // #374: keep the TV lobby roster live as players come/go.
                // The chips live inside #waiting-view, so this is a no-op
                // visually once the game leaves LOBBY (that view is hidden).
                if (msg.teams) lobbyTeams = msg.teams;
                lastLobbyPlayers = msg.players || lastLobbyPlayers;
                renderLobbyPlayers(msg.players);
                break;
            case 'teams_update':
                // A team formed or dissolved (#365) without the roster
                // changing — the TV has to follow that on its own.
                lobbyTeams = msg.teams || [];
                renderLobbyPlayers(lastLobbyPlayers);
                break;
            // #741: both of these are full broadcasts — the television
            // socket has always received them and had nowhere to put
            // them. Anna froze Ben and the board showed nothing; the
            // room watched points move with no account of why.
            case 'powerup_applied':
                handlePowerUpApplied(msg);
                break;
            case 'reaction':
                showDashboardReaction(msg.emoji);
                break;
            case 'reaction_bonus':
                handleReactionBonus(msg);
                break;
        }
    }

    // ============================================
    // #741 — power-ups and reactions on the big screen
    // ============================================

    // Only the power-ups that land on somebody *else* go on the board.
    // JOKER, DOUBLE_POINTS and TIME_BOOST change nothing but the user's
    // own turn: putting them up would mean something is on screen almost
    // constantly, and then none of it means anything.
    var POWERUP_BANNER_MS = 4000;
    var POWERUP_BANNER_EXIT_MS = 260;
    // The specs, the sentence and the reading of `powerup_applied` are
    // the host page's too, to the character — shared since #787. Only the
    // two span class names are the board's own.
    var BOARD_POWERUPS = window.QuizifyRenderShared.POWERUP_SPECS;
    var POWERUP_CLASSES = {
        name: 'dashboard-powerup-name',
        points: 'dashboard-powerup-points'
    };

    function powerUpSentenceHtml(spec, vars) {
        return window.QuizifyRenderShared.powerUpSentenceHtml(spec, vars, POWERUP_CLASSES);
    }

    var handlePowerUpApplied = window.QuizifyRenderShared.createPowerUpApplied({
        showBanner: function (spec, vars) { showPowerUpBanner(spec, vars); },
        showScoreDeltas: function (list) { showScoreDeltas(list); }
    });

    function showPowerUpBanner(spec, vars) {
        if (!els.powerupBanners) return;
        var el = document.createElement('div');
        el.className = 'dashboard-powerup-banner';
        el.innerHTML =
            '<span class="dashboard-powerup-icon" aria-hidden="true">' + spec.icon + '</span>' +
            '<span class="dashboard-powerup-text">' + powerUpSentenceHtml(spec, vars) + '</span>';
        els.powerupBanners.appendChild(el);
        // Each strip owns its own timer, so a second one arriving mid-stand
        // stacks underneath and the older one still leaves first.
        setTimeout(function () {
            el.classList.add('is-leaving');
            setTimeout(function () {
                if (el.parentNode) el.parentNode.removeChild(el);
            }, POWERUP_BANNER_EXIT_MS);
        }, POWERUP_BANNER_MS);
    }

    // Transient +/- chips on the two rows a steal moved. Held in state
    // rather than poked into the DOM, because game_state repaints the
    // leaderboard every few seconds and would wipe them mid-stand. The
    // store, the hold and the chip markup are the host page's too, so
    // both come from QuizifyRenderShared since #787; the repaint is this
    // board's own panel.
    var lastLeaderboardPlayers = null;
    // #865: the field the finale is currently showing, so the board can be
    // re-fitted (denser rows, fewer awards) without a new server frame.
    var finalePlayers = [];
    var _scoreDeltas = window.QuizifyRenderShared.createScoreDeltas({
        repaint: function () {
            if (lastLeaderboardPlayers) renderLeaderboard(els.leaderboard, lastLeaderboardPlayers);
        }
    });

    function showScoreDeltas(deltas) {
        _scoreDeltas.show(deltas);
    }

    function scoreDeltaHtml(name) {
        return _scoreDeltas.html(name);
    }

    function showDashboardReaction(emoji) {
        // Reveal only. Over a live question the movement competes with
        // reading the answers, which is the one thing the board is for.
        if (currentPhase !== 'ANSWER_REVEAL') return;
        if (!els.reactionLayer || !emoji) return;
        // No throttle of our own: the server already collapses the buffer
        // to one frame per distinct player+emoji per flush window.
        var el = document.createElement('div');
        el.className = 'dashboard-floating-reaction';
        el.textContent = emoji;
        el.style.left = (6 + Math.random() * 84) + '%';
        els.reactionLayer.appendChild(el);
        setTimeout(function () {
            if (el.parentNode) el.parentNode.removeChild(el);
        }, 3200);
    }

    function handleReactionBonus(msg) {
        // The +1s are already awarded and already on the server's
        // leaderboard — render it now instead of letting the number lag
        // the animation until the next game_state frame arrives.
        if (msg.leaderboard) renderLeaderboard(els.leaderboard, msg.leaderboard);
    }

    // #805: the game's language, not the television's. The board was
    // initialised once from the Home Assistant language and never looked
    // at `msg.language` again — although the server puts it in every
    // snapshot for exactly this, and both the phones
    // (`player-core.js` `_syncServerLanguage`) and the host page follow
    // it. A German HA running an English game framed English questions
    // in German on the one screen the whole room reads, while every
    // guest's phone had already switched.
    //
    // There is no per-device override to protect here: unlike the phone
    // (#492) a television has no language picker, so the HA language is
    // only the pre-game default. Elements the JS writes from keys
    // re-render on the next message; the sweep repaints the static
    // `data-i18n` labels ("Leaderboard", "Did you know?", "Awards").
    function syncServerLanguage(msg) {
        if (!msg || !msg.language || !window.QuizifyI18n) return false;
        if (QuizifyI18n.getLanguage() === msg.language) return false;
        QuizifyI18n.setLanguage(msg.language).then(function () {
            QuizifyI18n.initPageTranslations(document);
        });
        return true;
    }

    function handleGameState(msg) {
        syncServerLanguage(msg);
        if (msg.phase) currentPhase = msg.phase;
        if (msg.leaderboard) renderLeaderboard(els.leaderboard, msg.leaderboard);

        // #296: the paused scrim is independent of the underlying view —
        // set it on any PAUSED snapshot, clear it otherwise.
        setPaused(msg.phase === 'PAUSED', msg.pause_reason);

        switch (msg.phase) {
            case 'LOBBY':
                showView('waiting');
                // #374: render the join QR + live roster on the TV lobby.
                // Teams ride along on the snapshot (#365) so a TV that
                // connects late still shows who is playing with whom.
                if (msg.teams) lobbyTeams = msg.teams;
                renderLobby(msg.players);
                break;
            case 'WAGER_ACTIVE':
                // #656: a TV that (re)connects mid-window. The snapshot
                // has the category and the room's remaining seconds; the
                // tally arrives with the next bet.
                if (msg.wager) {
                    handleWagerProgress({
                        round_num: msg.round,
                        total_rounds: msg.total_rounds,
                        category: msg.wager.category,
                        locked_in: msg.wager.locked_in,
                        player_count: msg.wager.player_count,
                        window_duration: msg.wager.window_remaining,
                    });
                } else {
                    showView('question');
                }
                break;
            case 'QUESTION_ACTIVE':
                if (msg.question) {
                    handleQuestionStarted({
                        question_text: msg.question.text,
                        answers: msg.question.answers,
                        timer_duration: msg.question.time_limit,
                        round_num: msg.round,
                        total_rounds: msg.total_rounds,
                        category: msg.question.category,
                        image_url: msg.question.image_url,
                        reveal_style: msg.question.reveal_style,
                        time_remaining: msg.question.time_remaining,
                        question_type: msg.question.question_type,
                        estimate: msg.question.estimate,
                    });
                } else {
                    showView('question');
                }
                break;
            case 'ANSWER_REVEAL':
                // #296: a (re)connect during the reveal has no live
                // question_started to render from. The snapshot's nested
                // round_summary now carries the question + canonical
                // answers + correct index, so rebuild the question view
                // instead of leaving it blank.
                showView('question');
                // #275: estimate reveal on reconnect — render the number
                // line from the snapshot's estimate block.
                if (msg.round_summary && msg.round_summary.question_type === 'estimate' && msg.round_summary.estimate) {
                    els.questionText.textContent = msg.round_summary.question_text || '';
                    if (els.answersGrid) els.answersGrid.innerHTML = '';
                    renderDashboardEstimateReveal(msg.round_summary.estimate);
                } else if (msg.round_summary && msg.round_summary.answers) {
                    renderRevealFromSnapshot(msg.round_summary, msg.round, msg.total_rounds);
                }
                break;
            case 'HOT_SEAT_AUCTION':
            case 'HOT_SEAT':
            case 'HOT_SEAT_REVEAL':
                // A TV that (re)connects mid-detour. Same reasoning as
                // WAGER_ACTIVE above: the snapshot carries the stage and
                // the seconds, and everything else arrives with the next
                // message.
                if (msg.hot_seat) {
                    renderHotSeatFromSnapshot(msg.hot_seat, msg.round, msg.total_rounds);
                } else {
                    showView('question');
                }
                break;
            case 'PAUSED':
                // Freeze the bar where it stands; the scrim explains why.
                // The view behind stays whatever it was (we don't know the
                // pre-pause question from this snapshot, but the overlay
                // covers it). Keep the bar from any prior tick frozen.
                break;
            case 'LIGHTNING':
                showView('lightning');
                if (msg.lightning) {
                    // #425: seed the per-question duration for the timer bar.
                    if (typeof msg.lightning.seconds_per_question === 'number') {
                        lightningSeconds = Math.round(msg.lightning.seconds_per_question);
                    }
                    if (msg.lightning.splash_pending) {
                        handleLightningSplash({
                            num_questions: msg.lightning.num_questions,
                            seconds_per_question: msg.lightning.seconds_per_question,
                        });
                    } else {
                        var lq = msg.lightning.question;
                        handleLightningQuestion({
                            question_text: lq ? lq.text : '',
                            answers: lq ? lq.answers : [],
                            index: msg.lightning.index,
                            num_questions: msg.lightning.num_questions,
                        });
                    }
                    if (msg.lightning.leaderboard) {
                        renderLeaderboard(els.lightningLeaderboard, msg.lightning.leaderboard);
                    }
                }
                break;
            case 'LIGHTNING_RECAP':
                if (msg.lightning_recap) handleLightningRecap(msg.lightning_recap);
                else showView('lightningRecap');
                break;
            case 'FINALE':
                handleFinale(msg);
                break;
        }
    }

    // #296: rebuild the reveal question view from the snapshot. Renders
    // the same markup as handleQuestionStarted, then applies the correct
    // highlight via the canonical original-order index. Reuses the live
    // reveal classes (.correct / .wrong / .revealed) so the look matches.
    function renderRevealFromSnapshot(rs, round, totalRounds) {
        els.funFact.classList.remove('visible');
        els.roundIndicator.textContent = t('dashboard.questionCounter',
            'Question ' + round + ' / ' + totalRounds)
            .replace('{current}', round).replace('{total}', totalRounds);
        els.questionCategory.textContent = rs.category || '';
        els.questionText.textContent = rs.question_text || '';
        renderQuestionImage(rs.image_url);
        // The reveal is over — freeze the bar full (no countdown on reveal).
        els.timerFill.style.width = '0%';
        els.timerFill.className = 'dashboard-timer-fill';

        var labels = ['A', 'B', 'C', 'D'];
        var answers = rs.answers || [];
        // #521: the snapshot's answers ride the round shuffle, so the
        // highlight must use the matching index. Older servers send only
        // the question-JSON index, and their grid is unshuffled too — the
        // fallback keeps those in step.
        var correctIdx = (typeof rs.correct_answer_index === 'number')
            ? rs.correct_answer_index
            : (typeof rs.correct_answer_index_original === 'number')
                ? rs.correct_answer_index_original : -1;
        els.answersGrid.innerHTML = answers.map(function(answer, i) {
            var text = (answer && typeof answer === 'object') ? answer.text : answer;
            var cls = 'dashboard-answer revealed ' + (i === correctIdx ? 'correct' : 'wrong');
            return '<div class="' + cls + '" data-index="' + i + '">' +
                '<span class="answer-label">' + (labels[i] || '') + '</span>' +
                '<span class="answer-text">' + escapeHtml(text) + '</span>' +
                '</div>';
        }).join('');

        if (rs.fun_fact) {
            els.funFactText.textContent = rs.fun_fact;
            setTimeout(function() { els.funFact.classList.add('visible'); }, 300);
        }
    }

    // ---- Lightning (#296) — mirrors admin.js handlers for the TV. ----
    function handleLightningSplash(msg) {
        currentPhase = 'LIGHTNING';
        showView('lightning');
        if (els.lightningSplash) els.lightningSplash.hidden = false;
        if (els.lightningQuestionSection) els.lightningQuestionSection.hidden = true;
        if (els.lightningSplashRules) {
            var n = (typeof msg.num_questions === 'number') ? msg.num_questions : 5;
            var s = (typeof msg.seconds_per_question === 'number')
                ? Math.round(msg.seconds_per_question) : 15;
            // #425: remember the round's duration for the shared timer bar.
            lightningSeconds = s;
            els.lightningSplashRules.textContent =
                t('lightning.startHint', n + ' questions · ' + s + 's each');
        }
    }

    function handleLightningQuestion(msg) {
        currentPhase = 'LIGHTNING';
        showView('lightning');
        if (els.lightningSplash) els.lightningSplash.hidden = true;
        if (els.lightningQuestionSection) els.lightningQuestionSection.hidden = false;
        if (els.lightningProgress) {
            els.lightningProgress.textContent =
                ((msg.index || 0) + 1) + ' / ' + (msg.num_questions || 5);
        }
        if (els.lightningQuestionText) {
            els.lightningQuestionText.textContent = msg.question_text || '';
        }
        var labels = ['A', 'B', 'C', 'D'];
        var answers = msg.answers || [];
        if (els.lightningAnswersGrid) {
            els.lightningAnswersGrid.innerHTML = answers.map(function(answer, i) {
                var text = (answer && typeof answer === 'object') ? answer.text : answer;
                return '<div class="dashboard-answer" data-index="' + i + '">' +
                    '<span class="answer-label">' + (labels[i] || '') + '</span>' +
                    '<span class="answer-text">' + escapeHtml(text) + '</span>' +
                    '</div>';
            }).join('');
        }
    }

    function handleLightningTick(msg) {
        // Lightning has its own clock; show it on the shared timer bar so
        // the room sees the countdown. The server sends remaining seconds.
        if (typeof msg.remaining !== 'number') return;
        // #425: use the round's real seconds_per_question (seeded from the
        // splash / game_state payload) so a non-15s round scales correctly.
        var total = lightningSeconds > 0 ? lightningSeconds : 15;
        var pct = Math.max(0, Math.min(100, (msg.remaining / total) * 100));
        els.timerFill.style.width = pct + '%';
        els.timerFill.className = 'dashboard-timer-fill';
        if (msg.remaining <= 5) els.timerFill.classList.add('critical');
    }

    function handleLightningRecap(recap) {
        currentPhase = 'LIGHTNING_RECAP';
        showView('lightningRecap');
        if (els.lightningRecapLeaderboard) {
            renderLeaderboard(els.lightningRecapLeaderboard, recap.leaderboard || []);
        }
        if (els.lightningRecapGrid) {
            var questions = recap.questions || [];
            els.lightningRecapGrid.innerHTML = questions.map(function(q, qi) {
                return '<div class="dashboard-lightning-recap-row">' +
                    '<span class="lr-qnum">' + (qi + 1) + '.</span>' +
                    escapeHtml(q.question_text) +
                    ' <span class="lr-correct">→ ' + escapeHtml(q.correct_answer) + '</span>' +
                    '</div>';
            }).join('');
        }
    }

    /**
     * The final round's betting window (#656).
     *
     * The TV shows the category and the lock-in tally, and nothing else:
     * no question (it has not been sent) and no amounts (a bet the room
     * can read off the screen is not a bet). Its bar counts the window
     * down, not the answer clock — that one has not started yet.
     */
    function handleWagerProgress(msg) {
        currentPhase = 'WAGER_ACTIVE';
        showView('question');
        els.funFact.classList.remove('visible');

        els.roundIndicator.textContent = window.QuizifyI18n
            ? window.QuizifyI18n.t('dashboard.questionCounter', { current: msg.round_num, total: msg.total_rounds })
            : 'Question ' + msg.round_num + ' / ' + msg.total_rounds;
        els.questionCategory.textContent = msg.category || '';
        var title = window.QuizifyI18n
            ? window.QuizifyI18n.t('wager.hostWindowTitle') : 'Placing bets';
        var progress = window.QuizifyI18n
            ? window.QuizifyI18n.t('wager.hostProgress', { locked: msg.locked_in, total: msg.player_count })
            : msg.locked_in + ' / ' + msg.player_count;
        // #808: the room's stake is decided off the category and nothing
        // else — no question has been sent. Leading the question slot with
        // it puts that one fact in question-size type instead of leaving
        // it to the eyebrow above, which a three-metre couch reads last.
        // The lock-in count stays: the room still has to see who it is
        // waiting for.
        els.questionText.textContent = msg.category
            ? title + ' · ' + msg.category + ' — ' + progress
            : title + ' — ' + progress;
        renderQuestionImage('', '');
        if (els.answersGrid) els.answersGrid.innerHTML = '';
        if (els.dashboardEstimate) els.dashboardEstimate.classList.add('hidden');

        // Only the opening message carries the duration. Restarting the
        // bar on every incoming bet would make the window look longer the
        // more people play.
        if (typeof msg.window_duration === 'number') {
            timerDuration = msg.window_duration;
            timerRemaining = timerDuration;
            els.timerFill.style.width = '100%';
            els.timerFill.className = 'dashboard-timer-fill';
        }
    }

    /* ------------------------------------------------------------
     * Hot Seat on the television (#664).
     *
     * Deliberately rendered into the existing question view rather than
     * a view of its own, exactly like the betting window: the room is
     * looking at the same frame, and one auction line replacing the
     * question text is all the detour needs to stop looking broken.
     * ---------------------------------------------------------- */
    function hotSeatT(key, vars, fallback) {
        return (window.QuizifyI18n && window.QuizifyI18n.t)
            ? window.QuizifyI18n.t(key, vars)
            : fallback;
    }

    function hotSeatFrame(round, total) {
        showView('question');
        els.funFact.classList.remove('visible');
        if (els.roundIndicator && typeof round === 'number') {
            els.roundIndicator.textContent = hotSeatT(
                'dashboard.questionCounter', { current: round, total: total },
                'Question ' + round + ' / ' + total
            );
        }
        renderQuestionImage('', '');
        if (els.answersGrid) els.answersGrid.innerHTML = '';
        if (els.dashboardEstimate) els.dashboardEstimate.classList.add('hidden');
    }

    function handleHotSeatAuction(msg) {
        currentPhase = 'HOT_SEAT_AUCTION';
        hotSeatFrame(msg.round_num, msg.total_rounds);
        els.questionCategory.textContent = '';
        els.questionText.textContent = hotSeatT(
            'hotSeat.auctionTitle', null, 'The chair goes to the highest bid'
        );
        if (typeof msg.seconds === 'number') {
            timerDuration = msg.seconds;
            timerRemaining = timerDuration;
            els.timerFill.style.width = '100%';
            els.timerFill.className = 'dashboard-timer-fill';
        }
    }

    function handleHotSeatBidCount(msg) {
        // The count, never the amounts — the auction is sealed, and the
        // screen everyone can see is the last place to leak it.
        if (!els.answerProgress) return;
        els.answerProgress.textContent =
            window.QuizifyRenderShared.hotSeatBidCountText(msg);
        els.answerProgress.classList.remove('hidden');
    }

    function handleHotSeatAwarded(msg) {
        hotSeatFrame();
        els.questionCategory.textContent = '';
        // #804/#787: who pays for the chair, not who sits in it — read once,
        // in render-shared.js, for all three screens.
        els.questionText.textContent =
            window.QuizifyRenderShared.hotSeatAward(msg).text;
        if (els.answerProgress) els.answerProgress.classList.add('hidden');
    }

    function handleHotSeatQuestion(msg) {
        currentPhase = 'HOT_SEAT';
        // ``question`` is the field name on the wire (see
        // _broadcast_hot_seat_question); the snapshot path passes the
        // same shape so both routes land here identically.
        handleQuestionStarted({
            question_text: msg.question || '',
            answers: msg.answers || [],
            timer_duration: msg.seconds,
            round_num: msg.round_num,
            total_rounds: msg.total_rounds,
            category: msg.category || '',
            image_url: msg.image_url || ''
        });
    }

    function handleHotSeatResult(msg) {
        currentPhase = 'HOT_SEAT_REVEAL';
        // #698: the settlement had no consumer on any surface. The board
        // stayed on the question with the bar at zero until the host
        // advanced, so the room never learned whether the chair paid off —
        // which is the only thing everyone was watching for.
        hotSeatFrame(msg.round_num, msg.total_rounds);
        els.questionCategory.textContent = '';
        // The tri-state read of ``answered`` and the #804 delta lookup are
        // the shared renderer's since #787 — all three screens have to tell
        // the room the same story, and the host page had already drifted.
        els.questionText.textContent =
            window.QuizifyRenderShared.hotSeatSettlement(msg).text;
        // #833: the chair is the biggest single points swing in the game,
        // and the board beside the headline was still showing the previous
        // standing — the player who had just lost everything, first. The
        // settlement is applied server-side before this frame is built, so
        // these are the numbers the host page shows after a reload.
        if (msg.leaderboard) renderLeaderboard(els.leaderboard, msg.leaderboard);
        // #833: and the answer, so the left column is not one line over a
        // blank screen while the room waits for the host. Rendered as the
        // reveal's own correct tile rather than a new kind of element —
        // the room has been reading that shape all game. The letter label
        // is left off on purpose: the tile is alone, so "B" would name a
        // position in a grid that is no longer on screen.
        if (els.answersGrid) {
            els.answersGrid.innerHTML = msg.correct_answer
                ? '<div class="dashboard-answer revealed correct">' +
                      '<span class="answer-text">' +
                      escapeHtml(msg.correct_answer) + '</span>' +
                  '</div>'
                : '';
        }
        if (els.answerProgress) els.answerProgress.classList.add('hidden');
        if (els.timerFill) els.timerFill.style.width = '0%';
    }

    function handleHotSeatNoBids() {
        hotSeatFrame();
        els.questionCategory.textContent = '';
        els.questionText.textContent = hotSeatT(
            'hotSeat.noBids', null, 'Nobody bid — carrying on as usual.'
        );
        if (els.answerProgress) els.answerProgress.classList.add('hidden');
    }

    function renderHotSeatFromSnapshot(hs, round, total) {
        if (hs.stage === 'auction') {
            handleHotSeatAuction({
                round_num: round, total_rounds: total, seconds: hs.time_remaining
            });
            handleHotSeatBidCount({ count: hs.bid_count, total: hs.bidder_count });
            return;
        }
        if (hs.stage === 'question' && hs.question) {
            handleHotSeatQuestion({
                question: hs.question.text,
                answers: hs.question.answers || [],
                seconds: hs.time_remaining,
                round_num: round,
                total_rounds: total,
                category: hs.question.category,
                image_url: hs.question.image_url
            });
            return;
        }
        // #874: the server's third stage. HOT_SEAT_REVEAL lasts until the
        // host taps Next, so any television that reloads inside that
        // window used to fall through to the awarded line and print the
        // auction price ("Anna - 40%") over the settlement everyone was
        // waiting for -- no correct answer, no outcome, at the biggest
        // points swing of the game. ``hot_seat.summary`` is the same dict
        // the live ``hot_seat_result`` frame spreads, so the restored
        // board renders the identical frame.
        if (hs.stage === 'result' && hs.summary) {
            handleHotSeatResult(Object.assign(
                { round_num: round, total_rounds: total }, hs.summary
            ));
            return;
        }
        handleHotSeatAwarded({
            // #804: the snapshot carries both, so a television that
            // reconnects mid-detour names the payer the same way the live
            // frame did.
            winner: hs.winner, entrant: hs.entrant,
            pct: hs.pct, stake: hs.stake
        });
    }

    function handleQuestionStarted(msg) {
        currentPhase = 'QUESTION_ACTIVE';
        showView('question');
        els.funFact.classList.remove('visible');

        els.roundIndicator.textContent = window.QuizifyI18n
            ? window.QuizifyI18n.t('dashboard.questionCounter', { current: msg.round_num, total: msg.total_rounds })
            : 'Question ' + msg.round_num + ' / ' + msg.total_rounds;
        els.questionCategory.textContent = msg.category || '';
        els.questionText.textContent = msg.question_text;
        renderQuestionImage(msg.image_url, msg.reveal_style);

        timerDuration = msg.timer_duration || 30;
        timerRemaining = timerDuration;
        // #434: a board that (re)connects mid-question gets its remaining
        // time in the snapshot, so the blur resumes where the round
        // actually is instead of restarting from opaque.
        setRevealBlur(
            (typeof msg.time_remaining === 'number') ? msg.time_remaining : timerDuration,
            timerDuration
        );
        els.timerFill.style.width = '100%';
        els.timerFill.className = 'dashboard-timer-fill';

        // #275: estimate rounds show a slider range hint instead of the
        // A/B/C grid during the question; the number line appears at reveal.
        if (msg.question_type === 'estimate' && msg.estimate) {
            if (els.answersGrid) els.answersGrid.innerHTML = '';
            if (els.dashboardEstimate) {
                els.dashboardEstimate.classList.remove('hidden');
                var est = msg.estimate;
                var unit = est.unit || '';
                function fmt(v) { return unit ? (v + ' ' + unit) : v; }
                var rangeLabel = window.QuizifyI18n
                    ? window.QuizifyI18n.t('estimate.dashRange', { min: fmt(est.min), max: fmt(est.max) })
                    : 'Estimate between ' + fmt(est.min) + ' and ' + fmt(est.max);
                els.dashboardEstimate.innerHTML =
                    '<div class="dashboard-estimate-hint">' +
                        '<span class="dashboard-estimate-badge-dot" aria-hidden="true"></span>' +
                        escapeHtml(rangeLabel) +
                    '</div>';
            }
            return;
        }
        if (els.dashboardEstimate) els.dashboardEstimate.classList.add('hidden');

        // Render answers
        var labels = ['A', 'B', 'C', 'D'];
        currentAnswers = msg.answers || [];
        els.answersGrid.innerHTML = currentAnswers.map(function(answer, i) {
            // Answer payloads arrive in two shapes: the `question_started`
            // (admin/cast) message sends objects `{text, correct}`, while the
            // `game_state` snapshot sends plain strings. Normalize to the
            // displayable string so the cast/TV view never renders the raw
            // object as "[object Object]" (issue #283).
            var text = (answer && typeof answer === 'object') ? answer.text : answer;
            // Distribution markup is included up-front but kept hidden via
            // the .revealed class on the parent (issue #151). Embedding it
            // at question-render time avoids a DOM thrash at reveal — only
            // the fill-width + percent-text change.
            return '<div class="dashboard-answer" data-index="' + i + '">' +
                '<span class="answer-label">' + (labels[i] || '') + '</span>' +
                '<span class="answer-text">' + escapeHtml(text) + '</span>' +
                '<div class="dashboard-answer-distribution" aria-hidden="true">' +
                    '<div class="dashboard-answer-bar"><div class="dashboard-answer-bar-fill"></div></div>' +
                    '<span class="dashboard-answer-percent">0%</span>' +
                '</div>' +
                '</div>';
        }).join('');
    }

    function handleHeadToHead(msg) {
        // #613: the same line in two places. `at` picks the target — the
        // lobby before the game, the end screen after it, where the score
        // already includes the game just played.
        var target = (msg && msg.at === 'finale') ? els.endH2h : els.lobbyH2h;
        if (!target) return;
        if (!msg || !msg.left || !msg.right) {
            target.classList.add('hidden');
            return;
        }
        var t = (window.QuizifyI18n && window.QuizifyI18n.t)
            || function (k) { return k; };
        // "last 90 days" is stated, not implied: the detailed history
        // prunes at RETENTION_DAYS, so calling this an all-time record
        // would be a claim the data cannot support.
        target.innerHTML =
            '<span class="h2h-label">' + escapeHtml(t('dashboard.h2hLabel')) + '</span>'
            + escapeHtml(msg.left) + ' ' + msg.left_wins
            + ' – ' + msg.right_wins + ' ' + escapeHtml(msg.right)
            + '<span class="h2h-scope">' + escapeHtml(t('dashboard.h2hRecent')) + '</span>';
        target.classList.remove('hidden');
        // #775: on the end screen this line takes a slice of the column the
        // awards were already sized against — it arrives a beat later, on
        // analytics_recorded. Re-fit rather than let it push a card out of
        // the picture.
        // #865: it is also the slice that turned a four-player board into a
        // three-and-a-bit one, so the whole board is re-fitted, not only
        // the awards.
        if (target === els.endH2h) fitFinaleBoard();
    }

    function handleEveningTally(msg) {
        if (!els.eveningTally) return;
        var leaders = (msg && msg.leaders) || [];
        if (!leaders.length) {
            els.eveningTally.classList.add('hidden');
            return;
        }
        var t = (window.QuizifyI18n && window.QuizifyI18n.t)
            || function (k) { return k; };
        // Three names is what reads from a couch; a fourth turns the line
        // into a table nobody parses mid-celebration.
        var parts = leaders.slice(0, 3).map(function (entry) {
            var wins = entry.wins === 1
                ? t('dashboard.tonightWinsOne')
                : t('dashboard.tonightWins', { wins: entry.wins });
            return escapeHtml(entry.name) + ' ' + wins;
        });
        // parts[] is already escaped per entry — escaping the joined
        // string again would turn a name like "Jan & Anna" into
        // "Jan &amp;amp; Anna" on screen.
        els.eveningTally.innerHTML =
            '<span class="tally-label">' + escapeHtml(t('dashboard.tonightLabel'))
            + '</span>' + parts.join(' · ');
        els.eveningTally.classList.remove('hidden');
    }

    function handleAnswerProgress(msg) {
        if (!els.answerProgress) return;
        var total = msg.total || 0;
        if (!total) {
            els.answerProgress.classList.add('hidden');
            return;
        }
        var submitted = msg.submitted || 0;
        els.answerProgress.textContent = submitted + '/' + total;
        els.answerProgress.classList.remove('hidden');
        els.answerProgress.classList.toggle('is-complete', submitted === total);
    }

    function handleTimerTick(msg) {
        // #296: ignore stray ticks while paused so the bar holds frozen.
        // (The server already stops sending ticks on pause; this guards a
        // late/in-flight tick that would otherwise resume the drain.)
        if (isPaused) return;
        timerRemaining = msg.remaining;
        setRevealBlur(timerRemaining, timerDuration);
        var pct = timerDuration > 0 ? (timerRemaining / timerDuration) * 100 : 0;
        els.timerFill.style.width = pct + '%';

        els.timerFill.className = 'dashboard-timer-fill';
        if (timerRemaining <= 5) {
            els.timerFill.classList.add('critical');
        } else if (timerRemaining <= 10) {
            els.timerFill.classList.add('warning');
        }
    }

    function handleRoundSummary(msg) {
        currentPhase = 'ANSWER_REVEAL';
        var summary = msg.round_summary || msg;

        // #434: the summary keeps the question view's image element, so
        // the blur has to come off here or the answer is revealed beside a
        // picture that is still unreadable.
        clearRevealBlur();

        // #460: reset the timer bar at reveal. A skipped/timed-out question
        // otherwise leaves a stalled red/pulsing bar during the summary.
        // Mirror renderRevealFromSnapshot: width 0% + clean className.
        if (els.timerFill) {
            els.timerFill.style.width = '0%';
            els.timerFill.className = 'dashboard-timer-fill';
        }

        // #275: estimate rounds render a number line instead of the MC
        // answer-tile highlight + distribution.
        if (summary.question_type === 'estimate' && summary.estimate) {
            renderDashboardEstimateReveal(summary.estimate);
            var efact = summary.fun_fact || '';
            if (efact) {
                els.funFactText.textContent = efact;
                setTimeout(function() { els.funFact.classList.add('visible'); }, 300);
            }
            var elb = summary.leaderboard || msg.leaderboard || [];
            if (elb.length) renderLeaderboard(els.leaderboard, elb);
            return;
        }

        // Highlight correct answer.
        // Until #521 this preferred the ORIGINAL-order index, because the
        // grid was drawn in question-JSON order. It now rides the round
        // shuffle (the same order TTS speaks), so the canonical index is
        // the one that points at the right tile. `answer_distribution`
        // below is emitted in that same space.
        var correctIdx = summary.correct_answer_index;
        if (correctIdx == null || correctIdx < 0) {
            correctIdx = summary.correct_answer_index_original != null
                ? summary.correct_answer_index_original : -1;
        }
        var answerEls = els.answersGrid.querySelectorAll('.dashboard-answer');

        // Issue #151: render live answer distribution at reveal.
        // Server sends [{index, count, percent}, ...]; empty when no one
        // submitted. The .revealed class triggers the CSS fade-in; bar
        // fill widths animate from 0 % to their target. Tabular-num
        // percent text avoids width-jitter as the value updates.
        var distByIdx = {};
        (summary.answer_distribution || []).forEach(function(d) {
            if (d && typeof d.index === 'number') distByIdx[d.index] = d;
        });

        answerEls.forEach(function(el) {
            var idx = parseInt(el.dataset.index, 10);
            if (idx === correctIdx) {
                el.classList.add('correct');
            } else {
                el.classList.add('wrong');
            }
            el.classList.add('revealed');

            var entry = distByIdx[idx];
            var pct = (entry && typeof entry.percent === 'number') ? entry.percent : 0;
            var fill = el.querySelector('.dashboard-answer-bar-fill');
            var pctEl = el.querySelector('.dashboard-answer-percent');
            if (fill) fill.style.width = pct + '%';
            if (pctEl) pctEl.textContent = pct + '%';
        });

        // Fun fact
        var fact = summary.fun_fact || '';
        if (fact) {
            els.funFactText.textContent = fact;
            setTimeout(function() { els.funFact.classList.add('visible'); }, 300);
        }

        // Leaderboard
        var lb = summary.leaderboard || msg.leaderboard || [];
        if (lb.length) renderLeaderboard(els.leaderboard, lb);
    }

    // #275: TV number-line reveal — every player's guess plotted along the
    // range, the true value pinned, the winner highlighted, ranked list.
    function renderDashboardEstimateReveal(est) {
        if (!els.dashboardEstimate) return;
        els.dashboardEstimate.classList.remove('hidden');
        if (els.answersGrid) els.answersGrid.innerHTML = '';

        var unit = est.unit || '';
        var answer = Number(est.answer);
        var guesses = (est.guesses || []).filter(function(g) {
            return !g.no_guess && g.guess !== null && g.guess !== undefined;
        });

        function fmt(v) {
            if (v === null || v === undefined) return '—';
            var n = Math.round(v * 1000) / 1000;
            return (n === Math.round(n)) ? String(Math.round(n)) : String(n);
        }

        var dataVals = guesses.map(function(g) { return Number(g.guess); }).concat([answer]);
        var lo = Math.min.apply(null, dataVals), hi = Math.max.apply(null, dataVals);
        if (!(hi > lo)) hi = lo + 1;
        var span = hi - lo, pad = span * 0.18;
        lo -= pad; hi += pad;
        if (isFinite(Number(est.min))) lo = Math.max(lo, Number(est.min) - span * 0.02);
        if (isFinite(Number(est.max))) hi = Math.min(hi, Number(est.max) + span * 0.02);
        span = hi - lo;
        if (!(span > 0)) { hi = lo + 1; span = 1; }
        function pct(v) { return Math.max(0, Math.min(100, ((v - lo) / span) * 100)); }

        var sortedMarkers = guesses.slice().sort(function(a, b) { return Number(a.guess) - Number(b.guess); });
        var markers = sortedMarkers.map(function(g, idx) {
            var left = pct(Number(g.guess));
            var isWinner = g.rank === 1;
            var pos = (idx % 2 === 0) ? 'below' : 'above';
            var dotStyle = 'background:' + (g.color || '#7FA8C4') + ';';
            if (isWinner) dotStyle += 'width:20px;height:20px;border-color:#E8C47F;';
            return '<div class="dnl-tick" style="left:' + left.toFixed(1) + '%;">' +
                '<div class="dnl-dot" style="' + dotStyle + '"></div>' +
                '<div class="dnl-lbl dnl-lbl--' + pos + (isWinner ? ' is-winner' : '') + '">' +
                    escapeHtml(g.player_name) + (isWinner ? ' ★' : '') + ' ' + escapeHtml(fmt(Number(g.guess))) +
                '</div></div>';
        }).join('');

        var truthLeft = pct(answer);
        var truth = '<div class="dnl-truth-line" style="left:' + truthLeft.toFixed(1) + '%;"></div>' +
            '<div class="dnl-truth-flag" style="left:' + truthLeft.toFixed(1) + '%;">' + escapeHtml(fmt(answer)) + ' ✓</div>';

        var truthLabel = window.QuizifyI18n ? window.QuizifyI18n.t('estimate.correctAnswer') : 'Correct answer';

        els.dashboardEstimate.innerHTML =
            '<div class="dnl-truth"><div class="dnl-truth-label">' + escapeHtml(truthLabel) + '</div>' +
                '<div class="dnl-truth-value">' + escapeHtml(fmt(answer)) + (unit ? ' ' + escapeHtml(unit) : '') + '</div></div>' +
            '<div class="dnl-card"><div class="dnl-axis">' + markers + truth + '</div>' +
                '<div class="dnl-scale-ends"><span>' + escapeHtml(fmt(lo)) + '</span><span>' + escapeHtml(fmt(hi)) + '</span></div></div>';
    }

    function handleFinale(msg) {
        currentPhase = 'FINALE';
        showView('finale');

        // #777: the counter belongs to the questions, not to the result.
        // Left alone it kept saying "Question 5 / 5" under an empty
        // progress bar — the two contradicted each other on the same
        // screen and it read as if a sixth question were coming. The
        // result screen already names itself ("Result"), so the header
        // line goes quiet, exactly as it does on game_reset.
        if (els.roundIndicator) els.roundIndicator.textContent = '';
        els.timerFill.style.width = '0%';

        // #613: clear last game's duel before this game's arrives on
        // analytics_recorded, a beat later. Leaving it up would show the
        // PREVIOUS game's standing under this game's podium.
        if (els.endH2h) {
            els.endH2h.classList.add('hidden');
            els.endH2h.innerHTML = '';
        }

        // #787: both boards read the same two collections off this frame,
        // including `all_players` — the older field name the snapshot path
        // still sends. Wire-format knowledge, so it is read in one place.
        var standings = window.QuizifyRenderShared.finaleStandings(msg);
        renderPodium(standings.podium);

        // #865: kept so the board can be re-fitted without a new frame
        // from the server — the duel line arrives a beat later, and a
        // television can change resolution while the result is up.
        finalePlayers = standings.leaderboard;
        renderLeaderboard(els.finaleLeaderboard, finalePlayers);

        renderAwards(msg.superlatives || []);
        fitFinaleBoard();

        // Soft Parlor: no confetti. The warm pause IS the effect. See DESIGN.md.
    }

    // ---- Renderers ----
    function renderLeaderboard(container, players, limit) {
        if (!container || !players) return;
        // #741: remembered so an expiring steal chip can repaint the same
        // rows without waiting for the next frame from the server.
        if (container === els.leaderboard) lastLeaderboardPlayers = players;
        // Show top 5 in game view, all in finale
        var capped = container === els.leaderboard;
        // #865: the finale may pass a smaller `limit` when a television is
        // too short for the whole field even at the densest rows — see
        // fitFinaleBoard(). Left alone it is still the whole field, which
        // is the case this screen exists for.
        var max = capped ? 5 : (limit > 0 ? limit : players.length);
        var list = players.slice(0, max);
        // The row itself is the same on all three surfaces (#787); the cap
        // and the "+N more" tail below are this panel's layout, not the
        // row's shape, so they stay here.
        var html = window.QuizifyRenderShared.leaderboardRowsHtml(list, {
            afterScore: function (p) { return scoreDeltaHtml(p.name); }
        });
        // #429: the in-game panel caps at 5 rows — hint the hidden tail so a
        // 6th+ player isn't silently dropped. The panel is overflow-y:auto,
        // so the muted row is reachable if the list scrolls.
        var hidden = players.length - list.length;
        if (hidden > 0) {
            html += '<div class="leaderboard-row leaderboard-more">' +
                '<span class="leaderboard-name">' +
                t('dashboard.leaderboardMore', '+' + hidden + ' more')
                    .replace('{count}', hidden) +
                '</span></div>';
        }
        container.innerHTML = html;
    }

    function renderPodium(podium) {
        // Per Markus 2026-05-29 (msg 320): names + medal + score sit on
        // TOP of each podium column (in a flex label wrapper), bar at
        // bottom. With `justify-content: space-between` on .podium-place
        // and a fixed total height, labels align across columns and
        // bars share a baseline.
        //
        // #883: the planks are the shared renderer's now. This page and the
        // host console wrote the same three columns by hand and had drifted
        // into two DOM shapes for one feature — the medal and the label
        // wrapper are what the television adds, so they are what it asks for.
        var ptsLabel = window.QuizifyI18n ? window.QuizifyI18n.t('dashboard.pointsShort') : 'pts';
        els.podium.innerHTML = window.QuizifyRenderShared.podiumHtml(podium, {
            pointsLabel: ptsLabel,
            medals: true
        });

        // Render players 4+ in the side panel.
        var othersEl = document.getElementById('podium-others');
        if (!othersEl) return;
        var others = podium.slice(3);
        if (!others.length) {
            othersEl.setAttribute('hidden', '');
            othersEl.innerHTML = '';
            return;
        }
        othersEl.removeAttribute('hidden');
        var title = window.QuizifyI18n ? window.QuizifyI18n.t('dashboard.runnersUp') : 'Also playing';
        othersEl.innerHTML = '<div class="podium-others-title">' + escapeHtml(title) + '</div>' +
            others.map(function(p, i) {
                var rank = i + 4;  // 4th, 5th, \u2026
                return '<div class="podium-other-row">' +
                    '<span class="podium-other-rank">#' + rank + '</span>' +
                    '<span class="podium-other-name">' + escapeHtml(p.name) + '</span>' +
                    '<span class="podium-other-score">' + p.score + '</span>' +
                '</div>';
            }).join('');
    }

    // #733: resolve an i18n key, falling back to the server's English
    // string. QuizifyI18n.t() hands back the key itself when nothing
    // matches, which is exactly how a missing key reaches the screen
    // unnoticed — treat that as "no translation" and use the fallback.
    function translateOrFallback(key, params, fallback) {
        if (!key || !window.QuizifyI18n) return fallback || '';
        var translated = window.QuizifyI18n.t(key, params || undefined);
        if (!translated || translated === key) return fallback || '';
        return translated;
    }

    function renderAwards(superlatives) {
        var section = document.getElementById('awards-section');
        if (!section || !superlatives || !superlatives.length) {
            if (section) section.classList.add('hidden');
            return;
        }
        section.classList.remove('hidden');
        var awardsLabel = window.QuizifyI18n ? window.QuizifyI18n.t('dashboard.awards') : 'Awards';
        section.innerHTML = '<div class="awards-title">' + escapeHtml(awardsLabel) + '</div><div class="awards-grid"></div>';
        var grid = section.querySelector('.awards-grid');

        superlatives.forEach(function(s, i) {
            var card = document.createElement('div');
            card.className = 'award-card';
            card.style.animationDelay = (i * 0.5) + 's';
            // Compact pill layout (Markus 2026-05-29 msg 323): icon
            // owns the left chip, award-name + winner share one line,
            // detail wraps as a quiet sub-line.
            //
            // #733: the payload carries award_key / detail_key /
            // detail_params next to the English award/detail strings.
            // Rendering the English ones put "Fastest Finger · avg 4.2s
            // per correct answer" under a German "Auszeichnungen"
            // heading while the guests' phones — which have always used
            // the keys (js/player-end.js) — showed the German wording
            // for the same award.
            var awardName = translateOrFallback(s.award_key, null, s.award);
            var awardDetail = translateOrFallback(s.detail_key, s.detail_params, s.detail);
            card.innerHTML =
                '<div class="award-icon">' + escapeHtml(s.icon) + '</div>' +
                '<div class="award-text">' +
                    '<div class="award-name">' + escapeHtml(awardName) + ' · <span class="award-winner">' + escapeHtml(s.winner) + '</span></div>' +
                    '<div class="award-detail">' + escapeHtml(awardDetail) + '</div>' +
                '</div>';
            grid.appendChild(card);
        });
        // #865: the fit is the caller's, because the awards are no longer
        // the only block being sized — fitFinaleBoard() decides the split
        // between them and the leaderboard and calls fitAwards() itself.
    }

    /* #775: the height the finale leaderboard needs — its header plus the
       rows now inside it — read off the card that is on screen rather than
       carried as a constant. Every hand-derived pixel figure in this file
       went stale the moment a font or a padding moved; this one cannot.

       #865: it used to ask for three rows and call that a floor (#695).
       Three rows is not a floor on a television, it is a lid: a four-player
       game — the smallest party this mode is for — left the fourth player
       below the lower edge of a box a television cannot scroll, so one
       guest never saw where they finished. It now asks for every row that
       is in the list, and fitFinaleBoard() is what decides how many that
       is. */
    function leaderboardNeedPx() {
        var card = document.querySelector('.finale-leaderboard-card');
        var list = card && card.querySelector('.dashboard-leaderboard');
        if (!card || !list) return 0;
        var header = card.querySelector('.card-header');
        var cs = getComputedStyle(list);
        // #865: the header's MARGINS, not only its box. A stray
        // `margin-bottom: 16px` inherited from the phone's stylesheet is
        // exactly what made this function under-report by one player's
        // worth of height, and a measurement that can be wrong by a rule
        // written in another file is not a measurement.
        var headerPx = 0;
        if (header) {
            var hs = getComputedStyle(header);
            headerPx = header.offsetHeight
                + (parseFloat(hs.marginTop) || 0)
                + (parseFloat(hs.marginBottom) || 0);
        }
        var rows = [].slice.call(list.children);
        var rowsPx = 0;
        for (var i = 0; i < rows.length; i++) rowsPx += rows[i].offsetHeight;
        if (rows.length > 1) {
            rowsPx += (parseFloat(cs.rowGap) || 0) * (rows.length - 1);
        }
        return headerPx
            + rowsPx
            + (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0)
            + (card.offsetHeight - card.clientHeight);  // the card's borders
    }

    /* #865: the row densities the final board may use, in the order
       it tries them. The empty first entry is the board as it has always
       looked; a four-player game never leaves it once the leaderboard is
       given the height it asks for. */
    var FINALE_DENSITY = ['', 'is-compact', 'is-dense', 'is-tight'];

    function setFinaleDensity(step) {
        var list = els.finaleLeaderboard;
        if (!list) return;
        FINALE_DENSITY.forEach(function (cls) {
            if (cls) list.classList.remove(cls);
        });
        if (FINALE_DENSITY[step]) list.classList.add(FINALE_DENSITY[step]);
    }

    /* #865: is every row the board rendered actually in the picture, and
       does the column still end above the bottom edge of the screen? Asked
       of the boxes themselves rather than derived, for the reason the whole
       of #775 was rewritten: a budget computed by hand holds only for the
       screen it was computed on. */
    function finaleBoardFits() {
        var list = els.finaleLeaderboard;
        var column = document.querySelector('.dashboard-finale-right');
        if (!list || !column) return true;
        return list.scrollHeight <= list.clientHeight + 1
            && column.scrollHeight <= column.clientHeight + 1;
    }

    /* #865: put every entrant on the final board, or say how many are
       missing — never cut one through the middle and never leave one below
       an edge a television cannot scroll past.

       The order of what gives way is the order of what costs least:

       1. The awards give way first. That was already decided in #692 and
          #775 — every award is also on every player's phone, and the board
          shows whole cards or none. Asking leaderboardNeedPx() for every
          row rather than three is what makes fitAwards() spend the column
          on the ranking instead of on a second award card.
       2. Then the rows compact, in two steps (see the CSS above). The first
          takes the padding and the rank badge and leaves the type alone;
          only the second touches the type.
       3. Only if the tightest rows still do not fit does the board show
          fewer of them, with the "+N more" tail the in-game panel has used
          since #429. Twelve guests on a 720p television is arithmetic no
          layout can beat; being told that three names are missing is not
          the same defect as one name silently below the fold.

       Measured at 1280x720, 1366x768 and 1920x1080 with four, six, eight
       and twelve players: everything up to eight lands in step 1 or 2 at
       every resolution, and only 720p with nine or more reaches step 3. */
    var fittingFinaleBoard = false;

    function fitFinaleBoard() {
        // Re-entrancy guard: fitAwards() hides cards and this function sets
        // a min-height, and the column is watched by a ResizeObserver.
        if (fittingFinaleBoard) return;
        var list = els.finaleLeaderboard;
        var card = document.querySelector('.finale-leaderboard-card');
        if (!list || !card || !finalePlayers.length) return;
        fittingFinaleBoard = true;
        try {
            var total = finalePlayers.length;
            var step;

            var attempt = function (keep) {
                renderLeaderboard(list, finalePlayers, keep);
                // The card only ever gets its content height (its list is
                // `flex: 1`, so its own basis is nothing) — telling it what
                // the rows need is what makes the column give the room up.
                card.style.minHeight = leaderboardNeedPx() + 'px';
                fitAwards();
                return finaleBoardFits();
            };

            // Pass 1: the whole field, compacting until it is all in view.
            for (step = 0; step < FINALE_DENSITY.length; step++) {
                setFinaleDensity(step);
                if (attempt(total)) return;
            }

            // Pass 2: the densest rows the board has, and one fewer name at
            // a time until what is left fits — the tail says how many.
            setFinaleDensity(FINALE_DENSITY.length - 1);
            for (var keep = total - 1; keep >= 1; keep--) {
                if (attempt(keep)) return;
            }
        } finally {
            fittingFinaleBoard = false;
        }
    }

    /* #775: the right-hand column of the result screen is exactly as tall
       as the television, and three blocks want that height — the awards,
       the final leaderboard and the duel line. Every previous attempt
       divided it with a budget derived by hand (#692, #694, #695), and each
       one held only for the player count and the screen it was measured on;
       the moment a line moved, the block that lost was cut through the
       middle.

       The awards are the block that can be shortened without losing a fact:
       every award is also on every player's phone (js/player-end.js). So
       the board shows as many WHOLE award cards as the leftover height
       holds and drops the rest. Nothing is ever cut in half again, at any
       resolution, player count or award count — and no number in this file
       has to be re-derived when a line is added.

       Re-run whenever the column's budget moves: the duel line arrives a
       beat after the finale (analytics_recorded), and a television can be
       resized. */
    function fitAwards() {
        var section = document.getElementById('awards-section');
        var column = document.querySelector('.dashboard-finale-right');
        if (!section || !column || section.classList.contains('hidden')) return;
        var grid = section.querySelector('.awards-grid');
        var title = section.querySelector('.awards-title');
        if (!grid) return;

        // Everything back in flow first — the budget may have grown since
        // the last run (the window got taller, the duel line never came).
        var cards = [].slice.call(grid.querySelectorAll('.award-card'));
        cards.forEach(function(card) { card.hidden = false; });

        var colGap = parseFloat(getComputedStyle(column).rowGap) || 0;
        var secGap = parseFloat(getComputedStyle(section).rowGap) || 0;
        var h2hVisible = els.endH2h && !els.endH2h.classList.contains('hidden');
        var budget = column.clientHeight
            - leaderboardNeedPx() - colGap
            - (h2hVisible ? els.endH2h.offsetHeight + colGap : 0);

        // The section grows into slack, so its own height says nothing
        // about what it needs — ask its children.
        function needed() {
            return (title ? title.offsetHeight + secGap : 0) + grid.offsetHeight;
        }

        // Never drop the last one: one award the room can read beats an
        // empty strip where two were earned.
        var i;
        for (i = cards.length - 1; i > 0 && needed() > budget; i--) {
            cards[i].hidden = true;
        }

        // The budget is arithmetic and arithmetic can be wrong — a font
        // that measures differently, a duel line that wraps to two rows.
        // So the last word goes to the column itself: while anything still
        // hangs past its bottom edge, one more award steps back. This is
        // the rule that makes "nothing is ever cut in half" true rather
        // than merely intended.
        for (i = cards.length - 1; i > 0 && column.scrollHeight > column.clientHeight; i--) {
            cards[i].hidden = true;
        }
    }

    function escapeHtml(text) {
        var el = document.createElement('span');
        el.textContent = text || '';
        return el.innerHTML;
    }

    // #215: hook for sw-update.js — is the TV/dashboard idle enough that a
    // service-worker-triggered reload won't interrupt anything? The
    // always-on host screen is idle only on the waiting (lobby) and finale
    // (end) views; the question view covers live questions, the per-round
    // reveal, and lightning, so we treat it as not-idle and never reload
    // mid-round.
    window.quizifyIsIdleForReload = function () {
        return (views.question == null) || !views.question.classList.contains('active');
    };

    // ---- Init ----
    if (window.QuizifyI18n && typeof window.QuizifyI18n.init === 'function') {
        window.QuizifyI18n.init(window.QuizifyI18n.getPreferredLanguage()).then(function () {
            window.QuizifyI18n.initPageTranslations(document);
        });
    }
    // #775: a television that changes resolution, a browser window resized
    // while the result is up, or a font that finishes loading a beat late
    // all change the column the awards were fitted to. Watch the column
    // itself rather than the window: it is the box the budget is measured
    // against, and it changes in cases no resize event accompanies.
    // Hiding a card changes the awards' height, not the column's, so this
    // does not feed itself.
    // #865: the leaderboard is fitted against the same column, so the
    // whole board is re-run, not only the awards.
    var fitAwardsTimer = null;
    function scheduleFitAwards() {
        clearTimeout(fitAwardsTimer);
        fitAwardsTimer = setTimeout(fitFinaleBoard, 100);
    }
    var finaleColumn = document.querySelector('.dashboard-finale-right');
    if (finaleColumn && typeof ResizeObserver === 'function') {
        new ResizeObserver(scheduleFitAwards).observe(finaleColumn);
    } else {
        window.addEventListener('resize', scheduleFitAwards);
    }

    connect();
})();
