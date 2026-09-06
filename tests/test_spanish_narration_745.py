"""Spanish narration + the localized, toggleable streak shout (#745).

Quizify shipped twelve Spanish question packs and a full Spanish UI, but
``tts_phrases`` only knew ``de``/``en`` — a Spanish evening showed Spanish on
every screen and was narrated in English. Two more holes sat next to it: the
streak line was a hardcoded English f-string spoken into German games with no
per-event toggle of its own, and ``tts.speak`` was never told which language to
speak, so the phrase was handed to whatever voice the engine defaults to.

The suite covers all three: the phrase table, the announcer, and the admin
panel that has to expose the new toggle for it to be reachable.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game import tts_phrases  # noqa: E402
from custom_components.quizify.game.questions import Answer, Question  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.house_settings import HouseSettings  # noqa: E402
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402

_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeTTSEntity:
    def __init__(self, supported: list[str]) -> None:
        self.supported_languages = supported


class _FakeEntityComponent:
    def __init__(self, entities: dict[str, _FakeTTSEntity]) -> None:
        self._entities = entities

    def get_entity(self, entity_id: str):  # noqa: ANN201
        return self._entities.get(entity_id)


class _FakeHass:
    """Records tts.speak calls, and can advertise engine languages like HA."""

    def __init__(self, supported: list[str] | None = None) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.states = MagicMock()
        self.states.get = lambda eid: MagicMock(state="on")
        self.services = MagicMock()
        self.data: dict = {}
        if supported is not None:
            self.data["tts"] = _FakeEntityComponent(
                {"tts.cloud": _FakeTTSEntity(supported)}
            )

        async def _async_call(domain, service, data, blocking=False):  # noqa: ANN001
            self.calls.append((domain, service, dict(data)))

        self.services.async_call = _async_call

    def async_create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def game(tmp_path):
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")


def _announcer(hass, game) -> QuizifyTTSAnnouncer:
    return QuizifyTTSAnnouncer(
        hass=hass,
        tts_entity_id="tts.cloud",
        media_player_entity_id="media_player.kitchen",
        game_state=game,
    )


def _configure(t: QuizifyTTSAnnouncer, **overrides) -> None:
    cfg = {
        "enabled": True,
        "announce_question": True,
        "announce_options": True,
        "announce_reveal": True,
        "announce_standings": True,
        "announce_join": True,
        "announce_countdown": True,
        "announce_milestone": True,
    }
    cfg.update(overrides)
    t.configure(**cfg)


def _last_call(hass) -> dict:
    assert hass.calls, "expected a tts.speak call"
    domain, service, data = hass.calls[-1]
    assert (domain, service) == ("tts", "speak")
    return data


def _last_message(hass) -> str:
    return _last_call(hass)["message"]


# ---------------------------------------------------------------------------
# The phrase table
# ---------------------------------------------------------------------------


class TestSpanishPhraseTable:
    def test_spanish_is_a_narration_language(self) -> None:
        assert "es" in tts_phrases.SUPPORTED_LANGUAGES
        assert tts_phrases.normalize_language("es") == "es"

    def test_all_three_languages_carry_the_same_keys(self) -> None:
        keys = {
            lang: set(table)
            for lang, table in tts_phrases._PHRASES.items()
        }
        assert set(keys) == {"de", "en", "es"}
        assert keys["es"] == keys["en"] == keys["de"]

    @pytest.mark.parametrize(
        ("key", "kwargs", "expected"),
        [
            (
                "question",
                {"round": 3, "total": 10, "text": "¿Capital?"},
                "Pregunta 3 de 10: ¿Capital?",
            ),
            ("answer", {"answer": "Madrid"}, "La respuesta correcta es Madrid."),
            ("countdown", {"seconds": 5}, "¡Quedan 5 segundos!"),
        ],
    )
    def test_spanish_renders_in_spanish(self, key, kwargs, expected) -> None:
        assert tts_phrases.phrase("es", key, **kwargs) == expected

    def test_no_spanish_phrase_leaks_english(self) -> None:
        """Every Spanish template must differ from the English fallback."""
        english = tts_phrases._PHRASES["en"]
        spanish = tts_phrases._PHRASES["es"]
        shared = [k for k in english if english[k] == spanish[k]]
        assert shared == [], f"still English in the es table: {shared}"

    def test_spanish_joins_names_with_y(self) -> None:
        assert tts_phrases.join_names("es", ["Marco", "Ana"]) == "Marco y Ana"

    def test_spanish_swaps_y_for_e_before_an_i_sound(self) -> None:
        """"Marco e Inés", not "Marco y Inés"."""
        assert tts_phrases.join_names("es", ["Marco", "Inés"]) == "Marco e Inés"
        # "hie-" is pronounced /je/ and keeps "y".
        assert tts_phrases.join_names("es", ["Ana", "Hierro"]) == "Ana y Hierro"

    def test_unknown_language_still_falls_back_to_english(self) -> None:
        assert tts_phrases.phrase("fr", "final_round") == "Final round!"


# ---------------------------------------------------------------------------
# The announcer speaks the game's language
# ---------------------------------------------------------------------------


class TestAnnouncerSpeaksSpanish:
    @pytest.mark.asyncio
    async def test_question_is_narrated_in_spanish(self, game) -> None:
        game.language = "es"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        question = Question(
            id="q1",
            question="¿Capital de España?",
            answers=[Answer(text="Madrid", correct=True)],
        )
        t.announce_question(question, 3, 10, ["Madrid", "Roma", "París"])
        await asyncio.sleep(0)
        msg = _last_message(hass)
        assert "Pregunta 3 de 10: ¿Capital de España?" in msg
        assert "Las opciones son:" in msg
        assert "Question" not in msg

    @pytest.mark.asyncio
    async def test_join_is_narrated_in_spanish(self, game) -> None:
        game.language = "es"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_join("Lucía")
        await asyncio.sleep(0)
        assert _last_message(hass) == "¡Lucía se une a la partida!"


# ---------------------------------------------------------------------------
# The lifecycle lines that used to be hardcoded English
# ---------------------------------------------------------------------------


class TestLifecycleLinesAreLocalized:
    @pytest.mark.asyncio
    async def test_game_start_speaks_the_game_language(self, game) -> None:
        game.language = "es"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        game.phase = GamePhase.QUESTION_ACTIVE
        game.round = 1
        t._on_state_changed()
        await asyncio.sleep(0)
        assert _last_message(hass) == "¡Empieza Quizify! ¡Mucha suerte!"

    @pytest.mark.asyncio
    async def test_final_round_speaks_german_in_a_german_game(self, game) -> None:
        game.language = "de"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t._last_phase = GamePhase.ANSWER_REVEAL
        game.phase = GamePhase.QUESTION_ACTIVE
        game.round = 5
        game.total_rounds = 5
        t._on_state_changed()
        await asyncio.sleep(0)
        assert _last_message(hass) == "Letzte Runde!"

    @pytest.mark.asyncio
    async def test_winner_line_speaks_the_game_language(self, game) -> None:
        game.language = "es"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t._last_phase = GamePhase.ANSWER_REVEAL
        game.phase = GamePhase.FINALE
        game.add_player("Lucía", MagicMock())
        game.get_player("Lucía").score = 420
        t._on_state_changed()
        await asyncio.sleep(0)
        msg = _last_message(hass)
        assert msg == "¡Se acabó el juego! Gana Lucía con 420 puntos."
        assert "winner" not in msg


# ---------------------------------------------------------------------------
# The streak shout: localized, and toggleable like its six siblings
# ---------------------------------------------------------------------------


class TestMilestoneIsLocalizedAndToggleable:
    @pytest.mark.asyncio
    async def test_streak_is_not_english_in_a_german_game(self, game) -> None:
        game.language = "de"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_milestone("Anna", 3)
        await asyncio.sleep(0)
        msg = _last_message(hass)
        assert msg == "Anna hat 3 in Folge richtig!"
        assert "streak" not in msg

    @pytest.mark.asyncio
    async def test_streak_is_spanish_in_a_spanish_game(self, game) -> None:
        game.language = "es"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_milestone("Lucía", 3)
        await asyncio.sleep(0)
        assert _last_message(hass) == "¡Lucía lleva 3 seguidas!"

    @pytest.mark.asyncio
    async def test_the_on_fire_variant_is_localized_too(self, game) -> None:
        game.language = "es"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_milestone("Lucía", 12)
        await asyncio.sleep(0)
        assert _last_message(hass) == "¡Lucía está imparable: 12 seguidas!"

    @pytest.mark.asyncio
    async def test_its_own_toggle_silences_it(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_milestone=False)
        t.announce_milestone("Anna", 3)
        await asyncio.sleep(0)
        assert hass.calls == []

    @pytest.mark.asyncio
    async def test_the_other_toggles_do_not_silence_it(self, game) -> None:
        """The streak switch is independent — it is not the reveal switch."""
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_reveal=False, announce_standings=False)
        t.announce_milestone("Anna", 3)
        await asyncio.sleep(0)
        assert len(hass.calls) == 1

    def test_the_toggle_survives_an_options_reload(self, game) -> None:
        """Same reload continuity the six siblings get (#411 / #789)."""
        hass = _FakeHass()
        settings = HouseSettings(
            tts_entity="tts.cloud", media_player="media_player.kitchen"
        )
        t = QuizifyTTSAnnouncer(hass=hass, game_state=game, settings=settings)
        _configure(t, announce_milestone=False)
        settings.update_from_options({"tts_entity": "tts.cloud"})
        assert t._announce_milestone is False


# ---------------------------------------------------------------------------
# The engine has to be told which language to speak
# ---------------------------------------------------------------------------


class TestSpeakCarriesTheLanguage:
    @pytest.mark.asyncio
    async def test_language_rides_the_speak_call(self, game) -> None:
        game.language = "es"
        hass = _FakeHass(supported=["de", "en", "es"])
        t = _announcer(hass, game)
        _configure(t)
        t.announce_join("Lucía")
        await asyncio.sleep(0)
        assert _last_call(hass)["language"] == "es"

    @pytest.mark.asyncio
    async def test_a_regional_engine_gets_its_own_tag(self, game) -> None:
        """HA matches by strict membership, so "es" must become "es-ES"."""
        game.language = "es"
        hass = _FakeHass(supported=["de-DE", "en-US", "es-ES", "es-MX"])
        t = _announcer(hass, game)
        _configure(t)
        t.announce_join("Lucía")
        await asyncio.sleep(0)
        assert _last_call(hass)["language"] == "es-ES"

    @pytest.mark.asyncio
    async def test_regional_pick_is_deterministic_without_a_preference(
        self, game
    ) -> None:
        game.language = "es"
        hass = _FakeHass(supported=["es-MX", "es-AR"])
        t = _announcer(hass, game)
        _configure(t)
        t.announce_join("Lucía")
        await asyncio.sleep(0)
        assert _last_call(hass)["language"] == "es-AR"

    @pytest.mark.asyncio
    async def test_language_is_omitted_when_the_engine_cannot_speak_it(
        self, game
    ) -> None:
        """Silence is worse than the wrong accent — tts.speak raises on an
        unsupported tag, so we let the engine keep its default."""
        game.language = "es"
        hass = _FakeHass(supported=["de-DE", "en-US"])
        t = _announcer(hass, game)
        _configure(t)
        t.announce_join("Lucía")
        await asyncio.sleep(0)
        assert "language" not in _last_call(hass)

    @pytest.mark.asyncio
    async def test_language_is_omitted_when_the_engine_is_unknown(
        self, game
    ) -> None:
        game.language = "es"
        hass = _FakeHass()  # no tts entity component at all
        t = _announcer(hass, game)
        _configure(t)
        t.announce_join("Lucía")
        await asyncio.sleep(0)
        assert "language" not in _last_call(hass)


# ---------------------------------------------------------------------------
# The admin panel has to expose the new toggle
# ---------------------------------------------------------------------------


class TestAdminPanelExposesTheToggle:
    def test_the_checkbox_is_in_the_setup_step(self) -> None:
        html = (_WWW / "admin.html").read_text("utf-8")
        assert 'id="tts-announce-milestone"' in html
        assert 'data-i18n="setup.tts.milestone"' in html
        # Nested under the master switch with its six siblings.
        children = html.split('id="tts-children"', 1)[1]
        assert 'id="tts-announce-milestone"' in children.split("</div>", 1)[0]

    def test_admin_js_reads_persists_and_pushes_it(self) -> None:
        js = (_WWW / "js" / "admin.js").read_text("utf-8")
        assert "announce_milestone: true" in js
        assert "tts-announce-milestone" in js
        assert "saved.announce_milestone" in js
        assert "_ttsEls.milestone" in js

    def test_every_bundle_has_the_label(self) -> None:
        for lang in ("en", "de", "es"):
            bundle = json.loads(
                (_WWW / "i18n" / f"{lang}.json").read_text("utf-8")
            )
            assert bundle["setup"]["tts"]["milestone"], lang

    def test_the_server_forwards_the_toggle(self) -> None:
        ws = (
            _REPO_ROOT
            / "custom_components"
            / "quizify"
            / "server"
            / "websocket.py"
        ).read_text("utf-8")
        assert 'announce_milestone=bool(tts.get("announce_milestone", True))' in ws
