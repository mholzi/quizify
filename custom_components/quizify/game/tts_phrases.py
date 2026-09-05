"""Localized TTS announcement phrases for Quizify (issue #281).

Spoken announcements are composed here and rendered per language. Quizify's
default game language is German; German, English and Spanish are all complete,
and English is the *authoritative* fallback — any missing language or key falls
back to English, and a translated template that fails to format also falls back
to English so a malformed string can never break a live game.

Spanish was added in #745: the integration already shipped twelve Spanish
question packs and a full Spanish UI, so a Spanish evening showed Spanish on
every screen and was narrated in English. The three sets are held to the same
bar — a host talking in the room, not a translated string table.

Templates are ``str.format`` strings; placeholders like ``{name}`` are filled
by the caller. ``{names}`` may hold a single name or several joined by
``join_names`` (the language's "and" word).

Keys: question-start, answer-options readout, reveal answer, who-got-it,
standings/leader-change, player-joined, the time-running-out countdown, the
game lifecycle lines (start / final round / game over + winner) and the
streak-milestone shout.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

# Languages we ship spoken phrases for. These mirror the languages the UI and
# the question packs ship in (de/en/es) so a game is narrated in the language
# it is played in; everything else falls back to English via
# ``normalize_language``.
SUPPORTED_LANGUAGES = ("de", "en", "es")

# Word joining names in a spoken list ("Marco and Anna" / "Marco und Anna" /
# "Marco y Anna"). Spanish swaps "y" for "e" before an i- sound — see
# ``join_names``.
_AND: dict[str, str] = {
    "en": "and",
    "de": "und",
    "es": "y",
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
        "game_start": "Quizify starting. Good luck!",
        "final_round": "Final round!",
        "game_over": "Game over.",
        "game_over_winner": (
            "Game over. The winner is {name} with {score} points!"
        ),
        "milestone_streak": "{name} hit a {streak}-streak!",
        "milestone_fire": "{name} is on fire — {streak} in a row!",
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
        "game_start": "Quizify geht los. Viel Glück!",
        "final_round": "Letzte Runde!",
        "game_over": "Das Spiel ist vorbei.",
        "game_over_winner": (
            "Das Spiel ist vorbei. Gewonnen hat {name} mit {score} Punkten!"
        ),
        "milestone_streak": "{name} hat {streak} in Folge richtig!",
        "milestone_fire": "{name} ist nicht zu stoppen — {streak} in Folge!",
    },
    "es": {
        "question": "Pregunta {round} de {total}: {text}",
        "options": "Las opciones son: {options}.",
        "answer": "La respuesta correcta es {answer}.",
        "got_it_single": "{names} lo ha acertado.",
        "got_it_multi": "{names} lo han acertado.",
        "nobody": "Esta ronda no la ha acertado nadie.",
        "leader_change": "¡{name} se pone en cabeza!",
        "tie_at_top": "Empate en cabeza.",
        "player_joined": "¡{name} se une a la partida!",
        "countdown": "¡Quedan {seconds} segundos!",
        "game_start": "¡Empieza Quizify! ¡Mucha suerte!",
        "final_round": "¡Última ronda!",
        "game_over": "Se acabó el juego.",
        "game_over_winner": (
            "¡Se acabó el juego! Gana {name} con {score} puntos."
        ),
        "milestone_streak": "¡{name} lleva {streak} seguidas!",
        "milestone_fire": "¡{name} está imparable: {streak} seguidas!",
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


def _spanish_and(next_name: str) -> str:
    """Spanish "y", or "e" when the following name starts with an i- sound.

    "Marco e Inés", not "Marco y Inés". The exception to the exception is a
    "hie-" start ("hierro"), which keeps "y" because it is pronounced /je/.
    """
    word = next_name.strip().lower()
    if word.startswith("hie"):
        return "y"
    if word.startswith(("i", "hi")):
        return "e"
    return "y"


def join_names(language: str | None, names: list[str]) -> str:
    """Join names for speech ("Marco and Anna" / "Marco und Anna")."""
    lang = normalize_language(language)
    if lang == "es":
        # The conjunction depends on the word that follows it, so Spanish is
        # joined pairwise instead of with a single separator.
        if not names:
            return ""
        joined = names[0]
        for name in names[1:]:
            joined = f"{joined} {_spanish_and(name)} {name}"
        return joined
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
