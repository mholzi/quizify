"""The television hides the mouse pointer after 3 s still (#1183).

``10-tv.css`` never set ``cursor``, so a host who opened the board with a
mouse had the pointer standing in the middle of the picture all evening.
"""

from __future__ import annotations

from pathlib import Path

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"


def test_the_board_hides_an_idle_pointer() -> None:
    js = (_WWW / "js" / "dashboard.js").read_text("utf-8")
    assert "var CURSOR_IDLE_MS = 3000;" in js
    block = js[js.index("function _cursorAwake()") :][:500]
    assert "classList.remove('cursor-idle')" in block
    assert "classList.add('cursor-idle')" in block
    assert "'mousemove'" in js[js.index("function _cursorAwake()") :][:800]


def test_the_tv_sheet_carries_the_rule() -> None:
    for path in (_WWW / "css" / "src" / "10-tv.css", _WWW / "css" / "tv.css"):
        css = path.read_text("utf-8")
        assert "body.surface-tv.cursor-idle" in css
        assert "cursor: none" in css
