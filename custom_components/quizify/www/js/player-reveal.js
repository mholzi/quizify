/**
 * Quizify Player - Reveal Module
 * Reveal phase: correct answer display, personal result, all answers grid, reactions
 */

(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var game = window.QuizifyPlayerGame;
    var utils = window.QuizifyUtils || {};
    var state = pu.state;

    // ============================================
    // Reveal View Update
    // ============================================

    /**
     * Update reveal view with round results
     * @param {Object} data - State data from server
     */
    function updateRevealView(data) {
        var players = data.players || [];
        var allAnswers = data.all_answers || [];

        // Find current player's entry in all_answers for personal result
        var myAnswerEntry = null;
        for (var i = 0; i < allAnswers.length; i++) {
            if (allAnswers[i].player_name === state.playerName) {
                myAnswerEntry = allAnswers[i];
                break;
            }
        }

        // Round indicator
        var roundEl = document.getElementById('reveal-round');
        var totalEl = document.getElementById('reveal-total');
        if (roundEl) roundEl.textContent = data.round || 1;
        if (totalEl) totalEl.textContent = data.total_rounds || 10;

        // Question text in reveal header
        var revealQuestionText = document.getElementById('reveal-question-text');
        if (revealQuestionText && data.question) {
            revealQuestionText.textContent = data.question.text || data.question_text || '';
        }

        // Correct answer
        var correctAnswerEl = document.getElementById('correct-answer');
        if (correctAnswerEl) {
            correctAnswerEl.textContent = data.correct_answer || '';
        }

        // Fun fact
        renderFunFact(data.fun_fact);

        // Find current player
        var currentPlayer = null;
        for (var i = 0; i < players.length; i++) {
            if (players[i].name === state.playerName) {
                currentPlayer = players[i];
                break;
            }
        }

        // Emotion display — prefer all_answers entry (has speed_bonus, correct, etc.)
        renderRevealEmotion(myAnswerEntry || currentPlayer);

        // Flash answer buttons correct/wrong
        flashAnswerButtons(data.correct_answer_index, myAnswerEntry);

        // Personal result — prefer all_answers entry (has breakdown), fall back to leaderboard player
        renderPersonalResult(myAnswerEntry || currentPlayer);

        // All answers grid — use all_answers if available, fall back to players
        renderAllAnswers(allAnswers.length > 0 ? allAnswers : players);

        // Leaderboard
        if (data.leaderboard) {
            game.updateLeaderboard(data, 'reveal-leaderboard-list');
        }

        // Admin controls
        var adminControls = document.getElementById('reveal-admin-controls');
        var nextRoundBtn = document.getElementById('next-round-btn');
        if (adminControls && currentPlayer && currentPlayer.is_admin) {
            adminControls.classList.remove('hidden');

            if (nextRoundBtn) {
                if (data.last_round) {
                    nextRoundBtn.textContent = utils.t ? utils.t('leaderboard.finalResults') : 'Final Results';
                    nextRoundBtn.classList.add('is-final');
                } else {
                    nextRoundBtn.textContent = utils.t ? utils.t('admin.nextRound') : 'Next Round';
                    nextRoundBtn.classList.remove('is-final');
                }
                nextRoundBtn.disabled = false;
            }
        } else if (adminControls) {
            adminControls.classList.add('hidden');
        }

        // (Confetti handled inside renderRevealEmotion)
    }

    // ============================================
    // Fun Fact
    // ============================================

    /**
     * Render fun fact slide-in card
     * @param {string|null} text - Fun fact text
     */
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

        var exactTexts   = ['CORRECT! 🎯', 'NAILED IT! 🎯', 'SPOT ON! ✨', 'BULLSEYE! 🎯'];
        var closeTexts   = ['So close! 😅', 'Nearly there! 🙌', 'Good try! 👍'];
        var wrongTexts   = ['Wrong! ❌', 'Nope! 😬', 'Not quite! 😅', 'Oops! 💨'];
        var missedTexts  = ['Too slow! ⏱️', 'Time\'s up! ⏰', 'Missed it! 😬'];

        var exactSubs    = ['Perfect answer!', 'You knew it!', 'Impressive!'];
        var fastSubs     = ['Lightning fast ⚡', 'Speed bonus earned!'];
        var streakSubs   = ['🔥 ' + streak + '-streak!', 'On a roll! 🔥'];
        var wrongSubs    = ['Better luck next time', 'Keep going!', 'You\'ll get the next one'];
        var missedSubs   = ['No answer submitted', 'You missed this one'];

        function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

        var emotionType, emotionText, subtitle;

        if (missed) {
            emotionType = 'missed';
            emotionText = pick(missedTexts);
            subtitle = pick(missedSubs);
        } else if (correct) {
            emotionType = 'exact';
            emotionText = pick(exactTexts);
            if (streak >= 3) subtitle = pick(streakSubs);
            else if (speedBonus > 200) subtitle = pick(fastSubs);
            else subtitle = pick(exactSubs);
        } else {
            emotionType = 'wrong';
            emotionText = pick(wrongTexts);
            subtitle = pick(wrongSubs);
        }

        var emotionHtml = '<span class="reveal-emotion-text">' + emotionText + '</span>';
        if (subtitle) {
            emotionHtml += '<div class="reveal-emotion-subtitle">' + subtitle + '</div>';
        }
        emotionEl.innerHTML = emotionHtml;
        emotionEl.classList.add('reveal-emotion--' + emotionType);
        emotionEl.classList.remove('hidden');

        // Confetti on correct
        if (correct && typeof confetti === 'function') {
            confetti({ particleCount: 80, spread: 60, origin: { y: 0.7 } });
        }
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

        if (!player) {
            resultContent.innerHTML =
                '<div class="result-missed-container">' +
                    '<div class="result-missed-icon">👋</div>' +
                    '<div class="result-missed-text">You joined after this round started</div>' +
                    '<div class="result-missed-text" style="font-size:0.8rem;opacity:0.6;">You\'ll play from the next question!</div>' +
                '</div>';
            return;
        }

        // Missed round (no answer)
        if (player.missed_round || player.no_answer || player.answer_index === null) {
            var prevStreak = player.streak || 0;
            var streakLostHtml = prevStreak > 1
                ? '<div class="result-row"><span class="result-label">💔 Lost ' + prevStreak + '-streak!</span></div>'
                : '';
            resultContent.innerHTML =
                '<div class="result-missed-container">' +
                    '<div class="result-missed-icon">⏱️</div>' +
                    '<div class="result-missed-text">Time ran out — no answer</div>' +
                '</div>' +
                streakLostHtml +
                '<div class="result-score is-zero">0 pts</div>';
            return;
        }

        var isCorrect = player.correct === true;
        var baseScore = player.base_score || (isCorrect ? (player.round_score || player.points_earned || 0) : 0);
        var speedBonus = player.speed_bonus || 0;
        var streakBonus = player.streak_bonus || 0;
        var difficultyMult = player.difficulty_multiplier || 1.0;
        var roundScore = player.round_score || player.points_earned || 0;
        var streak = player.streak || 0;
        var answerText = player.answer_text || player.answer || '?';

        // Score breakdown rows (like Beatify)
        var breakdownHtml = '';
        if (isCorrect) {
            breakdownHtml +=
                '<div class="result-row">' +
                    '<span class="result-label">Base score</span>' +
                    '<span class="result-value">1000 pts</span>' +
                '</div>';

            if (speedBonus > 0) {
                breakdownHtml +=
                    '<div class="result-row">' +
                        '<span class="result-label">⚡ Speed bonus</span>' +
                        '<span class="result-value is-bonus">+' + speedBonus + ' pts</span>' +
                    '</div>';
            }

            if (difficultyMult > 1.0) {
                breakdownHtml +=
                    '<div class="result-row">' +
                        '<span class="result-label">🎯 Difficulty</span>' +
                        '<span class="result-value is-bonus">' + difficultyMult.toFixed(1) + 'x</span>' +
                    '</div>';
            }
        }

        var streakHtml = '';
        if (streakBonus > 0) {
            streakHtml =
                '<div class="result-row streak-bonus-row">' +
                    '<span class="result-label">🔥 ' + streak + '-streak bonus!</span>' +
                    '<span class="result-value is-streak">+' + streakBonus + ' pts</span>' +
                '</div>';
        } else if (streak > 1 && isCorrect) {
            streakHtml =
                '<div class="result-row streak-bonus-row">' +
                    '<span class="result-label">🔥 ' + streak + '-streak!</span>' +
                '</div>';
        }

        resultContent.innerHTML =
            '<div class="result-row">' +
                '<span class="result-label">Your answer</span>' +
                '<span class="result-value">' + pu.escapeHtml(answerText) + '</span>' +
            '</div>' +
            '<div class="result-row">' +
                '<span class="result-label">Result</span>' +
                '<span class="result-value ' + (isCorrect ? 'is-correct' : 'is-wrong') + '">' +
                    (isCorrect ? '✅ Correct' : '❌ Wrong') +
                '</span>' +
            '</div>' +
            breakdownHtml +
            '<div class="result-score ' + (roundScore > 0 ? 'is-score-high' : 'is-zero') + '">+' + roundScore + ' pts</div>' +
            streakHtml;
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
            var resultDisplay = isMissed ? '⏱️ No answer' :
                                isCorrect ? '✅ Correct' : '❌ Wrong';

            html += '<div class="result-card ' + scoreClass + (isCurrentPlayer ? ' result-card--mine' : '') + '">' +
                '<div class="card-name">' + pu.escapeHtml(player.player_name || player.name) + (isCurrentPlayer ? ' <span class="you-badge">You</span>' : '') + '</div>' +
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

    function flashAnswerButtons(correctShuffledIndex, myEntry) {
        var answerButtons = document.getElementById('answer-buttons');
        if (!answerButtons) return;

        var buttons = answerButtons.querySelectorAll('.answer-btn');
        if (!buttons.length) return;

        var mySubmittedIndex = game && game.getLastSubmittedIndex ? game.getLastSubmittedIndex() : -1;

        buttons.forEach(function(btn) {
            var idx = parseInt(btn.dataset.index, 10);
            btn.classList.remove('is-selected');

            if (idx === correctShuffledIndex) {
                btn.classList.add('correct');
            } else if (idx === mySubmittedIndex && (!myEntry || !myEntry.correct)) {
                btn.classList.add('wrong');
            } else {
                btn.classList.add('dimmed');
            }
        });

        // Use all_answers to determine if player was correct
        if (myEntry && !myEntry.correct && mySubmittedIndex >= 0) {
            var wrongBtn = buttons[mySubmittedIndex];
            if (wrongBtn) { wrongBtn.classList.remove('dimmed'); wrongBtn.classList.add('wrong'); }
        }
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
