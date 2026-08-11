"""Keep the UI-language directory scan off the event loop (#542).

``_available_ui_languages`` derives the player's language chips from the
``www/i18n/*.json`` bundles actually shipped, which means a directory scan. It
is ``lru_cache``d, so the cost was exactly one blocking ``scandir`` per HA
start — but it landed on the first player render, inside the loop, where HA's
watcher flagged it and asked for a bug report.

#343 had already moved the other two disk reads in ``views.py`` (manifest,
HTML) into an executor; the language chips arrived later (#492) and missed the
pattern. The fix primes the cache at setup via ``prime_ui_languages``.

Asserted two ways: the priming helper really fills the cache, and setup really
calls it off the loop. The second half is a source-text assertion because
exercising ``async_setup_entry`` needs the full HA harness, which is
``importorskip``-gated in this repo.
"""

from __future__ import annotations

from pathlib import Path

from custom_components.quizify.server.views import (
    _available_ui_languages,
    prime_ui_languages,
)

REPO = Path(__file__).resolve().parent.parent
INIT_PY = REPO / "custom_components" / "quizify" / "__init__.py"


def test_priming_fills_the_cache() -> None:
    """After priming, the request path can resolve languages with no I/O."""
    _available_ui_languages.cache_clear()
    assert _available_ui_languages.cache_info().currsize == 0

    primed = prime_ui_languages()

    assert _available_ui_languages.cache_info().currsize == 1
    assert primed == _available_ui_languages()
    # A cache hit, not a second scan.
    assert _available_ui_languages.cache_info().hits >= 1


def test_priming_returns_the_shipped_bundles() -> None:
    """The primed value is the real bundle set, not a placeholder.

    Guards against a "fix" that satisfies the loop watcher by no longer
    reading the directory at all — the data-driven behaviour from #492 is the
    point of the function.
    """
    _available_ui_languages.cache_clear()
    i18n_dir = REPO / "custom_components" / "quizify" / "www" / "i18n"
    shipped = {p.stem for p in i18n_dir.glob("*.json")}
    assert shipped, "expected www/i18n/ to ship language bundles"
    assert set(prime_ui_languages()) == shipped


def test_setup_primes_it_in_an_executor() -> None:
    """Setup must prime it OFF the loop, or the warning simply moves.

    ``async_add_executor_job`` is what makes this a fix rather than a
    relocation: calling ``prime_ui_languages()`` directly in ``async_setup``
    would run the same scandir on the same loop, just earlier.
    """
    source = INIT_PY.read_text(encoding="utf-8")
    assert "prime_ui_languages" in source, (
        "__init__.py does not prime the UI-language cache — the first player "
        "render will do the scandir on the event loop again (#542)."
    )
    assert "async_add_executor_job(prime_ui_languages)" in source, (
        "prime_ui_languages() must be handed to async_add_executor_job; "
        "calling it directly keeps the blocking scan on the loop."
    )
