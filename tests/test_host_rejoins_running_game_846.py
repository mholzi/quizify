"""The host page comes back into the game it left (#846).

From the v1.16.0-RC2 live test. A host runs the evening from
``/quizify/admin`` without joining as a player — the default flow since the
lobby grew start-without-joining. Their tab closes. Reopening
``/quizify/admin`` showed the **setup screen** — "Ready for a round of
Quizify?", the preset, the pack picker — while the television and four phones
were on round 7 of 10. Twice in one session, and again after a hard reload.

The socket was fine: ``Admin authenticated with valid session token`` in the
log, no refusal anywhere, and the snapshot ``_handle_admin_connect`` sends
carries the whole reveal (``serialize_state_snapshot`` → ``round_summary``).
The host page simply had nowhere to put it. ``handleGameState``'s
``ANSWER_REVEAL`` case was an explicit no-op, written on the premise that "the
production flow always redirects the host to /quizify/player on game start" —
the same premise #618 already had to undo for the LIVE reveal, which is where
``handleRoundSummary`` came from. The snapshot path kept it.

And ANSWER_REVEAL is exactly where a host-less room waits: nothing advances the
game until somebody presses Next, so every reconnect in that evening landed on
the one phase with no landing.

The screen it landed on instead is not inert. "Open lobby →" puts the host page
into a lobby view over a game that is still running, and Start Game from there
reaches the server and destroys it — ``_handle_start_game`` resets any
non-LOBBY phase on purpose (Markus, 2026-05-31: a fresh start must apply the
fresh settings). That behaviour is right; being shown the button was the bug.

These are structural assertions on the shipped source, the pattern this file's
neighbours (#618, #806, #699, #832) use for admin.js: it is one closed IIFE
with no exports, so there is nothing a node test can call.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_CC = _REPO_ROOT / "custom_components" / "quizify"
_ADMIN_JS = _CC / "www" / "js" / "admin.js"


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _switch_case(body: str, label: str) -> str:
    """One ``case 'X':`` arm of the phase switch, up to its ``break;``."""
    start = body.index("case '" + label + "':")
    end = body.index("break;", start)
    return body[start:end]


def _phase_switch() -> str:
    body = _function_body(_ADMIN_JS.read_text("utf-8"), "function handleGameState(")
    return body[body.index("switch (msg.phase)") :]


# ---------------------------------------------------------------------------
# The premise: the server hands the page everything it needs
# ---------------------------------------------------------------------------


def test_the_reconnect_snapshot_describes_the_reveal() -> None:
    """Nothing was missing on the wire, which is why the log looked clean."""
    source = (_CC / "server" / "serializers.py").read_text("utf-8")
    body = source.split('snapshot["round_summary"] = {', 1)[1].split("\n        }", 1)[0]

    assert '"question_text"' in body
    assert '"correct_answer"' in body


def test_the_admin_connect_frame_is_that_snapshot() -> None:
    source = (_CC / "server" / "websocket.py").read_text("utf-8")
    body = source.split("async def _handle_admin_connect(", 1)[1].split(
        "\n    async def ", 1
    )[0]

    assert "state = self._snapshot(game_state)" in body
    assert 'state["type"] = "game_state"' in body


# ---------------------------------------------------------------------------
# …and the page has somewhere to put it
# ---------------------------------------------------------------------------


def test_the_reveal_no_longer_leaves_the_host_on_the_setup_screen() -> None:
    """The defect itself. A game view, not an invitation to start a new game."""
    arm = _switch_case(_phase_switch(), "ANSWER_REVEAL")

    assert "showView('game')" in arm


def test_the_reveal_renders_what_the_snapshot_carries() -> None:
    """Landing on an empty game view would only move the confusion."""
    arm = _switch_case(_phase_switch(), "ANSWER_REVEAL")

    assert "msg.round_summary" in arm
    assert "question_text" in arm
    assert "renderLeaderboard(els.gameLeaderboard" in arm


def test_the_reveal_reuses_the_live_renderer() -> None:
    """``handleRoundSummary`` is what un-hides Next Question and End Game
    (#618) and what relabels Next on the last round (#806). A second copy here
    would drift from both, and the host page has to offer the one step the room
    is waiting for — that is the entire reason to come back."""
    arm = _switch_case(_phase_switch(), "ANSWER_REVEAL")

    assert "handleRoundSummary(" in arm
    assert "last_round" in arm


def test_no_running_phase_can_fall_through_to_the_setup_screen_again() -> None:
    """The class of bug, not just its instance: an unlisted phase used to mean
    "leave the host wherever they were", and on a fresh page load that is the
    setup screen with a live Start Game on it."""
    switch = _phase_switch()

    assert "default:" in switch
    default_arm = switch[switch.index("default:") :]
    assert "showView('game')" in default_arm


def test_the_lobby_arm_is_deliberately_not_that() -> None:
    """Guards the fix against overreach. A host who reopens the page after the
    game was reset SHOULD get the setup screen — that is where a new game is
    configured — so LOBBY keeps its own arm and its own rule."""
    arm = _switch_case(_phase_switch(), "LOBBY")

    assert "showView('lobby')" in arm
    assert "views.lobby.classList.contains('active')" in arm
