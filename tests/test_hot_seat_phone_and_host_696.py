"""#696 / #697 / #699 — the Hot Seat has to leave the phone and the host usable.

Three faults with one origin: the detour was built as its own island and never
handed the surfaces back.

* **#696** ``renderSeatAnswers`` replaced ``#answer-buttons`` innerHTML with
  bare buttons. That container's markup is what ``renderQuestion`` fills — it
  reuses the existing ``.answer-btn`` elements and writes only their
  ``.answer-text`` child — so once it was gone the player who *won* the chair
  saw the hot seat's answers under every later question and could not answer
  any of them.
* **#697** the detour is entered from ANSWER_REVEAL, which hides the admin
  control bar; nothing brought it back, and HOT_SEAT_REVEAL is left only by an
  explicit ``next_round``. A host playing along had no way to advance.
* **#699** the admin tab had no case for the detour phases, and a reload during
  any of them lands on the setup screen where the only visible control is
  Start — which resets a non-LOBBY game.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"


def _read(name: str) -> str:
    return (WWW / "js" / name).read_text(encoding="utf-8")


def test_the_seat_grid_is_filled_not_replaced() -> None:
    """The one property that broke every later round.

    Written against the destruction, not the replacement: any future rewrite
    that clears the container fails here, whatever it puts back.
    """
    source = _read("player-hotseat.js")
    start = source.index("function renderSeatAnswers(")
    body = source[start : source.index("\n    }", start)]
    # The comment above the fix names the old mechanism, so read code only.
    code = re.sub(r"//.*", "", body)
    assert "innerHTML" not in code, (
        "renderSeatAnswers destroys the markup renderQuestion depends on"
    )
    assert ".answer-text" in code, "the grid is filled the way renderQuestion fills it"


def test_the_seat_answer_goes_through_the_delegated_handler() -> None:
    """An inline onclick shadowed the delegated one and outlived the round."""
    hotseat = _read("player-hotseat.js")
    core = _read("player-core.js")
    assert "b.onclick" not in hotseat, "inline handler is back on the answer grid"
    assert "handleSeatAnswerClick" in hotseat
    assert "handleSeatAnswerClick" in core, (
        "player-core must route the tap while the seat holder is answering"
    )


def test_the_host_can_advance_out_of_the_hot_seat() -> None:
    """HOT_SEAT_REVEAL ends only on next_round, so the button has to be there."""
    core = _read("player-core.js")
    start = core.index("case 'HOT_SEAT_AUCTION':")
    body = core[start : core.index("case 'PAUSED':", start)]
    assert "admin-control-bar" in body, (
        "the control bar is still hidden for the whole detour"
    )
    assert "next-round-admin-btn" in body


def test_the_admin_tab_knows_the_detour_phases() -> None:
    admin = _read("admin.js")
    for phase in ("WAGER_ACTIVE", "HOT_SEAT_AUCTION", "HOT_SEAT", "HOT_SEAT_REVEAL"):
        assert f"case '{phase}':" in admin, f"admin.js has no case for {phase}"
    assert "function setDetourNotice(" in admin


def test_a_live_round_is_never_idle_enough_to_reload_the_host_out_of_it() -> None:
    """The reload lands on setup, where Start is the only visible control and
    Start resets a running game. Every phase that is not the lobby or the
    finale therefore has to block the service-worker reload."""
    admin = _read("admin.js")
    start = admin.index("window.quizifyIsIdleForReload")
    body = admin[start : admin.index("};", start)]
    for phase in (
        "QUESTION_ACTIVE",
        "LIGHTNING",
        "ANSWER_REVEAL",
        "WAGER_ACTIVE",
        "LIGHTNING_RECAP",
        "HOT_SEAT_AUCTION",
        "HOT_SEAT",
        "HOT_SEAT_REVEAL",
        "PAUSED",
    ):
        assert re.search(rf"case '{phase}':", body), f"{phase} still counts as idle"


def test_the_phone_shows_the_hot_seat_question_instead_of_the_last_one() -> None:
    """The server sends the text to every phone, seated or not; the handler
    never rendered it, so the room bid under the previous round's question and
    its green/red reveal colouring."""
    source = _read("player-hotseat.js")
    start = source.index("function handleQuestion(")
    body = source[start : source.index("\n    function renderSeatAnswers", start)]
    assert "question-text" in body, "the stale question is still on screen"
