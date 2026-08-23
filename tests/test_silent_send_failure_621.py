"""Commands that cannot be delivered say so (issue #621).

``send()`` on both the admin page and the phone was the same three lines: if the
socket is OPEN, send — and nothing otherwise. No toast, no console line, no
return value. During an HA restart or a Wi-Fi blip the host tapped "Next
Question", the button disabled itself for 1.5s as if the command had landed, and
re-enabled. The command never left the browser.

This is the other half of #586. #599 made *refused* commands visible: the server
answers with an error code and the client raises a toast. An *undelivered*
command looks identical from the host's chair, and stayed dark.

On the phone it is worse than on the admin page: a guest taps an answer while
the socket is down, the tile lights up, and the round closes without them.

Everything needed already existed — ``showErrorToast`` / ``showToast``, the
connection indicator, and ``connection.reconnecting`` in all three bundles. What
was missing was the ``else``.

The return value matters as much as the toast: callers were disabling their
button afterwards, which is a success animation for something that did not
happen.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"


def _function_body(source: str, signature: str) -> str:
    """Brace-matched body — slicing to the next dedent would stop at the first
    nested block and make these assertions meaningless."""
    start = source.index(signature)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def test_the_string_exists_in_every_language() -> None:
    for code in ("de", "en", "es"):
        bundle = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))
        assert bundle["connection"]["reconnecting"]


def test_the_admin_send_reports_failure_instead_of_swallowing_it() -> None:
    body = _function_body(
        (_JS / "admin.js").read_text("utf-8"), "function send(type, payload)"
    )

    assert "return true" in body
    assert "return false" in body
    assert "connection.reconnecting" in body


def test_the_phone_send_reports_failure_too() -> None:
    """Not in the issue, same defect, worse consequence — a lost answer."""
    body = _function_body(
        (_JS / "player-core.js").read_text("utf-8"), "function send(type, payload)"
    )

    assert "return true" in body
    assert "return false" in body
    assert "connection.reconnecting" in body


def test_the_button_is_not_held_when_nothing_was_sent() -> None:
    """The half of the fix a toast alone would miss.

    Holding the button for 1.5s after an undelivered send tells the host
    "received, working on it" — precisely the thing that did not happen.
    """
    body = _function_body(
        (_JS / "admin.js").read_text("utf-8"), "function _debouncedSend(btn, msgType)"
    )

    assert "if (!send(" in body
    guard = body.index("if (!send(")
    timeout = body.index("setTimeout")
    assert guard < timeout, "the early return must come before the 1.5s hold"


def test_the_lobby_start_button_no_longer_returns_in_silence() -> None:
    body = _function_body(
        (_JS / "player-lobby.js").read_text("utf-8"),
        "function setupAdminControls(sendFn)",
    )

    assert "connection.reconnecting" in body


def test_all_of_it_reached_the_shipped_bundle() -> None:
    """player.html loads the bundle; the module edits alone ship nothing."""
    bundle = (_JS / "player.bundle.js").read_text("utf-8")

    assert bundle.count("connection.reconnecting") >= 2
