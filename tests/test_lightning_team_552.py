"""A team answers lightning together (#552).

Markus' decision (2026-08-12): in the Lightning Round a team answers exactly as
it does in a normal round — one answer, one score, any member may change it
until the clock stops. The alternative (everyone taps for themselves, the team
collects) would have handed a team of four four bites at every question, which
is precisely the arithmetic the normal rounds deliberately avoid.

Two consequences follow from that decision rather than from taste, and both are
pinned below:

* the question runs its clock, because ending it as soon as everyone has
  answered would close it on the first member's tap;
* the recap and the standings name the team, since that is who scored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.quizify.game.lightning import LightningRound
from custom_components.quizify.game.questions import QuestionBank
from custom_components.quizify.game.team import ANSWER_CHANGE_LOCK_SECONDS

_SOFA = [{"team_id": "t1", "name": "Sofa", "members": ["Anna", "Jan"]}]


@pytest.fixture
def bank() -> QuestionBank:
    root = Path(__file__).resolve().parent.parent / "custom_components" / "quizify"
    return QuestionBank(root / "questions")


def _round(bank: QuestionBank, *, teams=None, players=("Anna", "Jan", "Mira")):
    lr = LightningRound(
        bank, list(players), language="en", category="picture-round-en",
        teams=teams,
    )
    assert lr.start(), "the picture pack must yield lightning questions"
    return lr


def _correct_button(lr: LightningRound, name: str) -> int:
    """The position of the correct answer on THIS player's phone."""
    q = lr.current_question
    correct = next(i for i, a in enumerate(q.answers) if a.correct)
    return lr.ensure_shuffle(name).index(correct)


def _wrong_button(lr: LightningRound, name: str) -> int:
    q = lr.current_question
    wrong = next(i for i, a in enumerate(q.answers) if not a.correct)
    return lr.ensure_shuffle(name).index(wrong)


# ----------------------------------------------------------------------
# One answer, one score
# ----------------------------------------------------------------------


def test_a_team_scores_once_not_once_per_member(bank: QuestionBank) -> None:
    lr = _round(bank, teams=_SOFA)

    assert lr.record_answer("Anna", _correct_button(lr, "Anna"), now=100.0) is True
    lr.advance()

    # Keyed by team id since #728 — the name is what the room reads, not
    # what the bookkeeping is indexed by.
    assert lr.scores["t1"] == lr.points_per_correct
    assert lr.display_name("t1") == "Sofa"
    assert "Anna" not in lr.scores and "Jan" not in lr.scores, (
        "members are represented by their team, not next to it"
    )


def test_both_members_read_the_teams_score(bank: QuestionBank) -> None:
    """The ack the WS layer sends must not tell one member a different total."""
    lr = _round(bank, teams=_SOFA)
    lr.record_answer("Jan", _correct_button(lr, "Jan"), now=100.0)
    lr.advance()

    assert lr.score_for("Anna") == lr.score_for("Jan") == lr.points_per_correct
    assert lr.score_for("Mira") == 0, "the solo player keeps her own total"


def test_a_teammate_can_change_the_answer(bank: QuestionBank) -> None:
    lr = _round(bank, teams=_SOFA)
    lr.record_answer("Anna", _wrong_button(lr, "Anna"), now=100.0)

    later = 100.0 + ANSWER_CHANGE_LOCK_SECONDS
    assert lr.record_answer("Jan", _correct_button(lr, "Jan"), now=later) is True
    lr.advance()

    assert lr.scores["t1"] == lr.points_per_correct, "the last tap counts"


def test_the_lock_refuses_an_instant_second_change(bank: QuestionBank) -> None:
    lr = _round(bank, teams=_SOFA)
    wrong = _wrong_button(lr, "Anna")
    lr.record_answer("Anna", wrong, now=100.0)

    assert lr.record_answer("Jan", _correct_button(lr, "Jan"), now=100.5) is None
    lr.advance()

    assert lr.scores["t1"] == 0, "the locked-out change must not apply"


def test_a_solo_players_answer_is_still_final(bank: QuestionBank) -> None:
    """The base game is untouched — only team mode gained re-decisions."""
    lr = _round(bank, teams=_SOFA)
    lr.record_answer("Mira", _wrong_button(lr, "Mira"), now=100.0)

    assert lr.record_answer("Mira", _correct_button(lr, "Mira"), now=200.0) is None
    lr.advance()

    assert lr.scores["Mira"] == 0


# ----------------------------------------------------------------------
# The clock
# ----------------------------------------------------------------------


def test_the_question_runs_its_clock_in_team_mode(bank: QuestionBank) -> None:
    """Otherwise the first member's tap ends the question, and "answering
    together" is theatre — the same trap the normal round fell into."""
    lr = _round(bank, teams=_SOFA)
    lr.record_answer("Anna", 0, now=100.0)
    lr.record_answer("Mira", 0, now=100.0)

    assert lr.all_connected_answered(["Anna", "Jan", "Mira"]) is False


def test_without_teams_the_question_still_ends_early(bank: QuestionBank) -> None:
    """Lightning keeps its fast cadence in an ordinary game."""
    lr = _round(bank, players=("Anna", "Mira"))
    lr.record_answer("Anna", 0, now=100.0)
    lr.record_answer("Mira", 0, now=100.0)

    assert lr.all_connected_answered(["Anna", "Mira"]) is True


# ----------------------------------------------------------------------
# What the room sees
# ----------------------------------------------------------------------


def test_the_recap_and_standings_name_the_team(bank: QuestionBank) -> None:
    lr = _round(bank, teams=_SOFA)
    lr.record_answer("Anna", _correct_button(lr, "Anna"), now=100.0)
    lr.record_answer("Mira", _wrong_button(lr, "Mira"), now=100.0)
    while lr.advance():
        pass

    recap = lr.build_recap()
    names = {row["name"] for row in recap["leaderboard"]}
    assert names == {"Sofa", "Mira"}, "the standings print the team's NAME"
    first = recap["questions"][0]["results"]
    # ...while the grid is keyed by entrant (#728), with the map that turns
    # a key back into the name the host screen chips out.
    assert first["t1"] == "correct"
    assert recap["names"]["t1"] == "Sofa"
    assert first["Mira"] == "wrong"
    assert "Anna" not in first and "Jan" not in first


def test_the_standing_answer_is_readable_per_member(bank: QuestionBank) -> None:
    """The broadcast needs both halves: what stands, and who is in the team."""
    lr = _round(bank, teams=_SOFA)
    lr.record_answer("Jan", _correct_button(lr, "Jan"), now=100.0)

    standing = lr.standing_answer("Anna")
    assert standing is not None
    assert standing.set_by == "Jan"
    assert sorted(lr.members_of("Anna")) == ["Anna", "Jan"]
    assert lr.members_of("Mira") == ["Mira"], "a solo player is her own team"
    # Anna's phone shuffles for itself, so the index she must highlight is
    # resolved against her own order — not against Jan's tap.
    assert lr.ensure_shuffle("Anna").index(standing.answer_index) >= 0


def test_a_latecomer_plays_for_themselves(bank: QuestionBank) -> None:
    """Teams are frozen in the lobby, so a mid-round arrival is their own."""
    lr = _round(bank, teams=_SOFA)
    lr.add_player("Late")

    assert lr.entrant_for("Late") == "Late"
    lr.record_answer("Late", _correct_button(lr, "Late"), now=100.0)
    lr.advance()

    assert lr.scores["Late"] == lr.points_per_correct
