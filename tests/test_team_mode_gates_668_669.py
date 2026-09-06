"""The wager window and the Hot Seat, once gated off in team mode (#668/#669).

Both features stake a percentage of a score. In team mode ``player.score`` is a
by-product rather than a score: the carrier of a given round — whoever's tap
stands at settle — receives the team's points personally, and every other
member stays at zero. The team's real standing lives on the team.

That mismatch produced two failures from one cause:

* **#668** — the final-round betting window opened for everyone, showed each
  member "your bank" against their meaningless personal number, and then
  settled only the carrier's bet against the carrier's shadow score.
* **#669** — the auction let bids be a percentage of the same shadow value.
  Zero-stake bids are rejected, so most of the room could not bid; and the
  settlement wrote to ``player.score`` while the leaderboard, podium and awards
  all read teams, so the whole detour's points landed nowhere visible.

Both were gated off "until the mechanics themselves learn about teams, the way
Lightning did in #552". **#804 is that work**, so the gates are gone: the bet
and the bid now belong to the entrant — a team, or a player who joined none —
and settle onto the row the television shows.

This file keeps the *shape* of the old one on purpose. The two properties
still worth pinning are the ones the gate made easy to lose: that solo play is
untouched, and that ``team_mode`` is derived from the room rather than latched
at kickoff. What has flipped is the third: a team game now gets both features
instead of neither. The mechanics themselves live in
``test_team_auction_and_wager_804.py``.
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
    """The mode the feature was built for must survive the change."""
    solo.start_next_question()
    solo.round = solo.total_rounds
    question = solo.get_current_question()
    assert solo._needs_wager_window(question) is True


def test_team_final_round_now_opens_the_wager_window(
    teamed: QuizifyGameState,
) -> None:
    """#804: the bet is the team's, so there is a bet to open a window for."""
    teamed.start_next_question()
    teamed.round = teamed.total_rounds
    question = teamed.get_current_question()
    assert teamed._needs_wager_window(question) is True


def test_it_is_still_only_the_final_round(teamed: QuizifyGameState) -> None:
    """Removing the team gate must not open a window on every round."""
    teamed.start_next_question()
    question = teamed.get_current_question()
    for r in range(1, teamed.total_rounds):
        teamed.round = r
        assert teamed._needs_wager_window(question) is False


def test_the_host_toggle_still_closes_the_window_in_team_mode(
    tmp_path: Path,
) -> None:
    """#742 is a host's choice, not a mechanic — it outranks team mode."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Jan", "Mira"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.start_game(category="picture-round-en", difficulty="easy",
                  num_rounds=3, language="en", wager_enabled=False)
    st.start_next_question()
    st.round = st.total_rounds
    assert st._needs_wager_window(st.get_current_question()) is False


# ---------------------------------------------------------------------------
# #669 — the auction
# ---------------------------------------------------------------------------


def test_solo_game_still_arms_the_auction(solo: QuizifyGameState) -> None:
    solo._hot_seat_target_round = solo.round + 1
    solo.phase = GamePhase.ANSWER_REVEAL
    assert solo.should_trigger_hot_seat() is True


def test_team_game_now_arms_the_auction(teamed: QuizifyGameState) -> None:
    """#804: teams bid as entrants, so there is something to auction to."""
    teamed._hot_seat_target_round = teamed.round + 1
    teamed.phase = GamePhase.ANSWER_REVEAL
    assert teamed.should_trigger_hot_seat() is True


def test_the_trigger_still_follows_the_room(teamed: QuizifyGameState) -> None:
    """``team_mode`` is derived, so a game that loses its teams plays on.

    Pinned since #669: the behaviour must follow the room rather than a flag
    set once at kickoff. It matters more now, not less — the auction has to
    work on both sides of that line.
    """
    team_id = teamed.get_team_of("Anna")["team_id"]
    teamed.leave_team("Anna")
    teamed.leave_team("Jan")
    assert teamed.team_mode is False, team_id
    teamed._hot_seat_target_round = teamed.round + 1
    teamed.phase = GamePhase.ANSWER_REVEAL
    assert teamed.should_trigger_hot_seat() is True


def test_the_image_hint_no_longer_promises_a_question_the_detour_takes(
    teamed: QuizifyGameState,
) -> None:
    """``peek_next_image_url`` mirrored the gate and has to mirror its removal.

    The hint exists to warm the picture the *next* round will show. A pending
    detour draws from its own pool, so the queue head is not next — and since
    #804 the detour is pending in team mode too.
    """
    teamed._hot_seat_target_round = teamed.round + 1
    teamed._hot_seat_fired = False
    assert teamed.peek_next_image_url() is None
