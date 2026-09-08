"""One label, one form: the bank line reads the same in both panels (#922).

The Hot Seat auction card and the final Wager card are the same component --
``.wager-panel``, with the same ``.wager-bank-line`` inside -- and both print
the player's bank through the same i18n key, ``wager.bank``. Only the wager one
carried a colon, and it carried it in the *markup*::

    <span data-i18n="wager.bank">Current points</span>:      <- wager
    <span data-i18n="wager.bank">Current points</span>       <- hot seat

So one player, in one evening, saw "Current points: 75" during the auction's
sibling screen and "Current points 32" in the auction itself, minutes apart.
Seen live on v1.17.0-RC1 at 390x844.

The punctuation being outside the translated string is the part that does not
heal on its own: ``de.json``, ``en.json`` and ``es.json`` each get to choose the
words and none of them gets to choose the separator. A language that punctuates
a label differently -- or drops the separator entirely -- has nowhere to say so.

The fix moved the colon into the three strings and took it out of the markup.
This file guards both halves:

* every ``data-i18n="wager.bank"`` occurrence in ``player.html`` renders the
  same label form, i.e. what sits between the label span and the value span is
  identical in both panels (this is the assertion that fails on the old file);
* no ``data-i18n`` span in ``player.html`` is followed by hard-coded
  punctuation, which is the general shape of the bug rather than this instance
  of it;
* all three locales carry ``wager.bank`` with the same trailing punctuation, so
  the separator is a translated property and stays consistent across languages.

What it does NOT check: how the line *looks*. Whether a colon is the right
separator, and whether the mono value next to the muted label needs one at all,
is a design question -- the test only holds the two panels to one answer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_PLAYER_HTML = _WWW / "player.html"
_I18N = _WWW / "i18n"
_LOCALES = ("de", "en", "es")

# The label span, plus whatever literal text follows it before the next tag.
# That trailing run is the whole bug: a colon living in the markup instead of
# in the string.
_BANK_LABEL = re.compile(
    r'<span data-i18n="wager\.bank">(?P<fallback>[^<]*)</span>(?P<trailing>[^<]*)'
)

# Any translated span followed by non-whitespace literal text -- the general
# form of "punctuation the translator cannot reach".
_TRAILING_PUNCT = re.compile(r'(<span data-i18n="[^"]+">[^<]*</span>)([^\s<][^<]*)')

# The separator a locale chose: the run of non-word characters a label ends on,
# possibly empty. Comparing whole strings would only prove they are different
# languages; comparing last characters would compare Spanish 's' to German 'd'.
_TRAILING_SEPARATOR = re.compile(r"[^\w\s]*$")


@pytest.fixture(scope="module")
def player_html() -> str:
    return _PLAYER_HTML.read_text(encoding="utf-8")


def test_both_panels_print_the_bank_label_the_same_way(player_html: str) -> None:
    """The wager card and the Hot Seat card must render one label form."""
    matches = list(_BANK_LABEL.finditer(player_html))
    assert len(matches) == 2, (
        "expected the wager panel and the Hot Seat panel to be the only two "
        f"users of wager.bank in player.html, found {len(matches)}"
    )

    forms = {
        (m.group("fallback"), m.group("trailing").strip()) for m in matches
    }
    assert len(forms) == 1, (
        "the two .wager-bank-line labels do not read the same:\n  "
        + "\n  ".join(
            f"fallback={f!r} trailing={t!r}" for f, t in sorted(forms)
        )
        + "\nBoth panels are the same component and share the i18n key, so the "
        "label -- punctuation included -- has to come out identical."
    )


def test_no_translated_span_is_followed_by_hardcoded_punctuation(
    player_html: str,
) -> None:
    """Separators belong to the string, not to player.html."""
    offenders = [
        (player_html[: m.start()].count("\n") + 1, m.group(1), m.group(2).strip())
        for m in _TRAILING_PUNCT.finditer(player_html)
    ]
    assert not offenders, (
        "these data-i18n spans carry punctuation no translation can change:\n  "
        + "\n  ".join(
            f"player.html:{ln}: {span}{tail!r}" for ln, span, tail in offenders
        )
    )


def test_every_locale_punctuates_the_bank_label_the_same(player_html: str) -> None:
    """de/en/es all own the separator, and all three agree on it."""
    values = {}
    for locale in _LOCALES:
        data = json.loads((_I18N / f"{locale}.json").read_text(encoding="utf-8"))
        assert "wager" in data and "bank" in data["wager"], (
            f"{locale}.json is missing wager.bank"
        )
        values[locale] = data["wager"]["bank"]

    tails = {
        locale: _TRAILING_SEPARATOR.search(value).group(0)
        for locale, value in values.items()
    }
    assert len(set(tails.values())) == 1, (
        "the locales disagree on how the bank label ends: "
        + ", ".join(f"{loc}={val!r}" for loc, val in sorted(values.items()))
    )

    # And the untranslated fallback in the markup has to match the English
    # string, or a missing i18n bundle changes the punctuation back.
    fallback = _BANK_LABEL.search(player_html)
    assert fallback is not None
    assert fallback.group("fallback") == values["en"], (
        f"player.html's fallback {fallback.group('fallback')!r} does not match "
        f"en.json's {values['en']!r}"
    )
