"""Tests for the TTS-entities picker endpoint (#281).

``tts_entities_view`` backs the admin TTS dropdowns: it lists the available
``tts.*`` engines and ``media_player.*`` speakers from HA, each sorted by
friendly name. On the standalone dev server (no ``hass`` on the runtime) it
returns empty lists so the dropdowns degrade to the "configure in HA" fallback.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402
from custom_components.quizify.server.views import tts_entities_view  # noqa: E402


class _FakeState:
    def __init__(self, entity_id: str, friendly_name: str | None = None) -> None:
        self.entity_id = entity_id
        self.attributes = {}
        if friendly_name is not None:
            self.attributes["friendly_name"] = friendly_name


class _FakeStates:
    def __init__(self, by_domain: dict[str, list[_FakeState]]) -> None:
        self._by_domain = by_domain

    def async_all(self, domain: str) -> list[_FakeState]:
        return list(self._by_domain.get(domain, []))


class _FakeHass:
    def __init__(self, by_domain: dict[str, list[_FakeState]]) -> None:
        self.states = _FakeStates(by_domain)


class _HARuntime:
    """Runtime exposing ``hass`` like the real HARuntime."""

    def __init__(self, hass) -> None:  # noqa: ANN001
        self.hass = hass


class _StandaloneRuntime:
    """No ``hass`` attribute — mirrors the dev-server runtime."""


class _FakeRequest:
    def __init__(self, ctx) -> None:  # noqa: ANN001
        self.app = {APP_CTX_KEY: ctx}


def _call(ctx) -> dict:  # noqa: ANN001
    resp = asyncio.run(tts_entities_view(_FakeRequest(ctx)))  # type: ignore[arg-type]
    return json.loads(resp.body)


def test_returns_tts_and_media_players_sorted() -> None:
    hass = _FakeHass(
        {
            "tts": [
                _FakeState("tts.google_translate_say", "Google Translate"),
                _FakeState("tts.cloud", "Home Assistant Cloud"),
                _FakeState("tts.piper", "Piper"),
            ],
            "media_player": [
                _FakeState("media_player.living_room", "Living Room"),
                _FakeState("media_player.kitchen", "Kitchen"),
            ],
        }
    )
    ctx = SimpleNamespace(runtime=_HARuntime(hass))
    out = _call(ctx)

    # Sorted by friendly_name (case-insensitive).
    assert [e["friendly_name"] for e in out["tts"]] == [
        "Google Translate",
        "Home Assistant Cloud",
        "Piper",
    ]
    assert [e["entity_id"] for e in out["tts"]] == [
        "tts.google_translate_say",
        "tts.cloud",
        "tts.piper",
    ]
    assert [e["friendly_name"] for e in out["media_players"]] == [
        "Kitchen",
        "Living Room",
    ]


def test_friendly_name_falls_back_to_entity_id() -> None:
    """An entity with no friendly_name attribute uses its entity_id."""
    hass = _FakeHass({"tts": [_FakeState("tts.bare")], "media_player": []})
    ctx = SimpleNamespace(runtime=_HARuntime(hass))
    out = _call(ctx)
    assert out["tts"] == [{"entity_id": "tts.bare", "friendly_name": "tts.bare"}]
    assert out["media_players"] == []


def test_standalone_runtime_returns_empty_lists() -> None:
    """No hass on the runtime (dev server) → empty lists, no crash."""
    ctx = SimpleNamespace(runtime=_StandaloneRuntime())
    out = _call(ctx)
    assert out == {"tts": [], "media_players": []}
