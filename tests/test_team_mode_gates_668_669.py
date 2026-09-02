"""Team mode keeps its hands off the wager window and the Hot Seat (#668, #669).

Both features stake a percentage of ``player.score``. In team mode that number
is a by-product rather than a score: the carrier of a given round — whoever's
tap stands at settle — receives the team's points personally, and every other
member stays at zero. The team's real standing lives on the team.

That mismatch produced two different failures from one cause:

* **#668** — the final-round betting window opened for everyone, showed each
  member "your bank" against their meaningless personal number, and then
  settled only the carrier's bet against the carrier's shadow score. A team of
  two could have one member wager 80%% with no effect at all, while the other's
  50%% of a hidden 40 decided the finale.
* **#669** — the auction let bids be a percentage of the same shadow value.
  Zero-stake bids are rejected, so most of the room could not bid; and the
  settlement wrote to ``player.score`` while the leaderboard, podium and awards
  all read teams, so the whole detour's points landed nowhere visible.

Both are gated off until the mechanics themselves learn about teams, the way
Lightning did in #552. These tests pin the gate *and* pin that solo play is
untouched — a gate that quietly disables a feature for everyone is the more
expensive mistake.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.state import QuizifyGameState


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _game(tmp_path: Path, *, teams: bool) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Jan", "Mira"):
        st.add_player(name, _ws())
    if teams:
        st.create_team("Sofa", "Anna")
        st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.start_game(category="picture-round-en", difficulty="easy",
                  num_rounds=3, language="en")
    return st


@pytest.fixture
def solo(tmp_path: Path) -> QuizifyGameState:
    return _game(tmp_path, teams=False)


@pytest.fixture
def teamed(tmp_path: Path) -> QuizifyGameState:
    return _game(tmp_path, teams=True)


# ---------------------------------------------------------------------------
# #668 — the final wager
# ---------------------------------------------------------------------------


def test_solo_final_round_still_opens_the_wager_window(
    solo: QuizifyGameState,
) -> None:
    """The gate must not touch the mode the feature was built for."""
    solo.start_next_question()
    solo.round = solo.total_rounds
    question = solo.get_current_question()
    assert solo._needs_wager_window(question) is True


def test_team_final_round_opens_no_wager_window(
    teamed: QuizifyGameState,
) -> None:
    teamed.start_next_question()
    teamed.round = teamed.total_rounds
    question = teamed.get_current_question()
    assert teamed._needs_wager_window(question) is False


def test_the_gate_is_team_mode_and_not_the_round(
    teamed: QuizifyGameState,
) -> None:
    """Not an off-by-one: no round of a team game opens a window."""
    teamed.start_next_question()
    question = teamed.get_current_question()
    for r in range(1, teamed.total_rounds + 1):
        teamed.round = r
        assert teamed._needs_wager_window(question) is False


# ---------------------------------------------------------------------------
# #669 — the auction
# ---------------------------------------------------------------------------


def test_solo_game_still_arms_the_auction(solo: QuizifyGameState) -> None:
    solo._hot_seat_target_round = solo.round + 1
    solo.phase = GamePhase.ANSWER_REVEAL
    assert solo.should_trigger_hot_seat() is True


def test_team_game_never_arms_the_auction(teamed: QuizifyGameState) -> None:
    teamed._hot_seat_target_round = teamed.round + 1
    teamed.phase = GamePhase.ANSWER_REVEAL
    assert teamed.should_trigger_hot_seat() is False


def test_the_auction_gate_survives_a_team_being_emptied(
    teamed: QuizifyGameState,
) -> None:
    """``team_mode`` is derived, so a game that loses its teams plays on.

    This is the behaviour the property documents, and it means the gate
    follows the room rather than a flag set once at kickoff.
    """
    team_id = teamed.get_team_of("Anna")["team_id"]
    teamed.leave_team("Anna")
    teamed.leave_team("Jan")
    assert teamed.team_mode is False, team_id
    teamed._hot_seat_target_round = teamed.round + 1
    teamed.phase = GamePhase.ANSWER_REVEAL
    assert teamed.should_trigger_hot_seat() is True
