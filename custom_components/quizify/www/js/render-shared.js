/**
 * Quizify — renderers the three surfaces genuinely share (#787).
 *
 * The television, the host page and the phone each carried their own copy of
 * the same handful of renderers: a leaderboard row, the +/- chip a steal
 * leaves behind, the power-up sentence, the progressive blur, the preload of
 * the next round's picture. Twenty function names were duplicated between the
 * dashboard and the player alone. Every one of those pairs is a place where a
 * fix lands on one screen and not the other, which is how #741 (the TV never
 * showed power-ups) and #733/#734 (TV-only i18n gaps) happened.
 *
 * The rule for what belongs here: the *markup and the arithmetic* are shared,
 * the *element references and the class names are not*. So every function
 * takes what differs as an argument and returns identical output for identical
 * input. Nothing here reads `els`, holds a surface's state, or knows which
 * page it is on.
 *
 * Loaded from `common.bundle.js`, after `utils.js` (for `escapeHtml`).
 */

(function () {
    'use strict';

    // Same escape as utils.js, resolved at call time rather than load time so
    // the module order can never make this silently stop escaping. The inline
    // copy is the fallback for a page that has not loaded utils.js — it is
    // deliberately identical, not "close enough".
    function _esc(value) {
        var u = window.QuizifyUtils;
        if (u && u.escapeHtml) return u.escapeHtml(value);
        if (value == null) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /** Translate, or fall back to the caller's English when the key is unset.
     *
     * `vars` is passed through to the bundle. The `value !== key` guard is the
     * point of the wrapper: `QuizifyI18n.t` hands back the key itself when no
     * bundle is loaded, so falling back on falsiness would put
     * "hotSeat.hostSeated" on the screen instead of a sentence (#732).
     */
    function _t(key, fallback, vars) {
        var i18n = window.QuizifyI18n;
        if (i18n && typeof i18n.t === 'function') {
            var value = i18n.t(key, vars);
            if (value && value !== key) return value;
        }
        return fallback != null ? fallback : key;
    }

    // ============================================
    // Leaderboard rows
    // ============================================

    /**
     * The rows, as HTML. Every surface prints the same four spans in the same
     * order; what differs is what each hangs off them —
     *
     *   opts.nameSuffix(p) — the phone's "(you)" badge.
     *   opts.afterScore(p) — the board's +/- steal chip.
     *
     * Both default to nothing, which is exactly what the third surface wants.
     * Capping (the TV shows five in-game) and the "+N more" tail stay with the
     * caller: they are that panel's layout, not the row's shape.
     */
    function leaderboardRowsHtml(players, opts) {
        opts = opts || {};
        var nameSuffix = opts.nameSuffix;
        var afterScore = opts.afterScore;
        return players
            .map(function (p, i) {
                var rank = p.rank || i + 1;
                var rankClass = rank <= 3 ? ' rank-' + rank : '';
                return '<div class="leaderboard-row">' +
                    '<span class="leaderboard-rank' + rankClass + '">' + rank + '</span>' +
                    '<span class="leaderboard-name">' + _esc(p.name) +
                        (nameSuffix ? nameSuffix(p) : '') + '</span>' +
                    '<span class="leaderboard-score">' + p.score + '</span>' +
                    (afterScore ? afterScore(p) : '') +
                    (p.streak > 1 ? '<span class="leaderboard-streak">' + p.streak + 'x</span>' : '') +
                    '</div>';
            })
            .join('');
    }

    // ============================================
    // Podium
    // ============================================

    var PODIUM_MEDALS = { 1: '\uD83E\uDD47', 2: '\uD83E\uDD48', 3: '\uD83E\uDD49' };
    var PODIUM_BAR_CLASS = { 1: 'first', 2: 'second', 3: 'third' };

    /**
     * The three planks, as HTML (#883).
     *
     * Written twice by hand until now — once on the television with a medal and
     * a `.podium-label` wrapper, once on the host page with a champion title and
     * no avatar — and the two had drifted far enough apart that
     * `05-finale.css` carried `.podium-bar` **and** `.podium-stand`, plus a
     * "legacy" avatar rule whose comment pointed at the page that had stopped
     * rendering it. One stylesheet styling two DOM shapes for one feature is
     * how the bleed in #880 got its foothold.
     *
     * The order is 2 — 1 — 3 on both surfaces and is not an option: it is what
     * makes the champion the middle, tallest plank. What the callers do differ
     * on is passed in —
     *
     *   opts.pointsLabel   the translated "pts"; the two pages read it from
     *                      different i18n keys (`dashboard.pointsShort` vs
     *                      `leaderboard.pointsShort`).
     *   opts.medals        the television's medal glyph, inside the extra
     *                      `.podium-label` wrapper its layout hangs the
     *                      top-aligned block off.
     *   opts.championLabel with a winner, prepends the host page's
     *                      `.podium-title` / `.podium-champion-name` block.
     *   opts.wrap          wrap the planks in `<div class="podium">`. The TV
     *                      renders into an element that already carries the
     *                      class; the host page renders into a section.
     */
    function podiumHtml(podium, opts) {
        opts = opts || {};
        var pointsLabel = opts.pointsLabel != null ? opts.pointsLabel : '';
        var ordered = [];
        if (podium[1]) ordered.push({ name: podium[1].name, score: podium[1].score, place: 2 });
        if (podium[0]) ordered.push({ name: podium[0].name, score: podium[0].score, place: 1 });
        if (podium[2]) ordered.push({ name: podium[2].name, score: podium[2].score, place: 3 });

        var planks = ordered
            .map(function (p) {
                var label =
                    (opts.medals
                        ? '<div class="podium-avatar">' + (PODIUM_MEDALS[p.place] || '') + '</div>'
                        : '') +
                    '<div class="podium-name">' + _esc(p.name) + '</div>' +
                    '<div class="podium-score">' + p.score + ' ' + _esc(pointsLabel) + '</div>';
                if (opts.medals) {
                    label = '<div class="podium-label">' + label + '</div>';
                }
                return '<div class="podium-place">' + label +
                    '<div class="podium-bar ' + (PODIUM_BAR_CLASS[p.place] || '') + '">' +
                        p.place +
                    '</div>' +
                    '</div>';
            })
            .join('');

        var title = '';
        if (opts.championLabel && podium[0]) {
            title = '<div class="podium-title">' + _esc(opts.championLabel) + '</div>' +
                '<div class="podium-champion-name">' + _esc(podium[0].name) + '</div>';
        }
        return title + (opts.wrap ? '<div class="podium">' + planks + '</div>' : planks);
    }

    // ============================================
    // Score deltas — the transient +/- chip after a steal
    // ============================================

    var SCORE_DELTA_MS = 4000;

    /**
     * Held in a closure rather than poked into the DOM, because `game_state`
     * repaints the leaderboard every few seconds and would wipe the chips
     * mid-stand. `repaint` is how the owner redraws its own leaderboard.
     */
    function createScoreDeltas(opts) {
        opts = opts || {};
        var holdMs = opts.holdMs != null ? opts.holdMs : SCORE_DELTA_MS;
        var repaint = opts.repaint || function () {};
        var deltas = {};
        var timer = null;

        function show(list) {
            list.forEach(function (d) {
                if (d.name) deltas[d.name] = d.points;
            });
            repaint();
            if (timer) clearTimeout(timer);
            timer = setTimeout(function () {
                deltas = {};
                timer = null;
                repaint();
            }, holdMs);
        }

        function html(name) {
            var delta = deltas[name];
            if (!delta) return '';
            return '<span class="leaderboard-delta ' + (delta < 0 ? 'is-down' : 'is-up') + '">' +
                (delta < 0 ? '\u2212' : '+') + Math.abs(delta) + '</span>';
        }

        return { show: show, html: html };
    }

    // ============================================
    // Power-ups
    // ============================================

    var POWERUP_SPECS = {
        freeze: {
            icon: '\uD83E\uDDCA',
            key: 'dashboard.powerupFreeze',
            fallback: '{source} froze {target}'
        },
        steal: {
            icon: '\uD83E\uDD77',
            key: 'dashboard.powerupSteal',
            fallback: '{source} stole {points} points from {target}'
        }
    };

    /**
     * Fills a whole-sentence template. The placeholders are substituted here
     * rather than through `i18n.t(key, vars)` for two reasons: the names and
     * the point count are marked up (gold, tabular), and German and Spanish
     * put them in a different order than English — so the unit that gets
     * translated has to be the whole sentence, never fragments concatenated in
     * English order.
     *
     * `classes` names the two spans, because the board and the host page style
     * them differently (`dashboard-powerup-name` vs `powerup-banner-name`).
     */
    function powerUpSentenceHtml(spec, vars, classes) {
        classes = classes || {};
        var nameClass = classes.name || 'powerup-banner-name';
        var pointsClass = classes.points || 'powerup-banner-points';
        return _t(spec.key, spec.fallback).replace(/\{(\w+)\}/g, function (_m, name) {
            var value = vars[name];
            if (value == null) return '';
            var cls = name === 'points' ? pointsClass : nameClass;
            return '<span class="' + cls + '">' + _esc(String(value)) + '</span>';
        });
    }

    /**
     * The `powerup_applied` reading both boards do, byte for byte: look the
     * spec up, refuse to print half a sentence, and let a steal move the two
     * rows it moved.
     *
     *   opts.showBanner(spec, vars)  — paint the strip.
     *   opts.showScoreDeltas(list)   — optional; only a steal has points.
     */
    function createPowerUpApplied(opts) {
        var showBanner = opts.showBanner;
        var showScoreDeltas = opts.showScoreDeltas;
        return function handlePowerUpApplied(msg) {
            var spec = POWERUP_SPECS[msg.powerup_type];
            if (!spec) return;
            var source = msg.source_player || '';
            var target = msg.target_player || '';
            // Both names or no sentence — "Anna froze" is worse than silence.
            if (!source || !target) return;
            var points = Math.abs(Number(msg.stolen_points) || 0);
            showBanner(spec, { source: source, target: target, points: points });
            if (msg.powerup_type === 'steal' && points > 0 && showScoreDeltas) {
                showScoreDeltas([
                    { name: source, points: points },
                    { name: target, points: -points }
                ]);
            }
        };
    }

    // ============================================
    // The lobby roster (#787)
    // ============================================

    /**
     * The roster as an array, however the frame carried it.
     *
     * `player_joined` sends a list; some snapshot paths send the players dict
     * keyed by name. The television and the host page each did this read by
     * hand and each got it right, which is exactly how long that lasts.
     */
    function rosterList(players) {
        if (Array.isArray(players)) return players;
        if (players && typeof players === 'object') return Object.values(players);
        return [];
    }

    function _rosterName(entry) {
        if (typeof entry === 'string') return entry;
        return (entry && entry.name != null) ? entry.name : '';
    }

    /**
     * The lobby grouped by team (#365, #804), as HTML.
     *
     * Written twice: the television since #365, the host page since #804, and
     * `admin.js` said so in a comment — "same grouping the television has used
     * since #365" — which is a duplicate documenting itself rather than being
     * removed. A player in no team is a team of one, not an error state, so
     * they keep their own entry below the groups instead of being swept into a
     * leftover bucket. A member named in `teams` but missing from `players`
     * still renders: a roster frame and a `teams_update` can arrive either way
     * round.
     *
     * What the two surfaces differ on is passed in —
     *
     *   opts.entry(player, index) — the chip or card itself. `index` runs
     *                     continuously across the groups AND the solo tail,
     *                     because the host page's colour fallback is
     *                     palette-indexed and must not restart per team.
     *   opts.groupClass / opts.nameClass — the two wrappers' class names.
     *   opts.membersClass — the television's inner wrapper; omit for none.
     *   opts.sizeClass — the host page's member-count badge; omit for none.
     *
     * Returns '' for an empty `teams`, so the caller keeps its own "no teams,
     * render them flat" branch — the same division of labour
     * `leaderboardRowsHtml` uses for capping.
     */
    function teamGroupedRosterHtml(players, teams, opts) {
        opts = opts || {};
        var list = rosterList(players);
        var groups = teams || [];
        if (!groups.length) return '';

        var entry = opts.entry;
        var index = 0;
        var inTeam = {};

        var html = groups.map(function (team) {
            var members = (team.members || []).map(function (name) {
                inTeam[name] = true;
                for (var i = 0; i < list.length; i++) {
                    if (_rosterName(list[i]) === name) return list[i];
                }
                return { name: name };
            });
            var size = opts.sizeClass
                ? '<span class="' + opts.sizeClass + '">' + members.length + '</span>'
                : '';
            var rendered = members.map(function (member) {
                return entry(member, index++);
            }).join('');
            if (opts.membersClass) {
                rendered = '<div class="' + opts.membersClass + '">' + rendered + '</div>';
            }
            return '<div class="' + opts.groupClass + '">' +
                '<div class="' + opts.nameClass + '">' + _esc(team.name || '') + size + '</div>' +
                rendered +
                '</div>';
        }).join('');

        var solo = list.filter(function (p) { return !inTeam[_rosterName(p)]; });
        return html + solo.map(function (p) { return entry(p, index++); }).join('');
    }

    // ============================================
    // Hot Seat (#664, #698, #804, #832)
    // ============================================

    /**
     * "3 / 7" — how many have bid, never how much.
     *
     * The auction is sealed, so the count is its whole public half, and it is
     * also the only thing that moves on any screen while the room bids. All
     * three surfaces printed the same sentence from the same key with the same
     * fallback, character for character; only the element it lands in differs.
     */
    function hotSeatBidCountText(msg) {
        msg = msg || {};
        var count = msg.count || 0;
        var total = msg.total || 0;
        return _t('hotSeat.bidCount', count + ' / ' + total,
                  { count: count, total: total });
    }

    /**
     * The chair has been won, as the room is told it.
     *
     * #804: `winner` is the person taking the chair, `entrant` is who pays for
     * it — their team in team mode, the same person again otherwise. Every
     * screen names the payer, because that is the row the points move on.
     *
     * The seat holder's own "you won it" line (`hotSeat.won`) is not here: it
     * names nobody, and the phone is the one screen that knows it is talking to
     * the winner.
     *
     * Returns the key and vars beside the text because the phone writes them
     * into `data-i18n` / `data-i18n-params` so a mid-game language switch
     * re-translates the line rather than leaving the old one.
     */
    function hotSeatAward(msg) {
        msg = msg || {};
        var payer = msg.entrant || msg.winner || '';
        var vars = { name: payer, pct: msg.pct, pts: msg.stake };
        return {
            key: 'hotSeat.lost',
            name: payer,
            vars: vars,
            text: _t('hotSeat.lost', payer + ' \u2014 ' + msg.pct + '%', vars)
        };
    }

    /**
     * The settlement, read the same way on all three screens.
     *
     * `answered` is TRI-STATE on the server: true = right, false = wrong, null
     * = never answered. A bare falsy check collapses the last two, and since
     * #653 an unanswered chair costs exactly what a wrong one does — so "ran
     * out of time" against "got it wrong" is the entire distinction these three
     * keys exist to make.
     *
     * `deltas` is keyed by ENTRANT and a team's key is its id, which no screen
     * can construct (#804), so `winner_delta` is preferred. The `deltas[winner]`
     * read stays as the fallback for a frame that predates it — the host page
     * had quietly dropped it and printed 0 where the television and the phone
     * printed the real number. That drift is what this function exists to make
     * impossible.
     */
    function hotSeatSettlement(msg) {
        msg = msg || {};
        var noAnswer = msg.answered === null || msg.answered === undefined;
        var right = msg.answered === true;
        var name = msg.entrant || msg.winner || '';
        var delta = (msg.winner_delta != null)
            ? msg.winner_delta
            : ((msg.deltas && msg.deltas[msg.winner]) || 0);
        var key = noAnswer
            ? 'hotSeat.resultTimeout'
            : (right ? 'hotSeat.resultRight' : 'hotSeat.resultWrong');
        var vars = { name: name, pts: Math.abs(delta) };
        return {
            key: key,
            noAnswer: noAnswer,
            right: right,
            name: name,
            delta: delta,
            vars: vars,
            text: _t(key, name + ' ' + (delta > 0 ? '+' : '') + delta, vars)
        };
    }

    // ============================================
    // The finale frame
    // ============================================

    /**
     * The two collections a finale frame carries.
     *
     * `all_players` is the older field name and is still on the wire from the
     * snapshot path, so both boards read the same `||` chain — the kind of
     * wire-format knowledge that has no business being written twice.
     */
    function finaleStandings(msg) {
        msg = msg || {};
        return {
            podium: msg.podium || [],
            leaderboard: msg.leaderboard || msg.all_players || []
        };
    }

    // ============================================
    // Progressive image reveal (#434)
    // ============================================

    /**
     * The picture starts blurred and sharpens as the round timer drains, so an
     * early guess is worth more than a late one. Driven entirely off the
     * countdown both surfaces already receive, so the mechanic needs no
     * message of its own.
     *
     * The blur is a CSS custom property rather than a class per step: the
     * value has to move every tick, and thirty discrete classes would fight
     * the transition instead of riding it.
     *
     *   opts.targets()  — the elements to blur, looked up per call. The phone
     *                     returns two (the banner and the zoom overlay renders
     *                     a second <img> from the same src, so one tap on the
     *                     magnifier would otherwise defeat the round); the TV
     *                     returns one. Nulls are skipped.
     *   opts.maxBlurPx  — 28 on the television, 14 on the smaller canvas.
     */
    function createProgressiveReveal(opts) {
        var targets = opts.targets;
        var maxBlurPx = opts.maxBlurPx;
        var active = false;
        var armedDuration = 0;

        function each(fn) {
            (targets() || []).forEach(function (el) {
                if (el) fn(el);
            });
        }

        return {
            isActive: function () { return active; },

            /** Start blurred. `duration` is remembered for callers that do
             *  not pass one back on every tick. */
            arm: function (duration) {
                active = true;
                armedDuration = duration || 0;
                each(function (el) {
                    el.classList.add('progressive-reveal');
                    el.style.setProperty('--reveal-blur', maxBlurPx + 'px');
                });
            },

            /** Blur for a point in the round. Full at the start, zero once the
             *  clock runs out — a question nobody answered still ends up
             *  readable, which is what the reveal needs anyway. */
            set: function (remaining, duration) {
                if (!active) return;
                var d = (duration === undefined) ? armedDuration : duration;
                var frac = (d > 0) ? Math.max(0, Math.min(1, remaining / d)) : 0;
                var px = (maxBlurPx * frac).toFixed(2) + 'px';
                each(function (el) {
                    el.style.setProperty('--reveal-blur', px);
                });
            },

            /** Drop the blur entirely. Must be called at reveal: both surfaces
             *  keep the question view's image element instead of re-rendering
             *  it, so without this the correct answer would appear next to a
             *  picture nobody can make out. */
            clear: function () {
                active = false;
                each(function (el) {
                    el.classList.remove('progressive-reveal');
                    el.style.removeProperty('--reveal-blur');
                });
            }
        };
    }

    // ============================================
    // Next-round image preload (#736)
    // ============================================

    // The reference is kept on purpose: a detached Image with no live
    // reference is collectable, and some browsers abandon its request.
    var _preloadedImage = null;

    /**
     * Warm the NEXT round's picture during the reveal, when nothing else is on
     * the wire. Without it the fetch starts on `question_started` — by which
     * point the countdown deadline is already stamped — and every client in
     * the room pulls the same file at the same instant.
     *
     * DETACHED on purpose, and never the on-screen banner: that element still
     * holds the round being revealed, so writing to it would swap the picture
     * the room is looking at for the next one — and on a progressive-reveal
     * round (#434) it would hand out the unblurred picture a round early,
     * because the blur is applied when the banner renders and that has not run
     * yet. A `new Image()` never enters the document: it only fills the HTTP
     * cache.
     *
     * The hint from the wire gets no more trust than the round's own
     * `image_url` (#536/#540) — same sanitizer.
     */
    function preloadNextImage(url) {
        var safe = (window.QuizifyUtils && window.QuizifyUtils.safeImageUrl)
            ? window.QuizifyUtils.safeImageUrl(url) : '';
        if (!safe) return null;
        var img = new Image();
        img.decoding = 'async';
        // Failure is a non-event: the banner requests it again next round and
        // runs its own onerror fallback.
        img.onerror = function () { _preloadedImage = null; };
        img.src = safe;
        _preloadedImage = img;
        return img;
    }

    window.QuizifyRenderShared = {
        SCORE_DELTA_MS: SCORE_DELTA_MS,
        POWERUP_SPECS: POWERUP_SPECS,
        PODIUM_MEDALS: PODIUM_MEDALS,
        leaderboardRowsHtml: leaderboardRowsHtml,
        podiumHtml: podiumHtml,
        rosterList: rosterList,
        teamGroupedRosterHtml: teamGroupedRosterHtml,
        hotSeatBidCountText: hotSeatBidCountText,
        hotSeatAward: hotSeatAward,
        hotSeatSettlement: hotSeatSettlement,
        finaleStandings: finaleStandings,
        createScoreDeltas: createScoreDeltas,
        powerUpSentenceHtml: powerUpSentenceHtml,
        createPowerUpApplied: createPowerUpApplied,
        createProgressiveReveal: createProgressiveReveal,
        preloadNextImage: preloadNextImage
    };
})();
