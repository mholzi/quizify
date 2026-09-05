"""Issue #750 — three message types the server sent that nobody listened to.

``kicked``, ``guess_accepted`` and ``streak_milestone`` were all broadcast
with comments describing UX that was never built. The resolution splits:

* ``kicked``          — built. A removed guest now gets a screen that says so
                        instead of a socket that simply goes quiet.
* ``guess_accepted``  — built. The estimate round has no ``answer_result``, so
                        this ack is the only word the server says about a
                        guess; the slider's "Submitted!" tick now waits for it
                        rather than claiming success on tap.
* ``streak_milestone`` — deleted. No TV or admin surface ever grew the flash it
                        was added for, and the phone toast has always run off
                        ``answer_result.new_streak``. TTS and the HA bus event
                        were always separate calls and stay.

The client half is JS, so it is asserted at text level over the *built*
bundle — the same pattern as ``test_reconnect_failed_227.py`` and
``test_fe_ux_r4.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.phase_controller import GamePhase  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    AnswerResult,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
BUNDLE = (WWW / "js" / "player.bundle.js").read_text("utf-8")
PLAYER_CORE = (WWW / "js" / "player-core.js").read_text("utf-8")
PLAYER_GAME = (WWW / "js" / "player-game.js").read_text("utf-8")
PLAYER_HTML = (WWW / "player.html").read_text("utf-8")
WEBSOCKET_PY = (
    _REPO_ROOT / "custom_components" / "quizify" / "server" / "websocket.py"
).read_text("utf-8")
I18N = {
    lang: json.loads((WWW / "i18n" / f"{lang}.json").read_text("utf-8"))
    for lang in ("en", "de", "es")
}

KICKED_KEYS = (
    "removedTitle",
    "removedHint",
    "removedRejoinHint",
    "removedRejoin",
)


# --------------------------------------------------------------------------
# The invariant the issue is really about
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg_type", ["kicked", "guess_accepted", "streak_milestone"]
)
def test_no_message_type_is_sent_into_the_void(msg_type: str) -> None:
    """Sent by the server iff handled by the player client.

    This is the whole of #750 in one line: a payload the server builds and
    nothing reads is a promise in comment form. Either end may be the one
    that goes, but they go together.
    """
    sent = f'"type": "{msg_type}"' in WEBSOCKET_PY
    heard = f"case '{msg_type}':" in BUNDLE
    assert sent == heard, (
        f"{msg_type}: server sends={sent}, player client handles={heard} — "
        "wire the receiving end or drop the send"
    )


# --------------------------------------------------------------------------
# kicked — built
# --------------------------------------------------------------------------


class TestKickedScreen:
    def test_player_page_has_a_removed_view(self) -> None:
        assert 'id="kicked-view"' in PLAYER_HTML
        for key in KICKED_KEYS:
            assert f'data-i18n="errors.{key}"' in PLAYER_HTML, key
        # A way forward, not a dead end.
        assert 'id="kicked-rejoin-btn"' in PLAYER_HTML

    def test_kicked_view_is_registered_with_showview(self) -> None:
        """showView only touches IDs in ``viewIds`` — an unregistered view can
        never be revealed (the #239 trap)."""
        start = BUNDLE.index("var viewIds = [")
        view_ids = BUNDLE[start : BUNDLE.index("]", start)]
        assert "'kicked-view'" in view_ids

    def test_client_routes_the_kicked_message_to_that_view(self) -> None:
        idx = BUNDLE.index("case 'kicked':")
        case = BUNDLE[idx : idx + 1400]
        assert "showView('kicked-view')" in case
        # The session token must die with the kick, or the reconnect ladder
        # climbs straight back into the lobby we were thrown out of.
        assert "clearSession()" in case
        assert "state.playerName = null" in case

    @pytest.mark.parametrize("lang", ["en", "de", "es"])
    def test_removal_strings_exist_in_every_bundle(self, lang: str) -> None:
        errors = I18N[lang]["errors"]
        for key in KICKED_KEYS:
            assert key in errors, f"{lang}.json is missing errors.{key}"
            assert errors[key].strip(), f"{lang}.json errors.{key} is empty"

    @pytest.mark.parametrize("lang", ["de", "es"])
    def test_removal_strings_are_actually_translated(self, lang: str) -> None:
        """A German player must not read an English screen."""
        for key in KICKED_KEYS:
            assert I18N[lang]["errors"][key] != I18N["en"]["errors"][key], (
                f"{lang}.json errors.{key} is still the English string"
            )

    @pytest.mark.asyncio
    async def test_server_still_sends_kicked_before_closing(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """The message the new screen depends on. Send *then* close — the
        other order delivers nothing."""
        admin_ws = _ws()
        target_ws = _ws()
        game.add_player("Admin", admin_ws)
        game.add_player("Vic", target_ws)
        game.get_player("Admin").is_admin = True
        game.phase = GamePhase.LOBBY

        await handler._handle_kick_player(admin_ws, {"player_name": "Vic"}, game)

        payloads = _sent_payloads(target_ws)
        assert any(p.get("type") == "kicked" for p in payloads), payloads
        target_ws.close.assert_awaited()


# --------------------------------------------------------------------------
# guess_accepted — built
# --------------------------------------------------------------------------


class TestGuessAck:
    def test_client_handles_the_ack(self) -> None:
        idx = BUNDLE.index("case 'guess_accepted':")
        assert "confirmGuess" in BUNDLE[idx : idx + 800]

    def test_estimate_submit_no_longer_claims_success_on_tap(self) -> None:
        """The tick used to appear the instant the player tapped, whether or
        not the server ever took the guess."""
        start = PLAYER_GAME.index("function handleEstimateSubmit(")
        body = PLAYER_GAME[start : PLAYER_GAME.index("\n    }", start)]
        assert "hasSubmitted = true" not in body
        assert "_guessPending = true" in body

    def test_only_the_ack_locks_the_round(self) -> None:
        start = PLAYER_GAME.index("function confirmGuess(")
        body = PLAYER_GAME[start : PLAYER_GAME.index("\n    }", start)]
        assert "hasSubmitted = true" in body

    def test_a_refused_guess_gives_the_slider_back(self) -> None:
        assert "game.releaseGuess(msg.code)" in PLAYER_CORE
        start = PLAYER_GAME.index("var GUESS_RETRYABLE_CODES")
        codes = PLAYER_GAME[start : PLAYER_GAME.index("\n", start)]
        # Retrying only helps where a retry can succeed.
        assert "INVALID_ACTION" in codes and "FROZEN" in codes
        assert "ALREADY_SUBMITTED" not in codes
        assert "ROUND_EXPIRED" not in codes

    @pytest.mark.asyncio
    async def test_server_still_acks_a_valid_guess(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        ws = _ws()
        player = MagicMock()
        player.name = "Ann"
        gs = MagicMock()
        gs.phase = GamePhase.QUESTION_ACTIVE
        gs.get_player_by_ws.return_value = player
        question = MagicMock()
        question.is_estimate = True
        gs.get_current_question.return_value = question
        gs.submit_guess.return_value = None

        await handler._handle_submit_answer(ws, {"guess": 42}, gs)

        assert any(p.get("type") == "guess_accepted" for p in _sent_payloads(ws))


# --------------------------------------------------------------------------
# streak_milestone — deleted
# --------------------------------------------------------------------------


class TestStreakMilestoneRemoved:
    def test_the_broadcast_is_gone_from_the_source(self) -> None:
        assert '"streak_milestone"' not in WEBSOCKET_PY

    def test_the_phone_toast_still_rides_on_answer_result(self) -> None:
        """The consumer that always existed. Deleting the broadcast must not
        take the celebration with it."""
        idx = BUNDLE.index("case 'answer_result':")
        assert "new_streak" in BUNDLE[idx : idx + 700]

    @pytest.mark.asyncio
    async def test_milestone_speaks_and_fires_but_does_not_broadcast(
        self, handler: QuizifyWebSocketHandler
    ) -> None:
        ws = _ws()
        player = MagicMock()
        player.name = "Ann"
        gs = MagicMock()
        gs.phase = GamePhase.QUESTION_ACTIVE
        gs.get_player_by_ws.return_value = player
        question = MagicMock()
        question.is_estimate = False
        gs.get_current_question.return_value = question
        gs.get_player_shuffle.return_value = [0, 1, 2, 3]
        gs.submit_answer.return_value = AnswerResult(
            player_id="Ann",
            correct=True,
            points_earned=120,
            new_streak=5,
            new_total=600,
            milestone_bonus=50,
            milestone_streak=5,
        )

        announcer = MagicMock()
        emitter = MagicMock()
        handler._tts_announcer = announcer
        handler._event_emitter = emitter

        try:
            await handler._handle_submit_answer(ws, {"answer_index": 0}, gs)

            broadcast_types = [
                call.args[0].get("type")
                for call in handler._conn.broadcast.await_args_list
                if call.args and isinstance(call.args[0], dict)
            ]
            assert "streak_milestone" not in broadcast_types, broadcast_types

            # The real consumers are untouched.
            announcer.announce_milestone.assert_called_once_with("Ann", 5)
            emitter.notify_streak_milestone.assert_called_once_with("Ann", 5, 50)

            # And the player still learns about it, on the message that has
            # always carried it.
            result = next(
                p for p in _sent_payloads(ws) if p.get("type") == "answer_result"
            )
            assert result["new_streak"] == 5
            assert result["milestone_bonus"] == 50
        finally:
            task = handler._progress_flush_task
            if task is not None:
                task.cancel()


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _sent_payloads(ws: MagicMock) -> list[dict]:
    return [
        call.args[0]
        for call in ws.send_json.await_args_list
        if call.args and isinstance(call.args[0], dict)
    ]


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(tmp_path: Path, game: QuizifyGameState) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    return h
