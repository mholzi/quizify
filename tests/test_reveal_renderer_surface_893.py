"""No renderer in the reveal module may sit there uncalled (#893).

``renderRevealEmotion``, ``renderPersonalResult`` and ``renderAllAnswers`` were
written for the pre-redesign reveal and kept working after nothing called them:
``updateRevealView`` stopped dispatching to them, but each stayed in the module,
stayed in the ``QuizifyPlayerReveal`` export block, and kept three ``sr-only``
placeholder divs alive in ``player.html`` plus ~200 lines of CSS and eleven
translation keys in three languages. The export entry is what made them look
alive — ``renderAllAnswers: renderAllAnswers`` is a reference, not a call, so a
plain grep for the name always found something.

So the rule this pins is about *calls*, not mentions: every ``function render…``
defined in ``player-reveal.js`` has to be invoked somewhere the browser actually
loads. The reveal's entry points count because ``player-core.js`` calls them
through the export object; a function whose only company is its own export line
does not.

The corpus skips the generated bundles — a renderer must not stay "reachable"
because ``player.bundle.js`` carries a second copy of the module.
"""

from __future__ import annotations

import re
from pathlib import Path

_WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"
_REVEAL = _WWW / "js" / "player-reveal.js"

#: Generated from the ``js/player-*.js`` modules — counting them would let every
#: module function look used twice.
_GENERATED = {"player.bundle.js", "common.bundle.js"}

_DEFINITION = re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(")

#: Renderers ``updateRevealView`` dispatches to, plus the two entry points
#: ``player-core.js`` reaches through ``window.QuizifyPlayerReveal``.
EXPECTED_EXPORTS = ("updateRevealView", "resetRankMemo", "renderFunFact")


def _corpus() -> str:
    parts: list[str] = []
    for path in sorted(_WWW.rglob("*")):
        if not path.is_file() or path.suffix not in (".js", ".html"):
            continue
        if path.name in _GENERATED:
            continue
        parts.append(path.read_text("utf-8", errors="replace"))
    return "\n".join(parts)


def _call_count(name: str, corpus: str) -> int:
    """``name(`` occurrences that are not the ``function name(`` definition."""
    return len(re.findall(rf"(?<!function )\b{re.escape(name)}\s*\(", corpus))


def test_every_function_in_the_reveal_module_is_called_somewhere() -> None:
    corpus = _corpus()
    defined = sorted(set(_DEFINITION.findall(_REVEAL.read_text("utf-8"))))
    assert defined, "no functions found — the scan is not reading player-reveal.js"

    uncalled = [name for name in defined if _call_count(name, corpus) == 0]
    assert not uncalled, (
        "player-reveal.js defines functions that nothing calls. An entry in the "
        "QuizifyPlayerReveal export block is a reference, not a call — delete "
        "the function and its export entry instead of leaving it exported "
        "(#893):\n  " + "\n  ".join(uncalled)
    )


def test_the_scan_would_notice_a_renderer_that_only_gets_exported() -> None:
    """Guards the guard: a corpus that failed to load would pass vacuously."""
    corpus = _corpus()
    assert _call_count("renderFunFact", corpus) > 0, (
        "renderFunFact is called by updateRevealView but the scan missed it — "
        "the corpus is not being read"
    )
    for gone in ("renderRevealEmotion", "renderPersonalResult", "renderAllAnswers"):
        assert gone not in corpus, (
            f"{gone} is back in the front end. It was deleted in #893 because "
            "nothing dispatched to it; re-add it only together with the call site"
        )


def test_the_export_block_lists_exactly_the_live_entry_points() -> None:
    source = _REVEAL.read_text("utf-8")
    block = source[source.index("window.QuizifyPlayerReveal = {") :]
    block = block[: block.index("};")]
    exported = re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*:", block, re.M)
    assert exported == list(EXPECTED_EXPORTS), (
        f"QuizifyPlayerReveal exports {exported}, expected "
        f"{list(EXPECTED_EXPORTS)}. Anything added here needs a caller (#893)"
    )


def test_player_html_keeps_only_the_placeholders_something_writes_into() -> None:
    markup = (_WWW / "player.html").read_text("utf-8")
    for live in ("reveal-leaderboard-list", "reveal-leaderboard-summary"):
        assert f'id="{live}"' in markup, (
            f"#{live} is gone from player.html, but player-core.js / "
            "player-game.js still write into it"
        )
    for dead in (
        "reveal-emotion",
        "result-content",
        "reveal-results-cards",
        "reveal-question-text",
        "reveal-difficulty-badge",
        "round-analytics-content",
    ):
        assert f'id="{dead}"' not in markup, (
            f"#{dead} is an sr-only placeholder for a renderer deleted in #893. "
            "If something writes into it again, give it a visible home instead"
        )
