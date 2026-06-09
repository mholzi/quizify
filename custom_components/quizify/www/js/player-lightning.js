/**
 * Quizify Player - Lightning Round Module (issue #42)
 *
 * Renders the fast lightning question view + the end recap grid. The mode
 * has NO reveal between questions, fixed points per correct answer, and
 * power-ups disabled — so this module is intentionally light: show the
 * question, let the player tap once, lock the buttons, wait for the next
 * one to be pushed by the server.
 *
 * Core (player-core.js) injects its `send` function via setSend() so we
 * don't need our own WebSocket handle.
 */
(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var state = pu.state;

    var _send = function () {};
    function setSend(fn) { _send = fn; }

    function t(key, vars) {
        var fn = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
        return fn(key, vars);
    }

    var _answeredIndex = -1; // server question index we've answered

    // ------------------------------------------------------------------
    // Question render
    // ------------------------------------------------------------------

    function handleLightningQuestion(msg) {
        pu.showView('lightning-view');

        var prog = document.getElementById('lightning-progress');
        if (prog) prog.textContent = ((msg.index || 0) + 1) + ' / ' + (msg.num_questions || 5);

        var cat = document.getElementById('lightning-category');
        if (cat) cat.textContent = msg.category || '';

        var img = document.getElementById('lightning-image');
        if (img) {
            if (msg.image_url) { img.src = msg.image_url; img.hidden = false; }
            else { img.hidden = true; img.removeAttribute('src'); }
        }

        var qtext = document.getElementById('lightning-question-text');
        if (qtext) qtext.textContent = msg.question_text || '';

        var answers = msg.answers || [];
        for (var i = 0; i < 3; i++) {
            var span = document.getElementById('lightning-answer-text-' + i);
            var btn = document.querySelector('[data-lightning-answer="' + i + '"]');
            if (span) span.textContent = answers[i] || '';
            if (btn) {
                btn.disabled = false;
                btn.classList.remove('answer-btn--correct', 'answer-btn--wrong', 'selected');
                btn.style.visibility = (i < answers.length) ? '' : 'hidden';
            }
        }

        var answered = document.getElementById('lightning-answered');
        if (answered) answered.classList.add('hidden');

        var timer = document.getElementById('lightning-timer');
        if (timer) timer.textContent = Math.ceil(msg.seconds || 6);

        // Admin gets an "end now" control.
        var adminBar = document.getElementById('lightning-admin-bar');
        if (adminBar) adminBar.classList.toggle('hidden', !state.isAdmin);

        _answeredIndex = -1;
    }

    function handleLightningTick(msg) {
        var timer = document.getElementById('lightning-timer');
        if (timer && typeof msg.remaining === 'number') {
            timer.textContent = Math.ceil(msg.remaining);
        }
    }

    function submitAnswer(shuffledIndex) {
        // One answer per question; ignore re-taps.
        if (_answeredIndex !== -1) return;
        _answeredIndex = shuffledIndex;
        _send('lightning_answer', { answer_index: shuffledIndex });

        // Lock buttons + mark the chosen one. No correctness shown until
        // the recap (no reveal between questions) — except the lightweight
        // ack flips a subtle state via handleLightningAnswerResult.
        var btns = document.querySelectorAll('[data-lightning-answer]');
        for (var i = 0; i < btns.length; i++) {
            btns[i].disabled = true;
            if (parseInt(btns[i].getAttribute('data-lightning-answer'), 10) === shuffledIndex) {
                btns[i].classList.add('selected');
            }
        }
        var answered = document.getElementById('lightning-answered');
        if (answered) answered.classList.remove('hidden');
    }

    function handleLightningAnswerResult(msg) {
        // Optional subtle feedback on the player's own button (still no
        // "the right answer was X" — that's reserved for the recap).
        var scoreEl = document.getElementById('lightning-score');
        if (scoreEl && typeof msg.score === 'number') scoreEl.textContent = msg.score;
    }

    // ------------------------------------------------------------------
    // Recap render
    // ------------------------------------------------------------------

    function handleLightningRecap(msg) {
        pu.showView('lightning-recap-view');
        var recap = msg.recap || (msg.lightning_recap) || {};
        renderRecap(recap);

        var adminCtl = document.getElementById('lightning-recap-admin');
        var playerMsg = document.getElementById('lightning-recap-player-msg');
        if (adminCtl) adminCtl.classList.toggle('hidden', !state.isAdmin);
        if (playerMsg) playerMsg.classList.toggle('hidden', !!state.isAdmin);
    }

    function renderRecap(recap) {
        var lb = recap.leaderboard || [];
        var lbEl = document.getElementById('lightning-recap-leaderboard');
        if (lbEl) {
            lbEl.innerHTML = lb.map(function (p) {
                var you = (p.name === state.playerName)
                    ? ' <span class="you-badge">(you)</span>' : '';
                return '<div class="final-leaderboard-row">' +
                    '<span class="rank">#' + p.rank + '</span>' +
                    '<span class="name">' + pu.escapeHtml(p.name) + you + '</span>' +
                    '<span class="score">' + p.score + '</span>' +
                    '</div>';
            }).join('');
        }

        // Per-question recap rows (Variant C refined): one row per question,
        // focused on THIS player. Always shows the correct answer (green);
        // when the player answered wrong, also shows their own wrong pick.
        var grid = document.getElementById('lightning-recap-grid');
        if (grid) {
            var me = state.playerName;
            var questions = recap.questions || [];
            grid.innerHTML = questions.map(function (q, qi) {
                var result = (q.results || {})[me] || 'miss';
                var ok = result === 'correct';
                var rowCls = ok ? 'lr-recap-row ok' : 'lr-recap-row no';
                var badge = ok ? '✓' : '✗'; // ✓ / ✗

                var html = '<div class="' + rowCls + '">' +
                    '<span class="lr-recap-badge">' + badge + '</span>' +
                    '<div class="lr-recap-body">' +
                        '<div class="lr-recap-q">' +
                            '<span class="lr-recap-qnum">' + (qi + 1) + '.</span> ' +
                            pu.escapeHtml(q.question_text) +
                        '</div>' +
                        '<div class="lr-recap-correct">' +
                            '<span class="lr-recap-tag" data-i18n="lightning.correctTag">' +
                            pu.escapeHtml(t('lightning.correctTag')) + '</span>' +
                            pu.escapeHtml(q.correct_answer) +
                        '</div>';

                // Player answered wrong → surface their pick.
                if (!ok && result === 'wrong') {
                    var chosen = (q.chosen || {})[me];
                    if (chosen) {
                        html += '<div class="lr-recap-yours">' +
                            '<span class="lr-recap-tag" data-i18n="lightning.youSaidTag">' +
                            pu.escapeHtml(t('lightning.youSaidTag')) + '</span>' +
                            pu.escapeHtml(chosen) +
                            '</div>';
                    }
                }

                html += '</div></div>';
                return html;
            }).join('');
        }
    }

    // ------------------------------------------------------------------
    // Wiring
    // ------------------------------------------------------------------

    function init() {
        var btns = document.querySelectorAll('[data-lightning-answer]');
        for (var i = 0; i < btns.length; i++) {
            (function (idx) {
                btns[idx].addEventListener('click', function () {
                    submitAnswer(parseInt(this.getAttribute('data-lightning-answer'), 10));
                });
            })(i);
        }
        var endBtn = document.getElementById('lightning-end-btn');
        if (endBtn) endBtn.addEventListener('click', function () { _send('end_lightning', {}); });

        var againBtn = document.getElementById('lightning-recap-again-btn');
        if (againBtn) againBtn.addEventListener('click', function () { _send('start_lightning', {}); });
        var newGameBtn = document.getElementById('lightning-recap-newgame-btn');
        if (newGameBtn) newGameBtn.addEventListener('click', function () { _send('reset_game', {}); });
    }

    window.QuizifyPlayerLightning = {
        setSend: setSend,
        init: init,
        handleLightningQuestion: handleLightningQuestion,
        handleLightningTick: handleLightningTick,
        handleLightningAnswerResult: handleLightningAnswerResult,
        handleLightningRecap: handleLightningRecap,
        renderRecap: renderRecap
    };
})();
