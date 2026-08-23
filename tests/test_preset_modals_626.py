"""Saving and deleting a preset stays inside the product (issue #626).

Saving used `window.prompt`, its failures `window.alert`, deleting
`window.confirm`. The host builds a game template in a carefully styled screen
and then gets the grey operating-system box for the name.

The argument is not looks. It is a decision this codebase already made and only
half applied: #480 dropped `window.confirm` for player kicks in favour of a
themed modal — same file, a few hundred lines further down. And some embedded
WebViews render those dialogs poorly or suppress them outright, in which case
the host cannot save a preset and never learns that anything tried to happen.
The Android Companion has produced two such surprises already (#348, #377).

The error path is the state that matters most, and the issue did not mention
it: the server's refusal now lands under the input instead of in an alert, so
the typed name stays on screen to be corrected rather than retyped from memory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"

LANGUAGES = ("de", "en", "es")
NEW_KEYS = (
    "savePresetTitle",
    "savePresetBody",
    "savePresetPlaceholder",
    "savePresetBtn",
    "deletePresetTitle",
    "deletePresetBody",
    "deletePresetBtn",
)


def _without_comments(source: str) -> str:
    """Drop JS comments before asserting on code.

    Third time this trap has been hit today (#625, #622, here): a fix explains
    itself in a comment that quotes the very thing it removed, and a raw text
    search then reads the explanation as the code. Assertions look at
    declarations, never at prose.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _function_body(source: str, signature: str) -> str:
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


def test_every_string_ships_in_every_language() -> None:
    for code in LANGUAGES:
        setup = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))["setup"]
        for key in NEW_KEYS:
            assert setup.get(key), f"{code}.setup.{key} missing or empty"


def test_the_delete_body_names_the_preset() -> None:
    """"Delete preset?" alone makes the host guess which one they tapped."""
    for code in LANGUAGES:
        setup = json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))["setup"]
        assert "{name}" in setup["deletePresetBody"], f"{code} has no name slot"


def test_both_modals_exist_in_the_markup() -> None:
    html = (_WWW / "admin.html").read_text("utf-8")

    for modal_id in ("save-preset-modal", "delete-preset-modal"):
        assert f'id="{modal_id}"' in html
    assert 'id="save-preset-error"' in html
    delete_block = html.split('id="delete-preset-modal"', 1)[1][:900]
    assert 'class="btn btn-danger"' in delete_block


def test_saving_opens_the_modal_rather_than_prompting() -> None:
    body = _without_comments(
        _function_body(_ADMIN_JS.read_text("utf-8"), "function _saveCurrentPreset(")
    )

    assert "openConfirmModal('save-preset-modal'" in body
    prompt_at = body.index("window.prompt")
    guard_at = body.index("if (!modal || !input)")
    assert guard_at < prompt_at, (
        "window.prompt may only survive inside the markup-missing fallback"
    )


def test_a_refusal_keeps_the_dialog_and_the_typed_name() -> None:
    """The whole point of the error state.

    An alert() throws the name away; this writes the server's sentence under
    the field and leaves the input untouched.
    """
    body = _without_comments(
        _function_body(_ADMIN_JS.read_text("utf-8"), "function _postPreset(")
    )

    assert "window.alert" not in body
    assert "errorEl.textContent = err.message" in body
    assert "errorEl.classList.remove('hidden')" in body


def test_deleting_uses_the_themed_danger_modal() -> None:
    body = _without_comments(
        _function_body(_ADMIN_JS.read_text("utf-8"), "function _deleteCustomPreset(")
    )

    assert "openConfirmModal('delete-preset-modal'" in body
    confirm_at = body.index("window.confirm")
    guard_at = body.index("if (!modal || !confirmBtn)")
    assert guard_at < confirm_at


def test_the_delete_handler_is_rewired_per_open() -> None:
    """A listener kept from a previous open would delete the wrong preset.

    The closure carries the preset that was tapped, so it has to be replaced
    each time rather than added once.
    """
    body = _function_body(_ADMIN_JS.read_text("utf-8"), "function _deleteCustomPreset(")

    assert "confirmBtn.onclick =" in body
    assert "confirmBtn.addEventListener" not in body


def test_both_entry_points_hand_over_a_real_element() -> None:
    """`openConfirmModal` stores its third argument to restore focus (#479).

    `addEventListener('click', fn)` hands the handler an Event, which has no
    `.focus()`. The restore would silently do nothing and a keyboard user would
    land back at the top of the page.
    """
    source = _ADMIN_JS.read_text("utf-8")

    assert "add.addEventListener('click', _saveCurrentPreset)" not in source
    assert "_saveCurrentPreset(add)" in source
    assert "_deleteCustomPreset(p, del)" in source


def test_the_markup_missing_path_still_saves() -> None:
    """Drift must not cost the host the feature outright.

    The precedent is the kick modal in #480, which keeps its confirm() fallback
    for the same reason.
    """
    save = _function_body(_ADMIN_JS.read_text("utf-8"), "function _saveCurrentPreset(")
    delete = _function_body(
        _ADMIN_JS.read_text("utf-8"), "function _deleteCustomPreset("
    )

    assert "window.prompt" in save
    assert "window.confirm" in delete
