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

        // Emotion display
        renderRevealEmotion(currentPlayer);

        // Personal result
        renderPersonalResult(currentPlayer);

        // All answers grid
        renderAllAnswers(players);

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

        // Confetti on correct answer
        if (currentPlayer && currentPlayer.correct && typeof confetti === 'function') {
            confetti({
                particleCount: 80,
                spread: 60,
                origin: { y: 0.7 }
            });
        }
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

        var emotionType, emotionText, subtitle;

        if (player.missed_round) {
            emotionType = 'missed';
            emotionText = 'Missed!';
            subtitle = 'No answer submitted';
        } else if (player.correct) {
            emotionType = 'exact';
            emotionText = 'CORRECT! \uD83C\uDFAF';
            subtitle = 'Well done!';
        } else {
            emotionType = 'wrong';
            emotionText = 'Wrong! \u274C';
            subtitle = 'Better luck next time';
        }

        var emotionHtml = '<span class="reveal-emotion-text">' + emotionText + '</span>';
        if (subtitle) {
            emotionHtml += '<div class="reveal-emotion-subtitle">' + subtitle + '</div>';
        }
        emotionEl.innerHTML = emotionHtml;

        emotionEl.classList.add('reveal-emotion--' + emotionType);
        emotionEl.classList.remove('hidden');
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
            resultContent.innerHTML = '<div class="result-missed">Player not found</div>';
            return;
        }

        if (player.missed_round) {
            resultContent.innerHTML =
                '<div class="result-missed-container">' +
                    '<div class="result-missed-icon">\u23F0</div>' +
                    '<div class="result-missed-text">No answer submitted</div>' +
                '</div>' +
                '<div class="result-score is-zero">0 pts</div>';
            return;
        }

        var roundScore = player.round_score || 0;
        var resultIcon = player.correct ? '\u2705' : '\u274C';
        var resultClass = player.correct ? 'is-correct' : 'is-wrong';

        var streakHtml = '';
        if (player.streak && player.streak > 1) {
            streakHtml =
                '<div class="result-row streak-bonus-row">' +
                    '<span class="result-label">\uD83D\uDD25 ' + player.streak + '-streak!</span>' +
                '</div>';
        }

        resultContent.innerHTML =
            '<div class="result-row">' +
                '<span class="result-label">Your Answer</span>' +
                '<span class="result-value">' + pu.escapeHtml(player.answer || 'n/a') + '</span>' +
            '</div>' +
            '<div class="result-row">' +
                '<span class="result-label">Result</span>' +
                '<span class="result-value ' + resultClass + '">' + resultIcon + ' ' + (player.correct ? 'Correct' : 'Wrong') + '</span>' +
            '</div>' +
            streakHtml +
            '<div class="result-score ' + (roundScore > 0 ? '' : 'is-zero') + '">+' + roundScore + ' pts</div>';
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
            var isMissed = player.missed_round === true;
            var roundScore = player.round_score || 0;

            var scoreClass = isMissed ? 'is-score-zero' :
                             roundScore > 0 ? 'is-score-high' : 'is-score-zero';

            var answerDisplay = isMissed ? '\u2014' : pu.escapeHtml(player.answer || 'n/a');
            var resultDisplay = isMissed ? 'No answer' :
                                player.correct ? '\u2705 Correct' : '\u274C Wrong';

            html += '<div class="result-card ' + scoreClass + (isCurrentPlayer ? ' is-current' : '') + '">' +
                '<div class="card-name">' + pu.escapeHtml(player.name) + '</div>' +
                '<div class="card-guess">' + answerDisplay + '</div>' +
                '<div class="card-accuracy">' + resultDisplay + '</div>' +
                '<div class="card-score">+' + roundScore + '</div>' +
            '</div>';
        });

        html += '</div>';
        container.innerHTML = html;
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
