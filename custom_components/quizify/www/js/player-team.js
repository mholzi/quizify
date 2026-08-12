/**
 * Quizify Player - Teams (#365)
 *
 * Three screens, all of them quiet by design:
 *
 *  1. The lobby (route C). One question first — alone, or with someone? Only
 *     a player who says "with someone" ever sees a team list, and the name is
 *     a suggestion rather than a field to fill in. Two cases the lobby answers
 *     itself, or the host gets asked: a latecomer after the start (teams are
 *     fixed, play alone) and the last member leaving (team dissolved, you play
 *     for yourself).
 *  2. The question screen. The standing answer shows as small member dots on
 *     the answer it sits on — no banner. Disagreement therefore shows as dots
 *     on two different rows. Known cost, accepted: someone mid-tap notices a
 *     change only if they look.
 *  3. The reveal and the finale need nothing here — they are the existing
 *     screens fed with teams instead of players.
 */

(function () {
    'use strict';

    var pu = window.QuizifyPlayerUtils;
    var state = pu.state;

    function t(key, params) {
        var i18n = window.QuizifyI18n;
        return (i18n && i18n.t) ? i18n.t(key, params) : key;
    }

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // Soft Parlor palette, keyed by the colour names the server hands out.
    var TEAM_COLORS = {
        coral: '#E88A7F', sage: '#8FA98A', sky: '#7FA8C9',
        sun: '#E2B65C', mauve: '#A98FA9', brick: '#B4705C'
    };

    function colorOf(team) {
        return (team && TEAM_COLORS[team.color]) || '#A89E89';
    }

    // ---- module state ------------------------------------------------
    // `teams` is the whole room's teams; `myTeamId` is ours. Both come from
    // the server — nothing here is inferred, because a wrong guess about who
    // is in which team is invisible until the scores come out wrong.
    var teams = [];
    var myTeamId = null;
    var send = null;
    var pickOpen = false;   // did this player say "with someone"?
    var lockTimer = null;

    function myTeam() {
        for (var i = 0; i < teams.length; i++) {
            if (teams[i].team_id === myTeamId) return teams[i];
        }
        return null;
    }

    function isTeamMode() {
        return !!myTeamId;
    }

    // ==================================================================
    // Lobby (route C)
    // ==================================================================

    function renderLobby() {
        var section = $('team-section');
        if (!section) return;

        var mine = myTeam();
        var ask = $('team-ask');
        var pick = $('team-pick');
        var mineEl = $('team-mine');

        // In a team: the list and the question step away entirely. What is
        // left is the one thing that still matters — who is in it.
        if (mine) {
            if (ask) ask.classList.add('hidden');
            if (pick) pick.classList.add('hidden');
            if (mineEl) mineEl.classList.remove('hidden');
            var dot = $('team-mine-dot');
            if (dot) dot.style.background = colorOf(mine);
            var nameEl = $('team-mine-name');
            if (nameEl) nameEl.textContent = mine.name;
            var membersEl = $('team-mine-members');
            if (membersEl) {
                var others = (mine.members || []).filter(function (n) {
                    return n !== state.playerName;
                });
                membersEl.textContent = others.length
                    ? others.join(' · ')
                    : t('teams.waitingForTeammate');
            }
            return;
        }

        if (mineEl) mineEl.classList.add('hidden');
        if (ask) ask.classList.toggle('hidden', pickOpen);
        if (pick) pick.classList.toggle('hidden', !pickOpen);
        if (!pickOpen) return;

        var list = $('team-list');
        if (list) {
            list.innerHTML = teams.map(function (team) {
                return '<button type="button" class="qz-team-item" data-team="' + esc(team.team_id) + '">' +
                    '<span class="qz-team-itemDot" style="background:' + colorOf(team) + '"></span>' +
                    '<span class="qz-team-itemName">' + esc(team.name) + '</span>' +
                    '<span class="qz-team-itemMembers">' + esc((team.members || []).join(' · ')) + '</span>' +
                    '<span class="qz-team-itemJoin">' + esc(t('teams.joinTeam')) + '</span>' +
                '</button>';
            }).join('');
        }
        var empty = $('team-empty');
        if (empty) empty.classList.toggle('hidden', teams.length > 0);
    }

    function showNote(message) {
        var note = $('team-note');
        if (!note) return;
        note.textContent = message;
        note.classList.remove('hidden');
    }

    function openNameRow(show) {
        var row = $('team-name-row');
        var hint = $('team-name-hint');
        var openBtn = $('team-open-btn');
        if (row) row.classList.toggle('hidden', !show);
        if (hint) hint.classList.toggle('hidden', !show);
        if (openBtn) openBtn.classList.toggle('hidden', show);
        if (show) {
            var input = $('team-name-input');
            if (input) { input.value = ''; input.focus(); }
        }
    }

    function setupLobby(sendFn) {
        send = sendFn;

        var aloneBtn = $('team-alone-btn');
        if (aloneBtn) {
            aloneBtn.addEventListener('click', function () {
                // Answering "alone" is a real answer: the whole team block
                // disappears rather than sitting there asking again.
                pickOpen = false;
                var section = $('team-section');
                if (section) section.classList.add('hidden');
            });
        }

        var togetherBtn = $('team-together-btn');
        if (togetherBtn) {
            togetherBtn.addEventListener('click', function () {
                pickOpen = true;
                renderLobby();
            });
        }

        var openBtn = $('team-open-btn');
        if (openBtn) openBtn.addEventListener('click', function () { openNameRow(true); });

        var cancelBtn = $('team-name-cancel');
        if (cancelBtn) cancelBtn.addEventListener('click', function () { openNameRow(false); });

        var confirmBtn = $('team-name-confirm');
        if (confirmBtn) {
            confirmBtn.addEventListener('click', function () {
                var input = $('team-name-input');
                // Empty is allowed on purpose — the server names the team.
                send('create_team', { name: input ? input.value.trim() : '' });
                openNameRow(false);
            });
        }

        var input = $('team-name-input');
        if (input) {
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    if (confirmBtn) confirmBtn.click();
                }
            });
        }

        var list = $('team-list');
        if (list) {
            list.addEventListener('click', function (e) {
                var item = e.target.closest ? e.target.closest('.qz-team-item') : null;
                if (!item) return;
                send('join_team', { team_id: item.getAttribute('data-team') });
            });
        }

        var leaveBtn = $('team-leave-btn');
        if (leaveBtn) {
            leaveBtn.addEventListener('click', function () { send('leave_team', {}); });
        }
    }

    // ==================================================================
    // Server messages
    // ==================================================================

    function handleTeamsUpdate(msg) {
        teams = msg.teams || [];
        // Our own membership can change without us doing anything — the last
        // member leaving dissolves the team we were in. Re-derive it from the
        // roster rather than trusting the id we remember.
        var found = null;
        for (var i = 0; i < teams.length; i++) {
            if ((teams[i].members || []).indexOf(state.playerName) !== -1) {
                found = teams[i];
                break;
            }
        }
        var hadTeam = myTeamId !== null;
        myTeamId = found ? found.team_id : null;
        if (hadTeam && !found) {
            pickOpen = true;
            showNote(t('teams.dissolved'));
        }
        renderLobby();
        renderChip();
    }

    function handleTeamJoined(msg) {
        var team = msg.team || {};
        myTeamId = team.team_id || null;
        // Fold the confirmed team into the roster right away. The broadcast
        // that carries it to everyone else is a separate message, and waiting
        // for it would leave the founder looking at an unchanged lobby for a
        // round-trip — the one moment she is watching for a sign that it
        // worked.
        var known = false;
        for (var i = 0; i < teams.length; i++) {
            if (teams[i].team_id === myTeamId) { teams[i] = team; known = true; break; }
        }
        if (!known && myTeamId) teams.push(team);
        var note = $('team-note');
        if (note) note.classList.add('hidden');
        renderLobby();
        renderChip();
    }

    function handleTeamLeft() {
        myTeamId = null;
        pickOpen = true;
        renderLobby();
        renderChip();
    }

    /**
     * A refused team action. The lobby answers the two awkward cases itself
     * instead of leaving the player to ask the host.
     */
    function handleTeamError() {
        showNote(t('teams.closedAfterStart'));
        pickOpen = false;
        renderLobby();
    }

    // ==================================================================
    // Question screen — dots on the answer row
    // ==================================================================

    function renderChip(setBy) {
        var chip = $('team-chip');
        if (!chip) return;
        var mine = myTeam();
        if (!mine) {
            chip.classList.add('hidden');
            return;
        }
        chip.classList.remove('hidden');
        var dot = $('team-chip-dot');
        if (dot) dot.style.background = colorOf(mine);
        var nameEl = $('team-chip-name');
        if (nameEl) nameEl.textContent = mine.name;
        var setEl = $('team-chip-set');
        if (setEl) {
            setEl.textContent = setBy ? t('teams.standingAnswer', { name: setBy }) : '';
        }
    }

    function clearDots() {
        var buttons = document.querySelectorAll('#answer-buttons .answer-btn');
        for (var i = 0; i < buttons.length; i++) {
            buttons[i].classList.remove('has-team-answer');
            var old = buttons[i].querySelector('.qz-team-dots');
            if (old) old.remove();
        }
    }

    /**
     * Paint the standing team answer.
     *
     * ``msg.answer_index`` is already expressed in THIS phone's answer order —
     * the server remaps it per member, because every player sees the answers
     * shuffled differently (#253). Doing that mapping here would be guesswork.
     */
    function handleTeamAnswer(msg) {
        clearDots();
        var buttons = document.querySelectorAll('#answer-buttons .answer-btn');
        var mine = myTeam();
        for (var i = 0; i < buttons.length; i++) {
            if (parseInt(buttons[i].dataset.index, 10) !== msg.answer_index) continue;
            buttons[i].classList.add('has-team-answer');
            var wrap = document.createElement('span');
            wrap.className = 'qz-team-dots';
            var members = msg.members || (mine && mine.members) || [];
            wrap.innerHTML = members.map(function (name) {
                var initial = (name || '?').charAt(0).toUpperCase();
                var isSetter = name === msg.set_by;
                return '<span class="qz-team-dot' + (isSetter ? ' is-setter' : '') + '"' +
                    ' style="background:' + colorOf(mine) + '" title="' + esc(name) + '">' +
                    esc(initial) + '</span>';
            }).join('');
            buttons[i].appendChild(wrap);
        }
        renderChip(msg.set_by);
        applyLock(msg.lock_seconds || 0);
    }

    /**
     * The brake. It belongs to the team, so every member's buttons go quiet
     * for the same short moment — that is what stops two people flipping the
     * answer back and forth, rather than slowing one of them down.
     */
    function applyLock(seconds) {
        var buttons = document.querySelectorAll('#answer-buttons .answer-btn');
        if (lockTimer) { clearTimeout(lockTimer); lockTimer = null; }
        if (seconds <= 0) return;

        for (var i = 0; i < buttons.length; i++) {
            buttons[i].disabled = true;
            buttons[i].classList.add('is-team-locked');
        }
        lockTimer = setTimeout(function () {
            var btns = document.querySelectorAll('#answer-buttons .answer-btn');
            for (var j = 0; j < btns.length; j++) {
                btns[j].disabled = false;
                btns[j].classList.remove('is-team-locked');
            }
            lockTimer = null;
        }, seconds * 1000);
    }

    /** Called at the start of every round — the answer does not carry over. */
    function resetRound() {
        if (lockTimer) { clearTimeout(lockTimer); lockTimer = null; }
        clearDots();
        renderChip();
    }

    // ==================================================================
    // Export
    // ==================================================================

    window.QuizifyPlayerTeam = {
        setupLobby: setupLobby,
        isTeamMode: isTeamMode,
        myTeam: myTeam,
        handleTeamsUpdate: handleTeamsUpdate,
        handleTeamJoined: handleTeamJoined,
        handleTeamLeft: handleTeamLeft,
        handleTeamError: handleTeamError,
        handleTeamAnswer: handleTeamAnswer,
        renderChip: renderChip,
        resetRound: resetRound
    };

})();
