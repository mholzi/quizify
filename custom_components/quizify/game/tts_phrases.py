"""Localized TTS announcement phrases for Quizify (issue #281).

Spoken announcements are composed here and rendered per language. Quizify's
default game language is German, so the German strings are complete; English is
the *authoritative* fallback — any missing language or key falls back to
English, and a translated template that fails to format also falls back to
English so a malformed string can never break a live game.

Templates are ``str.format`` strings; placeholders like ``{name}`` are filled
by the caller. ``{names}`` may hold a single name or several joined by
``join_names`` (the language's "and" word).

Keys: question-start, answer-options readout, reveal answer, who-got-it,
standings/leader-change, player-joined, and the time-running-out countdown.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

# Languages we ship spoken phrases for. Quizify games run de/en (and some
# packs es), but only de+en are translated here; everything else falls back to
# English via ``normalize_language``.
SUPPORTED_LANGUAGES = ("de", "en")

# Word joining names in a spoken list ("Marco and Anna" / "Marco und Anna").
_AND: dict[str, str] = {
    "en": "and",
    "de": "und",
}

# str.format templates per language. Keys must be identical across languages;
# English is the fallback for any missing language or key.
_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "question": "Question {round} of {total}: {text}",
        "options": "Your options are: {options}.",
        "answer": "The correct answer is {answer}.",
        "got_it_single": "{names} got it right.",
        "got_it_multi": "{names} got it right.",
        "nobody": "Nobody got it this round.",
        "leader_change": "{name} takes the lead!",
        "tie_at_top": "It's a tie at the top.",
        "player_joined": "{name} joined the game.",
        "countdown": "{seconds} seconds left!",
    },
    "de": {
        "question": "Frage {round} von {total}: {text}",
        "options": "Die Antwortmöglichkeiten sind: {options}.",
        "answer": "Die richtige Antwort ist {answer}.",
        "got_it_single": "{names} hatte recht.",
        "got_it_multi": "{names} hatten recht.",
        "nobody": "Diese Runde hatte niemand richtig.",
        "leader_change": "{name} übernimmt die Führung!",
        "tie_at_top": "Gleichstand an der Spitze.",
        "player_joined": "{name} ist dabei!",
        "countdown": "Noch {seconds} Sekunden!",
    },
}


def normalize_language(language: str | None) -> str:
    """Return a language we have phrases for, defaulting to English."""
    if language and language in _PHRASES:
        return language
    return DEFAULT_LANGUAGE


def phrase(language: str | None, key: str, **kwargs: object) -> str:
    """Render a localized announcement phrase, falling back to English.

    Falls back to the English template when the language/key is missing, and
    again if a (translated) template fails to format — a malformed string must
    never break a live game.
    """
    lang = normalize_language(language)
    template = _PHRASES.get(lang, {}).get(key)
    if template is None:
        template = _PHRASES[DEFAULT_LANGUAGE][key]
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return _PHRASES[DEFAULT_LANGUAGE][key].format(**kwargs)


def join_names(language: str | None, names: list[str]) -> str:
    """Join names for speech ("Marco and Anna" / "Marco und Anna")."""
    lang = normalize_language(language)
    and_word = _AND.get(lang, _AND[DEFAULT_LANGUAGE])
    return f" {and_word} ".join(names)


def spoken_number(language: str | None, value: object) -> str:
    """Render a number for speech.

    Quizify does not ship ``num2words`` as a dependency (not in the manifest),
    so this is a plain digit rendering — neural TTS engines read Quizify's
    small round/total counts fine as digits. Kept as a function so a future
    num2words upgrade has a single seam to plug into.
    """
    if value is None:
        return ""
    return str(value)
