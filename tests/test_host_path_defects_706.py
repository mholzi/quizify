"""#706 — five small defects on the host's path.

Each was verified on its own; they are grouped the way #667 grouped its four,
because every one of them is a couple of lines and all of them sit between the
host and a running game.

1. **Stats was dead until the lobby had been opened**, then opened one tab per
   visit — ``initStatsLink`` was called from the "Open lobby" handler alone,
   so the button on the setup screen had no listener until then and collected
   another one on every trip through the lobby.
2. **The timer was written into the 6 px bar container**, wiping
   ``.timer-bar-fill``; the element built for it — 1.5 rem bold tabular with
   warning and critical states — was never written.
3. **"Final Round!" never came down again.** The only line that hid it lives
   in ``updateGameView``, which nothing has called since #619, and Play again
   keeps the phones on the same page.
4. **The television kept the previous question's answer tally**, through the
   reveal, the lightning round, the finale and into the next lobby, where it
   sat above the QR code.
5. **An empty lobby read "Ready at 1 players · still need 1 more"** — a plural
   for one and a countdown for a threshold nobody waits on.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"


def _js(name: str) -> str:
    return (WWW / "js" / name).read_text(encoding="utf-8")


def _html(name: str) -> str:
    return (WWW / name).read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Comments quote the old behaviour, so assertions read code only."""
    return re.sub(r"//.*", "", text)


def _function_body(source: str, signature: str, end: str = "\n    }") -> str:
    start = source.index(signature)
    return source[start : source.index(end, start)]


# --- 1. the Stats link -----------------------------------------------------


def test_the_stats_link_is_bound_at_load_not_on_the_way_to_the_lobby() -> None:
    source = _js("admin.js")
    lobby_handler = _function_body(
        source, "on(els.startGameBtn, 'click'", end="\n    });"
    )
    assert "initStatsLink" not in _strip_comments(lobby_handler), (
        "binding from the lobby handler leaves the setup-screen button dead "
        "until the host has been to the lobby"
    )
    # Called from the module body, next to connect().
    assert re.search(r"^    initStatsLink\(\);", source, re.MULTILINE)


def test_binding_the_stats_link_twice_adds_one_listener() -> None:
    """Otherwise one tap opens one tab per visit."""
    body = _strip_comments(_function_body(_js("admin.js"), "function initStatsLink("))
    assert "_statsLinkBound" in body, "no guard against a second listener"
    assert body.index("_statsLinkBound") < body.index("addEventListener"), (
        "the guard has to run before the listener is attached"
    )


# --- 2. the in-game timer --------------------------------------------------


def test_the_timer_writes_to_the_text_element_not_the_bar() -> None:
    """#admin-timer-bar is the 6 px .timer-bar-container and holds the fill."""
    source = _js("admin.js")
    start = source.index("var adminTimer = {")
    timer = _strip_comments(source[start : source.index("\n    };", start)])
    assert "admin-timer-bar-text" in _strip_comments(source), (
        "the styled element is still never written"
    )
    assert not re.search(
        r"getElementById\(\s*'admin-timer-bar'\s*\)\.textContent", timer
    )
    for line in timer.splitlines():
        if "textContent" in line:
            assert "Fill" not in line and "adminTimerFillEl" not in line, (
                f"text is being written into the bar again: {line.strip()}"
            )


def test_the_timer_uses_the_warning_and_critical_states() -> None:
    """They exist in styles.css for this element and were unreachable."""
    source = _js("admin.js")
    start = source.index("var adminTimer = {")
    timer = _strip_comments(source[start : source.index("\n    };", start)])
    assert "'critical'" in timer and "'warning'" in timer
    css = (WWW / "css" / "styles.css").read_text(encoding="utf-8")
    assert ".timer-text.warning" in css and ".timer-text.critical" in css


# --- 3. the final-round banner ---------------------------------------------


def test_the_final_round_banner_comes_down_on_a_normal_round() -> None:
    body = _strip_comments(
        _function_body(_js("player-core.js"), "function handleQuestionStarted(")
    )
    assert "last-round-banner" in body, (
        "nothing takes the pill down, so it survives into the next game"
    )
    assert "isFinalRound(msg)" in body, "it may only come down when it is not the final"


# --- 4. the television's answer tally --------------------------------------


def test_leaving_the_question_view_clears_the_answer_tally() -> None:
    """One funnel, so a future path cannot forget it again."""
    body = _strip_comments(
        _function_body(
            _html("dashboard.html"), "function showView(name)", end="\n        }"
        )
    )
    assert "answerProgress" in body, "showView still leaves the tally on the board"
    assert "!== 'question'" in body, "the question view sets its own tally"


def test_the_tally_lives_outside_every_view() -> None:
    """Which is why hiding a view was never enough.

    If it is ever moved inside #question-view this test should be deleted
    along with the guard above.
    """
    html = _html("dashboard.html")
    tally = html.index('id="answer-progress"')
    header = html.index('class="dashboard-header"')
    first_view = html.index('id="question-view"')
    assert header < tally < first_view


# --- 5. the empty lobby's countdown ----------------------------------------


def test_the_countdown_line_is_hidden_at_a_threshold_of_one() -> None:
    source = _js("admin.js")
    body = _strip_comments(_function_body(source, "function renderLobbyPlayers("))
    assert "LOBBY_MIN_PLAYERS > 1" in body, (
        'at a threshold of 1 the line can only ever read "Ready at 1 players · '
        'still need 1 more"'
    )
    assert re.search(r"LOBBY_MIN_PLAYERS\s*=\s*1", source), (
        "if the threshold rises, this test should be re-read rather than deleted"
    )
