/**
 * Quizify Player - End Module
 * End screen: podium, superlatives, share card, full leaderboard, rematch/new-game
 */

(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var utils = window.QuizifyUtils || {};
    var state = pu.state;

    // ============================================
    // End View
    // ============================================

    /**
     * Update end view with final standings and stats
     * @param {Object} data - State data with leaderboard and game_stats
     */
    function updateEndView(data) {
        window.scrollTo(0, 0);
        var leaderboard = data.leaderboard || [];

        leaderboard.forEach(function (entry) {
            entry.is_current = (entry.name === state.playerName);
        });

        // Podium (positions 1, 2, 3)
        renderFinale(leaderboard);

        // Your result
        var currentPlayer = leaderboard.find(function (p) { return p.is_current; });
        renderYourResult(currentPlayer, data);

        // Full leaderboard
        renderFullLeaderboard(leaderboard);

        // Superlatives
        renderSuperlatives(data.superlatives);

        // Share card
        renderShareCard(data.share_data);

        // Admin / player controls
        var adminControls = document.getElementById('end-admin-controls');
        var playerMessage = document.getElementById('end-player-message');

        if (currentPlayer && currentPlayer.is_admin) {
            if (adminControls) adminControls.classList.remove('hidden');
            if (playerMessage) playerMessage.classList.add('hidden');
        } else {
            if (adminControls) adminControls.classList.add('hidden');
            if (playerMessage) playerMessage.classList.remove('hidden');
        }

        // Confetti for winner
        if (currentPlayer && currentPlayer.rank === 1 && typeof confetti === 'function') {
            setTimeout(function () {
                confetti({
                    particleCount: 150,
                    spread: 90,
                    origin: { y: 0.6 }
                });
            }, 1500);
        }
    }

    // ============================================
    // Podium / Finale
    // ============================================

    /**
     * Animate podium rise: 2nd, then 1st, then 3rd (Beatify order)
     * @param {Array} leaderboard - Sorted leaderboard
     */
    function renderFinale(leaderboard) {
        // Update podium places
        [1, 2, 3].forEach(function (place) {
            var player = leaderboard.find(function (p) { return p.rank === place; });
            var nameEl = document.getElementById('podium-' + place + '-name');
            var scoreEl = document.getElementById('podium-' + place + '-score');
            if (nameEl) nameEl.textContent = player ? pu.escapeHtml(player.name) : '---';
            if (scoreEl) scoreEl.textContent = player ? player.score : '0';
        });

        // Animate podium rise with delays: 2nd (0s), 1st (1s), 3rd (2s)
        var podiumPlaces = document.querySelectorAll('.podium-place');
        podiumPlaces.forEach(function (el) {
            el.classList.remove('podium-rise');
        });

        // 2nd place rises first
        var place2 = document.querySelector('.podium-2');
        if (place2) {
            setTimeout(function () { place2.classList.add('podium-rise'); }, 300);
        }

        // 1st place rises second
        var place1 = document.querySelector('.podium-1');
        if (place1) {
            setTimeout(function () { place1.classList.add('podium-rise'); }, 1300);
        }

        // 3rd place rises last
        var place3 = document.querySelector('.podium-3');
        if (place3) {
            setTimeout(function () { place3.classList.add('podium-rise'); }, 2300);
        }
    }

    // ============================================
    // Your Result
    // ============================================

    /**
     * Render personal final result
     * @param {Object} currentPlayer - Current player leaderboard entry
     * @param {Object} data - Full state data
     */
    function renderYourResult(currentPlayer, data) {
        var rankEl = document.getElementById('your-final-rank');
        var scoreEl = document.getElementById('your-final-score');
        var bestStreakEl = document.getElementById('stat-best-streak');
        var roundsEl = document.getElementById('stat-rounds');
        var powerupsEl = document.getElementById('stat-powerups');

        if (currentPlayer) {
            if (rankEl) rankEl.textContent = '#' + currentPlayer.rank;
            if (scoreEl) scoreEl.textContent = currentPlayer.score + ' points';
            if (bestStreakEl) bestStreakEl.textContent = currentPlayer.best_streak || 0;
            if (roundsEl) roundsEl.textContent = currentPlayer.rounds_played || 0;
            if (powerupsEl) powerupsEl.textContent = currentPlayer.powerups_used || 0;
        }
    }

    // ============================================
    // Full Leaderboard
    // ============================================

    /**
     * Render numbered leaderboard with scores
     * @param {Array} leaderboard - Full sorted leaderboard
     */
    function renderFullLeaderboard(leaderboard) {
        var listEl = document.getElementById('final-leaderboard-list');
        if (!listEl) return;

        listEl.innerHTML = leaderboard.map(function (entry) {
            var currentClass = entry.is_current ? 'is-current' : '';
            var disconnectedClass = entry.connected === false ? 'final-entry--disconnected' : '';
            var awayBadge = entry.connected === false ? '<span class="away-badge">(away)</span>' : '';
            return '<div class="final-entry ' + currentClass + ' ' + disconnectedClass + '">' +
                '<span class="final-rank">#' + entry.rank + '</span>' +
                '<span class="final-name">' + pu.escapeHtml(entry.name) + awayBadge + '</span>' +
                '<span class="final-score">' + entry.score + '</span>' +
            '</div>';
        }).join('');
    }

    // ============================================
    // Superlatives
    // ============================================

    /**
     * Render superlatives / fun awards with staggered entrance
     * @param {Array|null} superlatives - Array of award objects
     */
    function renderSuperlatives(superlatives) {
        var container = document.getElementById('superlatives-container');
        if (!container) return;

        if (!superlatives || superlatives.length === 0) {
            container.classList.add('hidden');
            return;
        }

        var html = '';
        superlatives.forEach(function (award, index) {
            var valueText = award.value || '';

            html += '<div class="superlative-card" style="animation-delay: ' + (index * 0.2) + 's">' +
                '<div class="superlative-emoji">' + (award.emoji || '\uD83C\uDFC6') + '</div>' +
                '<div class="superlative-title">' + pu.escapeHtml(award.title || '') + '</div>' +
                '<div class="superlative-player">' + pu.escapeHtml(award.player_name || '') + '</div>' +
                '<div class="superlative-value">' + pu.escapeHtml(String(valueText)) + '</div>' +
            '</div>';
        });

        container.innerHTML = html;
        container.classList.remove('hidden');
    }

    // ============================================
    // Share Card
    // ============================================

    /**
     * Render shareable emoji grid + copy button
     * @param {Object|null} shareData - Share data with emoji_grids
     */
    function renderShareCard(shareData) {
        var container = document.getElementById('share-container');
        if (!container) return;

        if (!shareData || !shareData.emoji_grids) {
            container.classList.add('hidden');
            return;
        }

        var myGrid = shareData.emoji_grids[state.playerName];
        if (!myGrid) {
            var keys = Object.keys(shareData.emoji_grids);
            if (keys.length === 1) {
                myGrid = shareData.emoji_grids[keys[0]];
            }
        }
        if (!myGrid) {
            container.classList.add('hidden');
            return;
        }

        var gridEl = document.getElementById('share-emoji-grid');
        if (gridEl) {
            var lines = myGrid.split('\n').map(function (line) {
                return '<div class="emoji-grid-line">' + pu.escapeHtml(line) + '</div>';
            }).join('');
            gridEl.innerHTML = lines;
            gridEl.dataset.rawText = myGrid;
        }

        var copyBtn = document.getElementById('share-copy-btn');
        if (copyBtn) {
            copyBtn.onclick = function () {
                navigator.clipboard.writeText(myGrid).then(function () {
                    var toast = document.getElementById('share-toast');
                    if (toast) {
                        toast.classList.remove('hidden');
                        setTimeout(function () { toast.classList.add('hidden'); }, 2000);
                    }
                });
            };
        }

        container.classList.remove('hidden');
    }

    // ============================================
    // Admin Actions
    // ============================================

    /**
     * Wire up rematch button
     * @param {Function} sendFn - Function to send WS messages
     */
    function setupRematchButton(sendFn) {
        var rematchBtn = document.getElementById('player-rematch-btn');
        if (rematchBtn) {
            rematchBtn.onclick = function () {
                rematchBtn.disabled = true;
                var origText = rematchBtn.textContent;
                rematchBtn.textContent = '\u23F3';
                sendFn('rematch', {});
                // Server will broadcast rematch_started - button stays disabled
                setTimeout(function () {
                    // Fallback restore if no response
                    if (rematchBtn.disabled) {
                        rematchBtn.disabled = false;
                        rematchBtn.textContent = origText;
                    }
                }, 10000);
            };
        }
    }

    /**
     * Wire up new game button
     */
    function setupNewGameButton() {
        var newGameBtn = document.getElementById('new-game-btn');
        if (newGameBtn) {
            newGameBtn.onclick = function () {
                try {
                    sessionStorage.removeItem('quizify_session_token');
                    sessionStorage.removeItem('quizify_player_name');
                    sessionStorage.removeItem('quizify_admin_name');
                    sessionStorage.removeItem('quizify_is_admin');
                } catch (e) { /* ignore */ }

                window.location.href = '/quizify/admin';
            };
        }
    }

    // ============================================
    // Export
    // ============================================

    window.QuizifyPlayerEnd = {
        updateEndView: updateEndView,
        renderFinale: renderFinale,
        renderYourResult: renderYourResult,
        renderFullLeaderboard: renderFullLeaderboard,
        renderSuperlatives: renderSuperlatives,
        renderShareCard: renderShareCard,
        setupRematchButton: setupRematchButton,
        setupNewGameButton: setupNewGameButton
    };

})();
