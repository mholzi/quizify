/**
 * Quizify Player - End Module
 * End screen: podium, superlatives, share card, full leaderboard, new-game
 */

(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var utils = window.QuizifyUtils || {};
    var state = pu.state;

    function _t(key, params) {
        if (window.QuizifyI18n && typeof window.QuizifyI18n.t === 'function') {
            return window.QuizifyI18n.t(key, params);
        }
        return key;
    }

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

        // Admin / player controls.
        //
        // Source-of-truth: trust ``state.isAdmin`` for gating the
        // host-only "Start New Game" button. Earlier versions only
        // looked at ``currentPlayer.is_admin`` from the leaderboard,
        // which created a chronic lockout: when a player joined while
        // the server was already in FINALE, the snapshot's leaderboard
        // (built by ``state.get_leaderboard()``) didn't include
        // ``is_admin`` per row, so the admin's button stayed hidden.
        // ``state.isAdmin`` is set from the URL ``?admin=true`` flag
        // and confirmed by the server's ``joined`` reply. v1.1.4 also
        // restores ``is_admin`` to the snapshot leaderboard, so either
        // signal is now sufficient — but local state is authoritative.
        var adminControls = document.getElementById('end-admin-controls');
        var playerMessage = document.getElementById('end-player-message');
        var amAdmin = !!state.isAdmin || !!(currentPlayer && currentPlayer.is_admin);

        if (amAdmin) {
            if (adminControls) adminControls.classList.remove('hidden');
            if (playerMessage) playerMessage.classList.add('hidden');
        } else {
            if (adminControls) adminControls.classList.add('hidden');
            if (playerMessage) playerMessage.classList.remove('hidden');
        }

        // Broadcast Living Room: no confetti on finale.
        // The restraint is the differentiation. A single spotlight and tabular score-tick IS the effect.
        // See DESIGN.md — "The dramatic pause IS the effect. Restraint crowns louder than chaos."
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

        // Champion title block: fill the winner's name under "Champion".
        var champEl = document.getElementById('podium-champion-name');
        if (champEl) {
            var champion = leaderboard.find(function (p) { return p.rank === 1; });
            champEl.textContent = champion ? pu.escapeHtml(champion.name) : '---';
        }

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
            if (scoreEl) scoreEl.textContent = _t('game.pointsLabel', { count: currentPlayer.score });
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
            var awayBadge = entry.connected === false
                ? '<span class="away-badge">(' + pu.escapeHtml(_t('lobby.away') !== 'lobby.away' ? _t('lobby.away') : 'away') + ')</span>'
                : '';
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
            // Server format: {award, icon, winner, detail, award_key, detail_key, detail_params}
            // Legacy format: {title, emoji, player_name, value}
            var emoji = award.icon || award.emoji || '🏆';
            // Prefer i18n keys if the server sent them; fall back to English literals.
            var titleKey = award.award_key;
            var detailKey = award.detail_key;
            var detailParams = award.detail_params || {};
            var title = titleKey ? _t(titleKey) : (award.award || award.title || '');
            // If _t returned the key unchanged (missing translation), keep the
            // English literal as a fallback so we never show "highlights.awards.x".
            if (titleKey && title === titleKey) title = award.award || award.title || '';
            var player = award.winner || award.player_name || '';
            var detail = detailKey ? _t(detailKey, detailParams) : (award.detail || award.value || '');
            if (detailKey && detail === detailKey) detail = award.detail || award.value || '';

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
        if (winner) highlights.push({
            icon: '🥇',
            label: _t('highlights.topScore'),
            player: winner.name,
            value: _t('highlights.scoreUnit', { count: winner.score }),
        });

        // Longest streak
        var streakLeader = leaderboard.slice().sort(function(a,b){ return (b.streak||0)-(a.streak||0); })[0];
        if (streakLeader && streakLeader.streak > 1) {
            highlights.push({
                icon: '🔥',
                label: _t('highlights.bestStreak'),
                player: streakLeader.name,
                value: _t('highlights.streakUnit', { count: streakLeader.streak }),
            });
        }

        // Most rounds correct (from round_history if available)
        var mostCorrect = leaderboard.slice().sort(function(a,b){
            var ac = (a.rounds_correct || 0); var bc = (b.rounds_correct || 0);
            return bc - ac;
        })[0];
        if (mostCorrect && mostCorrect.rounds_correct > 0) {
            highlights.push({
                icon: '🎯',
                label: _t('highlights.mostCorrect'),
                player: mostCorrect.name,
                value: _t('highlights.correctUnit', { count: mostCorrect.rounds_correct }),
            });
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

        // Background — Soft Parlor cream paper
        ctx.fillStyle = '#FAF6EC';
        ctx.fillRect(0, 0, 600, 400);

        // Accent bar top — single coral
        ctx.fillStyle = '#E88A7F';
        ctx.fillRect(0, 0, 600, 4);

        // Logo
        ctx.font = 'bold 32px "Cabinet Grotesk", system-ui, sans-serif';
        ctx.fillStyle = '#2A2820';
        ctx.fillText('Quizify', 30, 55);

        // Category
        ctx.font = '14px "JetBrains Mono", ui-monospace, monospace';
        ctx.fillStyle = '#6E6A5C';
        ctx.fillText((shareData && shareData.category ? shareData.category.toUpperCase() : ''), 30, 80);

        // Emoji grid
        ctx.font = '28px system-ui, sans-serif';
        ctx.fillStyle = '#2A2820';
        var lines = emojiGrid.split('\n');
        var y = 120;
        lines.forEach(function(line) {
            if (line.trim()) {
                ctx.fillText(line, 30, y);
                y += 42;
            }
        });

        // Footer
        ctx.font = '13px "JetBrains Mono", ui-monospace, monospace';
        ctx.fillStyle = '#E88A7F';
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

    // Rematch button removed in v1.1.3. Hosts always start a fresh
    // game from the admin URL (which also resets player session
    // tokens). Keeping a single primary CTA on the finale screen
    // simplifies the host's mental model — there's exactly one
    // post-game action.

    /**
     * Wire up the finale CTAs.
     *
     * Two buttons:
     *   - Wieder spielen (same settings): single-tap rematch. Server keeps
     *     players + scores zeroed, reuses the cached settings. The admin
     *     stays on /quizify/player and just waits for the next round.
     *   - Neues Spiel: full reset — back to /quizify/admin setup screen.
     */
    function setupNewGameButton() {
        var playAgainBtn = document.getElementById('play-again-same-btn');
        var newGameBtn = document.getElementById('new-game-btn');

        if (playAgainBtn) {
            playAgainBtn.onclick = function () {
                var ws = state.ws;
                if (!ws || ws.readyState !== WebSocket.OPEN) {
                    // Fall back to full reset if our WS is gone.
                    if (newGameBtn) newGameBtn.click();
                    return;
                }
                playAgainBtn.disabled = true;
                playAgainBtn.innerHTML = '<span>⏳</span>';
                try { ws.send(JSON.stringify({ type: 'play_again' })); } catch (e) { /* ignore */ }
                // Server will broadcast game_state QUESTION_ACTIVE → the
                // phase router in player-core picks it up and switches
                // views. No navigation needed.
            };
        }

        if (newGameBtn) {
            newGameBtn.onclick = function () {
                try {
                    sessionStorage.removeItem('quizify_session_token');
                    sessionStorage.removeItem('quizify_player_name');
                    sessionStorage.removeItem('quizify_admin_name');
                    sessionStorage.removeItem('quizify_is_admin');
                } catch (e) { /* ignore */ }

                var ws = state.ws;
                if (ws && ws.readyState === WebSocket.OPEN) {
                    try { ws.send(JSON.stringify({ type: 'reset_game' })); } catch (e) { /* ignore */ }
                }
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
        setupNewGameButton: setupNewGameButton,
        renderHighlights: renderHighlights
    };

})();
