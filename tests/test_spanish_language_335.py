"""Tests for Spanish (es) language integration (#335).

Covers the data-driven admin chip rendering (Architecture A) + flag-only
language selector (Selector B), the removal of the de/en clamp in the
featured-pack endpoint, the clean spotlight degradation for a language with no
featured-eligible pack, and end-to-end serving of Spanish questions through the
real QuestionBank queue filter.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import (  # noqa: E402
    QuestionBank,
)
from custom_components.quizify.server import views  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402
from custom_components.quizify.server.views import featured_pack_view  # noqa: E402

# ---------------------------------------------------------------------------
# Fakes (mirrors the harness in test_featured_pack_260.py)
# ---------------------------------------------------------------------------


class _FakeRuntime:
    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


class _FakeBank:
    def __init__(self, pack_versions: dict, categories: dict | None = None) -> None:
        self._pack_versions = pack_versions
        self._categories = categories or {slug: [] for slug in pack_versions}

    @property
    def categories(self) -> dict:
        return dict(self._categories)

    def get_pack_versions(self) -> dict:
        return {slug: dict(meta) for slug, meta in self._pack_versions.items()}


class _FakeRequest:
    def __init__(self, ctx, query: dict) -> None:  # noqa: ANN001
        self.app = {APP_CTX_KEY: ctx}
        self.query = query


def _call_featured(ctx, lang: str) -> dict:
    req = _FakeRequest(ctx, {"lang": lang})
    resp = asyncio.run(featured_pack_view(req))  # type: ignore[arg-type]
    return json.loads(resp.body)


_DE_PACK = {
    "language": "de",
    "question_count": 149,
    "name": "Geografía DE",
    "theme": "geography",
}
_EN_PACK = {
    "language": "en",
    "question_count": 150,
    "name": "Geography",
    "theme": "geography",
}
_ES_PACK = {
    "language": "es",
    "question_count": 120,
    "name": "Geografía",
    "theme": "geography",
}
_ES_PACK2 = {
    "language": "es",
    "question_count": 100,
    "name": "Ciencia",
    "theme": "science",
}


# ---------------------------------------------------------------------------
# Language-derivation helpers
# ---------------------------------------------------------------------------


def test_sorted_pack_languages_known_order() -> None:
    pv = {"a": _EN_PACK, "b": _DE_PACK, "c": _ES_PACK}
    # de, en, es is the fixed display order regardless of dict insertion order.
    assert views._sorted_pack_languages(pv) == ["de", "en", "es"]


def test_sorted_pack_languages_unknown_appended() -> None:
    pv = {"a": _DE_PACK, "z": {"language": "fr", "question_count": 1, "name": "x"}}
    assert views._sorted_pack_languages(pv) == ["de", "fr"]


def test_resolve_default_lang() -> None:
    assert views._resolve_default_lang("es") == "es"
    assert views._resolve_default_lang("es-ES") == "es"
    assert views._resolve_default_lang("de-DE") == "de"
    assert views._resolve_default_lang("en") == "en"
    assert views._resolve_default_lang("") == "en"
    assert views._resolve_default_lang(None) == "en"
    assert views._resolve_default_lang("fr") == "en"  # unknown → en


def test_language_chip_meta_fallback() -> None:
    assert views._language_chip_meta("es") == ("🇪🇸", "Español")
    flag, name = views._language_chip_meta("xx")
    assert flag == "🌐"
    assert name == "XX"


# ---------------------------------------------------------------------------
# Language-chip rendering (Selector B — flag-only)
# ---------------------------------------------------------------------------


def test_language_chips_are_flag_only_with_aria() -> None:
    pv = {"a": _DE_PACK, "b": _EN_PACK, "c": _ES_PACK}
    html = views._render_language_chips(pv, default_lang="es")
    # One chip per present language, flag glyph as the visible content.
    assert html.count("<button") == 3
    assert "🇩🇪" in html and "🇬🇧" in html and "🇪🇸" in html
    # Full name is on aria-label + title for accessibility, not visible text.
    assert 'aria-label="Español"' in html
    assert 'title="Español"' in html
    # data-value carries the bare language code (drives start_game language).
    assert 'data-value="es"' in html
    # The default language chip is the active one — exactly one active chip.
    assert html.count("chip active") == 1
    assert 'data-value="es" aria-label="Español" title="Español"' in html


def test_language_chips_active_falls_back_when_default_absent() -> None:
    pv = {"a": _DE_PACK, "b": _EN_PACK}  # no es packs
    html = views._render_language_chips(pv, default_lang="es")
    # es isn't present → first present language (de) becomes active.
    assert html.count("chip active") == 1
    assert 'class="chip active" data-value="de"' in html


def test_language_chips_empty_bank_falls_back_to_de_en() -> None:
    html = views._render_language_chips({}, default_lang="en")
    assert "🇩🇪" in html and "🇬🇧" in html
    assert html.count("<button") == 2


# ---------------------------------------------------------------------------
# Category-chip rendering (Architecture A — data-driven cards)
# ---------------------------------------------------------------------------


def test_category_chips_preserve_dom_contract() -> None:
    pv = {"geografia-es": _ES_PACK}
    html = views._render_category_chips(pv, default_lang="es")
    # The Mixed tile is always present + active by default, with no data-lang.
    assert 'data-value="mixed"' in html
    assert 'data-i18n="admin.categoryMixed"' in html
    assert 'data-i18n="admin.mixedAllPacks"' in html
    # The pack card preserves every attribute admin.js reads.
    assert 'data-value="geografia-es"' in html
    assert 'data-lang="es"' in html
    assert 'data-theme="geography"' in html
    assert 'data-icon="🌍"' in html
    assert 'data-count="120"' in html
    # The structural spans the JS + paintP1Icons depend on.
    assert '<span class="pack-card-icon">' in html
    assert '<span class="pack-card-name">Geografía</span>' in html
    assert '<span class="pack-card-count">120 preguntas</span>' in html


def test_category_chips_per_language_unit() -> None:
    pv = {"geo-de": _DE_PACK, "geo-en": _EN_PACK, "geo-es": _ES_PACK}
    html = views._render_category_chips(pv, default_lang="de")
    assert "149 Fragen" in html       # de unit
    assert "150 questions" in html    # en unit
    assert "120 preguntas" in html    # es unit


def test_category_chips_escape_pack_name() -> None:
    pv = {
        "x": {"language": "es", "question_count": 5, "name": "A & B <x>", "theme": ""},
    }
    html = views._render_category_chips(pv, default_lang="es")
    assert "A &amp; B &lt;x&gt;" in html
    # Unknown/empty theme → default die icon.
    assert 'data-icon="🎲"' in html


def test_category_chips_grouped_by_language_then_name() -> None:
    pv = {
        "ciencia": _ES_PACK2,    # es, "Ciencia"
        "geografia": _ES_PACK,   # es, "Geografía"
        "geo-en": _EN_PACK,      # en
    }
    html = views._render_category_chips(pv, default_lang="es")
    # en comes before es; within es, alphabetical by name (Ciencia < Geografía).
    i_en = html.index('data-value="geo-en"')
    i_ciencia = html.index('data-value="ciencia"')
    i_geo = html.index('data-value="geografia"')
    assert i_en < i_ciencia < i_geo


# ---------------------------------------------------------------------------
# featured-pack endpoint — de/en clamp removed, es served + degradation
# ---------------------------------------------------------------------------


def test_featured_pack_serves_spanish() -> None:
    bank = _FakeBank({"geo-es": _ES_PACK, "geo-de": _DE_PACK})
    ctx = SimpleNamespace(
        game=SimpleNamespace(question_bank=bank),
        analytics=None,
        question_stats=None,
        runtime=_FakeRuntime(),
    )
    out = _call_featured(ctx, lang="es")
    # No clamp to de: the es pack is featured, with a Spanish subtitle + unit.
    assert out["value"] == "geo-es"
    assert "🌍 Geografía" in out["title"]
    assert "preguntas" in out["meta"]
    assert "Para toda la familia" in out["meta"]  # es 'default' subtitle


def test_featured_pack_spanish_one_pack_still_features_it() -> None:
    """A language with a single pack still yields that pack (no crash, no
    wrong-language fallback) — the spotlight degradation lives client-side."""
    bank = _FakeBank({"geo-es": _ES_PACK})
    ctx = SimpleNamespace(
        game=SimpleNamespace(question_bank=bank),
        analytics=None,
        question_stats=None,
        runtime=_FakeRuntime(),
    )
    out = _call_featured(ctx, lang="es")
    assert out["value"] == "geo-es"


def test_featured_pack_unknown_language_degrades_to_empty() -> None:
    """A language with zero packs returns {} → the admin UI hides the
    spotlight cleanly rather than falling back to a de/en pack."""
    bank = _FakeBank({"geo-de": _DE_PACK, "geo-en": _EN_PACK})
    ctx = SimpleNamespace(
        game=SimpleNamespace(question_bank=bank),
        analytics=None,
        question_stats=None,
        runtime=_FakeRuntime(),
    )
    assert _call_featured(ctx, lang="es") == {}


# ---------------------------------------------------------------------------
# End-to-end: Spanish questions are selectable + served by the real bank
# ---------------------------------------------------------------------------


def _write_pack(qdir: Path, slug: str, language: str, name: str) -> None:
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{slug}.json").write_text(
        json.dumps(
            {
                "name": name,
                "language": language,
                "version": "1.0",
                "theme": "geography",
                "questions": [
                    {
                        "id": f"{slug}_q{i}",
                        "question": f"Pregunta {i}",
                        "answers": [
                            {"text": "A", "correct": True},
                            {"text": "B", "correct": False},
                            {"text": "C", "correct": False},
                        ],
                        "difficulty": "easy",
                        "fun_fact": "Dato.",
                    }
                    for i in range(3)
                ],
            }
        ),
        encoding="utf-8",
    )


def test_spanish_pack_loads_with_es_language(tmp_path: Path) -> None:
    _write_pack(tmp_path, "geografia-es", "es", "Geografía")
    bank = QuestionBank(questions_dir=tmp_path)
    bank.load_category("geografia-es")
    meta = bank.get_pack_versions()["geografia-es"]
    assert meta["language"] == "es"
    assert meta["question_count"] == 3
    # Each question inherits the pack language so the queue filter matches.
    for q in bank.categories["geografia-es"]:
        assert q.language == "es"


def test_queue_filters_to_spanish_questions(tmp_path: Path) -> None:
    _write_pack(tmp_path, "geografia-es", "es", "Geografía")
    _write_pack(tmp_path, "geography-en", "en", "Geography")
    bank = QuestionBank(questions_dir=tmp_path)
    bank.load_category("geografia-es")
    bank.load_category("geography-en")
    # Build the mixed-category queue but filter to Spanish — only es questions
    # come out, proving es flows end-to-end through start_game's language arg.
    bank._build_queue(category=None, difficulty=None, language="es")
    served = []
    while True:
        q = bank.get_next_question()
        if q is None:
            break
        served.append(q)
    assert served, "expected Spanish questions to be served"
    assert all(q.language == "es" for q in served)
    assert all(q.id.startswith("geografia-es") for q in served)
