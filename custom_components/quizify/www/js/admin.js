/**
 * Quizify — Admin panel client.
 * Manages game setup, lobby, live monitoring, and game flow control.
 */

(function () {
    'use strict';

    // ---- State ----
    let ws = null;
    let reconnectAttempts = 0;
    // Mirror the player-side reconnect policy (player-utils.js): exponential
    // backoff capped at 30s and a high attempt budget, not the old 5-attempt /
    // ~15s budget. An HA restart takes 1-5 min, so the old budget left the
    // host's admin tablet permanently dead with no recovery (#290).
    const MAX_RECONNECT = 10;
    const MAX_RECONNECT_DELAY_MS = 30000;
    let _redirecting = false;
    // When the admin joined themselves as a player via "Als Spieler
    // beitreten", we keep them on the admin page during LOBBY. This
    // holds their player name so handleGameState can redirect to
    // /quizify/player once the phase leaves LOBBY (so admin can
    // actually answer questions).
    let _adminJoinedAs = null;

    // Settings (from chips)
    let selectedCategory = 'mixed';
    let selectedCategories = [];
    let selectedDifficulty = 'medium';
    let selectedRounds = 10;
    // Initial UI language, in priority order (#152):
    //   1. Home Assistant's configured language (Settings → General),
    //      injected server-side into the quizify-ha-lang meta tag.
    //   2. Browser locale — the standalone dev server has no hass, so the
    //      meta tag is empty there.
    //   3. 'en' as a final fallback.
    // HA is authoritative; there is no localStorage persistence. A full-page
    // reload (e.g. player-end "Neues Spiel starten" → /quizify/admin) always
    // resolves back to the HA language rather than a stale per-device choice.
    let selectedLanguage = (function () {
        function normalize(code) {
            var c = String(code || '').toLowerCase();
            if (c.indexOf('de') === 0) return 'de';
            if (c.indexOf('es') === 0) return 'es';
            return 'en';
        }
        var meta = document.querySelector('meta[name="quizify-ha-lang"]');
        var haLang = meta ? meta.getAttribute('content') : '';
        // Guard against an unsubstituted {{HA_LANG}} token leaking through.
        if (haLang && haLang.indexOf('{') === -1) return normalize(haLang);
        if (window.QuizifyI18n && QuizifyI18n.detectBrowserLanguage) {
            return QuizifyI18n.detectBrowserLanguage();
        }
        return 'en';
    })();
    let selectedTimer = 30;  // seconds per question (20 / 30 / 45 / 180, #506)
    // Auto Lightning Round toggle (#285), default ON. The surprise fast round
    // fires once at a random mid-game round; the host opts out via the setup
    // toggle. Read live from the checkbox at start_game time.
    let selectedLightning = true;
    let selectedHotSeat = true;
    // Power-ups (#340) and the final-round wager (#656), reachable by the host
    // since #742. Same shape as the two above: default ON, read live from the
    // checkbox at start_game time.
    let selectedPowerups = true;
    let selectedWager = true;

    // Game state
    let currentPhase = 'LOBBY';
    let playerCount = 0;

    // #215: hook for sw-update.js — is the admin screen idle enough that a
    // service-worker-triggered reload won't interrupt anything? Idle = setup /
    // lobby, the per-round reveal, the lightning recap, and the finale end
    // screen. NOT idle during a live question or a live lightning round, where
    // a reload would yank the host out mid-play.
    window.quizifyIsIdleForReload = function () {
        switch (currentPhase) {
            case 'QUESTION_ACTIVE':
            case 'LIGHTNING':
            // #699: a reload during any of these lands on #setup-screen,
            // which is the default active view and which handleRoundSummary
            // never leaves. From there the only visible way on is Start —
            // and Start resets a non-LOBBY game, so a service-worker update
            // mid-reveal could quietly wipe every score.
            case 'ANSWER_REVEAL':
            case 'WAGER_ACTIVE':
            case 'LIGHTNING_RECAP':
            case 'HOT_SEAT_AUCTION':
            case 'HOT_SEAT':
            case 'HOT_SEAT_REVEAL':
            case 'PAUSED':
                return false;
            default:
                // LOBBY, FINALE, and any setup state before a game starts.
                return true;
        }
    };

    // ---- Simple inline timer ----
    // #706: the seconds used to be written into #admin-timer-bar, which is the
    // 6px .timer-bar-container — so the text wiped .timer-bar-fill and spilled
    // out of a strip six pixels tall, while the element built for it
    // (#admin-timer-bar-text, 1.5rem bold tabular with warning/critical
    // states) was never written at all. The text goes to the text node, the
    // bar keeps its fill, and the fill now actually empties: start() has
    // always been handed the duration and never used it.
    var adminTimerTextEl = null;
    var adminTimerFillEl = null;
    var adminTimerDuration = 0;
    var adminTimerInterval = null;

    function _adminTimerText() {
        if (!adminTimerTextEl) {
            adminTimerTextEl = document.getElementById('admin-timer-bar-text');
        }
        return adminTimerTextEl;
    }

    function _adminTimerFill() {
        if (!adminTimerFillEl) {
            var bar = document.getElementById('admin-timer-bar');
            adminTimerFillEl = bar ? bar.querySelector('.timer-bar-fill') : null;
        }
        return adminTimerFillEl;
    }

    var adminTimer = {
        start: function(duration) {
            adminTimerDuration = typeof duration === 'number' && duration > 0
                ? duration
                : 0;
            clearInterval(adminTimerInterval);
            var fill = _adminTimerFill();
            if (fill) fill.style.width = '100%';
        },
        update: function(remaining) {
            var left = Math.max(0, remaining);
            var textEl = _adminTimerText();
            if (textEl) {
                textEl.textContent = Math.ceil(left) + 's';
                // The same thresholds the player's clock uses, so host and
                // room turn red at the same moment.
                textEl.classList.toggle('critical', left <= 5);
                textEl.classList.toggle('warning', left > 5 && left <= 10);
            }
            var fill = _adminTimerFill();
            if (fill && adminTimerDuration > 0) {
                fill.style.width = (100 * left / adminTimerDuration) + '%';
            }
        },
        stop: function() {
            clearInterval(adminTimerInterval);
            var textEl = _adminTimerText();
            if (textEl) {
                textEl.textContent = '';
                textEl.classList.remove('warning', 'critical');
            }
            var fill = _adminTimerFill();
            if (fill) fill.style.width = '0%';
        }
    };

    // ---- DOM refs ----
    const views = {
        setup: document.getElementById('setup-screen'),
        lobby: document.getElementById('lobby-screen'),
        game: document.getElementById('game-view'),
        finale: document.getElementById('admin-finale-view'),
        lightning: document.getElementById('admin-lightning-view'),
        lightningRecap: document.getElementById('admin-lightning-recap-view'),
    };

    const els = {
        categoryChips: document.getElementById('category-chips'),
        heroPackChips: document.getElementById('hero-pack-chips'),
        categorySummary: document.getElementById('category-summary'),
        featuredSpotlight: document.getElementById('featured-spotlight'),
        spotlightTitle: document.getElementById('spotlight-title'),
        spotlightMeta: document.getElementById('spotlight-meta'),
        themeTabs: document.getElementById('theme-tabs'),
        difficultyChips: document.getElementById('difficulty-chips'),
        roundsChips: document.getElementById('rounds-chips'),
        timerChips: document.getElementById('timer-chips'),
        languageChips: document.getElementById('language-chips'),
        gameSettingsSummary: document.getElementById('game-settings-summary'),
        qrContainer: document.getElementById('qr-container'),
        dashboardLink: document.getElementById('dashboard-link'),
        lobbyPlayerCount: document.getElementById('lobby-player-count'),
        lobbyPlayerChips: document.getElementById('lobby-player-chips'),
        lobbyPlayersEmpty: document.getElementById('lobby-players-empty'),
        startGameBtn: document.getElementById('start-game-btn'),
        heroFeatureCard: document.getElementById('hero-feature-card'),
        startGameplayBtn: document.getElementById('start-gameplay-btn'),
        participateBtn: document.getElementById('participate-btn'),
        // In-game
        adminRound: document.getElementById('admin-round'),
        adminQuestion: document.getElementById('admin-question'),
        // #832: the Hot Seat's second line — bid count, price of the chair,
        // settlement. Not #admin-correct, which is styled as a success.
        adminDetourNote: document.getElementById('admin-detour-note'),
        adminCorrect: document.getElementById('admin-correct'),
        gameLeaderboard: document.getElementById('game-leaderboard'),
        // #741: the same power-up strip the television got, on the screen
        // where the host notices why the points jumped.
        powerupBanners: document.getElementById('powerup-banners'),
        reactionLayer: document.getElementById('reaction-layer'),
        nextQuestionBtn: document.getElementById('next-question-btn'),
        endGameBtn: document.getElementById('end-game-btn'),
        resetGameBtn: document.getElementById('reset-game-btn'),
        // Finale
        adminPodium: document.getElementById('admin-podium'),
        adminFinaleLeaderboard: document.getElementById('admin-finale-leaderboard'),
        newGameBtn: document.getElementById('new-game-btn'),
        lobbyBackBtn: document.getElementById('lobby-back-btn'),
        // Lightning Round (issue #42 mechanics, #285 auto-trigger). The manual
        // start / start-questions / end / again controls were retired; only
        // the display elements + the recap "Continue" button remain.
        adminLightningProgress: document.getElementById('admin-lightning-progress'),
        adminLightningQuestion: document.getElementById('admin-lightning-question'),
        adminLightningTimer: document.getElementById('admin-lightning-timer'),
        adminLightningSplash: document.getElementById('admin-lightning-splash'),
        adminLightningSplashRules: document.getElementById('admin-lightning-splash-rules'),
        adminLightningQuestionSection: document.getElementById('admin-lightning-question-section'),
        adminLightningRecapLeaderboard: document.getElementById('admin-lightning-recap-leaderboard'),
        adminLightningRecapGrid: document.getElementById('admin-lightning-recap-grid'),
        adminLightningContinueBtn: document.getElementById('admin-lightning-continue-btn'),
        // Admin join modal
        adminJoinModal: document.getElementById('admin-join-modal'),
        adminNameInput: document.getElementById('admin-name-input'),
        adminJoinBtn: document.getElementById('admin-join-btn'),
        adminCancelBtn: document.getElementById('admin-cancel-btn'),
    };

    // ---- Redirect helper ----
    function redirectToPlayer(name) {
        _redirecting = true;
        sessionStorage.setItem('quizify_admin_name', name);
        var url = new URL('/quizify/player', location.href);
        url.searchParams.set('name', name);
        url.searchParams.set('admin', 'true');
        // When the admin already registered as a player on the admin WS
        // (via "Als Spieler beitreten"), the player page must reconnect
        // with the fresh session_token instead of doing a fresh join —
        // otherwise the still-open admin WS holds the player slot and
        // the join fails with "Name bereits vergeben".
        if (_adminJoinedAs === name) {
            url.searchParams.set('reconnect', '1');
        }
        window.location.href = url.toString();
    }

    // ---- View management ----
    function showView(name) {
        if (_redirecting) return;
        Object.values(views).forEach(function (v) { if (v) v.classList.remove('active'); });
        if (views[name]) views[name].classList.add('active');
        // Header reset button: only useful once a game exists. Hide it on
        // the setup screen (nothing to reset) and reveal it everywhere
        // else. handleGameReset returns us to 'setup', re-hiding it.
        if (els.resetGameBtn) {
            els.resetGameBtn.classList.toggle('hidden', name === 'setup');
        }
    }

    // ---- Collapsible sections ----
    function setupCollapsibles() {
        document.addEventListener('click', function (e) {
            var header = e.target.closest('.section-header-collapsible');
            if (!header) return;
            var section = header.closest('.section-collapsible');
            if (section) {
                section.classList.toggle('collapsed');
                header.setAttribute('aria-expanded', String(!section.classList.contains('collapsed')));
            }
        });
    }

    // ---- Chip selectors ----
    function setupChips(container, onSelect) {
        if (!container) return;
        container.addEventListener('click', function (e) {
            var chip = e.target.closest('.chip');
            if (!chip) return;
            container.querySelectorAll('.chip').forEach(function (c) { c.classList.remove('active'); });
            chip.classList.add('active');
            onSelect(chip.dataset.value);
        });
    }

    function setupCategoryChips(container) {
        if (!container) return;
        container.addEventListener('click', function (e) {
            var chip = e.target.closest('.chip');
            if (!chip) return;
            var val = chip.dataset.value;

            if (val === 'mixed') {
                container.querySelectorAll('.chip').forEach(function (c) { c.classList.remove('active'); });
                chip.classList.add('active');
                selectedCategory = 'mixed';
                selectedCategories = [];
            } else {
                var mixedChip = container.querySelector('.chip[data-value="mixed"]');
                if (mixedChip) mixedChip.classList.remove('active');
                chip.classList.toggle('active');
                selectedCategories = Array.from(container.querySelectorAll('.chip.active'))
                    .map(function (c) { return c.dataset.value; });
                if (selectedCategories.length === 0) {
                    if (mixedChip) mixedChip.classList.add('active');
                    selectedCategory = 'mixed';
                } else if (selectedCategories.length === 1) {
                    selectedCategory = selectedCategories[0];
                } else {
                    selectedCategory = 'multi';
                }
            }
            updateCategorySummary();
        });
    }

    // ---- Hero pack picker (variant C) ----
    // The hero chips are a thin proxy over #category-chips, which stays the
    // single source of truth for selection + the start payload. Building from
    // it means we never hand-maintain a second pack list. The featured pack
    // (World Cup / Weltmeisterschaft) is excluded — it has the spotlight card.
    // #335: exclusion is now theme-driven (data-theme="worldcup") instead of a
    // hardcoded slug list, so a World Cup pack in any language (incl. a future
    // Spanish one) is correctly surfaced via the hero feature card, not the
    // grid. Languages without a World Cup pack (e.g. es today) simply have
    // nothing to exclude and the hero feature card hides itself.
    var _FEATURED_PACK_THEME = 'worldcup';

    // SVG line icons + accent tints now live in the shared module
    // (www/js/icons.js, loaded before admin.js) so both admin and player
    // JS reuse one glyph set (issue #212). These thin aliases keep the
    // existing call-sites unchanged.
    var _CATEGORY_TINT = window.QuizifyIcons.CATEGORY_TINT;

    function _categoryIconSvg(theme) {
        return window.QuizifyIcons.icon(theme);
    }

    // Hero category grid: color-tinted SVG tiles (Direction A "Categories-
    // forward"). Built from #category-chips so it stays the single source of
    // truth for selection + the start payload. The featured pack (World Cup /
    // Weltmeisterschaft) is excluded — it has the spotlight card above.
    // #335 (AC2): count the grid-selectable packs (excludes Mixed + the
    // World-Cup hero pack) for the active language. When there is exactly one,
    // "Mixed" is degenerate (it equals that single pack) so we hide the Mixed
    // tile and auto-select the lone pack instead.
    function _gridPackChipsForLang() {
        if (!els.categoryChips) return [];
        var out = [];
        els.categoryChips.querySelectorAll('.chip[data-lang]').forEach(function (c) {
            if (c.dataset.theme === _FEATURED_PACK_THEME) return;
            if (c.dataset.lang === selectedLanguage) out.push(c);
        });
        return out;
    }

    function buildHeroPackChips() {
        if (!els.heroPackChips || !els.categoryChips) return;
        els.heroPackChips.innerHTML = '';
        var singlePack = _gridPackChipsForLang().length === 1;
        els.categoryChips.querySelectorAll('.chip').forEach(function (cat) {
            var value = cat.dataset.value;
            // World Cup packs live on the hero feature card, not the grid.
            if (cat.dataset.theme === _FEATURED_PACK_THEME) return;
            // AC2: with only one pack for this language, the "Mixed" tile is
            // redundant — skip it (the lone pack auto-selects below).
            if (value === 'mixed' && singlePack) return;
            // Language filter: Mixed (no data-lang) plus the active language.
            var lang = cat.dataset.lang;
            if (lang && lang !== selectedLanguage) return;

            var nameEl = cat.querySelector('.pack-card-name');
            var name = nameEl ? nameEl.textContent : value;
            var countEl = cat.querySelector('.pack-card-count');
            var count = countEl ? countEl.textContent : '';
            var theme = cat.dataset.theme || 'mixed';

            var tile = document.createElement('button');
            tile.type = 'button';
            tile.className = 'hero-cat-tile hpt-' + (_CATEGORY_TINT[theme] || 'mix') +
                (cat.classList.contains('active') ? ' active' : '');
            tile.dataset.value = value;
            tile.innerHTML = '<span class="hct-icon">' + _categoryIconSvg(theme) + '</span>' +
                '<span class="hct-name"></span>' +
                '<span class="hct-count"></span>';
            tile.querySelector('.hct-name').textContent = name;
            tile.querySelector('.hct-count').textContent = count;
            tile.addEventListener('click', function () {
                // Proxy to the real category chip so all existing selection
                // logic (mixed/single/multi + summary) runs unchanged.
                var target = els.categoryChips.querySelector('.chip[data-value="' + value + '"]');
                if (target) target.click();
                syncHeroPackChips();
                syncHeroFeatureCardState();
            });
            els.heroPackChips.appendChild(tile);
        });
        // AC2: when the language has a single pack and Mixed is still the
        // active selection, auto-select the lone pack so the (now hidden) Mixed
        // tile isn't the implicit choice. Guard against the World-Cup-only edge
        // by reusing the same grid-pack set.
        if (singlePack && selectedCategory === 'mixed') {
            var lone = _gridPackChipsForLang()[0];
            if (lone) {
                els.categoryChips.querySelectorAll('.chip').forEach(function (c) {
                    c.classList.remove('active');
                });
                lone.classList.add('active');
                selectedCategory = lone.dataset.value;
                selectedCategories = [lone.dataset.value];
                if (typeof updateCategorySummary === 'function') updateCategorySummary();
                syncHeroPackChips();
            }
        }
        syncHeroFeatureCardState();
    }

    function syncHeroPackChips() {
        if (!els.heroPackChips || !els.categoryChips) return;
        els.heroPackChips.querySelectorAll('.hero-cat-tile').forEach(function (tile) {
            var cat = els.categoryChips.querySelector('.chip[data-value="' + tile.dataset.value + '"]');
            tile.classList.toggle('active', !!(cat && cat.classList.contains('active')));
        });
    }

    // #335: derive the World Cup pack chip for the active language from the
    // data-driven grid (any pack with data-theme="worldcup" + matching
    // data-lang) instead of a hardcoded de→weltmeisterschaft / *→world-cup map.
    // Returns null when the active language has no World Cup pack (e.g. es), so
    // callers can hide the static hero feature card cleanly.
    function _worldCupChipForLang() {
        if (!els.categoryChips) return null;
        var chips = els.categoryChips.querySelectorAll('.chip[data-theme="worldcup"]');
        for (var i = 0; i < chips.length; i++) {
            if (chips[i].dataset.lang === selectedLanguage) return chips[i];
        }
        return null;
    }

    // Reflect the World Cup pack's selected state on the featured card (active
    // border + checkmark), mirroring the category chip for the active language.
    // When the active language has no World Cup pack, hide the card entirely so
    // a language like Spanish never shows a non-functional World Cup tile.
    function syncHeroFeatureCardState() {
        if (!els.heroFeatureCard || !els.categoryChips) return;
        var cat = _worldCupChipForLang();
        if (!cat) {
            els.heroFeatureCard.classList.add('hidden');
            els.heroFeatureCard.classList.remove('active');
            els.heroFeatureCard.setAttribute('aria-pressed', 'false');
            return;
        }
        els.heroFeatureCard.classList.remove('hidden');
        var active = cat.classList.contains('active');
        els.heroFeatureCard.classList.toggle('active', active);
        els.heroFeatureCard.setAttribute('aria-pressed', active ? 'true' : 'false');
    }

    function _t(key, params) {
        if (window.QuizifyI18n && typeof window.QuizifyI18n.t === 'function') {
            return window.QuizifyI18n.t(key, params);
        }
        return key;
    }

    /**
     * ``_t`` with a written-out English fallback.
     *
     * ``_t()`` hands back the key itself when no bundle is loaded, so the
     * fallback has to be chosen on that rather than on falsiness — otherwise
     * the host reads "hotSeat.hostSeated" off the screen (#732).
     */
    function _tOr(key, params, fallback) {
        var text = _t(key, params);
        return (text && text !== key) ? text : fallback;
    }

    // ---- Seasonal pack badges (#276) ----
    // Packs may carry a recurring season window (e.g. Christmas). The
    // /api/quizify/packs endpoint resolves which packs are in-season *today*
    // (server-side date math) and returns is_seasonal + season_label per slug.
    // For each in-season pack we badge its picker card with that label. Purely
    // additive: packs that aren't seasonal are untouched, and a failed fetch
    // just means no badges (the picker still works).
    function _applySeasonalBadge(chip, label) {
        if (!chip) return;
        // Idempotent: never stack badges across re-renders / lang toggles.
        var existing = chip.querySelector('.pack-card-season-badge');
        if (existing) existing.remove();
        var badge = document.createElement('span');
        badge.className = 'pack-card-season-badge';
        badge.textContent = label;
        chip.appendChild(badge);
    }

    function applySeasonalBadges() {
        if (!els.categoryChips) return;
        fetch('/api/quizify/packs')
            .then(function (resp) { return resp.ok ? resp.json() : null; })
            .then(function (packs) {
                if (!packs) return;
                Object.keys(packs).forEach(function (slug) {
                    var meta = packs[slug];
                    if (!meta || !meta.is_seasonal || !meta.season_label) return;
                    var chip = els.categoryChips.querySelector(
                        '.chip[data-value="' + slug + '"]');
                    _applySeasonalBadge(chip, meta.season_label);
                });
            })
            .catch(function (e) {
                console.warn('[quizify] applySeasonalBadges failed:', e);
            });
    }

    // ---- Phase 2: Pack-UI (Featured-Spotlight + Theme-Tabs) ----
    // Always-on per the approved mockup
    // (~/.gstack/designs/pack-ui-2026-05-27/phase2-themes.html). The
    // spotlight + tabs render regardless of pack count; Markus made the
    // call on 2026-05-27 to drop the threshold gating after seeing the
    // first cut with 3 packs and asking for "genauso wie in den Mockups".

    // Fallback Featured Pack — used while the /api/quizify/featured-pack
    // fetch is in flight, and as a hard fallback if the endpoint errors.
    // Real selection comes from the backend (most-played on even days,
    // most-difficult on odd days). Markus 2026-05-29 (msg 283).
    // Count is NOT hardcoded — it's read from the matching pack card's
    // data-count at paint time (see _featuredFallback) so the fallback can
    // never drift from the real pack size the way a literal "47" did.
    var FEATURED_PACK = {
        de: { value: 'geographie',  title: '🌍 Geographie',  unit: 'Fragen',    sub: 'Familienfreundlich' },
        en: { value: 'geography',   title: '🌍 Geography',   unit: 'questions', sub: 'Family-friendly' },
    };

    // Build the fallback spotlight object, pulling the live question count
    // from the matching pack card in the DOM (same source the grid shows).
    function _featuredFallback(lang) {
        var base = FEATURED_PACK[lang];
        if (!base) return null;
        var meta = base.sub;
        if (els.categoryChips) {
            var chip = els.categoryChips.querySelector('.chip[data-value="' + base.value + '"]');
            var count = chip ? parseInt(chip.dataset.count || '0', 10) : 0;
            if (count > 0) meta = count + ' ' + base.unit + ' · ' + base.sub;
        }
        return { value: base.value, title: base.title, meta: meta };
    }

    // Cache resolved featured packs by language so a quick lang-toggle
    // doesn't double-fetch. Cleared on a real page reload.
    var _featuredCache = {};

    // #335: a language can have zero featured-eligible packs (e.g. a brand-new
    // language with a single pack, or one where the backend returned {}). In
    // that case the spotlight must hide cleanly rather than show stale or
    // wrong-language content. _hideSpotlight collapses it out of layout; any
    // valid paint re-shows it.
    function _hideSpotlight() {
        if (!els.featuredSpotlight) return;
        els.featuredSpotlight.classList.add('hidden');
        els.featuredSpotlight.dataset.value = '';
    }

    function _paintFeatured(lang, data) {
        if (!els.featuredSpotlight) return;
        if (!data || !data.value || !data.title) { _hideSpotlight(); return; }
        els.featuredSpotlight.classList.remove('hidden');
        if (els.spotlightTitle) els.spotlightTitle.textContent = data.title;
        if (els.spotlightMeta)  els.spotlightMeta.textContent  = data.meta || '';
        // Remember selection for the spotlight-click handler.
        els.featuredSpotlight.dataset.value = data.value;
    }

    function _fetchFeatured(lang) {
        if (_featuredCache[lang]) {
            _paintFeatured(lang, _featuredCache[lang]);
            return;
        }
        fetch('/api/quizify/featured-pack?lang=' + encodeURIComponent(lang))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (data && data.value && data.title) {
                    _featuredCache[lang] = data;
                    _paintFeatured(lang, data);
                } else {
                    // Backend has nothing to feature for this language — only
                    // hide if the synchronous fallback didn't already paint a
                    // valid spotlight (i.e. dataset.value is empty).
                    if (!els.featuredSpotlight ||
                        !els.featuredSpotlight.dataset.value) {
                        _hideSpotlight();
                    }
                }
            })
            .catch(function () { /* paint already showed fallback (or hid) */ });
    }

    function updatePackUIScaling(lang) {
        // Paint fallback synchronously so the spotlight is never blank for a
        // language with a known fallback (de/en). For languages without one
        // (#335, e.g. es), hide the spotlight up-front so a prior language's
        // content never lingers while the live fetch is in flight — the fetch
        // either re-shows it with a real pack or leaves it hidden.
        var fallback = _featuredFallback(lang);
        if (els.featuredSpotlight) {
            if (fallback) {
                _paintFeatured(lang, fallback);
            } else if (!_featuredCache[lang]) {
                _hideSpotlight();
            }
        }
        _fetchFeatured(lang);
        // Re-apply the "all" theme filter so a language switch clears any
        // leftover hidden-by-theme state on chips that just became
        // visible for the new language.
        applyThemeFilter('all');
    }

    function applyThemeFilter(theme) {
        if (!els.categoryChips) return;
        if (els.themeTabs) {
            els.themeTabs.querySelectorAll('.theme-tab').forEach(function (t) {
                var isActive = t.dataset.theme === theme;
                t.classList.toggle('active', isActive);
                // #463: these filter buttons expose aria-pressed (role="group",
                // not a tablist) so screen readers announce the active filter.
                t.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });
        }
        els.categoryChips.querySelectorAll('.chip[data-theme]').forEach(function (chip) {
            var matches = (theme === 'all') || (chip.dataset.theme === theme);
            chip.classList.toggle('hidden-by-theme', !matches);
        });
    }

    function setupThemeTabs() {
        if (!els.themeTabs) return;
        els.themeTabs.addEventListener('click', function (e) {
            var tab = e.target.closest('.theme-tab');
            if (!tab) return;
            applyThemeFilter(tab.dataset.theme || 'all');
        });
    }

    // P1 of #212: replace emoji UI-icons with the shared SVG line-icon set
    // (Option 2 "Rounded Duotone"). Two surfaces:
    //  - theme filter tabs  → fill the empty .qz-icon span per data-theme
    //  - detail-view pack cards → swap the emoji glyph in .pack-card-icon
    // Selection wiring is untouched — we only paint icon markup; the chip
    // data-value/data-theme/data-lang/.active contract is preserved.
    function paintP1Icons() {
        var Icons = window.QuizifyIcons;
        if (!Icons) return;
        // Theme filter tabs: each themed tab has an empty .qz-icon span.
        if (els.themeTabs) {
            els.themeTabs.querySelectorAll('.theme-tab[data-theme]').forEach(function (tab) {
                var theme = tab.dataset.theme;
                if (!theme || theme === 'all') return;
                var slot = tab.querySelector('.qz-icon');
                if (slot) slot.innerHTML = Icons.icon(theme);
            });
        }
        // Detail-view pack cards: swap emoji for the themed SVG. Mixed (no
        // data-theme) falls back to the mixed glyph. The duotone backing
        // disc + tint come from CSS keyed off the qz-icon--<tint> class.
        if (els.categoryChips) {
            els.categoryChips.querySelectorAll('.pack-card-icon').forEach(function (slot) {
                var chip = slot.closest('.chip');
                var theme = (chip && chip.dataset.theme) || 'mixed';
                slot.classList.add('qz-icon', 'qz-icon--' + Icons.tint(theme));
                slot.innerHTML = Icons.icon(theme);
            });
        }
    }

    // P4 of #225: paint the static UI emoji-icon spans (admin lobby
    // cast/join/start) with the shared Rounded Duotone set. Any element
    // carrying both `.qz-icon` and `data-ui-icon="<name>"` gets its emoji
    // swapped for the matching window.QuizifyIcons.uiIcon() <svg>. Existing
    // layout classes (.btn-icon etc.) + the qz-icon--<tint> class stay put.
    function paintUiIcons() {
        var Icons = window.QuizifyIcons;
        if (!Icons || !Icons.uiIcon) return;
        document.querySelectorAll('.qz-icon[data-ui-icon]').forEach(function (slot) {
            var svg = Icons.uiIcon(slot.getAttribute('data-ui-icon'));
            if (svg) slot.innerHTML = svg;
        });
    }

    function setupFeaturedSpotlight() {
        if (!els.featuredSpotlight || !els.categoryChips) return;
        els.featuredSpotlight.addEventListener('click', function () {
            var lang = (typeof selectedLanguage === 'string') ? selectedLanguage : 'de';
            // Prefer the dynamically resolved spotlight pack (data-value
            // set by _paintFeatured); fall back to the static map if the
            // fetch hadn't landed yet.
            var packValue = els.featuredSpotlight.dataset.value
                || (FEATURED_PACK[lang] && FEATURED_PACK[lang].value);
            if (!packValue) return;
            // Featured click = pick exactly this pack. Clears Mixed + any
            // multi-select from prior interactions.
            els.categoryChips.querySelectorAll('.chip').forEach(function (c) {
                c.classList.remove('active');
            });
            var target = els.categoryChips.querySelector('.chip[data-value="' + packValue + '"]');
            if (target) {
                target.classList.add('active');
                selectedCategory = packValue;
                selectedCategories = [packValue];
                updateCategorySummary();
            }
        });
    }

    function updateCategorySummary() {
        // Keep the ready-screen topics line in sync with every category
        // change (chips, spotlight click, language switch).
        updateHeroSummary();
        if (!els.categorySummary) return;
        var countEl = document.getElementById('question-count-summary');
        if (selectedCategory === 'mixed') {
            els.categorySummary.textContent = _t('admin.categoryMixed');
            if (countEl) countEl.classList.add('hidden');
        } else if (selectedCategory === 'multi') {
            els.categorySummary.textContent = _t('admin.categoriesCountPlural', { count: selectedCategories.length });
            if (countEl) {
                var total = 0;
                selectedCategories.forEach(function (val) {
                    var chip = els.categoryChips.querySelector('.chip[data-value="' + val + '"]');
                    if (chip) total += parseInt(chip.dataset.count || '0', 10);
                });
                countEl.textContent = _t('admin.packsAndQuestions', {
                    packs: selectedCategories.length,
                    questions: total,
                });
                countEl.classList.remove('hidden');
            }
        } else {
            var activeChip = els.categoryChips.querySelector('.chip.active');
            els.categorySummary.textContent = activeChip ? activeChip.textContent : selectedCategory;
            if (countEl) {
                var count = activeChip ? parseInt(activeChip.dataset.count || '0', 10) : 0;
                if (count > 0) {
                    countEl.textContent = _t('admin.questionsOnly', {
                        questions: count,
                    });
                    countEl.classList.remove('hidden');
                } else {
                    countEl.classList.add('hidden');
                }
            }
        }
    }

    function updateSettingsSummary() {
        if (els.gameSettingsSummary) {
            var diffChip = els.difficultyChips ? els.difficultyChips.querySelector('.chip.active') : null;
            var diffLabel = diffChip ? diffChip.textContent : _t('difficulties.medium');
            els.gameSettingsSummary.textContent = _t('admin.settingsSummary', {
                difficulty: diffLabel,
                rounds: selectedRounds,
                roundsUnit: _t('admin.summaryRoundsUnit'),
                timer: selectedTimer,
            });
        }
        updateHeroSummary();
        markActivePreset();
    }

    // Hero summary line: "Klassiker · 10 Runden · Mittel · 30 s · 🇩🇪"
    // Mirrors the preset name when one matches the current settings,
    // otherwise just lists the values. Drives the variant-A hero state.
    function updateHeroSummary() {
        var heroEl = document.getElementById('setup-summary');
        if (!heroEl) return;
        var preset = _matchingPreset();
        var diffLabel = (els.difficultyChips && els.difficultyChips.querySelector('.chip.active'))
            ? els.difficultyChips.querySelector('.chip.active').textContent.trim()
            : _t('difficulties.medium');
        // Map, not a ternary (#625): "English or else German" flew a German
        // flag over every Spanish game. An unknown code falls back to the
        // globe rather than to some language's flag — wrong-but-plausible is
        // worse here than visibly neutral.
        var LANG_FLAGS = { en: '🇬🇧', de: '🇩🇪', es: '🇪🇸' };
        var langFlag = LANG_FLAGS[selectedLanguage] || '🌐';
        var parts = [];
        if (preset) parts.push(preset.label);
        parts.push(selectedRounds + ' ' + _t('admin.summaryRoundsUnit'));
        parts.push(diffLabel);
        parts.push(selectedTimer + ' s');
        parts.push(langFlag);
        heroEl.textContent = parts.join(' · ');

        // Topics line below the summary row: list the manually-chosen packs
        // so the ready screen reflects a custom topic selection. Hidden for
        // 'mixed' (no specific topics) since that needs no callout.
        var topicsEl = document.getElementById('setup-topics');
        if (topicsEl) {
            var names = _selectedCategoryNames();
            if (names.length === 0) {
                topicsEl.classList.add('hidden');
                topicsEl.textContent = '';
            } else {
                topicsEl.textContent = '🎯 ' + names.join(' · ');
                topicsEl.classList.remove('hidden');
            }
        }
    }

    // Display names of the manually-selected topic packs, read from the
    // pack cards in the DOM. Returns [] for the 'mixed' (all-packs) case.
    function _selectedCategoryNames() {
        if (selectedCategory === 'mixed' || !els.categoryChips) return [];
        var values = selectedCategory === 'multi'
            ? selectedCategories
            : [selectedCategory];
        var names = [];
        values.forEach(function (val) {
            var chip = els.categoryChips.querySelector('.chip[data-value="' + val + '"]');
            if (!chip) return;
            var nameEl = chip.querySelector('.pack-card-name');
            names.push(nameEl ? nameEl.textContent.trim() : val);
        });
        return names;
    }

    // `lightning` mirrors data-lightning on the cards in admin.html (1/0) and
    // arms or disarms the auto Lightning Round (#285) for the bundle.
    var _PRESETS = [
        { id: 'schnellrunde', rounds: 5,  difficulty: 'easy',   timer: 20, lightning: true,  hotSeat: true,  powerups: true,  wager: true,  labelKey: 'setup.preset.fastName'     },
        { id: 'klassiker',    rounds: 10, difficulty: 'medium', timer: 30, lightning: true,  hotSeat: true,  powerups: true,  wager: true,  labelKey: 'setup.preset.classicName'  },
        // #506: the long-timer bundle for hosts playing with small kids. Its
        // timer must stay one of the #timer-chips values — _applyPreset calls
        // _activateChip, which silently highlights nothing otherwise.
        // #513: Lightning off — 5 questions at 15 s each would undo the long
        // timer this preset exists for, right in the middle of the game.
        // #616: Hot Seat off for the same family of reasons as Lightning —
        // the auction is built on losing points, which is the mechanic
        // children like least about the game.
        // #742: power-ups and the final-round wager off, which is the same
        // call a third and fourth time. Steal and Freeze take points off
        // another child, and the last question staked "no answer costs you the
        // stake" against a score a seven-year-old spent the evening building.
        { id: 'kinder',       rounds: 5,  difficulty: 'easy',   timer: 180, lightning: false, hotSeat: false, powerups: false, wager: false, labelKey: 'setup.preset.kidsName'   },
        { id: 'marathon',     rounds: 20, difficulty: 'hard',   timer: 45, lightning: true,  hotSeat: true,  powerups: true,  wager: true,  labelKey: 'setup.preset.marathonName' },
    ];

    // #433: the host's own saved presets, loaded from the server so a preset
    // saved on the tablet exists on the phone too.
    var _customPresets = [];
    var _presetsLoaded = false;

    function _sameBundle(p) {
        // Lightning is part of the bundle (#513) — flipping the toggle by
        // hand makes the run "Eigene" again, same as picking own topics.
        return p.rounds === selectedRounds && p.difficulty === selectedDifficulty
            && p.timer === selectedTimer && p.lightning === selectedLightning
            // #616: the Hot Seat rides the bundle for the same reason — a kids
            // run with the auction switched back on is no longer "Mit Kindern".
            && p.hotSeat === selectedHotSeat
            // #742: and neither is one with Steal and the final bet back on.
            && p.powerups === selectedPowerups
            && p.wager === selectedWager;
    }

    function _samePacks(packs) {
        var mine = selectedCategories || [];
        var theirs = packs || [];
        if (mine.length !== theirs.length) return false;
        var a = mine.slice().sort(), b = theirs.slice().sort();
        for (var i = 0; i < a.length; i++) { if (a[i] !== b[i]) return false; }
        return true;
    }

    function _matchingPreset() {
        // A built-in preset (Schnellrunde / Klassiker / Marathon) is a full
        // bundle that implies mixed topics. Once the host picks specific
        // topics it is no longer that preset — it's custom (Eigene). So a
        // non-mixed category selection never matches a BUILT-IN preset, which
        // stops the ready screen from mislabelling a custom topic run as
        // "Klassiker".
        if (selectedCategory === 'mixed') {
            for (var i = 0; i < _PRESETS.length; i++) {
                var p = _PRESETS[i];
                if (_sameBundle(p)) return { id: p.id, label: _t(p.labelKey) };
            }
        }
        // #433: saved presets CAN express a pack choice, so they are matched
        // on packs as well — checked after the built-ins so a saved preset
        // that happens to equal Klassiker still reads as Klassiker.
        for (var j = 0; j < _customPresets.length; j++) {
            var c = _customPresets[j];
            if (_sameBundle(c) && _samePacks(c.packs)) {
                return { id: c.id, label: c.name, custom: true };
            }
        }
        return null;
    }

    function markActivePreset() {
        var match = _matchingPreset();
        document.querySelectorAll('.preset-card[data-preset]').forEach(function (card) {
            var id = card.getAttribute('data-preset');
            // "Eigene" is active when no built-in preset matches — including
            // when a SAVED preset does (#433). A saved preset has no card of
            // its own, so without this branch the whole card row ends up with
            // nothing highlighted, which reads as "no mode selected".
            var active = (match && !match.custom)
                ? (id === match.id)
                : (id === 'eigene');
            card.classList.toggle('is-active', active);
        });
        // The chips answer the same question as the cards, so they have to be
        // repainted from the same place. Rendering them only on load/save/apply
        // left a chip lit after the host changed a setting afterwards.
        _renderCustomPresets();
    }

    // ── Saved presets (#433) ──────────────────────────────────────────
    // Server-stored, so a preset saved on the living-room tablet is there on
    // the host's phone too. The four built-in cards below are untouched by
    // all of this; this only adds a chip row above them.

    // #608: one place that turns "I hold the admin token" into a request.
    //
    // The token used to ride along as ?token=, which put a replayable
    // full-control credential into aiohttp's access log and every reverse proxy
    // in front of HA — the leak #359 removed. The rule was even written down,
    // three lines above one of the four sites that broke it, which is why this
    // is now a function rather than a comment.
    function _adminFetch(url, opts) {
        var tok = QuizifyUtils.readAdminToken();
        var init = opts || {};
        init.headers = init.headers || {};
        if (tok) init.headers['X-Quizify-Token'] = tok;
        return fetch(url, init);
    }

    function _presetFetch(opts) {
        var init = opts || {};
        return _adminFetch('/api/quizify/presets' + (init._q || ''), init);
    }

    function _loadCustomPresets() {
        return _presetFetch({ method: 'GET' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                _customPresets = (data && data.presets) || [];
                _renderCustomPresets();
            })
            .catch(function () { /* no presets is a fine steady state */ });
    }

    function _renderCustomPresets() {
        var wrap = document.getElementById('my-presets');
        var row = document.getElementById('my-presets-row');
        if (!wrap || !row) return;

        row.innerHTML = '';
        var match = _matchingPreset();

        _customPresets.forEach(function (p) {
            var chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'preset-chip'
                + (match && match.custom && match.id === p.id ? ' is-active' : '');
            chip.setAttribute('data-preset-id', p.id);

            var label = document.createElement('span');
            label.textContent = p.name;
            chip.appendChild(label);

            var del = document.createElement('button');
            del.type = 'button';
            del.className = 'preset-chip-remove';
            del.textContent = '×';
            del.setAttribute('aria-label', _t('setup.deletePreset') + ' ' + p.name);
            del.addEventListener('click', function (ev) {
                ev.stopPropagation();   // deleting must never also select
                _deleteCustomPreset(p, del);
            });
            chip.appendChild(del);

            chip.addEventListener('click', function () { _applyCustomPreset(p); });
            row.appendChild(chip);
        });

        var add = document.createElement('button');
        add.type = 'button';
        add.className = 'preset-chip preset-chip--add';
        add.textContent = '+ ' + _t('setup.savePreset');
        // Pass the button, not the event: addEventListener hands the handler an
        // Event, and openConfirmModal stores its argument to restore focus on
        // close (#479). An Event there has no .focus() and the restore silently
        // does nothing — the keyboard user lands back at the top of the page.
        add.addEventListener('click', function () { _saveCurrentPreset(add); });
        row.appendChild(add);

        // Hidden entirely while nothing is saved: the label plus a lone
        // "save" chip would be furniture explaining nothing. The save chip
        // still needs a home, so the row shows as soon as the host has
        // anything — and before that it lives in the Eigene panel's flow.
        wrap.hidden = _customPresets.length === 0;
    }

    function _applyCustomPreset(p) {
        // The built-in presets above use hotSeat; a preset that came back from
        // the server carries hot_seat, because that is the wire name the store
        // persists. Read both rather than renaming one of them: the JS side
        // stays camelCase and the payload stays snake_case, and the boundary
        // is the one place that has to know.
        var hotSeat = p.hotSeat != null ? p.hotSeat : p.hot_seat;
        // #742: powerups/wager need no such translation — the JS name and the
        // wire name are already the same word.
        _applyPreset(
            p.rounds, p.difficulty, p.timer, p.lightning, hotSeat,
            p.powerups, p.wager
        );
        _applyPacks(p.packs || []);
        // markActivePreset repaints the chips too, so no separate call here.
        markActivePreset();
    }

    function _applyPacks(packs) {
        var container = document.getElementById('category-chips');
        if (!container) return;
        var wanted = packs.slice();
        container.querySelectorAll('.chip').forEach(function (c) {
            var v = c.dataset.value;
            c.classList.toggle('active',
                wanted.length ? wanted.indexOf(v) !== -1 : v === 'mixed');
        });
        selectedCategories = wanted;
        selectedCategory = wanted.length === 0 ? 'mixed'
            : (wanted.length === 1 ? wanted[0] : 'multi');
        if (typeof updateCategorySummary === 'function') updateCategorySummary();
    }

    function _saveCurrentPreset(triggerEl) {
        // #626: themed modal instead of window.prompt. The dialog stays open on
        // a server refusal so the typed name survives — an alert() throws it
        // away and the host retypes from memory.
        var modal = document.getElementById('save-preset-modal');
        var input = document.getElementById('save-preset-input');
        var errorEl = document.getElementById('save-preset-error');
        if (!modal || !input) {
            // Markup drift must not cost the host the feature entirely.
            var fallbackName = window.prompt(_t('setup.savePresetPrompt'));
            if (fallbackName === null) return;
            fallbackName = fallbackName.trim();
            if (fallbackName) _postPreset(fallbackName, null, null);
            return;
        }

        input.value = '';
        if (errorEl) {
            errorEl.textContent = '';
            errorEl.classList.add('hidden');
        }

        var confirmBtn = document.getElementById('save-preset-confirm-btn');
        if (confirmBtn && !confirmBtn._presetWired) {
            confirmBtn._presetWired = true;
            confirmBtn.addEventListener('click', function () {
                var name = (input.value || '').trim();
                if (!name) {
                    input.focus();
                    return;
                }
                _postPreset(name, errorEl, 'save-preset-modal');
            });
        }
        if (!input._presetWired) {
            input._presetWired = true;
            // Enter submits: this dialog has one field and one obvious action.
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && confirmBtn) {
                    e.preventDefault();
                    confirmBtn.click();
                }
            });
        }

        openConfirmModal('save-preset-modal', 'save-preset-cancel-btn', triggerEl);
        setTimeout(function () { input.focus(); }, 0);
    }

    function _postPreset(name, errorEl, modalId) {
        _presetFetch({
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                rounds: selectedRounds,
                difficulty: selectedDifficulty,
                timer: selectedTimer,
                lightning: selectedLightning,
                hot_seat: selectedHotSeat,
                powerups: selectedPowerups,
                wager: selectedWager,
                category: selectedCategory,
                packs: selectedCategories || []
            })
        })
            .then(function (r) {
                return r.json().then(function (body) {
                    // The server's message is written for a person ("at most
                    // 20 presets can be saved"), so show it rather than a
                    // generic failure the host has to guess about.
                    if (!r.ok) throw new Error(body.error || 'save failed');
                    return body;
                });
            })
            .then(function (body) {
                if (modalId) closeConfirmModal(modalId);
                return body;
            })
            .then(_loadCustomPresets)
            .catch(function (err) {
                // Stay open, show why, keep the name. Falls back to the toast
                // when the modal is not the surface in play.
                if (errorEl) {
                    errorEl.textContent = err.message;
                    errorEl.classList.remove('hidden');
                } else {
                    showErrorToast(err.message);
                }
            });
    }

    function _deleteCustomPreset(p, triggerEl) {
        // #626: the themed danger modal, the same one kicks have used since
        // #480. The name goes in the sentence — "delete preset?" alone makes
        // the host guess which one they tapped.
        var modal = document.getElementById('delete-preset-modal');
        var textEl = document.getElementById('delete-preset-modal-text');
        var confirmBtn = document.getElementById('delete-preset-confirm-btn');

        function doDelete() {
            _presetFetch({ method: 'DELETE', _q: '?id=' + encodeURIComponent(p.id) })
                .then(_loadCustomPresets)
                .catch(function () { /* stays on screen; next load reconciles */ });
        }

        if (!modal || !confirmBtn) {
            if (window.confirm(_t('setup.deletePresetConfirm') + ' „' + p.name + '"')) {
                doDelete();
            }
            return;
        }

        if (textEl) {
            var tmpl = _t('setup.deletePresetBody') || '"{name}" will be removed for good.';
            textEl.textContent = tmpl.replace('{name}', p.name);
        }
        // Re-wired per open: the closure carries THIS preset, and a stale
        // listener from a previous open would delete the wrong one.
        confirmBtn.onclick = function () {
            closeConfirmModal('delete-preset-modal');
            doDelete();
        };
        openConfirmModal('delete-preset-modal', 'delete-preset-cancel-btn', triggerEl);
    }

    // Apply preset: write to the active-chip state AND to the typed vars
    // so the existing chip group serialization works unchanged.
    function _applyPreset(rounds, difficulty, timer, lightning, hotSeat, powerups, wager) {
        if (rounds != null) {
            selectedRounds = rounds;
            _activateChip(els.roundsChips, String(rounds));
        }
        if (difficulty != null) {
            selectedDifficulty = difficulty;
            _activateChip(els.difficultyChips, difficulty);
        }
        if (timer != null) {
            selectedTimer = timer;
            _activateChip(els.timerChips, String(timer));
        }
        // #513: the Lightning Round rides the bundle. Write the checkbox too,
        // not just the variable — _buildStartGamePayload reads the DOM first,
        // and the host opening "Eigene" must see the state that will ship.
        if (lightning != null) {
            selectedLightning = lightning;
            var lightningEl = document.getElementById('lightning-enabled-toggle');
            if (lightningEl) lightningEl.checked = lightning;
        }
        // #616: same treatment — write the checkbox, not only the variable,
        // because _buildStartGamePayload reads the DOM first.
        if (hotSeat != null) {
            selectedHotSeat = hotSeat;
            var hotSeatEl = document.getElementById('hot-seat-enabled-toggle');
            if (hotSeatEl) hotSeatEl.checked = hotSeat;
        }
        // #742: the same again for both new toggles — the checkbox is what
        // _buildStartGamePayload reads, so writing only the variable would
        // ship the DOM's stale value.
        if (powerups != null) {
            selectedPowerups = powerups;
            var powerupsEl = document.getElementById('powerups-enabled-toggle');
            if (powerupsEl) powerupsEl.checked = powerups;
        }
        if (wager != null) {
            selectedWager = wager;
            var wagerEl = document.getElementById('wager-enabled-toggle');
            if (wagerEl) wagerEl.checked = wager;
        }
        // #851: a preset is the other way the difficulty changes, and the one
        // the live test used — "Schnellrunde" sets easy without the chip
        // handler ever running.
        _pushDifficulty();
        updateSettingsSummary();
    }

    function _activateChip(container, value) {
        if (!container) return;
        container.querySelectorAll('.chip').forEach(function (c) {
            c.classList.toggle('active', c.dataset.value === value);
        });
    }

    // Hero ↔ detail toggle.
    function _setSetupMode(mode) {
        var screen = document.getElementById('setup-screen');
        if (screen) screen.setAttribute('data-mode', mode === 'detail' ? 'detail' : 'hero');
    }

    function _wireSetupHeroAndPresets() {
        var tweakBtn = document.getElementById('setup-tweak-btn');
        var backBtn  = document.getElementById('setup-back-btn');
        var applyBtn = document.getElementById('setup-apply-btn');
        var custom   = document.getElementById('setup-custom');

        if (tweakBtn) tweakBtn.addEventListener('click', function () {
            _setSetupMode('detail');
            markActivePreset();
        });
        if (backBtn)  backBtn.addEventListener('click', function () { _setSetupMode('hero'); });
        if (applyBtn) applyBtn.addEventListener('click', function () { _setSetupMode('hero'); });

        document.querySelectorAll('.preset-card[data-preset]').forEach(function (card) {
            card.addEventListener('click', function () {
                var id = card.getAttribute('data-preset');
                if (id === 'eigene') {
                    if (custom) custom.classList.remove('hidden');
                    markActivePreset();
                    return;
                }
                if (custom) custom.classList.add('hidden');
                var rounds = parseInt(card.getAttribute('data-rounds'), 10);
                var difficulty = card.getAttribute('data-difficulty');
                var timer = parseInt(card.getAttribute('data-timer'), 10);
                var lightningAttr = card.getAttribute('data-lightning');
                var lightning = lightningAttr == null ? null : lightningAttr === '1';
                var hotSeatAttr = card.getAttribute('data-hot-seat');
                var hotSeat = hotSeatAttr == null ? null : hotSeatAttr === '1';
                var powerupsAttr = card.getAttribute('data-powerups');
                var powerups = powerupsAttr == null ? null : powerupsAttr === '1';
                var wagerAttr = card.getAttribute('data-wager');
                var wager = wagerAttr == null ? null : wagerAttr === '1';
                _applyPreset(
                    rounds, difficulty, timer, lightning, hotSeat, powerups, wager
                );
            });
        });
    }
    _wireSetupHeroAndPresets();
    // Initial hero summary paint
    updateHeroSummary();
    markActivePreset();

    setupCategoryChips(els.categoryChips);
    setupThemeTabs();
    paintP1Icons();
    paintUiIcons();
    setupFeaturedSpotlight();
    // Initial scaling pass — paints spotlight/tabs once we know the
    // visible-pack-count for the default language.
    updatePackUIScaling(typeof selectedLanguage === 'string' ? selectedLanguage : 'de');
    buildHeroPackChips();
    setupChips(els.difficultyChips, function (v) {
        selectedDifficulty = v;
        // Carry the pick to the game right away so the lobby the guests are
        // sitting in re-renders, instead of showing the last game's until
        // start_game (#851).
        _pushDifficulty();
        updateSettingsSummary();
    });
    setupChips(els.roundsChips, function (v) {
        selectedRounds = parseInt(v, 10);
        updateSettingsSummary();
    });
    setupChips(els.timerChips, function (v) {
        selectedTimer = parseInt(v, 10);
        updateSettingsSummary();
    });
    // Lightning Round toggle (#285) — keep selectedLightning in sync with the
    // checkbox so the start payload reflects the host's choice.
    var lightningToggle = document.getElementById('lightning-enabled-toggle');
    if (lightningToggle) {
        selectedLightning = !!lightningToggle.checked;
        on(lightningToggle, 'change', function () {
            selectedLightning = !!lightningToggle.checked;
            // #513: Lightning is part of a preset bundle now, so flipping it
            // by hand has to re-run the match — otherwise a kids run with
            // Lightning switched back on still reads "Mit Kindern".
            updateSettingsSummary();
        });
    }
    // Hot Seat auction toggle (#616) — same sync, same bundle-rematch.
    var hotSeatToggle = document.getElementById('hot-seat-enabled-toggle');
    if (hotSeatToggle) {
        selectedHotSeat = !!hotSeatToggle.checked;
        on(hotSeatToggle, 'change', function () {
            selectedHotSeat = !!hotSeatToggle.checked;
            updateSettingsSummary();
        });
    }
    // Power-ups and the final-round wager (#742) — same sync, same
    // bundle-rematch as the two toggles above.
    var powerupsToggle = document.getElementById('powerups-enabled-toggle');
    if (powerupsToggle) {
        selectedPowerups = !!powerupsToggle.checked;
        on(powerupsToggle, 'change', function () {
            selectedPowerups = !!powerupsToggle.checked;
            updateSettingsSummary();
        });
    }
    var wagerToggle = document.getElementById('wager-enabled-toggle');
    if (wagerToggle) {
        selectedWager = !!wagerToggle.checked;
        on(wagerToggle, 'change', function () {
            selectedWager = !!wagerToggle.checked;
            updateSettingsSummary();
        });
    }
    // TTS narration toggles (#281). Master switch defaults OFF; the per-event
    // toggles default ON. Persisted across reloads in localStorage so the host
    // keeps their preference. The values ride start_game (_readTtsConfig).
    // NB: these constants must be assigned BEFORE _initTtsToggles() runs — the
    // init call executes here at top-level, ahead of the helper block below, so
    // a later `var TTS_DEFAULTS = …` would still read as undefined here (#281).
    var TTS_STORAGE_KEY = 'quizify_tts';
    var TTS_DEFAULTS = {
        enabled: false,
        announce_question: true,
        announce_options: true,
        announce_reveal: true,
        announce_standings: true,
        announce_join: true,
        announce_countdown: true,
        announce_milestone: true,
        // Per-game entity overrides (#281). Empty string → fall back to the
        // integration-options default entities on the server.
        tts_entity: '',
        media_player: '',
    };
    var _ttsEls = {};
    // Guard for the #281 tts-entities fetch: the endpoint is admin-token gated
    // (#356), but the panel first loads at page-init before the admin session
    // token arrives over the WebSocket — that first fetch 401s and the
    // dropdowns fall back to "None found". We refetch once the token lands
    // (handleGameState) and flip this flag on the first successful load so we
    // never refetch again.
    var _ttsEntitiesLoaded = false;
    _initTtsToggles();

    // House Plays Along (#494), Variant D "Presets". Master switch defaults OFF
    // (silent until the host opts in); the ten per-effect toggles default to the
    // "game_show" preset. Persisted in localStorage, pushed to the server on
    // every change + on ws.onopen (configure_house), and it also rides
    // start_game (_readHouseConfig → _buildStartGamePayload → house).
    // NB (same trap as TTS above): these constants must be assigned BEFORE
    // _initHouseToggles() runs — the init call executes here at top-level, ahead
    // of the helper block further down, so a later `var HOUSE_DEFAULTS = …` would
    // hoist but still read as undefined at this point.
    var HOUSE_STORAGE_KEY = 'quizify_house';
    // The ten per-effect booleans, in DOM order. `preset` is frontend-only —
    // the backend only ever receives resolved booleans.
    var HOUSE_EFFECT_KEYS = [
        'light_question', 'light_countdown', 'light_reveal', 'light_streak',
        'light_winner', 'winner_scene',
        'sfx_correct', 'sfx_wrong', 'sfx_streak', 'sfx_winner',
    ];
    // preset → the exact effect-toggle set it writes. `events_only` turns every
    // effect off on purpose: the quizify_* HA events still fire, so the host can
    // drive their own automations.
    var HOUSE_PRESETS = {
        cozy_glow: {
            light_question: true, light_countdown: false, light_reveal: true,
            light_streak: false, light_winner: true, winner_scene: true,
            sfx_correct: false, sfx_wrong: false, sfx_streak: false, sfx_winner: false,
        },
        game_show: {
            light_question: true, light_countdown: true, light_reveal: true,
            light_streak: true, light_winner: true, winner_scene: true,
            sfx_correct: true, sfx_wrong: true, sfx_streak: true, sfx_winner: true,
        },
        events_only: {
            light_question: false, light_countdown: false, light_reveal: false,
            light_streak: false, light_winner: false, winner_scene: false,
            sfx_correct: false, sfx_wrong: false, sfx_streak: false, sfx_winner: false,
        },
    };
    // preset id → the i18n key of the one-line hint under the segmented control.
    var HOUSE_PRESET_HINTS = {
        cozy_glow: 'setup.house.hintCozy',
        game_show: 'setup.house.hintGameShow',
        events_only: 'setup.house.hintEvents',
        custom: 'setup.house.hintCustom',
    };
    var HOUSE_DEFAULTS = {
        enabled: false,
        preset: 'game_show',
        light_question: true, light_countdown: true, light_reveal: true,
        light_streak: true, light_winner: true, winner_scene: true,
        sfx_correct: true, sfx_wrong: true, sfx_streak: true, sfx_winner: true,
        // Per-game entity overrides. [] / '' → fall back to the config-entry
        // values on the server; never send a placeholder string.
        light_entities: [],
        media_player: '',
        winner_scene_entity: '',
    };
    var _houseEls = {};
    // Currently selected preset ('cozy_glow' | 'game_show' | 'events_only' |
    // 'custom'). 'custom' = no segment highlighted.
    var _housePreset = HOUSE_DEFAULTS.preset;
    // Last known-good config. The entity pickers populate asynchronously (the
    // lists arrive on the admin-connect frame), so until they have rendered,
    // _readHouseConfig() must fall back to the persisted entity selection
    // instead of reporting an empty one and silently wiping it.
    var _houseSaved = null;
    var _houseLightsRendered = false;
    var _houseEntitiesLoaded = false;
    _initHouseToggles();

    // Tell the server which language the lobby is being run in (#776). No-op
    // if the socket isn't open yet — ws.onopen re-sends after admin_connect,
    // the same contract _pushTtsConfig / _pushHouseConfig work under.
    function _pushLanguage() {
        send('set_language', { language: selectedLanguage });
    }

    // The same for the difficulty (#851). `GameState.difficulty` is written by
    // start_game and by nothing else, so the lobby snapshot every phone and
    // the television render described the PREVIOUS game — the constructor
    // default on the first game of the evening, the last game's pick after
    // that. Pushing the chip the moment it changes is what #776 did for the
    // language, for the same reason and with the same no-op-if-closed
    // contract.
    function _pushDifficulty() {
        send('set_difficulty', { difficulty: selectedDifficulty });
    }

    setupChips(els.languageChips, function (v) {
        // Session-only switch — not persisted. On the next full-page reload
        // the UI resolves back to the Home Assistant language (#152).
        selectedLanguage = v;
        // Carry the pick to the game right away so phones already sitting in
        // the lobby re-render, instead of finding out at start_game (#776).
        _pushLanguage();
        // Show/hide category chips based on selected language
        if (els.categoryChips) {
            var chips = els.categoryChips.querySelectorAll('.chip[data-lang]');
            chips.forEach(function (chip) {
                chip.style.display = (chip.dataset.lang === v) ? '' : 'none';
            });
            // Reset to mixed if current category doesn't match language
            var activeChip = els.categoryChips.querySelector('.chip.active');
            if (activeChip && activeChip.dataset.lang && activeChip.dataset.lang !== v) {
                activeChip.classList.remove('active');
                var mixedChip = els.categoryChips.querySelector('.chip[data-value="mixed"]');
                if (mixedChip) mixedChip.classList.add('active');
                selectedCategory = 'mixed';
                selectedCategories = [];
            }
        }
        // Re-evaluate pack-UI scaling: visible pack count may have
        // crossed a threshold (e.g. DE has 12 packs but EN has 3).
        updatePackUIScaling(v);
        // Rebuild the hero pack chips for the new language.
        buildHeroPackChips();
        // Re-translate the entire admin UI so labels switch
        // immediately on language pick (was previously a static
        // mismatch — admin saw mixed DE/EN labels).
        if (window.QuizifyI18n) {
            QuizifyI18n.setLanguage(v).then(function () {
                QuizifyI18n.initPageTranslations();
                updateSettingsSummary();
                updateCategorySummary();
            });
        } else {
            updateSettingsSummary();
            updateCategorySummary();
        }
    });
    // Init: sync language-chip active state + category-chip visibility to
    // the restored language (default German). Without this, a reloaded admin
    // page always showed the German chip active even after restoring English.
    if (els.languageChips) {
        els.languageChips.querySelectorAll('.chip').forEach(function (c) {
            c.classList.toggle('active', c.dataset.value === selectedLanguage);
        });
    }
    if (els.categoryChips) {
        els.categoryChips.querySelectorAll('.chip[data-lang]').forEach(function (chip) {
            chip.style.display = (chip.dataset.lang === selectedLanguage) ? '' : 'none';
        });
    }
    setupCollapsibles();

    // ---- WebSocket ----
    function connect() {
        var savedToken = QuizifyUtils.readAdminToken();
        // #359: the admin session token used to be appended to the WS URL as
        // ?token=..., where it leaked into aiohttp / reverse-proxy access logs
        // and browser history. It is now sent as the FIRST WebSocket frame
        // (admin_auth) below, before admin_connect or any other traffic, so it
        // never lands in a URL. The server still accepts ?token= as a
        // deprecated fallback, but we no longer put it there.
        ws = window.QuizifyClientCore.createSocket('/api/quizify/ws?role=admin', {
            logPrefix: '[Quizify Admin]',
            onOpen: function () {
                reconnectAttempts = 0;
                updateConnectionStatus('connected');
                // Send the token out-of-URL first so the server can grant admin
                // before admin_connect. On a fresh bootstrap there is no saved
                // token yet — the server grants admin on the token-less handshake
                // (bootstrap path), so skipping admin_auth here is safe.
                if (savedToken) send('admin_auth', { token: savedToken });
                send('admin_connect', {});
                // Configure narration up-front so pre-game lobby joins narrate (#281).
                _pushTtsConfig();
                // Same for the house effects (#494) — lobby-time, so they work
                // before start_game.
                _pushHouseConfig();
                // And the language (#776): the game keeps its own `language`, and
                // until start_game lands it is the constructor default "de". Every
                // phone joining an English lobby was handed a German frame. Push
                // the pick now so the lobby the players see matches the one the
                // host is looking at.
                _pushLanguage();
                // And the difficulty (#851), which sat at the previous game's
                // value for the same reason and reached the phones the same
                // way — only at start_game.
                _pushDifficulty();
            },
            onMessage: handleMessage,
            onClose: function () {
                ws = null;
                if (reconnectAttempts < MAX_RECONNECT) {
                    updateConnectionStatus('reconnecting');
                    // Exponential backoff, capped at 30s — the same curve the
                    // phone uses, which is why it lives in the shared core
                    // since #787. 1s, 2s, 4s, 8s, 16s, 30s, 30s, … keeps
                    // retrying across the full 1-5 min of an HA restart (#290).
                    var delay = window.QuizifyClientCore.backoffDelay(
                        reconnectAttempts, MAX_RECONNECT_DELAY_MS
                    );
                    reconnectAttempts++;
                    setTimeout(connect, delay);
                } else {
                    // Reached the attempt cap, but recovery is still possible — the
                    // visibilitychange listener and the manual retry affordance in
                    // updateConnectionStatus reset attempts and reconnect (#290).
                    updateConnectionStatus('disconnected');
                }
            }
        });
    }

    // Returns whether the message actually went out (#621).
    //
    // This used to be the same three lines without the `else`: on a closed
    // socket the command vanished with no toast, no log, nothing. #599 made
    // *refused* commands visible; an *undelivered* one looks identical from the
    // host's chair, so half the picture in #586 stayed dark.
    //
    // The boolean matters as much as the toast: callers were disabling their
    // button for 1.5s afterwards, which is a success animation for something
    // that never happened.
    function send(type, payload) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(Object.assign({ type: type }, payload)));
            return true;
        }
        _LOG_UNDELIVERED(type);
        showErrorToast(_t('connection.reconnecting'));
        return false;
    }

    function _LOG_UNDELIVERED(type) {
        // console, not _LOGGER — this is the browser. Kept separate so the
        // toast text and the diagnostic can diverge without touching send().
        if (window.console && console.warn) {
            console.warn('[quizify] command not sent, socket not open:', type);
        }
    }

    // ---- Message router ----
    function handleMessage(msg) {
        switch (msg.type || msg.event) {
            case 'joined':
                // Server confirmed the admin is registered as a player.
                // Stash the session_token under the keys the /quizify/player
                // page reads on load — when we later redirect (admin clicks
                // Spiel starten), the player page reconnects via token
                // instead of doing a fresh join (which would race against
                // the admin WS still being marked connected and fail with
                // "Name bereits vergeben").
                // #787: through QuizifyClientCore, which owns the two key
                // names. Spelled out here as literals, a rename on the phone
                // would have broken this redirect with nothing to catch it.
                if (msg.session_token && _adminJoinedAs) {
                    window.QuizifyClientCore.saveSession(msg.session_token, _adminJoinedAs);
                }
                break;
            case 'game_state':
                handleGameState(msg);
                break;
            case 'player_joined':
            case 'player_left':
                // #804: the roster frame carries the teams (#365) — a player
                // leaving also leaves their team, and the last one out
                // dissolves it. Adopt them before repainting, or the host
                // screen keeps grouping people under a team nobody is in.
                if (msg.teams) _lobbyTeams = msg.teams;
                if (msg.players) renderLobbyPlayers(msg.players);
                break;
            case 'teams_update':
                // A team formed or dissolved without the roster changing
                // (#365/#804) — the host screen has to follow that on its own,
                // exactly as the television does.
                setLobbyTeams(msg.teams);
                break;
            case 'wager_progress':
                handleWagerProgress(msg);
                break;
            case 'question_started':
                handleQuestionStarted(msg);
                break;
            case 'round_summary':
            case 'round_evaluated':
                handleRoundSummary(msg);
                break;
            case 'timer_tick':
                adminTimer.update(msg.remaining);
                break;
            case 'finale':
            case 'game_ended':
                handleFinale(msg);
                break;
            case 'game_reset':
                handleGameReset();
                break;
            case 'lightning_splash':
                handleAdminLightningSplash(msg);
                break;
            case 'lightning_question':
                handleAdminLightningQuestion(msg);
                break;
            case 'lightning_tick':
                if (els.adminLightningTimer && typeof msg.remaining === 'number') {
                    els.adminLightningTimer.textContent = Math.ceil(msg.remaining) + 's';
                }
                break;
            case 'lightning_recap':
                handleAdminLightningRecap(msg.recap || {});
                break;
            // #741: full broadcasts the host page received and dropped. A
            // steal moved the leaderboard in front of the host with no
            // account of why, and the reveal's reactions were phone-private.
            case 'powerup_applied':
                handlePowerUpApplied(msg);
                break;
            case 'reaction':
                showAdminReaction(msg.emoji);
                break;
            case 'reaction_bonus':
                handleReactionBonus(msg);
                break;
            // #832/#830: the Hot Seat detour is announced by these broadcasts
            // and by one snapshot at its start. The host page read the
            // snapshot only, so it stayed on the auction notice for the whole
            // detour and offered no Next Question at the end — see the block
            // above handleHotSeatAuction.
            case 'hot_seat_auction':
                handleHotSeatAuction(msg);
                break;
            case 'hot_seat_bid_count':
                handleHotSeatBidCount(msg);
                break;
            case 'hot_seat_no_bids':
                handleHotSeatNoBids();
                break;
            case 'hot_seat_awarded':
                handleHotSeatAwarded(msg);
                break;
            case 'hot_seat_question':
                handleHotSeatQuestion(msg);
                break;
            case 'hot_seat_tick':
                handleHotSeatTick(msg);
                break;
            case 'hot_seat_result':
                handleHotSeatResult(msg);
                break;
            case 'error':
                // The initial admin_connect attempt before authentication
                // returns "Admin only" — that's expected handshake noise,
                // not an error worth showing in the console. A REFUSED
                // command arrives as ADMIN_REQUIRED instead and is always
                // shown: swallowing it is what made Skip/Pause/Stop look
                // dead in #586.
                if (!(msg.code === 'INVALID_ACTION' && msg.message === 'Admin only')) {
                    console.warn('[Quizify Admin] Error:', msg.code, msg.message);
                }
                // Translate via i18n so admins in any locale see their own
                // language. Previous inline German-only map ignored locale.
                // Lookup pattern matches player-core.js handleError so both
                // sides agree.
                var tErr = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
                var errKey = 'errors.' + (msg.code || 'UNKNOWN');
                var translatedErr = tErr(errKey);
                var userMsg;
                if (translatedErr && translatedErr !== errKey) {
                    userMsg = translatedErr;
                } else if (msg.message) {
                    userMsg = msg.message;
                } else {
                    userMsg = tErr('errors.UNKNOWN');
                }
                // Suppress the noisy "Admin only" ping from initial admin_connect
                // attempt \u2014 it's expected when not yet authenticated.
                if (msg.code === 'INVALID_ACTION' && msg.message === 'Admin only') {
                    break;
                }
                showErrorToast(userMsg);
                break;
        }
    }

    // ---- Phase handlers ----

    function handleGameState(msg) {
        currentPhase = msg.phase;
        if (msg.admin_session_token) {
            QuizifyUtils.writeAdminToken(msg.admin_session_token);
            // #433: load the saved presets once the token exists. Doing it on
            // page load instead would race the token's arrival and 401 —
            // exactly the failure #501 chased for the entity dropdowns.
            if (!_presetsLoaded) {
                _presetsLoaded = true;
                _loadCustomPresets();
            }
        }
        // The admin-connect frame now carries the TTS-engine + media-player
        // lists directly (server-side, over this already-authenticated socket),
        // so the narration dropdowns (#281) populate WITHOUT the separate
        // admin-token-gated /api/quizify/tts-entities fetch racing the token's
        // arrival (#356/#501 were a fragile refetch band-aid for exactly that
        // race). Populate straight from the payload and mark loaded. An empty
        // array is still "present" (JS: [] is truthy), so a host with no
        // TTS/media_player entities correctly shows "None found".
        if ((msg.tts_entities || msg.media_players) && _ttsEls && _ttsEls.engine) {
            var _ttsCfg = _readTtsConfig();
            _populateEntitySelect(_ttsEls.engine, msg.tts_entities || [], _ttsCfg.tts_entity);
            _populateEntitySelect(_ttsEls.speaker, msg.media_players || [], _ttsCfg.media_player);
            _ttsEntitiesLoaded = true;
        } else if (msg.admin_session_token && !_ttsEntitiesLoaded && _ttsEls && _ttsEls.engine) {
            // Fallback for an older server that doesn't send the lists on the
            // admin frame: now that the token is stored, refetch over HTTP once.
            _loadTtsEntities(_readTtsConfig());
        }
        // House Plays Along entity lists (#494) ride the same admin-connect
        // frame — {lights, media_players, scenes}, each item {entity_id,
        // friendly_name}. Same one-shot-guard + HTTP fallback as the TTS lists.
        if (msg.house_entities && _houseEls && _houseEls.lightList) {
            _populateHouseEntities(msg.house_entities, _readHouseConfig());
        } else if (msg.admin_session_token && !_houseEntitiesLoaded && _houseEls && _houseEls.lightList) {
            _loadHouseEntities(_readHouseConfig());
        }
        // #804: teams ride the snapshot (#365), so a host screen that connects
        // late — or reloads mid-lobby — still shows who is playing with whom.
        if (msg.teams) _lobbyTeams = msg.teams;
        if (msg.players) renderLobbyPlayers(msg.players);

        // If game is running and we have a stored name, redirect to player.html
        if (msg.phase !== 'LOBBY' && msg.phase !== 'FINALE') {
            var savedName = _adminJoinedAs || sessionStorage.getItem('quizify_admin_name');
            if (savedName && !_redirecting) {
                redirectToPlayer(savedName);
                return;
            }
        } else if (!_adminJoinedAs) {
            // Game is in lobby or over and admin hasn't actively joined as
            // a player in this session — clear any stale name so we don't
            // accidentally redirect on the next phase change. When
            // _adminJoinedAs IS set (admin clicked "Als Spieler beitreten"
            // and is waiting in the lobby), we MUST keep the name so the
            // redirect fires when they hit Spiel starten.
            sessionStorage.removeItem('quizify_admin_name');
        }

        switch (msg.phase) {
            case 'LOBBY':
                // Stay on whatever view is currently active (setup or
                // lobby) instead of forcing setup. If admin loaded the
                // page while game was running and it has since been
                // reset, leave them on setup so they can configure.
                if (views.lobby.classList.contains('active') ||
                    views.game.classList.contains('active')) {
                    showView('lobby');
                }
                break;
            case 'QUESTION_ACTIVE':
                if (msg.question) {
                    handleQuestionStarted({
                        question_text: msg.question.text,
                        correct_answer: '',
                        timer_duration: msg.question.time_limit,
                        round_num: msg.round,
                        total_rounds: msg.total_rounds,
                    });
                }
                if (msg.leaderboard) renderLeaderboard(els.gameLeaderboard, msg.leaderboard);
                break;
            case 'ANSWER_REVEAL':
                // No-op on the admin tab: the production flow always
                // redirects the host to /quizify/player on game start,
                // so the per-round reveal is owned by .pl-result in
                // player.html. The old #admin-reveal-view was removed
                // in v1.1.16.
                break;
            case 'WAGER_ACTIVE':
            case 'HOT_SEAT_AUCTION':
            case 'HOT_SEAT':
            case 'HOT_SEAT_REVEAL':
                // #699: this switch had no case for any of them, so a host who
                // started without joining kept looking at the previous
                // question, "Correct: …" and an enabled Next Question that
                // answers ERR_INVALID_ACTION for the ~90 seconds the detour
                // lasts. Show what phase the room is in, and only offer Next
                // where the server actually accepts it.
                showView('game');
                // #732: the seat holder's name so the notice can name who the
                // room is watching, instead of the second-person hint written
                // for that player's own phone.
                setDetourNotice(msg.phase, msg.hot_seat && msg.hot_seat.winner);
                if (msg.leaderboard) renderLeaderboard(els.gameLeaderboard, msg.leaderboard);
                break;
            case 'FINALE':
                // Land directly on finale view so the admin sees the
                // result and the "Neues Spiel starten" button without
                // first being shown the (no-op) setup screen. Fixes
                // the lockout where admin clicked Spiel starten and
                // server rejected because phase was already FINALE.
                handleFinale(msg);
                break;
            case 'LIGHTNING':
                // Admin who didn't join as a player watches the lightning
                // round here; admins-as-players were redirected to
                // player.html above (savedName branch). (issue #42)
                currentPhase = 'LIGHTNING';
                showView('lightning');
                if (msg.lightning && msg.lightning.splash_pending) {
                    handleAdminLightningSplash({
                        num_questions: msg.lightning.num_questions,
                        seconds_per_question: msg.lightning.seconds_per_question,
                    });
                } else if (msg.lightning) {
                    handleAdminLightningQuestion({
                        index: msg.lightning.index,
                        num_questions: msg.lightning.num_questions,
                        question_text: msg.lightning.question ? msg.lightning.question.text : '',
                    });
                }
                break;
            case 'LIGHTNING_RECAP':
                currentPhase = 'LIGHTNING_RECAP';
                showView('lightningRecap');
                if (msg.lightning_recap) handleAdminLightningRecap(msg.lightning_recap);
                break;
        }
    }

    // ---- Lightning Round (issue #42) ----

    function _toggleAdminLightningSplash(showSplash) {
        // #285: the host no longer dismisses the splash (no Start button) — it
        // auto-advances server-side. This just swaps the splash/question panes.
        if (els.adminLightningSplash) els.adminLightningSplash.hidden = !showSplash;
        if (els.adminLightningQuestionSection) {
            els.adminLightningQuestionSection.hidden = showSplash;
        }
    }

    function handleAdminLightningSplash(msg) {
        if (_redirecting) return;
        msg = msg || {};
        currentPhase = 'LIGHTNING';
        showView('lightning');
        _toggleAdminLightningSplash(true);
        if (els.adminLightningSplashRules) {
            var tFn = (window.QuizifyI18n && window.QuizifyI18n.t) || function (k) { return k; };
            var n = (typeof msg.num_questions === 'number') ? msg.num_questions : 5;
            var s = (typeof msg.seconds_per_question === 'number')
                ? Math.round(msg.seconds_per_question) : 15;
            var hint = tFn('lightning.startHint');
            els.adminLightningSplashRules.textContent =
                (hint && hint !== 'lightning.startHint') ? hint
                : (n + ' questions · ' + s + 's each');
        }
    }

    function handleAdminLightningQuestion(msg) {
        if (_redirecting) return;
        currentPhase = 'LIGHTNING';
        showView('lightning');
        _toggleAdminLightningSplash(false);
        if (els.adminLightningProgress) {
            els.adminLightningProgress.textContent =
                ((msg.index || 0) + 1) + ' / ' + (msg.num_questions || 5);
        }
        if (els.adminLightningQuestion) {
            els.adminLightningQuestion.textContent = msg.question_text || '';
        }
    }

    function handleAdminLightningRecap(recap) {
        currentPhase = 'LIGHTNING_RECAP';
        showView('lightningRecap');
        if (els.adminLightningRecapLeaderboard) {
            renderLeaderboard(els.adminLightningRecapLeaderboard, recap.leaderboard || []);
        }
        if (els.adminLightningRecapGrid) {
            var questions = recap.questions || [];
            // The results are keyed by entrant — a team's key is its id since
            // #728, so the chip label comes from the names map. Falling back
            // to the key keeps solo players (whose key IS their name) right.
            var names = recap.names || {};
            els.adminLightningRecapGrid.innerHTML = questions.map(function (q, qi) {
                var chips = Object.keys(q.results || {}).map(function (entrant) {
                    var r = q.results[entrant];
                    var mark = r === 'correct' ? '✓' : (r === 'wrong' ? '✗' : '–');
                    return '<span class="lr-admin-chip lr-admin-chip--' + r + '">' +
                        escapeHtmlAdmin(names[entrant] || entrant) + ' ' + mark + '</span>';
                }).join('');
                return '<div class="lr-admin-row">' +
                    '<div class="lr-admin-q"><span class="lr-admin-qnum">' + (qi + 1) +
                    '.</span> ' + escapeHtmlAdmin(q.question_text) +
                    ' <span class="lr-admin-correct">→ ' + escapeHtmlAdmin(q.correct_answer) +
                    '</span></div>' +
                    '<div class="lr-admin-chips">' + chips + '</div></div>';
            }).join('');
        }
    }

    function escapeHtmlAdmin(s) {
        if (window.QuizifyUtils && window.QuizifyUtils.escapeHtml) {
            return window.QuizifyUtils.escapeHtml(s);
        }
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    /**
     * The final round's betting window (#656).
     *
     * The host screen shows the same thing the phones do — category, and how
     * many bets are in. Never the amounts: a tally the TV gave away would not
     * be a bet. The opening message carries ``window_duration`` and starts the
     * countdown; the refreshes that arrive with each bet leave it out, so the
     * clock keeps running instead of restarting on every tap.
     */
    /**
     * Tell the host which detour the room is in (#699).
     *
     * The admin tab has no view of its own for the auction, the seat holder's
     * question or the wager window, and it used to keep showing the previous
     * round with a live Next Question that the server refuses. This replaces
     * the stale text and only offers Next where next_round is accepted —
     * HOT_SEAT_REVEAL, which is the one phase the server leaves on it.
     *
     * #732: the host reads host strings. #699 reached for keys that read well
     * in English and happened to exist, and two of them were written for
     * somebody else's screen — `hotSeat.seatedHint` addresses the phone of the
     * person in the chair ("You answer alone; right wins your stake…"), and
     * `hotSeat.sealed` belongs to the auction, so at the *result* stage, next
     * to an enabled Next Question, the host was told the bids were still
     * hidden. The English fallbacks beside those keys said the right thing and
     * never rendered: `_t()` hands back a translation whenever the key exists,
     * so a wrong-but-present key always wins. ``seatHolder`` is the winner's
     * name out of the snapshot's ``hot_seat`` block, which is populated for
     * both HOT_SEAT and HOT_SEAT_REVEAL — the detour is only entered once the
     * chair has actually been won.
     */
    function setDetourNotice(phase, seatHolder) {
        var name = seatHolder ? String(seatHolder) : '';
        var keys = {
            WAGER_ACTIVE: ['wager.hostWindowTitle', null, 'Final round — players are betting'],
            HOT_SEAT_AUCTION: ['hotSeat.auctionTitle', null, 'The chair goes to the highest bid'],
            HOT_SEAT: name
                ? ['hotSeat.hostSeated', { name: name },
                   name + ' has the chair and is answering alone']
                : ['hotSeat.hostSeatedUnknown', null,
                   'Whoever won the chair is answering alone'],
            HOT_SEAT_REVEAL: ['hotSeat.hostSettled', null,
                              'The chair is settled — Next Question continues the game']
        };
        var entry = keys[phase];
        if (els.adminQuestion && entry) {
            // _tOr, not `|| entry[2]`: _t() returns the key itself when no
            // bundle is loaded, so a falsy check never reaches the fallback
            // and the host reads "hotSeat.hostSeated" off the screen.
            els.adminQuestion.textContent = _tOr(entry[0], entry[1], entry[2]);
        }
        if (els.adminCorrect) {
            els.adminCorrect.textContent = '';
            els.adminCorrect.style.display = 'none';
        }
        // Each stage of the detour writes its own second line afterwards; a
        // stale one (the bid count, once the auction is over) would be worse
        // than none.
        setDetourDetail('');
        var advanceable = (phase === 'HOT_SEAT_REVEAL');
        if (els.nextQuestionBtn) {
            els.nextQuestionBtn.classList.toggle('hidden', !advanceable);
        }
        if (els.endGameBtn) els.endGameBtn.classList.remove('hidden');
    }

    /** The detour's second line, under the notice. '' hides it. */
    function setDetourDetail(text) {
        if (!els.adminDetourNote) return;
        els.adminDetourNote.textContent = text || '';
        els.adminDetourNote.style.display = text ? '' : 'none';
    }

    // ---- Hot Seat, live (#832 / #830) ----------------------------------
    //
    // The detour's phase changes are announced by one-shot broadcasts, not by
    // snapshots. The server sends a full `game_state` when the auction opens
    // and not again until the round after it; the chair being won, the seat
    // question and the settlement each arrive as a `hot_seat_*` frame alone.
    // #699 taught `handleGameState` about the three phases, so the host page
    // was right for exactly as long as the snapshot was — it then sat on "The
    // chair goes to the highest bid" for the rest of the detour, and at the
    // end offered no Next Question although HOT_SEAT_REVEAL accepts
    // next_question. Only a reload, which pulls a fresh snapshot, said so
    // (#832, found on hardware during the v1.16.0-RC1 live test).
    //
    // So the host page follows the events as well as the snapshot. Both
    // routes land in `setDetourNotice`, which stays the single place that
    // decides what the notice says and whether Next Question is offered.

    function _setDetourRound(msg) {
        if (!els.adminRound || typeof msg.round_num !== 'number') return;
        els.adminRound.textContent = _t('admin.questionCounter', {
            current: msg.round_num,
            total: msg.total_rounds,
        });
    }

    /** The auction opened: the notice, the round it belongs to, the clock. */
    function handleHotSeatAuction(msg) {
        if (_redirecting) return;
        currentPhase = 'HOT_SEAT_AUCTION';
        showView('game');
        _setDetourRound(msg);
        setDetourNotice('HOT_SEAT_AUCTION');
        if (typeof msg.seconds === 'number') adminTimer.start(msg.seconds);
    }

    /**
     * How many have bid — never how much.
     *
     * A blind auction, so the count is the public half (the amounts stay with
     * the bidder until the reveal). It is also the only thing that moves on
     * the host's screen while the room bids: without it the auction looks
     * identical to a frozen page for the whole window.
     */
    function handleHotSeatBidCount(msg) {
        if (_redirecting) return;
        setDetourDetail(_tOr(
            'hotSeat.bidCount',
            { count: msg.count || 0, total: msg.total || 0 },
            (msg.count || 0) + ' / ' + (msg.total || 0)
        ));
    }

    /** Nobody bid. Not a failure — a round that does not happen. */
    function handleHotSeatNoBids() {
        if (_redirecting) return;
        showView('game');
        if (els.adminQuestion) {
            els.adminQuestion.textContent = _tOr(
                'hotSeat.noBids', null, 'Nobody bid — carrying on as usual.'
            );
        }
        setDetourDetail('');
        // The server resumes the normal question straight after this, and
        // question_started repaints the screen; until it lands there is
        // nothing here for the host to advance.
        if (els.nextQuestionBtn) els.nextQuestionBtn.classList.add('hidden');
    }

    /** The chair has been won. */
    function handleHotSeatAwarded(msg) {
        if (_redirecting) return;
        currentPhase = 'HOT_SEAT';
        showView('game');
        // #804: `winner` is the PERSON in the chair, `entrant` is who pays —
        // their team in team mode. The notice names the person the room is
        // about to watch; the price is charged to the entrant.
        setDetourNotice('HOT_SEAT', msg.winner);
        var payer = msg.entrant || msg.winner;
        setDetourDetail(_tOr(
            'hotSeat.lost',
            { name: payer, pct: msg.pct, pts: msg.stake },
            payer + ' — ' + msg.pct + '%'
        ));
    }

    /**
     * The seat holder's question, on the host's screen too.
     *
     * The host is running the evening: they read the room, and for ninety
     * seconds the only question in play was one they could not see. The
     * answers are deliberately not here — the server sends them to the seat
     * holder alone, and the host page is not a player.
     */
    function handleHotSeatQuestion(msg) {
        if (_redirecting) return;
        currentPhase = 'HOT_SEAT';
        showView('game');
        _setDetourRound(msg);
        if (els.adminQuestion) els.adminQuestion.textContent = msg.question || '';
        // The second line is left exactly as the award set it: what the chair
        // cost is what this answer is about to win or lose.
        if (typeof msg.seconds === 'number') adminTimer.start(msg.seconds);
    }

    /** Both windows tick over the same bar the normal round uses. */
    function handleHotSeatTick(msg) {
        if (_redirecting) return;
        if (typeof msg.remaining === 'number') adminTimer.update(msg.remaining);
    }

    /**
     * The settlement — and the reason for #832.
     *
     * This is the frame that moves the game into HOT_SEAT_REVEAL, the one
     * phase of the detour where the server accepts `next_question`. Without a
     * case here the host was left with the reset icon and End Game: both
     * throw away the rest of the evening.
     */
    function handleHotSeatResult(msg) {
        if (_redirecting) return;
        currentPhase = 'HOT_SEAT_REVEAL';
        showView('game');
        adminTimer.stop();
        _setDetourRound(msg);
        setDetourNotice('HOT_SEAT_REVEAL', msg.winner);
        // `answered` is tri-state on the server: true = right, false = wrong,
        // null = never answered. A bare falsy check collapses the last two,
        // and since #653 an unanswered chair costs the same as a wrong one —
        // so the host would be told the room ran out of time when somebody
        // had actually guessed.
        var noAnswer = msg.answered === null || msg.answered === undefined;
        var payer = msg.entrant || msg.winner;
        // #804: a team's key in `deltas` is its id, which no screen can
        // construct — `winner_delta` is the seat entrant's own settlement.
        var delta = (msg.winner_delta != null) ? msg.winner_delta : 0;
        var key = noAnswer
            ? 'hotSeat.resultTimeout'
            : (msg.answered === true ? 'hotSeat.resultRight' : 'hotSeat.resultWrong');
        setDetourDetail(_tOr(
            key, { name: payer, pts: Math.abs(delta) },
            payer + ' ' + (delta > 0 ? '+' : '') + delta
        ));
        // The stake has just moved the standings the host is looking at. The
        // frame carries them keyed by name, ranked — a leaderboard that still
        // shows the pre-settlement scores next to "the chair is settled" is
        // the same lie the notice was.
        if (msg.scores) {
            renderLeaderboard(els.gameLeaderboard, Object.keys(msg.scores)
                .map(function (name) { return { name: name, score: msg.scores[name] }; })
                .sort(function (a, b) { return b.score - a.score; }));
        }
    }

    function handleWagerProgress(msg) {
        if (_redirecting) return;
        currentPhase = 'WAGER_ACTIVE';
        showView('game');

        els.adminRound.textContent = _t('admin.questionCounter', {
            current: msg.round_num,
            total: msg.total_rounds,
        });
        els.adminQuestion.textContent =
            _t('wager.hostWindowTitle') + ' — ' +
            _t('wager.hostProgress', {
                locked: msg.locked_in,
                total: msg.player_count,
            });
        if (els.adminCorrect) {
            els.adminCorrect.textContent = '';
            els.adminCorrect.style.display = 'none';
        }
        if (typeof msg.window_duration === 'number') {
            adminTimer.start(msg.window_duration);
        }
        if (els.nextQuestionBtn) els.nextQuestionBtn.classList.add('hidden');
        if (els.endGameBtn) els.endGameBtn.classList.add('hidden');
    }

    function handleQuestionStarted(msg) {
        if (_redirecting) return;
        currentPhase = 'QUESTION_ACTIVE';
        showView('game');

        els.adminRound.textContent = _t('admin.questionCounter', {
            current: msg.round_num,
            total: msg.total_rounds,
        });
        els.adminQuestion.textContent = msg.question_text;
        // Never show correct answer during active question
        if (els.adminCorrect) {
            els.adminCorrect.textContent = '';
            els.adminCorrect.style.display = 'none';
        }
        // The normal game is back; whatever the Hot Seat left under the
        // question belongs to a round that is over (#832).
        setDetourDetail('');

        adminTimer.start(msg.timer_duration);
        if (els.nextQuestionBtn) els.nextQuestionBtn.classList.add('hidden');
        if (els.endGameBtn) els.endGameBtn.classList.add('hidden');
    }

    // The full admin reveal view (showReveal / renderAnswerDistribution) was
    // removed in v1.1.16 because "the host always redirects to
    // /quizify/player on game start" — true then, but the lobby has since
    // grown a "start without joining" path (doStartGameNoJoin), and that host
    // stays here. For them this used to render nothing at all: both controls
    // are hidden at question start and nothing un-hid them, so the reveal was
    // a dead screen and the guests' only way on was the 60-second "host gone"
    // reset, which wipes the game (#618).
    //
    // This deliberately does NOT rebuild the old reveal view. The player tab
    // still owns the rich reveal; the admin tab needs the answer and a way
    // forward, and nothing more.
    function handleRoundSummary(msg) {
        if (_redirecting) return;
        currentPhase = 'ANSWER_REVEAL';
        adminTimer.stop();

        if (els.adminCorrect && msg && msg.correct_answer) {
            els.adminCorrect.textContent = _t('admin.correctLabel', {
                answer: msg.correct_answer,
            });
            els.adminCorrect.style.display = '';
        }
        // #849: the round the host is revealing has already been scored, and
        // this frame carries the standings that came out of it — the
        // television renders exactly this field at exactly this moment. The
        // host page did not, so it kept the previous round's board until the
        // next `question_started` arrived: the one screen reading the scores
        // out to the room was the one screen a round behind. Same class as
        // #833 one surface over — the settlement had happened, only the board
        // had not been told.
        if (msg && msg.leaderboard) {
            renderLeaderboard(els.gameLeaderboard, msg.leaderboard);
        }
        // The last round advances like any other: `next_question` from
        // ANSWER_REVEAL resolves to the finale server-side (#255), which is
        // exactly what the phone's admin bar offers as "Final Results"
        // (player-reveal.js). Hiding the button here left the admin-tab host
        // with only the red End Game and its disconnect warning — warned off
        // the one step the room was waiting for (#806). So relabel it
        // instead of hiding it, and set data-i18n too so a mid-game language
        // switch re-translates the new label rather than the old one.
        if (els.nextQuestionBtn) {
            var lastRound = !!(msg && msg.last_round);
            var nextKey = lastRound ? 'reveal.finalResults' : 'admin.nextQuestion';
            var nextFallback = lastRound ? 'Final Results' : 'Next Question';
            // _t() returns the key itself when no bundle is loaded, so the
            // fallback is chosen on that rather than on falsiness.
            var nextText = _t(nextKey);
            els.nextQuestionBtn.setAttribute('data-i18n', nextKey);
            els.nextQuestionBtn.textContent =
                (nextText && nextText !== nextKey) ? nextText : nextFallback;
            els.nextQuestionBtn.classList.remove('hidden');
        }
        if (els.endGameBtn) els.endGameBtn.classList.remove('hidden');
    }

    function handleFinale(msg) {
        if (_redirecting) return;
        currentPhase = 'FINALE';
        adminTimer.stop();
        showView('finale');

        var podium = msg.podium || [];
        renderPodium(els.adminPodium, podium);

        var lb = msg.leaderboard || msg.all_players || [];
        renderLeaderboard(els.adminFinaleLeaderboard, lb);
    }

    function handleGameReset() {
        currentPhase = 'LOBBY';
        sessionStorage.removeItem('quizify_admin_name');
        // The reset wiped the whole session server-side (issue #207),
        // including the admin's own player slot. Forget any "joined as"
        // name so the host starts from a truly clean lobby instead of
        // showing a stale joined-confirmation chip.
        _adminJoinedAs = null;
        _redirecting = false;
        showView('setup');
    }

    // ---- Renderers ----

    // Player palette mirrors PLAYER_COLORS in game/player.py so the
    // dot in the lobby card matches the dot the player sees on their phone.
    var _LOBBY_COLORS = [
        '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7', '#DDA0DD',
        '#98D8C8', '#F7DC6F', '#BB8FCE', '#F0A500',
    ];

    // Minimum players required before "ready" — keep in sync with
    // const.MIN_PLAYERS on the server (currently 1: solo play allowed).
    // Used to flip the marquee text between "Waiting…" and "About to
    // start…" and to gate the Start Game button.
    var LOBBY_MIN_PLAYERS = 1;

    // The teams as of the last roster / snapshot / teams_update (#804). The
    // television has grouped its lobby by team since #365; the host screen
    // showed a flat name list, so the one person running the evening was the
    // only one in the room who could not see that the game is in team mode —
    // and therefore could not see why the Hot Seat and the final wager behave
    // differently. Kept beside the roster rather than derived from it, exactly
    // as dashboard.html does: a team can form or dissolve without the roster
    // changing at all.
    var _lobbyTeams = [];
    var _lastLobbyPlayers = [];

    /** True once at least one team exists — the server's ``team_mode``. */
    function isTeamMode() {
        return _lobbyTeams.length > 0;
    }

    /**
     * Adopt the teams carried by a roster/snapshot frame, then repaint (#804).
     *
     * ``player_joined`` / ``player_left`` / ``game_state`` all carry ``teams``
     * (a player leaving also leaves their team, #365), and ``teams_update``
     * fires when a team forms or dissolves with no roster change at all.
     */
    function setLobbyTeams(teams) {
        _lobbyTeams = Array.isArray(teams) ? teams : [];
        renderLobbyPlayers(_lastLobbyPlayers);
    }

    function renderLobbyPlayers(players) {
        var list = Array.isArray(players)
            ? players
            : (players && typeof players === 'object' ? Object.values(players) : []);
        _lastLobbyPlayers = list;
        playerCount = list.length;
        if (els.lobbyPlayerCount) els.lobbyPlayerCount.textContent = playerCount;

        var isReady = playerCount >= LOBBY_MIN_PLAYERS;

        // Marquee text — "Waiting…" until min reached, then "About to start…"
        var marqueeEl = document.getElementById('lobby-marquee');
        if (marqueeEl) {
            marqueeEl.textContent = _t(isReady ? 'lobby.marqueeReady' : 'lobby.marqueeWaiting');
        }

        // Countdown line under the QR row. Once we're at threshold we
        // hide it entirely — "Ready at 1 player · still need 0 more" is
        // noise once the Start button is visible.
        var countdownEl = document.getElementById('lobby-countdown');
        var minEl = document.getElementById('lobby-min-players');
        var missingEl = document.getElementById('lobby-missing-players');
        if (minEl) minEl.textContent = LOBBY_MIN_PLAYERS;
        if (missingEl) {
            missingEl.textContent = Math.max(0, LOBBY_MIN_PLAYERS - playerCount);
        }
        if (countdownEl) {
            countdownEl.classList.toggle('is-ready', isReady);
            // #706: at LOBBY_MIN_PLAYERS = 1 the only state that shows this
            // line is the empty lobby, where it reads "Ready at 1 players ·
            // still need 1 more" — a plural for one and a countdown for a
            // threshold nobody is waiting on. The marquee already says the
            // room is waiting. Kept in the markup for a higher threshold.
            var worthShowing = LOBBY_MIN_PLAYERS > 1 && !isReady;
            countdownEl.classList.toggle('hidden', !worthShowing);
        }

        if (els.startGameplayBtn) {
            // Gated on min-players: shows as soon as we can run a round.
            els.startGameplayBtn.classList.toggle('hidden', !isReady);
        }

        // Once the admin has joined as a player, swap the "Als Spieler
        // beitreten" button into a static confirmation chip so they see
        // they're registered and can't accidentally re-join under a
        // different name. We don't strictly need the server state here —
        // _adminJoinedAs is set the moment we send the join message — but
        // we double-check the roster so the chip survives a reload.
        if (els.participateBtn && _adminJoinedAs) {
            // #244: disable the "Join as Player" control the moment the admin
            // has claimed a player slot — don't wait for the roster broadcast
            // to round-trip, otherwise there's a window where a second tap
            // creates a duplicate player. The confirmation chip ("Joined as
            // …") still requires roster confirmation so it survives a reload.
            els.participateBtn.disabled = true;
            var rosterHasMe = list.some(function (p) {
                var n = typeof p === 'string' ? p : (p && p.name);
                return n === _adminJoinedAs;
            });
            if (rosterHasMe) {
                els.participateBtn.classList.add('is-joined');
                els.participateBtn.innerHTML =
                    '<span class="btn-icon" aria-hidden="true">✓</span>' +
                    '<span>' + escapeHtml(_t('admin.joinedAs', { name: _adminJoinedAs })) + '</span>';
            }
        }
        if (els.lobbyPlayersEmpty) {
            els.lobbyPlayersEmpty.classList.toggle('hidden', playerCount > 0);
        }
        if (els.lobbyPlayerChips) {
            var hostBadge = '<span class="host-badge" title="'
                + escapeHtml(_t('admin.hostBadge') || 'Host')
                + '" aria-label="'
                + escapeHtml(_t('admin.hostBadge') || 'Host')
                + '">👑</span>';
            var card = function (p, idx) {
                    var name = typeof p === 'string' ? p : (p.name || p);
                    var isAdmin = typeof p === 'object' && p && p.is_admin;
                    var fallbackColor = _LOBBY_COLORS[idx % _LOBBY_COLORS.length];
                    // #312 defense-in-depth: p.color is server-provided and is
                    // injected into a style="background:..." attribute. Accept
                    // only a strict #rrggbb hex; otherwise fall back to the
                    // palette so a hostile value can't break out of the style.
                    var rawColor = (typeof p === 'object' && p && p.color) ? p.color : '';
                    var color = /^#[0-9a-fA-F]{6}$/.test(rawColor)
                        ? rawColor
                        : fallbackColor;
                    var initial = (name || '?').charAt(0).toUpperCase();
                    var kickBtn = isAdmin
                        ? ''
                        : '<button type="button" class="player-chip-kick" data-kick-name="'
                            + escapeHtml(name)
                            + '" aria-label="'
                            + escapeHtml(_t('admin.kickPlayer') || ('Remove ' + name))
                            + '" title="'
                            + escapeHtml(_t('admin.kickPlayer') || ('Remove ' + name))
                            + '">&times;</button>';
                    return '<div class="lobby-e-row-card' + (isAdmin ? ' is-host' : '') + '">'
                        + '<span class="dot" style="background:' + color + '">' + escapeHtml(initial) + '</span>'
                        + '<span class="name">' + escapeHtml(name) + '</span>'
                        + (isAdmin ? hostBadge : '')
                        + kickBtn
                        + '</div>';
            };

            if (!isTeamMode()) {
                els.lobbyPlayerChips.innerHTML = list.map(card).join('');
            } else {
                // Same grouping the television has used since #365: every team
                // as a titled block of its members, and anyone who joined no
                // team keeps their own row underneath. A player in no team is
                // a team of one, not an error state — so they are not swept
                // into a leftover group.
                var inTeam = {};
                var seq = 0;
                var groups = _lobbyTeams.map(function (team) {
                    var members = (team.members || []).map(function (memberName) {
                        inTeam[memberName] = true;
                        for (var i = 0; i < list.length; i++) {
                            var candidate = list[i];
                            var candidateName = typeof candidate === 'string'
                                ? candidate
                                : (candidate && candidate.name);
                            if (candidateName === memberName) return candidate;
                        }
                        return { name: memberName };
                    });
                    return '<div class="lobby-e-team">'
                        + '<div class="lobby-e-team-name">'
                        + escapeHtml(team.name || '')
                        + '<span class="lobby-e-team-size">'
                        + members.length
                        + '</span></div>'
                        + members.map(function (m) { return card(m, seq++); }).join('')
                        + '</div>';
                }).join('');
                var solo = list.filter(function (p) {
                    var n = typeof p === 'string' ? p : (p && p.name);
                    return !inTeam[n];
                });
                els.lobbyPlayerChips.innerHTML =
                    groups + solo.map(function (p) { return card(p, seq++); }).join('');
            }
            // Single delegated click handler — re-attaching per render is fine
            // because innerHTML wipes the previous listeners with the nodes.
            els.lobbyPlayerChips.querySelectorAll('.player-chip-kick').forEach(function (btn) {
                btn.addEventListener('click', function (ev) {
                    ev.stopPropagation();
                    var name = btn.getAttribute('data-kick-name');
                    if (!name) return;
                    // #480: themed confirm modal instead of window.confirm().
                    openKickModal(name, btn);
                });
            });
        }
        applyTeamModeSetupNotes();
    }

    /**
     * Explain what teams do to the two settings, before Start (#804).
     *
     * The Hot Seat and the final wager are chosen on the setup screen; the
     * teams are formed by the guests in the lobby, i.e. afterwards. So a host
     * who ticked both had no way to learn from the host screen that the room
     * had since split into teams, nor what that does to the two toggles —
     * which for the whole of #668/#669 was "switches them off", and is now
     * "the team bids and bets as one". Either way the host should read it
     * before Start rather than infer it from what does or does not happen.
     *
     * The note lives on the setup rows because that is where the promise was
     * made, and the lobby's "Settings" button leads straight back to it.
     */
    function applyTeamModeSetupNotes() {
        var on = isTeamMode();
        ['hot-seat-team-note', 'wager-team-note'].forEach(function (id) {
            var node = document.getElementById(id);
            if (node) node.classList.toggle('hidden', !on);
        });
    }

    // ============================================
    // #741 — power-ups and reactions on the host screen
    // ============================================

    // Same strip, same rules as the television (dashboard.html): only the
    // power-ups that land on somebody *else*. JOKER, DOUBLE_POINTS and
    // TIME_BOOST touch nothing but the user's own turn, so showing them would
    // put something on screen almost constantly and none of it would mean
    // anything.
    var POWERUP_BANNER_MS = 4000;
    var POWERUP_BANNER_EXIT_MS = 260;
    var SCORE_DELTA_MS = 4000;
    // The specs, the sentence and the reading of `powerup_applied` are the
    // television's too, to the character — shared since #787. Only the two
    // span class names are the host page's own.
    var BOARD_POWERUPS = window.QuizifyRenderShared.POWERUP_SPECS;
    var POWERUP_CLASSES = {
        name: 'powerup-banner-name',
        points: 'powerup-banner-points'
    };

    var _lastGameLeaderboard = null;

    function powerUpSentenceHtml(spec, vars) {
        return window.QuizifyRenderShared.powerUpSentenceHtml(spec, vars, POWERUP_CLASSES);
    }

    var handlePowerUpApplied = window.QuizifyRenderShared.createPowerUpApplied({
        showBanner: function (spec, vars) { showPowerUpBanner(spec, vars); },
        showScoreDeltas: function (list) { showScoreDeltas(list); }
    });

    function showPowerUpBanner(spec, vars) {
        if (!els.powerupBanners) return;
        var el = document.createElement('div');
        el.className = 'powerup-banner';
        el.innerHTML =
            '<span class="powerup-banner-icon" aria-hidden="true">' + spec.icon + '</span>' +
            '<span class="powerup-banner-text">' + powerUpSentenceHtml(spec, vars) + '</span>';
        els.powerupBanners.appendChild(el);
        // Each strip carries its own timer, so a second one arriving mid-stand
        // stacks underneath and the older one still leaves first.
        setTimeout(function () {
            el.classList.add('is-leaving');
            setTimeout(function () {
                if (el.parentNode) el.parentNode.removeChild(el);
            }, POWERUP_BANNER_EXIT_MS);
        }, POWERUP_BANNER_MS);
    }

    // Transient +/- chips on the two rows a steal moved. Kept in state because
    // game_state repaints the leaderboard every few seconds and would
    // otherwise wipe them mid-stand. The store, the hold and the chip markup
    // are the television's too, so both come from QuizifyRenderShared since
    // #787; the repaint is this page's own panel.
    var _scoreDeltas = window.QuizifyRenderShared.createScoreDeltas({
        holdMs: SCORE_DELTA_MS,
        repaint: function () {
            if (_lastGameLeaderboard) renderLeaderboard(els.gameLeaderboard, _lastGameLeaderboard);
        }
    });

    function showScoreDeltas(deltas) {
        _scoreDeltas.show(deltas);
    }

    function scoreDeltaHtml(name) {
        return _scoreDeltas.html(name);
    }

    function showAdminReaction(emoji) {
        // Reveal only, same rule as the television: over a live question the
        // movement competes with reading. The server already collapses the
        // buffer to one frame per distinct player+emoji per flush window, so
        // there is nothing left to throttle here.
        if (currentPhase !== 'ANSWER_REVEAL') return;
        if (!els.reactionLayer || !emoji) return;
        var el = document.createElement('div');
        el.className = 'floating-reaction-board';
        el.textContent = emoji;
        el.style.left = (6 + Math.random() * 84) + '%';
        els.reactionLayer.appendChild(el);
        setTimeout(function () {
            if (el.parentNode) el.parentNode.removeChild(el);
        }, 3200);
    }

    function handleReactionBonus(msg) {
        // The +1s are already awarded and already in this payload's
        // leaderboard — render it now instead of letting the number lag the
        // animation until the next game_state frame.
        if (msg.leaderboard) renderLeaderboard(els.gameLeaderboard, msg.leaderboard);
    }

    function renderLeaderboard(container, players) {
        if (!container) return;
        // #741: remembered so an expiring steal chip can repaint the same rows
        // without waiting for the next frame from the server.
        if (container === els.gameLeaderboard) _lastGameLeaderboard = players;
        // Same row on all three surfaces (#787); the steal chip is the only
        // thing this page hangs off it.
        container.innerHTML = window.QuizifyRenderShared.leaderboardRowsHtml(players, {
            afterScore: function (p) { return scoreDeltaHtml(p.name); }
        });
    }

    function renderPodium(container, podium) {
        if (!container) return;
        var ordered = [];
        if (podium[1]) ordered.push(Object.assign({}, podium[1], { place: 2 }));
        if (podium[0]) ordered.push(Object.assign({}, podium[0], { place: 1 }));
        if (podium[2]) ordered.push(Object.assign({}, podium[2], { place: 3 }));

        var barClass = { 1: 'first', 2: 'second', 3: 'third' };
        var pointsShort = _t('leaderboard.pointsShort');
        var champ = podium[0];
        var championLabel = _t('leaderboard.champion');
        if (championLabel === 'leaderboard.champion') championLabel = 'Champion';

        // Champion title block (only when there's a winner).
        var titleHtml = '';
        if (champ) {
            titleHtml =
                '<div class="podium-title">' + escapeHtml(championLabel) + '</div>' +
                '<div class="podium-champion-name">' + escapeHtml(champ.name) + '</div>';
        }

        // Shelf row: 2 — 1 — 3.
        // The plank itself shows the rank number ("numbers speak" per DESIGN.md).
        var planks = ordered
            .map(function (p) {
                return '<div class="podium-place">' +
                    '<div class="podium-name">' + escapeHtml(p.name) + '</div>' +
                    '<div class="podium-score">' + p.score + ' ' + escapeHtml(pointsShort) + '</div>' +
                    '<div class="podium-bar ' + (barClass[p.place] || '') + '">' + p.place + '</div>' +
                    '</div>';
            })
            .join('');

        container.innerHTML = titleHtml + '<div class="podium">' + planks + '</div>';
    }

    function escapeHtml(text) {
        return QuizifyUtils.escapeHtml(text);
    }

    // ---- QR Code ----
    var _qrInstance = null;
    function generateQR(url) {
        var container = document.getElementById('qr-container');
        if (!container) return;
        container.innerHTML = '';
        // #620: the address in plain text next to the code, always — not only
        // in the branch below where the QR library itself is missing. The
        // failure this covers is a phone that cannot reach the address, and
        // the code renders perfectly in that case.
        var urlEl = document.getElementById('admin-join-url');
        // location.host, not a scheme-stripping regex — see the note in
        // dashboard.html's renderLobbyQr and the #540 guard.
        if (urlEl) urlEl.textContent = window.location.host + '/quizify/player';
        if (typeof QRCode !== 'undefined') {
            _qrInstance = new QRCode(container, {
                text: url, width: 180, height: 180,
                colorDark: '#0b0e1a', colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M,
            });
        } else {
            container.innerHTML = '<div style="padding:20px;word-break:break-all;font-size:12px;">' + url + '</div>';
        }
    }

    // ---- Admin Join Modal (Start Game flow) ----

    function openAdminJoinModal(mode) {
        // mode: 'start' — submit will send start_game + join the admin.
        //       'join'  — submit will ONLY register the admin as a player
        //                 (game stays in LOBBY). Used by the "Als Spieler
        //                 beitreten" button so admin can join without
        //                 starting the game.
        _adminModalMode = mode === 'join' ? 'join' : 'start';
        var isStart = _adminModalMode === 'start';
        var titleEl = document.getElementById('admin-join-modal-title');
        var subtitleEl = document.getElementById('admin-join-modal-subtitle');
        var btnLabelEl = document.getElementById('admin-join-btn-label');
        if (titleEl) titleEl.textContent = _t(isStart ? 'admin.joinModalTitleStart' : 'admin.joinModalTitleJoin');
        if (subtitleEl) subtitleEl.textContent = _t(isStart ? 'admin.joinModalSubtitleStart' : 'admin.joinModalSubtitleJoin');
        if (btnLabelEl) btnLabelEl.textContent = _t(isStart ? 'admin.joinModalBtnStart' : 'admin.joinModalBtnJoin');
        if (els.adminJoinModal) els.adminJoinModal.classList.remove('hidden');
        if (els.adminNameInput) {
            els.adminNameInput.value = '';
            setTimeout(function() { els.adminNameInput.focus(); }, 100);
        }
        if (els.adminJoinBtn) els.adminJoinBtn.disabled = true;
    }

    function closeAdminJoinModal() {
        if (els.adminJoinModal) els.adminJoinModal.classList.add('hidden');
    }

    // Active mode for the admin-join modal \u2014 set by openAdminJoinModal,
    // read by the submit handler so it knows whether to send start_game
    // or just register the admin as a player.
    var _adminModalMode = 'start';

    function _readName() {
        var name = els.adminNameInput ? els.adminNameInput.value.trim() : '';
        if (!name) {
            if (els.adminNameInput) {
                els.adminNameInput.style.border = '2px solid #D65858';
                setTimeout(function() { els.adminNameInput.style.border = ''; }, 1500);
            }
            return null;
        }
        return name;
    }

    // TTS narration (#281) state (TTS_STORAGE_KEY / TTS_DEFAULTS / _ttsEls) is
    // declared earlier — before _initTtsToggles() runs at init.

    function _loadTtsConfig() {
        var cfg = {
            enabled: TTS_DEFAULTS.enabled,
            announce_question: TTS_DEFAULTS.announce_question,
            announce_options: TTS_DEFAULTS.announce_options,
            announce_reveal: TTS_DEFAULTS.announce_reveal,
            announce_standings: TTS_DEFAULTS.announce_standings,
            announce_join: TTS_DEFAULTS.announce_join,
            announce_countdown: TTS_DEFAULTS.announce_countdown,
            announce_milestone: TTS_DEFAULTS.announce_milestone,
            tts_entity: TTS_DEFAULTS.tts_entity,
            media_player: TTS_DEFAULTS.media_player,
        };
        try {
            var raw = localStorage.getItem(TTS_STORAGE_KEY);
            if (raw) {
                var saved = JSON.parse(raw);
                if (saved && typeof saved === 'object') {
                    if (typeof saved.enabled === 'boolean') cfg.enabled = saved.enabled;
                    if (typeof saved.announce_question === 'boolean') cfg.announce_question = saved.announce_question;
                    if (typeof saved.announce_options === 'boolean') cfg.announce_options = saved.announce_options;
                    if (typeof saved.announce_reveal === 'boolean') cfg.announce_reveal = saved.announce_reveal;
                    if (typeof saved.announce_standings === 'boolean') cfg.announce_standings = saved.announce_standings;
                    if (typeof saved.announce_join === 'boolean') cfg.announce_join = saved.announce_join;
                    if (typeof saved.announce_countdown === 'boolean') cfg.announce_countdown = saved.announce_countdown;
                    if (typeof saved.announce_milestone === 'boolean') cfg.announce_milestone = saved.announce_milestone;
                    if (typeof saved.tts_entity === 'string') cfg.tts_entity = saved.tts_entity;
                    if (typeof saved.media_player === 'string') cfg.media_player = saved.media_player;
                }
            }
        } catch (e) { /* malformed/unavailable storage — fall back to defaults */ }
        return cfg;
    }

    function _saveTtsConfig() {
        var cfg = _readTtsConfig();
        try {
            localStorage.setItem(TTS_STORAGE_KEY, JSON.stringify(cfg));
        } catch (e) { /* storage unavailable — preference is session-only */ }
        // Push to the server too so the announcer is configured during the
        // pre-game lobby (player-join narration fires before start_game) (#281).
        _pushTtsConfig(cfg);
        _syncTtsChildState();
    }

    // Send the current TTS config to the server (no-op if the socket isn't
    // open yet — onopen re-sends after admin_connect).
    function _pushTtsConfig(cfg) {
        send('configure_tts', cfg || _readTtsConfig());
    }

    // Variant B (#281): dim + disable the child event toggles when the master
    // switch is off, so the master→child hierarchy is unmistakable.
    function _syncTtsChildState() {
        var on = _ttsEls.enable ? !!_ttsEls.enable.checked : false;
        var sub = document.getElementById('tts-children');
        if (sub) sub.classList.toggle('is-disabled', !on);
        ['question', 'options', 'reveal', 'standings', 'join', 'countdown'].forEach(function (k) {
            if (_ttsEls[k]) _ttsEls[k].disabled = !on;
        });
    }

    function _readTtsConfig() {
        return {
            enabled: _ttsEls.enable ? !!_ttsEls.enable.checked : TTS_DEFAULTS.enabled,
            announce_question: _ttsEls.question ? !!_ttsEls.question.checked : TTS_DEFAULTS.announce_question,
            announce_options: _ttsEls.options ? !!_ttsEls.options.checked : TTS_DEFAULTS.announce_options,
            announce_reveal: _ttsEls.reveal ? !!_ttsEls.reveal.checked : TTS_DEFAULTS.announce_reveal,
            announce_standings: _ttsEls.standings ? !!_ttsEls.standings.checked : TTS_DEFAULTS.announce_standings,
            announce_join: _ttsEls.join ? !!_ttsEls.join.checked : TTS_DEFAULTS.announce_join,
            announce_countdown: _ttsEls.countdown ? !!_ttsEls.countdown.checked : TTS_DEFAULTS.announce_countdown,
            announce_milestone: _ttsEls.milestone ? !!_ttsEls.milestone.checked : TTS_DEFAULTS.announce_milestone,
            tts_entity: _ttsEls.engine ? _ttsEls.engine.value : TTS_DEFAULTS.tts_entity,
            media_player: _ttsEls.speaker ? _ttsEls.speaker.value : TTS_DEFAULTS.media_player,
        };
    }

    // Fill a <select> with entity options, preserving (and restoring) the saved
    // selection. Graceful empty/error fallback: a single disabled "no entities"
    // option so the host knows to configure entities in HA (Beatify pattern).
    // `noneKey` overrides the i18n key of that fallback option (the House panel
    // (#494) passes its own); it defaults to the TTS key so the #281 callers
    // keep working unchanged.
    function _populateEntitySelect(sel, entities, savedValue, noneKey) {
        if (!sel) return;
        // The leading "Use default" option is authored in admin.html; keep it
        // and rebuild only the entity options after it.
        while (sel.options.length > 1) sel.remove(1);
        if (!entities || !entities.length) {
            var none = document.createElement('option');
            none.value = '';
            none.disabled = true;
            none.textContent = _t(noneKey || 'setup.tts.noentities');
            sel.appendChild(none);
            sel.value = '';
            return;
        }
        entities.forEach(function (ent) {
            var opt = document.createElement('option');
            opt.value = ent.entity_id;
            opt.textContent = ent.friendly_name || ent.entity_id;
            sel.appendChild(opt);
        });
        // Restore the saved selection if it still exists; else fall to default.
        if (savedValue && Array.prototype.some.call(sel.options, function (o) {
            return o.value === savedValue;
        })) {
            sel.value = savedValue;
        } else {
            sel.value = '';
        }
    }

    function _loadTtsEntities(cfg) {
        // #356: the tts-entities endpoint is admin-token gated. Send the
        // session token the admin page already holds.
        _adminFetch('/api/quizify/tts-entities')
            .then(function (resp) { return resp.ok ? resp.json() : null; })
            .then(function (data) {
                if (!data) {
                    // No token yet (401) or an error. Show the "None found"
                    // fallback WITHOUT marking loaded, so the refetch in
                    // handleGameState retries once the admin token arrives —
                    // but never over a list that already loaded (#524). A
                    // request that failed learned nothing about the host's
                    // entities and must not overwrite an answer that did.
                    if (_ttsEntitiesLoaded) return;
                    _populateEntitySelect(_ttsEls.engine, null, cfg.tts_entity);
                    _populateEntitySelect(_ttsEls.speaker, null, cfg.media_player);
                    return;
                }
                _ttsEntitiesLoaded = true;
                _populateEntitySelect(_ttsEls.engine, data.tts, cfg.tts_entity);
                _populateEntitySelect(_ttsEls.speaker, data.media_players, cfg.media_player);
            })
            .catch(function (e) {
                console.warn('[quizify] tts-entities fetch failed:', e);
                if (_ttsEntitiesLoaded) return;   // #524, see above
                _populateEntitySelect(_ttsEls.engine, null, cfg.tts_entity);
                _populateEntitySelect(_ttsEls.speaker, null, cfg.media_player);
            });
    }

    function _initTtsToggles() {
        _ttsEls = {
            enable: document.getElementById('tts-enable-toggle'),
            question: document.getElementById('tts-announce-question'),
            options: document.getElementById('tts-announce-options'),
            reveal: document.getElementById('tts-announce-reveal'),
            standings: document.getElementById('tts-announce-standings'),
            join: document.getElementById('tts-announce-join'),
            countdown: document.getElementById('tts-announce-countdown'),
            milestone: document.getElementById('tts-announce-milestone'),
            engine: document.getElementById('tts-engine-select'),
            // #525: one speaker for the whole game. The control moved out of
            // #tts-children (that container goes pointer-events:none when
            // narration is off, and this speaker also carries the House sound
            // effects), but it is still stored as quizify_tts.media_player —
            // the single source of truth both panels resolve against.
            speaker: document.getElementById('game-speaker-select'),
        };
        var cfg = _loadTtsConfig();
        if (_ttsEls.enable) _ttsEls.enable.checked = cfg.enabled;
        if (_ttsEls.question) _ttsEls.question.checked = cfg.announce_question;
        if (_ttsEls.options) _ttsEls.options.checked = cfg.announce_options;
        if (_ttsEls.reveal) _ttsEls.reveal.checked = cfg.announce_reveal;
        if (_ttsEls.standings) _ttsEls.standings.checked = cfg.announce_standings;
        if (_ttsEls.join) _ttsEls.join.checked = cfg.announce_join;
        if (_ttsEls.countdown) _ttsEls.countdown.checked = cfg.announce_countdown;
        if (_ttsEls.milestone) _ttsEls.milestone.checked = cfg.announce_milestone;
        ['enable', 'question', 'options', 'reveal', 'standings', 'join', 'countdown', 'milestone', 'engine', 'speaker'].forEach(function (k) {
            if (_ttsEls[k]) on(_ttsEls[k], 'change', _saveTtsConfig);
        });
        // Reflect the master→child enabled/dimmed state (Variant B, #281).
        _syncTtsChildState();
        // NO entity fetch here (#524). The endpoint is admin-token gated and
        // the token only arrives over the WebSocket, so a fetch at page-init
        // can never carry one — sessionStorage is per-tab and empty on every
        // fresh tab, making the 401 a certainty rather than a risk. Its late
        // failure handler then repainted the "None found" fallback OVER the
        // lists the admin-connect frame had already delivered. The lists ride
        // that frame (#502); handleGameState populates them, and only an
        // older server without them falls back to the HTTP fetch — by which
        // point the token exists.
    }

    // ---- House Plays Along (#494), Variant D "Presets" ----
    // State (HOUSE_STORAGE_KEY / HOUSE_PRESETS / HOUSE_DEFAULTS / _houseEls / …)
    // is declared earlier — before _initHouseToggles() runs at init.

    function _loadHouseConfig() {
        var cfg = {
            enabled: HOUSE_DEFAULTS.enabled,
            preset: HOUSE_DEFAULTS.preset,
            light_entities: HOUSE_DEFAULTS.light_entities.slice(),
            media_player: HOUSE_DEFAULTS.media_player,
            winner_scene_entity: HOUSE_DEFAULTS.winner_scene_entity,
        };
        HOUSE_EFFECT_KEYS.forEach(function (k) { cfg[k] = HOUSE_DEFAULTS[k]; });
        try {
            var raw = localStorage.getItem(HOUSE_STORAGE_KEY);
            if (raw) {
                var saved = JSON.parse(raw);
                if (saved && typeof saved === 'object') {
                    if (typeof saved.enabled === 'boolean') cfg.enabled = saved.enabled;
                    HOUSE_EFFECT_KEYS.forEach(function (k) {
                        if (typeof saved[k] === 'boolean') cfg[k] = saved[k];
                    });
                    if (Array.isArray(saved.light_entities)) {
                        cfg.light_entities = saved.light_entities.filter(function (e) {
                            return typeof e === 'string' && e;
                        });
                    }
                    // #525: `media_player_override` is authoritative once it
                    // exists. A setup written before this change only has the
                    // legacy `media_player`, which held a resolved speaker —
                    // read it as the candidate override and let
                    // _migrateSpeakerSplit decide whether it means anything.
                    if (typeof saved.media_player_override === 'string') {
                        cfg.media_player = saved.media_player_override;
                    } else if (typeof saved.media_player === 'string') {
                        cfg.media_player = saved.media_player;
                    }
                    if (typeof saved.winner_scene_entity === 'string') {
                        cfg.winner_scene_entity = saved.winner_scene_entity;
                    }
                }
            }
        } catch (e) { /* malformed/unavailable storage — fall back to defaults */ }
        // The stored `preset` is never trusted: it is derived from the toggles,
        // so a hand-edited storage entry can't show a preset that doesn't match.
        cfg.preset = _detectHousePreset(cfg);
        return cfg;
    }

    function _saveHouseConfig() {
        var cfg = _readHouseConfig();
        _houseSaved = cfg;
        try {
            // #525: persist the OVERRIDE, not the resolved speaker. Storing the
            // resolved value would look like a deliberate second speaker on the
            // next page load: change the game speaker afterwards and the two
            // stored values diverge, so the migration would resurrect a split
            // the host never asked for. `media_player` stays in the object for
            // the server payload; `media_player_override` is what survives.
            var stored = {};
            Object.keys(cfg).forEach(function (k) { stored[k] = cfg[k]; });
            stored.media_player_override = _rawHouseSpeakerOverride();
            localStorage.setItem(HOUSE_STORAGE_KEY, JSON.stringify(stored));
        } catch (e) { /* storage unavailable — preference is session-only */ }
        // Push to the server too, so the effects are configured during the
        // pre-game lobby (before start_game) (#494).
        _pushHouseConfig(cfg);
        _syncHouseChildState();
    }

    // Send the current house config to the server (no-op if the socket isn't
    // open yet — onopen re-sends after admin_connect).
    function _pushHouseConfig(cfg) {
        send('configure_house', cfg || _readHouseConfig());
    }

    // Dim + disable everything below the master switch when it is off (same
    // .is-disabled pattern as _syncTtsChildState): presets, the advanced
    // section and the entity pickers.
    function _syncHouseChildState() {
        var on = _houseEls.enable ? !!_houseEls.enable.checked : false;
        var sub = _houseEls.children;
        if (sub) {
            sub.classList.toggle('is-disabled', !on);
            sub.querySelectorAll('input, select, button').forEach(function (el) {
                el.disabled = !on;
            });
        }
    }

    // Which preset (if any) does this exact set of effect toggles match?
    // Returns 'custom' when it matches none.
    function _detectHousePreset(cfg) {
        var names = Object.keys(HOUSE_PRESETS);
        for (var i = 0; i < names.length; i++) {
            var map = HOUSE_PRESETS[names[i]];
            var hit = HOUSE_EFFECT_KEYS.every(function (k) {
                return !!cfg[k] === !!map[k];
            });
            if (hit) return names[i];
        }
        return 'custom';
    }

    // Highlight the segment for `preset` ('custom' → none highlighted) and
    // swap the one-line hint under the control. The hint keeps a live
    // data-i18n attribute so a mid-session language switch re-translates it.
    function _setHousePresetActive(preset) {
        _housePreset = preset;
        if (_houseEls.presets) {
            _houseEls.presets.querySelectorAll('.setup-house-preset').forEach(function (btn) {
                var isOn = btn.dataset.preset === preset;
                btn.classList.toggle('is-active', isOn);
                btn.setAttribute('aria-pressed', isOn ? 'true' : 'false');
            });
        }
        var hintKey = HOUSE_PRESET_HINTS[preset] || HOUSE_PRESET_HINTS.custom;
        if (_houseEls.presetHint) {
            _houseEls.presetHint.setAttribute('data-i18n', hintKey);
            _houseEls.presetHint.textContent = _t(hintKey);
        }
    }

    // Tapping a preset writes the WHOLE effect-toggle set. The entity pickers
    // are untouched — a preset only ever decides which effects fire.
    function _applyHousePreset(preset) {
        var map = HOUSE_PRESETS[preset];
        if (!map) return;
        HOUSE_EFFECT_KEYS.forEach(function (k) {
            if (_houseEls[k]) _houseEls[k].checked = !!map[k];
        });
        _setHousePresetActive(preset);
        _saveHouseConfig();
    }

    // Any manual toggle in the advanced section re-derives the preset — which
    // is 'custom' unless the host happened to land exactly on a preset's map.
    // We never write toggles back here, so a manual choice is never overwritten.
    function _onHouseEffectChange() {
        var effects = {};
        HOUSE_EFFECT_KEYS.forEach(function (k) {
            effects[k] = _houseEls[k] ? !!_houseEls[k].checked : !!HOUSE_DEFAULTS[k];
        });
        _setHousePresetActive(_detectHousePreset(effects));
        _saveHouseConfig();
    }

    // #525: resolve the House speaker. An empty override follows the game-wide
    // speaker (step 7); a non-empty one wins. Kept as a function rather than an
    // inline `||` because both _readHouseConfig and the migration need the same
    // rule, and getting the two out of step is precisely the class of bug this
    // issue was about.
    function _resolveHouseSpeaker(override) {
        if (override) return override;
        return _ttsEls && _ttsEls.speaker
            ? _ttsEls.speaker.value
            : (_loadTtsConfig().media_player || '');
    }

    // The RAW override, before _resolveHouseSpeaker folds in the game speaker.
    // Restoring the picker must use this: feeding it the resolved value would
    // pre-select the game speaker as an explicit override and turn "follow"
    // into a split on the next save — the very divergence this issue removed.
    function _rawHouseSpeakerOverride() {
        if (_houseEntitiesLoaded && _houseEls.speaker) return _houseEls.speaker.value;
        return (_houseSaved && _houseSaved.media_player) || '';
    }

    // #525 migration. Before this change the host answered the speaker question
    // twice, so a stored setup can legitimately hold two different values.
    // Folding the question into one control must not silently reassign one of
    // them: an existing divergence is kept as the override AND the disclosure
    // holding it is opened, so the split is visible rather than inherited in
    // the dark. A stored value that merely repeats the game speaker carries no
    // intent and is normalised to "follow".
    function _migrateSpeakerSplit() {
        if (!_houseEls.speaker) return;
        var ttsSpeaker = _loadTtsConfig().media_player || '';
        var houseSpeaker = (_houseSaved && _houseSaved.media_player) || '';
        if (!houseSpeaker || houseSpeaker === ttsSpeaker) {
            if (_houseSaved) _houseSaved.media_player = '';
            return;
        }
        _toggleHouseAdvanced(true);
    }

    function _toggleHouseAdvanced(force) {
        var btn = _houseEls.advBtn;
        var panel = _houseEls.advanced;
        if (!btn || !panel) return;
        var open = (force === undefined)
            ? btn.getAttribute('aria-expanded') !== 'true'
            : !!force;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        panel.hidden = !open;
    }

    function _readHouseConfig() {
        var saved = _houseSaved || HOUSE_DEFAULTS;
        var cfg = {
            enabled: _houseEls.enable ? !!_houseEls.enable.checked : HOUSE_DEFAULTS.enabled,
            // Frontend-only — the backend ignores it.
            preset: _housePreset,
            // Until the pickers have been populated from the server they hold
            // no options yet, so read through to the persisted selection rather
            // than reporting an empty one (which would mean "use the
            // config-entry default" and silently drop the host's choice).
            light_entities: _houseLightsRendered
                ? _readHouseLightEntities()
                : (saved.light_entities || []).slice(),
            // #525: quizify_house.media_player is now an *override*, not a
            // second speaker question — empty means "follow the game speaker
            // from step 7". The server keeps receiving one resolved entity id,
            // so the wire format is unchanged; only the UI collapsed.
            media_player: _resolveHouseSpeaker(
                _houseEntitiesLoaded && _houseEls.speaker
                    ? _houseEls.speaker.value
                    : saved.media_player
            ),
            winner_scene_entity: _houseEntitiesLoaded && _houseEls.scene
                ? _houseEls.scene.value
                : saved.winner_scene_entity,
        };
        HOUSE_EFFECT_KEYS.forEach(function (k) {
            cfg[k] = _houseEls[k] ? !!_houseEls[k].checked : !!HOUSE_DEFAULTS[k];
        });
        return cfg;
    }

    function _readHouseLightEntities() {
        var box = _houseEls.lightList;
        if (!box) return [];
        var out = [];
        box.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
            if (cb.checked) out.push(cb.value);
        });
        return out;
    }

    // Multi-choice light picker: a scrollable checkbox list (a native
    // <select multiple> is miserable on a phone). Uses the shared
    // .toggle-compact / .toggle-switch-compact switch markup so it reads as
    // part of the same panel.
    function _renderHouseLightList(lights, savedValue) {
        var box = _houseEls.lightList;
        if (!box) return;
        var selected = savedValue || [];
        var on_ = _houseEls.enable ? !!_houseEls.enable.checked : false;
        box.textContent = '';
        if (!lights || !lights.length) {
            var empty = document.createElement('div');
            empty.className = 'setup-house-empty';
            empty.setAttribute('data-i18n', 'setup.house.nolights');
            empty.textContent = _t('setup.house.nolights');
            box.appendChild(empty);
            // A real (if empty) list is "rendered": the host has no lights, so
            // an empty selection is the truth. A failed fetch passes null and
            // leaves the flag alone, so the saved selection survives.
            _houseLightsRendered = Array.isArray(lights);
            return;
        }
        lights.forEach(function (ent, i) {
            var cbId = 'house-light-cb-' + i;
            var label = document.createElement('label');
            label.className = 'toggle-compact setup-house-toggle setup-house-light';
            label.setAttribute('for', cbId);
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.id = cbId;
            cb.value = ent.entity_id;
            cb.checked = selected.indexOf(ent.entity_id) !== -1;
            cb.disabled = !on_;
            var sw = document.createElement('span');
            sw.className = 'toggle-switch-compact';
            sw.setAttribute('aria-hidden', 'true');
            var txt = document.createElement('span');
            txt.className = 'toggle-label';
            txt.textContent = ent.friendly_name || ent.entity_id;
            txt.title = ent.entity_id;
            label.appendChild(cb);
            label.appendChild(sw);
            label.appendChild(txt);
            on(cb, 'change', _saveHouseConfig);
            box.appendChild(label);
        });
        _houseLightsRendered = true;
    }

    // payload = {lights, media_players, scenes}, each item {entity_id,
    // friendly_name} — straight off the admin-connect frame or the HTTP
    // fallback endpoint.
    function _populateHouseEntities(payload, cfg) {
        var data = payload || {};
        _renderHouseLightList(data.lights || [], cfg.light_entities);
        _houseEntitiesLoaded = true;
        // #525: restore the raw override, never the resolved value.
        _populateEntitySelect(_houseEls.speaker, data.media_players || [], _rawHouseSpeakerOverride(), 'setup.house.noentities');
        _populateEntitySelect(_houseEls.scene, data.scenes || [], cfg.winner_scene_entity, 'setup.house.noentities');
        _syncHouseChildState();
    }

    // HTTP fallback for an older server that doesn't put house_entities on the
    // admin frame. Admin-token gated like /api/quizify/tts-entities (#356).
    function _loadHouseEntities(cfg) {
        _adminFetch('/api/quizify/house-entities')
            .then(function (resp) { return resp.ok ? resp.json() : null; })
            .then(function (data) {
                if (!data) {
                    // No token yet (401) or an error — show the "none found"
                    // fallbacks WITHOUT marking loaded, so the refetch in
                    // handleGameState retries once the admin token arrives.
                    // Never over an already-loaded list (#524/#527): a failed
                    // fetch wiping the rendered light list is exactly how the
                    // party-light picker came up empty on a host with 72
                    // lights.
                    if (_houseEntitiesLoaded) return;
                    _renderHouseLightList(null, cfg.light_entities);
                    _populateEntitySelect(_houseEls.speaker, null, _rawHouseSpeakerOverride(), 'setup.house.noentities');
                    _populateEntitySelect(_houseEls.scene, null, cfg.winner_scene_entity, 'setup.house.noentities');
                    return;
                }
                _populateHouseEntities(data, cfg);
            })
            .catch(function (e) {
                console.warn('[quizify] house-entities fetch failed:', e);
                if (_houseEntitiesLoaded) return;   // #524/#527, see above
                _renderHouseLightList(null, cfg.light_entities);
                _populateEntitySelect(_houseEls.speaker, null, _rawHouseSpeakerOverride(), 'setup.house.noentities');
                _populateEntitySelect(_houseEls.scene, null, cfg.winner_scene_entity, 'setup.house.noentities');
            });
    }

    function _initHouseToggles() {
        _houseEls = {
            enable: document.getElementById('house-enable-toggle'),
            children: document.getElementById('house-children'),
            presets: document.getElementById('house-presets'),
            presetHint: document.getElementById('house-preset-hint'),
            advBtn: document.getElementById('house-advanced-btn'),
            advanced: document.getElementById('house-advanced'),
            lightList: document.getElementById('house-light-list'),
            speaker: document.getElementById('house-speaker-select'),
            scene: document.getElementById('house-scene-select'),
            light_question: document.getElementById('house-light-question'),
            light_countdown: document.getElementById('house-light-countdown'),
            light_reveal: document.getElementById('house-light-reveal'),
            light_streak: document.getElementById('house-light-streak'),
            light_winner: document.getElementById('house-light-winner'),
            winner_scene: document.getElementById('house-winner-scene'),
            sfx_correct: document.getElementById('house-sfx-correct'),
            sfx_wrong: document.getElementById('house-sfx-wrong'),
            sfx_streak: document.getElementById('house-sfx-streak'),
            sfx_winner: document.getElementById('house-sfx-winner'),
        };
        if (!_houseEls.enable) return;  // panel not on this page
        var cfg = _loadHouseConfig();
        _houseSaved = cfg;
        _houseEls.enable.checked = cfg.enabled;
        HOUSE_EFFECT_KEYS.forEach(function (k) {
            if (_houseEls[k]) _houseEls[k].checked = !!cfg[k];
        });
        // Restore the preset the stored toggles resolve to (else 'custom').
        _setHousePresetActive(cfg.preset);

        on(_houseEls.enable, 'change', _saveHouseConfig);
        HOUSE_EFFECT_KEYS.forEach(function (k) {
            if (_houseEls[k]) on(_houseEls[k], 'change', _onHouseEffectChange);
        });
        if (_houseEls.presets) {
            _houseEls.presets.querySelectorAll('.setup-house-preset').forEach(function (btn) {
                on(btn, 'click', function () { _applyHousePreset(btn.dataset.preset); });
            });
        }
        if (_houseEls.advBtn) {
            on(_houseEls.advBtn, 'click', function () { _toggleHouseAdvanced(); });
            _toggleHouseAdvanced(false);  // collapsed by default
        }
        [_houseEls.speaker, _houseEls.scene].forEach(function (sel) {
            if (sel) on(sel, 'change', _saveHouseConfig);
        });
        // #525: decide what a stored two-speaker setup means BEFORE the state
        // sync, and open the disclosure if it holds a real divergence.
        _migrateSpeakerSplit();
        // Reflect the master→children enabled/dimmed state.
        _syncHouseChildState();
        // NO entity fetch here — same reasoning as the TTS panel (#524/#527):
        // the token-gated fetch cannot succeed at page-init, and its failure
        // handler wiped the light list the admin-connect frame had already
        // rendered. The lists ride that frame (#502/#494 Phase 4).
    }

    function _buildStartGamePayload() {
        var categoryPayload = selectedCategory === 'mixed'
            ? null
            : selectedCategory === 'multi'
                ? selectedCategories
                : selectedCategory;
        // Read the Lightning toggle live so a last-second flip is honoured even
        // if the change listener didn't fire (e.g. programmatic state).
        var lightningEl = document.getElementById('lightning-enabled-toggle');
        var lightningEnabled = lightningEl ? !!lightningEl.checked : selectedLightning;
        // Same live read for the Hot Seat auction (#616).
        var hotSeatEl = document.getElementById('hot-seat-enabled-toggle');
        var hotSeatEnabled = hotSeatEl ? !!hotSeatEl.checked : selectedHotSeat;
        // Same live read for power-ups and the final-round wager (#742).
        var powerupsEl = document.getElementById('powerups-enabled-toggle');
        var powerupsEnabled = powerupsEl ? !!powerupsEl.checked : selectedPowerups;
        var wagerEl = document.getElementById('wager-enabled-toggle');
        var wagerEnabled = wagerEl ? !!wagerEl.checked : selectedWager;
        return {
            category: categoryPayload,
            difficulty: selectedDifficulty === 'mixed' ? null : selectedDifficulty,
            num_rounds: selectedRounds,
            language: selectedLanguage,
            timer_duration: selectedTimer,
            lightning_enabled: lightningEnabled,
            hot_seat_enabled: hotSeatEnabled,
            powerups_enabled: powerupsEnabled,
            wager_enabled: wagerEnabled,
            // TTS narration toggles (#281), read live from the inputs.
            tts: _readTtsConfig(),
            // House Plays Along config (#494), read live from the inputs.
            house: _readHouseConfig(),
        };
    }

    function doStartGame() {
        // Mode "start" \u2014 admin enters name, server starts the game AND
        // registers them as a player. Used by the legacy "Mitspielen &
        // starten" CTA when admin wants one-tap join + start.
        var name = _readName();
        if (!name) return;

        if (els.adminJoinBtn) {
            els.adminJoinBtn.disabled = true;
            var btnLabelEl = document.getElementById('admin-join-btn-label');
            if (btnLabelEl) btnLabelEl.textContent = _t('admin.starting');
        }

        sessionStorage.setItem('quizify_admin_name', name);
        send('start_game', _buildStartGamePayload());

        // Safety timeout: if for any reason the server doesn't respond in
        // 3s, fall back to the old behavior so the user isn't stuck.
        setTimeout(function () {
            if (!_redirecting) {
                redirectToPlayer(name);
            }
        }, 3000);

        closeAdminJoinModal();
    }

    function doJoinAsPlayer() {
        // Mode "join" \u2014 register the admin as a player on the SAME
        // WebSocket the admin page already holds, then stay on the
        // admin lobby. The admin sees the QR + roster + Spiel-starten
        // button as the host; the redirect to /quizify/player only
        // happens once they press Spiel starten (handled by the
        // phase-change branch in handleGameState).
        //
        // Previous behavior redirected immediately, which surprised
        // hosts who wanted to keep monitoring the lobby (QR for
        // late-joiners) before tipping into gameplay.
        var name = _readName();
        if (!name) return;

        if (els.adminJoinBtn) {
            els.adminJoinBtn.disabled = true;
            var btnLabelEl = document.getElementById('admin-join-btn-label');
            if (btnLabelEl) btnLabelEl.textContent = _t('admin.starting');
        }

        // Mark that the admin claims this name as their player slot;
        // handleGameState reads this when the phase leaves LOBBY to
        // know it should redirect to /quizify/player so the admin can
        // actually answer questions.
        _adminJoinedAs = name;
        sessionStorage.setItem('quizify_admin_name', name);
        // #244: disable the trigger immediately so a fast second tap (before
        // the server's roster broadcast re-renders the lobby) can't open the
        // modal again and create a duplicate self-join.
        if (els.participateBtn) els.participateBtn.disabled = true;
        // #358: carry the admin session token so the server can authorise a
        // crown transfer from a stale (disconnected) admin slot — without it a
        // LAN client could seize the crown during a host reload.
        var _joinMsg = { name: name, is_admin: true };
        var _adminTok = QuizifyUtils.readAdminToken();
        if (_adminTok) _joinMsg.admin_token = _adminTok;
        send('join', _joinMsg);
        closeAdminJoinModal();
    }

    function doStartGameNoJoin() {
        // Admin clicks "Spiel starten" without joining as a player.
        // Server starts the game with whichever players are in the
        // lobby; admin stays on /quizify/admin as the pure TV host
        // view. No leaderboard entry for admin.
        send('start_game', _buildStartGamePayload());
    }

    function _submitAdminModal() {
        if (_adminModalMode === 'join') doJoinAsPlayer();
        else doStartGame();
    }

    function setupAdminJoinModal() {
        // Participate button — opens the modal in "join" mode (game
        // is in lobby, admin choosing to play along, no fresh start).
        on(els.participateBtn, 'click', function () {
            // #244: once the admin has self-joined as a player, re-tapping
            // must be a no-op — never open the modal again, otherwise the
            // host can create a duplicate/ghost player from one admin session.
            if (_adminJoinedAs) return;
            openAdminJoinModal('join');
        });
        on(els.adminCancelBtn, 'click', closeAdminJoinModal);

        var backdrop = els.adminJoinModal ? els.adminJoinModal.querySelector('.modal-backdrop') : null;
        if (backdrop) backdrop.addEventListener('click', closeAdminJoinModal);

        on(els.adminNameInput, 'input', function () {
            // Use the shared validator (utils.js) so the admin self-join
            // modal enforces the exact same name rule + limit as the player
            // join flow — no second hardcoded `length > 20` to drift.
            var result = window.QuizifyUtils.validateName(this.value);
            if (els.adminJoinBtn) els.adminJoinBtn.disabled = !result.valid;
            // Surface the (now localised) error in the dedicated slot —
            // only when the field is non-empty but invalid (i.e. too long);
            // an empty field just disables the button without nagging.
            var errEl = document.getElementById('admin-name-error');
            if (errEl) {
                if (!result.valid && this.value.trim()) {
                    errEl.textContent = result.error;
                    errEl.classList.remove('hidden');
                } else {
                    errEl.textContent = '';
                    errEl.classList.add('hidden');
                }
            }
        });

        on(els.adminNameInput, 'keydown', function (e) {
            if (e.key === 'Enter' && els.adminJoinBtn && !els.adminJoinBtn.disabled) {
                _submitAdminModal();
            }
        });

        on(els.adminJoinBtn, 'click', _submitAdminModal);
    }

    // ---- Safe event binding helper ----
    function on(id, event, fn) {
        var el = typeof id === 'string' ? document.getElementById(id) : id;
        if (el) el.addEventListener(event, fn);
    }

    // ---- Event listeners ----

    // Setup screen → Lobby screen (shows QR, waits for players)
    on(els.startGameBtn, 'click', function () {
        showView('lobby');
        initJoinUrl();
    });

    // Featured pack spotlight (hero) → SELECT/DESELECT the World Cup pack, the
    // same as the other category chips. It does NOT start the game; the host
    // picks packs (card + chips) and then taps "Start Game". Proxies to the
    // World Cup category chip so all existing selection logic runs unchanged.
    on(els.heroFeatureCard, 'click', function () {
        // #335: resolve the World Cup pack for the active language from the
        // data-driven grid rather than a hardcoded slug map.
        var target = _worldCupChipForLang();
        if (target) target.click();
        syncHeroFeatureCardState();
        syncHeroPackChips();
    });

    // Lobby → setup. Round-trip nav so the host can tweak settings
    // (difficulty, rounds, pack) after the QR is up without nuking
    // already-joined players. No WS reset — server state survives,
    // re-applying settings re-emits them on submit.
    on(els.lobbyBackBtn, 'click', function () {
        showView('setup');
    });

    // Lobby: "Spiel starten" — starts the game with players already in
    // the lobby. Does NOT auto-join the admin. If admin wants to play,
    // they click "Als Spieler beitreten" first (separate button) — that
    // route just registers them as a player without starting the game.
    // Splitting the actions fixes the "as soon as I join, the game
    // starts" surprise. Was openAdminJoinModal('start') before.
    on(els.startGameplayBtn, 'click', function () {
        if (els.startGameplayBtn) {
            els.startGameplayBtn.disabled = true;
            setTimeout(function () { els.startGameplayBtn.disabled = false; }, 3000);
        }
        doStartGameNoJoin();
    });

    // In-game controls (only shown if admin stays on admin.html without redirect)
    // Button-disable on click to prevent double-advance (#17 in logical review).
    // Re-enabled by handleGameState / handleQuestionStarted when the next phase arrives.
    function _debouncedSend(btn, msgType) {
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        // #621: only hold the button if the command actually left. Disabling
        // for 1.5s after an undelivered send tells the host "received, working
        // on it" — the one thing that did not happen.
        if (!send(msgType, {})) {
            btn.disabled = false;
            return;
        }
        // Re-enable after 1.5s as a safety net (in case server doesn't respond).
        setTimeout(function () { btn.disabled = false; }, 1500);
    }
    on(els.nextQuestionBtn, 'click', function () { _debouncedSend(els.nextQuestionBtn, 'next_question'); });

    // #479: focus management for the destructive confirm dialogs. Opening one
    // by toggling .hidden left keyboard focus behind the backdrop; on close it
    // was never returned to the trigger. openConfirmModal moves focus to the
    // Cancel button (safe default for a destructive action) and remembers the
    // element that opened it; closeConfirmModal restores focus to that trigger.
    var _confirmModalTrigger = null;
    // #487: Tab-trap so keyboard focus stays inside the open aria-modal dialog.
    // Without this, Tab/Shift+Tab escaped behind the backdrop. We install a
    // keydown handler when a modal opens and remove it on close.
    var _confirmTrapHandler = null;

    function _getModalFocusable(modal) {
        var content = modal.querySelector('.modal-content') || modal;
        var nodes = content.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        var out = [];
        for (var i = 0; i < nodes.length; i++) {
            var el = nodes[i];
            if (!el.disabled && el.getAttribute('tabindex') !== '-1'
                && (el.offsetWidth > 0 || el.offsetHeight > 0 || el === document.activeElement)) {
                out.push(el);
            }
        }
        return out;
    }

    function openConfirmModal(modalId, cancelBtnId, triggerEl) {
        var modal = document.getElementById(modalId);
        if (!modal) return;
        _confirmModalTrigger = triggerEl
            || (document.activeElement && document.activeElement !== document.body
                ? document.activeElement
                : null);
        modal.classList.remove('hidden');
        var cancelBtn = document.getElementById(cancelBtnId);
        if (cancelBtn) setTimeout(function () { cancelBtn.focus(); }, 0);

        // Remove any stale trap before installing a fresh one.
        if (_confirmTrapHandler) {
            document.removeEventListener('keydown', _confirmTrapHandler, true);
            _confirmTrapHandler = null;
        }
        _confirmTrapHandler = function (e) {
            if (e.key !== 'Tab') return;
            var focusable = _getModalFocusable(modal);
            if (!focusable.length) return;
            var first = focusable[0];
            var last = focusable[focusable.length - 1];
            var active = document.activeElement;
            if (e.shiftKey) {
                if (active === first || !modal.contains(active)) {
                    e.preventDefault();
                    last.focus();
                }
            } else {
                if (active === last || !modal.contains(active)) {
                    e.preventDefault();
                    first.focus();
                }
            }
        };
        document.addEventListener('keydown', _confirmTrapHandler, true);
    }

    function closeConfirmModal(modalId) {
        var modal = document.getElementById(modalId);
        if (modal) modal.classList.add('hidden');
        if (_confirmTrapHandler) {
            document.removeEventListener('keydown', _confirmTrapHandler, true);
            _confirmTrapHandler = null;
        }
        var trigger = _confirmModalTrigger;
        _confirmModalTrigger = null;
        if (trigger && typeof trigger.focus === 'function') trigger.focus();
    }

    on(els.endGameBtn, 'click', function () {
        openConfirmModal('end-game-modal', 'end-game-cancel-btn', els.endGameBtn);
    });

    on('end-game-confirm-btn', 'click', function () {
        send('end_game', {});
        closeConfirmModal('end-game-modal');
    });
    on('end-game-cancel-btn', 'click', function () {
        closeConfirmModal('end-game-modal');
    });
    var endBackdrop = document.querySelector('#end-game-modal .modal-backdrop');
    if (endBackdrop) on(endBackdrop, 'click', function () {
        closeConfirmModal('end-game-modal');
    });

    on(els.newGameBtn, 'click', function () { send('reset_game', {}); });

    // Lightning Round (issue #42 mechanics, #285 auto-trigger). It now fires
    // automatically mid-game and auto-advances, so there is no manual start /
    // end control. The recap's only action resumes the paused main game via
    // the normal next_question advance (server: resume_after_lightning).
    on(els.adminLightningContinueBtn, 'click', function () { send('next_question', {}); });

    // ---- Reset Game button (header) ----
    on(els.resetGameBtn, 'click', function () {
        openConfirmModal('reset-game-modal', 'reset-game-cancel-btn', els.resetGameBtn);
    });
    on('reset-game-confirm-btn', 'click', function () {
        send('reset_game', {});
        closeConfirmModal('reset-game-modal');
    });
    on('reset-game-cancel-btn', 'click', function () {
        closeConfirmModal('reset-game-modal');
    });
    var resetBackdrop = document.querySelector('#reset-game-modal .modal-backdrop');
    if (resetBackdrop) on(resetBackdrop, 'click', function () {
        closeConfirmModal('reset-game-modal');
    });

    // ---- Kick player confirmation modal (#480) ----
    // Replaces the raw window.confirm() with the themed btn-danger modal used by
    // end/reset. The kick action itself is unchanged (send('kick_player', ...)).
    var _pendingKickName = null;

    function openKickModal(name, triggerEl) {
        _pendingKickName = name;
        var textEl = document.getElementById('kick-player-modal-text');
        if (textEl) {
            var tmpl = _t('admin.kickConfirm') || 'Remove {name} from the lobby?';
            textEl.textContent = tmpl.replace('{name}', name);
        }
        var modal = document.getElementById('kick-player-modal');
        if (!modal) {
            // Fallback: markup missing → keep the old confirm() so kicks still work.
            var msg = (_t('admin.kickConfirm') || ('Remove ' + name + ' from the lobby?')).replace('{name}', name);
            if (window.confirm(msg)) send('kick_player', { player_name: name });
            _pendingKickName = null;
            return;
        }
        openConfirmModal('kick-player-modal', 'kick-player-cancel-btn', triggerEl);
    }

    on('kick-player-confirm-btn', 'click', function () {
        if (_pendingKickName) send('kick_player', { player_name: _pendingKickName });
        _pendingKickName = null;
        closeConfirmModal('kick-player-modal');
    });
    on('kick-player-cancel-btn', 'click', function () {
        _pendingKickName = null;
        closeConfirmModal('kick-player-modal');
    });
    var kickBackdrop = document.querySelector('#kick-player-modal .modal-backdrop');
    if (kickBackdrop) on(kickBackdrop, 'click', function () {
        _pendingKickName = null;
        closeConfirmModal('kick-player-modal');
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            closeAdminJoinModal();
            // Restore focus to the trigger for whichever confirm dialog is open.
            var confirmIds = ['end-game-modal', 'reset-game-modal', 'kick-player-modal'];
            for (var i = 0; i < confirmIds.length; i++) {
                var m = document.getElementById(confirmIds[i]);
                if (m && !m.classList.contains('hidden')) {
                    _pendingKickName = null;
                    closeConfirmModal(confirmIds[i]);
                }
            }
        }
    });

    // The Android HA Companion WebView silently swallows target="_blank"
    // (see launcher.html #348). Detect that context so we can navigate
    // window.location directly instead of relying on a dead new-tab link.
    function isAndroidCompanion() {
        var ua = navigator.userAgent || '';
        return /Android/i.test(ua) && /Home ?Assistant/i.test(ua);
    }

    // ---- Generate join URL ----
    var _statsLinkBound = false;

    function initStatsLink() {
        // #706: this used to be called from the "Open lobby" handler alone, so
        // the Stats button on the setup screen did nothing until the host had
        // opened the lobby once — and every further trip through the lobby
        // added another listener, so one tap opened one tab per visit. Bound
        // once, at load, for a button that is visible from the first paint.
        if (_statsLinkBound) return;
        var btn = document.getElementById('setup-stats-btn');
        if (!btn) return;
        _statsLinkBound = true;
        btn.addEventListener('click', function () {
            // Deliberately NO ?token= (#359, #608): analytics.html reads the
            // admin token from localStorage via QuizifyUtils.readAdminToken()
            // and only falls back to the URL param. Appending it here would put
            // a full-control credential back into browser history for no gain.
            var url = window.location.origin + '/quizify/analytics';
            // Android Companion swallows target="_blank" (#348/#377), so
            // navigate the frame there instead of opening a tab.
            if (isAndroidCompanion()) {
                window.location.href = url;
            } else {
                window.open(url, '_blank');
            }
        });
    }

    function initJoinUrl() {
        var joinUrl = window.location.origin + '/quizify/player';
        generateQR(joinUrl);
        if (els.dashboardLink) {
            var dashboardUrl = window.location.origin + '/quizify/dashboard';
            els.dashboardLink.href = dashboardUrl;
            // #622: the button never cast anything — it opened this URL in a
            // tab on the host's own phone. A first-time host tapped it, got the
            // TV view at 390px in their hand, and no hint how it reaches the
            // television. (The one external bug report we have, #586, said
            // exactly this and it was filed as an unexplained symptom.)
            //
            // Tapping now explains the actual mechanic and offers the address
            // to type on the TV. The direct open survives as the secondary
            // action, for a host who IS sitting at the TV device.
            els.dashboardLink.addEventListener('click', function (evt) {
                evt.preventDefault();
                openCastModal(dashboardUrl, els.dashboardLink);
            });
        }
    }

    function openCastModal(dashboardUrl, triggerEl) {
        var urlEl = document.getElementById('cast-tv-url');
        // location.host, not a scheme regex — see the #540 guard note in
        // generateQR. Same reason, same shape.
        if (urlEl) urlEl.textContent = window.location.host + '/quizify/dashboard';

        var openBtn = document.getElementById('cast-tv-open-btn');
        if (openBtn && !openBtn._castWired) {
            openBtn._castWired = true;
            openBtn.addEventListener('click', function () {
                closeConfirmModal('cast-tv-modal');
                // Android Companion swallows target="_blank" (#377), so
                // navigate the frame there instead of opening a tab.
                if (isAndroidCompanion()) {
                    window.location.href = dashboardUrl;
                } else {
                    window.open(dashboardUrl, '_blank');
                }
            });
        }

        var modal = document.getElementById('cast-tv-modal');
        if (!modal) {
            // Markup missing → fall back to the old behaviour rather than
            // leaving the host with a button that does nothing at all.
            if (isAndroidCompanion()) {
                window.location.href = dashboardUrl;
            } else {
                window.open(dashboardUrl, '_blank');
            }
            return;
        }
        openConfirmModal('cast-tv-modal', 'cast-tv-cancel-btn', triggerEl);
    }

    // ---- Error toast ----
    function showErrorToast(message) {
        var toast = document.getElementById('error-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'error-toast';
            toast.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#D65858;color:white;padding:10px 20px;border-radius:10px;font-size:0.85rem;z-index:9999;opacity:0;transition:opacity 0.3s;pointer-events:none;';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.style.opacity = '1';
        setTimeout(function () { toast.style.opacity = '0'; }, 3000);
    }

    // ---- Connection status ----
    // Soft Parlor palette: connected = sage (success/correct), warning = sun, error = warm brick.
    function updateConnectionStatus(status) {
        var indicator = document.getElementById('conn-status');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'conn-status';
            indicator.style.cssText = 'position:fixed;bottom:12px;right:12px;display:flex;align-items:center;gap:6px;font-size:0.75rem;color:#6E6A5C;z-index:100;';
            document.body.appendChild(indicator);
        }
        var colors = { connected: '#7FA897', reconnecting: '#E8C47F', disconnected: '#D66A6A' };
        var glow = { connected: 'rgba(127,168,151,0.45)', reconnecting: 'rgba(232,196,127,0.45)', disconnected: 'rgba(214,106,106,0.45)' };
        var color = colors[status] || '#6E6A5C';
        var glowColor = glow[status] || 'rgba(110,106,92,0.25)';
        var dot = '<span style="width:10px;height:10px;border-radius:50%;display:inline-block;flex:none;background:' +
            color + ';box-shadow:0 0 10px ' + glowColor + ';"></span>';
        // Visible label instead of a bare dot the host can't interpret: while
        // reconnecting/disconnected, show the i18n connection text (and, when
        // disconnected, a tappable retry affordance) so the host knows the
        // tablet is trying to recover and can force it (#290). The existing
        // connection.* keys are reused (no new i18n strings).
        if (status === 'connected') {
            indicator.style.cursor = '';
            indicator.onclick = null;
            indicator.innerHTML = dot;
            return;
        }
        if (status === 'disconnected') {
            indicator.style.cursor = 'pointer';
            indicator.setAttribute('role', 'button');
            indicator.onclick = function () {
                // Manual recovery: reset the budget and reconnect immediately.
                reconnectAttempts = 0;
                updateConnectionStatus('reconnecting');
                connect();
            };
            indicator.innerHTML = dot + '<span>' + _t('connection.retryConnection') + '</span>';
            return;
        }
        // reconnecting (or any other transient state)
        indicator.style.cursor = '';
        indicator.onclick = null;
        indicator.innerHTML = dot + '<span>' + _t('connection.reconnecting') + '</span>';
    }

    // ---- Init ----
    setupAdminJoinModal();

    if (window.QuizifyI18n) {
        // Apply selected language so admin sees correct labels on
        // first paint (default is German). Selecting a different
        // language chip via setupChips re-runs initPageTranslations.
        QuizifyI18n.init(selectedLanguage).then(function () {
            QuizifyI18n.initPageTranslations();
            updateSettingsSummary();
            updateCategorySummary();
            // Rebuild hero pack chips now that labels are translated (the
            // shared "Mixed" chip reads its translated name post-i18n).
            buildHeroPackChips();
        });
    } else {
        // No i18n: still populate the summary chip with the timer
        // value on first paint (was missing in v1.1.3).
        updateSettingsSummary();
        updateCategorySummary();
    }

    // #706: the Stats button sits on the setup screen, so it is bound here
    // rather than on the way into the lobby.
    initStatsLink();

    connect();
    updateConnectionStatus('reconnecting');

    // Reconnect when the host brings the admin tablet back to the foreground.
    // Mirrors the player view (player-core.js): the OS may have frozen/closed
    // the socket while the tab was hidden (e.g. during an HA restart), and the
    // attempt budget may already be exhausted — reset it and reconnect so the
    // host doesn't return to a permanently dead tab (#290).
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState !== 'visible') return;
        if (!ws || ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED) {
            reconnectAttempts = 0;
            updateConnectionStatus('reconnecting');
            connect();
        }
    });

    // ---- New packs that arrived with the last update (#649) ----
    showPackNews();

    // ---- Seasonal pack badges (#276) ----
    applySeasonalBadges();

    // ---- PWA install button ----
    // Android Chrome / Edge / Samsung Browser: fire beforeinstallprompt
    // when the page is install-eligible. We cache the prompt so the user
    // can trigger it on click. iOS Safari doesn't support this API, so
    // we detect iOS separately and show the manual Add-to-Home-Screen
    // hint modal instead. If the app is already installed (standalone
    // display-mode or iOS-standalone), do nothing.
    (function () {
        var installBtn = document.getElementById('pwa-install-btn');
        if (!installBtn) return;
        var isStandalone = window.matchMedia('(display-mode: standalone)').matches
            || window.navigator.standalone === true;
        if (isStandalone) return;

        var deferredPrompt = null;

        window.addEventListener('beforeinstallprompt', function (e) {
            e.preventDefault();
            deferredPrompt = e;
            installBtn.classList.remove('hidden');
        });

        window.addEventListener('appinstalled', function () {
            installBtn.classList.add('hidden');
            deferredPrompt = null;
        });

        // iOS detection — Safari on iPhone/iPad never fires
        // beforeinstallprompt, so surface the install button anyway
        // and let it open the manual hint modal.
        var isIOS = /iPad|iPhone|iPod/.test(window.navigator.userAgent)
            && !window.MSStream;
        var iosHint = document.getElementById('pwa-ios-hint');
        if (isIOS && iosHint) {
            installBtn.classList.remove('hidden');
        }

        installBtn.addEventListener('click', function () {
            if (deferredPrompt) {
                deferredPrompt.prompt();
                deferredPrompt.userChoice.then(function () {
                    deferredPrompt = null;
                    installBtn.classList.add('hidden');
                });
            } else if (iosHint) {
                iosHint.classList.remove('hidden');
            }
        });

        var iosClose = document.getElementById('pwa-ios-hint-close');
        if (iosClose && iosHint) {
            iosClose.addEventListener('click', function () {
                iosHint.classList.add('hidden');
            });
            // Backdrop click + Escape close the hint.
            iosHint.addEventListener('click', function (e) {
                if (e.target === iosHint) iosHint.classList.add('hidden');
            });
            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && !iosHint.classList.contains('hidden')) {
                    iosHint.classList.add('hidden');
                }
            });
        }
    })();
})();

/**
 * Show a banner listing packs that arrived with the update the host just
 * installed (#649).
 *
 * Packs ship inside the integration, so by the time this runs the new packs
 * are already on disk and playable — the banner is an announcement, not a
 * task. Nothing is fetched from GitHub and the host is never asked to copy
 * files by hand.
 *
 * Runs once on page load; silently does nothing if the request fails.
 */
async function showPackNews() {
    try {
        // Wait for the language bundle before rendering (#648). This banner is
        // built as one shot of markup and is not re-rendered on its own, so
        // without the await it races the i18n fetch and whichever finishes
        // first decides whether the host reads a sentence or a translation
        // key. init() returns the in-flight promise when the page has already
        // started loading a language, so this does not kick off a second load.
        if (window.QuizifyI18n) {
            try { await window.QuizifyI18n.init(); } catch (_e) { /* fall through untranslated */ }
        }

        const resp = await fetch('/api/quizify/packs/news');
        if (!resp.ok) return;
        const data = await resp.json();
        const packs = (data && data.new_packs) || [];
        if (packs.length === 0) return;

        // showPackNews lives outside the admin IIFE, so the IIFE-local
        // _t/escapeHtml helpers are out of scope here. Use the global
        // window.t (i18n) and window.QuizifyUtils.escapeHtml instead, and
        // escape all pack metadata before it touches innerHTML (defends
        // against a malicious pack name → stored XSS).
        const tt = (key, params) => (window.t ? window.t(key, params) : key);
        const esc = (s) => (window.QuizifyUtils && window.QuizifyUtils.escapeHtml
            ? window.QuizifyUtils.escapeHtml(String(s == null ? '' : s))
            : String(s == null ? '' : s));

        const names = packs.map(p =>
            esc(p.name) + ' (' + esc(p.question_count) + ')'
        ).join(', ');

        // Build banner
        const banner = document.createElement('div');
        banner.id = 'pack-news-banner';
        // Soft Parlor: white surface + coral left-accent + warm ink text on cream-ground page
        banner.style.cssText = [
            'background:#FFFFFF',
            'border:1px solid #E5DFCF',
            'border-left:3px solid #E88A7F',
            'border-radius:10px',
            'padding:12px 16px',
            'margin:12px 0',
            'display:flex',
            'align-items:flex-start',
            'gap:10px',
            'font-size:0.9rem',
            'color:#2A2820',
            'box-shadow:0 2px 8px rgba(42, 40, 32, 0.08)',
            'position:relative',
        ].join(';');

        // data-i18n on the two static strings so a language switch re-runs
        // initPageTranslations over them like every other element on the page.
        banner.innerHTML =
            '<span style="font-size:1.2rem;flex-shrink:0">🎁</span>' +
            '<div style="flex:1">' +
                '<strong data-i18n="admin.packNewsTitle" style="color:#E88A7F;font-family:\'DM Sans\',sans-serif;font-weight:700">' + esc(tt('admin.packNewsTitle')) + '</strong>' +
                '<div style="margin-top:3px;color:#2A2820">' + names + '</div>' +
                '<div data-i18n="admin.packNewsBody" style="margin-top:5px;font-size:0.8rem;color:#6E6A5C">' + esc(tt('admin.packNewsBody')) + '</div>' +
            '</div>' +
            '<button type="button" id="pack-news-dismiss" ' +
                'style="background:none;border:none;color:#6E6A5C;cursor:pointer;font-size:1rem;padding:0;flex-shrink:0" ' +
                'data-i18n-title="common.close" title="' + esc(tt('common.close')) + '">✕</button>';

        // Dismissing is persisted server-side: the packs stay announced across
        // reloads until the host actually acknowledges them, and stay gone
        // afterwards. The banner is removed either way — a failed POST should
        // not leave the host clicking a ✕ that does nothing.
        const dismiss = banner.querySelector('#pack-news-dismiss');
        if (dismiss) {
            dismiss.addEventListener('click', function () {
                banner.remove();
                const headers = {};
                const tok = window.QuizifyUtils && window.QuizifyUtils.readAdminToken
                    ? window.QuizifyUtils.readAdminToken()
                    : null;
                if (tok) headers['X-Quizify-Token'] = tok;
                fetch('/api/quizify/packs/news/dismiss', { method: 'POST', headers: headers })
                    .catch(function (e) { console.warn('[quizify] pack news dismiss failed:', e); });
            });
        }

        // Insert at top of setup screen, before first section
        const setupScreen = document.getElementById('setup-screen');
        if (setupScreen) {
            setupScreen.insertBefore(banner, setupScreen.firstChild);
        }
    } catch (e) {
        // Offline or HA not ready — log so a real error (e.g. a future
        // refactor reintroducing an out-of-scope reference) is visible
        // instead of being silently swallowed.
        console.warn('[quizify] showPackNews failed:', e);
    }
}
