/**
 * Quizify Player — Hot Seat auction (#616)
 *
 * Three states share one panel, because they are one continuous moment for
 * the player: bid for the chair, then either answer it alone or stake on
 * whoever won it.
 *
 * The bid is a PERCENT of your own score, never a number of points. In points
 * the auction would always go to whoever is already ahead, which is the
 * opposite of what this mode is for — see game/hot_seat.py for the full
 * reasoning. The slider therefore shows both numbers: the percentage you are
 * committing and what it costs you right now.
 */

(function () {
    'use strict';

    var _state = {
        bidding: false,
        bidPlaced: false,
        seated: false,
        betPlaced: false,
        score: 0,
        winner: null
    };

    function t(key, vars) {
        var fn = window.QuizifyI18n && window.QuizifyI18n.t;
        return fn ? fn(key, vars) : key;
    }

    function send(type, payload) {
        var fn = window.QuizifyPlayer && window.QuizifyPlayer.send;
        if (fn) fn(type, payload);
    }

    function el(id) {
        return document.getElementById(id);
    }

    function panel() {
        return el('hotseat-panel');
    }

    function hide() {
        var p = panel();
        if (p) p.classList.add('hidden');
        _state.bidding = false;
    }

    function reset() {
        _state.bidding = false;
        _state.bidPlaced = false;
        _state.seated = false;
        _state.betPlaced = false;
        _state.winner = null;
        hide();
    }

    // ------------------------------------------------------------------
    // Bidding
    // ------------------------------------------------------------------

    function handleAuctionYou(msg) {
        var p = panel();
        if (!p) return;
        _state.bidding = true;
        _state.bidPlaced = false;
        _state.seated = false;
        _state.betPlaced = false;
        _state.score = msg.score || 0;

        p.classList.remove('hidden');
        p.classList.remove('wager-panel--collapsed');

        var bidStage = el('hotseat-bid-stage');
        var betStage = el('hotseat-bet-stage');
        if (bidStage) bidStage.classList.remove('hidden');
        if (betStage) betStage.classList.add('hidden');

        var title = el('hotseat-title');
        var hint = el('hotseat-hint');
        var bank = el('hotseat-bank');
        var slider = el('hotseat-slider');
        var btn = el('hotseat-bid-btn');
        var count = el('hotseat-bid-count');

        if (title) title.textContent = t('hotSeat.auctionTitle');
        if (hint) hint.textContent = t('hotSeat.auctionHint');
        if (bank) bank.textContent = _state.score;
        if (count) count.textContent = t('hotSeat.sealed');

        if (slider) {
            slider.value = '25';
            slider.disabled = false;
            slider.oninput = syncBidValue;
            syncBidValue();
        }
        if (btn) {
            btn.disabled = false;
            btn.textContent = t('hotSeat.bidSubmit');
            btn.onclick = submitBid;
        }
    }

    function syncBidValue() {
        var slider = el('hotseat-slider');
        var out = el('hotseat-value');
        if (!slider || !out) return;
        var pct = parseInt(slider.value, 10);
        // Show the points too: a percentage on its own tells you nothing
        // about what you are risking.
        var pts = Math.floor(_state.score * pct / 100);
        out.textContent = t('wager.valueFmt', { pct: pct, pts: pts });
    }

    function submitBid() {
        if (_state.bidPlaced) return;
        var slider = el('hotseat-slider');
        if (!slider) return;
        var pct = parseInt(slider.value, 10);
        _state.bidPlaced = true;
        slider.disabled = true;
        var btn = el('hotseat-bid-btn');
        if (btn) btn.disabled = true;
        send('hot_seat_bid', { bid: pct });

        var hint = el('hotseat-hint');
        if (hint) {
            hint.textContent = t('hotSeat.bidPlaced', {
                pct: pct,
                pts: Math.floor(_state.score * pct / 100)
            });
        }
    }

    function handleBidCount(msg) {
        // Only the count, never the amounts — the auction is sealed.
        var count = el('hotseat-bid-count');
        if (!count || !_state.bidding) return;
        count.textContent = t('hotSeat.bidCount', {
            count: msg.count || 0,
            total: msg.total || 0
        });
    }

    // ------------------------------------------------------------------
    // Award
    // ------------------------------------------------------------------

    function handleAwarded(msg) {
        var utils = window.QuizifyPlayerUtils;
        var me = (utils && utils.state) ? utils.state.playerName : null;
        _state.winner = msg.winner;
        _state.seated = (me != null && me === msg.winner);

        var title = el('hotseat-title');
        var hint = el('hotseat-hint');
        var bidStage = el('hotseat-bid-stage');
        if (bidStage) bidStage.classList.add('hidden');

        if (_state.seated) {
            if (title) title.textContent = t('hotSeat.won', {
                pct: msg.pct, pts: msg.stake
            });
            if (hint) hint.textContent = t('hotSeat.seatedHint');
        } else {
            if (title) title.textContent = t('hotSeat.lost', {
                name: msg.winner, pct: msg.pct, pts: msg.stake
            });
            if (hint) hint.textContent = '';
        }
    }

    function handleNoBids() {
        var hint = el('hotseat-hint');
        if (hint) hint.textContent = t('hotSeat.noBids');
        reset();
    }

    // ------------------------------------------------------------------
    // The question
    // ------------------------------------------------------------------

    function handleQuestion(msg) {
        var p = panel();
        if (!p) return;
        p.classList.remove('hidden');

        if (msg.you_are_seated) {
            // The seat holder answers through the normal answer grid, so the
            // panel steps out of the way and only keeps the reminder that a
            // silent clock costs the same as a wrong tap.
            var bidStage = el('hotseat-bid-stage');
            var betStage = el('hotseat-bet-stage');
            if (bidStage) bidStage.classList.add('hidden');
            if (betStage) betStage.classList.add('hidden');
            var hint = el('hotseat-hint');
            if (hint) hint.textContent = t('hotSeat.timeoutNote');
            renderSeatAnswers(msg.answers || []);
            return;
        }

        _state.score = typeof msg.score === 'number' ? msg.score : _state.score;
        showBetStage(msg.winner);
    }

    function renderSeatAnswers(answers) {
        var host = el('answer-buttons');
        if (!host) return;
        host.innerHTML = '';
        answers.forEach(function (text, i) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'answer-btn';
            b.textContent = text;
            b.onclick = function () {
                host.querySelectorAll('.answer-btn').forEach(function (x) {
                    x.disabled = true;
                });
                b.classList.add('answer-btn--selected');
                send('hot_seat_answer', { answer: i });
            };
            host.appendChild(b);
        });
    }

    function showBetStage(winner) {
        var betStage = el('hotseat-bet-stage');
        var title = el('hotseat-title');
        var hint = el('hotseat-hint');
        if (betStage) betStage.classList.remove('hidden');
        if (title) title.textContent = t('hotSeat.betTitle', { name: winner });
        if (hint) hint.textContent = '';

        var slider = el('hotseat-bet-slider');
        if (slider) {
            slider.disabled = false;
            slider.oninput = syncBetValue;
            syncBetValue();
        }
        wireBet('hotseat-bet-will', 'will');
        wireBet('hotseat-bet-wont', 'wont');
    }

    function syncBetValue() {
        var slider = el('hotseat-bet-slider');
        var out = el('hotseat-bet-value');
        if (!slider || !out) return;
        var pct = parseInt(slider.value, 10);
        out.textContent = t('wager.valueFmt', {
            pct: pct,
            pts: Math.floor(_state.score * pct / 100)
        });
    }

    function wireBet(id, side) {
        var btn = el(id);
        if (!btn) return;
        btn.disabled = false;
        btn.onclick = function () {
            if (_state.betPlaced) return;
            var slider = el('hotseat-bet-slider');
            var pct = slider ? parseInt(slider.value, 10) : 0;
            _state.betPlaced = true;
            if (slider) slider.disabled = true;
            ['hotseat-bet-will', 'hotseat-bet-wont'].forEach(function (x) {
                var b = el(x);
                if (b) b.disabled = true;
            });
            send('hot_seat_bet', { side: side, bet: pct });
            var hint = el('hotseat-hint');
            if (hint) {
                hint.textContent = t('hotSeat.betPlaced', {
                    pct: pct,
                    side: t(side === 'will' ? 'hotSeat.betWill' : 'hotSeat.betWont')
                });
            }
        };
    }

    // ------------------------------------------------------------------
    // Result
    // ------------------------------------------------------------------

    function handleResult() {
        // The reveal screen owns the outcome; the panel's job is done.
        reset();
    }

    function handleTick(msg) {
        var fn = window.QuizifyPlayerGame && window.QuizifyPlayerGame.updateTimer;
        if (fn) fn(msg.remaining);
    }

    window.QuizifyPlayerHotSeat = {
        handleAuctionYou: handleAuctionYou,
        handleBidCount: handleBidCount,
        handleAwarded: handleAwarded,
        handleNoBids: handleNoBids,
        handleQuestion: handleQuestion,
        handleResult: handleResult,
        handleTick: handleTick,
        reset: reset
    };

})();
