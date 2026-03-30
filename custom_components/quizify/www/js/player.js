/**
 * Quizify — Player client.
 * Connects via WebSocket, handles all game phases.
 */

(function () {
    'use strict';

    // ---- State ----
    let ws = null;
    let playerName = null;
    let joined = false;
    let currentPhase = 'LOBBY';
    let currentQuestion = null;
    let myPowerUp = null;
    let reconnectAttempts = 0;
    const MAX_RECONNECT = 5;

    // ---- Components ----
    const timerBar = new TimerBar('timer-bar', 15);
    const answerBtns = new AnswerButtons('answers-container');
    const revealBtns = new AnswerButtons('reveal-answers');
    const funFact = new FunFactReveal('fun-fact');

    // ---- DOM refs ----
    const views = {
        lobby: document.getElementById('lobby-view'),
        question: document.getElementById('question-view'),
        reveal: document.getElementById('reveal-view'),
        finale: document.getElementById('finale-view'),
    };

    const els = {
        nameInput: document.getElementById('name-input'),
        joinBtn: document.getElementById('join-btn'),
        joinSection: document.getElementById('join-section'),
        waitingSection: document.getElementById('waiting-section'),
        playerChips: document.getElementById('player-chips'),
        playerCount: document.getElementById('player-count'),
        timerBarContainer: document.getElementById('timer-bar'),
        roundIndicator: document.getElementById('round-indicator'),
        questionText: document.getElementById('question-text'),
        questionCategory: document.getElementById('question-category'),
        powerupBar: document.getElementById('powerup-bar'),
        powerupBtn: document.getElementById('powerup-btn'),
        powerupIcon: document.getElementById('powerup-icon'),
        powerupLabel: document.getElementById('powerup-label'),
        revealQuestion: document.getElementById('reveal-question'),
        pointsEarned: document.getElementById('points-earned'),
        scoreDetail: document.getElementById('score-detail'),
        streakBadge: document.getElementById('streak-badge'),
        rankDisplay: document.getElementById('rank-display'),
        podium: document.getElementById('podium'),
        finaleLeaderboard: document.getElementById('finale-leaderboard'),
    };

    // ---- View management ----
    function showView(name) {
        Object.values(views).forEach(v => v.classList.remove('active'));
        if (views[name]) views[name].classList.add('active');
    }

    // ---- WebSocket ----
    function connect() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${location.host}/api/quizify/ws`;
        ws = new WebSocket(url);

        ws.onopen = () => {
            reconnectAttempts = 0;
            updateConnectionStatus('connected');
            // Try session-based reconnect first
            const savedToken = sessionStorage.getItem('quizify_session_token');
            const savedName = sessionStorage.getItem('quizify_player_name');
            if (savedToken && savedName) {
                send('reconnect', { session_token: savedToken, name: savedName });
            } else if (playerName && !joined) {
                send('join', { name: playerName });
            }
        };

        ws.onmessage = (evt) => {
            try {
                const msg = JSON.parse(evt.data);
                handleMessage(msg);
            } catch (e) {
                console.error('[Quizify] Bad message:', e);
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
            case 'joined':
                joined = true;
                playerName = msg.player_id || playerName;
                if (msg.session_token) {
                    sessionStorage.setItem('quizify_session_token', msg.session_token);
                    sessionStorage.setItem('quizify_player_name', playerName);
                }
                els.joinSection.classList.add('hidden');
                els.waitingSection.classList.remove('hidden');
                break;
            case 'reconnected':
                joined = true;
                playerName = msg.player_id || playerName;
                if (msg.session_token) {
                    sessionStorage.setItem('quizify_session_token', msg.session_token);
                    sessionStorage.setItem('quizify_player_name', playerName);
                }
                els.joinSection.classList.add('hidden');
                els.waitingSection.classList.remove('hidden');
                break;
            case 'reconnect_failed':
                // Token was stale, clear it and show join form
                sessionStorage.removeItem('quizify_session_token');
                sessionStorage.removeItem('quizify_player_name');
                break;
            case 'question_started':
                handleQuestionStarted(msg);
                break;
            case 'answer_result':
                handleAnswerResult(msg);
                break;
            case 'round_summary':
                handleRoundSummary(msg);
                break;
            case 'timer_tick':
                timerBar.update(msg.remaining);
                break;
            case 'powerup_assigned':
                handlePowerUpAssigned(msg);
                break;
            case 'powerup_applied':
                handlePowerUpApplied(msg);
                break;
            case 'finale':
                handleFinale(msg);
                break;
            case 'error':
                handleError(msg);
                break;
            case 'player_joined':
            case 'player_left':
                if (msg.players) renderPlayerList(msg.players);
                break;
        }
    }

    // ---- Phase handlers ----

    function handleGameState(msg) {
        currentPhase = msg.phase;
        if (msg.players) renderPlayerList(msg.players);

        switch (msg.phase) {
            case 'LOBBY':
                showView('lobby');
                break;
            case 'QUESTION_ACTIVE':
                if (msg.question) {
                    handleQuestionStarted({
                        question_text: msg.question.text,
                        answers: msg.question.answers,
                        timer_duration: msg.question.time_limit,
                        round_num: msg.round,
                        total_rounds: msg.total_rounds,
                        category: msg.question.category,
                    });
                }
                break;
            case 'ANSWER_REVEAL':
                if (msg.round_summary) {
                    showView('reveal');
                }
                break;
            case 'FINALE':
                handleFinale(msg);
                break;
        }
    }

    function handleQuestionStarted(msg) {
        currentPhase = 'QUESTION_ACTIVE';
        currentQuestion = msg;

        // Show view
        showView('question');
        els.timerBarContainer.classList.remove('hidden');

        // Round indicator
        els.roundIndicator.textContent = `Frage ${msg.round_num} / ${msg.total_rounds}`;
        els.questionText.textContent = msg.question_text;
        els.questionCategory.textContent = msg.category || '';

        // Timer
        timerBar.start(msg.timer_duration);

        // Answers
        const answers = msg.answers.map((text, i) => ({ text, index: i }));
        answerBtns.setAnswers(answers);

        // Power-up
        if (myPowerUp) {
            els.powerupBar.classList.remove('hidden');
            renderPowerUp(myPowerUp);
        } else {
            els.powerupBar.classList.add('hidden');
        }
    }

    function handleAnswerResult(msg) {
        // Store result for reveal phase rendering
        window._lastResult = msg;
    }

    function handleRoundSummary(msg) {
        currentPhase = 'ANSWER_REVEAL';
        timerBar.stop();
        els.timerBarContainer.classList.add('hidden');
        showView('reveal');

        // Question echo
        if (currentQuestion) {
            els.revealQuestion.textContent = currentQuestion.question_text;

            // Render answer buttons in reveal mode
            const answers = currentQuestion.answers.map((text, i) => ({ text, index: i }));
            revealBtns.setAnswers(answers);
            revealBtns.showResult(msg.correct_answer_index != null ? msg.correct_answer_index : -1);
        }

        // Score
        const result = window._lastResult || {};
        const pts = result.points_earned || 0;
        els.pointsEarned.textContent = pts > 0 ? `+${pts}` : '0';
        els.pointsEarned.classList.toggle('zero', pts === 0);

        els.scoreDetail.textContent = result.correct
            ? 'Richtig!'
            : `Falsch — die Antwort war: ${msg.correct_answer || ''}`;

        // Streak
        if (result.new_streak && result.new_streak > 1) {
            els.streakBadge.classList.remove('hidden');
            els.streakBadge.textContent = `${result.new_streak}x Serie`;
        } else {
            els.streakBadge.classList.add('hidden');
        }

        // Rank
        if (msg.leaderboard && playerName) {
            const entry = msg.leaderboard.find(p => p.name === playerName);
            if (entry) {
                els.rankDisplay.textContent = `Platz ${entry.rank} — ${entry.score} Punkte`;
            }
        }

        // Fun fact
        if (msg.fun_fact) {
            funFact.show(msg.fun_fact);
        }

        myPowerUp = null;
    }

    function handleFinale(msg) {
        currentPhase = 'FINALE';
        timerBar.stop();
        els.timerBarContainer.classList.add('hidden');
        showView('finale');

        // Podium
        const podium = msg.podium || [];
        renderPodium(podium);

        // Full leaderboard
        const lb = msg.leaderboard || msg.all_players || [];
        renderLeaderboard(els.finaleLeaderboard, lb);

        // Share card — show copy button if share_texts available
        const shareTexts = msg.share_texts || {};
        const myShareText = shareTexts[playerName];
        const copyBtn = document.getElementById('copy-result-btn');
        if (copyBtn && myShareText) {
            copyBtn.style.display = 'inline-flex';
            copyBtn.onclick = () => {
                navigator.clipboard.writeText(myShareText).then(() => {
                    copyBtn.textContent = window.t ? t('finale.copied') : 'Kopiert!';
                    setTimeout(() => {
                        copyBtn.textContent = window.t ? t('finale.copyResult') : 'Ergebnis kopieren';
                    }, 2000);
                }).catch(() => {});
            };
        }
    }

    function handleError(msg) {
        console.warn('[Quizify] Error:', msg.code, msg.message);
        showErrorToast(msg.message || msg.code);
    }

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

    // ---- Power-ups ----

    const POWERUP_META = {
        joker: { icon: '🃏', label: 'Joker' },
        double_points: { icon: '2x', label: 'Doppelt' },
        freeze: { icon: '🧊', label: 'Einfrieren' },
        time_boost: { icon: '⏱️', label: '+5 Sek.' },
    };

    function handlePowerUpAssigned(msg) {
        myPowerUp = msg.powerup_type;
    }

    function renderPowerUp(type) {
        const meta = POWERUP_META[type] || { icon: '?', label: type };
        els.powerupIcon.textContent = meta.icon;
        els.powerupLabel.textContent = meta.label;
        els.powerupBtn.classList.remove('used');
    }

    function handlePowerUpApplied(msg) {
        if (msg.type === 'joker' && msg.joker_remove_index != null) {
            answerBtns.eliminate(msg.joker_remove_index);
        }
        myPowerUp = null;
        els.powerupBtn.classList.add('used');
        els.powerupBar.classList.add('hidden');
    }

    // ---- Renderers ----

    function renderPlayerList(players) {
        const list = Array.isArray(players)
            ? players
            : Object.values(players);
        els.playerCount.textContent = list.length;
        els.playerChips.innerHTML = list
            .map(p => {
                const name = typeof p === 'string' ? p : (p.name || p);
                return `<span class="player-chip"><span class="dot"></span>${escapeHtml(name)}</span>`;
            })
            .join('');
    }

    function renderPodium(podium) {
        // Reorder: [2nd, 1st, 3rd]
        const ordered = [];
        if (podium[1]) ordered.push({ ...podium[1], place: 2 });
        if (podium[0]) ordered.push({ ...podium[0], place: 1 });
        if (podium[2]) ordered.push({ ...podium[2], place: 2 + 1 });

        const medals = { 1: '🥇', 2: '🥈', 3: '🥉' };
        const barClass = { 1: 'first', 2: 'second', 3: 'third' };

        els.podium.innerHTML = ordered
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

    function renderLeaderboard(container, players) {
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

    function escapeHtml(text) {
        return QuizifyUtils.escapeHtml(text);
    }

    // ---- Event listeners ----

    // Name input → enable join button
    els.nameInput.addEventListener('input', () => {
        const val = els.nameInput.value.trim();
        els.joinBtn.disabled = val.length < 1;
    });

    // Enter key in name input
    els.nameInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !els.joinBtn.disabled) {
            els.joinBtn.click();
        }
    });

    // Join button
    els.joinBtn.addEventListener('click', () => {
        const name = els.nameInput.value.trim();
        if (!name) return;
        playerName = name;
        send('join', { name });
        // Optimistically show waiting
        els.joinSection.classList.add('hidden');
        els.waitingSection.classList.remove('hidden');
        joined = true;
    });

    // Answer callback
    answerBtns.onAnswer((index) => {
        send('submit_answer', { answer_index: index });
    });

    // Power-up button
    els.powerupBtn.addEventListener('click', () => {
        if (!myPowerUp) return;
        send('use_powerup', { target_player_id: null });
    });

    // Timer expired
    timerBar.onExpired(() => {
        answerBtns.disable();
    });

    // ---- Init ----
    // Auto-fill name from URL param ?name=... (used by admin self-join)
    const urlParams = new URLSearchParams(location.search);
    const prefilledName = urlParams.get('name');
    if (prefilledName) {
        els.nameInput.value = prefilledName;
        els.joinBtn.disabled = false;
    }

    // i18n init
    if (window.QuizifyI18n) {
        QuizifyI18n.init().then(function() {
            QuizifyI18n.initPageTranslations();
        });
    }

    updateConnectionStatus('reconnecting');
    connect();

    // Auto-join if name was pre-filled (wait for WS connection)
    if (prefilledName) {
        const autoJoinInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN && !joined) {
                clearInterval(autoJoinInterval);
                els.joinBtn.click();
            }
        }, 200);
        // Give up after 5s
        setTimeout(() => clearInterval(autoJoinInterval), 5000);
    }
})();
