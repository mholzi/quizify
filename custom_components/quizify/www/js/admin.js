/**
 * Quizify — Admin panel client.
 * Manages game setup, live monitoring, and game flow control.
 */

(function () {
    'use strict';

    // ---- State ----
    let ws = null;
    let reconnectAttempts = 0;
    const MAX_RECONNECT = 5;

    // Settings (from chips)
    let selectedCategory = 'mixed';
    let selectedCategories = []; // for multi-select mode
    let selectedDifficulty = 'medium';
    let selectedRounds = 10;

    // Game state
    let currentPhase = 'LOBBY';
    let playerCount = 0;

    // ---- Components ----
    const adminTimer = new TimerBar('admin-timer-bar', 15);

    // ---- DOM refs ----
    const views = {
        setup: document.getElementById('setup-view'),
        game: document.getElementById('game-view'),
        reveal: document.getElementById('admin-reveal-view'),
        finale: document.getElementById('admin-finale-view'),
    };

    const els = {
        categoryChips: document.getElementById('category-chips'),
        difficultyChips: document.getElementById('difficulty-chips'),
        roundsChips: document.getElementById('rounds-chips'),
        qrContainer: document.getElementById('qr-container'),
        joinUrl: document.getElementById('join-url'),
        setupPlayerCount: document.getElementById('setup-player-count'),
        setupPlayerChips: document.getElementById('setup-player-chips'),
        startGameBtn: document.getElementById('start-game-btn'),
        selfJoinName: document.getElementById('self-join-name'),
        selfJoinBtn: document.getElementById('self-join-btn'),
        // In-game
        adminRound: document.getElementById('admin-round'),
        adminQuestion: document.getElementById('admin-question'),
        adminCorrect: document.getElementById('admin-correct'),
        gameLeaderboard: document.getElementById('game-leaderboard'),
        nextQuestionBtn: document.getElementById('next-question-btn'),
        endGameBtn: document.getElementById('end-game-btn'),
        // Reveal
        revealRound: document.getElementById('reveal-round'),
        revealQuestion: document.getElementById('reveal-question'),
        revealCorrect: document.getElementById('reveal-correct'),
        revealFunFact: document.getElementById('reveal-fun-fact'),
        revealLeaderboard: document.getElementById('reveal-leaderboard'),
        continueBtn: document.getElementById('continue-btn'),
        // Finale
        adminPodium: document.getElementById('admin-podium'),
        adminFinaleLeaderboard: document.getElementById('admin-finale-leaderboard'),
        newGameBtn: document.getElementById('new-game-btn'),
    };

    // ---- View management ----
    function showView(name) {
        Object.values(views).forEach(v => v.classList.remove('active'));
        if (views[name]) views[name].classList.add('active');
    }

    // ---- Chip selectors ----
    function setupChips(container, onSelect) {
        if (!container) return;
        container.addEventListener('click', (e) => {
            const chip = e.target.closest('.chip');
            if (!chip) return;
            container.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            onSelect(chip.dataset.value);
        });
    }

    // Category: Gemischt = single select, others = multi-select
    function setupCategoryChips(container) {
        if (!container) return;
        container.addEventListener('click', (e) => {
            const chip = e.target.closest('.chip');
            if (!chip) return;
            const val = chip.dataset.value;

            if (val === 'mixed') {
                // Gemischt selected — deselect all others, select only Gemischt
                container.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                selectedCategory = 'mixed';
                selectedCategories = [];
            } else {
                // Deselect Gemischt
                const mixedChip = container.querySelector('.chip[data-value="mixed"]');
                if (mixedChip) mixedChip.classList.remove('active');

                // Toggle this chip
                chip.classList.toggle('active');

                // Build selectedCategories from active chips
                selectedCategories = Array.from(container.querySelectorAll('.chip.active'))
                    .map(c => c.dataset.value);

                if (selectedCategories.length === 0) {
                    // Nothing selected — fall back to Gemischt
                    if (mixedChip) mixedChip.classList.add('active');
                    selectedCategory = 'mixed';
                } else if (selectedCategories.length === 1) {
                    selectedCategory = selectedCategories[0];
                } else {
                    selectedCategory = 'multi'; // signal to use selectedCategories array
                }
            }
        });
    }

    setupCategoryChips(els.categoryChips);
    setupChips(els.difficultyChips, v => { selectedDifficulty = v; });
    setupChips(els.roundsChips, v => { selectedRounds = parseInt(v, 10); });

    // ---- WebSocket ----
    function connect() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const savedToken = sessionStorage.getItem('quizify_admin_session_token');
        let url = `${proto}//${location.host}/api/quizify/ws?role=admin`;
        if (savedToken) {
            url += `&token=${encodeURIComponent(savedToken)}`;
        }
        ws = new WebSocket(url);

        ws.onopen = () => {
            reconnectAttempts = 0;
            updateConnectionStatus('connected');
            send('admin_connect', {});
        };

        ws.onmessage = (evt) => {
            try {
                const msg = JSON.parse(evt.data);
                handleMessage(msg);
            } catch (e) {
                console.error('[Quizify Admin] Bad message:', e);
            }
        };

        ws.onclose = () => {
            ws = null;
            if (reconnectAttempts < MAX_RECONNECT) {
                reconnectAttempts++;
                updateConnectionStatus('reconnecting');
                setTimeout(connect, 1000 * reconnectAttempts);
            } else {
                updateConnectionStatus('disconnected');
            }
        };

        ws.onerror = () => ws && ws.close();
    }

    function send(type, payload) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type, ...payload }));
        }
    }

    // ---- Message router ----
    function handleMessage(msg) {
        switch (msg.type || msg.event) {
            case 'game_state':
                handleGameState(msg);
                break;
            case 'player_joined':
            case 'player_left':
                if (msg.players) renderSetupPlayers(msg.players);
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
                console.warn('[Quizify Admin] Error:', msg.code, msg.message);
                showErrorToast(msg.message || msg.code);
                break;
        }
    }

    // ---- Phase handlers ----

    function handleGameState(msg) {
        currentPhase = msg.phase;
        if (msg.admin_session_token) {
            sessionStorage.setItem('quizify_admin_session_token', msg.admin_session_token);
        }
        if (msg.players) renderSetupPlayers(msg.players);
        if (msg.join_url) renderJoinUrl(msg.join_url);

        switch (msg.phase) {
            case 'LOBBY':
                showView('setup');
                break;
            case 'QUESTION_ACTIVE':
                if (msg.question) {
                    handleQuestionStarted({
                        question_text: msg.question.text,
                        correct_answer: msg.question.answers ? findCorrectAnswer(msg.question) : '',
                        timer_duration: msg.question.time_limit,
                        round_num: msg.round,
                        total_rounds: msg.total_rounds,
                    });
                }
                if (msg.leaderboard) renderLeaderboard(els.gameLeaderboard, msg.leaderboard);
                break;
            case 'ANSWER_REVEAL':
                showView('reveal');
                if (msg.round_summary) showReveal(msg);
                if (msg.leaderboard) renderLeaderboard(els.revealLeaderboard, msg.leaderboard);
                break;
            case 'FINALE':
                handleFinale(msg);
                break;
        }
    }

    function handleQuestionStarted(msg) {
        currentPhase = 'QUESTION_ACTIVE';
        showView('game');

        els.adminRound.textContent = `Frage ${msg.round_num} / ${msg.total_rounds}`;
        els.adminQuestion.textContent = msg.question_text;
        els.adminCorrect.textContent = `Richtig: ${msg.correct_answer || ''}`;

        adminTimer.start(msg.timer_duration);
        els.nextQuestionBtn.classList.add('hidden');
    }

    function handleRoundSummary(msg) {
        currentPhase = 'ANSWER_REVEAL';
        adminTimer.stop();
        showView('reveal');
        showReveal(msg);
    }

    function showReveal(msg) {
        const summary = msg.round_summary || msg;
        els.revealRound.textContent = `Frage ${msg.round || ''} / ${msg.total_rounds || ''}`;
        els.revealQuestion.textContent = summary.question_text || msg.question?.text || '';
        els.revealCorrect.textContent = `Richtig: ${summary.correct_answer || ''}`;

        // Fun fact
        const funFactText = summary.fun_fact || '';
        if (funFactText) {
            els.revealFunFact.classList.remove('hidden');
            els.revealFunFact.classList.add('visible');
            els.revealFunFact.querySelector('.fun-fact-text').textContent = funFactText;
        } else {
            els.revealFunFact.classList.add('hidden');
        }

        // Leaderboard
        const lb = summary.leaderboard || msg.leaderboard || [];
        renderLeaderboard(els.revealLeaderboard, lb);
    }

    function handleFinale(msg) {
        currentPhase = 'FINALE';
        adminTimer.stop();
        showView('finale');

        const podium = msg.podium || [];
        renderPodium(els.adminPodium, podium);

        const lb = msg.leaderboard || msg.all_players || [];
        renderLeaderboard(els.adminFinaleLeaderboard, lb);
    }

    function handleGameReset() {
        currentPhase = 'LOBBY';
        showView('setup');
    }

    // ---- Renderers ----

    function renderSetupPlayers(players) {
        const list = Array.isArray(players) ? players : Object.values(players);
        playerCount = list.length;
        els.setupPlayerCount.textContent = playerCount;
        els.startGameBtn.disabled = playerCount < 1;
        els.setupPlayerChips.innerHTML = list
            .map(p => {
                const name = typeof p === 'string' ? p : (p.name || p);
                return `<span class="player-chip"><span class="dot"></span>${escapeHtml(name)}</span>`;
            })
            .join('');
    }

    function renderJoinUrl(url) {
        els.joinUrl.textContent = url;
        // QR code generation — use a simple inline QR lib or skip if not available
        generateQR(url);
    }

    function renderLeaderboard(container, players) {
        if (!container) return;
        container.innerHTML = players
            .map((p, i) => {
                const rank = p.rank || i + 1;
                const rankClass = rank <= 3 ? ` rank-${rank}` : '';
                return `
                    <div class="leaderboard-row">
                        <span class="leaderboard-rank${rankClass}">${rank}</span>
                        <span class="leaderboard-name">${escapeHtml(p.name)}</span>
                        <span class="leaderboard-score">${p.score}</span>
                        ${p.streak > 1 ? `<span class="leaderboard-streak">${p.streak}x</span>` : ''}
                    </div>
                `;
            })
            .join('');
    }

    function renderPodium(container, podium) {
        const ordered = [];
        if (podium[1]) ordered.push({ ...podium[1], place: 2 });
        if (podium[0]) ordered.push({ ...podium[0], place: 1 });
        if (podium[2]) ordered.push({ ...podium[2], place: 3 });

        const medals = { 1: '🥇', 2: '🥈', 3: '🥉' };
        const barClass = { 1: 'first', 2: 'second', 3: 'third' };

        container.innerHTML = ordered
            .map(p => `
                <div class="podium-place">
                    <div class="podium-avatar">${medals[p.place] || ''}</div>
                    <div class="podium-name">${escapeHtml(p.name)}</div>
                    <div class="podium-score">${p.score} Pkt.</div>
                    <div class="podium-bar ${barClass[p.place] || ''}">${p.place}</div>
                </div>
            `)
            .join('');
    }

    function findCorrectAnswer(question) {
        // The admin gets the full question with correct flag
        if (question.correct_answer) return question.correct_answer;
        if (question.answers && Array.isArray(question.answers)) {
            for (const a of question.answers) {
                if (typeof a === 'object' && a.correct) return a.text;
            }
        }
        return '';
    }

    function escapeHtml(text) {
        return QuizifyUtils.escapeHtml(text);
    }

    // ---- QR Code (qrcodejs library) ----
    let _qrInstance = null;
    function generateQR(url) {
        const container = document.getElementById('qr-container');
        if (!container) return;

        // Clear previous QR
        container.innerHTML = '';

        if (typeof QRCode !== 'undefined') {
            _qrInstance = new QRCode(container, {
                text: url,
                width: 180,
                height: 180,
                colorDark: '#0b0e1a',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M,
            });
        } else {
            // Fallback: just show the URL as text if library didn't load
            container.innerHTML = `<div style="padding:20px;word-break:break-all;font-size:12px;">${url}</div>`;
        }
    }

    // ---- Event listeners ----

    // Start game
    // Self-join as player
    els.selfJoinBtn.addEventListener('click', () => {
        const name = els.selfJoinName.value.trim();
        if (!name) { els.selfJoinName.focus(); return; }
        // Open player page in new tab with name pre-filled
        const playerUrl = new URL('/quizify/player', location.href);
        playerUrl.searchParams.set('name', name);
        window.open(playerUrl.toString(), '_blank');
        els.selfJoinName.value = '';
    });

    els.selfJoinName.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') els.selfJoinBtn.click();
    });

    els.startGameBtn.addEventListener('click', () => {
        const categoryPayload = selectedCategory === 'mixed'
            ? null
            : selectedCategory === 'multi'
                ? selectedCategories
                : selectedCategory;
        send('start_game', {
            category: categoryPayload,
            difficulty: selectedDifficulty === 'mixed' ? null : selectedDifficulty,
            num_rounds: selectedRounds,
        });
    });

    // Next question (from reveal or in-game skip)
    els.nextQuestionBtn.addEventListener('click', () => {
        send('next_question', {});
    });

    els.continueBtn.addEventListener('click', () => {
        send('next_question', {});
    });

    // End game
    els.endGameBtn.addEventListener('click', () => {
        if (confirm('Spiel wirklich beenden?')) {
            send('end_game', {});
        }
    });

    // New game (from finale)
    els.newGameBtn.addEventListener('click', () => {
        send('reset_game', {});
    });

    // ---- Generate join URL on load ----
    function initJoinUrl() {
        const joinUrl = `${location.protocol}//${location.host}/quizify/player`;
        renderJoinUrl(joinUrl);
    }

    // ---- Error toast ----
    function showErrorToast(message) {
        let toast = document.getElementById('error-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'error-toast';
            toast.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#ff4757;color:white;padding:10px 20px;border-radius:10px;font-size:0.85rem;z-index:9999;opacity:0;transition:opacity 0.3s;pointer-events:none;';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.style.opacity = '1';
        setTimeout(() => { toast.style.opacity = '0'; }, 3000);
    }

    // ---- Connection status indicator ----
    function updateConnectionStatus(status) {
        let indicator = document.getElementById('conn-status');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'conn-status';
            indicator.style.cssText = 'position:fixed;bottom:12px;right:12px;display:flex;align-items:center;gap:6px;font-size:0.75rem;color:#a4a4b8;z-index:100;';
            document.body.appendChild(indicator);
        }
        const colors = { connected: '#00b894', reconnecting: '#ffa502', disconnected: '#ff4757' };
        indicator.innerHTML = '<span style="width:8px;height:8px;border-radius:50%;background:' + (colors[status] || '#636e8a') + ';"></span>' + status;
    }

    // ---- Init ----
    initJoinUrl();

    // i18n init
    if (window.QuizifyI18n) {
        QuizifyI18n.init().then(function() {
            QuizifyI18n.initPageTranslations();
        });
    }

    connect();
    updateConnectionStatus('reconnecting');
})();
