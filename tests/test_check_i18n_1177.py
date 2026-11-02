"""The text check, the glossary file and the stats labels of #1177.

``scripts/check_i18n.py`` runs in CI as a warning first (a separate,
non-required job), so these tests pin its mechanics — that each rule finds what
it is meant to find, and that warning mode never fails a build — rather than
making today's tree pass every rule. Tightening it into an error is a later,
deliberate step.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "check_i18n.py"
_PKG = _REPO / "custom_components" / "quizify"
_I18N = _PKG / "www" / "i18n"


def _checker():
    spec = importlib.util.spec_from_file_location("check_i18n", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_i18n"] = module
    spec.loader.exec_module(module)
    return module


def _seed(root: Path, bundles: dict[str, dict], glossary: dict | None = None) -> None:
    i18n = root / "custom_components" / "quizify" / "www" / "i18n"
    i18n.mkdir(parents=True)
    for code, data in bundles.items():
        (i18n / f"{code}.json").write_text(json.dumps(data), encoding="utf-8")
    (i18n / "glossary.json").write_text(
        json.dumps(glossary or json.loads((_I18N / "glossary.json").read_text("utf-8"))),
        encoding="utf-8",
    )
    www = i18n.parent
    (www / "page.html").write_text(
        '<button aria-label="Close">x</button>'
        '<span data-i18n="a.ok">ok</span>'
        '<span data-i18n="a.okOne">x</span>',
        encoding="utf-8",
    )


def test_warning_mode_never_fails_the_build() -> None:
    check = _checker()
    assert check.main(["--summary"]) == 0


def test_the_shipped_tree_reports_every_category() -> None:
    report = _checker().run(_REPO)
    summary = report.summary()
    for category in ("missing", "placeholder", "glossary", "overlength",
                     "fragment", "card", "hardcoded", "punct", "unused"):
        assert f" {category}" in summary


def test_the_structural_rules_are_clean_on_the_shipped_tree() -> None:
    """Parity, placeholders, fragments, card and hard-coded attributes are not
    matters of taste: a finding there is a bug, so they stay at zero."""
    report = _checker().run(_REPO)
    for category in ("missing", "placeholder", "fragment", "card", "hardcoded"):
        found = [f.line() for f in report.findings if f.category == category]
        assert not found, f"{category}:\n" + "\n".join(found)


def test_each_rule_finds_what_it_is_for(tmp_path: Path) -> None:
    en = {"a": {"ok": "Start Game", "okOne": "1 thing", "missingDe": "x",
                "name": "Hi {name}", "lineLine1": "A"}}
    de = {"a": {"ok": "Streak …", "okOne": "1 Ding", "name": "Hallo {nom}",
                "lineLine1": "A"}}
    es = {"a": {"ok": "¿Comodines...?", "okOne": "1 cosa", "missingDe": "x",
                "name": "Hola {name}", "lineLine1": "A"}}
    _seed(tmp_path, {"en": en, "de": de, "es": es})
    check = _checker()
    report = check.run(tmp_path)
    count = report.count

    assert count("missing") == 1          # de lacks a.missingDe
    assert count("placeholder") == 1      # {nom} vs {name}
    assert count("glossary") >= 2         # de Streak, es Comodines
    assert count("fragment") == 2         # a.line…Line1, a.okOne
    assert count("hardcoded") == 1        # aria-label="Close"
    assert count("punct") >= 3            # "...", space before "…", Title Case
    assert count("unused") >= 1           # a.name, a.missingDe, …
    assert check.main(["--root", str(tmp_path), "--strict", "--summary"]) == 1
    assert check.main(["--root", str(tmp_path), "--summary"]) == 0


def test_plural_templates_are_measured_by_their_longest_branch() -> None:
    check = _checker()
    shown = check.display_text("{count, plural, one{1 player} other{# players}}")
    assert shown == "10 players"
    assert check.placeholders("{count, plural, one{Falta 1} other{Faltan #}} {x}") == {
        "count", "x"
    }


def test_the_glossary_is_not_a_language() -> None:
    """glossary.json lives next to the bundles; the server must not offer it
    as a language chip."""
    from custom_components.quizify.server import views

    assert (_I18N / "glossary.json").is_file()
    views._available_ui_languages.cache_clear()
    try:
        languages = views._available_ui_languages()
    finally:
        views._available_ui_languages.cache_clear()
    assert "glossary" not in languages
    assert set(languages) == {"de", "en", "es"}


def test_the_glossary_carries_the_decisions_of_the_issue() -> None:
    glossary = json.loads((_I18N / "glossary.json").read_text("utf-8"))
    terms = {t["concept"]: t for t in glossary["terms"]}
    assert terms["one question"]["de"] == "Frage"
    assert terms["game leader"]["de"] == "Host"
    assert terms["game leader"]["es"] == "anfitrión"
    assert terms["remove a player"]["es"] == "quitar"
    assert "vosotros" in glossary["voice"]["es"]
    assert glossary["limits"]["button"]["max"] == 22
    assert glossary["limits"]["pill"]["max"] == 18
    assert glossary["limits"]["tvHeading"]["max"] == 28


@pytest.mark.asyncio
async def test_the_stats_chart_sends_dates_for_the_page_to_name(tmp_path: Path) -> None:
    """Weekday and month names come from Intl in the game language (#1177); the
    server sends ISO dates and a unit next to its English strftime labels."""
    from custom_components.quizify.analytics import QuizifyAnalytics

    class _Runtime:
        data_dir = tmp_path

        async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
            return func(*args)

    analytics = QuizifyAnalytics(_Runtime())  # type: ignore[arg-type]
    games = [{"ended_at": time.time()}]
    week = analytics._compute_games_over_time(games, "7d")  # type: ignore[arg-type]
    assert week["unit"] == "day"
    assert len(week["dates"]) == len(week["labels"]) == 7
    assert all(len(d) == 10 and d[4] == "-" for d in week["dates"])
    months = analytics._compute_games_over_time(games, "all")  # type: ignore[arg-type]
    assert months["unit"] == "month"
    assert months["dates"][-1].endswith("-01")
