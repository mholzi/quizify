/**
 * Quizify — Admin panel client.
 * Manages game setup, lobby, live monitoring, and game flow control.
 */

(function () {
    'use strict';

    // ---- State ----
    let ws = null;
    let reconnectAttempts = 0;
    const MAX_RECONNECT = 5;
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
    let selectedLanguage = 'de';
    let selectedTimer = 30;  // seconds per question (20 / 30 / 45)

    // Game state
    let currentPhase = 'LOBBY';
    let playerCount = 0;

    // ---- Simple inline timer ----
    var adminTimerEl = null;
    var adminTimerInterval = null;
    var adminTimer = {
        start: function(duration) {
            adminTimerEl = document.getElementById('admin-timer-bar');
            clearInterval(adminTimerInterval);
        },
        update: function(remaining) {
            if (!adminTimerEl) adminTimerEl = document.getElementById('admin-timer-bar');
            if (adminTimerEl) adminTimerEl.textContent = Math.ceil(remaining) + 's';
        },
        stop: function() {
            clearInterval(adminTimerInterval);
            if (adminTimerEl) adminTimerEl.textContent = '';
        }
    };

    // ---- DOM refs ----
    const views = {
        setup: document.getElementById('setup-screen'),
        lobby: document.getElementById('lobby-screen'),
        game: document.getElementById('game-view'),
        finale: document.getElementById('admin-finale-view'),
    };

    const els = {
        categoryChips: document.getElementById('category-chips'),
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
        startGameplayBtn: document.getElementById('start-gameplay-btn'),
        participateBtn: document.getElementById('participate-btn'),
        // In-game
        adminRound: document.getElementById('admin-round'),
        adminQuestion: document.getElementById('admin-question'),
        adminCorrect: document.getElementById('admin-correct'),
        gameLeaderboard: document.getElementById('game-leaderboard'),
        nextQuestionBtn: document.getElementById('next-question-btn'),
        endGameBtn: document.getElementById('end-game-btn'),
        resetGameBtn: document.getElementById('reset-game-btn'),
        // Finale
        adminPodium: document.getElementById('admin-podium'),
        adminFinaleLeaderboard: document.getElementById('admin-finale-leaderboard'),
        newGameBtn: document.getElementById('new-game-btn'),
        lobbyBackBtn: document.getElementById('lobby-back-btn'),
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

    function _t(key, params) {
        if (window.QuizifyI18n && typeof window.QuizifyI18n.t === 'function') {
            return window.QuizifyI18n.t(key, params);
        }
        return key;
    }

    // ---- Phase 2: Pack-UI (Featured-Spotlight + Theme-Tabs) ----
    // Always-on per the approved mockup
    // (~/.gstack/designs/pack-ui-2026-05-27/phase2-themes.html). The
    // spotlight + tabs render regardless of pack count; Markus made the
    // call on 2026-05-27 to drop the threshold gating after seeing the
    // first cut with 3 packs and asking for "genauso wie in den Mockups".

    // Featured pack per language. Picked deliberately (newest / most
    // crowd-pleasing). Update when adding fresh packs.
    var FEATURED_PACK = {
        de: { value: 'geographie',  title: '🌍 Geographie',  meta: '47 Fragen · Familienfreundlich' },
        en: { value: 'geography',   title: '🌍 Geography',   meta: '47 questions · Family-friendly' },
    };

    function updatePackUIScaling(lang) {
        // Populate spotlight from FEATURED_PACK for the current language.
        if (els.featuredSpotlight && FEATURED_PACK[lang]) {
            if (els.spotlightTitle) els.spotlightTitle.textContent = FEATURED_PACK[lang].title;
            if (els.spotlightMeta)  els.spotlightMeta.textContent  = FEATURED_PACK[lang].meta;
        }
        // Re-apply the "all" theme filter so a language switch clears any
        // leftover hidden-by-theme state on chips that just became
        // visible for the new language.
        applyThemeFilter('all');
    }

    function applyThemeFilter(theme) {
        if (!els.categoryChips) return;
        if (els.themeTabs) {
            els.themeTabs.querySelectorAll('.theme-tab').forEach(function (t) {
                t.classList.toggle('active', t.dataset.theme === theme);
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

    function setupFeaturedSpotlight() {
        if (!els.featuredSpotlight || !els.categoryChips) return;
        els.featuredSpotlight.addEventListener('click', function () {
            var lang = (typeof selectedLanguage === 'string') ? selectedLanguage : 'de';
            var featured = FEATURED_PACK[lang];
            if (!featured) return;
            // Featured click = pick exactly this pack. Clears Mixed + any
            // multi-select from prior interactions.
            els.categoryChips.querySelectorAll('.chip').forEach(function (c) {
                c.classList.remove('active');
            });
            var target = els.categoryChips.querySelector('.chip[data-value="' + featured.value + '"]');
            if (target) {
                target.classList.add('active');
                selectedCategory = featured.value;
                selectedCategories = [featured.value];
                updateCategorySummary();
            }
        });
    }

    function updateCategorySummary() {
        if (!els.categorySummary) return;
        if (selectedCategory === 'mixed') {
            els.categorySummary.textContent = _t('admin.categoryMixed');
        } else if (selectedCategory === 'multi') {
            els.categorySummary.textContent = _t('admin.categoriesCountPlural', { count: selectedCategories.length });
        } else {
            var activeChip = els.categoryChips.querySelector('.chip.active');
            els.categorySummary.textContent = activeChip ? activeChip.textContent : selectedCategory;
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
        var langFlag = selectedLanguage === 'en' ? '🇬🇧' : '🇩🇪';
        var parts = [];
        if (preset) parts.push(preset.label);
        parts.push(selectedRounds + ' ' + _t('admin.summaryRoundsUnit'));
        parts.push(diffLabel);
        parts.push(selectedTimer + ' s');
        parts.push(langFlag);
        heroEl.textContent = parts.join(' · ');
    }

    var _PRESETS = [
        { id: 'schnellrunde', rounds: 5,  difficulty: 'easy',   timer: 20, labelKey: 'setup.preset.fastName'     },
        { id: 'klassiker',    rounds: 10, difficulty: 'medium', timer: 30, labelKey: 'setup.preset.classicName'  },
        { id: 'marathon',     rounds: 20, difficulty: 'hard',   timer: 45, labelKey: 'setup.preset.marathonName' },
    ];

    function _matchingPreset() {
        for (var i = 0; i < _PRESETS.length; i++) {
            var p = _PRESETS[i];
            if (p.rounds === selectedRounds && p.difficulty === selectedDifficulty && p.timer === selectedTimer) {
                return { id: p.id, label: _t(p.labelKey) };
            }
        }
        return null;
    }

    function markActivePreset() {
        var match = _matchingPreset();
        document.querySelectorAll('.preset-card[data-preset]').forEach(function (card) {
            var id = card.getAttribute('data-preset');
            // "Eigene" is active only when no real preset matches.
            var active = match ? (id === match.id) : (id === 'eigene');
            card.classList.toggle('is-active', active);
        });
    }

    // Apply preset: write to the active-chip state AND to the typed vars
    // so the existing chip group serialization works unchanged.
    function _applyPreset(rounds, difficulty, timer) {
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
                _applyPreset(rounds, difficulty, timer);
            });
        });
    }
    _wireSetupHeroAndPresets();
    // Initial hero summary paint
    updateHeroSummary();
    markActivePreset();

    setupCategoryChips(els.categoryChips);
    setupThemeTabs();
    setupFeaturedSpotlight();
    // Initial scaling pass — paints spotlight/tabs once we know the
    // visible-pack-count for the default language.
    updatePackUIScaling(typeof selectedLanguage === 'string' ? selectedLanguage : 'de');
    setupChips(els.difficultyChips, function (v) {
        selectedDifficulty = v;
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
    setupChips(els.languageChips, function (v) {
        selectedLanguage = v;
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
    // Init: hide English category chips on load
    if (els.categoryChips) {
        els.categoryChips.querySelectorAll('.chip[data-lang="en"]').forEach(function (chip) {
            chip.style.display = 'none';
        });
    }
    setupCollapsibles();

    // ---- WebSocket ----
    function connect() {
        var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        var savedToken = sessionStorage.getItem('quizify_admin_session_token');
        var url = proto + '//' + location.host + '/api/quizify/ws?role=admin';
        if (savedToken) url += '&token=' + encodeURIComponent(savedToken);
        ws = new WebSocket(url);

        ws.onopen = function () {
            reconnectAttempts = 0;
            updateConnectionStatus('connected');
            send('admin_connect', {});
        };

        ws.onmessage = function (evt) {
            try {
                var msg = JSON.parse(evt.data);
                handleMessage(msg);
            } catch (e) {
                console.error('[Quizify Admin] Bad message:', e);
            }
        };

        ws.onclose = function () {
            ws = null;
            if (reconnectAttempts < MAX_RECONNECT) {
                reconnectAttempts++;
                updateConnectionStatus('reconnecting');
                setTimeout(connect, 1000 * reconnectAttempts);
            } else {
                updateConnectionStatus('disconnected');
            }
        };

        ws.onerror = function () { if (ws) ws.close(); };
    }

    function send(type, payload) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(Object.assign({ type: type }, payload)));
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
                if (msg.session_token && _adminJoinedAs) {
                    try {
                        sessionStorage.setItem('quizify_session_token', msg.session_token);
                        sessionStorage.setItem('quizify_player_name', _adminJoinedAs);
                    } catch (e) { /* storage unavailable */ }
                }
                break;
            case 'game_state':
                handleGameState(msg);
                break;
            case 'player_joined':
            case 'player_left':
                if (msg.players) renderLobbyPlayers(msg.players);
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
            case 'error':
                // The initial admin_connect attempt before authentication
                // returns "Admin only" — that's expected handshake noise,
                // not an error worth showing in the console.
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
            sessionStorage.setItem('quizify_admin_session_token', msg.admin_session_token);
        }
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
            case 'FINALE':
                // Land directly on finale view so the admin sees the
                // result and the "Neues Spiel starten" button without
                // first being shown the (no-op) setup screen. Fixes
                // the lockout where admin clicked Spiel starten and
                // server rejected because phase was already FINALE.
                handleFinale(msg);
                break;
        }
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

        adminTimer.start(msg.timer_duration);
        if (els.nextQuestionBtn) els.nextQuestionBtn.classList.add('hidden');
        if (els.endGameBtn) els.endGameBtn.classList.add('hidden');
    }

    // handleRoundSummary / showReveal / renderAnswerDistribution lived
    // here in v1.1.15 and earlier to populate the admin-only reveal
    // view. The host always redirects to /quizify/player on game start,
    // so they were never actually run for HACS users. Removed with
    // the #admin-reveal-view markup in v1.1.16.
    function handleRoundSummary(/* msg */) {
        if (_redirecting) return;
        currentPhase = 'ANSWER_REVEAL';
        adminTimer.stop();
        // The player tab owns the reveal UI; nothing for the admin tab
        // to render. Keeping this stub so the WS message router still
        // has a target (avoids "undefined" route errors).
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

    function renderLobbyPlayers(players) {
        var list = Array.isArray(players) ? players : Object.values(players);
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
            countdownEl.classList.toggle('hidden', isReady);
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
        if (els.participateBtn) {
            var rosterHasMe = _adminJoinedAs && list.some(function (p) {
                var n = typeof p === 'string' ? p : (p && p.name);
                return n === _adminJoinedAs;
            });
            if (_adminJoinedAs && rosterHasMe) {
                els.participateBtn.disabled = true;
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
            els.lobbyPlayerChips.innerHTML = list
                .map(function (p, idx) {
                    var name = typeof p === 'string' ? p : (p.name || p);
                    var isAdmin = typeof p === 'object' && p && p.is_admin;
                    var color = (typeof p === 'object' && p && p.color)
                        ? p.color
                        : _LOBBY_COLORS[idx % _LOBBY_COLORS.length];
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
                })
                .join('');
            // Single delegated click handler — re-attaching per render is fine
            // because innerHTML wipes the previous listeners with the nodes.
            els.lobbyPlayerChips.querySelectorAll('.player-chip-kick').forEach(function (btn) {
                btn.addEventListener('click', function (ev) {
                    ev.stopPropagation();
                    var name = btn.getAttribute('data-kick-name');
                    if (!name) return;
                    var confirmMsg = _t('admin.kickConfirm') || ('Remove ' + name + ' from the lobby?');
                    if (window.confirm(confirmMsg.replace('{name}', name))) {
                        send('kick_player', { player_name: name });
                    }
                });
            });
        }
    }

    function renderLeaderboard(container, players) {
        if (!container) return;
        container.innerHTML = players
            .map(function (p, i) {
                var rank = p.rank || i + 1;
                var rankClass = rank <= 3 ? ' rank-' + rank : '';
                return '<div class="leaderboard-row">' +
                    '<span class="leaderboard-rank' + rankClass + '">' + rank + '</span>' +
                    '<span class="leaderboard-name">' + escapeHtml(p.name) + '</span>' +
                    '<span class="leaderboard-score">' + p.score + '</span>' +
                    (p.streak > 1 ? '<span class="leaderboard-streak">' + p.streak + 'x</span>' : '') +
                    '</div>';
            })
            .join('');
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

    function _buildStartGamePayload() {
        var categoryPayload = selectedCategory === 'mixed'
            ? null
            : selectedCategory === 'multi'
                ? selectedCategories
                : selectedCategory;
        return {
            category: categoryPayload,
            difficulty: selectedDifficulty === 'mixed' ? null : selectedDifficulty,
            num_rounds: selectedRounds,
            language: selectedLanguage,
            timer_duration: selectedTimer,
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
        send('join', { name: name, is_admin: true });
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
        on(els.participateBtn, 'click', function () { openAdminJoinModal('join'); });
        on(els.adminCancelBtn, 'click', closeAdminJoinModal);

        var backdrop = els.adminJoinModal ? els.adminJoinModal.querySelector('.modal-backdrop') : null;
        if (backdrop) backdrop.addEventListener('click', closeAdminJoinModal);

        on(els.adminNameInput, 'input', function () {
            var name = this.value.trim();
            if (els.adminJoinBtn) els.adminJoinBtn.disabled = !name || name.length > 20;
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
        send(msgType, {});
        // Re-enable after 1.5s as a safety net (in case server doesn't respond).
        setTimeout(function () { btn.disabled = false; }, 1500);
    }
    on(els.nextQuestionBtn, 'click', function () { _debouncedSend(els.nextQuestionBtn, 'next_question'); });

    on(els.endGameBtn, 'click', function () {
        var modal = document.getElementById('end-game-modal');
        if (modal) modal.classList.remove('hidden');
    });

    on('end-game-confirm-btn', 'click', function () {
        send('end_game', {});
        var modal = document.getElementById('end-game-modal');
        if (modal) modal.classList.add('hidden');
    });
    on('end-game-cancel-btn', 'click', function () {
        var modal = document.getElementById('end-game-modal');
        if (modal) modal.classList.add('hidden');
    });
    var endBackdrop = document.querySelector('#end-game-modal .modal-backdrop');
    if (endBackdrop) on(endBackdrop, 'click', function () {
        var modal = document.getElementById('end-game-modal');
        if (modal) modal.classList.add('hidden');
    });

    on(els.newGameBtn, 'click', function () { send('reset_game', {}); });

    // ---- Reset Game button (header) ----
    on(els.resetGameBtn, 'click', function () {
        var modal = document.getElementById('reset-game-modal');
        if (modal) modal.classList.remove('hidden');
    });
    on('reset-game-confirm-btn', 'click', function () {
        send('reset_game', {});
        var modal = document.getElementById('reset-game-modal');
        if (modal) modal.classList.add('hidden');
    });
    on('reset-game-cancel-btn', 'click', function () {
        var modal = document.getElementById('reset-game-modal');
        if (modal) modal.classList.add('hidden');
    });
    var resetBackdrop = document.querySelector('#reset-game-modal .modal-backdrop');
    if (resetBackdrop) on(resetBackdrop, 'click', function () {
        var modal = document.getElementById('reset-game-modal');
        if (modal) modal.classList.add('hidden');
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            closeAdminJoinModal();
            var endModal = document.getElementById('end-game-modal');
            if (endModal) endModal.classList.add('hidden');
            var resetModal = document.getElementById('reset-game-modal');
            if (resetModal) resetModal.classList.add('hidden');
        }
    });

    // ---- Generate join URL ----
    function initJoinUrl() {
        var joinUrl = window.location.origin + '/quizify/player';
        generateQR(joinUrl);
        if (els.dashboardLink) {
            els.dashboardLink.href = window.location.origin + '/quizify/dashboard';
        }
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
        indicator.innerHTML = '<span style="width:10px;height:10px;border-radius:50%;display:inline-block;background:' +
            color + ';box-shadow:0 0 10px ' + glowColor + ';"></span>';
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
        });
    } else {
        // No i18n: still populate the summary chip with the timer
        // value on first paint (was missing in v1.1.3).
        updateSettingsSummary();
        updateCategorySummary();
    }

    connect();
    updateConnectionStatus('reconnecting');

    // ---- Question pack update check ----
    checkPackUpdates();

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
 * Fetch /api/quizify/packs/updates and show a banner if any packs have updates.
 * Runs once on page load; silently does nothing if the request fails or GitHub
 * is unreachable (e.g. fully offline HA setup).
 */
async function checkPackUpdates() {
    try {
        const resp = await fetch('/api/quizify/packs/updates');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.upstream_available || !data.updates || data.updates.length === 0) return;

        const updates = data.updates;
        const names = updates.map(u => u.name + ' (' + u.installed_version + ' → ' + u.upstream_version + ')').join(', ');

        // Build banner
        const banner = document.createElement('div');
        banner.id = 'pack-update-banner';
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

        const packPath = '<code style="background:#F3EEDF;padding:1px 4px;border-radius:3px;font-family:\'JetBrains Mono\',monospace;color:#2A2820">custom_components/quizify/questions/</code>';
        const bodyText = _t('admin.packUpdateBody', { path: packPath });
        const dismissTitle = _t('common.close');
        banner.innerHTML =
            '<span style="font-size:1.2rem;flex-shrink:0">📦</span>' +
            '<div style="flex:1">' +
                '<strong style="color:#E88A7F;font-family:\'Cabinet Grotesk\',sans-serif;font-weight:700">' + _t('admin.packUpdateTitle') + '</strong>' +
                '<div style="margin-top:3px;color:#2A2820">' + names + '</div>' +
                '<div style="margin-top:5px;font-size:0.8rem;color:#6E6A5C">' + bodyText + '</div>' +
            '</div>' +
            '<button onclick="document.getElementById(\'pack-update-banner\').remove()" ' +
                'style="background:none;border:none;color:#6E6A5C;cursor:pointer;font-size:1rem;padding:0;flex-shrink:0" ' +
                'title="' + dismissTitle + '">✕</button>';

        // Insert at top of setup screen, before first section
        const setupScreen = document.getElementById('setup-screen');
        if (setupScreen) {
            setupScreen.insertBefore(banner, setupScreen.firstChild);
        }
    } catch (_e) {
        // Silently ignore — offline or HA not ready
    }
}
