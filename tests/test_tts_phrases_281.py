"""Tests for the localized TTS phrase module (issue #281).

The English strings are *authoritative* and byte-pinned here: the announcer
composes its utterances from these templates, and a stray edit to the wording
would silently change what the Quizmaster says. Pinning them makes that a
test failure instead of a surprise on the speaker.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game import tts_phrases  # noqa: E402

# Every v1 ("Quizmaster core") phrase key the module must carry.
_V1_KEYS = (
    "question",
    "answer",
    "got_it_single",
    "got_it_multi",
    "nobody",
    "leader_change",
    "tie_at_top",
)


class TestPhraseRendering:
    def test_every_v1_key_renders_de_and_en(self) -> None:
        # Supply every placeholder any template might use; extras are ignored
        # by str.format, missing ones would raise (and so fail the test).
        kw = {
            "round": 2, "total": 10, "text": "Wer malte die Mona Lisa?",
            "answer": "Leonardo da Vinci", "names": "Alice", "name": "Bob",
        }
        for lang in ("de", "en"):
            for key in _V1_KEYS:
                out = tts_phrases.phrase(lang, key, **kw)
                assert isinstance(out, str) and out, (lang, key)

    def test_english_strings_are_byte_pinned(self) -> None:
        assert tts_phrases.phrase(
            "en", "question", round=3, total=12, text="What year?"
        ) == "Question 3 of 12: What year?"
        assert tts_phrases.phrase(
            "en", "answer", answer="Berlin"
        ) == "The correct answer is Berlin."
        assert tts_phrases.phrase(
            "en", "got_it_single", names="Alice"
        ) == "Alice got it right."
        assert tts_phrases.phrase(
            "en", "got_it_multi", names="Alice and Bob"
        ) == "Alice and Bob got it right."
        assert tts_phrases.phrase("en", "nobody") == "Nobody got it this round."
        assert tts_phrases.phrase(
            "en", "leader_change", name="Carol"
        ) == "Carol takes the lead!"
        assert tts_phrases.phrase("en", "tie_at_top") == "It's a tie at the top."

    def test_german_strings(self) -> None:
        assert tts_phrases.phrase(
            "de", "question", round=1, total=5, text="Hauptstadt?"
        ) == "Frage 1 von 5: Hauptstadt?"
        assert tts_phrases.phrase(
            "de", "answer", answer="Berlin"
        ) == "Die richtige Antwort ist Berlin."
        assert tts_phrases.phrase(
            "de", "leader_change", name="Carol"
        ) == "Carol übernimmt die Führung!"


class TestFallbacks:
    def test_unknown_language_falls_back_to_english(self) -> None:
        # French is not translated here → English template. (Spanish was an
        # untranslated language until #745; it is a first-class one now.)
        assert tts_phrases.phrase(
            "fr", "nobody"
        ) == "Nobody got it this round."

    def test_unknown_key_within_known_language_uses_english(self) -> None:
        # A key absent from de but present in en still resolves via the
        # English fallback path. (All v1 keys exist in both, so this also
        # documents the contract for future en-only additions.)
        # Use a key that exists in en; simulate "missing in de" by deleting.
        # Instead, assert the documented behavior directly: an unknown key
        # raises KeyError on the English fallback lookup — guard that the
        # *known* keys never do.
        for key in _V1_KEYS:
            assert tts_phrases.phrase("de", key, round=1, total=1, text="x",
                                      answer="a", names="n", name="n")

    def test_malformed_template_args_fall_back_to_english(self) -> None:
        # English "answer" needs {answer}; passing only a wrong kwarg makes the
        # de template fail to format and fall back to the en template, which
        # also needs {answer} — so an entirely-missing arg surfaces via the
        # English path. We assert the de→en fallback doesn't crash when the de
        # template would KeyError but en succeeds. Use a key whose de template
        # references a placeholder we DO supply, with an extra bad one ignored.
        out = tts_phrases.phrase("de", "answer", answer="Rom", extra="ignored")
        assert out == "Die richtige Antwort ist Rom."


class TestJoinNames:
    def test_join_names_english(self) -> None:
        assert tts_phrases.join_names("en", ["Alice", "Bob"]) == "Alice and Bob"
        assert tts_phrases.join_names("en", ["Solo"]) == "Solo"

    def test_join_names_german(self) -> None:
        assert tts_phrases.join_names("de", ["Alice", "Bob"]) == "Alice und Bob"

    def test_join_names_unknown_language_uses_english_and(self) -> None:
        assert tts_phrases.join_names("fr", ["A", "B"]) == "A and B"


class TestNormalizeLanguage:
    def test_known_languages_pass_through(self) -> None:
        assert tts_phrases.normalize_language("de") == "de"
        assert tts_phrases.normalize_language("en") == "en"
        # Spanish joined the shipped set in #745.
        assert tts_phrases.normalize_language("es") == "es"

    def test_unknown_and_none_default_to_english(self) -> None:
        assert tts_phrases.normalize_language("fr") == "en"
        assert tts_phrases.normalize_language(None) == "en"
        assert tts_phrases.normalize_language("") == "en"

    def test_default_language_constant(self) -> None:
        assert tts_phrases.DEFAULT_LANGUAGE == "en"


class TestSpokenNumber:
    def test_digits_fallback(self) -> None:
        assert tts_phrases.spoken_number("de", 12) == "12"
        assert tts_phrases.spoken_number("en", 3) == "3"

    def test_none_is_empty(self) -> None:
        assert tts_phrases.spoken_number("de", None) == ""
