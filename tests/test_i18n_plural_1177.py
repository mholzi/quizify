"""Plural rules and language-aware numbers in ``i18n.js`` (#1177).

Before this the bundles had no plural rule. Every count was a hand-built pair
(``lobby.alsoOne`` / ``lobby.alsoMany``) or, worse, a sentence assembled from
fragments — which is how the host lobby came to read "noch 2 fehlt" and
"faltan 1 más", and the highlights "1 pts". ``t()`` now understands the ICU
subset ``{count, plural, =0{…} one{…} other{…}}`` with ``#`` for the count,
and writes numbers the way the game language does.

The tests drive the real ``i18n.js`` in node against small fixture bundles, so
the parser is checked on its own, apart from whatever the shipped bundles say.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "custom_components" / "quizify" / "www" / "js"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_BUNDLES = {
    "en": {
        "p": {
            "players": "{count, plural, one{# player} other{# players}}",
            "missing": "{count, plural, =0{Ready} one{1 more player} other{# more players}} to go",
            "mixed": "{name}: {count, plural, one{# point} other{# points}}",
            "nested": "{a, plural, one{one a and {b, plural, one{# b} other{# bs}}} other{# as}}",
            "plain": "Score {score}",
        }
    },
    "de": {
        "p": {
            "players": "{count, plural, one{# Spieler} other{# Spieler}}",
            "missing": "Noch {count, plural, one{1 Spieler fehlt} other{# Spieler fehlen}}",
            "mixed": "{name}: {count, plural, one{# Punkt} other{# Punkte}}",
            "nested": "{a, plural, one{ein a} other{# as}}",
            "plain": "Punkte {score}",
        }
    },
    "es": {
        "p": {
            "players": "{count, plural, one{# jugador} other{# jugadores}}",
            "missing": "{count, plural, one{Falta 1 jugador} other{Faltan # jugadores}} para empezar",
            "mixed": "{name}: {count, plural, one{# punto} other{# puntos}}",
            "nested": "{a, plural, one{una a} other{# as}}",
            "plain": "Puntos {score}",
        }
    },
}

_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});
(async function () {{
    var I = window.QuizifyI18n;
    var out = {{}};
    for (var lang of ['en', 'de', 'es']) {{
        await I.setLanguage(lang);
        out[lang] = {{
            players1: I.t('p.players', {{ count: 1 }}),
            players3: I.t('p.players', {{ count: 3 }}),
            missing0: I.t('p.missing', {{ count: 0 }}),
            missing1: I.t('p.missing', {{ count: 1 }}),
            missing2: I.t('p.missing', {{ count: 2 }}),
            mixed: I.t('p.mixed', {{ name: 'Bea', count: 1 }}),
            big: I.t('p.players', {{ count: 12345 }}),
            nested: I.t('p.nested', {{ a: 1, b: 2 }}),
            noCount: I.t('p.players'),
            plain: I.t('p.plain', {{ score: 384400 }}),
            stringParam: I.t('p.plain', {{ score: '384400' }}),
            decimal: I.formatNumber(3.5),
            date: I.formatDate(Date.UTC(2026, 10, 2, 12, 0), {{ month: 'short', timeZone: 'UTC' }}),
            weekday: I.formatDate(Date.UTC(2026, 10, 2, 12, 0), {{ weekday: 'short', timeZone: 'UTC' }}),
        }};
    }}
    console.log(JSON.stringify(out));
}})();
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("i18n")
    for code, bundle in _BUNDLES.items():
        (root / f"{code}.json").write_text(json.dumps(bundle), encoding="utf-8")
    script = _SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(root)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_one_and_other_pick_the_right_branch(result: dict) -> None:
    assert result["en"]["players1"] == "1 player"
    assert result["en"]["players3"] == "3 players"
    assert result["es"]["players1"] == "1 jugador"
    assert result["es"]["players3"] == "3 jugadores"


def test_the_lobby_line_is_grammatical_in_every_language(result: dict) -> None:
    """The exact sentences the review quoted as broken."""
    assert result["de"]["missing1"] == "Noch 1 Spieler fehlt"
    assert result["de"]["missing2"] == "Noch 2 Spieler fehlen"
    assert result["es"]["missing1"] == "Falta 1 jugador para empezar"
    assert result["es"]["missing2"] == "Faltan 2 jugadores para empezar"


def test_an_exact_match_beats_the_category(result: dict) -> None:
    assert result["en"]["missing0"] == "Ready to go"


def test_other_params_still_fill_around_the_plural(result: dict) -> None:
    assert result["en"]["mixed"] == "Bea: 1 point"
    assert result["de"]["mixed"] == "Bea: 1 Punkt"


def test_a_plural_can_nest(result: dict) -> None:
    assert result["en"]["nested"] == "one a and 2 bs"


def test_a_missing_count_falls_back_to_other_without_crashing(result: dict) -> None:
    assert result["en"]["noCount"] == "# players"


def test_counts_are_grouped_the_way_the_language_groups_them(result: dict) -> None:
    assert result["en"]["big"] == "12,345 players"
    assert result["de"]["big"] == "12.345 Spieler"
    assert result["es"]["big"] == "12.345 jugadores"


def test_number_params_are_formatted_and_string_params_are_left_alone(
    result: dict,
) -> None:
    assert result["en"]["plain"] == "Score 384,400"
    assert result["de"]["plain"] == "Punkte 384.400"
    assert result["de"]["stringParam"] == "Punkte 384400"


def test_decimals_use_the_language_separator(result: dict) -> None:
    assert result["en"]["decimal"] == "3.5"
    assert result["de"]["decimal"] == "3,5"
    assert result["es"]["decimal"] == "3,5"


def test_dates_are_named_in_the_game_language(result: dict) -> None:
    assert result["en"]["weekday"] == "Mon"
    assert result["de"]["weekday"].startswith("Mo")
    assert result["es"]["weekday"].startswith("lun")
    assert result["de"]["date"].startswith("Nov")
    assert result["es"]["date"].startswith("nov")
