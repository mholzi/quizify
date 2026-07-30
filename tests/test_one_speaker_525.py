"""Guard the single game-wide speaker and its migration (#525).

The setup screen used to ask for a speaker twice: once in step 7 ("Quizmaster
voice?") for the narration and again in step 8 ("Does the house play along?")
for the sound effects. Both were labelled "Speaker", both were fed from the same
media_player list, and nothing said they were related.

Decision: **one** speaker for the whole game, with the narration/effects split
demoted into step 8's "Customise single effects" disclosure.

Three things have to hold, and each of them is a way the change could go wrong:

1. The shared control must sit **outside** ``#tts-children``. That container
   gets ``.is-disabled`` → ``pointer-events: none`` when narration is switched
   off, so a speaker parked inside it would be unreachable for a host who wants
   house effects but no quizmaster voice.
2. The house picker must sit **inside** ``#house-advanced`` — that is what
   "behind the disclosure" means.
3. A stored setup holding two *different* speakers must be carried forward with
   the disclosure opened, never silently collapsed onto one value. And the
   picker must be restored from the raw override, because restoring it from the
   resolved value would pre-select the game speaker as an explicit override and
   re-create the split on the next save.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
ADMIN_HTML = WWW / "admin.html"
ADMIN_JS = WWW / "js" / "admin.js"
I18N = WWW / "i18n"


def _element_span(html: str, element_id: str) -> tuple[int, int]:
    """Return (start, end) offsets of the <div> carrying ``element_id``."""
    marker = f'id="{element_id}"'
    at = html.index(marker)
    start = html.rindex("<div", 0, at)
    depth = 0
    i = start
    while i < len(html):
        nxt_open = html.find("<div", i)
        nxt_close = html.find("</div>", i)
        if nxt_close == -1:
            break
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 4
            continue
        depth -= 1
        i = nxt_close + 6
        if depth == 0:
            return start, i
    raise AssertionError(f"unbalanced <div> while scanning #{element_id}")


def _function_body(js: str, name: str) -> str:
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


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------


def test_only_one_speaker_control_outside_the_disclosure() -> None:
    """Exactly one speaker picker may be visible on the default path."""
    html = ADMIN_HTML.read_text("utf-8")
    _, adv_end = _element_span(html, "house-advanced")
    adv_start, _ = _element_span(html, "house-advanced")
    outside = html[:adv_start] + html[adv_end:]
    selects = re.findall(r'<select id="([a-z-]*speaker[a-z-]*)"', outside)
    assert selects == ["game-speaker-select"], (
        "step 7 and step 8 must not both ask for a speaker outside the "
        f"disclosure (#525); found {selects}"
    )


def test_game_speaker_is_not_inside_the_narration_children() -> None:
    """It must survive the narration master switch being off (#525).

    ``.setup-tts-children.is-disabled`` sets ``pointer-events: none``, so a
    control parked in there cannot be operated — and this one also drives the
    house sound effects.
    """
    html = ADMIN_HTML.read_text("utf-8")
    start, end = _element_span(html, "tts-children")
    assert 'id="game-speaker-select"' not in html[start:end], (
        "the game-wide speaker must live outside #tts-children, otherwise it "
        "goes pointer-events:none when narration is switched off (#525)"
    )
    assert 'id="game-speaker-select"' in html, "the shared picker must exist"


def test_house_speaker_moved_into_the_disclosure() -> None:
    """The split is available, but off the default path (#525)."""
    html = ADMIN_HTML.read_text("utf-8")
    start, end = _element_span(html, "house-advanced")
    assert 'id="house-speaker-select"' in html[start:end], (
        "the effects-speaker override belongs inside #house-advanced (#525)"
    )


def test_pointer_events_none_still_applies_to_the_narration_children() -> None:
    """Pins the property the placement rule depends on."""
    css = (WWW / "css" / "styles.css").read_text("utf-8")
    block = css[css.index(".setup-tts-children.is-disabled") :]
    block = block[block.index("{") + 1 : block.index("}")]
    assert "pointer-events: none" in block, (
        "if this ever stops being true, revisit test_game_speaker_is_not_"
        "inside_the_narration_children — the placement rule exists because of it"
    )


# --------------------------------------------------------------------------
# Resolution + migration
# --------------------------------------------------------------------------


def test_empty_override_follows_the_game_speaker() -> None:
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_resolveHouseSpeaker")
    assert "if (override) return override" in body, (
        "a set override must win over the game speaker"
    )
    assert "_ttsEls.speaker" in body and "_loadTtsConfig()" in body, (
        "an empty override must fall through to the game-wide speaker (#525)"
    )


def test_migration_opens_the_disclosure_on_a_real_divergence() -> None:
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_migrateSpeakerSplit")
    # equal or empty → normalised to "follow", and NOT opened
    assert "houseSpeaker === ttsSpeaker" in body, (
        "a stored value that merely repeats the game speaker carries no intent "
        "and must be normalised to 'follow' (#525)"
    )
    assert "_toggleHouseAdvanced(true)" in body, (
        "a genuine two-speaker setup must open the disclosure so the host sees "
        "the split instead of inheriting it invisibly (#525)"
    )
    # the normalise branch must return before reaching the open call
    norm = body.index("houseSpeaker === ttsSpeaker")
    opened = body.index("_toggleHouseAdvanced(true)")
    assert "return" in body[norm:opened], (
        "the normalise branch must not fall through into opening the disclosure"
    )


def test_migration_runs_at_init() -> None:
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_initHouseToggles")
    assert "_migrateSpeakerSplit()" in body, (
        "the migration has to run when the panel initialises (#525)"
    )


def test_override_picker_is_restored_from_the_raw_value() -> None:
    """Restoring from the resolved value would re-create the split (#525)."""
    js = ADMIN_JS.read_text("utf-8")
    for line in js.splitlines():
        if "_populateEntitySelect(_houseEls.speaker" in line:
            assert "_rawHouseSpeakerOverride()" in line, (
                "the override picker must be restored from the raw override, "
                f"not the resolved speaker (#525): {line.strip()}"
            )
    assert "_rawHouseSpeakerOverride" in js


def test_storage_keeps_the_override_not_the_resolved_speaker() -> None:
    """Persisting the resolved value would resurrect the split (#525).

    Failure mode this pins: host follows the game speaker (override empty), a
    save writes the *resolved* id into storage, the host later changes the game
    speaker — now the two stored values differ, and on the next load the
    migration reads that as a deliberate two-speaker setup and re-opens the
    split the host never asked for.
    """
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_saveHouseConfig")
    assert "media_player_override" in body, (
        "the stored object must carry the raw override (#525)"
    )
    assert "_rawHouseSpeakerOverride()" in body


def test_legacy_storage_without_the_override_key_still_loads() -> None:
    """Setups written before this change only have the legacy key (#525)."""
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_loadHouseConfig")
    assert "saved.media_player_override" in body, (
        "the new key must be preferred when present"
    )
    assert "saved.media_player" in body, (
        "a pre-#525 setup only has the legacy key and must still be read"
    )
    new_at = body.index("saved.media_player_override")
    legacy_at = body.index("else if (typeof saved.media_player === 'string')")
    assert new_at < legacy_at, "the override key must take precedence"


def test_house_config_sends_a_resolved_entity_id() -> None:
    """The wire format is unchanged — the server still gets one entity id."""
    body = _function_body(ADMIN_JS.read_text("utf-8"), "_readHouseConfig")
    assert "_resolveHouseSpeaker(" in body, (
        "configure_house must carry the resolved speaker, so collapsing the UI "
        "does not change what the backend receives (#525)"
    )


# --------------------------------------------------------------------------
# i18n
# --------------------------------------------------------------------------


def test_new_labels_exist_in_every_language() -> None:
    for lang in ("en", "de", "es"):
        data = json.loads((I18N / f"{lang}.json").read_text("utf-8"))
        setup = data["setup"]
        assert setup["audio"]["speaker"], f"{lang}: setup.audio.speaker missing"
        assert setup["audio"]["speakerHint"], f"{lang}: setup.audio.speakerHint missing"
        house = setup["house"]
        assert house["speakerOverride"], f"{lang}: house.speakerOverride missing"
        assert house["speakerFollow"], f"{lang}: house.speakerFollow missing"


def test_retired_label_is_gone_everywhere() -> None:
    """The old step-7 'Speaker' key must not linger unused."""
    html = ADMIN_HTML.read_text("utf-8")
    assert "setup.tts.speaker" not in html
    for lang in ("en", "de", "es"):
        data = json.loads((I18N / f"{lang}.json").read_text("utf-8"))
        assert "speaker" not in data["setup"]["tts"], (
            f"{lang}: setup.tts.speaker is no longer referenced by the markup"
        )
