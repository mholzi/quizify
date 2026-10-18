"""A shared first place is a win for every tied player (#1085).

The finale podium crowns everyone on step 1 (#1078), but the win tallies used
``max(players, key=players.get)`` and credited the first of them only. The
all-time record and the evening ("Tonight") tally now share ``winners_of``.
"""

from __future__ import annotations

from custom_components.quizify.analytics import (
    QuizifyAnalytics,
    game_winners,
    winners_of,
)

HOUR = 3600


def _analytics() -> QuizifyAnalytics:
    inst = QuizifyAnalytics.__new__(QuizifyAnalytics)
    inst._data = {"version": 2, "games": [], "all_time_players": {}}
    return inst


async def _play(
    inst: QuizifyAnalytics, game_id: str, scores: dict[str, int]
) -> QuizifyAnalytics:
    async def _noop(_record):
        return None

    inst.add_game = _noop  # type: ignore[method-assign]
    await inst.record_game(game_id, "mixed", "normal", 5, scores, 60)
    return inst


def test_winners_of_rules() -> None:
    assert winners_of({}) == []
    assert winners_of({"A": 5, "B": 3}) == ["A"]
    assert winners_of({"A": 225, "B": 225}) == ["A", "B"]
    assert winners_of({"A": 7, "B": 7, "C": 7, "D": 1}) == ["A", "B", "C"]
    # All-zero: everyone shares first, as the podium shows.
    assert winners_of({"A": 0, "B": 0}) == ["A", "B"]


async def test_tie_of_two_credits_both_all_time() -> None:
    inst = await _play(_analytics(), "g1", {"Probe": 225, "Anna": 225})
    rec = inst._data["all_time_players"]
    assert rec["Probe"]["wins"] == 1
    assert rec["Anna"]["wins"] == 1


async def test_tie_of_three_credits_all_three() -> None:
    inst = await _play(_analytics(), "g1", {"A": 9, "B": 9, "C": 9, "D": 2})
    rec = inst._data["all_time_players"]
    assert [rec[n]["wins"] for n in "ABCD"] == [1, 1, 1, 0]


async def test_no_tie_credits_only_the_leader() -> None:
    inst = await _play(_analytics(), "g1", {"A": 9, "B": 4})
    rec = inst._data["all_time_players"]
    assert (rec["A"]["wins"], rec["B"]["wins"]) == (1, 0)


async def test_all_zero_game_shares_the_win() -> None:
    inst = await _play(_analytics(), "g1", {"A": 0, "B": 0})
    rec = inst._data["all_time_players"]
    assert (rec["A"]["wins"], rec["B"]["wins"]) == (1, 1)


def test_tonight_tally_credits_both_tied_players() -> None:
    inst = _analytics()
    inst._data["games"] = [
        {
            "ended_at": 10_000 + i * HOUR,
            "player_scores": scores,
            "winner": next(iter(scores)),
        }
        for i, scores in enumerate(
            [{"Probe": 5, "Anna": 5}, {"Anna": 8, "Probe": 2}]
        )
    ]
    tally = inst.get_evening_tally(now=10_000 + 2 * HOUR)
    assert tally is not None
    wins = {x["name"]: x["wins"] for x in tally["leaders"]}
    assert wins == {"Anna": 2, "Probe": 1}


def test_older_record_with_only_a_winner_name_still_counts() -> None:
    assert game_winners({"winner": "Anna"}) == ["Anna"]
    assert game_winners({"winner": ""}) == []
    # A pre-#1085 record with scores re-derives the tie.
    assert game_winners({"winner": "A", "player_scores": {"A": 3, "B": 3}}) == [
        "A",
        "B",
    ]
    assert game_winners({"winners": ["B"], "winner": "A"}) == ["B"]


def test_migration_rebuild_credits_ties() -> None:
    inst = _analytics()
    data = inst._migrate(
        {
            "games": [
                {
                    "ended_at": 1,
                    "winner": "A",
                    "player_scores": {"A": 4, "B": 4},
                }
            ]
        }
    )
    rec = data["all_time_players"]
    assert (rec["A"]["wins"], rec["B"]["wins"]) == (1, 1)
