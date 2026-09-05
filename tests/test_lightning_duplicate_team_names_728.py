"""Two teams with the same name are two teams (#728).

Nothing in Quizify makes a team name unique: ``TeamRegistry.create`` takes the
name the phone typed, and its empty-name fallback hands the same suggestion out
again as soon as an earlier team has dissolved. Two rooms full of friends
naming themselves "Sofa" is therefore not an edge case, it is a Tuesday.

The Lightning Round used to key its entrants by that name. Both Sofas mapped
onto one entrant, which cost the round three separate things:

* one team's tap landed on the other's standing answer;
* a single correct answer was paid twice, because ``_entrants`` carried the key
  once per team while ``scores`` — built with ``dict.fromkeys`` — carried it
  once;
* the recap and the standings showed one row where two teams had played.

The fix keys entrants by team id. What the room *sees* must not change: the
phones and the TV still read the team's name, which now travels beside the key
instead of being the key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.quizify.game.lightning import LightningRound
from custom_components.quizify.game.questions import QuestionBank
from custom_components.quizify.game.team import ANSWER_CHANGE_LOCK_SECONDS

#: Two different teams, same name — the whole point of this file.
_TWO_SOFAS = [
    {"team_id": "t1", "name": "Sofa", "members": ["Anna", "Ben"]},
    {"team_id": "t2", "name": "Sofa", "members": ["Cara", "Dan"]},
]


_WWW_JS = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "quizify"
    / "www"
    / "js"
)


@pytest.fixture
def bank() -> QuestionBank:
    root = Path(__file__).resolve().parent.parent / "custom_components" / "quizify"
    return QuestionBank(root / "questions")


def _round(bank: QuestionBank) -> LightningRound:
    lr = LightningRound(
        bank,
        ["Anna", "Ben", "Cara", "Dan"],
        language="en",
        category="picture-round-en",
        teams=_TWO_SOFAS,
    )
    assert lr.start(), "the picture pack must yield lightning questions"
    return lr


def _correct_button(lr: LightningRound, name: str) -> int:
    """The position of the correct answer on THIS player's phone."""
    q = lr.current_question
    assert q is not None
    correct = next(i for i, a in enumerate(q.answers) if a.correct)
    return lr.ensure_shuffle(name).index(correct)


def _wrong_button(lr: LightningRound, name: str) -> int:
    q = lr.current_question
    assert q is not None
    wrong = next(i for i, a in enumerate(q.answers) if not a.correct)
    return lr.ensure_shuffle(name).index(wrong)


# ----------------------------------------------------------------------
# The money
# ----------------------------------------------------------------------


def test_one_correct_answer_pays_one_team_once(bank: QuestionBank) -> None:
    """The reported symptom: 20 points for a 10-point answer."""
    lr = _round(bank)

    assert lr.record_answer("Anna", _correct_button(lr, "Anna"), now=100.0) is True
    lr.advance()

    assert lr.scores["t1"] == lr.points_per_correct
    assert lr.scores["t2"] == 0, "the other Sofa answered nothing"
    assert sum(lr.scores.values()) == lr.points_per_correct


def test_both_sofas_keep_their_own_total(bank: QuestionBank) -> None:
    lr = _round(bank)

    lr.record_answer("Anna", _correct_button(lr, "Anna"), now=100.0)
    lr.record_answer("Cara", _wrong_button(lr, "Cara"), now=100.0)
    lr.advance()

    assert lr.score_for("Anna") == lr.score_for("Ben") == lr.points_per_correct
    assert lr.score_for("Cara") == lr.score_for("Dan") == 0


# ----------------------------------------------------------------------
# The answer
# ----------------------------------------------------------------------


def test_one_sofa_cannot_overwrite_the_other_sofas_answer(bank: QuestionBank) -> None:
    """Cara is not Anna's teammate; her tap is her own team's answer.

    Under the name-keyed version Cara's tap either overwrote Anna's or — taps
    landing in the same second, as they do in a 15s round — was refused by the
    re-decision lock that only teammates should feel.
    """
    lr = _round(bank)

    assert lr.record_answer("Anna", _correct_button(lr, "Anna"), now=100.0) is True
    assert lr.record_answer("Cara", _wrong_button(lr, "Cara"), now=100.0) is False

    anna = lr.standing_answer("Anna")
    cara = lr.standing_answer("Cara")
    assert anna is not None and cara is not None
    assert anna.set_by == "Anna"
    assert cara.set_by == "Cara"
    assert anna.correct is True and cara.correct is False


def test_the_lock_stays_inside_one_team(bank: QuestionBank) -> None:
    """The brake exists so two members cannot flip their team's answer back
    and forth — it has no business reaching across to a different team."""
    lr = _round(bank)

    lr.record_answer("Anna", _wrong_button(lr, "Anna"), now=100.0)
    # A teammate is held back...
    assert lr.record_answer("Ben", _correct_button(lr, "Ben"), now=100.2) is None
    # ...the other Sofa is not.
    assert lr.record_answer("Dan", _correct_button(lr, "Dan"), now=100.2) is True

    later = 100.0 + ANSWER_CHANGE_LOCK_SECONDS
    assert lr.record_answer("Ben", _correct_button(lr, "Ben"), now=later) is True
    lr.advance()

    assert lr.scores["t1"] == lr.points_per_correct
    assert lr.scores["t2"] == lr.points_per_correct


def test_members_do_not_bleed_between_two_teams_of_a_name(
    bank: QuestionBank,
) -> None:
    lr = _round(bank)

    assert sorted(lr.members_of("Anna")) == ["Anna", "Ben"]
    assert sorted(lr.members_of("Cara")) == ["Cara", "Dan"]
    assert lr.entrant_for("Anna") == "t1"
    assert lr.entrant_for("Dan") == "t2"


# ----------------------------------------------------------------------
# What the room sees
# ----------------------------------------------------------------------


def test_the_standings_show_both_sofas_by_name(bank: QuestionBank) -> None:
    """Two rows, both saying "Sofa" — never an id on a screen."""
    lr = _round(bank)
    lr.record_answer("Anna", _correct_button(lr, "Anna"), now=100.0)
    while lr.advance():
        pass

    board = lr.build_recap()["leaderboard"]
    assert [row["name"] for row in board] == ["Sofa", "Sofa"]
    assert {row["entrant_id"] for row in board} == {"t1", "t2"}
    top = next(row for row in board if row["entrant_id"] == "t1")
    assert top["rank"] == 1 and top["score"] == lr.points_per_correct


def test_the_recap_grid_tells_the_two_sofas_apart(bank: QuestionBank) -> None:
    """The per-question grid is keyed by entrant, and ``names`` turns each key
    back into the label the host screen prints on its chips."""
    lr = _round(bank)
    lr.record_answer("Anna", _correct_button(lr, "Anna"), now=100.0)
    lr.record_answer("Cara", _wrong_button(lr, "Cara"), now=100.0)
    while lr.advance():
        pass

    recap = lr.build_recap()
    first = recap["questions"][0]["results"]
    assert first["t1"] == "correct"
    assert first["t2"] == "wrong"
    assert recap["names"] == {"t1": "Sofa", "t2": "Sofa"}
    assert "Sofa" not in first, "the name is a label, not a key"


def test_a_wrong_pick_belongs_to_the_team_that_made_it(bank: QuestionBank) -> None:
    lr = _round(bank)
    lr.record_answer("Cara", _wrong_button(lr, "Cara"), now=100.0)
    lr.record_answer("Anna", _correct_button(lr, "Anna"), now=100.0)
    lr.advance()

    chosen = lr.build_recap()["questions"][0]["chosen"]
    assert "t2" in chosen, "the Sofa that guessed wrong sees its own pick"
    assert "t1" not in chosen, "the Sofa that got it right has nothing to show"


# ----------------------------------------------------------------------
# The clients that read those keys
# ----------------------------------------------------------------------


def test_the_phone_looks_itself_up_by_team_id() -> None:
    """A phone that still looked itself up by name would find the wrong
    Sofa's row — or its own, half the time, which is worse."""
    src = (_WWW_JS / "player-lightning.js").read_text("utf-8")

    assert "mine.team_id" in src
    assert "p.entrant_id || p.name" in src


def test_the_host_screen_prints_the_name_not_the_key() -> None:
    """The recap chips are the one place an entrant key could leak onto a
    screen: their label used to BE the key. It now goes through the map."""
    src = (_WWW_JS / "admin.js").read_text("utf-8")

    assert "recap.names" in src
    assert "names[entrant] || entrant" in src


def test_the_shipped_bundle_carries_the_fix() -> None:
    """The player page loads the bundle, not the modules — without a rebuild
    the phones keep the name-keyed lookup (#728)."""
    bundle = (_WWW_JS / "player.bundle.js").read_text("utf-8")

    assert "mine.team_id" in bundle
