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

    /**
     * Paint (or clear) the shared question banner (#802).
     *
     * The detour borrows ``#question-media`` from the normal round, and only
     * ``question_started`` ever wrote to it — so the seat holder answered
     * "Which animal is this?" under the PREVIOUS round's picture, or under
     * nothing at all, while the television showed the right one. An empty URL
     * hides the banner, which is what the auction wants: bidding is a bet on
     * yourself, not on a picture you have already seen.
     */
    function paintQuestionImage(imgUrl) {
        var fn = window.QuizifyPlayerGame
            && window.QuizifyPlayerGame.renderQuestionImageBanner;
        // No reveal style and no duration: the hot seat has no progressive
        // reveal, and passing the answer clock would blur a picture nobody
        // asked to have blurred.
        if (fn) fn(imgUrl || '', null, 0);
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
        _state.seatAnswering = false;
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

        // #802: the auction opens over whatever the last round left behind.
        // Clearing it here is the same move the wager window makes for the
        // same reason — the picture on screen belongs to a question that is
        // over, and here it would also be a free hint about a chair that is
        // still being bid on.
        paintQuestionImage('');

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
            // #804: ``winner`` is the person in the chair, ``entrant`` is who
            // pays — their team in team mode, the same person again otherwise.
            // The room is told the payer, because that is the row the points
            // move on.
            if (title) title.textContent = t('hotSeat.lost', {
                name: msg.entrant || msg.winner, pct: msg.pct, pts: msg.stake
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

        // #698: the detour used to play out under the *previous* round's
        // question and its green/red reveal colouring, because the snapshot
        // that opens the view clears neither and this handler never rendered
        // msg.question — which the server sends to every phone, seated or not.
        var qText = el('question-text');
        if (qText && msg.question) qText.textContent = msg.question;
        var qCat = el('question-category');
        if (qCat) qCat.textContent = '';
        // #802: the same hole one field over. The server sends image_url to
        // every phone — seated or betting — and nothing here read it, so a
        // picture question in the chair was answered blind while the previous
        // round's photo sat above it.
        paintQuestionImage(msg.image_url);
        // #847: the third leftover of the same family as #698 and #802, and
        // the expensive one. ``renderQuestion`` swaps the two input sections
        // per round type (#275): an estimate round hides #answers-container
        // and shows the slider. The auction fires between rounds and
        // ``hot_seat.py`` only keeps estimates out of the CHAIR's question,
        // not out of the rounds it interrupts — so the detour regularly opens
        // on top of a slider, and nothing here ever swapped the sections back.
        // On the live test the seat holder read the chair's question above the
        // previous round's slider, still showing their own submitted guess,
        // with no answers anywhere; the clock ran out and the settlement took
        // 60 points off them for a question they were never shown the answers
        // to. The slider belongs to a round that is over for everyone in the
        // room, seated or betting, so it goes away for everyone.
        var estimateSection = el('estimate-container');
        if (estimateSection) estimateSection.classList.add('hidden');

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

        // #804: a teammate of the seat holder is neither seated nor a
        // spectator. They stake the purse the chair already staked, so the
        // server refuses their bet — showing them the slider anyway would be
        // a control that silently does nothing.
        if (msg.you_are_seat_team) {
            var betStage = el('hotseat-bet-stage');
            if (betStage) betStage.classList.add('hidden');
            var teamTitle = el('hotseat-title');
            if (teamTitle) {
                teamTitle.textContent = t('hotSeat.teamSeated', {
                    name: msg.winner
                });
            }
            var teamHint = el('hotseat-hint');
            if (teamHint) teamHint.textContent = '';
            return;
        }

        showBetStage(msg.winner);
    }

    function renderSeatAnswers(answers) {
        // #847: the grid has to be back on screen before it is worth
        // filling. See ``handleQuestion`` for what hid it.
        var answersSection = el('answers-container');
        if (answersSection) answersSection.classList.remove('hidden');

        // #696: this used to replace the container's innerHTML with bare
        // buttons. The markup it destroyed is the markup renderQuestion fills
        // — that function reuses the existing .answer-btn elements and only
        // writes their .answer-text child, so once they were gone the seat
        // winner saw the hot seat's answers under every question for the rest
        // of the game and could not answer any of them. The grid is filled the
        // same way here, and left intact.
        var host = el('answer-buttons');
        if (!host) return;
        var buttons = host.querySelectorAll('.answer-btn');
        for (var i = 0; i < buttons.length; i++) {
            var btn = buttons[i];
            var text = answers[i];
            btn.dataset.index = String(i);
            btn.disabled = false;
            btn.classList.remove(
                'is-selected', 'is-correct', 'is-wrong', 'is-eliminated', 'hidden'
            );
            var textEl = btn.querySelector('.answer-text');
            if (textEl) {
                textEl.textContent =
                    ((text && typeof text === 'object') ? text.text : text) || '';
            }
            if (i >= answers.length) btn.classList.add('hidden');
        }
        _state.seatAnswering = true;
    }

    /**
     * Route a tap on the shared answer grid (#696).
     *
     * The delegated handler in player-core owns every click on
     * ``#answer-buttons``. While the seat holder is answering, the tap means
     * ``hot_seat_answer`` and not ``submit_answer``; an inline onclick used to
     * do this, and it shadowed the delegated handler badly enough that the
     * normal path stopped working afterwards.
     */
    function handleSeatAnswerClick(index) {
        if (!_state.seatAnswering) return false;
        var host = el('answer-buttons');
        if (host) {
            var buttons = host.querySelectorAll('.answer-btn');
            for (var i = 0; i < buttons.length; i++) {
                buttons[i].disabled = true;
                if (i === index) buttons[i].classList.add('is-selected');
            }
        }
        _state.seatAnswering = false;
        send('hot_seat_answer', { answer: index });
        return true;
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

    /**
     * Rebuild the panel from a state snapshot (#664).
     *
     * The live flow is driven entirely by one-shot events, which a reload
     * misses for good — they are never re-sent. This maps the snapshot's
     * hot_seat block onto the same handlers the live events call, so a phone
     * that reconnects mid-detour lands where it left off instead of on the
     * lobby. It matters most for the seat holder: they cannot answer a
     * question they cannot see, and an unanswered question costs the stake.
     */
    function restoreFromSnapshot(hs) {
        if (!hs) return;
        var stage = hs.stage;

        if (stage === 'auction') {
            handleAuctionYou({ score: hs.own_bank });
            handleBidCount({ count: hs.bid_count, total: hs.bidder_count });
            if (hs.you_bid != null) {
                // Already bid before the reload. The server rejects a second
                // bid anyway, so a live-looking slider would only invite a tap
                // that silently does nothing.
                lockBidUi(hs.you_bid);
            }
            return;
        }

        if (stage === 'awarded' || stage === 'result') {
            handleAwarded({
                winner: hs.winner,
                pct: hs.pct,
                stake: hs.stake,
                bids: hs.bids || []
            });
            return;
        }

        if (stage === 'question') {
            handleAwarded({
                winner: hs.winner,
                pct: hs.pct,
                stake: hs.stake,
                bids: hs.bids || []
            });
            handleQuestion(questionMessageFromSnapshot(hs));
            if (hs.you_bet) lockBetUi(hs.you_bet);
        }
    }

    /**
     * A snapshot's ``hot_seat`` block, in the shape ``hot_seat_question`` has
     * (#730).
     *
     * The hand-written list this replaces forgot the question itself. The
     * snapshot has carried ``hot_seat.question.text`` since #664 and the live
     * handler has rendered it since #698 — but the restore path named four
     * fields and ``question`` was not one of them, so the seat holder whose
     * phone locked came back to three answer buttons under a blank question
     * (fresh page) or the previous round's (in-tab reconnect), with the clock
     * running and an unanswered question costing the whole stake (#653).
     *
     * Forwarding the frame is what stops that happening a third time: every
     * key the server puts in the question block rides along, and only the
     * fields that live outside it, or that a restore must recompute, are named
     * here. See tests/test_snapshot_restore_parity_730_731.py.
     */
    function questionMessageFromSnapshot(hs) {
        var q = hs.question || {};
        var msg = {};
        for (var k in q) {
            if (Object.prototype.hasOwnProperty.call(q, k)) msg[k] = q[k];
        }
        // The live event's name for the same string — and the field #730 is
        // about.
        msg.question = q.text;
        msg.answers = q.answers || [];
        // Block-level, not question-level.
        msg.winner = hs.winner;
        msg.you_are_seated = !!hs.you_are_seated;
        // The live payload sends the full answer window; a phone rejoining
        // mid-window gets what the room has left of it, never a fresh one.
        msg.seconds = hs.time_remaining;
        // The bank the bets are percentages of, as of the auction.
        msg.score = hs.own_bank;
        return msg;
    }

    function lockBidUi(pct) {
        _state.bidPlaced = true;
        var slider = el('hotseat-slider');
        var btn = el('hotseat-bid-btn');
        if (slider) slider.disabled = true;
        if (btn) btn.disabled = true;
        var hint = el('hotseat-hint');
        if (hint) {
            hint.textContent = t('hotSeat.bidPlaced', {
                pct: pct,
                pts: Math.floor((_state.score || 0) * pct / 100)
            });
        }
    }

    function lockBetUi(bet) {
        _state.betPlaced = true;
        var hint = el('hotseat-hint');
        if (hint) {
            hint.textContent = t('hotSeat.betPlaced', {
                pct: bet.pct,
                side: bet.side
            });
        }
    }

    window.QuizifyPlayerHotSeat = {
        restoreFromSnapshot: restoreFromSnapshot,
        handleAuctionYou: handleAuctionYou,
        handleBidCount: handleBidCount,
        handleAwarded: handleAwarded,
        handleNoBids: handleNoBids,
        handleQuestion: handleQuestion,
        handleSeatAnswerClick: handleSeatAnswerClick,
        handleResult: handleResult,
        handleTick: handleTick,
        reset: reset
    };

})();
