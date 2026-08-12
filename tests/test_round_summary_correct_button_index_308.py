"""Regression test for the per-player correct button index (#308).

Background: answers are shuffled PER PLAYER (the #253/#286 projection), so a
player who answered WRONG or didn't answer cannot tell which button is the
real correct one when two answers share the same text — the client has no
per-player mapping from the canonical/original correct index to its own
button positions.

The server now ships ``correct_button_index`` in each player's
``all_answers`` entry of the round-summary payload: the index of the correct
answer in THAT player's own shuffled button order. The reveal client prefers
it (falling back to its local #319 heuristic when absent).

This test pins that the field:
  * points at the button position holding the correct answer in each
    player's own shuffle, AND
  * differs across two players whose shuffles place the correct answer at
    different button positions, AND
  * is correct even when the question has two identical correct-text answers
    (the duplicate-text trap) for a wrong / non-answering player.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.player import PlayerSession  # noqa: E402
from custom_components.quizify.game.questions import Answer, Question  # noqa: E402
from custom_components.quizify.game.state import RoundSummary  # noqa: E402
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)


def _dup_text_question() -> Question:
    """A question whose correct answer text ("Paris") is duplicated by a
    non-correct decoy at original index 1 — the duplicate-text trap.

    Only original index 0 is flagged ``correct``; index 1 is a wrong answer
    that merely shares the text, which is exactly the case the text-match
    highlight got wrong before #308.
    """
    return Question(
        id="dup1",
        question="Capital of France?",
        answers=[
            Answer(text="Paris", correct=True),   # orig idx 0 — the real one
            Answer(text="Paris", correct=False),  # orig idx 1 — decoy, same text
            Answer(text="Berlin", correct=False),
            Answer(text="Rome", correct=False),
        ],
        difficulty="easy",
        fun_fact="Paris has been the capital since 508 AD.",
        category="Geography",
    )


class _FakeGameState:
    """Minimal stand-in exposing only what RoundMessageBuilder touches."""

    def __init__(
        self,
        *,
        question: Question,
        players: list[PlayerSession],
        player_shuffles: dict[str, list[int]],
        shuffle_map: list[int],
        round_summary: RoundSummary,
        round_num: int = 3,
        total_rounds: int = 10,
    ) -> None:
        self._question = question
        self._players = players
        self.round = round_num
        self.total_rounds = total_rounds
        self.round_duration = 20.0
        self._player_shuffles = player_shuffles
        self.shuffle_map = shuffle_map
        self.shuffled_answers = [question.answers[i].text for i in shuffle_map]
        self._round_summary = round_summary

    def get_players(self) -> list[PlayerSession]:
        return list(self._players)

    def get_ranked_participants(self) -> list[PlayerSession]:
        """Mirror of the real state (#365): players unless teams are in play.

        These fakes model an ordinary game, so the ranking is the players —
        but the method has to exist, or the builder silently falls back and a
        real object missing it would go unnoticed.
        """
        return list(self._players)

    def get_player_shuffle(self, player_name: str) -> list[int]:
        return self._player_shuffles.get(player_name) or self.shuffle_map

    def get_round_summary(self) -> RoundSummary | None:
        return self._round_summary


def _summary(q: Question) -> RoundSummary:
    return RoundSummary(question=q, correct_answer=q.answers[0], fun_fact=q.fun_fact)


def _build(players, player_shuffles):
    q = _dup_text_question()
    gs = _FakeGameState(
        question=q,
        players=players,
        # canonical shuffle is irrelevant to per-player correct_button_index
        shuffle_map=[3, 2, 1, 0],
        player_shuffles=player_shuffles,
        round_summary=_summary(q),
    )
    msg = RoundMessageBuilder().build_round_summary(gs)
    assert msg is not None
    return {a["player_name"]: a for a in msg["all_answers"]}


class TestCorrectButtonIndex308:
    def test_wrong_answerer_with_duplicate_text_gets_real_correct_button(self):
        """Wrong answerer: their button order puts the REAL correct answer
        (orig 0) at button position 2 and the same-text decoy (orig 1) at
        position 0. correct_button_index must point at 2 — the real one —
        not at the decoy a text-match would have grabbed first.
        """
        # Alice's shuffle: button_pos -> original_index
        # pos0->1(decoy "Paris"), pos1->3, pos2->0(real "Paris"), pos3->2
        alice = PlayerSession(name="Alice", ws=None, score=10)
        alice.submitted = True
        alice.current_answer = 2  # answered orig idx 2 ("Berlin") — WRONG
        alice.round_score = 0
        alice.round_score_breakdown = {}
        alice.streak = 0

        answers = _build([alice], {"Alice": [1, 3, 0, 2]})
        assert answers["Alice"]["correct"] is False
        # real correct (orig 0) sits at Alice's button position 2
        assert answers["Alice"]["correct_button_index"] == 2

    def test_non_answerer_gets_correct_button_index(self):
        """A player who didn't answer still gets a correct_button_index in
        their own shuffle (the case the client cannot resolve at all)."""
        bob = PlayerSession(name="Bob", ws=None)
        bob.submitted = False
        # Bob's shuffle: pos0->0(real "Paris"), pos1->2, pos2->1(decoy), pos3->3
        answers = _build([bob], {"Bob": [0, 2, 1, 3]})
        assert answers["Bob"]["no_answer"] is True
        # real correct (orig 0) sits at Bob's button position 0
        assert answers["Bob"]["correct_button_index"] == 0

    def test_differs_across_players_with_different_shuffles(self):
        """Two players with different shuffles get different button indices
        for the same correct answer — proving it's per-player, not canonical.
        """
        alice = PlayerSession(name="Alice", ws=None)
        alice.submitted = True
        alice.current_answer = 2  # WRONG
        alice.round_score = 0
        alice.round_score_breakdown = {}
        alice.streak = 0
        bob = PlayerSession(name="Bob", ws=None)
        bob.submitted = False

        answers = _build(
            [alice, bob],
            {
                # real correct orig 0 at Alice pos 2, at Bob pos 0
                "Alice": [1, 3, 0, 2],
                "Bob": [0, 2, 1, 3],
            },
        )
        assert answers["Alice"]["correct_button_index"] == 2
        assert answers["Bob"]["correct_button_index"] == 0
        assert (
            answers["Alice"]["correct_button_index"]
            != answers["Bob"]["correct_button_index"]
        )

    def test_correct_answerer_button_index_matches_their_tap(self):
        """A correct answerer's correct_button_index equals the button they
        tapped (self-consistency with the #319 fallback)."""
        # Carol's shuffle: pos0->2, pos1->0(real "Paris"), pos2->1, pos3->3
        carol = PlayerSession(name="Carol", ws=None)
        carol.submitted = True
        carol.current_answer = 0  # answered orig 0 ("Paris") — CORRECT
        carol.round_score = 50
        carol.round_score_breakdown = {"speed_bonus": 5}
        carol.streak = 1

        answers = _build([carol], {"Carol": [2, 0, 1, 3]})
        assert answers["Carol"]["correct"] is True
        # real correct (orig 0) sits at Carol's button position 1
        assert answers["Carol"]["correct_button_index"] == 1

    def test_minus_one_when_player_has_no_shuffle(self):
        """No per-player shuffle stored → falls back to canonical via
        get_player_shuffle; field still resolves against that order rather
        than crashing. (Here canonical [3,2,1,0] puts orig 0 at pos 3.)"""
        dave = PlayerSession(name="Dave", ws=None)
        dave.submitted = False
        answers = _build([dave], {})  # no shuffle for Dave
        # get_player_shuffle falls back to canonical [3,2,1,0] → orig0 at pos3
        assert answers["Dave"]["correct_button_index"] == 3
