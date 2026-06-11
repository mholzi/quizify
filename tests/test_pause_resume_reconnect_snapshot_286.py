"""Regression tests for the P0 pause/resume/reconnect/snapshot cluster.

Four issues shared ONE root cause: state reaching a PLAYER in CANONICAL answer
order while ``submit_answer`` maps the tapped index through that player's OWN
per-player shuffle (#253). The #253 fix projected only the join/reconnect
snapshot; three other paths still leaked canonical order or never broadcast at
all:

* #286 — ``get_state`` reply and manual resume sent the raw (unprojected)
  snapshot to players, mis-scoring ~2/3 of taps after a mid-round reconnect /
  resume.
* #287 — auto-resume after an admin reconnect resumed the game but never
  broadcast it, leaving the other players frozen on the "Host disconnected"
  paused-view while their timers ran out (scored as timeouts).
* #295 — pause/resume froze per-player timers but not the round wall-clock
  (``round_start_time``) nor the per-player elapsed, so a late joiner post-resume
  got an instantly-expired timer and the speed bonus was inflated.
* #297 — the snapshot leaderboard omitted client-contract fields
  (``submitted`` / ``best_streak`` / ``rounds_played`` / ``powerups_used`` /
  ``round_score``), so a reconnect-after-submit never re-locked and FINALE
  reconnect showed 0 stats.

These tests exercise the real ``QuizifyGameState`` + ``RoundMessageBuilder`` +
``QuizifyWebSocketHandler`` so they catch drift between the snapshot, the
shuffle store, the timers and the submit path.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    runtime = _FakeRuntime(tmp_path)
    return QuizifyGameState(runtime=runtime, entry_id="test")


@pytest.fixture
def builder() -> RoundMessageBuilder:
    return RoundMessageBuilder()


def _handler(
    game: QuizifyGameState, tmp_path: Path
) -> tuple[QuizifyWebSocketHandler, dict]:
    """Build a handler whose per-socket sends are recorded by ws-id.

    Returns ``(handler, sends)`` where ``sends[id(ws)]`` is the list of message
    dicts delivered to that socket. ``broadcast`` is recorded too (last call).
    """
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._get_game_state = lambda: game  # type: ignore[assignment]

    sends: dict = {"by_ws": {}, "broadcasts": []}

    async def _send(ws, message: dict) -> None:
        sends["by_ws"].setdefault(id(ws), []).append(message)

    async def _broadcast(message: dict) -> None:
        sends["broadcasts"].append(message)

    h._conn.send = _send  # type: ignore[assignment]
    h._conn.broadcast = _broadcast  # type: ignore[assignment]
    return h, sends


def _start_round(game: QuizifyGameState) -> None:
    """Drive into QUESTION_ACTIVE with canonical + per-player shuffles, the way
    ``_start_next_question`` does. Alice/Bob get FIXED distinct non-identity
    shuffles so the canonical-vs-player contrast holds deterministically."""
    game.add_player("Alice", _ws())
    game.add_player("Bob", _ws())
    game.start_game(language="de", num_rounds=3, difficulty="easy")
    question = game.start_next_question()
    assert question is not None
    base = list(range(len(question.answers)))
    canonical = base[1:] + base[:1]
    alice = base[2:] + base[:2]
    bob = base[3:] + base[:3] if len(base) > 3 else base[1:] + base[:1]
    game.set_round_shuffle(canonical, [question.answers[i].text for i in canonical])
    game.set_player_shuffle("Alice", alice)
    game.set_player_shuffle("Bob", bob)


# ---------------------------------------------------------------------------
# #286 (a) — get_state reply for a player returns projected answers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_state_reply_projects_for_player(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """The ``get_state`` reply to a PLAYER socket must carry the player's OWN
    shuffled answer order — identical to what the join/reconnect push sends —
    so the tapped index scores the visible answer. Canonical order would
    mis-score ~2/3 of taps after every mid-round reconnect."""
    h, sends = _handler(game, tmp_path)
    _start_round(game)
    alice = game.get_player("Alice")
    alice_ws = alice.ws
    h._conn.add_connection(alice_ws, is_admin=False, is_dashboard=False)

    await h._handle_message(alice_ws, {"type": "get_state"}, is_admin=False)

    msgs = sends["by_ws"][id(alice_ws)]
    state_msgs = [m for m in msgs if m.get("type") == "game_state"]
    assert state_msgs, "get_state produced no game_state reply"
    visible = state_msgs[-1]["question"]["answers"]

    question = game.get_current_question()
    shuffle = game.get_player_shuffle("Alice")  # what submit_answer uses
    assert len(visible) == len(question.answers)
    for i, text in enumerate(visible):
        assert text == question.answers[shuffle[i]].text

    # The reply must match the canonical-projection a join would have produced.
    join_projected = RoundMessageBuilder().project_snapshot_for_player(
        game, snapshot=game.get_state_snapshot(), player=alice
    )
    assert visible == join_projected["question"]["answers"]

    # End-to-end: tapping the visible position of the correct answer scores it.
    correct_text = next(a.text for a in question.answers if a.correct)
    tapped = visible.index(correct_text)
    original_index = shuffle[tapped]
    result = game.submit_answer("Alice", original_index)
    assert not isinstance(result, str)
    assert result.correct is True


@pytest.mark.asyncio
async def test_get_state_reply_stays_canonical_for_admin_dashboard(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """A pure admin/dashboard socket (no player session) still gets the
    canonical snapshot — projection only applies to player sockets."""
    h, sends = _handler(game, tmp_path)
    _start_round(game)
    dash_ws = _ws()
    h._conn.add_connection(dash_ws, is_admin=False, is_dashboard=True)

    await h._handle_message(dash_ws, {"type": "get_state"}, is_admin=False)

    msgs = sends["by_ws"][id(dash_ws)]
    state_msgs = [m for m in msgs if m.get("type") == "game_state"]
    assert state_msgs
    visible = state_msgs[-1]["question"]["answers"]
    canonical = game.get_state_snapshot()["question"]["answers"]
    assert visible == canonical


# ---------------------------------------------------------------------------
# #286 (b) — manual resume fans out projected per-player state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_resume_broadcasts_projected_and_submit_scores(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """After ``resume_game`` each player must receive a game_state whose answers
    are in THEIR shuffle order, and a submit through that order scores
    correctly (the bug scored the wrong answer ~2/3 of the time)."""
    h, sends = _handler(game, tmp_path)
    _start_round(game)
    alice = game.get_player("Alice")
    bob = game.get_player("Bob")
    h._conn.add_connection(alice.ws, is_admin=False, is_dashboard=False)
    h._conn.add_connection(bob.ws, is_admin=False, is_dashboard=False)

    assert game.pause() is True
    assert game.phase == GamePhase.PAUSED

    # No-op the tick task so the handler doesn't spin up a real timer loop.
    h._start_timer_tick = lambda gs: None  # type: ignore[assignment]
    await h._handle_resume_game(alice.ws, game)
    assert game.phase == GamePhase.QUESTION_ACTIVE

    question = game.get_current_question()
    for player in (alice, bob):
        msgs = sends["by_ws"].get(id(player.ws), [])
        state_msgs = [m for m in msgs if m.get("type") == "game_state"]
        assert state_msgs, f"{player.name} got no resumed game_state"
        visible = state_msgs[-1]["question"]["answers"]
        shuffle = game.get_player_shuffle(player.name)
        for i, text in enumerate(visible):
            assert text == question.answers[shuffle[i]].text

        # Tap the visible position of the correct answer → scores correct.
        correct_text = next(a.text for a in question.answers if a.correct)
        tapped = visible.index(correct_text)
        result = game.submit_answer(player.name, shuffle[tapped])
        assert not isinstance(result, str)
        assert result.correct is True


# ---------------------------------------------------------------------------
# #287 — auto-resume after admin reconnect broadcasts to everyone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_reconnect_auto_resume_broadcasts_to_players(
    game: QuizifyGameState, tmp_path: Path
) -> None:
    """When the admin reconnects and we auto-resume the game WE paused
    (admin_disconnected), every OTHER player must receive a resumed game_state
    so they leave the paused-view — otherwise their timers run out unseen."""
    h, sends = _handler(game, tmp_path)
    _start_round(game)
    alice = game.get_player("Alice")  # plain player still on paused-view
    # Make Alice an admin too? No — keep Alice as the stuck player. Host = admin.
    game.add_player("Host", _ws())
    host = game.get_player("Host")
    host.is_admin = True
    h._conn.add_connection(alice.ws, is_admin=False, is_dashboard=False)

    # Issue a session token for Host so reconnect resolves it.
    token = h._conn.create_session_token("Host")

    # WE pause because the admin disconnected.
    assert game.pause(reason="admin_disconnected") is True
    assert game.phase == GamePhase.PAUSED

    h._start_timer_tick = lambda gs: None  # type: ignore[assignment]
    new_admin_ws = _ws()
    await h._handle_reconnect(
        new_admin_ws, {"session_token": token}, game
    )

    # Game resumed...
    assert game.phase == GamePhase.QUESTION_ACTIVE
    # ...and the stuck player got a resumed game_state (leaves paused-view).
    alice_states = [
        m
        for m in sends["by_ws"].get(id(alice.ws), [])
        if m.get("type") == "game_state"
        and m.get("phase") == GamePhase.QUESTION_ACTIVE.value
    ]
    assert alice_states, "stuck player never received the resumed state (#287)"
    # And it carries Alice's own shuffle order (projected).
    question = game.get_current_question()
    shuffle = game.get_player_shuffle("Alice")
    visible = alice_states[-1]["question"]["answers"]
    for i, text in enumerate(visible):
        assert text == question.answers[shuffle[i]].text


# ---------------------------------------------------------------------------
# #295 — pause/resume keeps the round wall-clock and elapsed sane
# ---------------------------------------------------------------------------


def test_resume_shifts_round_start_time_so_late_joiner_timer_is_sane(
    game: QuizifyGameState,
) -> None:
    """After a long pause, ``round_start_time`` must be advanced by the pause
    duration so a player joining post-resume gets a full-ish late-joiner timer
    — not the instantly-expired ~0.5s timer the un-shifted wall-clock gave."""
    _start_round(game)
    pc = game._phase_controller
    pc.round_duration = 30.0
    # Backdate the round start so only ~1s has nominally elapsed.
    pc.round_start_time = time.monotonic() - 1.0

    assert game.pause() is True
    # Simulate a long pause by backdating paused_at by 20s.
    pc.paused_at = time.monotonic() - 20.0
    before_start = pc.round_start_time
    assert game.resume() is True

    # round_start_time advanced by ~the pause duration (20s), not left stale.
    assert pc.round_start_time - before_start >= 19.0

    # A late joiner now gets a sane (multi-second) timer, not ~0.5s.
    game.add_player("Zoe", _ws())
    pc.add_late_joiner_timer("Zoe")
    zoe_timer = game.get_player_timer("Zoe")
    assert zoe_timer is not None
    assert zoe_timer.get_remaining() > 5.0

    # And the wall-clock snapshot time isn't already expired.
    assert pc.time_remaining_for_snapshot() > 5.0
    assert pc.round_wall_clock_expired() is False


def test_resume_preserves_per_player_elapsed_so_speed_bonus_not_inflated(
    game: QuizifyGameState,
) -> None:
    """Resume must preserve each player's pre-pause elapsed so the speed bonus
    (scored off elapsed) isn't inflated. A naive fresh timer at resume would
    report elapsed ≈ 0 → maximum speed bonus for a player who actually spent
    most of the round before the pause."""
    _start_round(game)
    alice_timer = game.get_player_timer("Alice")
    assert alice_timer is not None
    # Backdate Alice's timer so ~10s has elapsed before the pause.
    alice_timer._start_time = time.monotonic() - 10.0
    pre_pause_elapsed = alice_timer.get_elapsed()
    assert pre_pause_elapsed >= 9.5

    assert game.pause() is True
    frozen_remaining = game._phase_controller.paused_remaining["Alice"]
    # Simulate the pause lasting 15s.
    game._phase_controller.paused_at = time.monotonic() - 15.0
    assert game.resume() is True

    resumed_timer = game.get_player_timer("Alice")
    assert resumed_timer is not None
    # Elapsed is preserved (NOT reset to ~0): the speed bonus stays honest.
    assert resumed_timer.get_elapsed() >= pre_pause_elapsed - 0.5
    # And remaining is still the value frozen at pause, unaffected by the 15s
    # the game spent paused (a naive fresh full-round timer would jump back up).
    assert abs(resumed_timer.get_remaining() - frozen_remaining) <= 0.5


# ---------------------------------------------------------------------------
# #297 — snapshot leaderboard carries the full client contract
# ---------------------------------------------------------------------------


def test_snapshot_leaderboard_carries_client_contract_fields(
    game: QuizifyGameState,
) -> None:
    """``get_state_snapshot`` leaderboard must be wire-identical to the live
    ``serialize_leaderboard`` broadcast — carrying ``submitted``, ``best_streak``,
    ``rounds_played``, ``powerups_used``, ``round_score`` — so reconnect-after-
    submit re-locks and FINALE reconnect shows real stats."""
    _start_round(game)
    question = game.get_current_question()
    correct_idx = next(i for i, a in enumerate(question.answers) if a.correct)
    game.submit_answer("Alice", correct_idx)

    leaderboard = game.get_state_snapshot()["leaderboard"]
    assert leaderboard
    required = {
        "submitted",
        "best_streak",
        "rounds_played",
        "powerups_used",
        "round_score",
        "is_admin",
    }
    for entry in leaderboard:
        missing = required - set(entry)
        assert not missing, f"leaderboard entry missing {missing}: {entry}"

    alice_entry = next(e for e in leaderboard if e["name"] == "Alice")
    # Alice submitted this round → the client re-locks her buttons off this.
    assert alice_entry["submitted"] is True

    # Wire-identical to the live serializer used by the broadcasts.
    from custom_components.quizify.server.serializers import serialize_leaderboard

    assert leaderboard == serialize_leaderboard(game.get_players())
