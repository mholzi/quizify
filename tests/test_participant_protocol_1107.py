"""Teams and players share one typed ``Participant`` shape (#1107).

In team mode the ranking is teams plus the solo players who joined none
(#365). That mixed list used to flow through ``list[Any]`` into functions
annotated ``list[PlayerSession]``, with ``getattr``/``hasattr`` defaults
papering over the attributes only one class has. The refactor gives both
classes the two identity attributes explicitly (``team_id``, ``members``),
types the path against ``game.participant.Participant`` and narrows with
``isinstance`` where an attribute is player-only.

Behaviour must not move: these tests pin the serialized output of a
Team + solo mix field for field, so the refactor is checked against the exact
wire shape the clients already read.
"""

from __future__ import annotations

from custom_components.quizify.game.player import PlayerSession
from custom_components.quizify.game.scoring import calculate_podium
from custom_components.quizify.game.team import Team
from custom_components.quizify.server.serializers import (
    build_share_payload,
    serialize_answer_progress,
    serialize_leaderboard,
)


def _team() -> Team:
    team = Team(name="Sofa", team_id="t-sofa", color="coral")
    team.add_member("Anna")
    team.add_member("Bert")
    team.score = 120
    team.streak = 0
    team.max_streak = 4
    team.round_score = 30
    team.round_history = ["correct", "wrong", "correct"]
    team.round_score_breakdown = {
        "speed_bonus": 3,
        "streak_bonus": 0,
        "difficulty_multiplier": 1.5,
        "difficulty_bonus": 5,
        "double_points": False,
        "double_bonus": 0,
    }
    team.powerups_used = 1
    team.current_answer = 2
    return team


def _solo() -> PlayerSession:
    solo = PlayerSession(name="Cleo", color="#4ECDC4")
    solo.score = 120  # tied with the team: shares rank 1 (#308)
    solo.streak = 0
    solo.max_streak = 2
    solo.round_score = 0
    solo.record_round_result("wrong", scored_hit=True)  # estimate hit (#1084)
    solo.record_round_result("timeout")
    solo.powerups_used = 0
    solo.submitted = False
    return solo


def test_player_answers_to_the_participant_identity_attributes() -> None:
    """A player has ``team_id`` and ``members`` of its own, no probing needed."""
    solo = _solo()
    assert solo.team_id is None
    assert solo.members == ["Cleo"]
    # Derived, not stored: a mutation of the returned list changes nothing.
    solo.members.append("ghost")
    assert solo.members == ["Cleo"]


def test_team_mode_leaderboard_for_team_plus_solo_is_unchanged() -> None:
    rows = serialize_leaderboard([_solo(), _team()])
    assert rows == [
        {
            "rank": 1,
            "entrant_id": "Cleo",
            "name": "Cleo",
            "members": ["Cleo"],
            "score": 120,
            "streak": 0,
            "streak_active": False,
            "round_score": 0,
            "correct": False,
            "missed_round": True,
            "speed_bonus": 0,
            "streak_bonus": 0,
            "difficulty_multiplier": 1.0,
            "difficulty_bonus": 0,
            "double_points": False,
            "double_bonus": 0,
            "color": "#4ECDC4",
            "is_admin": False,
            "submitted": False,
            "best_streak": 2,
            "rounds_played": 2,
            "powerups_used": 0,
        },
        {
            "rank": 1,
            "entrant_id": "t-sofa",
            "name": "Sofa",
            "members": ["Anna", "Bert"],
            "score": 120,
            "streak": 0,
            "streak_active": False,
            "round_score": 30,
            "correct": True,
            "missed_round": False,
            "speed_bonus": 3,
            "streak_bonus": 0,
            "difficulty_multiplier": 1.5,
            "difficulty_bonus": 5,
            "double_points": False,
            "double_bonus": 0,
            "color": "coral",
            "is_admin": False,
            "submitted": False,
            "best_streak": 4,
            "rounds_played": 3,
            "powerups_used": 1,
        },
    ]


def test_answer_progress_for_team_plus_solo_is_unchanged() -> None:
    team = _team()
    solo = _solo()
    anna = PlayerSession(name="Anna", connected=False)
    bert = PlayerSession(name="Bert", connected=True)
    progress = serialize_answer_progress([anna, bert, solo], [team, solo])
    assert progress["players"] == [
        # A team has answered once it has a standing answer, and is present
        # while any member is (#835).
        {"entrant_id": "t-sofa", "name": "Sofa", "submitted": True, "connected": True},
        {"entrant_id": "Cleo", "name": "Cleo", "submitted": False, "connected": True},
    ]
    assert progress["submitted"] == 1


def test_share_card_reads_hits_only_from_players() -> None:
    """``round_hits`` is player-only; a team's strip is its history verbatim."""
    payload = build_share_payload([_team(), _solo()])
    by_name = {e["name"]: e for e in payload["players"]}
    assert by_name["Sofa"]["results"] == ["correct", "wrong", "correct"]
    assert by_name["Sofa"]["correct"] == 2
    # The estimate hit counts as correct on the card (#1084).
    assert by_name["Cleo"]["results"] == ["correct", "timeout"]
    assert by_name["Cleo"]["correct"] == 1


def test_podium_keeps_the_participant_type_it_was_given() -> None:
    team, solo = _team(), _solo()
    team.score = 200
    assert calculate_podium([solo, team]) == [team, solo]
