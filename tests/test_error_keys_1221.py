"""#1221: every error code the server can send has an ``errors.<CODE>`` key.

``FROZEN`` and ``TEAM_LOCKED`` were missing from en/de/es, so phones in de/es
showed the English server message. The client resolves ``errors.<code>`` (see
www/js/i18n.js, player-core.js, pack-submit.js), so each ``ERR_*`` code in
const.py needs a key in every bundle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import custom_components.quizify.const as const

_ROOT = Path(__file__).resolve().parent.parent
_I18N = _ROOT / "custom_components" / "quizify" / "www" / "i18n"
_LANGS = ("en", "de", "es")

#: Codes intentionally without an ``errors.*`` key. Empty on purpose: every
#: ERR_* code (WebSocket and pack-submit HTTP) reaches a client. Add an entry
#: here, with a reason, only for a code that is never sent to any browser.
_NOT_SENT_TO_CLIENTS: dict[str, str] = {}

_CODES = sorted(
    getattr(const, n)
    for n in dir(const)
    if n.startswith("ERR_") and isinstance(getattr(const, n), str)
)


@pytest.mark.parametrize("lang", _LANGS)
def test_every_error_code_has_a_key(lang: str) -> None:
    raw = (_I18N / f"{lang}.json").read_text(encoding="utf-8")
    errors = json.loads(raw)["errors"]
    missing = [
        c
        for c in _CODES
        if c not in _NOT_SENT_TO_CLIENTS and not errors.get(c, "").strip()
    ]
    assert not missing, f"{lang}.json lacks errors.<CODE> for: {missing}"


def test_allowlist_names_real_codes() -> None:
    assert set(_NOT_SENT_TO_CLIENTS) <= set(_CODES)
