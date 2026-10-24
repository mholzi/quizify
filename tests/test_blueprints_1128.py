"""The shipped automation blueprints (#1128) parse and only use real names."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from custom_components.quizify import game_events

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT_DIR = ROOT / "blueprints" / "automation" / "mholzi"
RAW_BASE = "https://raw.githubusercontent.com/mholzi/quizify/main/blueprints/automation/mholzi/"
FILES = sorted(BLUEPRINT_DIR.glob("*.yaml"))
EVENTS = {v for k, v in vars(game_events).items() if k.startswith("EVENT_")}


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!input", lambda loader, node: f"input:{node.value}")


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), _Loader)


def test_three_blueprints_shipped() -> None:
    assert len(FILES) == 3


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_blueprint_metadata(path: Path) -> None:
    bp = _load(path)["blueprint"]
    assert bp["domain"] == "automation"
    assert bp["source_url"] == RAW_BASE + path.name
    for name, spec in bp["input"].items():
        assert "selector" in spec, f"input {name} has no selector"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_events_and_services_exist(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for event in re.findall(r"event_type:\s*(\S+)", text):
        assert event in EVENTS, f"{event} is not defined in game_events.py"
    services = yaml.safe_load(
        (ROOT / "custom_components/quizify/services.yaml").read_text()
    )
    for action in re.findall(r"action:\s*quizify\.(\w+)", text):
        assert action in services
    if "quizify.start_game" in text:
        fields = services["start_game"]["fields"]
        for key in ("preset", "num_rounds"):
            assert key in fields


def test_payload_keys_documented() -> None:
    doc = game_events.__doc__ or ""
    flash = (BLUEPRINT_DIR / "quizify_flash_lights_on_reveal.yaml").read_text()
    winner = (BLUEPRINT_DIR / "quizify_announce_winner.yaml").read_text()
    for key in re.findall(r"trigger\.event\.data\.(\w+)", flash + winner):
        assert key in doc, f"payload key {key} is not in the game_events docstring"
