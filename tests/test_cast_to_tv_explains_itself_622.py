"""The TV button stops promising something it does not do (issue #622).

"Cast to TV" set `href` to `/quizify/dashboard` and opened it in a new tab **on
the host's own phone**. No cast call, no device picker. A first-time host tapped
it, got the TV view at 390px in their hand, and no hint how it reaches the
television.

This is also the second real finding inside #586, the only external bug report
this project has. The reporter wrote "Cast to TV does not ask which TV and does
not work" — filed as an unexplained symptom next to the round-hang, and left
there for three days. It was never a bug: the button does exactly what it does,
it just says something else.

So both halves change. The label stops making the promise, and tapping explains
the actual mechanic while still offering the direct open for a host who *is* at
the TV device.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"

LANGUAGES = ("de", "en", "es")
MODAL_KEYS = ("castModalTitle", "castModalBody", "castOpenHere")


def _dashboard_bundle(code: str) -> dict:
    return json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))["dashboard"]


def test_the_label_no_longer_promises_casting() -> None:
    """Every language, because the promise was made in every language.

    German said "Auf den TV streamen" and Spanish "Enviar a la TV" — both claim
    a stream that does not exist, so fixing only the English string would leave
    two thirds of the users misled.
    """
    forbidden = {"cast to tv", "auf den tv streamen", "enviar a la tv"}
    for code in LANGUAGES:
        label = _dashboard_bundle(code)["castToTv"]
        assert label.strip().lower() not in forbidden, f"{code} still promises casting"


def test_the_modal_strings_ship_everywhere() -> None:
    for code in LANGUAGES:
        bundle = _dashboard_bundle(code)
        for key in MODAL_KEYS:
            assert bundle.get(key), f"{code}.dashboard.{key} missing or empty"


def test_the_markup_fallbacks_were_updated_too() -> None:
    """The inline text renders until i18n boots.

    Leaving "Cast to TV" in the HTML means the old promise flashes on every
    load — the one moment a first-time host is most likely to read it.

    Checks the rendered span, not the raw file: the fix's own comment quotes the
    old label to explain what it replaced, and a substring scan would call a
    correct page broken. (Second time today; see the "Starting..." assertion
    in the #625 tests.)
    """
    pattern = re.compile(r'data-i18n="dashboard\.castToTv">([^<]*)<')
    for page in ("admin.html", "player.html"):
        source = (_WWW / page).read_text("utf-8")
        rendered = pattern.findall(source)
        assert rendered, f"{page} no longer has the castToTv span"
        for text in rendered:
            assert "Cast to TV" not in text, f"{page} still ships the old label"


def test_tapping_opens_the_sheet_instead_of_a_tab() -> None:
    source = (_WWW / "js" / "admin.js").read_text("utf-8")

    assert "openCastModal(dashboardUrl" in source
    assert 'id="cast-tv-modal"' in (_WWW / "admin.html").read_text("utf-8")


def test_the_direct_open_survives_as_the_secondary_action() -> None:
    """A host sitting at the TV must not lose the one-tap route.

    And the #377 Android Companion workaround has to survive with it: that
    WebView swallows target="_blank", so the frame is navigated instead.
    """
    source = (_WWW / "js" / "admin.js").read_text("utf-8")
    body = source.split("function openCastModal(", 1)[1].split("\n    }", 1)[0]

    assert "isAndroidCompanion()" in body
    assert "window.open(dashboardUrl" in body


def test_a_missing_modal_falls_back_to_the_old_behaviour() -> None:
    """Markup drift must not leave the host with a dead button.

    The kick-player modal sets this precedent (#480); the same reasoning
    applies here, and the failure mode without it is worse than the bug.
    """
    source = (_WWW / "js" / "admin.js").read_text("utf-8")
    body = source.split("function openCastModal(", 1)[1].split("\n    }", 1)[0]

    assert "if (!modal)" in body


def test_the_address_wraps_without_stranding_characters() -> None:
    """`break-all` split mid-token and left a lone character on its own line.

    Found in the render, not in review: the host reads this aloud or types it
    on a TV remote.
    """
    css = (_WWW / "css" / "styles.css").read_text("utf-8")
    block = css.split(".cast-tv-url", 1)[1].split("}", 1)[0]
    # Strip comments before asserting: the rule's own comment names `break-all`
    # to say why it is gone, and scanning raw text would read that as the
    # declaration still being there.
    declarations = re.sub(r"/\*.*?\*/", "", block, flags=re.S)

    assert "overflow-wrap: anywhere" in declarations
    assert "break-all" not in declarations
