/**
 * Quizify Player - End Module
 * End screen — Variant D "Hybrid" (#338): winner hero banner, horizontal
 * highlight chip row, ranked scoreboard with score bars + inline badges,
 * play-again / new-game controls. Renders entirely from the live finale
 * payload (leaderboard + superlatives) — no hard-coded sample data.
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

    // _tf — translate with an English/literal fallback when the key is
    // missing (i18n.t returns the key unchanged on a miss).
    function _tf(key, fallback, params) {
        var v = _t(key, params);
        return (v === key) ? fallback : v;
    }

    // ============================================
    // End View
    // ============================================

    /**
     * Update end view with final standings and stats
     * @param {Object} data - State data with leaderboard and superlatives
     */
    function updateEndView(data) {
        window.scrollTo(0, 0);
        var leaderboard = (data.leaderboard || []).slice();

        leaderboard.forEach(function (entry) {
            entry.is_current = (entry.name === state.playerName);
        });
        // Defensive: ensure rank order (server sends sorted, but be safe).
        leaderboard.sort(function (a, b) {
            return (a.rank || 0) - (b.rank || 0);
        });

        var superlatives = data.superlatives || [];

        // 1. Header meta line: "N Runden · M Spieler"
        renderEndHeader(leaderboard, data);

        // 2. Winner hero banner
        renderWinnerHero(leaderboard);

        // Build the highlight set once — shared by the chip row (3) and the
        // inline scoreboard badges (4), keyed by winner name.
        var highlights = buildHighlights(leaderboard, superlatives);

        // 3. Highlights chip row
        renderHighlightChips(highlights);

        // 4. Gesamtwertung scoreboard with bars + inline badges
        renderScoreboard(leaderboard, highlights);

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
    // Header meta line
    // ============================================

    /**
     * Fill the "N Runden · M Spieler" meta line under the title.
     * Rounds come from total_rounds (or the max rounds_played fallback);
     * players from the leaderboard length.
     */
    function renderEndHeader(leaderboard, data) {
        var metaEl = document.getElementById('end-meta-line');
        if (!metaEl) return;

        var rounds = (data && data.total_rounds) || 0;
        if (!rounds) {
            // Fallback: the highest rounds_played across players.
            leaderboard.forEach(function (p) {
                if ((p.rounds_played || 0) > rounds) rounds = p.rounds_played;
            });
        }
        var players = leaderboard.length;

        var parts = [];
        if (rounds) {
            parts.push(_tf('leaderboard.roundsCount', '{count} Runden', { count: rounds }));
        }
        parts.push(_tf('leaderboard.playersCount', '{count} Spieler', { count: players }));
        metaEl.textContent = parts.join(' · ');
    }

    // ============================================
    // Winner hero banner (SIEGER)
    // ============================================

    function renderWinnerHero(leaderboard) {
        var nameEl = document.getElementById('end-hero-name');
        var scoreEl = document.getElementById('end-hero-score');
        var winner = leaderboard.find(function (p) { return p.rank === 1; }) || leaderboard[0];
        // textContent escapes already — no escapeHtml (would double-encode).
        if (nameEl) nameEl.textContent = winner ? winner.name : '---';
        if (scoreEl) {
            scoreEl.textContent = winner
                ? _t('game.pointsLabel', { count: winner.score })
                : '';
        }
    }

    // ============================================
    // Highlights — shared model
    // ============================================

    // award_key → chip disc tint class. Keeps the chip palette stable per
    // highlight type (sun = top/score, coral = streak, sage = best round,
    // sky = fastest) — mirrors the approved Variant-D mockup.
    var CHIP_TINT = {
        'topScore': 'disc-sun',          // overall winner / highest total
        'highlights.awards.hotStreak': 'disc-coral',
        'highlights.awards.topScore': 'disc-sage',     // best single round
        'highlights.awards.fastestFinger': 'disc-sky',
        'highlights.awards.comebackKing': 'disc-sage',
        'highlights.awards.mostAccurate': 'disc-sage',
        'highlights.awards.buzzkill': 'disc-sky',
        'highlights.awards.knowledgeExpert': 'disc-sun'
    };

    // award_key → emoji glyph for the chip disc + inline scoreboard badge.
    var CHIP_EMOJI = {
        'topScore': '🏅',
        'highlights.awards.hotStreak': '🔥',
        'highlights.awards.topScore': '🎯',
        'highlights.awards.fastestFinger': '⚡',
        'highlights.awards.comebackKing': '🚀',
        'highlights.awards.mostAccurate': '🎯',
        'highlights.awards.buzzkill': '🧊',
        'highlights.awards.knowledgeExpert': '🧠'
    };

    /**
     * Build the highlight list rendered by the chip row + inline badges.
     * Source of truth is the server-computed ``superlatives`` payload (already
     * i18n-keyed), prepended with a derived "Höchste Punktzahl" chip for the
     * overall winner. Renders ONLY the highlights the payload provides — a
     * missing superlative simply omits its chip.
     *
     * @returns {Array<{key,emoji,tint,title,value,player,badge}>}
     */
    function buildHighlights(leaderboard, superlatives) {
        var out = [];
        var seenPlayerBadge = {};

        // Derived: overall highest total score (the winner).
        var winner = leaderboard.find(function (p) { return p.rank === 1; }) || leaderboard[0];
        if (winner && winner.score > 0) {
            out.push({
                key: 'topScore',
                emoji: CHIP_EMOJI.topScore,
                tint: CHIP_TINT.topScore,
                title: _tf('highlights.topScore', 'Höchste Punktzahl'),
                value: _t('highlights.scoreUnit', { count: winner.score }),
                player: winner.name,
                badge: '🏅'
            });
        }

        // Server-computed superlatives (fastest finger, hot streak, best round,
        // comeback, etc.). Each carries award_key/detail_key + winner.
        (superlatives || []).forEach(function (award) {
            var key = award.award_key || '';
            var emoji = CHIP_EMOJI[key] || award.icon || '🏆';
            var tint = CHIP_TINT[key] || 'disc-sage';

            var title = key ? _t(key) : (award.award || '');
            if (key && title === key) title = award.award || '';

            var detailKey = award.detail_key;
            var detailParams = award.detail_params || {};
            var value = detailKey ? _t(detailKey, detailParams) : (award.detail || '');
            if (detailKey && value === detailKey) value = award.detail || '';

            out.push({
                key: key,
                emoji: emoji,
                tint: tint,
                title: title,
                value: value,
                player: award.winner || '',
                badge: emoji
            });
        });

        // Resolve inline scoreboard badges: each player gets the emoji of every
        // highlight they own (dedup per player+emoji).
        out.forEach(function (h) {
            if (!h.player) return;
            var k = h.player + '|' + h.badge;
            if (seenPlayerBadge[k]) { h._dupBadge = true; return; }
            seenPlayerBadge[k] = true;
        });

        return out;
    }

    // ============================================
    // Highlights chip row (horizontal scroll)
    // ============================================

    function renderHighlightChips(highlights) {
        var section = document.getElementById('end-highlights-section');
        var row = document.getElementById('end-chiprow');
        if (!section || !row) return;

        if (!highlights || highlights.length === 0) {
            section.classList.add('hidden');
            row.innerHTML = '';
            return;
        }

        row.innerHTML = highlights.map(function (h) {
            var who = h.player ? '<div class="rchip-who">' + pu.escapeHtml(h.player) + '</div>' : '';
            return '<div class="rchip">' +
                '<div class="rchip-disc ' + h.tint + '">' + pu.escapeHtml(h.emoji) + '</div>' +
                '<div class="rchip-main">' +
                    '<div class="rchip-title">' + pu.escapeHtml(h.title) + '</div>' +
                    '<div class="rchip-val">' + pu.escapeHtml(String(h.value)) + '</div>' +
                    who +
                '</div>' +
            '</div>';
        }).join('');
        section.classList.remove('hidden');
    }

    // ============================================
    // Gesamtwertung scoreboard (bars + inline badges)
    // ============================================

    function renderScoreboard(leaderboard, highlights) {
        var board = document.getElementById('end-scoreboard');
        if (!board) return;

        var youLabel = _tf('lobby.you', 'Du');
        var awayText = _tf('lobby.away', 'away');

        // Map player name → ordered list of badge emojis they own. The "🏅"
        // overall-winner badge is implicit (rank 1 row is gold) so skip it on
        // the rows to avoid clutter; keep the earned superlative badges
        // (⚡ fastest, 🔥 streak, 🎯 best round, etc.).
        var badgesByPlayer = {};
        (highlights || []).forEach(function (h) {
            if (!h.player || h._dupBadge) return;
            if (h.key === 'topScore') return; // implicit on the gold row
            (badgesByPlayer[h.player] = badgesByPlayer[h.player] || []).push({
                emoji: h.badge,
                title: h.title
            });
        });

        var topScore = 0;
        leaderboard.forEach(function (p) { if ((p.score || 0) > topScore) topScore = p.score; });

        var html = leaderboard.map(function (entry) {
            var rank = entry.rank || 0;
            var isYou = !!entry.is_current;
            var isWinner = (rank === 1);
            var pct = topScore > 0 ? Math.max(4, Math.round((entry.score / topScore) * 100)) : 0;

            // Rank disc: gold for #1, silver for #2, neutral otherwise.
            var discClass = 'r-neutral';
            if (rank === 1) discClass = 'r-gold';
            else if (rank === 2) discClass = 'r-silver';
            else if (rank === 3) discClass = 'r-bronze';

            var barClass = isYou ? 'bar-coral' : (isWinner ? 'bar-gold' : 'bar-sage');

            var badges = (badgesByPlayer[entry.name] || []).map(function (b) {
                return '<span class="sb-badge" title="' + pu.escapeHtml(b.title) + '">' +
                    pu.escapeHtml(b.emoji) + '</span>';
            }).join('');

            var youTag = isYou
                ? '<span class="sb-you">' + pu.escapeHtml(youLabel) + '</span>' : '';

            var away = (entry.connected === false)
                ? '<span class="sb-away">(' + pu.escapeHtml(awayText) + ')</span>' : '';

            return '<div class="sb-row' + (isYou ? ' sb-row--me' : '') +
                    (entry.connected === false ? ' sb-row--away' : '') + '">' +
                '<div class="sb-rank ' + discClass + '">' + rank + '</div>' +
                '<div class="sb-body">' +
                    '<div class="sb-nameline">' +
                        '<span class="sb-name">' + pu.escapeHtml(entry.name || '') + '</span>' +
                        away + badges + youTag +
                    '</div>' +
                    '<div class="sb-barwrap"><div class="sb-bar ' + barClass +
                        '" style="width:' + pct + '%;"></div></div>' +
                '</div>' +
                '<div class="sb-score">' + (entry.score || 0) + '</div>' +
            '</div>';
        }).join('');

        board.innerHTML = html;
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
        renderWinnerHero: renderWinnerHero,
        renderScoreboard: renderScoreboard,
        renderHighlightChips: renderHighlightChips,
        buildHighlights: buildHighlights,
        setupNewGameButton: setupNewGameButton
    };

})();
