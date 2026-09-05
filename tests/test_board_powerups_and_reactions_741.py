"""#741 — the television (and the host page) never showed power-ups or reactions.

``powerup_applied`` and ``reaction`` are both **full** broadcasts
(``server/websocket.py``: ``self._conn.broadcast(...)``), so the television
socket has always received them. ``dashboard.html``'s message switch had a case
for neither, and no ``default:`` branch — so the frames arrived and were thrown
away in silence. ``admin.js`` was missing exactly the same three cases.

What the room saw: Anna freezes Ben, Ben's phone ices over, the big screen shows
nothing. A steal moves points on the leaderboard with no explanation. During the
reveal every phone rains emoji while the television stays static.

What is pinned here, beyond "the cases exist":

* **Only the power-ups that hit someone else.** ``FREEZE`` and ``STEAL`` go on
  the screen; ``JOKER``, ``DOUBLE_POINTS`` and ``TIME_BOOST`` change nothing but
  the user's own turn. Showing those would mean something is on the screen
  almost constantly, and then none of it means anything.
* **A sentence, not a symbol.** An icon on a leaderboard row assumes the viewer
  knows what the icon means, and the room does not.
* **Whole sentences in the bundles.** German and Spanish order the player name,
  the point count and the target differently from English, so the translated
  unit has to be the entire sentence with placeholders — fragments concatenated
  in English order produce a German television speaking English grammar.
* **Reactions only over the reveal.** Over a live question the movement would
  compete with reading the answers.
* **``reaction_bonus`` renders its own leaderboard**, so the +1 does not lag its
  own animation until the next ``game_state`` frame.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_DASHBOARD = _WWW / "dashboard.html"
_ADMIN_JS = _WWW / "js" / "admin.js"
_ADMIN_HTML = _WWW / "admin.html"
_ADMIN_CSS = _WWW / "css" / "src" / "06-admin.css"
_I18N = _WWW / "i18n"

LANGUAGES = ("de", "en", "es")

# The two surfaces that show the room what happened: the television and the
# host's page. Both were missing the same cases, so both are checked the same.
BOARDS = {
    "dashboard.html": _DASHBOARD,
    "admin.js": _ADMIN_JS,
}


def _bundle(code: str) -> dict:
    return json.loads((_I18N / f"{code}.json").read_text("utf-8"))


@pytest.mark.parametrize("name", sorted(BOARDS))
@pytest.mark.parametrize(
    "message_type", ["powerup_applied", "reaction", "reaction_bonus"]
)
def test_the_board_has_a_case_for_the_broadcast(name: str, message_type: str) -> None:
    """The frames arrived; there was nowhere to put them."""
    source = BOARDS[name].read_text(encoding="utf-8")
    assert f"case '{message_type}':" in source, (
        f"{name} still drops the {message_type} broadcast on the floor"
    )


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_only_the_power_ups_that_hit_someone_else_reach_the_screen(name: str) -> None:
    """A strip that is up constantly says nothing.

    JOKER / DOUBLE_POINTS / TIME_BOOST affect only the player's own turn, so
    they are deliberately absent from the table that drives the strip.
    """
    source = BOARDS[name].read_text(encoding="utf-8")
    match = re.search(r"var BOARD_POWERUPS = \{(.*?)\n    \};", source, re.S)
    if match is None:
        match = re.search(r"var BOARD_POWERUPS = \{(.*?)\n        \};", source, re.S)
    assert match, f"{name} has no BOARD_POWERUPS table"
    table = match.group(1)
    assert "freeze:" in table, f"{name} does not put a freeze on the screen"
    assert "steal:" in table, f"{name} does not put a steal on the screen"
    for own_turn in ("joker", "double_points", "time_boost"):
        assert f"{own_turn}:" not in table, (
            f"{name} shows {own_turn}, which only ever changed the user's own "
            "turn — the strip would be up constantly and mean nothing"
        )


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_the_strip_shows_a_sentence_and_not_only_a_symbol(name: str) -> None:
    """The whole point of the choice: the room does not read icons."""
    source = BOARDS[name].read_text(encoding="utf-8")
    assert "function powerUpSentenceHtml(" in source, (
        f"{name} builds no sentence for the strip"
    )
    assert "dashboard.powerupFreeze" in source and "dashboard.powerupSteal" in source, (
        f"{name} does not reach for the translated sentences"
    )


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_reactions_are_reveal_only(name: str) -> None:
    """Over a live question the movement competes with reading the answers."""
    source = BOARDS[name].read_text(encoding="utf-8")
    match = re.search(
        r"function (?:showDashboardReaction|showAdminReaction)\((.*?)\n    \}",
        source,
        re.S,
    )
    if match is None:
        match = re.search(
            r"function (?:showDashboardReaction|showAdminReaction)\((.*?)\n        \}",
            source,
            re.S,
        )
    assert match, f"{name} has no floating-reaction renderer"
    body = match.group(1)
    assert "ANSWER_REVEAL" in body, (
        f"{name} rains emoji over a live question — the reveal gate is missing"
    )


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_the_steal_also_moves_the_leaderboard_rows(name: str) -> None:
    """The strip says what happened; the rows say what it cost."""
    source = BOARDS[name].read_text(encoding="utf-8")
    assert "function scoreDeltaHtml(" in source, f"{name} renders no point delta"
    render = source[source.index("function renderLeaderboard(") :][:1600]
    assert "scoreDeltaHtml(p.name)" in render, (
        f"{name}'s leaderboard rows never show the delta a steal produced"
    )


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_the_reaction_bonus_repaints_the_leaderboard_at_once(name: str) -> None:
    """Otherwise the +1 lags its own animation until the next game_state."""
    source = BOARDS[name].read_text(encoding="utf-8")
    match = re.search(r"function handleReactionBonus\(msg\) \{(.*?)\n(    |        )\}", source, re.S)
    assert match, f"{name} has no reaction_bonus handler"
    assert "msg.leaderboard" in match.group(1), (
        f"{name} ignores the leaderboard the bonus frame already carries"
    )


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_two_at_once_stack_instead_of_replacing_each_other(name: str) -> None:
    """A second strip appends under the first; the oldest still leaves first."""
    source = BOARDS[name].read_text(encoding="utf-8")
    match = re.search(r"function showPowerUpBanner\(spec, vars\) \{(.*?)\n(    |        )\}\n", source, re.S)
    assert match, f"{name} has no strip renderer"
    body = match.group(1)
    assert "appendChild" in body, (
        f"{name} does not append — a second strip would replace the first"
    )
    assert "powerupBanners.innerHTML" not in body, (
        f"{name} clears the stack before appending, so only one strip is ever up"
    )


def test_both_boards_declare_the_strip_and_the_reaction_layer() -> None:
    """The JS looks these up by id; without the markup it is a silent no-op."""
    for page in (_DASHBOARD, _ADMIN_HTML):
        html = page.read_text(encoding="utf-8")
        assert 'id="powerup-banners"' in html, f"{page.name} has no strip container"
        assert 'id="reaction-layer"' in html, f"{page.name} has no reaction layer"


@pytest.mark.parametrize(
    "css,selector",
    [
        (_DASHBOARD, ".dashboard-powerup-banners"),
        (_ADMIN_CSS, ".powerup-banners"),
    ],
)
def test_the_stack_is_a_column(css: Path, selector: str) -> None:
    """Stacking is a layout property, not something the JS can assert."""
    text = css.read_text(encoding="utf-8")
    start = text.index(selector + " {")
    body = text[start : text.index("}", start)]
    assert "flex-direction: column" in body, (
        f"{selector} is not a column — two strips at once would sit side by side"
    )


@pytest.mark.parametrize("code", LANGUAGES)
def test_every_language_carries_both_sentences(code: str) -> None:
    """A German television must not show an English sentence."""
    dashboard = _bundle(code)["dashboard"]
    for key in ("powerupFreeze", "powerupSteal"):
        assert key in dashboard, f"{code}.json is missing dashboard.{key}"
        assert dashboard[key].strip(), f"{code}.json has an empty dashboard.{key}"


@pytest.mark.parametrize("code", LANGUAGES)
def test_the_sentences_are_whole_sentences_with_placeholders(code: str) -> None:
    """Not fragments concatenated in English order.

    German and Spanish put the name, the count and the target in a different
    order, and only a full-sentence template can express that.
    """
    dashboard = _bundle(code)["dashboard"]
    freeze = set(re.findall(r"\{(\w+)\}", dashboard["powerupFreeze"]))
    steal = set(re.findall(r"\{(\w+)\}", dashboard["powerupSteal"]))
    assert freeze == {"source", "target"}, (
        f"{code}.json dashboard.powerupFreeze interpolates {freeze}"
    )
    assert steal == {"source", "target", "points"}, (
        f"{code}.json dashboard.powerupSteal interpolates {steal}"
    )


@pytest.mark.parametrize("code", ("de", "es"))
def test_the_translations_are_actually_translated(code: str) -> None:
    """A copied English string passes a key-parity check and still lies."""
    english = _bundle("en")["dashboard"]
    other = _bundle(code)["dashboard"]
    for key in ("powerupFreeze", "powerupSteal"):
        assert other[key] != english[key], (
            f"{code}.json dashboard.{key} is still the English sentence"
        )
