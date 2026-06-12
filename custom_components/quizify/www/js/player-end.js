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
            // textContent already HTML-escapes; an extra escapeHtml() here
            // double-encodes (e.g. "Tom & Jr" → "Tom &amp;amp; Jr").
            if (nameEl) nameEl.textContent = player ? player.name : '---';
            if (scoreEl) scoreEl.textContent = player ? player.score : '0';
        });

        // Champion title block: fill the winner's name under "Champion".
        var champEl = document.getElementById('podium-champion-name');
        if (champEl) {
            var champion = leaderboard.find(function (p) { return p.rank === 1; });
            // textContent escapes already — no escapeHtml (would double-encode).
            champEl.textContent = champion ? champion.name : '---';
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

        // Shared medal-card standings (Standings A) — same treatment as the
        // lightning recap "Totals" (#246/#248). A round medal disc carries the
        // rank (top-3 gold/silver/bronze), the current player's row is coral-
        // highlighted with a "DU"/"YOU" tag. Disconnected players keep an
        // "(away)" badge appended to the name.
        var youLabel = (_t('lobby.you') && _t('lobby.you') !== 'lobby.you')
            ? _t('lobby.you') : 'Du';
        var awayText = (_t('lobby.away') && _t('lobby.away') !== 'lobby.away')
            ? _t('lobby.away') : 'away';

        var rows = leaderboard.map(function (entry) {
            var name = entry.name || '';
            if (entry.connected === false) name += ' (' + awayText + ')';
            return {
                rank: entry.rank,
                name: name,
                score: entry.score,
                isYou: !!entry.is_current
            };
        });
        pu.renderMedalStandings(listEl, rows, { youLabel: youLabel });
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

        // Trophy Tiles: 2-col grid of square cards, each with the award glyph
        // in a rotating colored disc (coral/sage/sky/sun across the palette).
        var DISC_TINTS = ['sup-disc--coral', 'sup-disc--sage', 'sup-disc--sky', 'sup-disc--sun'];
        // award_key → shared SVG glyph (#219 P2). Renders the icon client-side
        // from the stable key rather than the server emoji; falls back to the
        // server emoji for any future award_key not in this map.
        var AWARD_GLYPH = {
            'highlights.awards.topScore': 'medal',
            'highlights.awards.fastestFinger': 'bolt',
            'highlights.awards.comebackKing': 'rocket',
            'highlights.awards.hotStreak': 'flame',
            'highlights.awards.mostAccurate': 'target',
            'highlights.awards.buzzkill': 'freeze',
            'highlights.awards.knowledgeExpert': 'brain'
        };
        var icons = window.QuizifyIcons;
        var cards = '';
        superlatives.forEach(function (award, index) {
            // Server format: {award, icon, winner, detail, award_key, detail_key, detail_params}
            // Legacy format: {title, emoji, player_name, value}
            var emoji = award.icon || award.emoji || '🏆';
            // Resolve the SVG glyph from the award_key; fall back to the emoji.
            var glyphName = AWARD_GLYPH[award.award_key];
            var glyphSvg = (glyphName && icons) ? icons.uiIcon(glyphName) : '';
            // #312 defense-in-depth: glyphSvg is trusted local markup, but the
            // emoji fallback is server-provided — escape it before injecting.
            var discInner = glyphSvg || pu.escapeHtml(emoji);
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

            var tint = DISC_TINTS[index % DISC_TINTS.length];
            cards += '<div class="superlative-card" style="animation-delay: ' + (index * 0.2) + 's">' +
                '<div class="superlative-disc ' + tint + (glyphSvg ? ' superlative-disc--svg' : '') + '">' + discInner + '</div>' +
                '<div class="superlative-title">' + pu.escapeHtml(title) + '</div>' +
                '<div class="superlative-player">' + pu.escapeHtml(player) + '</div>' +
                '<div class="superlative-value">' + pu.escapeHtml(String(detail)) + '</div>' +
            '</div>';
        });

        container.innerHTML =
            '<div class="superlatives-header" data-i18n="leaderboard.awardsHeader">' +
                pu.escapeHtml(_t('leaderboard.awardsHeader') !== 'leaderboard.awardsHeader' ? _t('leaderboard.awardsHeader') : 'Auszeichnungen') +
            '</div>' +
            '<div class="superlatives-grid">' + cards + '</div>';
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

        // Vertical Timeline: a sage spine with a rotating-color dot per moment,
        // each card branching to the right. Reads top-to-bottom as the story
        // of the game.
        var DOT_TINTS = ['hl-dot--coral', 'hl-dot--sage', 'hl-dot--sky', 'hl-dot--sun'];
        listEl.innerHTML = highlights.map(function(h, i) {
            var tint = DOT_TINTS[i % DOT_TINTS.length];
            return '<div class="highlight-item" style="animation-delay:' + (i * 0.15) + 's">' +
                '<span class="highlight-dot ' + tint + '"></span>' +
                '<div class="highlight-card">' +
                    '<div class="highlight-label">' + pu.escapeHtml(h.label) + '</div>' +
                    '<div class="highlight-player">' + pu.escapeHtml(h.player) + '</div>' +
                    '<div class="highlight-value">' + pu.escapeHtml(h.value) + '</div>' +
                '</div>' +
            '</div>';
        }).join('');
        container.classList.remove('hidden');
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

        // The manual Lightning Round finale trigger (issue #42) was retired in
        // #285 — the Lightning Round is now an automatic mid-game event.

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
        setupNewGameButton: setupNewGameButton,
        renderHighlights: renderHighlights
    };

})();
