"""A picture question whose picture fails says so (#1182, L10).

``img.onerror`` used to hide the frame on the phone (``media.hidden = true``)
and collapse the TV to the text-only layout. "Which US president is shown
here?" was left without a picture on both screens: unanswerable, and it still
counted. On a slow network the frame was an empty box while the clock ran.

Now the frame is a skeleton until ``load`` and shows a calm placeholder line
on ``error``; the question keeps counting (decision 2a). This runs the real
``renderQuestionImageBanner`` (phone) and ``renderQuestionImage`` (TV) under
node with the shared DOM stub.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _function(path: Path, signature: str) -> str:
    src = path.read_text("utf-8")
    start = src.index(signature)
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced {signature}")


_SCRIPT = r"""
require(STUB);
QZ.els(['question-media', 'question-image', 'question-image-missing']);
var media = document.getElementById('question-media');
var img = document.getElementById('question-image');
var missing = document.getElementById('question-image-missing');
missing.hidden = true;
window.QuizifyUtils = { safeImageUrl: function (u) { return u; } };
window.QuizifyI18n = { t: function (k) { return k; } };
function clearRevealBlur() {}
var _reveal = { arm: function () {} };
var reveal = _reveal;
var els = { questionImage: img, questionMedia: media, dashboardLeft: null };
function setImageLayout(on) { media.hidden = !on; }
FN
function snap() {
    return { hidden: !!media.hidden, loading: media.classList.contains('is-loading'),
             missingClass: media.classList.contains('is-missing'),
             placeholder: !missing.hidden };
}
var out = {};
CALL('/quizify/static/img/a.jpg', 'none', 20);
out.loading = snap();
img.onerror();
out.failed = snap();
CALL('/quizify/static/img/b.jpg', 'none', 20);
out.nextRound = snap();
img.onload();
out.loaded = snap();
CALL('', 'none', 20);
out.noImage = snap();
console.log(JSON.stringify(out));
"""


def _run(path: Path, signature: str, name: str) -> dict:
    script = (
        _SCRIPT.replace("STUB", json.dumps(str(_STUB)))
        .replace("FN", _function(path, signature))
        .replace("CALL", name)
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


SURFACES = {
    "phone": (
        _WWW / "js" / "player-game.js",
        "function renderQuestionImageBanner(",
        "renderQuestionImageBanner",
    ),
    "tv": (
        _WWW / "js" / "dashboard.js",
        "function renderQuestionImage(",
        "renderQuestionImage",
    ),
}


@pytest.fixture(scope="module", params=sorted(SURFACES))
def result(request) -> dict:
    return _run(*SURFACES[request.param])


def test_a_loading_picture_is_a_skeleton(result: dict) -> None:
    assert result["loading"] == {
        "hidden": False,
        "loading": True,
        "missingClass": False,
        "placeholder": False,
    }


def test_a_failed_picture_keeps_the_frame_and_says_so(result: dict) -> None:
    assert result["failed"] == {
        "hidden": False,
        "loading": False,
        "missingClass": True,
        "placeholder": True,
    }


def test_the_next_round_starts_clean(result: dict) -> None:
    assert result["nextRound"]["placeholder"] is False
    assert result["nextRound"]["missingClass"] is False
    assert result["loaded"]["loading"] is False


def test_no_picture_hides_the_frame(result: dict) -> None:
    assert result["noImage"]["hidden"] is True
    assert result["noImage"]["placeholder"] is False


def test_both_placeholders_ship_with_their_key() -> None:
    phone = (_WWW / "player.html").read_text("utf-8")
    tv = (_WWW / "dashboard.html").read_text("utf-8")
    assert 'data-i18n="states.imageMissingPhone"' in phone
    assert 'data-i18n="states.imageMissingTv"' in tv
