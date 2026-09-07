"""The five leftovers of #894 stay gone, and cannot come back quietly.

#894 was a deletion, so the guard has to be the shape a deletion needs: not a
test that goes green when a feature works, but one that goes red when the
deleted thing reappears. Each check below names the mechanism that let the code
survive, because that mechanism is what will grow the next one.

* **An exported function with no caller reads as API.** ``updateGameView``
  survived #619 by being on ``QuizifyPlayerGame``'s export object; two comments
  in ``player-core.js`` said out loud that nothing called it, and it still
  shipped to every phone for months. ``isGuessPending`` and
  ``renderPlayerCards`` were the same trick.
* **A ``case`` label costs nothing to keep and lies for free.** Both boards
  switched on ``round_evaluated`` and ``game_ended`` — the names
  ``game/state.py`` fires through ``BroadcastDispatcher``, which are consumed
  server-side and never reach a socket. Reading them on the client says the
  wire carries them.
  ``test_frame_surface_coverage_787`` already forbids a case for a frame
  ``protocol.py`` does not declare; what it cannot know is that these three
  names are *internal*, so that is what this file adds.
* **A second place that spells a storage key is a second owner.** #787 gave
  the session one owner; ``player-end.js`` still cleared it by writing the key
  names out again, one of which (``quizify_is_admin``) nothing has ever set.
* **A duplicate key in an object literal is silent in JS.** ``player-utils.js``
  listed ``hideReconnectingOverlay`` twice, each with its own comment, and no
  tool said a word.

The dead CSS from the same issue is not re-checked here:
``test_dead_frontend_weight_797`` already fails on a rule whose classes nothing
sets, and the ``.podium-stand`` / ``.away-badge`` rules came out under it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "custom_components" / "quizify"
_WWW = _PKG / "www"
_JS = _WWW / "js"

#: The generated bundle is a copy of the modules; counting it would report
#: every finding twice and let a stale build hide one.
_GENERATED = ("player.bundle.js", "common.bundle.js")


def _modules() -> list[Path]:
    return sorted(p for p in _JS.rglob("*.js") if p.name not in _GENERATED)


def _sources() -> list[Path]:
    """Every hand-written front-end file: the modules plus the pages."""
    return _modules() + sorted(_WWW.glob("*.html"))


# ---------------------------------------------------------------------------
# a) + c) functions nothing called
# ---------------------------------------------------------------------------

#: Name → the issue that removed it, for the failure message.
REMOVED_FUNCTIONS = {
    "updateGameView": "#894 (uncalled since #619; player-core.js said so twice)",
    "isGuessPending": "#894 (never read outside the module that defined it)",
    "renderPlayerCards": (
        "#894 (the only source of `class=\"player-card\"`, which no stylesheet "
        "styled and no page ever contained)"
    ),
}


@pytest.mark.parametrize("name", sorted(REMOVED_FUNCTIONS))
def test_a_function_deleted_for_having_no_caller_has_not_returned(
    name: str,
) -> None:
    """Restoring the definition is fine; restoring it *unused* is the bug.

    The check is deliberately about the definition and the export, not about
    mentions: a comment recording why something went is exactly the kind of
    history this repo keeps, and must not be what makes the guard red.
    """
    reason = REMOVED_FUNCTIONS[name]
    define = re.compile(r"\bfunction\s+" + name + r"\s*\(")
    export = re.compile(r"^\s*" + name + r"\s*:", re.M)
    offenders = [
        path.relative_to(_REPO).as_posix()
        for path in _sources()
        if define.search(path.read_text("utf-8"))
        or export.search(path.read_text("utf-8"))
    ]
    assert not offenders, (
        f"`{name}` is back in {offenders}. It was removed by {reason}. If it "
        "has a caller now, delete this entry and say where; if it does not, "
        "it is dead weight again."
    )


# ---------------------------------------------------------------------------
# b) internal state-event names are not wire frames
# ---------------------------------------------------------------------------


def _internal_state_events() -> set[str]:
    """The event names ``game/state.py`` fires through the broadcast callback.

    Read out of the source rather than imported, so this test needs no working
    Home Assistant — the same reason ``test_frame_surface_coverage_787`` parses
    ``protocol.py`` with ``ast``.
    """
    tree = ast.parse((_PKG / "game" / "state.py").read_text("utf-8"))
    events: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "attr", None) != "_fire_broadcast":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                events.add(value)
    assert events, "no _fire_broadcast() calls found — this guard reads nothing"
    return events


_SURFACE_ROUTERS = {
    "tv": _JS / "dashboard.js",
    "host": _JS / "admin.js",
    "phone": _JS / "player-core.js",
}

_CASE_RE = re.compile(r"case\s+'([a-z_0-9]+)'\s*:")


@pytest.mark.parametrize("surface", sorted(_SURFACE_ROUTERS))
def test_no_screen_switches_on_an_internal_state_event(surface: str) -> None:
    """These names never leave the server, so a case for one can only mislead.

    ``_fire_broadcast('round_evaluated')`` hands a name to
    ``BroadcastDispatcher``, which turns it into whichever real frame the
    moment calls for — ``round_summary``, ``finale``. The name itself is not on
    the wire and never was. Both boards aliased two of them anyway, which is
    how a reader learns a frame exists that does not.
    """
    source = _SURFACE_ROUTERS[surface].read_text("utf-8")
    handled = set(_CASE_RE.findall(source))
    leaked = sorted(handled & _internal_state_events())
    assert not leaked, (
        f"{surface} has a `case` for {leaked}, which is a server-internal "
        "state-event name (game/state.py fires it at BroadcastDispatcher), not "
        "a frame any socket carries. Handle the frame the dispatcher actually "
        "sends instead."
    )


# ---------------------------------------------------------------------------
# e) one owner for the session keys
# ---------------------------------------------------------------------------

#: The two ``sessionStorage`` keys that make up a player session, and the one
#: module allowed to name them (#787).
SESSION_KEYS = ("quizify_session_token", "quizify_player_name")
SESSION_OWNER = "client-core.js"


def _storage_calls(key: str) -> re.Pattern[str]:
    """``sessionStorage.getItem('key')`` and friends — a use, not a mention.

    Matching the call rather than the bare string is deliberate: a comment
    recording which key went and why is exactly the history this repo keeps,
    and it must not be the thing that turns a guard red.
    """
    return re.compile(r"[sS]torage\.\w+\(\s*['\"]" + re.escape(key) + r"['\"]")


@pytest.mark.parametrize("key", SESSION_KEYS)
def test_only_client_core_spells_a_session_key(key: str) -> None:
    """A second speller is a drift waiting for a release to happen in."""
    call = _storage_calls(key)
    offenders = [
        path.relative_to(_REPO).as_posix()
        for path in _sources()
        if path.name != SESSION_OWNER and call.search(path.read_text("utf-8"))
    ]
    assert not offenders, (
        f"{offenders} name the session key {key!r} themselves. Go through "
        "QuizifyClientCore (saveSession / getSession / clearSession) — one "
        "owner for the spelling is the whole point of #787."
    )


def test_nothing_touches_a_storage_key_nothing_writes() -> None:
    """``quizify_is_admin`` was cleared on every "New game" and never set.

    A key with a remover and no writer is a belief about the code, written in
    the one place a reader will trust it.
    """
    call = _storage_calls("quizify_is_admin")
    offenders = [
        path.relative_to(_REPO).as_posix()
        for path in _sources()
        if call.search(path.read_text("utf-8"))
    ]
    assert not offenders, (
        f"{offenders} read or clear `quizify_is_admin`, which nothing in the "
        "repo sets. Either something writes it now — then say where — or it is "
        "the same ghost #894 removed."
    )


# ---------------------------------------------------------------------------
# e) a duplicate export entry is silent in JS
# ---------------------------------------------------------------------------

_EXPORT_ASSIGN = re.compile(r"window\.(Quizify\w+)\s*=\s*\{", re.M)
_ENTRY_RE = re.compile(r"^\s{8}([A-Za-z_$][\w$]*)\s*:", re.M)


def _export_blocks() -> list[tuple[Path, str, str]]:
    """(file, global name, literal body) for every ``window.QuizifyX = { … }``."""
    blocks: list[tuple[Path, str, str]] = []
    for path in _modules():
        text = path.read_text("utf-8")
        for match in _EXPORT_ASSIGN.finditer(text):
            open_brace = text.index("{", match.start())
            depth = 0
            end = open_brace
            for i in range(open_brace, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            blocks.append((path, match.group(1), text[open_brace : end + 1]))
    return blocks


def test_the_scan_can_see_the_export_objects() -> None:
    """A regex that stopped matching would make the check below vacuous."""
    found = {name for _, name, _ in _export_blocks()}
    for expected in ("QuizifyClientCore", "QuizifyPlayerUtils", "QuizifyPlayerGame"):
        assert expected in found, (
            f"{expected} was not found — this file reads export objects as "
            "`window.QuizifyX = {`, and that shape has changed"
        )


def test_no_module_exports_the_same_name_twice() -> None:
    """The last entry silently wins, so the first one's comment is a lie.

    ``player-utils.js`` carried ``hideReconnectingOverlay`` twice, added by
    #750 and #729, each with a comment explaining why *its* line was there.
    Both were right about the need and one of them was doing nothing.
    """
    offences: list[str] = []
    for path, name, body in _export_blocks():
        seen: dict[str, int] = {}
        for entry in _ENTRY_RE.findall(body):
            seen[entry] = seen.get(entry, 0) + 1
        for entry, count in sorted(seen.items()):
            if count > 1:
                offences.append(
                    f"{path.relative_to(_REPO).as_posix()}: {name}.{entry} "
                    f"listed {count}×"
                )
    assert not offences, (
        "duplicate export entries — JavaScript keeps the last one and says "
        "nothing:\n  " + "\n  ".join(offences)
    )
