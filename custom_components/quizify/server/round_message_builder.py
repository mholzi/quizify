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
        return serialize_question_for_player(
            question=question,
            shuffled_answers=shuffled_answers,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            timer_duration=game_state.round_duration,
            is_final_round=is_final,
            player_score=player.score,
        )

    def build_admin_question(
        self,
        game_state: QuizifyGameState,
        *,
        question: Question,
    ) -> dict[str, Any]:
        """Build the admin/dashboard question payload (with correct answer)."""
        return serialize_question_for_admin(
            question=question,
            round_num=game_state.round,
            total_rounds=game_state.total_rounds,
            timer_duration=game_state.round_duration,
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
        """
        leaderboard = serialize_leaderboard(players)
        payload: dict[str, Any] = {
            "type": "game_state",
            "phase": game_state.phase.value,
            "round": game_state.round,
            "total_rounds": game_state.total_rounds,
            "player_count": len(players),
            "players": leaderboard,
            "leaderboard": leaderboard,
        }
        # During a lightning round (#42) a leaderboard-refresh ``game_state``
        # must carry the lightning sub-state, or the client's LIGHTNING case
        # (player-core.js ~437) falls through to an empty ``lightning-view`` —
        # i.e. a blank player screen (#221). Mirror the canonical shape built
        # by ``QuizifyGameState.get_state_snapshot()`` so reconnect snapshots
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
        ``QuizifyGameState.get_state_snapshot()`` so the player client reads
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

        ``QuizifyGameState.get_state_snapshot()`` is player-agnostic — it
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

        leaderboard = serialize_leaderboard(game_state.get_players())

        # Build all_answers: what each player answered this round.
        # answer_index is the ORIGINAL question index (not shuffled) —
        # with per-player shuffles, a shuffled index is meaningless to
        # any other player. The client uses answer_text for display and
        # correct_answer_text for highlighting their own button.
        all_answers = []
        for player in game_state.get_players():
            if player.submitted and player.current_answer is not None:
                submitted_orig = player.current_answer
                answer_text = (
                    summary.question.answers[submitted_orig].text
                    if 0 <= submitted_orig < len(summary.question.answers)
                    else "?"
                )
                is_correct = summary.question.answers[submitted_orig].correct if submitted_orig < len(summary.question.answers) else False
                breakdown = player.round_score_breakdown
                all_answers.append({
                    "player_name": player.name,
                    "answer_index": submitted_orig,  # original index, not shuffled
                    "answer_text": answer_text,
                    "correct": is_correct,
                    "points_earned": player.round_score,
                    "speed_bonus": breakdown.get("speed_bonus", 0),
                    "streak_bonus": breakdown.get("streak_bonus", 0),
                    "difficulty_multiplier": breakdown.get("difficulty_multiplier", 1.0),
                    "double_points": breakdown.get("double_points", False),
                    "streak": player.streak,
                })
            else:
                all_answers.append({
                    "player_name": player.name,
                    "answer_index": None,
                    "answer_text": "—",
                    "correct": False,
                    "points_earned": 0,
                    "no_answer": True,
                })

        # Build players list with is_admin so the reveal client can show
        # the Next Round button to admin-as-player.
        players_list = serialize_player_list(game_state.get_players())
        last_round = game_state.round >= game_state.total_rounds

        return serialize_round_summary(
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
        )
