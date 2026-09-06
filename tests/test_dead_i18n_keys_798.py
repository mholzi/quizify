"""Every leaf key in ``en.json`` has to be reachable from the front end (#798).

``test_i18n_hardcoded_strings_625`` forces ``de.json`` and ``es.json`` to carry
exactly the key set of ``en.json``. That parity is worth having, but it turns
one dead key into three dead strings, and by the time this test was written 87
keys — the launcher's popup handling, the pre-redesign lobby, the flat
``highlights.*`` and ``leaderboard.*`` stats, the whole lightning recap bar —
were being translated into three languages and read by nothing.

Reachability is decided the boring way, on purpose: the dotted key appears
verbatim somewhere under ``www/`` (as ``data-i18n="…"``, ``t('…')``, or a key
literal handed around in JS), or it starts with a prefix that is demonstrably
assembled at runtime. Those prefixes are listed in ``DYNAMIC_PREFIXES`` below,
each with the call site that builds it, so the whitelist stays a short list of
known concatenations rather than a place to hide new dead keys.

``player.bundle.js`` is excluded from the corpus — it is generated from the
``js/player-*.js`` modules, so counting it would let a key stay "referenced"
purely because the bundle had not been rebuilt yet.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "custom_components" / "quizify"
_WWW = _PKG / "www"
_I18N = _WWW / "i18n"

LANGUAGES = ("de", "en", "es")

#: Key prefixes completed at runtime. Every entry names the code that builds it;
#: nothing goes on this list without one.
DYNAMIC_PREFIXES = {
    "errors.": "i18n.js:80 / pack-submit.js:249 — t('errors.' + code)",
    "join.refused.": "player-core.js:1346 — 'join.refused.' + code",
    "packSubmit.status.": "pack-submit.js:384 — 'packSubmit.status.' + status",
    "powerups.": "player-game.js:1297 — t('powerups.' + powerupType)",
    "difficulties.": "player-lobby.js:56 — t('difficulties.' + diff)",
    "connection.": "player-utils.js:235 — t('connection.' + status)",
    # Selected by pack/category id and by place, both server-supplied.
    "categories.": "category ids arrive from the pack metadata, not from source",
    "podium.": "place labels are indexed by finishing position",
}


def _leaves(node: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in node.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_leaves(value, name))
        else:
            out[name] = value
    return out


def _bundle(code: str) -> dict:
    return json.loads((_I18N / f"{code}.json").read_text("utf-8"))


def _reference_corpus() -> str:
    parts: list[str] = []
    for path in sorted(_WWW.rglob("*")):
        if not path.is_file() or path.suffix not in (".html", ".js"):
            continue
        if path.name == "player.bundle.js":  # generated from js/player-*.js
            continue
        if path.parent.name == "i18n":
            continue
        parts.append(path.read_text("utf-8", errors="replace"))
    for path in sorted(_PKG.rglob("*.py")):
        parts.append(path.read_text("utf-8", errors="replace"))
    return "\n".join(parts)


def _unreachable_keys() -> list[str]:
    corpus = _reference_corpus()
    dead: list[str] = []
    for key in sorted(_leaves(_bundle("en"))):
        if key in corpus:
            continue
        if any(key.startswith(prefix) for prefix in DYNAMIC_PREFIXES):
            continue
        dead.append(key)
    return dead


def test_no_translation_key_is_read_by_nothing() -> None:
    dead = _unreachable_keys()
    assert not dead, (
        f"{len(dead)} keys in en.json are referenced nowhere under www/ and sit "
        "under no runtime-assembled prefix. Parity keeps a copy in de.json and "
        f"es.json too, so each one is three strings to maintain (#798):\n  "
        + "\n  ".join(dead)
    )


def test_the_scan_can_see_keys_that_are_plainly_in_use() -> None:
    """Guards the guard: a corpus that failed to load would pass vacuously."""
    corpus = _reference_corpus()
    for live in ("lobby.you", "admin.nextRound", "reveal.finalResults"):
        assert live in corpus, (
            f"{live!r} is used in the front end but the corpus missed it — the "
            "scan is not reading www/ properly"
        )


def test_every_whitelisted_prefix_actually_has_keys_behind_it() -> None:
    """A prefix left over after its keys are gone would silently widen the net."""
    keys = _leaves(_bundle("en"))
    for prefix in DYNAMIC_PREFIXES:
        assert any(key.startswith(prefix) for key in keys), (
            f"{prefix!r} is whitelisted but no key uses it — drop it from "
            "DYNAMIC_PREFIXES so the scan stays as tight as it looks"
        )
