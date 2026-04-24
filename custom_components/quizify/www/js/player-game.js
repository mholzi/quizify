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

    /**
     * Start countdown timer
     * @param {number} deadline - Server deadline timestamp in milliseconds
     */
    function startCountdown(deadline) {
        stopCountdown();

        var timerElement = document.getElementById('timer');
        if (!timerElement) return;

        timerElement.classList.remove('timer--warning', 'timer--critical');

        function updateCountdown() {
            var now = Date.now();
            var remaining = Math.max(0, Math.ceil((deadline - now) / 1000));

            timerElement.textContent = remaining;

            if (remaining <= 5) {
                timerElement.classList.remove('timer--warning');
                timerElement.classList.add('timer--critical');
            } else if (remaining <= 10) {
                timerElement.classList.remove('timer--critical');
                timerElement.classList.add('timer--warning');
            } else {
                timerElement.classList.remove('timer--warning', 'timer--critical');
            }

            timerElement.setAttribute('aria-label', 'Time remaining: ' + remaining + ' seconds');

            if (remaining <= 0) {
                timerElement.setAttribute('aria-label', 'Time is up!');
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

    /**
     * Update timer from server tick
     * @param {number} remaining - Remaining seconds
     */
    function updateTimer(remaining) {
        var timerElement = document.getElementById('timer');
        if (!timerElement) return;

        timerElement.textContent = remaining;

        if (remaining <= 5) {
            timerElement.classList.remove('timer--warning');
            timerElement.classList.add('timer--critical');
        } else if (remaining <= 10) {
            timerElement.classList.remove('timer--critical');
            timerElement.classList.add('timer--warning');
        } else {
            timerElement.classList.remove('timer--warning', 'timer--critical');
        }

        timerElement.setAttribute('aria-label', 'Time remaining: ' + remaining + ' seconds');
    }

    // ============================================
    // Question Rendering
    // ============================================

    /**
     * Render question text and answer buttons
     * @param {Object} data - Question data with text, answers, category
     */
    function renderQuestion(data) {
        var questionText = document.getElementById('question-text');
        var questionCategory = document.getElementById('question-category');
        var answerButtons = document.getElementById('answer-buttons');

        if (questionText) questionText.textContent = data.question_text || '';
        if (questionCategory) questionCategory.textContent = data.category || '';

        if (answerButtons) {
            var labels = ['A', 'B', 'C'];
            var answers = data.answers || [];
            var buttons = answerButtons.querySelectorAll('.answer-btn');

            for (var i = 0; i < buttons.length; i++) {
                var btn = buttons[i];
                btn.dataset.index = String(i);

                var textEl = btn.querySelector('.answer-text');
                if (textEl) textEl.textContent = answers[i] || '';

                // Re-apply selected state if player already submitted
                if (hasSubmitted && lastSubmittedIndex === i) {
                    btn.disabled = true;
                    btn.classList.add('is-selected');
                    btn.classList.remove('is-correct', 'is-wrong', 'is-eliminated', 'hidden');
                } else if (hasSubmitted) {
                    btn.disabled = true;
                    btn.classList.remove('is-selected', 'is-correct', 'is-wrong', 'is-eliminated', 'hidden');
                } else {
                    btn.disabled = false;
                    btn.classList.remove('is-selected', 'is-correct', 'is-wrong', 'is-eliminated', 'hidden');
                }
            }
        }

        // Show/hide submitted confirmation
        var confirmation = document.getElementById('submitted-confirmation');
        if (confirmation) confirmation.classList.toggle('hidden', !hasSubmitted);
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
    function renderSubmissionTracker(players) {
        var tracker = document.getElementById('submission-tracker');
        var container = document.getElementById('submitted-players');

        if (!tracker || !container) return;

        var playerList = players || [];
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
     * Update leaderboard summary badge
     */
    function updateLeaderboardSummary(leaderboard) {
        var summaryIds = ['leaderboard-summary', 'reveal-leaderboard-summary'];

        summaryIds.forEach(function (id) {
            var summaryEl = document.getElementById(id);
            if (!summaryEl || !leaderboard || leaderboard.length === 0) return;

            var leader = leaderboard[0];
            if (leader) {
                summaryEl.textContent = leader.name + ': ' + leader.score;
            }
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
        updateTimer: updateTimer,
        renderQuestion: renderQuestion,
        handleAnswerClick: handleAnswerClick,
        lockSubmitted: lockSubmitted,
        resetSubmissionState: resetSubmissionState,
        updateGameView: updateGameView,
        renderSubmissionTracker: renderSubmissionTracker,
        updateLeaderboard: updateLeaderboard,
        renderLeaderboardEntry: renderLeaderboardEntry,
        updateLeaderboardSummary: updateLeaderboardSummary,
        renderPowerUp: renderPowerUp,
        applyJoker: applyJoker,
        handleAnswerResult: handleAnswerResult,
        getLastAnswerResult: getLastAnswerResult,
        clearLastAnswerResult: clearLastAnswerResult,
        getLastSubmittedIndex: getLastSubmittedIndex
    };

})();
