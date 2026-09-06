"""Three things the television got wrong on the one screen the room reads.

**#805 — the board never switched to the game language.** ``dashboard.html``
called ``QuizifyI18n.init(getPreferredLanguage())`` once, off the Home
Assistant language, and ``handleGameState`` never looked at ``msg.language``
again — although ``game/state.py`` has put it in every snapshot since Mai-27
for exactly this purpose, and both the phones
(``player-core.js`` ``_syncServerLanguage``) and the host page follow the
host's pick. A German HA running an English game therefore framed English
questions in "Bestenliste", "Wusstest du schon?" and "Frage 3 / 5" on the
television, while every guest's phone in the same room had switched to
English. Unlike the phone (#492) a TV has no language picker, so there is no
deliberate choice to protect: the HA language is the pre-game default and
nothing more.

**#807 — tonight's tally never came down.** ``#evening-tally`` sits in the
header outside every view and ``handleEveningTally`` was its only writer; it
hid itself only when a payload arrived with no leaders. The server sends it
once per finished game, from the second game of a sitting, and neither
``game_reset`` nor ``showView`` nor ``question_started`` cleared it. From game
three the room read "Tonight · Anna 2 wins · Ben 1 win" above the QR code in
the lobby and above the timer bar on every question and reveal — a
``flex-shrink: 0`` line plus 10px of margin out of the 549.8px left column
that #680/#688 fitted the longest questions into *without* it. This is the
same fix #706 gave the answer-progress line one element above.

**#808 — the category was a fixed 12px, and in the betting window it is the
whole bet.** Through an ordinary question ``.dashboard-category`` is an
eyebrow over the question text and small is defensible. In the final round's
wager window it is not: ``handleWagerProgress`` replaces the question with the
lock-in tally, so the category is the only clue the room's stake is placed on
(``player-game.js`` says as much for the phone). #707 clamped four content
strings and this was not among them; ``.dashboard-lightning-kicker`` carried
the same 12px. Both get #707's step, and the wager window additionally puts
the category into the question slot, where it is read at question size.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = (
    _REPO_ROOT / "custom_components" / "quizify" / "www" / "dashboard.html"
)

SOURCE = _DASHBOARD.read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    """The body of a top-level (8-space indented) function in dashboard.html."""
    marker = f"function {name}("
    assert marker in SOURCE, f"{name}() is gone"
    tail = SOURCE.split(marker, 1)[1]
    return tail.split("\n        }", 1)[0]


def _rule_body(selector: str) -> str:
    """The top-level style rule for ``selector`` — not a media-query override."""
    match = re.search(
        r"^        " + re.escape(selector) + r" \{(.*?)^        \}",
        SOURCE,
        re.S | re.M,
    )
    assert match, f"{selector} has no top-level rule"
    return match.group(1)


def _clamp_bounds(rule: str, selector: str) -> tuple[float, float]:
    match = re.search(r"font-size:\s*([^;]+);", rule)
    assert match, f"{selector} lost its font-size"
    value = match.group(1).strip()
    assert value.startswith("clamp("), (
        f"{selector} is back to a fixed {value} — the board is metres away"
    )
    args = value[len("clamp(") : value.rindex(")")].split(",")
    return (
        float(args[0].strip().rstrip("px")),
        float(args[-1].strip().rstrip("px")),
    )


# --------------------------------------------------------------------------
# #805 — the television follows the game's language
# --------------------------------------------------------------------------


def test_a_snapshot_carries_the_game_language() -> None:
    """The fix reads a field; this pins that the server still sends it."""
    state = (
        _REPO_ROOT / "custom_components" / "quizify" / "game" / "state.py"
    ).read_text(encoding="utf-8")
    snapshot = state.split("def get_state_snapshot(", 1)[1].split("\n    def ", 1)[0]
    assert '"language": self.language' in snapshot


def test_every_snapshot_reaches_the_language_sync() -> None:
    """``handleGameState`` is the only door every snapshot comes through."""
    assert "syncServerLanguage(msg)" in _fn_body("handleGameState"), (
        "handleGameState ignores msg.language again — a German TV will frame "
        "an English game in German"
    )


def test_the_sync_follows_the_phone() -> None:
    """Same guard shape as ``_syncServerLanguage`` in player-core.js."""
    body = _fn_body("syncServerLanguage")
    assert "msg.language" in body
    assert "QuizifyI18n.getLanguage() === msg.language" in body, (
        "without the equality guard every snapshot re-loads the bundle"
    )
    assert "QuizifyI18n.setLanguage(msg.language)" in body
    assert "initPageTranslations" in body, (
        "the static data-i18n labels are what the sweep repaints"
    )


def test_the_repaint_waits_for_the_bundle() -> None:
    """``setLanguage`` is async — repainting before it resolves paints the
    language the board is leaving."""
    body = _fn_body("syncServerLanguage")
    ordered = re.search(
        r"setLanguage\(msg\.language\)\s*\.then\(function \(\) \{\s*"
        r"QuizifyI18n\.initPageTranslations\(",
        body,
    )
    assert ordered, "initPageTranslations must run inside the setLanguage .then()"


def test_the_home_assistant_language_is_only_the_pre_game_default() -> None:
    """It may seed the boot, never override the running game."""
    assert SOURCE.count("getPreferredLanguage()") == 1
    boot = SOURCE.split("// ---- Init ----", 1)[1]
    assert "getPreferredLanguage()" in boot


# --------------------------------------------------------------------------
# #807 — tonight's tally is the finale's line and nobody else's
# --------------------------------------------------------------------------


def test_every_view_but_the_finale_takes_the_tally_down() -> None:
    body = _fn_body("showView")
    assert "els.eveningTally" in body, (
        "showView never touches #evening-tally — it will hang over the lobby "
        "QR code and the question timer from game three of a sitting"
    )
    guard = re.search(
        r"if \(name !== 'finale' && els\.eveningTally\) \{(.*?)\}", body, re.S
    )
    assert guard, "the tally must be scoped to the finale view"
    assert "classList.add('hidden')" in guard.group(1)


def test_the_tally_is_cleared_when_the_evening_is_reset() -> None:
    reset = SOURCE.split("case 'game_reset':", 1)[1].split("break;", 1)[0]
    assert "els.eveningTally" in reset, "game_reset leaves last night's tally in place"
    assert re.search(r"els\.eveningTally\.(textContent|innerHTML) = ''", reset)


def test_both_header_lines_are_cleared_the_same_way() -> None:
    """#706 fixed the answer progress and left the line below it. Neither may
    be allowed to drift back to a single writer."""
    body = _fn_body("showView")
    assert "els.answerProgress" in body
    assert "els.eveningTally" in body


# --------------------------------------------------------------------------
# #808 — the category is read from three metres, and it is the bet
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "selector", [".dashboard-category", ".dashboard-lightning-kicker"]
)
def test_the_category_type_scales_with_the_screen(selector: str) -> None:
    floor, ceiling = _clamp_bounds(_rule_body(selector), selector)
    assert floor >= 14, f"{selector} floors at {floor}px, below a legible 14px"
    assert ceiling >= 20, (
        f"{selector} tops out at {ceiling}px — a 4K television gets nothing"
    )


def test_the_wager_window_puts_the_category_in_the_question_slot() -> None:
    """No question has been sent; the category is the whole basis of the bet."""
    body = _fn_body("handleWagerProgress")
    written = re.search(
        r"els\.questionText\.textContent =(.*?);", body, re.S
    )
    assert written, "handleWagerProgress no longer writes the question slot"
    assert "msg.category" in written.group(1), (
        "the room decides its stake off the 12px eyebrow again"
    )


def test_the_wager_window_still_shows_who_it_is_waiting_for() -> None:
    body = _fn_body("handleWagerProgress")
    written = re.search(r"els\.questionText\.textContent =(.*?);", body, re.S).group(1)
    assert "progress" in written, "the lock-in count was dropped from the board"
    assert "title" in written
