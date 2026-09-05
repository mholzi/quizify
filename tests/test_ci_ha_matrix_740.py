"""The CI matrix must keep testing both ends of the supported range (#740).

A version matrix is not a thing you build once. The cap this issue removed —
``homeassistant<2026.3`` — was itself added for a good reason (Python 3.13 could
not run anything newer), and then outlived it by seven months without anybody
noticing, because a cap looks the same on the day it is right and on the day it
is wrong. The same is true of the floor leg: it is one ``continue-on-error:``
away from existing without gating anything, which is how #1593 rotted.

So the shape is asserted here, in the suite that runs on every PR, rather than
left to whoever next edits the workflow:

* the current-HA leg tracks upstream (no upper bound on the harness),
* the floor leg is pinned (a floor that drifts is not a floor),
* both legs gate — nothing in the workflow is advisory,
* the check named ``pytest`` still exists, because that is the name the repo
  merges on.

None of this proves the suite passes on either version. That is what the two
jobs themselves are for; this file only keeps them honest about what they are.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
CURRENT_REQS = REPO_ROOT / "requirements_test.txt"
FLOOR_REQS = REPO_ROOT / "requirements_test_ha_floor.txt"

_HARNESS = "pytest-homeassistant-custom-component"


def _requirement_lines(path: Path) -> list[str]:
    """Every non-comment, non-blank line, stripped."""
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_the_current_leg_is_not_capped() -> None:
    """No upper bound anywhere near Home Assistant in the tracking file.

    ``<``, ``<=`` and ``==`` all freeze it; ``~=`` freezes the minor. The
    harness pins an exact core release transitively, so pinning the harness
    pins Home Assistant just as effectively as naming it.
    """
    for line in _requirement_lines(CURRENT_REQS):
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
        if name in {"homeassistant", _HARNESS}:
            assert line == name, (
                f"{CURRENT_REQS.name} pins {name!r} as {line!r}. This file is the "
                "leg that is supposed to follow Home Assistant upstream; a bound "
                "here is how the matrix froze on 2026.2 (#740)."
            )


def test_the_floor_leg_is_pinned_exactly() -> None:
    """The opposite rule, for the opposite reason: a floor that moves is not one."""
    pins = [
        line for line in _requirement_lines(FLOOR_REQS) if line.startswith(_HARNESS)
    ]
    assert len(pins) == 1, f"{FLOOR_REQS.name} must name the harness exactly once"
    assert re.fullmatch(rf"{re.escape(_HARNESS)}==\d+\.\d+\.\d+", pins[0]), (
        f"{FLOOR_REQS.name} must pin the harness to an exact version, got {pins[0]!r}"
    )


def test_both_legs_are_wired_into_the_workflow() -> None:
    """Both requirement files are actually installed by a job."""
    workflow = WORKFLOW.read_text()
    assert "requirements: requirements_test.txt" in workflow
    assert "requirements: requirements_test_ha_floor.txt" in workflow
    assert "pip install -r ${{ matrix.requirements }}" in workflow


def test_the_required_check_keeps_its_name() -> None:
    """Branch protection and the merge habit both key off ``pytest``.

    Letting GitHub derive matrix check names would rename it to
    ``pytest (3.14, requirements_test.txt)`` and orphan the requirement.
    """
    workflow = WORKFLOW.read_text()
    assert "name: ${{ matrix.name }}" in workflow
    assert re.search(r"^\s*- name: pytest$", workflow, re.MULTILINE)
    assert re.search(r"^\s*- name: pytest-ha-floor$", workflow, re.MULTILINE)


def test_no_job_is_advisory() -> None:
    """A job that cannot fail the build is a job that stopped working quietly."""
    workflow = WORKFLOW.read_text()
    assert "continue-on-error" not in workflow
