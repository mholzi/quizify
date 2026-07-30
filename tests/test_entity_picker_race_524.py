"""Guard the entity-picker race fix (#524 / #527) and the toggle gap (#526).

#524: on every fresh admin tab ``sessionStorage`` is empty, so the page-init
fetch to the admin-token-gated ``/api/quizify/tts-entities`` went out without a
token and 401'd *by construction*. Its failure handler then repainted the
"None found — configure in HA" fallback over the lists the admin-connect frame
(#502) had already delivered — the dropdown was briefly correct and then wiped.
Remote hosts hit it reliably: the WebSocket is already open and pushes at once,
while the HTTP leg makes a fresh round-trip, so the doomed 401 lands last.

#527 is the same defect in the House-Plays-Along copy: the late 401 wiped the
rendered party-light checkbox list, which is why a host with 72 lights could
not select one.

Two guarantees, asserted over the shipped ``admin.js`` text because the
behaviour lives in the browser and there is no JS test runner in this repo:
1. Neither panel fetches entities at page-init any more.
2. Every failure branch bails out when the list already loaded, so a request
   that learned nothing can never overwrite an answer that did.

#526: the switch/label gap now lives on the shared ``.toggle-compact`` base
instead of being re-declared per panel — the TTS panel was the call site that
never got one, so step 7's labels butted against their switches.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
ADMIN_JS = WWW / "js" / "admin.js"
STYLES = WWW / "css" / "styles.css"
SHARED_SRC = WWW / "css" / "src" / "02-shared.css"


def _function_body(js: str, name: str) -> str:
    """Return the source of ``function <name>(...) { ... }`` by brace matching."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", js)
    assert m, f"{name}() not found in admin.js"
    depth = 0
    start = m.end() - 1
    for i in range(start, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start : i + 1]
    raise AssertionError(f"unbalanced braces while scanning {name}()")


def _rule(css: str, selector: str) -> str:
    """Return the declaration block for the first rule matching ``selector``."""
    idx = css.index(selector)
    start = css.index("{", idx)
    end = css.index("}", start)
    return css[start + 1 : end]


# --------------------------------------------------------------------------
# #524 / #527 — no doomed fetch at page-init
# --------------------------------------------------------------------------


def test_tts_panel_does_not_fetch_entities_at_init() -> None:
    """_initTtsToggles must not call the token-gated loader (#524).

    At page-init no admin token exists yet, so the call could only ever 401.
    """
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_initTtsToggles")
    assert "_loadTtsEntities(" not in body, (
        "_initTtsToggles() must not fetch entities at page-init (#524): the "
        "admin token has not arrived yet, so the request 401s and its failure "
        "handler wipes the lists the admin-connect frame delivered"
    )


def test_house_panel_does_not_fetch_entities_at_init() -> None:
    """_initHouseToggles must not call the token-gated loader (#527)."""
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_initHouseToggles")
    assert "_loadHouseEntities(" not in body, (
        "_initHouseToggles() must not fetch entities at page-init (#527)"
    )


def test_ws_frame_remains_the_primary_population_path() -> None:
    """The admin-connect frame must still populate both panels (#502).

    Dropping the init fetch is only safe because the WebSocket carries the
    lists; this pins that the consumer is still wired up.
    """
    js = ADMIN_JS.read_text("utf-8")
    body = _function_body(js, "handleGameState")
    assert "msg.tts_entities" in body and "msg.media_players" in body
    assert "msg.house_entities" in body
    # …and the HTTP loaders survive as the fallback for an older server, now
    # reached only once the admin token is in hand.
    assert "_loadTtsEntities(" in body
    assert "_loadHouseEntities(" in body


# --------------------------------------------------------------------------
# #524 / #527 — a failed load never overwrites a successful one
# --------------------------------------------------------------------------


def test_tts_failure_branches_bail_out_when_already_loaded() -> None:
    """Both the 401 branch and the .catch must respect _ttsEntitiesLoaded."""
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_loadTtsEntities")
    guards = re.findall(r"if\s*\(\s*_ttsEntitiesLoaded\s*\)\s*return", body)
    assert len(guards) >= 2, (
        "_loadTtsEntities must bail out of BOTH the !data branch and the "
        ".catch when the lists already loaded (#524)"
    )
    # Each wipe call must sit behind a guard: no `null` repaint may appear
    # before the first guard in the function body.
    first_guard = body.index("if (_ttsEntitiesLoaded) return")
    head = body[:first_guard]
    assert "_populateEntitySelect(_ttsEls.engine, null" not in head, (
        "a 'None found' repaint must never run ahead of the loaded-guard"
    )


def test_house_failure_branches_bail_out_when_already_loaded() -> None:
    """Same guarantee for the party-light list and the house pickers (#527)."""
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_loadHouseEntities")
    guards = re.findall(r"if\s*\(\s*_houseEntitiesLoaded\s*\)\s*return", body)
    assert len(guards) >= 2, (
        "_loadHouseEntities must bail out of BOTH the !data branch and the "
        ".catch when the lists already loaded (#527)"
    )
    first_guard = body.index("if (_houseEntitiesLoaded) return")
    head = body[:first_guard]
    assert "_renderHouseLightList(null" not in head, (
        "the party-light list must never be wiped ahead of the loaded-guard"
    )


def test_successful_load_still_marks_loaded() -> None:
    """The success paths must set the flags the guards read."""
    js = ADMIN_JS.read_text("utf-8")
    assert "_ttsEntitiesLoaded = true" in _function_body(js, "_loadTtsEntities")
    assert "_houseEntitiesLoaded = true" in _function_body(js, "_populateHouseEntities")


# --------------------------------------------------------------------------
# #526 — switch/label gap
# --------------------------------------------------------------------------


def test_toggle_compact_carries_the_gap() -> None:
    """The shared base supplies the switch↔label gap for every panel (#526)."""
    block = _rule(STYLES.read_text("utf-8"), ".toggle-compact {")
    m = re.search(r"gap:\s*(\d+)px", block)
    assert m, ".toggle-compact must declare the switch/label gap (#526)"
    assert int(m.group(1)) >= 8, "gap must be visible, not hairline"


def test_gap_is_authored_in_the_css_source_not_only_the_build() -> None:
    """styles.css is generated — the rule has to live in css/src/ too."""
    block = _rule(SHARED_SRC.read_text("utf-8"), ".toggle-compact {")
    assert re.search(r"gap:\s*\d+px", block), (
        "the gap must be authored in css/src/02-shared.css, otherwise the "
        "next build_css.py run drops it"
    )


def test_tts_toggles_inherit_the_gap() -> None:
    """Step 7's rows must end up spaced like steps 6 and 8.

    The TTS panel never declared a gap of its own — that WAS the bug — so the
    guarantee is that it inherits one and that no later rule zeroes it.
    """
    css = STYLES.read_text("utf-8")
    selectors = (
        ".setup-tts-toggle",
        ".setup-house-toggle",
        ".setup-lightning-toggle",
    )
    for selector in selectors:
        for m in re.finditer(re.escape(selector) + r"\s*\{([^}]*)\}", css):
            assert not re.search(r"gap:\s*0", m.group(1)), (
                f"{selector} must not cancel the inherited gap (#526)"
            )
