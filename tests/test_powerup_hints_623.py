"""Every power-up says what it does, in the player's language (issue #623).

The button label came from a hardcoded table in `player-game.js` — English
words plus an emoji, shown to every player in every language — while
`powerups.joker` and its four siblings sat translated in all three bundles,
unused. And nothing anywhere said what a power-up actually *does*: freeze and
steal at least got a target picker with a hint; joker, double points and time
boost fired with no explanation at all, mid-round, with the clock running.

**The wording came from the game code, not from the issue.** That matters most
for `steal`: `state.py` takes half of the target's ROUND score, not half their
total. "Half their points" is the plausible phrasing and it is wrong — and a
wrong explanation is worse than none, because a player acts on it.

Design chosen in a shotgun round against three alternatives (explanation inside
the button, a card above the answers, a hint beside the button). One muted line
under the button won because it is the only shape that holds at every text
length; the five explanations run from "+5 more seconds" to a full sentence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"

LANGUAGES = ("de", "en", "es")
TYPES = ("joker", "double_points", "freeze", "time_boost", "steal")
HINT_KEYS = (
    "jokerHint",
    "doublePointsHint",
    "freezeHint",
    "timeBoostHint",
    "stealHint2",
)


def _powerups(code: str) -> dict:
    return json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))["powerups"]


def test_every_power_up_has_a_hint_in_every_language() -> None:
    """Five types, three languages. A gap here shows a raw key mid-game."""
    for code in LANGUAGES:
        bundle = _powerups(code)
        for key in HINT_KEYS:
            assert bundle.get(key), f"{code}.powerups.{key} missing or empty"


def test_the_steal_hint_says_round_score_not_total() -> None:
    """The one explanation that is easy to get wrong.

    `state.py` subtracts `max(0, round_score) // 2` from the target's round
    score. A hint promising half their *total* would make players target the
    leader, which is not what the mechanic rewards.
    """
    expectations = {"de": "dieser Runde", "en": "this round", "es": "esta ronda"}
    for code, phrase in expectations.items():
        hint = _powerups(code)["stealHint2"]
        assert phrase in hint, (
            f"{code} steal hint does not scope to the round: {hint!r}"
        )


def test_the_label_table_no_longer_carries_english_words() -> None:
    """It kept emoji; the words now come from the bundles."""
    source = (_JS / "player-game.js").read_text("utf-8")
    table = source.split("POWERUP_ICONS = {", 1)[1].split("};", 1)[0]

    for word in ("Joker", "Double", "Freeze", "Steal", "+5s"):
        assert word not in table, f"POWERUP_ICONS still hardcodes {word!r}"


def test_the_renderer_uses_the_translated_name_and_the_hint() -> None:
    source = (_JS / "player-game.js").read_text("utf-8")
    body = source.split("function renderPowerUp(", 1)[1].split("\n    }", 1)[0]

    assert "t('powerups.' + powerupType)" in body
    assert "POWERUP_HINTS[powerupType]" in body


def test_every_type_is_mapped_to_a_hint_key() -> None:
    """A type without a mapping would render a button with no line under it."""
    source = (_JS / "player-game.js").read_text("utf-8")
    mapping = source.split("POWERUP_HINTS = {", 1)[1].split("};", 1)[0]

    for powerup_type in TYPES:
        assert f"{powerup_type}:" in mapping, f"{powerup_type} has no hint key"


def test_the_line_is_hidden_when_no_power_up_is_held() -> None:
    """Otherwise last round's explanation sits under a hidden button."""
    source = (_JS / "player-game.js").read_text("utf-8")
    body = source.split("function renderPowerUp(", 1)[1].split("\n    }", 1)[0]
    else_branch = body.split("} else {", 1)[1]

    assert "hintEl.classList.add('hidden')" in else_branch


def test_the_markup_and_style_exist() -> None:
    """The line is not aria-hidden: a screen reader user has even less chance
    of guessing what a power-up does than a sighted one."""
    html = (_WWW / "player.html").read_text("utf-8")
    css = (_WWW / "css" / "styles.css").read_text("utf-8")

    assert 'id="powerup-hint"' in html
    assert "aria-hidden" not in html.split('id="powerup-hint"', 1)[1][:200]
    assert ".powerup-explainer" in css


def test_the_container_stacks_without_margin_collapse() -> None:
    """Column + gap, not a margin on the line.

    A margin here collapses against the container padding, so the spacing would
    differ depending on whether the line is shown — the layout would twitch as
    a power-up is granted.
    """
    css = (_WWW / "css" / "styles.css").read_text("utf-8")
    block = css.split(".powerup-container {", 1)[1].split("}", 1)[0]
    declarations = re.sub(r"/\*.*?\*/", "", block, flags=re.S)

    assert "flex-direction: column" in declarations
    assert "gap:" in declarations


def test_it_reached_the_shipped_bundle() -> None:
    bundle = (_JS / "player.bundle.js").read_text("utf-8")

    assert "POWERUP_HINTS" in bundle
    assert "powerups.stealHint2" in bundle
