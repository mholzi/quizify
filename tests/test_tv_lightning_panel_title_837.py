"""#837 — the Lightning Round panel called itself the LEADERBOARD.

Seen in two separate games during the v1.16.0-RC1 live test. The moment the
Lightning Round starts, the panel in the top right of the television swaps to
``LightningRound.leaderboard()`` — a lightning-ONLY running score that starts
at zero for everyone — while keeping the position, the card and the heading the
room has been reading all game. Cleo was leading on 23 and the board showed

    LEADERBOARD
    1  Anna   0
    2  Ben    0
    3  Cleo   0

the leader last, on nothing, in roster order. The same happened in the team
game. The scores are intact underneath and come back after the recap, so this
is presentation only — and that is exactly why it is a lie rather than a
glitch: for the ~90s the round lasts the room is told the standings are
something they are not.

Of the two fixes the issue offers, this is the second: say what the panel is
showing. The running lightning score is worth watching — it is the thing the
round is about — and the recap panel one view further on already names itself
"Lightning Round Results". This one now names itself too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
DASHBOARD = WWW / "dashboard.html"
I18N = WWW / "i18n"

KEY = "lightning.liveScoreTitle"


def _view(view_id: str) -> str:
    """One top-level ``.dashboard-view`` block, up to the next one."""
    html = DASHBOARD.read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r'<div id="[a-z-]+-view"', html)]
    start = html.index(f'<div id="{view_id}"')
    later = [s for s in starts if s > start]
    return html[start : later[0]] if later else html[start:]


def _lookup(bundle: dict, dotted: str):  # noqa: ANN202
    node = bundle
    for part in dotted.split("."):
        node = node[part]
    return node


def test_the_live_panel_no_longer_calls_itself_the_leaderboard() -> None:
    view = _view("lightning-view")
    title = re.search(
        r'class="dashboard-leaderboard-title"\s+data-i18n="([^"]+)"', view
    )
    assert title is not None, "the lightning panel lost its heading"
    assert title.group(1) != "dashboard.leaderboard", (
        "same position, same card, same heading as the game leaderboard — "
        "and a different set of numbers"
    )
    assert title.group(1) == KEY


def test_the_game_board_keeps_the_heading_it_had() -> None:
    """Only the lightning panel is retitled; the question view is untouched."""
    view = _view("question-view")
    assert 'data-i18n="dashboard.leaderboard"' in view


def test_the_recap_panel_is_left_alone() -> None:
    view = _view("lightning-recap-view")
    assert 'data-i18n="lightning.recapTitle"' in view


@pytest.mark.parametrize("lang", ["de", "en", "es"])
def test_the_new_key_exists_in_every_bundle(lang: str) -> None:
    bundle = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))
    value = _lookup(bundle, KEY)
    assert isinstance(value, str) and value.strip()
    # It has to READ as a lightning label, not as a second leaderboard.
    assert value != _lookup(bundle, "dashboard.leaderboard")
    assert value != _lookup(bundle, "lightning.recapTitle")


def test_the_fallback_text_is_not_the_word_leaderboard() -> None:
    """The inline text is what a television shows before i18n has loaded."""
    view = _view("lightning-view")
    match = re.search(
        r'class="dashboard-leaderboard-title"[^>]*>([^<]+)<', view
    )
    assert match is not None
    assert "leaderboard" not in match.group(1).lower()
