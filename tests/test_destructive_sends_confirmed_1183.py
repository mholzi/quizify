"""Every message that cannot be taken back asks first (#1183).

UX review 7 found the same action under three rules: "End game" asked on the
admin console, sent straight away from the host's phone bar and the reveal bar,
and sent straight away from the Lovelace card — the card's footer link and its
cockpit button alike. "New game" on the end screen threw every guest out of the
standings without a word. The fixes are one sheet per surface; this is the
static check that keeps it that way.

The rule, mechanical on purpose: every place in the hand-written front-end that
names ``end_game``, ``reset_game`` or ``kick_player`` as a message must sit in
a function that only a confirmation reaches. "Reaches" is read off the source:

* the enclosing function's header mentions a confirmation — the console's
  ``on('end-game-confirm-btn', 'click', function () {``, the phone's
  ``confirmBtn.addEventListener(…)``, the card's ``_onConfirmed() {``, or the
  phone's ``confirmThen(btn, 'end', function () {``; or
* the enclosing function is a named one whose every other mention in the file
  is a ``confirmThen(`` call (``player-end.js``'s ``_startNewGame``).

The scan is not a JavaScript parser. Comments are blanked and string contents
are skipped for the brace matching, which is all the hand-written files need;
an odd construct shows up as a false alarm here, never as a silent pass.
"""

from __future__ import annotations

import re
from pathlib import Path

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"

# Generated or third-party: the bundles are concatenations of the files that
# are scanned, tokens.js is generated from design/tokens.json.
_SKIP = {"player.bundle.js", "common.bundle.js", "tokens.js"}

_DESTRUCTIVE = re.compile(r"""['"](end_game|reset_game|kick_player)['"]""")

# Each exception names the exact line and why it may stand.
_ALLOWED_LINES = {
    (
        "js/admin.js",
        "if (window.confirm(msg)) send('kick_player', { player_name: name });",
    ): (
        "the kick sheet's markup-missing fallback (#480, kept on purpose and "
        "pinned by test_r5_fe_ux); it still asks"
    ),
}


def _sources() -> list[Path]:
    files = sorted((_WWW / "js").glob("*.js")) + sorted((_WWW / "cards").glob("*.js"))
    return [f for f in files if f.name not in _SKIP]


def _strip(source: str) -> tuple[str, str]:
    """Return (code without comments, same code with string contents blanked).

    Both keep every character position and newline, so an index in one is the
    same place in the other and in the original.
    """
    code = list(source)
    blank = list(source)
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                code[i] = blank[i] = " "
                i += 1
            continue
        if ch == "/" and nxt == "*":
            while i < n and not (source[i] == "*" and i + 1 < n and source[i + 1] == "/"):
                if source[i] != "\n":
                    code[i] = blank[i] = " "
                i += 1
            for j in (i, i + 1):
                if j < n:
                    code[j] = blank[j] = " "
            i += 2
            continue
        if ch == "/" and _regex_may_start(source, i):
            # A regex literal: `/"/g` in an escaper would otherwise open a
            # string that swallows the rest of the file.
            i += 1
            in_class = False
            while i < n and source[i] != "\n":
                c = source[i]
                if c == "\\":
                    blank[i] = " "
                    i += 1
                elif c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    break
                if i < n:
                    blank[i] = " "
                i += 1
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\":
                    blank[i] = " "
                    i += 1
                if i < n and source[i] != "\n":
                    blank[i] = " "
                i += 1
            i += 1
            continue
        i += 1
    return "".join(code), "".join(blank)


def _regex_may_start(source: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and source[j] in " \t":
        j -= 1
    if j < 0:
        return True
    if source[j] in "(,=:[!&|?{};+-*%<>~^\n":
        return True
    return bool(re.search(r"\b(return|typeof|case)$", source[max(0, j - 6) : j + 1]))


_FUNC_HEADER = re.compile(
    r"function\b|=>|^\s*(?:async\s+)?(?!(?:if|for|while|switch|catch|with)\b)"
    r"[A-Za-z_$][\w$]*\s*\([^()]*\)\s*\{\s*$"
)


def _line_of(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start : end if end != -1 else len(text)]


def _enclosing_function_header(code: str, blank: str, pos: int) -> str | None:
    """The header line of the innermost function around ``pos``."""
    depth = 0
    i = pos
    while i > 0:
        i -= 1
        ch = blank[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth:
                depth -= 1
                continue
            header = _line_of(code, i)
            if _FUNC_HEADER.search(header):
                return header
    return None


def _function_name(header: str) -> str | None:
    m = re.search(r"function\s+([A-Za-z_$][\w$]*)\s*\(", header) or re.match(
        r"^\s*(?:async\s+)?(?!(?:if|for|while|switch|catch|with)\b)"
        r"([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{\s*$",
        header,
    )
    return m.group(1) if m else None


def _reached_only_through_confirm(code: str, name: str, header: str) -> bool:
    mentions = [
        m.start()
        for m in re.finditer(rf"(?<![\w$.]){re.escape(name)}(?![\w$])", code)
    ]
    others = [p for p in mentions if _line_of(code, p) != header]
    return bool(others) and all("confirmThen(" in _line_of(code, p) for p in others)


def _unconfirmed() -> list[str]:
    findings = []
    for path in _sources():
        rel = path.relative_to(_WWW).as_posix()
        code, blank = _strip(path.read_text("utf-8"))
        for m in _DESTRUCTIVE.finditer(code):
            line = _line_of(code, m.start()).strip()
            if (rel, line) in _ALLOWED_LINES:
                continue
            header = _enclosing_function_header(code, blank, m.start())
            if header and re.search(r"confirm", header.replace("window.confirm", ""), re.I):
                continue
            name = _function_name(header or "")
            if name and _reached_only_through_confirm(code, name, header or ""):
                continue
            lineno = code.count("\n", 0, m.start()) + 1
            findings.append(f"{rel}:{lineno} {m.group(1)} — {line}")
    return findings


def test_every_destructive_send_sits_behind_a_confirmation() -> None:
    findings = _unconfirmed()
    assert not findings, (
        "a message that ends, resets or removes is sent without asking first "
        "(#1183). Route it through the surface's confirm sheet:\n  "
        + "\n  ".join(findings)
    )


def test_the_scan_sees_every_surface() -> None:
    """A scan that finds nothing proves nothing — it must see the sends."""
    seen = {}
    for path in _sources():
        code, _blank = _strip(path.read_text("utf-8"))
        for m in _DESTRUCTIVE.finditer(code):
            seen.setdefault(path.relative_to(_WWW).as_posix(), set()).add(m.group(1))
    assert {"end_game", "reset_game", "kick_player"} <= seen.get("js/admin.js", set())
    assert {"end_game", "reset_game"} <= seen.get("js/player-core.js", set())
    assert "reset_game" in seen.get("js/player-end.js", set())
    assert {"end_game", "reset_game", "kick_player"} <= seen.get(
        "cards/quizify-host-card.js", set()
    )


def test_the_scan_would_catch_an_unconfirmed_send(tmp_path: Path) -> None:
    """The negative case, so the rule cannot pass by matching too loosely."""
    sample = (
        "function wire() {\n"
        "    btn.addEventListener('click', function () {\n"
        "        send('end_game', {});\n"
        "    });\n"
        "}\n"
    )
    code, blank = _strip(sample)
    pos = code.index("'end_game'")
    header = _enclosing_function_header(code, blank, pos)
    assert header is not None and "confirm" not in header.lower()


def test_no_window_confirm_on_the_lovelace_card() -> None:
    """The card's reset used window.confirm, which Companion WebViews swallow."""
    code, _blank = _strip((_WWW / "cards" / "quizify-host-card.js").read_text("utf-8"))
    assert "window.confirm" not in code
