/**
 * Quizify Player - Game Module
 * Playing phase: question display, answer submission, timer, power-ups, leaderboard
 */

(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var utils = window.QuizifyUtils || {};
    var state = pu.state;

    // ============================================
    // Countdown Timer
    // ============================================

    var countdownInterval = null;

    // Screen-reader countdown announcements (#380). The visible #timer is
    // aria-live="off" so VoiceOver no longer reads every tick; instead we push
    // a single terse announcement into the separate #timer-sr-announce polite
    // region at meaningful moments (10s / 5s left). `lastAnnouncedSecond`
    // dedupes repeated ticks at the same whole second (server sends decimals →
    // ceil can repeat a value) and resets whenever we're back above 10s, i.e.
    // at the start of each new question, without any extra lifecycle wiring.
    var lastAnnouncedSecond = null;

    function announceTimeLeft(seconds) {
        if (seconds > 10) {
            lastAnnouncedSecond = null;
            return;
        }
        if (seconds !== 10 && seconds !== 5) return;
        if (seconds === lastAnnouncedSecond) return;
        lastAnnouncedSecond = seconds;
        var region = document.getElementById('timer-sr-announce');
        if (!region) return;
        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
        region.textContent = t(seconds === 10 ? 'game.timerTenLeft' : 'game.timerFiveLeft');
    }

    /**
     * Start countdown timer
     * @param {number} deadline - Server deadline timestamp in milliseconds
     */
    function startCountdown(deadline) {
        stopCountdown();
        if (window.QuizifyPlayerSound) window.QuizifyPlayerSound.resetTick();

        var timerElement = document.getElementById('timer');
        if (!timerElement) return;

        timerElement.classList.remove('timer--warning', 'timer--critical');

        function updateCountdown() {
            var now = Date.now();
            var remaining = Math.max(0, Math.ceil((deadline - now) / 1000));

            timerElement.textContent = remaining;
            announceTimeLeft(remaining);

            // Soft tick in the last 5 seconds (one per whole second).
            if (window.QuizifyPlayerSound) window.QuizifyPlayerSound.tickFromRemaining(remaining);

            if (remaining <= 5) {
                timerElement.classList.remove('timer--warning');
                timerElement.classList.add('timer--critical');
            } else if (remaining <= 10) {
                timerElement.classList.remove('timer--critical');
                timerElement.classList.add('timer--warning');
            } else {
                timerElement.classList.remove('timer--warning', 'timer--critical');
            }

            var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            timerElement.setAttribute('aria-label', t('game.timerRemainingAria', { seconds: remaining }));

            if (remaining <= 0) {
                timerElement.setAttribute('aria-label', t('game.timerUpAria'));
                stopCountdown();
            }
        }

        updateCountdown();
        countdownInterval = setInterval(updateCountdown, 1000);
    }

    /**
     * Stop countdown timer
     */
    function stopCountdown() {
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
    }

    // ============================================
    // Freeze lockout "Ice Card" overlay (#322, Variant A)
    // ============================================

    // Ring geometry: r=54 -> circumference 2*pi*54 ~= 339.29 (matches the
    // stroke-dasharray in 07-player.css). The progress stroke depletes by
    // raising stroke-dashoffset toward the full circumference as time runs.
    var FROZEN_RING_CIRC = 2 * Math.PI * 54;
    var frozenInterval = null;

    /**
     * Show the frozen overlay and run a per-second countdown ring for
     * `seconds` seconds. While shown, answer taps are blocked both visually
     * (full-scrim overlay) and interactively (handleAnswerClick early-returns
     * when the overlay is active; server also rejects via ERR_FROZEN). The
     * overlay auto-removes when the countdown hits 0, or when stopFrozenOverlay
     * is called (question advances / reveal / reset). Idempotent: a fresh call
     * tears down any in-flight timer first so we never leak intervals or stack
     * two countdowns.
     *
     * @param {number} seconds - lockout duration from effect.freeze_duration
     */
    function startFrozenOverlay(seconds) {
        var overlay = document.getElementById('frozen-overlay');
        if (!overlay) return;

        // Defensive: a non-positive/garbage duration shouldn't strand the
        // player behind a permanent overlay — just don't show it.
        var total = Math.max(0, Math.floor(Number(seconds) || 0));
        if (total <= 0) { stopFrozenOverlay(); return; }

        // Clear any previous run (e.g. a second freeze before the first
        // elapsed) so timers don't stack.
        if (frozenInterval) {
            clearInterval(frozenInterval);
            frozenInterval = null;
        }

        var numEl = document.getElementById('frozen-ring-num');
        var progEl = document.getElementById('frozen-ring-prog');

        overlay.classList.remove('hidden');

        var remaining = total;

        function paint() {
            if (numEl) numEl.textContent = remaining;
            if (progEl) {
                // fraction elapsed -> how much of the ring is "used up"
                var elapsedFrac = (total - remaining) / total;
                progEl.style.strokeDashoffset = (FROZEN_RING_CIRC * elapsedFrac).toFixed(2);
            }
        }

        // Initial frame: full ring, full number.
        paint();

        frozenInterval = setInterval(function () {
            remaining -= 1;
            if (remaining <= 0) {
                remaining = 0;
                paint();
                stopFrozenOverlay();
                return;
            }
            paint();
        }, 1000);
    }

    /**
     * Hide the frozen overlay and clear its countdown timer. Safe to call
     * unconditionally (no-op if not shown) — used both by the countdown
     * itself reaching 0 and by lifecycle teardown (new question / reveal /
     * reset) to guarantee no stuck overlay if the question changes before
     * the timer elapses.
     */
    function stopFrozenOverlay() {
        if (frozenInterval) {
            clearInterval(frozenInterval);
            frozenInterval = null;
        }
        var overlay = document.getElementById('frozen-overlay');
        if (overlay) overlay.classList.add('hidden');
    }

    /**
     * @returns {boolean} true while the frozen overlay is on screen, so the
     * answer-tap handler can hard-block submits even if a tap somehow lands.
     */
    function isFrozen() {
        var overlay = document.getElementById('frozen-overlay');
        return !!(overlay && !overlay.classList.contains('hidden'));
    }

    /**
     * Update timer from server tick
     * @param {number} remaining - Remaining seconds
     */
    function updateTimer(remaining) {
        var timerElement = document.getElementById('timer');
        if (!timerElement) return;

        // Server sends decimals (e.g. 19.5) for smoother bar animations;
        // display as a whole-second integer so the count text reads cleanly.
        var displaySeconds = Math.ceil(remaining);
        timerElement.textContent = displaySeconds;
        announceTimeLeft(displaySeconds);

        // Soft tick in the last 5 seconds (one per whole second).
        if (window.QuizifyPlayerSound) window.QuizifyPlayerSound.tickFromRemaining(remaining);

        // #434: same clock, same sharpening as the board.
        setRevealBlur(remaining);

        if (remaining <= 5) {
            timerElement.classList.remove('timer--warning');
            timerElement.classList.add('timer--critical');
        } else if (remaining <= 10) {
            timerElement.classList.remove('timer--critical');
            timerElement.classList.add('timer--warning');
        } else {
            timerElement.classList.remove('timer--warning', 'timer--critical');
        }

        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
        timerElement.setAttribute('aria-label', t('game.timerRemainingAria', { seconds: displaySeconds }));
    }

    // ============================================
    // Question Rendering
    // ============================================

    /**
     * Render question text and answer buttons
     * @param {Object} data - Question data with text, answers, category
     */
    // Wager gate (gameplay idea #3). On the final round we block the
    // answer buttons until the player submits a wager. Without this
    // gate the player could just tap their answer instantly and never
    // commit to a bet — which would defeat the Jeopardy-final tension.
    var _wagerGate = { active: false, submitted: false };

    // #434: progressive reveal on the player's banner. The dashboard blurs
    // the picture as the timer drains; the phone has to do the same, or a
    // player simply reads the sharp copy in their hand and the mechanic on the
    // TV is decoration.
    var REVEAL_MAX_BLUR_PX = 14;   // smaller canvas than the TV, same feel
    var _revealActive = false;
    var _revealDuration = 0;

    function _revealTargets() {
        // The zoom overlay renders a SECOND <img> from the same src, so it has
        // to carry the blur too — otherwise one tap on the magnifier defeats
        // the whole round.
        return [
            document.getElementById('question-image'),
            document.getElementById('image-zoom-img'),
        ];
    }

    function setRevealBlur(remaining) {
        if (!_revealActive) return;
        var frac = (_revealDuration > 0)
            ? Math.max(0, Math.min(1, remaining / _revealDuration)) : 0;
        var px = (REVEAL_MAX_BLUR_PX * frac).toFixed(2) + 'px';
        _revealTargets().forEach(function (el) {
            if (el) el.style.setProperty('--reveal-blur', px);
        });
    }

    function clearRevealBlur() {
        _revealActive = false;
        _revealTargets().forEach(function (el) {
            if (!el) return;
            el.classList.remove('progressive-reveal');
            el.style.removeProperty('--reveal-blur');
        });
    }

    // Issue #183: render the question image banner + wire tap-to-zoom.
    function renderQuestionImageBanner(imgUrl, revealStyle, duration) {
        var media = document.getElementById('question-media');
        var img = document.getElementById('question-image');
        if (!media || !img) return;
        // Allowed forms live in QuizifyUtils.safeImageUrl, mirroring the
        // server's sanitizer — absolute http(s) or the integration's own
        // static mount (#536/#540).
        var safeImg = (window.QuizifyUtils && window.QuizifyUtils.safeImageUrl)
            ? window.QuizifyUtils.safeImageUrl(imgUrl) : '';
        img.onerror = function() {
            // Image failed → hide the banner, fall back to text-only.
            img.removeAttribute('src');
            media.hidden = true;
        };
        // Reset first — the banner element is reused every round, so a blur
        // left over from the last question must not bleed into this one.
        clearRevealBlur();
        if (safeImg) {
            // #467: populate a localized generic alt so the image question
            // isn't announced as an unlabelled graphic.
            var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            img.src = safeImg;
            img.alt = t('game.questionImageAlt');
            media.hidden = false;
            if (revealStyle === 'progressive') {
                _revealActive = true;
                _revealDuration = duration || 0;
                _revealTargets().forEach(function (el) {
                    if (el) {
                        el.classList.add('progressive-reveal');
                        el.style.setProperty('--reveal-blur', REVEAL_MAX_BLUR_PX + 'px');
                    }
                });
            }
        } else {
            img.removeAttribute('src');
            img.alt = '';
            media.hidden = true;
        }
    }

    // Wire the fullscreen zoom overlay once. The overlay shows the current
    // banner image at full size; closing returns to the game view.
    function _initImageZoom() {
        var zoomBtn = document.getElementById('question-image-zoom');
        var overlay = document.getElementById('image-zoom-overlay');
        var closeBtn = document.getElementById('image-zoom-close');
        var overlayImg = document.getElementById('image-zoom-img');
        var bannerImg = document.getElementById('question-image');
        if (!zoomBtn || !overlay || !overlayImg) return;

        function open() {
            if (bannerImg && bannerImg.getAttribute('src')) {
                overlayImg.src = bannerImg.getAttribute('src');
                overlayImg.alt = bannerImg.getAttribute('alt') || '';
                overlay.classList.remove('hidden');
            }
        }
        function close() {
            overlay.classList.add('hidden');
            overlayImg.removeAttribute('src');
        }
        zoomBtn.addEventListener('click', open);
        if (closeBtn) closeBtn.addEventListener('click', close);
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) close();
        });
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && !overlay.classList.contains('hidden')) close();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _initImageZoom);
    } else {
        _initImageZoom();
    }

    // Tracks whether the current round is an estimate question (#275) so the
    // shared reset/submit paths know which input is live.
    var _isEstimateRound = false;

    function renderQuestion(data) {
        var questionText = document.getElementById('question-text');
        var questionCategory = document.getElementById('question-category');
        var answerButtons = document.getElementById('answer-buttons');

        if (questionText) questionText.textContent = data.question_text || '';
        if (questionCategory) questionCategory.textContent = data.category || '';

        // Issue #25 + #183 (Variant 1): question image as a fixed-height
        // banner preview with tap-to-zoom. Only absolute http(s) URLs are
        // shown (server already sanitises; this is defence-in-depth).
        // Absent/invalid/load-error → the banner is hidden so the answer
        // pills stay reachable above the fold (graceful text-only fallback).
        renderQuestionImageBanner(data.image_url, data.reveal_style, data.timer_duration);

        // #275: branch on the question type. Estimate questions swap the 3-
        // answer grid for a slider (Variant B). Toggle the two sections and
        // render the right one. The estimate path has no wager UI.
        _isEstimateRound = (data.question_type === 'estimate');
        var estimateContainer = document.getElementById('estimate-container');
        var answersContainer = document.getElementById('answers-container');
        if (_isEstimateRound) {
            if (estimateContainer) estimateContainer.classList.remove('hidden');
            if (answersContainer) answersContainer.classList.add('hidden');
            _renderWagerPanel(false, 0);
            renderEstimateInput(data);
            return;
        }
        if (estimateContainer) estimateContainer.classList.add('hidden');
        if (answersContainer) answersContainer.classList.remove('hidden');

        // Wager round detection — set BEFORE we touch buttons so we can
        // disable them up-front, then enable once the wager is in.
        _wagerGate.active = !!data.is_final_round;
        _wagerGate.submitted = false;
        _renderWagerPanel(data.is_final_round, data.player_score || 0);

        if (answerButtons) {
            var answers = data.answers || [];
            var buttons = answerButtons.querySelectorAll('.answer-btn');

            for (var i = 0; i < buttons.length; i++) {
                var btn = buttons[i];
                btn.dataset.index = String(i);

                var textEl = btn.querySelector('.answer-text');
                if (textEl) {
                    // #283/#312: answers may arrive as objects ({text, ...})
                    // or plain strings — normalize before rendering.
                    var a = answers[i];
                    textEl.textContent = ((a && typeof a === 'object') ? a.text : a) || '';
                }

                // Re-apply selected state if player already submitted
                if (hasSubmitted && lastSubmittedIndex === i) {
                    btn.disabled = true;
                    btn.classList.add('is-selected');
                    btn.classList.remove('is-correct', 'is-wrong', 'is-eliminated', 'hidden');
                } else if (hasSubmitted) {
                    btn.disabled = true;
                    btn.classList.remove('is-selected', 'is-correct', 'is-wrong', 'is-eliminated', 'hidden');
                } else {
                    // Wager round: keep buttons disabled until wager submitted.
                    btn.disabled = _wagerGate.active && !_wagerGate.submitted;
                    btn.classList.remove('is-selected', 'is-correct', 'is-wrong', 'is-eliminated', 'hidden');
                }
            }
        }

        // Show/hide submitted confirmation
        var confirmation = document.getElementById('submitted-confirmation');
        if (confirmation) confirmation.classList.toggle('hidden', !hasSubmitted);
    }

    // ============================================
    // Estimate question (#275, Variant B — slider)
    // ============================================

    // Current estimate range/step/unit, cached so the live value display and
    // submit can read them without re-parsing the message.
    var _estimate = { min: 0, max: 100, step: 1, unit: '' };

    function _formatEstimateValue(val) {
        // Drop a trailing .0 so integer guesses read cleanly; keep the unit.
        var n = (Math.round(val * 1000) / 1000);
        var text = (n === Math.round(n)) ? String(Math.round(n)) : String(n);
        return _estimate.unit ? (text + ' ' + _estimate.unit) : text;
    }

    function renderEstimateInput(data) {
        var est = data.estimate || {};
        var min = Number(est.min);
        var max = Number(est.max);
        var step = Number(est.step) || 1;
        if (!isFinite(min)) min = 0;
        if (!isFinite(max) || max <= min) max = min + 100;
        _estimate = { min: min, max: max, step: step, unit: est.unit || '' };

        var slider = document.getElementById('estimate-slider');
        var valueEl = document.getElementById('estimate-value');
        var minEl = document.getElementById('estimate-scale-min');
        var midEl = document.getElementById('estimate-scale-mid');
        var maxEl = document.getElementById('estimate-scale-max');
        var submitBtn = document.getElementById('estimate-submit-btn');
        var confirmation = document.getElementById('estimate-submitted-confirmation');

        var mid = min + (max - min) / 2;

        if (minEl) minEl.textContent = _formatEstimateValue(min);
        if (midEl) midEl.textContent = _formatEstimateValue(mid);
        if (maxEl) maxEl.textContent = _formatEstimateValue(max);

        if (slider) {
            slider.min = String(min);
            slider.max = String(max);
            slider.step = String(step);
            // Default to the midpoint (snapped to step) so the thumb starts
            // centred rather than at an extreme.
            var startVal = Math.round((mid - min) / step) * step + min;
            slider.value = String(startVal);
            slider.setAttribute('aria-valuemin', String(min));
            slider.setAttribute('aria-valuemax', String(max));

            function syncValue() {
                var v = Number(slider.value);
                if (valueEl) valueEl.textContent = _formatEstimateValue(v);
                slider.setAttribute('aria-valuenow', String(v));
                slider.setAttribute('aria-valuetext', _formatEstimateValue(v));
            }
            slider.oninput = syncValue;
            syncValue();

            // Re-apply locked state on a reconnect mid-round.
            slider.disabled = hasSubmitted;
        }

        if (submitBtn) {
            submitBtn.disabled = hasSubmitted;
            submitBtn.onclick = function () {
                var send = window.QuizifyPlayer && window.QuizifyPlayer.send;
                if (send) handleEstimateSubmit(send);
            };
        }

        if (confirmation) confirmation.classList.toggle('hidden', !hasSubmitted);
    }

    /**
     * Submit the current slider value as a numeric guess (#275). Clamps to the
     * range, disables the slider + button, shows the confirmation, and sends
     * the same ``submit_answer`` message the MC path uses, carrying ``guess``.
     */
    function handleEstimateSubmit(sendFn) {
        if (hasSubmitted) return;
        if (isFrozen()) return;
        var slider = document.getElementById('estimate-slider');
        if (!slider) return;
        var guess = Number(slider.value);
        if (!isFinite(guess)) return;
        // Clamp defensively (the slider already constrains this).
        guess = Math.max(_estimate.min, Math.min(_estimate.max, guess));

        hasSubmitted = true;
        slider.disabled = true;
        var submitBtn = document.getElementById('estimate-submit-btn');
        if (submitBtn) submitBtn.disabled = true;
        var confirmation = document.getElementById('estimate-submitted-confirmation');
        if (confirmation) confirmation.classList.remove('hidden');

        sendFn('submit_answer', { guess: guess });
    }

    function _renderWagerPanel(isFinal, currentScore) {
        var panel = document.getElementById('wager-panel');
        if (!panel) return;

        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };

        if (!isFinal) {
            panel.classList.add('hidden');
            return;
        }
        panel.classList.remove('hidden');

        var titleEl = document.getElementById('wager-panel-title');
        var hintEl = document.getElementById('wager-panel-hint');
        var timeoutNoteEl = document.getElementById('wager-panel-timeout-note');
        var slider = document.getElementById('wager-slider');
        var valueEl = document.getElementById('wager-value');
        var bankEl = document.getElementById('wager-bank');
        var submitBtn = document.getElementById('wager-submit-btn');

        if (titleEl) titleEl.textContent = t('wager.title');
        if (hintEl) hintEl.textContent = t('wager.hint');
        // Make the "timeout keeps your points" rule explicit so it's not a
        // hidden trap/exploit (#301): the wager only resolves if you answer;
        // not answering keeps your current points (no win, no loss).
        if (timeoutNoteEl) timeoutNoteEl.textContent = t('wager.timeoutNote');
        if (bankEl) bankEl.textContent = currentScore;
        if (slider) {
            slider.value = '25';  // sensible default — quarter of bank
            slider.disabled = false;
        }

        function syncValue() {
            var pct = parseInt(slider.value, 10);
            var pts = Math.floor(currentScore * pct / 100);
            if (valueEl) valueEl.textContent = t('wager.valueFmt', { pct: pct, pts: pts });
        }
        if (slider && valueEl) {
            slider.oninput = syncValue;
            syncValue();
        }

        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = t('wager.submit');
            submitBtn.onclick = function () {
                if (_wagerGate.submitted) return;
                _wagerGate.submitted = true;
                submitBtn.disabled = true;
                if (slider) slider.disabled = true;
                // Send via the player-core send() function.
                var send = window.QuizifyPlayer && window.QuizifyPlayer.send;
                if (send) send('submit_wager', { wager: parseInt(slider.value, 10) });
                // Unlock answer buttons.
                var answerButtons = document.getElementById('answer-buttons');
                if (answerButtons) {
                    answerButtons.querySelectorAll('.answer-btn').forEach(function (b) {
                        b.disabled = false;
                    });
                }
                // Collapse the panel into a smaller "wager: 25%" badge so
                // the player remembers what they bet during the question.
                panel.classList.add('wager-panel--collapsed');
                if (titleEl) titleEl.textContent = t('wager.locked', { pct: parseInt(slider.value, 10) });
                if (hintEl) hintEl.textContent = '';
                if (timeoutNoteEl) timeoutNoteEl.textContent = '';
            };
        }
    }

    // ============================================
    // Answer Submission
    // ============================================

    var hasSubmitted = false;
    var lastSubmittedIndex = -1;

    /**
     * Handle answer button click
     * @param {number} selectedIndex - Index of selected answer (0, 1, 2)
     * @param {Function} sendFn - Function to send WS messages
     */
    function handleAnswerClick(selectedIndex, sendFn) {
        if (hasSubmitted) return;
        // #322: while the freeze-lockout overlay is up, hard-block submits.
        // The full-scrim overlay already swallows taps, and the server
        // rejects with ERR_FROZEN regardless — this is belt-and-suspenders
        // so a stray programmatic call can't sneak an answer through.
        if (isFrozen()) return;
        hasSubmitted = true;
        lastSubmittedIndex = selectedIndex;

        var answerButtons = document.getElementById('answer-buttons');
        if (answerButtons) {
            var buttons = answerButtons.querySelectorAll('.answer-btn');
            for (var i = 0; i < buttons.length; i++) {
                buttons[i].disabled = true;
                if (parseInt(buttons[i].dataset.index, 10) === selectedIndex) {
                    buttons[i].classList.add('is-selected');
                }
            }
        }

        var confirmation = document.getElementById('submitted-confirmation');
        if (confirmation) confirmation.classList.remove('hidden');

        sendFn('submit_answer', { answer_index: selectedIndex });
    }

    /**
     * Reset submission state for new round
     */
    function getLastSubmittedIndex() {
        return lastSubmittedIndex;
    }

    /**
     * Lock the UI into the "already submitted" state.
     * Used when reconnecting mid-round (#14 in logical review): server says
     * we've already submitted, so disable all answer buttons and show the
     * confirmation, even though we don't know which answer index we picked.
     */
    function lockSubmitted() {
        hasSubmitted = true;
        var answerButtons = document.getElementById('answer-buttons');
        if (answerButtons) {
            var buttons = answerButtons.querySelectorAll('.answer-btn');
            for (var i = 0; i < buttons.length; i++) {
                buttons[i].disabled = true;
            }
        }
        var confirmation = document.getElementById('submitted-confirmation');
        if (confirmation) confirmation.classList.remove('hidden');

        // #275: lock the estimate slider too on a reconnect.
        var slider = document.getElementById('estimate-slider');
        if (slider) slider.disabled = true;
        var estSubmitBtn = document.getElementById('estimate-submit-btn');
        if (estSubmitBtn) estSubmitBtn.disabled = true;
        var estConfirm = document.getElementById('estimate-submitted-confirmation');
        if (estConfirm) estConfirm.classList.remove('hidden');
    }

    function resetSubmissionState() {
        hasSubmitted = false;
        lastSubmittedIndex = -1;

        var answerButtons = document.getElementById('answer-buttons');
        if (answerButtons) {
            var buttons = answerButtons.querySelectorAll('.answer-btn');
            for (var i = 0; i < buttons.length; i++) {
                buttons[i].disabled = false;
                buttons[i].classList.remove('is-selected', 'is-correct', 'is-wrong', 'is-eliminated', 'correct', 'wrong', 'dimmed');
            }
        }

        var confirmation = document.getElementById('submitted-confirmation');
        if (confirmation) confirmation.classList.add('hidden');

        // #275: reset the estimate input for the new round.
        var slider = document.getElementById('estimate-slider');
        if (slider) slider.disabled = false;
        var estSubmitBtn = document.getElementById('estimate-submit-btn');
        if (estSubmitBtn) estSubmitBtn.disabled = false;
        var estConfirm = document.getElementById('estimate-submitted-confirmation');
        if (estConfirm) estConfirm.classList.add('hidden');
    }

    // ============================================
    // Game View Update
    // ============================================

    /**
     * Update game view with round data
     * @param {Object} data - State data from server
     */
    function updateGameView(data) {
        var currentRound = document.getElementById('current-round');
        var totalRounds = document.getElementById('total-rounds');
        var lastRoundBanner = document.getElementById('last-round-banner');

        if (currentRound) currentRound.textContent = data.round || 1;
        if (totalRounds) totalRounds.textContent = data.total_rounds || 10;

        if (lastRoundBanner) {
            if (data.last_round) {
                lastRoundBanner.classList.remove('hidden');
            } else {
                lastRoundBanner.classList.add('hidden');
            }
        }

        renderSubmissionTracker(data.players);

        if (data.leaderboard) {
            updateLeaderboard(data, 'leaderboard-list');
        } else if (data.players && data.players.length > 0) {
            // Fallback for round 1: server doesn't send `leaderboard` until
            // the round-summary message, so during the very first question
            // the section sat empty ("--"). Derive a zero-score board from
            // the player list so users see who they're up against.
            var fallback = data.players.map(function (p, idx) {
                return {
                    name: p.name,
                    score: p.score || 0,
                    rank: idx + 1,
                    color: p.color,
                    is_current: p.name === state.playerName,
                    connected: p.connected !== false,
                    streak: 0,
                };
            });
            updateLeaderboard({ leaderboard: fallback }, 'leaderboard-list');
        }
    }

    // ============================================
    // Submission Tracker
    // ============================================

    /**
     * Get initials from player name
     * @param {string} name - Player name
     * @returns {string} Initials (1-2 characters)
     */
    function getInitials(name) {
        if (!name) return '?';
        var trimmed = name.trim();
        if (!trimmed) return '?';

        var parts = trimmed.split(/[\s-]+/).filter(Boolean);
        if (parts.length >= 2) {
            return (parts[0][0] + parts[1][0]).toUpperCase();
        }
        return trimmed.slice(0, Math.min(2, trimmed.length)).toUpperCase();
    }

    /**
     * Render submission tracker showing who has submitted
     * @param {Array} players - Array of player objects
     */
    // Latest player list from the server, cached so the freeze/steal target
    // picker can render opponents without re-requesting state.
    var _latestPlayers = [];

    function renderSubmissionTracker(players) {
        var tracker = document.getElementById('submission-tracker');
        var container = document.getElementById('submitted-players');

        var playerList = players || [];
        _latestPlayers = playerList;

        if (!tracker || !container) return;
        var submittedCount = playerList.filter(function (p) {
            return p.submitted;
        }).length;
        var totalCount = playerList.length;

        var allSubmitted = submittedCount === totalCount && totalCount > 0;
        tracker.classList.toggle('all-submitted', allSubmitted);

        container.innerHTML = playerList.map(function (player) {
            var initials = getInitials(player.name);
            var isCurrentPlayer = player.name === state.playerName;
            var isDisconnected = player.connected === false;
            var classes = [
                'player-indicator',
                player.submitted ? 'is-submitted' : '',
                isCurrentPlayer ? 'is-current-player' : '',
                isDisconnected ? 'player-indicator--disconnected' : ''
            ].filter(Boolean).join(' ');

            return '<div class="' + classes + '">' +
                '<div class="player-avatar">' +
                    '<span class="player-initials">' + pu.escapeHtml(initials) + '</span>' +
                '</div>' +
                '<span class="player-name">' + pu.escapeHtml(player.name) + '</span>' +
            '</div>';
        }).join('');
    }

    // ============================================
    // Leaderboard
    // ============================================

    /**
     * Update leaderboard display
     * @param {Object} data - State data containing leaderboard
     * @param {string} targetListId - ID of list container
     */
    var _prevLeaderboardRanks = {}; // name -> rank

    // Clear the rank-delta memo (issue #257). Without this a game_reset
    // leaves stale ranks behind, so the first leaderboard of the *next*
    // game shows phantom ▲/▼ deltas against the previous game's standings.
    function resetRankMemo() {
        _prevLeaderboardRanks = {};
    }

    function updateLeaderboard(data, targetListId) {
        var leaderboard = data.leaderboard || [];
        var listEl = document.getElementById(targetListId || 'leaderboard-list');
        if (!listEl) return;

        leaderboard.forEach(function (entry) {
            entry.is_current = (entry.name === state.playerName);
            // Calculate rank delta vs previous
            var prevRank = _prevLeaderboardRanks[entry.name];
            if (prevRank !== undefined && prevRank !== entry.rank) {
                entry.rank_delta = prevRank - entry.rank; // positive = moved up
            } else {
                entry.rank_delta = 0;
            }
        });

        var displayList = compressLeaderboard(leaderboard, state.playerName);

        // FLIP animation: record old positions before DOM update
        var oldPositions = {};
        listEl.querySelectorAll('.leaderboard-entry[data-name]').forEach(function(el) {
            oldPositions[el.dataset.name] = el.getBoundingClientRect().top;
        });

        var html = '';
        displayList.forEach(function (entry) {
            html += renderLeaderboardEntry(entry);
        });

        listEl.innerHTML = html;

        // FLIP: animate from old positions to new
        var prefersReduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (!prefersReduced && Object.keys(oldPositions).length > 0) {
            listEl.querySelectorAll('.leaderboard-entry[data-name]').forEach(function(el) {
                var name = el.dataset.name;
                if (oldPositions[name] !== undefined) {
                    var newTop = el.getBoundingClientRect().top;
                    var delta = oldPositions[name] - newTop;
                    if (Math.abs(delta) > 2) {
                        el.style.transform = 'translateY(' + delta + 'px)';
                        el.style.transition = 'none';
                        requestAnimationFrame(function() {
                            el.style.transition = 'transform 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
                            el.style.transform = '';
                        });
                    }
                }
            });
        }

        // Store current ranks for next update
        leaderboard.forEach(function(entry) {
            _prevLeaderboardRanks[entry.name] = entry.rank;
        });

        if (leaderboard.length > 8) {
            scrollToCurrentPlayer(listEl);
        }

        updateLeaderboardSummary(leaderboard);
    }

    /**
     * Compress leaderboard for display when >10 players
     */
    function compressLeaderboard(players, currentPlayerName) {
        if (players.length <= 10) return players;

        var top5 = players.slice(0, 5);
        var bottom3 = players.slice(-3);
        var currentIdx = -1;

        for (var i = 0; i < players.length; i++) {
            if (players[i].name === currentPlayerName) {
                currentIdx = i;
                break;
            }
        }

        if (currentIdx < 5 || currentIdx >= players.length - 3) {
            return [].concat(top5, [{ separator: true }], bottom3);
        }

        return [].concat(
            top5,
            [{ separator: true }],
            [players[currentIdx]],
            [{ separator: true }],
            bottom3
        );
    }

    /**
     * Render a single leaderboard entry
     */
    function renderLeaderboardEntry(entry) {
        if (entry.separator) {
            return '<div class="leaderboard-separator">...</div>';
        }

        var rank = entry.rank || 0;
        var rankClass = rank <= 3 ? ' rank-' + rank : '';
        var currentClass = entry.is_current ? ' is-current' : '';
        var disconnectedClass = entry.connected === false ? ' is-disconnected' : '';
        var youBadge = entry.is_current ? '<span class="you-badge">(you)</span>' : '';
        var streakBadge = entry.streak > 1
            ? '<span class="leaderboard-streak">' + entry.streak + 'x</span>'
            : '';
        var deltaBadge = '';
        if (entry.rank_delta > 0) {
            deltaBadge = '<span class="rank-delta rank-delta--up">▲' + entry.rank_delta + '</span>';
        } else if (entry.rank_delta < 0) {
            deltaBadge = '<span class="rank-delta rank-delta--down">▼' + Math.abs(entry.rank_delta) + '</span>';
        }

        var colorDot = entry.color
            ? '<span class="player-color-dot" style="background:' + entry.color + '"></span>'
            : '';
        var colorBorder = entry.color ? ' style="border-left:3px solid ' + entry.color + '"' : '';
        return '<div class="leaderboard-entry' + currentClass + disconnectedClass + '"' + colorBorder + ' data-name="' + pu.escapeHtml(entry.name) + '">' +
            '<span class="entry-rank' + rankClass + '">' + rank + '</span>' +
            colorDot +
            '<span class="entry-name">' + pu.escapeHtml(entry.name) + youBadge + '</span>' +
            deltaBadge +
            '<span class="entry-score">' + entry.score + '</span>' +
            streakBadge +
        '</div>';
    }

    /**
     * Scroll leaderboard to show current player
     */
    function scrollToCurrentPlayer(listEl) {
        var currentEntry = listEl.querySelector('.leaderboard-entry.is-current');
        if (currentEntry) {
            currentEntry.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    /**
     * Update leaderboard summary badge.
     *
     * Shows the leader plus a "+N more" hint so a player below 1st place
     * knows the list expands. Without the hint, the collapsed summary
     * just reads "Alice: 19" and players 2-N have no signal that the
     * full board exists. Singular vs plural matters in German ("1
     * weiterer" / "2 weitere").
     */
    function updateLeaderboardSummary(leaderboard) {
        var summaryIds = ['leaderboard-summary', 'reveal-leaderboard-summary'];
        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };

        summaryIds.forEach(function (id) {
            var summaryEl = document.getElementById(id);
            if (!summaryEl || !leaderboard || leaderboard.length === 0) return;

            var leader = leaderboard[0];
            if (!leader) return;

            var summary = leader.name + ': ' + leader.score;
            var others = leaderboard.length - 1;
            if (others === 1) {
                summary += '  ' + t('lobby.andOthersOne');
            } else if (others > 1) {
                summary += '  ' + t('lobby.andOthers', { count: others });
            }
            summaryEl.textContent = summary;
        });
    }

    // ============================================
    // Power-up
    // ============================================

    /**
     * Handle power-up button visibility
     * @param {string|null} powerupType - Power-up type or null
     */
    var POWERUP_LABELS = {
        joker: '🃏 Joker',
        double_points: '✨ Double',
        freeze: '🧊 Freeze',
        time_boost: '⏰ +5s',
        steal: '🥷 Steal',
    };

    function renderPowerUp(powerupType) {
        var powerupBtn = document.getElementById('powerup-btn');
        if (!powerupBtn) return;

        if (powerupType) {
            powerupBtn.classList.remove('hidden', 'used');
            var label = powerupBtn.querySelector('.powerup-label') || powerupBtn;
            label.textContent = POWERUP_LABELS[powerupType] || powerupType;
        } else {
            powerupBtn.classList.add('hidden');
        }
    }

    /**
     * Returns true for power-up types whose effect lands on another player
     * (and therefore need a target picker).
     */
    function powerupNeedsTarget(powerupType) {
        return powerupType === 'freeze' || powerupType === 'steal';
    }

    /**
     * Open the target-picker modal so the player can choose who to freeze
     * or steal from. Calls onConfirm(targetPlayerName) on selection.
     */
    function openTargetPicker(powerupType, onConfirm) {
        var modal = document.getElementById('powerup-target-modal');
        var titleEl = document.getElementById('powerup-target-title');
        var hintEl = document.getElementById('powerup-target-hint');
        var listEl = document.getElementById('powerup-target-list');
        var cancelBtn = document.getElementById('powerup-target-cancel');
        if (!modal || !listEl) return;

        // If a previous picker is still open (double-open / re-entrant call),
        // tear down its listeners first — otherwise the old onBackdrop/onKey
        // handlers leak because _teardown is about to be overwritten below.
        if (typeof modal._teardown === 'function') {
            modal._teardown();
            modal._teardown = null;
        }

        var t = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };

        // Active opponents = connected, not the local player.
        var me = pu.state.playerName;
        var opponents = (_latestPlayers || []).filter(function (p) {
            return p && p.name && p.name !== me && p.connected !== false;
        });

        if (titleEl) {
            titleEl.textContent = powerupType === 'steal'
                ? t('powerups.pickStealTitle')
                : t('powerups.pickFreezeTitle');
        }

        listEl.innerHTML = '';

        if (opponents.length === 0) {
            if (hintEl) hintEl.textContent = t('powerups.noOpponents');
        } else {
            // STEAL is most effective after the target has locked in their
            // answer (target.round_score is non-zero, so half is non-zero).
            // Surface that as a hint + per-opponent "answered" badge so
            // players don't burn the power-up on someone who hasn't yet
            // earned anything to steal. FREEZE keeps the generic pickHint
            // (server v1.1.25 rejects submitted targets for FREEZE anyway).
            if (hintEl) {
                if (powerupType === 'steal') {
                    // #220 P3: bulb icon pulled out of the i18n string, rendered
                    // as an SVG beside the (now text-only) hint.
                    var hintIcon = pu.feedbackIconHtml ? pu.feedbackIconHtml('bulb', 'sun') : '';
                    if (hintIcon) {
                        hintEl.classList.add('powerup-hint--with-icon');
                        hintEl.innerHTML = hintIcon + '<span class="powerup-hint-text">' + pu.escapeHtml(t('powerups.stealHint')) + '</span>';
                    } else {
                        hintEl.textContent = t('powerups.stealHint');
                    }
                } else {
                    hintEl.classList.remove('powerup-hint--with-icon');
                    hintEl.textContent = t('powerups.pickHint');
                }
            }
            opponents.forEach(function (opp) {
                var li = document.createElement('li');
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'powerup-target-option';
                btn.dataset.player = opp.name;
                var colorDot = '';
                if (opp.color) {
                    colorDot = '<span class="powerup-target-dot" style="background:' +
                        pu.escapeHtml(opp.color) + '"></span>';
                }
                var submittedBadge = '';
                if (powerupType === 'steal' && opp.submitted) {
                    submittedBadge = '<span class="powerup-target-submitted">' +
                        pu.escapeHtml(t('powerups.targetAnswered')) + '</span>';
                }
                btn.innerHTML = colorDot +
                    '<span class="powerup-target-name">' + pu.escapeHtml(opp.name) + '</span>' +
                    submittedBadge;
                btn.addEventListener('click', function () {
                    closeTargetPicker();
                    onConfirm(opp.name);
                });
                li.appendChild(btn);
                listEl.appendChild(li);
            });
        }

        modal.classList.remove('hidden');

        // One-shot listeners; closeTargetPicker tears them down.
        function onCancel() { closeTargetPicker(); }
        function onBackdrop(e) {
            if (e.target && e.target.classList.contains('powerup-target-backdrop')) {
                closeTargetPicker();
            }
        }
        function onKey(e) {
            if (e.key === 'Escape') closeTargetPicker();
        }
        if (cancelBtn) cancelBtn.addEventListener('click', onCancel, { once: true });
        modal.addEventListener('click', onBackdrop);
        document.addEventListener('keydown', onKey);
        modal._teardown = function () {
            modal.removeEventListener('click', onBackdrop);
            document.removeEventListener('keydown', onKey);
            if (cancelBtn) cancelBtn.removeEventListener('click', onCancel);
        };
    }

    function closeTargetPicker() {
        var modal = document.getElementById('powerup-target-modal');
        if (!modal) return;
        modal.classList.add('hidden');
        if (typeof modal._teardown === 'function') {
            modal._teardown();
            modal._teardown = null;
        }
    }

    /**
     * Handle joker power-up applied - eliminate one wrong answer
     * @param {number} removeIndex - Index of answer to eliminate
     */
    function applyJoker(removeIndex) {
        var answerButtons = document.getElementById('answer-buttons');
        if (!answerButtons) return;

        var buttons = answerButtons.querySelectorAll('.answer-btn');
        for (var i = 0; i < buttons.length; i++) {
            if (parseInt(buttons[i].dataset.index, 10) === removeIndex) {
                buttons[i].classList.add('is-eliminated');
                buttons[i].disabled = true;
                break;
            }
        }
    }

    // ============================================
    // Answer Result Storage
    // ============================================

    var lastAnswerResult = null;

    /**
     * Store answer result for reveal phase
     * @param {Object} data - Answer result data
     */
    function handleAnswerResult(data) {
        lastAnswerResult = data;
    }

    /**
     * Get stored answer result
     */
    function getLastAnswerResult() {
        return lastAnswerResult;
    }

    /**
     * Clear stored answer result
     */
    function clearLastAnswerResult() {
        lastAnswerResult = null;
    }

    // ============================================
    // Export
    // ============================================

    window.QuizifyPlayerGame = {
        startCountdown: startCountdown,
        stopCountdown: stopCountdown,
        startFrozenOverlay: startFrozenOverlay,
        stopFrozenOverlay: stopFrozenOverlay,
        isFrozen: isFrozen,
        updateTimer: updateTimer,
        renderQuestion: renderQuestion,
        clearRevealBlur: clearRevealBlur,
        handleAnswerClick: handleAnswerClick,
        lockSubmitted: lockSubmitted,
        resetSubmissionState: resetSubmissionState,
        updateGameView: updateGameView,
        renderSubmissionTracker: renderSubmissionTracker,
        updateLeaderboard: updateLeaderboard,
        resetRankMemo: resetRankMemo,
        renderLeaderboardEntry: renderLeaderboardEntry,
        updateLeaderboardSummary: updateLeaderboardSummary,
        renderPowerUp: renderPowerUp,
        powerupNeedsTarget: powerupNeedsTarget,
        openTargetPicker: openTargetPicker,
        closeTargetPicker: closeTargetPicker,
        applyJoker: applyJoker,
        handleAnswerResult: handleAnswerResult,
        getLastAnswerResult: getLastAnswerResult,
        clearLastAnswerResult: clearLastAnswerResult,
        getLastSubmittedIndex: getLastSubmittedIndex
    };

})();
