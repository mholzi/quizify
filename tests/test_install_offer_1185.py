"""The install offer on the host's end screen, and the iPad (#1185, P11).

The offer used to be a 📲 in the header with no word and no occasion, and
iPadOS Safari — which introduces itself as a Mac — never got it at all.
"""

from __future__ import annotations

import re
from pathlib import Path

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"
_JS = (_WWW / "js" / "admin.js").read_text("utf-8")
_HTML = (_WWW / "admin.html").read_text("utf-8")


def test_ipad_is_detected_by_its_touch_points() -> None:
    assert re.search(r"/Macintosh/\.test\(window\.navigator\.userAgent\)\s*&&\s*window\.navigator\.maxTouchPoints > 1", _JS)


def test_the_card_sits_on_the_end_screen_with_add_and_later() -> None:
    finale = _HTML[_HTML.index('id="admin-finale-view"') :]
    finale = finale[: finale.index('id="admin-lightning-view"')]
    assert 'id="admin-install-card"' in finale and "hidden" in finale.split('id="admin-install-card"')[1][:20]
    assert 'id="admin-install-add"' in finale and 'id="admin-install-later"' in finale


def test_the_offer_rule_once_then_after_game_three_then_never() -> None:
    body = _JS[_JS.index("function _updateInstallOffer(gameId) {") :]
    body = body[: body.index("\n    }\n")]
    assert "rec.state === 'new' && rec.games >= 1" in body
    assert "rec.state === 'later' && rec.games >= INSTALL_OFFER_AGAIN_AT" in body
    assert "INSTALL_OFFER_AGAIN_AT = 3" in _JS
    answer = _JS[_JS.index("function _answerInstallOffer(add) {") :]
    answer = answer[: answer.index("\n    }\n")]
    assert "(add || rec.state === 'later') ? 'done' : 'later'" in answer


def test_the_header_button_stays() -> None:
    assert 'id="pwa-install-btn"' in _HTML
