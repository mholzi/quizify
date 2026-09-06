"""#732 — the host's Hot Seat notice is written for the host.

#699 gave the admin tab a notice for the three detour phases it had no view
for, and picked existing i18n keys for it. Two of them belong to other
screens:

* ``hotSeat.seatedHint`` — "You answer alone. Right wins your stake; wrong or
  no answer loses it." Second person, written for the phone of the person in
  the chair. The host is not in the chair.
* ``hotSeat.sealed`` — "Bids stay hidden until everyone has placed one." That
  is the auction. It was shown at the **result** stage, next to an enabled
  Next Question, so the host was told the bids were still hidden after the
  seat had been settled.

The English fallbacks in the map said the right thing and never rendered:
``_t()`` returns a translation whenever the key exists, so a wrong-but-present
key always wins. That is why nobody saw it until a television did.

These tests pin the host strings as host strings, in all three languages, and
pin the fallback so it can actually be reached.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

WWW = Path(__file__).resolve().parent.parent / "custom_components/quizify/www"
LANGS = ("en", "de", "es")

HOST_KEYS = ("hostSeated", "hostSeatedUnknown", "hostSettled")

# The two keys #699 borrowed from the player's screen.
PLAYER_KEYS = ("hotSeat.seatedHint", "hotSeat.sealed")


def _admin() -> str:
    return (WWW / "js/admin.js").read_text(encoding="utf-8")


def _notice_body() -> str:
    source = _admin()
    start = source.index("function setDetourNotice(")
    return source[start : source.index("\n    }", start)]


def _bundle(lang: str) -> dict:
    return json.loads((WWW / f"i18n/{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", PLAYER_KEYS)
def test_the_host_notice_does_not_borrow_a_player_key(key: str) -> None:
    """Words close enough to reuse is exactly how this bug happened."""
    assert key not in _notice_body(), (
        f"the host notice still resolves {key}, which is written for "
        "another screen"
    )


@pytest.mark.parametrize("key", HOST_KEYS)
def test_the_host_notice_uses_its_own_strings(key: str) -> None:
    assert f"hotSeat.{key}" in _notice_body(), (
        f"the host notice has no hotSeat.{key}"
    )


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", HOST_KEYS)
def test_every_language_carries_the_host_strings(lang: str, key: str) -> None:
    """A key missing in one language is a key that falls back to English."""
    assert key in _bundle(lang).get("hotSeat", {}), (
        f"{lang}.json has no hotSeat.{key}"
    )


@pytest.mark.parametrize("lang", LANGS)
def test_the_seated_notice_names_the_player_in_the_chair(lang: str) -> None:
    """The host is watching a person, not a role."""
    assert "{name}" in _bundle(lang)["hotSeat"]["hostSeated"], (
        f"{lang} hotSeat.hostSeated does not name anybody"
    )


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", HOST_KEYS)
def test_the_host_is_never_addressed_as_the_seat_holder(lang: str, key: str) -> None:
    """Second person here means the text was written for somebody else."""
    text = _bundle(lang)["hotSeat"][key]
    second_person = {
        "en": r"\bYou\b|\byour\b",
        "de": r"\bDu\b|\bdein",
        "es": r"\bRespondes\b|\btu apuesta\b",
    }[lang]
    assert not re.search(second_person, text, re.IGNORECASE), (
        f"{lang} hotSeat.{key} addresses the reader: {text!r}"
    )


@pytest.mark.parametrize("lang", LANGS)
def test_the_settled_notice_does_not_claim_the_bids_are_still_hidden(
    lang: str,
) -> None:
    """It is shown at the result stage, beside a working Next Question."""
    settled = _bundle(lang)["hotSeat"]["hostSettled"]
    sealed = _bundle(lang)["hotSeat"]["sealed"]
    assert settled != sealed, f"{lang} hostSettled is still the auction line"


def test_the_notice_is_told_who_holds_the_chair() -> None:
    """The name is in the snapshot's hot_seat block; it has to be passed on."""
    source = _admin()
    call = re.search(r"setDetourNotice\(msg\.phase[^)]*\)", source)
    assert call, "setDetourNotice is no longer called from the phase switch"
    assert "hot_seat" in call.group(0), (
        "setDetourNotice is still called with the phase alone, so it cannot "
        f"name the seat holder: {call.group(0)}"
    )


def test_the_english_fallback_can_actually_be_reached() -> None:
    """`_t()` returns the key itself with no bundle loaded, so `|| fallback`
    never fired — the host would have read "hotSeat.hostSeated".

    #832 gave the comparison a name (`_tOr`) because the Hot Seat's live
    handlers need the same three lines six more times, so this now checks the
    two halves it is made of: the notice resolves its strings through the
    helper, and the helper picks the fallback on key-identity rather than on
    falsiness.
    """
    body = _notice_body()
    assert "_tOr(entry[0]" in body, (
        "the host notice no longer resolves its strings through _tOr; "
        f"whatever it does instead has to reach the fallback: {body}"
    )

    source = _admin()
    helper = source[source.index("function _tOr(") :]
    helper = helper[: helper.index("\n    }")]
    assert re.search(r"!==\s*key", helper), (
        "the fallback is still selected on falsiness, which _t() never returns"
    )
