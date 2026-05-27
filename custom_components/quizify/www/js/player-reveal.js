/**
 * Quizify Player - Reveal Module
 * Reveal phase: correct answer display, personal result, all answers grid, reactions
 */

(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var game = window.QuizifyPlayerGame;
    var state = pu.state;

    // Translation helper — falls back to the key if i18n hasn't loaded yet.
    // window.t is wired in i18n.js. The QuizifyUtils.t reference used to live
    // here didn't exist (QuizifyUtils only has escapeHtml), which is why
    // every "Final Results" / "Next Round" stayed in English on a German
    // game even though the keys existed in de.json.
    function t(key, params) {
        if (typeof window.t === 'function') return window.t(key, params);
        return key;
    }

    // Track the current question id so the 🚩 flag button knows what to send.
    var _currentQuestionId = '';
    var _flaggedThisRound = false;

    // ============================================
    // Reveal View Update
    // ============================================

    /**
     * Update reveal view with round results
     * @param {Object} data - State data from server
     */
    // Memo of the previous round's leaderboard so we can compute rank deltas
    // ("↑1", "↓1", "—") without server help. Keyed by lowercased player name.
    var _prevRanks = null;

    function updateRevealView(data) {
        var players = data.players || [];
        var allAnswers = data.all_answers || [];

        // Remember the question id and reset per-round flag state so the
        // 🚩 button knows what to POST and can be clicked again next round.
        _currentQuestionId = data.question_id || '';
        _flaggedThisRound = false;
        _resetFlagButton();

        // Find current player's entry in all_answers
        var myAnswerEntry = null;
        for (var i = 0; i < allAnswers.length; i++) {
            if (allAnswers[i].player_name === state.playerName) {
                myAnswerEntry = allAnswers[i];
                break;
            }
        }

        // Find current player in the leaderboard (has is_admin)
        var currentPlayer = null;
        for (var j = 0; j < players.length; j++) {
            if (players[j].name === state.playerName) {
                currentPlayer = players[j];
                break;
            }
        }

        // Round indicator
        var roundEl = document.getElementById('reveal-round');
        var totalEl = document.getElementById('reveal-total');
        if (roundEl) roundEl.textContent = data.round || 1;
        if (totalEl) totalEl.textContent = data.total_rounds || 10;

        // Fun fact
        renderFunFact(data.fun_fact);

        // Flash answer buttons correct/wrong. Per-player shuffle means
        // the server's `correct_answer_index` is in the CANONICAL shuffle
        // and doesn't match this player's button positions. We match by
        // text instead.
        flashAnswerButtons(data.correct_answer, myAnswerEntry);

        // New result page: hero + answer strip + standings (with medals)
        renderResultHero(myAnswerEntry || currentPlayer, data);
        renderAnswerStrip(myAnswerEntry, data.correct_answer);
        renderStandings(data.leaderboard || players, state.playerName);

        // Admin controls — sticky bottom bar
        var adminControls = document.getElementById('reveal-admin-controls');
        var nextRoundBtn = document.getElementById('next-round-btn');
        var isAdmin = !!(currentPlayer && currentPlayer.is_admin);
        if (document.body) document.body.classList.toggle('is-admin', isAdmin);
        if (adminControls) {
            if (isAdmin) adminControls.classList.remove('hidden');
            else adminControls.classList.add('hidden');
        }
        if (nextRoundBtn) {
            var nextLabel = nextRoundBtn.querySelector('[data-i18n]');
            if (data.last_round) {
                if (nextLabel) {
                    nextLabel.setAttribute('data-i18n', 'reveal.finalResults');
                    nextLabel.textContent = t('reveal.finalResults');
                }
                nextRoundBtn.classList.add('is-final');
            } else {
                if (nextLabel) {
                    nextLabel.setAttribute('data-i18n', 'admin.nextRound');
                    nextLabel.textContent = t('admin.nextRound');
                }
                nextRoundBtn.classList.remove('is-final');
            }
            nextRoundBtn.disabled = false;
        }
    }

    // ============================================
    // Fun Fact
    // ============================================

    /**
     * Render fun fact slide-in card
     * @param {string|null} text - Fun fact text
     */
    function renderAnswerDistribution(distribution, correctIndex, allAnswers, myAnswerEntry) {
        var container = document.getElementById('answer-distribution');
        if (!container) return;
        if (!distribution || distribution.length === 0) {
            container.classList.add('hidden');
            return;
        }

        // Build answer text map from allAnswers (keyed by ORIGINAL index, not
        // shuffled — per-player shuffle made shuffled index meaningless across
        // viewers, so we standardized on original at the server side).
        var answerTexts = {};
        if (allAnswers && allAnswers.length > 0) {
            allAnswers.forEach(function(a) {
                if (a.answer_index !== null && a.answer_index !== undefined && a.answer_text) {
                    answerTexts[a.answer_index] = a.answer_text;
                }
            });
        }

        // The player's own pick — used to render the "· DU" tag on their row
        // so they can find themselves even though their A/B/C buttons were in
        // a different order than the canonical distribution.
        var myIndex = (myAnswerEntry && typeof myAnswerEntry.answer_index === 'number')
            ? myAnswerEntry.answer_index
            : null;
        var youLabel = (typeof t === 'function' && t('lobby.you') !== 'lobby.you')
            ? t('lobby.you')
            : 'DU';

        var optionLabels = ['A', 'B', 'C', 'D'];

        // Variant F (Filling-Bar): each row IS the bar. Text + percentage sit
        // over a coral wash that grows from the left to the vote percentage.
        // Correct row paints sage with a ✓; the player's row appends "· DU"
        // in coral so finding yourself stays trivial under per-player shuffle.
        container.innerHTML = distribution
            .filter(function(d) { return !d.no_answer; })
            .map(function(d) {
                var isCorrect = d.index === correctIndex;
                var isMine = myIndex !== null && d.index === myIndex;
                var label = optionLabels[d.index] || String(d.index + 1);
                var text = answerTexts[d.index] || '';
                var pct = d.percent || 0;
                var classes = ['ad-row'];
                if (isCorrect) classes.push('ad-row--correct');
                if (isMine) classes.push('ad-row--mine');
                return '<div class="' + classes.join(' ') + '">' +
                    '<div class="ad-fill" style="width:' + pct + '%"></div>' +
                    '<div class="ad-content">' +
                        '<span class="ad-letter">' + label + (isCorrect ? ' ✓' : '') + '</span>' +
                        '<span class="ad-text">' +
                            pu.escapeHtml(text) +
                            (isMine ? '<span class="ad-mine"> · ' + pu.escapeHtml(youLabel) + '</span>' : '') +
                        '</span>' +
                        '<span class="ad-pct">' + pct + '%</span>' +
                    '</div>' +
                '</div>';
            }).join('');
        container.classList.remove('hidden');
    }

    function renderFunFact(text) {
        var container = document.getElementById('fun-fact-container');
        var funFactEl = document.getElementById('fun-fact');

        if (!container) return;

        if (text && text.trim()) {
            if (funFactEl) funFactEl.textContent = text;
            container.classList.remove('hidden');
        } else {
            container.classList.add('hidden');
        }
    }

    // ============================================
    // Result Hero — picks 1 of 4 states (Soft Parlor redesign, 2026-05-24):
    //   X = streak hero (≥2 correct in a row + this round correct)
    //   Y = big +N points (correct, streak < 2)
    //   Z = wrong  (soft brick verdict + 0)
    //   W = missed (muted clock + Zeit abgelaufen + 0)
    // ============================================

    var STREAK_HERO_THRESHOLD = 2;  // streak ≥ 2 promotes to the streak headline

    function renderResultHero(player, data) {
        var hero = document.getElementById('reveal-hero');
        if (!hero) return;

        hero.className = 'pl-result-hero';
        hero.innerHTML = '';

        // Joined late: server returns no per-player entry. Show a friendly note.
        if (!player) {
            hero.classList.add('pl-result-hero--missed');
            hero.innerHTML =
                '<span class="pl-result-verdict-circle pl-result-verdict-circle--missed" aria-hidden="true">👋</span>' +
                '<div class="pl-result-verdict">' + pu.escapeHtml(t('reveal.joinedLate')) + '</div>' +
                '<div class="pl-result-sub">' + pu.escapeHtml(t('reveal.joinedLateHint')) + '</div>';
            return;
        }

        var missed = player.missed_round === true || player.no_answer === true || player.answer_index === null || player.answer_index === undefined;
        var correct = player.correct === true;
        var streak = player.streak || player.new_streak || 0;
        var roundScore = player.round_score || player.points_earned || 0;
        var correctAnswer = (data && data.correct_answer) || '';

        // Score breakdown (used by states X and Y)
        var BASE_POINTS = 10;
        var baseScore = (typeof player.base_score === 'number') ? player.base_score : (correct ? BASE_POINTS : 0);
        var speedBonus = player.speed_bonus || 0;
        var streakBonus = player.streak_bonus || 0;
        var difficultyMult = player.difficulty_multiplier || 1.0;
        var breakdownBits = [];
        if (baseScore) breakdownBits.push(t('reveal.baseScore') + ' <b>' + baseScore + '</b>');
        if (speedBonus) breakdownBits.push(t('reveal.speedBonus') + ' <b>+' + speedBonus + '</b>');
        if (streakBonus) breakdownBits.push(t('reveal.streakBonus', { count: streak }) + ' <b>+' + streakBonus + '</b>');
        if (difficultyMult > 1.0) breakdownBits.push(t('reveal.difficulty') + ' <b>' + difficultyMult.toFixed(1) + 'x</b>');
        var breakdownHtml = breakdownBits.length
            ? '<div class="pl-result-breakdown">' + breakdownBits.join(' &middot; ') + '</div>'
            : '';

        if (missed) {
            // State W — muted clock + Zeit abgelaufen
            hero.classList.add('pl-result-hero--missed');
            hero.innerHTML =
                '<span class="pl-result-verdict-circle pl-result-verdict-circle--missed" aria-hidden="true">⏱</span>' +
                '<div class="pl-result-verdict">' + pu.escapeHtml(t('reveal.timeUp')) + '</div>' +
                '<div class="pl-result-sub">' + pu.escapeHtml(t('reveal.correctAnswerWas')) + ' <b>' + pu.escapeHtml(correctAnswer) + '</b></div>' +
                '<div class="pl-result-zero">0<span class="pl-result-zero-unit">' + pu.escapeHtml(t('game.points')) + '</span></div>';
            return;
        }

        if (!correct) {
            // State Z — wrong. Show "Serie verloren" only if a streak was active.
            var prevStreak = player.previous_streak || 0;
            hero.classList.add('pl-result-hero--wrong');
            hero.innerHTML =
                '<span class="pl-result-verdict-circle pl-result-verdict-circle--wrong" aria-hidden="true">✗</span>' +
                '<div class="pl-result-verdict">' + pu.escapeHtml(t('reveal.wrongHeadline')) + '</div>' +
                '<div class="pl-result-sub">' + pu.escapeHtml(t('reveal.correctAnswerWas')) + ' <b>' + pu.escapeHtml(correctAnswer) + '</b></div>' +
                '<div class="pl-result-zero">0<span class="pl-result-zero-unit">' + pu.escapeHtml(t('game.points')) + '</span></div>' +
                (prevStreak >= 2 ? '<div class="pl-result-streak-lost">💔 ' + pu.escapeHtml(t('reveal.streakLost', { count: prevStreak })) + '</div>' : '');
            return;
        }

        // Correct: pick X (streak) or Y (big)
        if (streak >= STREAK_HERO_THRESHOLD) {
            // State X — flame headline
            hero.classList.add('pl-result-hero--streak');
            hero.innerHTML =
                '<div class="pl-result-flame" aria-hidden="true">🔥</div>' +
                '<div class="pl-result-headline">' + pu.escapeHtml(t('reveal.streakHeadline', { count: streak })) + '</div>' +
                '<div class="pl-result-pts">+' + roundScore + '<span class="pl-result-pts-unit">' + pu.escapeHtml(t('game.points')) + '</span></div>' +
                breakdownHtml;
            return;
        }

        // State Y — big +N. Skip the green ✓ circle on correct answers:
        // the "+N PUNKTE" already reads as the positive verdict, and the
        // extra tick competes for attention with the points number.
        // Wrong answers (state Z) keep their ✗ so the negative outcome
        // is still unmistakable.
        hero.classList.add('pl-result-hero--big');
        hero.innerHTML =
            '<div class="pl-result-big-points">+' + roundScore + '</div>' +
            '<div class="pl-result-pts-unit">' + pu.escapeHtml(t('game.points').toUpperCase()) + '</div>' +
            breakdownHtml;
    }

    // ============================================
    // Answer strip — single line under the hero showing the correct answer
    // and a ✓/✗/— mark relative to what the player picked.
    // ============================================
    function renderAnswerStrip(myAnswer, correctAnswer) {
        var strip = document.getElementById('reveal-answer-strip');
        if (!strip) return;

        strip.className = 'pl-result-answer-strip';

        var ans = correctAnswer || '';
        var hasAnswer = myAnswer && myAnswer.answer_index !== null && myAnswer.answer_index !== undefined;
        var isCorrect = myAnswer && myAnswer.correct === true;

        if (!hasAnswer) {
            strip.classList.add('pl-result-answer-strip--missed');
            strip.innerHTML =
                '<span>' + pu.escapeHtml(t('reveal.noAnswerShort')) + '</span>' +
                '<span class="pl-result-answer-mark">—</span>';
            return;
        }

        if (isCorrect) {
            strip.innerHTML =
                '<span>' + pu.escapeHtml(t('reveal.answerLabel')) + ': <b>' + pu.escapeHtml(ans) + '</b></span>' +
                '<span class="pl-result-answer-mark">✓</span>';
            return;
        }

        // Wrong — show YOUR answer + the correct one in the sub area (already in hero), strip says "Deine Antwort"
        strip.classList.add('pl-result-answer-strip--wrong');
        var myText = myAnswer.answer_text || myAnswer.answer || '';
        strip.innerHTML =
            '<span>' + pu.escapeHtml(t('reveal.yourAnswer')) + ': <b>' + pu.escapeHtml(myText) + '</b></span>' +
            '<span class="pl-result-answer-mark">✗</span>';
    }

    // ============================================
    // Standings — top 3 with medal tints, "me" row with coral left border,
    // rank-delta arrows computed from the previous round's snapshot.
    // ============================================
    var _PLAYER_COLORS = ['#E88A7F','#7FA8C4','#7FA897','#E8C47F','#B78FC7','#D66A6A','#6E9685','#6A9AB6'];
    function _colorFor(name, idx) {
        // Stable color per name: hash first char, fall back to position.
        if (!name) return _PLAYER_COLORS[idx % _PLAYER_COLORS.length];
        var c = name.charCodeAt(0) || 0;
        return _PLAYER_COLORS[(c + idx) % _PLAYER_COLORS.length];
    }

    function renderStandings(leaderboard, myName) {
        var container = document.getElementById('reveal-standings');
        if (!container) return;

        // Normalize: leaderboard may be an array of player objects with
        // {name, score} or {player_name, total_score}. Sort by score desc.
        var rows = (Array.isArray(leaderboard) ? leaderboard : []).map(function(p) {
            var name = p.name || p.player_name || '';
            var score = (typeof p.score === 'number') ? p.score
                      : (typeof p.total_score === 'number') ? p.total_score
                      : (typeof p.points === 'number') ? p.points : 0;
            var color = p.color || null;
            return { name: name, score: score, color: color };
        });
        rows.sort(function(a, b) { return b.score - a.score; });

        // Build new rank map for next round's delta computation.
        var newRanks = {};
        rows.forEach(function(r, i) { newRanks[(r.name || '').toLowerCase()] = i + 1; });

        // Compute delta vs. previous round's snapshot (if any).
        function deltaFor(name) {
            if (!_prevRanks) return null;
            var key = (name || '').toLowerCase();
            var prev = _prevRanks[key];
            var now = newRanks[key];
            if (typeof prev !== 'number' || typeof now !== 'number') return null;
            return prev - now;  // positive = moved up
        }

        var medalClass = ['pl-result-srow--gold','pl-result-srow--silver','pl-result-srow--bronze'];
        var youLabel = t('lobby.you') !== 'lobby.you' ? t('lobby.you') : 'Du';

        container.innerHTML = rows.map(function(r, i) {
            var isMe = r.name === myName;
            var classes = ['pl-result-srow'];
            if (i < 3) classes.push(medalClass[i]);
            if (isMe) classes.push('pl-result-srow--me');
            var initial = (r.name || '?').charAt(0).toUpperCase();
            var bg = r.color || _colorFor(r.name, i);
            var displayName = isMe ? youLabel : (r.name || '');
            var d = deltaFor(r.name);
            var deltaHtml = '<span class="pl-result-delta pl-result-delta--flat">—</span>';
            if (d !== null) {
                if (d > 0) deltaHtml = '<span class="pl-result-delta pl-result-delta--up">↑' + d + '</span>';
                else if (d < 0) deltaHtml = '<span class="pl-result-delta pl-result-delta--down">↓' + Math.abs(d) + '</span>';
            }
            return '<div class="' + classes.join(' ') + '">' +
                '<span class="pl-result-pos">' + (i + 1) + '.</span>' +
                '<span class="pl-result-av" style="background:' + pu.escapeHtml(bg) + '">' + pu.escapeHtml(initial) + '</span>' +
                '<span class="pl-result-nm">' + pu.escapeHtml(displayName) + '</span>' +
                deltaHtml +
                '<span class="pl-result-sc">' + r.score + '</span>' +
            '</div>';
        }).join('');

        // Store snapshot for the NEXT round's delta computation.
        _prevRanks = newRanks;
    }

    // ============================================
    // Emotion Display
    // ============================================

    /**
     * Show celebration/disappointment emotion
     * @param {Object} player - Current player data
     */
    function renderRevealEmotion(player) {
        var emotionEl = document.getElementById('reveal-emotion');
        if (!emotionEl) return;

        emotionEl.innerHTML = '';
        emotionEl.className = 'reveal-emotion-inline';
        emotionEl.classList.add('hidden');

        if (!player) return;

        var correct = player.correct === true;
        var missed = player.missed_round === true || player.no_answer === true || player.answer_index === null;
        var streak = player.streak || player.new_streak || 0;
        var speedBonus = player.speed_bonus || 0;

        // All emotion strings live in i18n (reveal.emotion.*) so they
        // translate with the rest of the game. The arrays come back from
        // window.t verbatim because i18n values can be arrays.
        function pickFromKey(key, params) {
            var arr = t(key, params);
            if (!Array.isArray(arr)) return typeof arr === 'string' ? arr : '';
            return arr[Math.floor(Math.random() * arr.length)];
        }

        var emotionType, emotionText, subtitle;

        if (missed) {
            emotionType = 'missed';
            emotionText = pickFromKey('reveal.emotion.missed');
            subtitle = pickFromKey('reveal.emotion.missedSub');
        } else if (correct) {
            emotionType = 'exact';
            emotionText = pickFromKey('reveal.emotion.exact');
            if (streak >= 3) subtitle = pickFromKey('reveal.emotion.streakSub', { count: streak });
            else if (speedBonus > 200) subtitle = pickFromKey('reveal.emotion.fastSub');
            else subtitle = pickFromKey('reveal.emotion.exactSub');
        } else {
            emotionType = 'wrong';
            emotionText = pickFromKey('reveal.emotion.wrong');
            subtitle = pickFromKey('reveal.emotion.wrongSub');
        }

        var emotionHtml = '<span class="reveal-emotion-text">' + emotionText + '</span>';
        if (subtitle) {
            emotionHtml += '<div class="reveal-emotion-subtitle">' + subtitle + '</div>';
        }
        emotionEl.innerHTML = emotionHtml;
        emotionEl.classList.add('reveal-emotion--' + emotionType);
        emotionEl.classList.remove('hidden');

        // Broadcast Living Room: no confetti on correct-answer reveal.
        // The gold glow sweep on the correct tile (320ms ease-out) IS the celebration. See DESIGN.md.
    }

    // ============================================
    // Personal Result
    // ============================================

    /**
     * Render personal result in reveal view
     * @param {Object} player - Current player data
     */
    function renderPersonalResult(player) {
        var resultContent = document.getElementById('result-content');
        if (!resultContent) return;

        var ptsUnit = t('reveal.ptsUnit');

        if (!player) {
            resultContent.innerHTML =
                '<div class="result-missed-container">' +
                    '<div class="result-missed-icon">👋</div>' +
                    '<div class="result-missed-text">' + pu.escapeHtml(t('reveal.joinedLate')) + '</div>' +
                    '<div class="result-missed-text" style="font-size:0.8rem;opacity:0.6;">' + pu.escapeHtml(t('reveal.joinedLateHint')) + '</div>' +
                '</div>';
            return;
        }

        // Missed round (no answer)
        if (player.missed_round || player.no_answer || player.answer_index === null) {
            var prevStreak = player.streak || 0;
            var streakLostHtml = prevStreak > 1
                ? '<div class="result-row"><span class="result-label">' + pu.escapeHtml(t('reveal.streakLost', { count: prevStreak })) + '</span></div>'
                : '';
            resultContent.innerHTML =
                '<div class="result-missed-container">' +
                    '<div class="result-missed-icon">⏱️</div>' +
                    '<div class="result-missed-text">' + pu.escapeHtml(t('reveal.noAnswer')) + '</div>' +
                '</div>' +
                streakLostHtml +
                '<div class="result-score is-zero">0 ' + pu.escapeHtml(ptsUnit) + '</div>';
            return;
        }

        var isCorrect = player.correct === true;
        // BASE_POINTS in scoring.py is 10 — server may also send base_score
        // explicitly. Never invent a number (previous code hardcoded "1000 pts"
        // which never matched the real total below).
        var BASE_POINTS = 10;
        var baseScore = (typeof player.base_score === 'number') ? player.base_score : (isCorrect ? BASE_POINTS : 0);
        var speedBonus = player.speed_bonus || 0;
        var streakBonus = player.streak_bonus || 0;
        var difficultyMult = player.difficulty_multiplier || 1.0;
        var roundScore = player.round_score || player.points_earned || 0;
        var streak = player.streak || 0;
        var answerText = player.answer_text || player.answer || '?';

        // Score breakdown rows
        var breakdownHtml = '';
        if (isCorrect) {
            breakdownHtml +=
                '<div class="result-row">' +
                    '<span class="result-label">' + pu.escapeHtml(t('reveal.baseScore')) + '</span>' +
                    '<span class="result-value">' + baseScore + ' ' + pu.escapeHtml(ptsUnit) + '</span>' +
                '</div>';

            if (speedBonus > 0) {
                breakdownHtml +=
                    '<div class="result-row">' +
                        '<span class="result-label">' + pu.escapeHtml(t('reveal.speedBonus')) + '</span>' +
                        '<span class="result-value is-bonus">' + pu.escapeHtml(t('reveal.ptsBonus', { count: speedBonus })) + '</span>' +
                    '</div>';
            }

            if (difficultyMult > 1.0) {
                breakdownHtml +=
                    '<div class="result-row">' +
                        '<span class="result-label">' + pu.escapeHtml(t('reveal.difficulty')) + '</span>' +
                        '<span class="result-value is-bonus">' + difficultyMult.toFixed(1) + 'x</span>' +
                    '</div>';
            }
        }

        var streakHtml = '';
        if (streakBonus > 0) {
            streakHtml =
                '<div class="result-row streak-bonus-row">' +
                    '<span class="result-label">' + pu.escapeHtml(t('reveal.streakBonus', { count: streak })) + '</span>' +
                    '<span class="result-value is-streak">' + pu.escapeHtml(t('reveal.ptsBonus', { count: streakBonus })) + '</span>' +
                '</div>';
        } else if (streak > 1 && isCorrect) {
            streakHtml =
                '<div class="result-row streak-bonus-row">' +
                    '<span class="result-label">' + pu.escapeHtml(t('reveal.streakActive', { count: streak })) + '</span>' +
                '</div>';
        }

        resultContent.innerHTML =
            '<div class="result-row">' +
                '<span class="result-label">' + pu.escapeHtml(t('reveal.yourAnswer')) + '</span>' +
                '<span class="result-value">' + pu.escapeHtml(answerText) + '</span>' +
            '</div>' +
            '<div class="result-row">' +
                '<span class="result-label">' + pu.escapeHtml(t('reveal.result')) + '</span>' +
                '<span class="result-value ' + (isCorrect ? 'is-correct' : 'is-wrong') + '">' +
                    pu.escapeHtml(isCorrect ? t('reveal.correct') : t('reveal.wrong')) +
                '</span>' +
            '</div>' +
            breakdownHtml +
            '<div class="result-score ' + (roundScore > 0 ? 'is-score-high' : 'is-zero') + '">+<span id="result-score-value">0</span> ' + pu.escapeHtml(ptsUnit) + '</div>' +
            streakHtml;

        // Animate score counting up
        if (roundScore > 0) {
            var scoreEl = document.getElementById('result-score-value');
            if (scoreEl && pu.animateValue) {
                pu.animateValue(scoreEl, 0, roundScore, 600);
                // Floating popup
                setTimeout(function() {
                    if (pu.showPointsPopup) pu.showPointsPopup(scoreEl, roundScore);
                }, 100);
                // Streak popup after
                if (streakBonus > 0) {
                    setTimeout(function() {
                        if (pu.showPointsPopup) pu.showPointsPopup(scoreEl, streakBonus, { isStreak: true });
                    }, 500);
                }
            }
        }
    }

    // ============================================
    // All Answers Grid
    // ============================================

    /**
     * Render all player answers grid
     * @param {Array} players - All players from state
     */
    function renderAllAnswers(players) {
        var container = document.getElementById('reveal-results-cards');
        if (!container) return;

        if (!players || players.length === 0) {
            container.innerHTML = '';
            return;
        }

        var sorted = players.slice().sort(function (a, b) {
            return (b.round_score || 0) - (a.round_score || 0);
        });

        var html = '<div class="results-cards-scroll">';

        sorted.forEach(function (player) {
            var isCurrentPlayer = player.name === state.playerName;
            // Support both all_answers format and players format
            var isMissed = player.no_answer === true || player.missed_round === true || player.answer_index === null;
            var roundScore = player.points_earned || player.round_score || 0;
            var isCorrect = player.correct === true;

            var scoreClass = isMissed ? 'is-score-zero' :
                             isCorrect ? 'is-score-high' : 'is-score-zero';

            var answerDisplay = isMissed ? '—' : pu.escapeHtml(player.answer_text || player.answer || '?');
            var resultDisplay = isMissed ? ('⏱️ ' + pu.escapeHtml(t('admin.answerNone'))) :
                                isCorrect ? pu.escapeHtml(t('reveal.correct')) : pu.escapeHtml(t('reveal.wrong'));
            var youLabel = pu.escapeHtml(t('lobby.you') || 'You');

            html += '<div class="result-card ' + scoreClass + (isCurrentPlayer ? ' result-card--mine' : '') + '">' +
                '<div class="card-name">' + pu.escapeHtml(player.player_name || player.name) + (isCurrentPlayer ? ' <span class="you-badge">' + youLabel + '</span>' : '') + '</div>' +
                '<div class="card-guess">' + answerDisplay + '</div>' +
                '<div class="card-accuracy">' + resultDisplay + '</div>' +
                '<div class="card-score">+' + roundScore + '</div>' +
            '</div>';
        });

        html += '</div>';
        container.innerHTML = html;
    }

    // ============================================
    // Answer Button Flash
    // ============================================

    function flashAnswerButtons(correctAnswerText, myEntry) {
        var answerButtons = document.getElementById('answer-buttons');
        if (!answerButtons) return;

        var buttons = answerButtons.querySelectorAll('.answer-btn');
        if (!buttons.length) return;

        var mySubmittedIndex = game && game.getLastSubmittedIndex ? game.getLastSubmittedIndex() : -1;

        // Match by TEXT, not shuffled index. With per-player shuffles
        // the server's correct_answer_index would be in a different
        // shuffle than this player's buttons. The button text is the
        // ground truth that survives any reshuffling.
        function _buttonAnswerText(btn) {
            var span = btn.querySelector('.answer-btn-text, .answer-text, span');
            return (span ? span.textContent : btn.textContent || '').trim();
        }
        var correctText = (correctAnswerText || '').trim();

        buttons.forEach(function(btn, i) {
            var btnText = _buttonAnswerText(btn);
            btn.classList.remove('is-selected');

            if (correctText && btnText === correctText) {
                btn.classList.add('correct');
            } else if (i === mySubmittedIndex && (!myEntry || !myEntry.correct)) {
                btn.classList.add('wrong');
            } else {
                btn.classList.add('dimmed');
            }
        });

        if (myEntry && !myEntry.correct && mySubmittedIndex >= 0) {
            var wrongBtn = buttons[mySubmittedIndex];
            if (wrongBtn) { wrongBtn.classList.remove('dimmed'); wrongBtn.classList.add('wrong'); }
        }
    }

    // ============================================
    // Flag question (🚩)
    // ============================================
    //
    // POSTs the current question's id to /api/quizify/flag-question so the
    // pack maintainer can triage ambiguous or wrong answers later. Reset
    // each round so a player can't spam-flag.

    function _resetFlagButton() {
        var btn = document.getElementById('flag-question-btn');
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🚩';
            btn.classList.remove('is-flagged');
        }
    }

    function _wireFlagButton() {
        var btn = document.getElementById('flag-question-btn');
        if (!btn || btn._wired) return;
        btn._wired = true;
        btn.addEventListener('click', function () {
            if (_flaggedThisRound || !_currentQuestionId) return;
            _flaggedThisRound = true;
            btn.disabled = true;
            btn.textContent = '✓';
            btn.classList.add('is-flagged');
            fetch('/api/quizify/flag-question', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    question_id: _currentQuestionId,
                    player_name: state.playerName || '',
                    reason: '',
                }),
            }).then(function (r) {
                if (!r.ok) throw new Error('flag failed: ' + r.status);
                pu.showToast(t('reveal.flagThanks'), 1800);
            }).catch(function (err) {
                console.warn('[Quizify] flag error', err);
                // Re-enable so the user can retry.
                _flaggedThisRound = false;
                _resetFlagButton();
            });
        });
    }

    // Wire once the module loads — the button lives in player.html.
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _wireFlagButton);
    } else {
        _wireFlagButton();
    }

    // ============================================
    // Export
    // ============================================

    window.QuizifyPlayerReveal = {
        updateRevealView: updateRevealView,
        renderFunFact: renderFunFact,
        renderRevealEmotion: renderRevealEmotion,
        renderPersonalResult: renderPersonalResult,
        renderAllAnswers: renderAllAnswers
    };

})();
