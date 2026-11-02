"""Setup and phone controls: 44 px targets, 8 px gaps, a press state (#1183).

UX review 7 measured 93 live controls: 21 with a hit area under 44 px, 25 with
less than 8 px to the next one, 10 with their own ``:active`` state. The
setup's switch rows were as tall as their text (22-24 px, 2 px apart), the
selects 34 px, the guide link 17 px, chips 6 px apart, the team field 39 px.
These pin the CSS and markup the fix rests on; the live measurement is the
non-blocking Playwright job (``.github/workflows/tap-targets.yml``).
"""

from __future__ import annotations

import re
from pathlib import Path

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"
_SRC = _WWW / "css" / "src"


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return m.group(1)


def test_switch_rows_selects_and_the_team_field_are_44_px() -> None:
    shared = (_SRC / "02-shared.css").read_text("utf-8")
    player = (_SRC / "07-player.css").read_text("utf-8")
    lobby = (_SRC / "04-lobby.css").read_text("utf-8")
    assert "min-height: 44px" in _rule(shared, ".toggle-compact")
    assert "min-height: 44px" in _rule(player, ".setup-select")
    assert "min-height: 44px" in _rule(player, ".setup-house-autohint a")
    assert "min-height: 44px" in _rule(lobby, ".qz-team-nameInput")
    assert "min-height: 44px" in _rule(shared, ".chip-group--flags .chip")


def test_neighbours_keep_8_px() -> None:
    shared = (_SRC / "02-shared.css").read_text("utf-8")
    player = (_SRC / "07-player.css").read_text("utf-8")
    lobby = (_SRC / "04-lobby.css").read_text("utf-8")
    assert "gap: var(--space-sm)" in _rule(shared, ".chip-group")
    assert "gap: var(--space-sm)" in _rule(player, ".setup-house-presets")
    assert "gap: var(--space-sm)" in _rule(lobby, ".qz-team-name")


def test_every_control_has_a_press_state() -> None:
    shared = (_SRC / "02-shared.css").read_text("utf-8")
    block = shared[shared.index("button:not(:disabled):active,") :][:200]
    assert ".toggle-compact:active" in block and "scale: 0.98" in block


def test_the_keyboard_says_what_enter_does() -> None:
    player = (_WWW / "player.html").read_text("utf-8")
    admin = (_WWW / "admin.html").read_text("utf-8")

    def tag(html: str, el_id: str) -> str:
        at = html.index(f'id="{el_id}"')
        return html[html.rindex("<", 0, at) : html.index(">", at)]

    assert 'enterkeyhint="go"' in tag(player, "name-input")
    assert 'enterkeyhint="done"' in tag(player, "team-name-input")
    assert 'enterkeyhint="go"' in tag(admin, "admin-name-input")
    assert 'enterkeyhint="done"' in tag(admin, "save-preset-input")
