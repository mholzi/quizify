"""Guard the progressive image reveal (#434).

The mechanic: an image question may declare ``reveal_style: "progressive"``.
The picture then starts heavily blurred and sharpens as the round timer drains,
so an early guess is worth more than a late one. It rides the ``timer_tick``
message the clients already receive — no new wire message.

The tests below are grouped around the three ways this feature can fail
*silently*, i.e. with a green suite and a broken round:

1. **A blur that never comes off.** The reveal view reuses the question view's
   ``<img>`` instead of re-rendering it, so the correct answer would be shown
   next to a picture nobody can read. Both clients must clear it explicitly.
2. **A blur the phone doesn't apply.** The dashboard is the shared screen, but
   every player holds a second copy of the same image. If only the TV blurs,
   the round is decoration — and the zoom overlay is a third copy again.
3. **A style that survives without a picture.** This issue sat blocked for
   months precisely because the image feature was empty; an "unblur" with
   nothing to unblur is the same trap in a new shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.quizify.game.questions import (
    REVEAL_STYLE_NONE,
    REVEAL_STYLE_PROGRESSIVE,
    _parse_question,
    _sanitize_reveal_style,
)

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"

IMG = "/quizify/static/img/packs/example.jpg"


def _mc_question(**extra) -> dict:
    """A minimal valid multiple-choice question dict."""
    data = {
        "id": "q1",
        "question": "Was ist das?",
        "answers": [
            {"text": "A", "correct": True},
            {"text": "B"},
            {"text": "C"},
        ],
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------- sanitizer


def test_progressive_style_is_kept_when_there_is_an_image() -> None:
    assert (
        _sanitize_reveal_style("progressive", IMG, "q1") == REVEAL_STYLE_PROGRESSIVE
    )


def test_style_without_an_image_is_dropped() -> None:
    """The exact trap #434 was blocked on: an effect with no subject.

    Keeping the style here would blur an element that has no ``src``, i.e.
    show a grey box where the question's layout expects nothing at all.
    """
    assert _sanitize_reveal_style("progressive", "", "q1") == REVEAL_STYLE_NONE


@pytest.mark.parametrize(
    "raw",
    ["progresive", "blur", "PROGRESSIVE ", "true", 1, {"style": "progressive"}, None],
)
def test_unknown_styles_fall_back_to_showing_the_image(raw: object) -> None:
    """A typo must not leave the picture permanently unreadable.

    An unrecognised value keyed straight into a CSS class would do exactly
    that — the class never matches a rule that removes the blur, so the round
    plays out behind a grey rectangle. Falling back to "show it" degrades to
    the pre-#434 behaviour instead.

    ``"PROGRESSIVE "`` is the one case that is *accepted* after normalisation;
    it is listed here to pin that trimming and case-folding happen.
    """
    result = _sanitize_reveal_style(raw, IMG, "q1")
    if isinstance(raw, str) and raw.strip().lower() == REVEAL_STYLE_PROGRESSIVE:
        assert result == REVEAL_STYLE_PROGRESSIVE
    else:
        assert result == REVEAL_STYLE_NONE


def test_packs_without_the_field_are_unchanged() -> None:
    q = _parse_question(_mc_question(image_url=IMG), "Test")
    assert q is not None
    assert q.reveal_style == REVEAL_STYLE_NONE


def test_parse_question_carries_the_style_through() -> None:
    q = _parse_question(
        _mc_question(image_url=IMG, reveal_style="progressive"), "Test"
    )
    assert q is not None
    assert q.reveal_style == REVEAL_STYLE_PROGRESSIVE


def test_parse_question_drops_a_style_whose_image_was_rejected() -> None:
    """A sanitised-away URL must take the style with it.

    ``javascript:`` is dropped by ``_sanitize_image_url``, which leaves the
    question image-less — so the style has to go too. This is why the style is
    sanitised against the *cleaned* URL rather than the raw pack value.
    """
    q = _parse_question(
        _mc_question(image_url="javascript:alert(1)", reveal_style="progressive"),
        "Test",
    )
    assert q is not None
    assert q.image_url == ""
    assert q.reveal_style == REVEAL_STYLE_NONE


# ------------------------------------------------------------------- wire


def test_question_started_payload_carries_the_style() -> None:
    from custom_components.quizify.server import serializers

    q = _parse_question(
        _mc_question(image_url=IMG, reveal_style="progressive"), "Test"
    )
    assert q is not None
    payload = serializers.serialize_question_for_player(
        question=q,
        shuffled_answers=["A", "B", "C"],
        round_num=1,
        total_rounds=10,
        timer_duration=30,
    )
    assert payload["reveal_style"] == REVEAL_STYLE_PROGRESSIVE


# ---------------------------------------------------------------- clients

DASHBOARD = WWW / "dashboard.html"
PLAYER_GAME = WWW / "js" / "player-game.js"
PLAYER_CORE = WWW / "js" / "player-core.js"
BUNDLE = WWW / "js" / "player.bundle.js"
CSS = WWW / "css" / "styles.css"


def test_dashboard_drives_the_blur_off_the_timer() -> None:
    src = DASHBOARD.read_text(encoding="utf-8")
    assert "setRevealBlur" in src
    # The tick handler is the only clock the board has; if the call is not
    # there the picture stays at its opening blur for the whole round.
    tick = src.split("function handleTimerTick")[1][:600]
    assert "setRevealBlur" in tick


def test_both_clients_clear_the_blur_at_reveal() -> None:
    """Failure mode 1 — the answer shown beside an unreadable picture."""
    dash = DASHBOARD.read_text(encoding="utf-8")
    summary = dash.split("function handleRoundSummary")[1][:800]
    assert "clearRevealBlur" in summary

    core = PLAYER_CORE.read_text(encoding="utf-8")
    player_summary = core.split("function handleRoundSummary")[1][:800]
    assert "clearRevealBlur" in player_summary


def test_player_blurs_its_own_copy_and_the_zoom_overlay() -> None:
    """Failure mode 2 — the mechanic defeated by looking at your phone.

    The zoom overlay is a *second* ``<img>`` built from the same ``src``, so
    it needs the blur as well; otherwise one tap on the magnifier hands out
    the sharp picture and the round is over.
    """
    src = PLAYER_GAME.read_text(encoding="utf-8")
    assert "question-image" in src
    assert "image-zoom-img" in src
    targets = src.split("function _revealTargets")[1][:400]
    assert "image-zoom-img" in targets


def test_bundle_is_rebuilt_with_the_reveal() -> None:
    """player.bundle.js is generated — a stale one ships the old behaviour."""
    bundle = BUNDLE.read_text(encoding="utf-8")
    assert "setRevealBlur" in bundle
    assert "_revealTargets" in bundle


def test_css_defines_the_blur_for_board_player_and_zoom() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert ".question-image.progressive-reveal" in css
    assert ".image-zoom-img.progressive-reveal" in css
    assert "--reveal-blur" in css


def test_reduced_motion_never_switches_the_blur_off() -> None:
    """Accessibility must not turn into an advantage.

    The global ``prefers-reduced-motion`` block collapses every transition
    duration, which is all this feature needs — the tick-to-tick easing stops
    and the blur still lands on each new value. The failure to guard against
    is the *next* well-meant edit: a reduced-motion rule that also clears
    ``filter`` would show the sharp picture to whoever asked for calmer
    motion, i.e. hand them the answer.
    """
    css = CSS.read_text(encoding="utf-8")
    for block in css.split("prefers-reduced-motion")[1:]:
        body = block[:1500]
        if "progressive-reveal" not in body:
            continue
        assert "filter" not in body, (
            "a reduced-motion block touches the reveal's filter — that would "
            "reveal the image early for those users"
        )
