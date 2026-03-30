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

        // Highlights
        renderHighlights(leaderboard);

        // Share card — server sends share_texts keyed by player name
        var shareData = data.share_data || (data.share_texts ? { emoji_grids: data.share_texts } : null);
        renderShareCard(shareData);

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
            // Support both server format {award, icon, winner, detail}
            // and legacy format {title, emoji, player_name, value}
            var emoji = award.icon || award.emoji || '🏆';
            var title = award.award || award.title || '';
            var player = award.winner || award.player_name || '';
            var detail = award.detail || award.value || '';

            html += '<div class="superlative-card" style="animation-delay: ' + (index * 0.2) + 's">' +
                '<div class="superlative-emoji">' + emoji + '</div>' +
                '<div class="superlative-title">' + pu.escapeHtml(title) + '</div>' +
                '<div class="superlative-player">' + pu.escapeHtml(player) + '</div>' +
                '<div class="superlative-value">' + pu.escapeHtml(String(detail)) + '</div>' +
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

        // PNG share card
        var saveBtn = document.getElementById('share-save-btn');
        if (saveBtn) {
            saveBtn.onclick = function () { generateShareCard(myGrid, shareData); };
        }

        container.classList.remove('hidden');
    }

    // ============================================
    // Highlights
    // ============================================

    function renderHighlights(leaderboard) {
        var container = document.getElementById('highlights-container');
        var listEl = document.getElementById('highlights-list');
        if (!container || !listEl) return;
        if (!leaderboard || leaderboard.length < 2) { container.classList.add('hidden'); return; }

        var highlights = [];

        // Top scorer this game
        var winner = leaderboard[0];
        if (winner) highlights.push({ icon: '🥇', label: 'Top Score', player: winner.name, value: winner.score + ' pts' });

        // Longest streak
        var streakLeader = leaderboard.slice().sort(function(a,b){ return (b.streak||0)-(a.streak||0); })[0];
        if (streakLeader && streakLeader.streak > 1) {
            highlights.push({ icon: '🔥', label: 'Best Streak', player: streakLeader.name, value: streakLeader.streak + ' in a row' });
        }

        // Most rounds correct (from round_history if available)
        var mostCorrect = leaderboard.slice().sort(function(a,b){
            var ac = (a.rounds_correct || 0); var bc = (b.rounds_correct || 0);
            return bc - ac;
        })[0];
        if (mostCorrect && mostCorrect.rounds_correct > 0) {
            highlights.push({ icon: '🎯', label: 'Most Correct', player: mostCorrect.name, value: mostCorrect.rounds_correct + ' questions' });
        }

        if (highlights.length === 0) { container.classList.add('hidden'); return; }

        listEl.innerHTML = highlights.map(function(h, i) {
            return '<div class="highlight-card" style="animation-delay:' + (i * 0.15) + 's">' +
                '<span class="highlight-icon">' + h.icon + '</span>' +
                '<div class="highlight-body">' +
                    '<div class="highlight-label">' + pu.escapeHtml(h.label) + '</div>' +
                    '<div class="highlight-player">' + pu.escapeHtml(h.player) + '</div>' +
                    '<div class="highlight-value">' + pu.escapeHtml(h.value) + '</div>' +
                '</div>' +
            '</div>';
        }).join('');
        container.classList.remove('hidden');
    }

    // ============================================
    // PNG Share Card
    // ============================================

    function generateShareCard(emojiGrid, shareData) {
        var canvas = document.createElement('canvas');
        canvas.width = 600;
        canvas.height = 400;
        var ctx = canvas.getContext('2d');

        // Background
        ctx.fillStyle = '#0d0e1a';
        ctx.fillRect(0, 0, 600, 400);

        // Gradient accent bar top
        var grad = ctx.createLinearGradient(0, 0, 600, 0);
        grad.addColorStop(0, '#a855f7');
        grad.addColorStop(0.5, '#ec4899');
        grad.addColorStop(1, '#22d3ee');
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, 600, 6);

        // Logo
        ctx.font = 'bold 32px system-ui, sans-serif';
        ctx.fillStyle = '#ffffff';
        ctx.fillText('🧠 Quizify', 30, 55);

        // Category
        ctx.font = '16px system-ui, sans-serif';
        ctx.fillStyle = '#a4a4b8';
        ctx.fillText((shareData && shareData.category ? shareData.category : ''), 30, 80);

        // Emoji grid
        ctx.font = '28px system-ui, sans-serif';
        ctx.fillStyle = '#ffffff';
        var lines = emojiGrid.split('\n');
        var y = 120;
        lines.forEach(function(line) {
            if (line.trim()) {
                ctx.fillText(line, 30, y);
                y += 42;
            }
        });

        // Footer
        ctx.font = '14px system-ui, sans-serif';
        ctx.fillStyle = '#a855f7';
        ctx.fillText('quizify.fun', 30, 370);

        // Download or share
        canvas.toBlob(function(blob) {
            var url = URL.createObjectURL(blob);
            if (navigator.share && navigator.canShare && navigator.canShare({ files: [new File([blob], 'quizify.png', { type: 'image/png' })] })) {
                navigator.share({
                    title: 'Quizify Result',
                    files: [new File([blob], 'quizify.png', { type: 'image/png' })]
                }).catch(function() {});
            } else {
                var a = document.createElement('a');
                a.href = url;
                a.download = 'quizify-result.png';
                a.click();
                setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
            }
        }, 'image/png');
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
        setupNewGameButton: setupNewGameButton,
        renderHighlights: renderHighlights
    };

})();
