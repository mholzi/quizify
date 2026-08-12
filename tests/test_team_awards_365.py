"""The end-of-game awards go to teams (#365, part 2).

Markus' decision was "the awards stay, but they are awarded to teams" — and
explicitly *not* a mechanical rename: every award needs a reading that makes
sense one rung up. The reading is not written in a second implementation but
in what a team records while it plays, because the awards are computed by the
same function that computes them for players:

* **Top Score** — the team's best single round.
* **Fastest Finger** — the average time of the tap that *stood*, so a team
  that argues to the buzzer is genuinely slower than one that agrees at once.
* **Comeback King** — the team's own second half against its first.
* **Hot Streak** — the team streak, which exists already because the answer is
  a team decision.
* **Most Accurate** — the team's correct rounds out of its rounds.
* **Buzzkill** — the freezes its members spent, summed at award time.
* **Knowledge Expert** — the points the team took on hard questions.
"""

from __future__ import annotations

from custom_components.quizify.game.highlights import compute_superlatives
from custom_components.quizify.game.team import Team


def _award(results, name: str) -> str | None:
    """Which participant won ``name``, if anybody did."""
    return next((s.winner for s in results if s.award == name), None)


def test_awards_name_teams_not_people() -> None:
    sofa = Team(name="Sofa", members=["Anna", "Jan"])
    sofa.round_scores = [30, 28, 26, 24]
    sofa.round_history = ["correct"] * 4
    sofa.answer_times = [2.0, 2.5, 3.0, 2.2]
    sofa.max_streak = 4

    kueche = Team(name="Küche", members=["Mira"])
    kueche.round_scores = [4, 5, 6, 7]
    kueche.round_history = ["correct", "wrong", "correct", "wrong"]
    kueche.answer_times = [12.0, 11.0]

    results = compute_superlatives([sofa, kueche])

    assert results, "four rounds and two teams is enough for awards"
    assert {s.winner for s in results} <= {"Sofa", "Küche"}
    assert _award(results, "Top Score") == "Sofa"


def test_fastest_finger_is_the_tap_that_stood() -> None:
    """The team that agreed quickly beats the one that argued to the buzzer.

    Both are correct throughout; only the time of the standing answer differs.
    """
    quick = Team(name="Quick", members=["A", "B"])
    quick.round_scores = [10, 10, 10]
    quick.round_history = ["correct"] * 3
    quick.answer_times = [3.0, 3.5, 3.0]

    slow = Team(name="Slow", members=["C", "D"])
    slow.round_scores = [9, 9, 9]
    slow.round_history = ["correct"] * 3
    slow.answer_times = [17.0, 18.0, 16.5]

    results = compute_superlatives([quick, slow])

    assert _award(results, "Fastest Finger") == "Quick"


def test_hot_streak_reads_the_team_streak() -> None:
    hot = Team(name="Hot", members=["A"])
    hot.round_scores = [5, 5, 5]
    hot.round_history = ["correct"] * 3
    hot.max_streak = 3

    cold = Team(name="Cold", members=["B"])
    cold.round_scores = [30, 0, 0]
    cold.round_history = ["correct", "wrong", "wrong"]
    cold.max_streak = 1

    results = compute_superlatives([hot, cold])

    # Cold takes Top Score with its one big round, which leaves Hot Streak to
    # the team that actually strung answers together.
    assert _award(results, "Hot Streak") == "Hot"


def test_buzzkill_names_the_team_whose_members_spent_the_freezes() -> None:
    """The schadenfreude stays in the room without singling out the tapper."""
    frosty = Team(name="Frosty", members=["A", "B"])
    frosty.round_scores = [5, 5, 5]
    frosty.round_history = ["correct", "wrong", "wrong"]
    frosty.freezes_used = 2  # one each, summed at award time

    mild = Team(name="Mild", members=["C"])
    mild.round_scores = [26, 5, 5]
    mild.round_history = ["correct", "wrong", "wrong"]

    results = compute_superlatives([frosty, mild])

    assert _award(results, "Buzzkill") == "Frosty"


def test_a_single_team_gets_no_awards() -> None:
    """Same rule as a solo game: there is nothing to be best at."""
    solo = Team(name="Solo", members=["A"])
    solo.round_scores = [30, 30, 30]
    solo.round_history = ["correct"] * 3

    assert compute_superlatives([solo]) == []
