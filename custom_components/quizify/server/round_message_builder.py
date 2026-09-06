"""RoundMessageBuilder — assembles the per-round client messages.

Extracted from ``QuizifyWebSocketHandler`` (issue #189, part of the #184
God-object split, following the BroadcastDispatcher seam from #187). The
handler owns the *sending* (it is tightly coupled to the connection manager)
and the *shuffle mutation* (side effects on game state); this builder owns the
**pure message assembly** — turning the current game state plus the round's
shuffles into the exact payload dicts that get sent to players, admin,
dashboards, and the round-summary broadcast.

Every method is a pure, side-effect-free computation that reads game state and
the shared serializers and returns plain dicts (or lists of them). The wire
shapes are byte-for-byte identical to the previous inline code in
``_start_next_question`` and ``_broadcast_round_summary`` — same fields, same
ordering, same serializer calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.server.serializers import (
    serialize_leaderboard,
    serialize_player_list,
    serialize_question_for_admin,
    serialize_question_for_player,
    serialize_round_summary,
    serialize_wager_window,
)

if TYPE_CHECKING:
    from custom_components.quizify.game.player import PlayerSession
    from custom_components.quizify.game.questions import Question
    from custom_components.quizify.game.state import QuizifyGameState


class RoundMessageBuilder:
    """Builds the per-round question and result payloads.

    Stateless: every method takes the current game state (and, where relevant,
    the already-fetched player list / question) and returns plain payload
    dicts. No connection access, no mutation — the handler keeps ownership of
    both. Behaviour mirrors the previous inline assembly exactly.
    """

    def build_wager_window(
        self,
        game_state: QuizifyGameState,
        *,
        question: Question,
        player: PlayerSession,
        window_duration: float,
    ) -> dict[str, Any]:
        """Build one player's betting-window payload (#656).

        Per-player because the bank shown on the slider is the score the bet is
        staked against — their own, or their team's in team mode (#804). It has
        to be the participant's, not ``player.score``: in team mode the latter
        is the shadow number #668 was filed about, so the slider used to price
        the bet against something no screen shows and nothing settles.

        Carries no question text — see ``serialize_wager_window``.
        """
        participant = game_state.get_ranked_participant_for(player.name) or player
        return serialize_wager_window(
            question=question,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            window_duration=window_duration,
            player_score=participant.score,
        )

    def build_wager_progress(
        self,
        game_state: QuizifyGameState,
        *,
        window_duration: float | None = None,
    ) -> dict[str, Any]:
        """Build the host/TV view of the betting window (#656).

        Counts locked-in bets and names who the room is still waiting for —
        never the amounts. A bet the TV gave away would not be a bet. The
        optional ``window_duration`` marks the opening message; refreshes sent
        on each incoming wager leave it out so the TV countdown, already
        running, is not restarted.
        """
        # Counted in PARTICIPANTS (#804): one bet per team, so a tally over
        # people would read "1 of 5 in" for a room where every team has
        # decided, and would never reach the count that closes the window.
        connected = {p.name for p in game_state.get_players() if p.connected}
        entrants = [
            participant
            for participant in game_state.get_ranked_participants()
            if any(
                member in connected
                for member in (
                    getattr(participant, "members", None) or [participant.name]
                )
            )
        ]
        waiting = game_state.players_missing_wager()
        payload: dict[str, Any] = {
            "type": "wager_progress",
            "round_num": game_state.round,
            "total_rounds": game_state.total_rounds,
            "locked_in": len(entrants) - len(waiting),
            "player_count": len(entrants),
            "waiting_on": waiting,
        }
        if window_duration is not None:
            question = game_state.get_current_question()
            payload["window_duration"] = window_duration
            payload["category"] = question.category if question else ""
            payload["difficulty"] = question.difficulty if question else ""
        return payload

    def build_player_question(
        self,
        game_state: QuizifyGameState,
        *,
        question: Question,
        player: PlayerSession,
        is_final: bool,
    ) -> dict[str, Any]:
        """Build the per-player question payload (own shuffled answer order).

        Mirrors the inline body of the per-player loop in
        ``_start_next_question``: look up the player's shuffle, project the
        answer texts in that order, and serialize. The handler decides whether
        to send (skips disconnected players) — this method does not.
        """
        shuffle = game_state.get_player_shuffle(player.name)
        shuffled_answers = [question.answers[i].text for i in shuffle]
        # The wager picker prices the bet against this number, so in team mode
        # it has to be the team's score (#804) — the same one the wager window
        # already showed a moment earlier.
        participant = game_state.get_ranked_participant_for(player.name) or player
        return serialize_question_for_player(
            question=question,
            shuffled_answers=shuffled_answers,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            timer_duration=game_state.round_duration,
            is_final_round=is_final,
            player_score=participant.score,
        )

    def build_admin_question(
        self,
        game_state: QuizifyGameState,
        *,
        question: Question,
    ) -> dict[str, Any]:
        """Build the admin/dashboard question payload (with correct answer).

        The answer grid rides the round's canonical shuffle (#521) so the TV
        does not sit in question-JSON order, where most packs keep the correct
        answer first.
        """
        return serialize_question_for_admin(
            question=question,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            timer_duration=game_state.round_duration,
            display_order=game_state.shuffle_map,
        )

    def build_game_state_with_leaderboard(
        self,
        game_state: QuizifyGameState,
        *,
        players: list[PlayerSession],
    ) -> dict[str, Any]:
        """Build the ``game_state`` broadcast carrying the live leaderboard.

        Mirrors the trailing broadcast in ``_start_next_question`` so players
        see rankings during the round. The caller passes the already-fetched
        player list to avoid a redundant ``get_players`` call.

        #835: the ``leaderboard`` is built from
        ``get_ranked_participants()`` — the rows the room can see — the way
        ``serialize_state_snapshot`` and every reveal-path builder already do
        it. This was the one leaderboard in the codebase still serialized
        straight off the player list, and it is the one the television shows
        *during* a question: so in team mode the board listed four individuals
        on 0 while the question was live and three teams the moment the reveal
        landed, flipping identity twice a round. Outside team mode the two
        lists are the same object's contents and nothing changes.

        ``players`` stays player-derived, mirroring the canonical snapshot
        where ``players`` is a roster and ``leaderboard`` is the ranking.
        """
        leaderboard = serialize_leaderboard(game_state.get_ranked_participants())
        payload: dict[str, Any] = {
            "type": "game_state",
            "phase": game_state.phase.value,
            "round": game_state.round,
            "total_rounds": game_state.total_rounds,
            "player_count": len(players),
            "players": serialize_leaderboard(players),
            "leaderboard": leaderboard,
        }
        # During a lightning round (#42) a leaderboard-refresh ``game_state``
        # must carry the lightning sub-state, or the client's LIGHTNING case
        # (player-core.js ~437) falls through to an empty ``lightning-view`` —
        # i.e. a blank player screen (#221). Mirror the canonical shape built
        # by ``serialize_state_snapshot()`` so reconnect snapshots
        # and live refreshes are wire-identical.
        lightning = self._build_lightning_substate(game_state)
        if lightning is not None:
            payload["lightning"] = lightning
        return payload

    def _build_lightning_substate(
        self, game_state: QuizifyGameState
    ) -> dict[str, Any] | None:
        """Build the ``lightning`` sub-object for a LIGHTNING ``game_state``.

        Returns ``None`` outside a lightning round. The shape is byte-for-byte
        identical to the lightning branch of
        ``serialize_state_snapshot()`` so the player client reads
        the same keys regardless of which builder produced the message:

        * ``splash_pending`` — True while the intro splash (#201) is up and no
          question has been broadcast yet (``question`` is then omitted).
        * ``index`` / ``num_questions`` / ``seconds_per_question`` — progress.
        * ``time_remaining`` — seconds left on the current question.
        * ``question`` — canonical (admin/TV) answer order; players still get
          their own shuffle via the ``lightning_question`` event.
        """
        if game_state.phase != GamePhase.LIGHTNING:
            return None
        lr = game_state.lightning
        if lr is None:
            return None
        substate: dict[str, Any] = {
            "index": lr.index,
            "num_questions": lr.num_questions,
            "time_remaining": round(lr.time_remaining(), 1),
            "seconds_per_question": lr.seconds_per_question,
            "leaderboard": lr.leaderboard(),
            "splash_pending": game_state.lightning_splash_pending,
        }
        q = lr.current_question
        if q is not None:
            substate["question"] = {
                "text": q.question,
                "answers": [a.text for a in q.answers],
                "category": q.category,
                "image_url": q.image_url,
            }
        return substate

    def project_snapshot_for_player(
        self,
        game_state: QuizifyGameState,
        *,
        snapshot: dict[str, Any],
        player: PlayerSession,
    ) -> dict[str, Any]:
        """Re-shape a canonical state snapshot for one reconnecting PLAYER.

        ``serialize_state_snapshot()`` is player-agnostic — it
        emits answers in canonical order and nests the reveal under
        ``round_summary`` (issue #253). That is correct for the admin and the
        TV dashboard, but a player rebuilds their answer buttons from this
        snapshot and ``submit_answer`` maps the tapped index through the
        player's OWN shuffle. Sending canonical order would mis-score ~2/3 of
        taps after a reload. This projects a *copy* of the snapshot into the
        recipient's frame:

        * QUESTION_ACTIVE: project ``question.answers`` through the player's
          shuffle (created on demand for a late joiner, as round-start does),
          and use the player's own QuestionTimer for ``time_remaining`` so a
          pause/freeze/boost is reflected on the reconnect clock.
        * LIGHTNING: project ``lightning.question.answers`` through the
          player's lightning shuffle (also created on demand).
        * ANSWER_REVEAL: replace the nested ``round_summary`` with the same
          FLAT shape the live ``round_summary`` broadcast uses (incl.
          ``all_answers``), which the reveal view reads as flat fields.

        The caller (``_handle_join`` / ``_handle_reconnect``) knows the
        recipient identity; admin/dashboard recipients keep the canonical
        snapshot untouched.
        """
        # Shallow copy is enough: we only replace top-level keys / nested
        # dicts we build fresh here, never mutate the caller's sub-objects.
        out = dict(snapshot)

        phase = out.get("phase")

        if phase == GamePhase.QUESTION_ACTIVE.value and "question" in out:
            question = game_state.get_current_question()
            if question is not None:
                shuffle = game_state.ensure_player_shuffle(player.name)
                projected = dict(out["question"])
                projected["answers"] = [
                    question.answers[i].text for i in shuffle
                ]
                # Prefer the player's own timer so pause/freeze/boost show
                # the right remaining time; fall back to the wall-clock value
                # already in the snapshot when no per-player timer exists.
                timer = game_state.get_player_timer(player.name)
                if timer is not None:
                    projected["time_remaining"] = round(
                        max(0.0, timer.get_remaining()), 1
                    )
                out["question"] = projected

        if phase == GamePhase.LIGHTNING.value and out.get("lightning"):
            lr = game_state.lightning
            lightning = dict(out["lightning"])
            if lr is not None and lr.current_question is not None:
                shuffle = lr.ensure_shuffle(player.name)
                q = lr.current_question
                lq = dict(lightning["question"])
                lq["answers"] = [q.answers[i].text for i in shuffle]
                lightning["question"] = lq
                out["lightning"] = lightning

        if out.get("hot_seat"):
            # #664: the canonical block is built for the admin and the TV. A
            # player must see the question in THEIR shuffle if they hold the
            # chair, and must not see answer buttons at all if they do not —
            # spectators stake on the seat holder, they do not answer.
            hs = game_state.hot_seat
            block = dict(out["hot_seat"])
            # Keyed by ENTRANT (#804): the player's own name outside team mode,
            # their team's id inside it. Read by player name in team mode this
            # returned 0 for everybody, which is the shadow bank #669 gated the
            # whole mode off for.
            entrant = (
                hs.entrant_for(player.name)
                if hs is not None
                else game_state.entrant_key_for_player(player.name)
            )
            block["own_bank"] = block.get("banks", {}).get(entrant, 0)
            block.pop("banks", None)
            if hs is not None:
                bid = hs.bids.get(entrant)
                block["you_bid"] = bid.pct if bid is not None else None
                bet = hs.bets.get(entrant)
                block["you_bet"] = (
                    {"side": bet.side, "pct": bet.pct} if bet is not None else None
                )
                # The chair holds a PERSON. In team mode that is the member who
                # placed the team's bid, so this compares against the seat
                # holder and not against the entrant that pays for it.
                block["you_are_seated"] = (
                    hs.seat_holder is not None and hs.seat_holder == player.name
                )
                # A teammate of the seat holder is neither seated nor a
                # spectator: they share the purse the chair already staked, so
                # they may not bet (#804). The phone needs to be told, or it
                # offers a slider the server refuses.
                block["you_are_seat_team"] = (
                    not block["you_are_seated"] and hs.is_on_seat_team(player.name)
                )
                if block.get("question") is not None:
                    # Named apart from the ``question`` above on purpose: that
                    # one is a Question, this one is the wire dict, and mypy
                    # rightly refuses to let one name mean both.
                    hs_question = dict(block["question"])
                    if block["you_are_seated"]:
                        hs_question["answers"] = hs.shuffled_answers()
                    else:
                        hs_question.pop("answers", None)
                    block["question"] = hs_question
            out["hot_seat"] = block

        if phase == GamePhase.ANSWER_REVEAL.value:
            summary = self.build_round_summary(game_state)
            if summary is not None:
                # Drop the player-agnostic nested copy and merge the flat
                # round_summary fields the reveal view reads (player-reveal.js).
                out.pop("round_summary", None)
                merged = dict(summary)
                # Preserve the snapshot envelope keys the flat summary lacks.
                merged.pop("type", None)
                out.update(merged)

        return out

    def build_round_summary(
        self,
        game_state: QuizifyGameState,
    ) -> dict[str, Any] | None:
        """Build the full round-summary broadcast payload.

        Mirrors ``_broadcast_round_summary`` exactly: resolve the correct
        answer's shuffled + original indices, assemble the per-player
        ``all_answers`` table, build the players list, and serialize the
        summary. Returns ``None`` when there is no round summary yet (the
        handler then no-ops), matching the previous early return.
        """
        summary = game_state.get_round_summary()
        if not summary:
            return None

        # Memoize the built dict per round (#414). This method is
        # recipient-independent — it is called once for the live broadcast and
        # again for every join/reconnect/get_state projection during
        # ANSWER_REVEAL — so recomputing it (O(P) each, O(P²) under a reconnect
        # storm) is wasted work. Keyed on (game_id, round); invalidated by
        # ``start_next_question``/``reset_to_lobby``. Callers treat the returned
        # dict read-only or ``dict()``-copy it (``project_snapshot_for_player``
        # copies before merging), so sharing the cached instance is safe. The
        # cache accessors are resolved via getattr so lightweight test doubles
        # that don't implement them simply skip memoization.
        get_cached = getattr(game_state, "get_cached_round_summary_msg", None)
        store_cached = getattr(game_state, "store_round_summary_msg", None)
        cache_key = (getattr(game_state, "game_id", None), game_state.round)
        if get_cached is not None:
            cached = get_cached(cache_key)
            if cached is not None:
                return cached

        question = summary.question

        # #736: the picture the next round will show, so clients can warm it
        # during the reveal. Resolved through getattr so the lightweight game
        # doubles several tests use — which implement only what they exercise —
        # keep working; they simply get no hint.
        peek_next_image = getattr(game_state, "peek_next_image_url", None)
        next_image_url = peek_next_image() if peek_next_image is not None else None

        # Estimate rounds (#275) carry no shuffled answers and no correct-tile
        # index — closeness is scored, and the reveal is a number line. Build a
        # minimal summary carrying the ``estimate`` block; the player/TV reveal
        # render the number line from it. MC fields are left at their no-op
        # defaults (no answer grid is shown for an estimate reveal).
        if getattr(question, "is_estimate", False):
            leaderboard = serialize_leaderboard(game_state.get_ranked_participants())
            players_list = serialize_player_list(game_state.get_players())
            last_round_est = game_state.round >= game_state.total_rounds
            est_msg = serialize_round_summary(
                correct_answer_index=-1,
                correct_answer_index_original=-1,
                correct_answer_text=summary.correct_answer.text,
                fun_fact=summary.fun_fact,
                leaderboard=leaderboard,
                round_num=game_state.round,
                total_rounds=game_state.total_rounds,
                all_answers=[],
                question_text=question.question,
                num_answer_options=0,
                players=players_list,
                last_round=last_round_est,
                question_id=question.id,
                question_type=question.type,
                estimate=summary.estimate,
                next_image_url=next_image_url,
            )
            if store_cached is not None:
                store_cached(cache_key, est_msg)
            return est_msg

        # Find the correct answer's shuffled index (canonical) AND its
        # original index in question.answers — dashboard needs the latter
        # because it renders unshuffled answer tiles (per #151 audit).
        correct_shuffled_idx = -1
        correct_original_idx = -1
        for a in summary.question.answers:
            if a.correct:
                correct_original_idx = summary.question.answers.index(a)
                for shuffled_idx, orig_idx in enumerate(game_state.shuffle_map):
                    if orig_idx == correct_original_idx:
                        correct_shuffled_idx = shuffled_idx
                        break
                break

        leaderboard = serialize_leaderboard(game_state.get_ranked_participants())

        # Build all_answers: what each player answered this round.
        # answer_index is the ORIGINAL question index (not shuffled) —
        # with per-player shuffles, a shuffled index is meaningless to
        # any other player. The client uses answer_text for display and
        # correct_answer_text for highlighting their own button.
        #
        # correct_button_index (#308): the index of the correct answer in
        # THIS player's OWN shuffled button order. Per-player shuffles (the
        # #253/#286 projection) mean neither the canonical
        # ``correct_answer_index`` nor the original ``answer_index`` map to a
        # player's button positions — and a player who answered wrong or
        # didn't answer cannot resolve the correct button locally when two
        # answers share the same text (the duplicate-text trap from #308).
        # We compute it server-side: a player's shuffle is
        # ``button_pos -> original_index``, so the correct button is the
        # position where ``shuffle[pos] == correct_original_idx``. ``-1`` when
        # unresolvable (no shuffle / no correct answer found); the reveal
        # client then falls back to its #319 index/text heuristic.
        def _correct_button_index(player_name: str) -> int:
            if correct_original_idx < 0:
                return -1
            shuffle = game_state.get_player_shuffle(player_name)
            if not shuffle:
                return -1
            try:
                return shuffle.index(correct_original_idx)
            except ValueError:
                return -1

        # Team mode (#365): the reveal is about the team, so every member reads
        # the same row — the answer that stood and the points it earned. Only
        # one member is actually scored (a team scores once), so without this
        # the other members' reveal says "no answer given" for a round their
        # team answered. ``correct_button_index`` stays per player: it is about
        # their own button order, not about the answer.
        _registry = getattr(game_state, "team_registry", None)
        team_of = getattr(_registry, "get_by_member", None)

        def _team_row(player_name: str) -> dict[str, Any] | None:
            team = team_of(player_name) if team_of is not None else None
            if team is None:
                return None
            if team.current_answer is None:
                return {
                    "player_name": player_name,
                    "answer_index": None,
                    "answer_text": "—",
                    "correct": False,
                    "correct_button_index": _correct_button_index(player_name),
                    "points_earned": 0,
                    "no_answer": True,
                }
            answers = summary.question.answers
            idx = team.current_answer
            breakdown = team.round_score_breakdown or {}
            return {
                "player_name": player_name,
                "answer_index": idx,
                "answer_text": answers[idx].text if 0 <= idx < len(answers) else "?",
                "correct": answers[idx].correct if 0 <= idx < len(answers) else False,
                "correct_button_index": _correct_button_index(player_name),
                "points_earned": team.round_score,
                "speed_bonus": breakdown.get("speed_bonus", 0),
                "streak_bonus": breakdown.get("streak_bonus", 0),
                "difficulty_multiplier": breakdown.get("difficulty_multiplier", 1.0),
                "double_points": breakdown.get("double_points", False),
                "streak": team.streak,
            }

        all_answers = []
        for player in game_state.get_players():
            row = _team_row(player.name)
            if row is not None:
                all_answers.append(row)
            elif player.submitted and player.current_answer is not None:
                submitted_orig = player.current_answer
                answer_text = (
                    summary.question.answers[submitted_orig].text
                    if 0 <= submitted_orig < len(summary.question.answers)
                    else "?"
                )
                is_correct = (
                    summary.question.answers[submitted_orig].correct
                    if submitted_orig < len(summary.question.answers)
                    else False
                )
                breakdown = player.round_score_breakdown
                all_answers.append({
                    "player_name": player.name,
                    "answer_index": submitted_orig,  # original index, not shuffled
                    "answer_text": answer_text,
                    "correct": is_correct,
                    "correct_button_index": _correct_button_index(player.name),
                    "points_earned": player.round_score,
                    "speed_bonus": breakdown.get("speed_bonus", 0),
                    "streak_bonus": breakdown.get("streak_bonus", 0),
                    "difficulty_multiplier": breakdown.get(
                        "difficulty_multiplier", 1.0
                    ),
                    "double_points": breakdown.get("double_points", False),
                    "streak": player.streak,
                })
            else:
                all_answers.append({
                    "player_name": player.name,
                    "answer_index": None,
                    "answer_text": "—",
                    "correct": False,
                    "correct_button_index": _correct_button_index(player.name),
                    "points_earned": 0,
                    "no_answer": True,
                })

        # Build players list with is_admin so the reveal client can show
        # the Next Round button to admin-as-player.
        players_list = serialize_player_list(game_state.get_players())
        last_round = game_state.round >= game_state.total_rounds

        msg = serialize_round_summary(
            correct_answer_index=correct_shuffled_idx,
            correct_answer_index_original=correct_original_idx,
            correct_answer_text=summary.correct_answer.text,
            fun_fact=summary.fun_fact,
            leaderboard=leaderboard,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            all_answers=all_answers,
            question_text=summary.question.question,
            num_answer_options=len(game_state.shuffled_answers),
            players=players_list,
            last_round=last_round,
            question_id=summary.question.id,
            display_order=game_state.shuffle_map,
            next_image_url=next_image_url,
        )
        if store_cached is not None:
            store_cached(cache_key, msg)
        return msg
