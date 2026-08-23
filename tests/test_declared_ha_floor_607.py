"""The declared HA minimum must not drift below what the code actually needs (#607).

`hacs.json` is metadata: nothing in the suite executes it, and CI runs against a
current core, so a floor that is too low stays green forever while breaking every
install below it. This test is the only thing standing between that field and an
`ImportError` on a stranger's machine.

The three features that set the floor, each verified against the source when this
test was written:

* ``ConfigFlowResult`` imported from ``homeassistant.config_entries``  (HA 2024.4)
* ``StaticPathConfig`` / ``async_register_static_paths``               (HA 2024.6)
* the automatic ``OptionsFlow.config_entry`` property, which
  ``QuizifyOptionsFlow`` relies on instead of stashing the entry itself (HA 2024.11/12)

The declared floor is deliberately rounded up to 2024.12.0 rather than shaved to
the last version that happens to work. If you lower it, one of the three above has
to be gone from the code first.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DECLARED_FLOOR = (2024, 12, 0)


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def test_hacs_json_declares_the_real_minimum() -> None:
    """HACS shows this number to users before they install."""
    hacs = json.loads((REPO_ROOT / "hacs.json").read_text())
    assert _version_tuple(hacs["homeassistant"]) >= DECLARED_FLOOR


def test_readme_agrees_with_hacs_json() -> None:
    """Two places state the requirement; a reader who hits the stale one is misled."""
    hacs = json.loads((REPO_ROOT / "hacs.json").read_text())
    major, minor, _ = _version_tuple(hacs["homeassistant"])
    readme = (REPO_ROOT / "README.md").read_text()
    assert f"**Home Assistant** {major}.{minor}+" in readme
    assert f"Home%20Assistant-{major}.{minor}+-" in readme


def test_the_options_flow_still_leans_on_the_automatic_property() -> None:
    """Pins the reason for the floor, not just the number.

    ``QuizifyOptionsFlow`` never assigns ``self.config_entry``; it reads the
    property HA provides. That is the newest requirement of the three, so if this
    ever changes the floor is worth re-deriving.
    """
    source = (
        REPO_ROOT / "custom_components" / "quizify" / "config_flow.py"
    ).read_text()
    class_body = source.split("class QuizifyOptionsFlow")[1]
    assert "self.config_entry =" not in class_body
    assert "self.config_entry" in class_body
