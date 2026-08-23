"""The admin token leaves the query string for good (issue #608).

Four call sites put a replayable full-control credential into `?token=`, which
lands in aiohttp's access log and in every reverse proxy in front of HA. The
analytics page also *read* it from its own address bar, so it sat in browser
history too. Since #530 the token is long-lived, so anyone who can read those
logs has admin.

The rule already existed. `admin.js` states it three lines above one of the four
violations: "Header rather than ?token= — #359 moved the token out of URLs, and
new call sites should not put it back in." It was broken four times anyway,
which is why this fix replaces the comment with a function: `_adminFetch` is now
the single way the admin page attaches its credential.

Scope is client-only on purpose. `views.py` still accepts `?token=` so a phone
holding a cached older `admin.js` keeps working; removing that fallback is a
separate change for a later release, once no client sends it.

These assertions look for the absent CALL, never the absent WORD — the comments
here quote `?token=` to explain what was removed, and four separate assertions
today tripped over exactly that.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"

# Every file that talks to an admin-gated endpoint from the browser.
CLIENT_FILES = (
    _WWW / "js" / "admin.js",
    _WWW / "js" / "pack-submit.js",
    _WWW / "analytics.html",
    _WWW / "js" / "player.bundle.js",
)

# The shape that leaked: building a URL with the credential glued on.
TOKEN_IN_URL = re.compile(r"""['"]\?token=['"]|token=['"]\s*\+\s*encodeURIComponent""")


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def test_no_client_builds_a_token_query_string() -> None:
    """The core guard. Comments are stripped first, deliberately."""
    offenders = [
        path.name
        for path in CLIENT_FILES
        if path.exists()
        and TOKEN_IN_URL.search(_strip_comments(path.read_text("utf-8")))
    ]

    assert not offenders, (
        f"{offenders} still put the admin token in a URL — send the "
        "X-Quizify-Token header instead (#359, #608)"
    )


def test_the_analytics_page_no_longer_reads_the_token_from_its_url() -> None:
    """That read is why the credential reached browser history.

    Since #530 localStorage always has it, so the param bought nothing — and
    the link built in #627 carries no token, yet the page still authenticates.
    """
    source = _strip_comments((_WWW / "analytics.html").read_text("utf-8"))

    assert "URLSearchParams(window.location.search).get('token')" not in source
    assert "QuizifyUtils.readAdminToken()" in source


def test_the_admin_page_has_one_place_that_attaches_the_credential() -> None:
    """A comment asking for a header was broken four times; a function cannot be."""
    source = _strip_comments((_WWW / "js" / "admin.js").read_text("utf-8"))

    assert "function _adminFetch(url, opts)" in source
    assert "'X-Quizify-Token'" in source
    for endpoint in ("tts-entities", "house-entities"):
        call = f"_adminFetch('/api/quizify/{endpoint}')"
        assert call in source, f"{endpoint} does not route through _adminFetch"


def test_the_preset_helper_routes_through_it_too() -> None:
    """It had the header already; leaving it separate would keep two copies of
    the same three lines and invite a third."""
    source = _strip_comments((_WWW / "js" / "admin.js").read_text("utf-8"))
    body = source.split("function _presetFetch(opts)", 1)[1].split("\n    }", 1)[0]

    assert "_adminFetch(" in body


def test_the_server_still_accepts_the_query_param() -> None:
    """Scope is client-only on purpose.

    A phone holding a cached older `admin.js` still sends `?token=`. Dropping
    the server fallback in the same change would answer it 401 on the entity
    lists — the #530 lockout shape, reintroduced by a security fix.
    """
    views = (_REPO_ROOT / "custom_components" / "quizify" / "server" / "views.py")
    source = views.read_text("utf-8")

    assert 'request.query.get("token")' in source
    assert 'request.headers.get("X-Quizify-Token")' in source
