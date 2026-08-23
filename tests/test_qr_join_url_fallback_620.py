"""The join address is visible without scanning (issue #620).

Both lobbies showed the QR code and a "scan to join" line. The address itself
appeared as text **only** in the branch that runs when the QR library fails to
load — a case that has nothing to do with the failure this covers.

The failure this covers: a guest on cellular data, or on the guest network,
scans a perfectly rendered code, lands on a LAN address their phone cannot
reach, and gets a spinner. And that is the one failure the join page can never
explain, because the join page is what failed to load. ``errors.checkNetwork``
exists in all three bundles and renders only on the post-load connection-lost
view — where it is no longer needed.

So the address and a "same Wi-Fi" line are now written on every render, not on
the library-missing path.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"

LANGUAGES = ("de", "en", "es")
NEW_KEYS = ("orTypeUrl", "sameWifiHint")


def test_both_keys_ship_in_every_language() -> None:
    """A hint that renders its own key would be worse than no hint."""
    for code in LANGUAGES:
        lobby = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))["lobby"]
        for key in NEW_KEYS:
            assert lobby.get(key), f"{code}.lobby.{key} missing or empty"


def test_the_tv_lobby_shows_the_address_and_the_hint() -> None:
    source = (_WWW / "dashboard.html").read_text("utf-8")

    assert 'id="lobby-join-url"' in source
    assert 'data-i18n="lobby.sameWifiHint"' in source
    assert 'data-i18n="lobby.orTypeUrl"' in source


def test_the_tv_writes_the_address_outside_the_library_check() -> None:
    """The point of the whole change.

    A fallback that only runs when ``QRCode`` is undefined does not help a guest
    whose phone cannot reach a perfectly rendered code. This asserts the write
    happens *before* that branch is entered.
    """
    source = (_WWW / "dashboard.html").read_text("utf-8")
    body = source.split("function renderLobbyQr()", 1)[1].split("function ", 1)[0]

    write_at = body.index("lobbyJoinUrlEl")
    branch_at = body.index("typeof QRCode !== 'undefined'")
    assert write_at < branch_at, (
        "the address must be written on every render, not only when the QR "
        "library is missing"
    )


def test_the_admin_lobby_got_the_same_treatment() -> None:
    """The issue named both screens; shipping one covers half the cases.

    The host reads this line aloud when a guest's phone cannot reach the code.
    """
    html = (_WWW / "admin.html").read_text("utf-8")
    js = (_WWW / "js" / "admin.js").read_text("utf-8")

    assert 'id="admin-join-url"' in html
    assert 'data-i18n="lobby.sameWifiHint"' in html

    body = js.split("function generateQR(url)", 1)[1].split("\n    }", 1)[0]
    assert body.index("admin-join-url") < body.index("typeof QRCode !== 'undefined'")


def test_the_admin_qr_column_keeps_its_width_cap() -> None:
    """Wrapping the QR in a column moved the grid child.

    The 220px cap lived on the code itself; without moving it, the wrapper takes
    the whole grid track and the QR dominates the card on a phone — the exact
    thing that cap was added to prevent.
    """
    css = (_WWW / "css" / "styles.css").read_text("utf-8")

    assert ".lobby-e-qr-col" in css
    column_block = css.split(".lobby-e-qr-col", 1)[1].split("}", 1)[0]
    assert "max-width: 220px" in column_block
