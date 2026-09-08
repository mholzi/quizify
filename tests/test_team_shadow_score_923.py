"""One score ledger, not two (#923).

Team mode used to keep a second, per-person total. ``Team.score`` is what every
screen shows — the leaderboard, the reveal, the podium and the television all
draw ``get_ranked_participants()`` — but ``submit_answer`` also did
``player.score += points`` while settling a team, so ``PlayerSession.score``
accumulated a value that is 0 for every member except whoever carried that
round, and that nothing renders.

Three readers outside the game layer still consumed it, and each of them
disagreed with the podium sitting next to it:

* ``_record_analytics`` — the all-time standings, the evening tally (#612), the
  end-screen head-to-head (#613) and the season standing pushed to each phone
  (#624). Observed on v1.17.0-RC1: team Sofa won with 112 points and the end
  screen underneath read "Tonight: Bert 2 wins · Dora 1 win".
* ``GameState.leader`` — the ``leader`` and ``top_score`` sensors, and the TTS
  game-over line.
* ``quizify_winner_decided`` — a person's name on the HA bus for a finale that
  ``quizify_game_ended`` reported as a team podium. Two events, one game, two
  answers to "who won".

This is the fourth and fifth reader of a defect class the project has paid for
one consumer at a time (#668 wager, #800 reaction bonus, #804 hot seat, #835
live leaderboard). So the last test here is a guard rather than a scenario: it
walks the AST of ``server/``, ``sensor.py``, ``game_events.py``,
``analytics.py`` and ``tts.py`` for ``.score`` reads and fails on any receiver
that is not already on a short, named allowlist. The next reader has to be
added on purpose.

Solo mode is the control throughout: nothing below may change for a room
without teams, and the guest who joined no team is a participant of their own,
so they keep their own total in a team game.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

from custom_components.quizify.game.state import QuizifyGameState
from custom_components.quizify.game_events import (
    EVENT_GAME_ENDED,
    EVENT_WINNER_DECIDED,
    QuizifyEventEmitter,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CC = _REPO_ROOT / "custom_components" / "quizify"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _Runtime:
    """Enough runtime for the game state; queued tasks are run by the test."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path
        self.tasks: list[Any] = []

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        self.tasks.append(coro)
        return None

    def drain(self) -> None:
        """Run whatever ``_record_analytics`` handed us, synchronously."""
        while self.tasks:
            asyncio.run(self.tasks.pop(0))


class _RecordingAnalytics:
    """Captures the one call ``_record_analytics`` makes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record_game(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _FakeBus:
    def __init__(self) -> None:
        self.fired: list[tuple[str, dict]] = []

    def async_fire(self, event_type, data):  # noqa: ANN001
        self.fired.append((event_type, dict(data)))


class _FakeHass:
    def __init__(self) -> None:
        self.bus = _FakeBus()


def _team_game(
    tmp_path: Path,
    runtime: _Runtime | None = None,
    *,
    category: str = "picture-round-en",
) -> QuizifyGameState:
    """Two teams of two plus one guest who joined none — three entrants."""
    st = QuizifyGameState(
        runtime=runtime or _Runtime(tmp_path), entry_id="test"
    )
    for name in ("Anna", "Jan", "Mira", "Tom", "Eva"):
        st.add_player(name)
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.create_team("Küche", "Mira")
    st.join_team(st.get_team_of("Mira")["team_id"], "Tom")
    st.start_game(
        category=category, difficulty="easy", num_rounds=3, language="en"
    )
    st.start_next_question()
    return st


def _solo_game(tmp_path: Path, runtime: _Runtime | None = None) -> QuizifyGameState:
    """The control: the same room with no teams at all."""
    st = QuizifyGameState(
        runtime=runtime or _Runtime(tmp_path), entry_id="test"
    )
    for name in ("Anna", "Jan", "Mira"):
        st.add_player(name)
    st.start_game(
        category="picture-round-en", difficulty="easy", num_rounds=3, language="en"
    )
    st.start_next_question()
    return st


def _team(st: QuizifyGameState, name: str):  # noqa: ANN202
    return next(t for t in st.team_registry.all_teams() if t.name == name)


def _correct_index(st: QuizifyGameState) -> int:
    question = st.get_current_question()
    return next(i for i, a in enumerate(question.answers) if a.correct)


# ---------------------------------------------------------------------------
# 1. The shadow ledger is not written any more
# ---------------------------------------------------------------------------


def test_a_team_member_carries_no_personal_total(tmp_path: Path) -> None:
    """The carrier scores for the team, not beside it."""
    st = _team_game(tmp_path)
    st.submit_answer("Anna", _correct_index(st))
    st.evaluate_round()

    assert _team(st, "Sofa").score > 0, "the team must have scored at all"
    assert st.get_player("Anna").score == 0
    assert st.get_player("Jan").score == 0


def test_a_guest_who_joined_no_team_keeps_their_own_total(tmp_path: Path) -> None:
    """A solo entrant in a team game is a participant, so their score is real.

    This is the line the fix must not cross: ``get_ranked_participants`` gives
    that guest a row of their own next to the teams (Markus, 2026-08-12), so
    their own total *is* the room's truth about them.
    """
    st = _team_game(tmp_path)
    st.submit_answer("Eva", _correct_index(st))
    st.evaluate_round()

    assert st.get_player("Eva").score > 0


def test_solo_mode_still_scores_the_player(tmp_path: Path) -> None:
    """The control: no teams, nothing changes."""
    st = _solo_game(tmp_path)
    st.submit_answer("Anna", _correct_index(st))
    st.evaluate_round()

    assert st.get_player("Anna").score > 0
    assert st.get_player("Jan").score == 0  # never answered


def test_the_estimate_path_keeps_the_same_rule(tmp_path: Path) -> None:
    """The number-line round scores the team, not the member who dragged it.

    ``_evaluate_estimate_round`` has its own ``player.score += points`` — the
    twin of the multiple-choice one, and the twin of this bug.
    """
    st = _team_game(tmp_path, category="estimation-en")
    answer = st.get_current_question().estimate_answer
    st.submit_guess("Anna", answer)
    st.submit_guess("Eva", answer)
    st.evaluate_round()

    assert _team(st, "Sofa").score > 0
    assert st.get_player("Anna").score == 0, "the carrier keeps no shadow total"
    assert st.get_player("Eva").score > 0, "the solo guest is her own entrant"


# ---------------------------------------------------------------------------
# 2. The readers now agree with the podium
# ---------------------------------------------------------------------------


def test_the_leader_is_the_winning_team(tmp_path: Path) -> None:
    st = _team_game(tmp_path)
    _team(st, "Sofa").score = 112
    _team(st, "Küche").score = 40
    st.get_player("Eva").score = 90

    leader = st.leader
    assert leader is not None
    assert leader.name == "Sofa"
    assert leader.score == 112


def test_the_leader_can_still_be_a_solo_guest(tmp_path: Path) -> None:
    """Teams do not automatically outrank the guest who joined none."""
    st = _team_game(tmp_path)
    _team(st, "Sofa").score = 40
    st.get_player("Eva").score = 90

    leader = st.leader
    assert leader is not None
    assert leader.name == "Eva"


def test_the_leader_is_unchanged_in_solo_mode(tmp_path: Path) -> None:
    st = _solo_game(tmp_path)
    st.get_player("Anna").score = 10
    st.get_player("Jan").score = 30

    leader = st.leader
    assert leader is not None
    assert leader.name == "Jan"


def test_an_empty_room_has_no_leader(tmp_path: Path) -> None:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    assert st.leader is None


def test_analytics_records_the_teams_not_their_members(tmp_path: Path) -> None:
    """What lands in analytics.json decides every downstream tally.

    The all-time standings, the evening tally and the head-to-head all read
    ``player_scores`` back out of this record, so a per-person map there is
    what put "Tonight: Bert 2 wins" under a team podium.
    """
    runtime = _Runtime(tmp_path)
    st = _team_game(tmp_path, runtime)
    analytics = _RecordingAnalytics()
    st.set_stats_services(analytics, None)

    _team(st, "Sofa").score = 112
    _team(st, "Küche").score = 40
    st.get_player("Eva").score = 90
    st.end_game()
    runtime.drain()

    assert len(analytics.calls) == 1
    recorded = analytics.calls[0]["players"]
    assert recorded == {"Sofa": 112, "Küche": 40, "Eva": 90}
    assert "Anna" not in recorded and "Jan" not in recorded
    # The details map has to be keyed the same way, or the all-time rollup
    # would credit a best streak to a name that has no score row.
    assert set(analytics.calls[0]["player_details"]) == set(recorded)


def test_analytics_is_unchanged_in_solo_mode(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path)
    st = _solo_game(tmp_path, runtime)
    analytics = _RecordingAnalytics()
    st.set_stats_services(analytics, None)

    st.get_player("Anna").score = 70
    st.get_player("Jan").score = 30
    st.end_game()
    runtime.drain()

    recorded = analytics.calls[0]["players"]
    assert recorded == {"Anna": 70, "Jan": 30, "Mira": 0}


def test_both_house_events_name_the_same_winner(tmp_path: Path) -> None:
    """``quizify_winner_decided`` and ``quizify_game_ended``, one finale.

    A blueprint may react to either. Before this they disagreed: the first
    named a person off the shadow ledger, the second the team podium.
    """
    st = _team_game(tmp_path)
    _team(st, "Sofa").score = 112
    _team(st, "Küche").score = 40
    st.get_player("Eva").score = 90

    hass = _FakeHass()
    emitter = QuizifyEventEmitter(hass=hass, game_state=st)
    emitter.configure(enabled=True)

    st.end_game()
    emitter._on_state_changed()
    emitter.notify_game_ended(st)

    fired = dict(hass.bus.fired)
    assert fired[EVENT_WINNER_DECIDED] == {"winner_name": "Sofa", "score": 112}
    assert fired[EVENT_GAME_ENDED]["leaderboard"][0] == {
        "name": "Sofa",
        "score": 112,
    }


# ---------------------------------------------------------------------------
# 3. The guard: no new reader of a raw ``.score``
# ---------------------------------------------------------------------------

def _key(*parts: str) -> str:
    """The identity of one ``.score`` read: file, enclosing scope, receiver."""
    return "::".join(parts)


_RMB = "server/round_message_builder.py"
_SER = "server/serializers.py"

#: Every ``<receiver>.score`` this code base is allowed to read outside the
#: game layer, as ``file::enclosing scope::receiver``. Each entry is a
#: participant — a row of ``get_ranked_participants()`` — and the note says who
#: hands it over. A new entry means somebody found a further way to read a
#: score; adding it here is the moment to check that what they read is a
#: participant and not a person standing in for one.
_ALLOWED_SCORE_READERS: dict[str, str] = {
    _key(
        "server/broadcasters.py",
        "HotSeatBroadcaster",
        "send_hot_seat_result",
        "participant",
    ): "the entrant a Hot Seat settlement paid (#804)",
    _key(_RMB, "RoundMessageBuilder", "build_wager_window", "participant"): (
        "the bank the final bet is staked against (#804)"
    ),
    _key(_RMB, "RoundMessageBuilder", "build_player_question", "participant"): (
        "the total shown on the phone during a round (#835)"
    ),
    _key(
        _RMB, "RoundMessageBuilder", "project_snapshot_for_player", "participant"
    ): "the same total, restored after a reload (#835)",
    _key(_SER, "serialize_leaderboard", "p"): (
        "a row handed in by get_ranked_participants (#365)"
    ),
    _key(_SER, "serialize_player_list", "p"): (
        "the lobby roster — people, deliberately. The score column reads 0 for "
        "a team member because their points are the team's (#923)"
    ),
    _key(_SER, "build_share_payload", "p"): (
        "the finale's shareable card, built from the standings (#369)"
    ),
    _key(_SER, "serialize_finale", "p"): (
        "the podium, built from calculate_podium(participants) (#365)"
    ),
    _key("sensor.py", "QuizifyLeaderSensor", "extra_state_attributes", "leader"): (
        "GameState.leader, a participant since #923"
    ),
    _key("sensor.py", "QuizifyTopScoreSensor", "native_value", "leader"): (
        "GameState.leader, a participant since #923"
    ),
    _key("game_events.py", "QuizifyEventEmitter", "_on_state_changed", "leader"): (
        "GameState.leader, a participant since #923"
    ),
    _key("game_events.py", "QuizifyEventEmitter", "notify_game_ended", "participant"): (
        "a row of get_standings() (#747)"
    ),
    _key("tts.py", "QuizifyTTSAnnouncer", "_on_state_changed", "leader"): (
        "GameState.leader, a participant since #923"
    ),
    _key("tts.py", "QuizifyTTSAnnouncer", "_announce_standings_fragment", "top[0]"): (
        "the round's standings, participants since #747"
    ),
    _key("tts.py", "QuizifyTTSAnnouncer", "_announce_standings_fragment", "e"): (
        "the same standings, scanned for a tie at the top (#747)"
    ),
}


class _ScoreReadVisitor(ast.NodeVisitor):
    """Collects ``<receiver>.score`` reads, keyed by file and enclosing scope."""

    def __init__(self, rel: str, found: dict[str, list[int]]) -> None:
        self._rel = rel
        self._found = found
        self._scope: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "score":
            key = "::".join([self._rel, *self._scope, ast.unparse(node.value)])
            self._found.setdefault(key, []).append(node.lineno)
        self.generic_visit(node)


def _score_readers() -> dict[str, list[int]]:
    """``file::scope::receiver`` -> line numbers, for the scanned modules."""
    files = sorted(_CC.joinpath("server").glob("*.py"))
    files += [
        _CC / "sensor.py",
        _CC / "game_events.py",
        _CC / "analytics.py",
        # The narrator too: it speaks the game-over winner off ``leader`` and
        # the tie-at-the-top line off the standings, so it is a reader of the
        # same truth even though the issue only listed the four above.
        _CC / "tts.py",
    ]

    found: dict[str, list[int]] = {}
    for path in files:
        rel = path.relative_to(_CC).as_posix()
        _ScoreReadVisitor(rel, found).visit(ast.parse(path.read_text("utf-8")))
    return found


def test_no_unknown_reader_of_a_raw_score() -> None:
    """A new ``.score`` outside the game layer has to be declared here.

    Five consumers of the shadow ledger were migrated one issue at a time
    (#668, #800, #804, #835, #923) because nothing stopped a sixth from being
    written. This is that stop: the failure message tells the author what to
    check, and the allowlist above records the answer for the next reader.
    """
    unknown = sorted(
        f"{key} (line{'s' if len(lines) > 1 else ''} "
        f"{', '.join(str(n) for n in lines)})"
        for key, lines in _score_readers().items()
        if key not in _ALLOWED_SCORE_READERS
    )
    assert not unknown, (
        "New `.score` read(s) outside the game layer:\n  "
        + "\n  ".join(unknown)
        + "\n\nIn team mode `PlayerSession.score` is not the room's truth — "
        "`Team.score` is, via `get_ranked_participants()`. If the object above "
        "is a participant (a team, or a player who joined no team), add it to "
        "`_ALLOWED_SCORE_READERS` with a one-line note. If it is a person, "
        "route it through `get_ranked_participant_for(name)` instead."
    )


def test_the_allowlist_has_no_dead_entries() -> None:
    """An allowlist that outlives its readers stops documenting anything."""
    found = _score_readers()
    dead = sorted(key for key in _ALLOWED_SCORE_READERS if key not in found)
    assert not dead, f"Allowlisted `.score` readers that no longer exist: {dead}"


@pytest.mark.parametrize("module", ["analytics.py"])
def test_the_analytics_module_reads_no_score_attribute(module: str) -> None:
    """Analytics sees names and numbers, never a domain object (#923).

    ``_record_analytics`` narrows participants to ``{name: score}`` on purpose,
    so that the module can evolve without learning what a team is. An
    attribute read here would mean that boundary moved.
    """
    keys = [k for k in _score_readers() if k.startswith(f"{module}::")]
    assert keys == []
