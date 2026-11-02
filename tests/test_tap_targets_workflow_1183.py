"""The live tap-target check runs in CI, as a warning first (#1183).

Decision 5 of the interaction review: the Playwright measurement goes into CI
as a warning, and becomes a failure once the list is worked off. It lives in a
workflow of its own: every job in test.yml gates on purpose
(test_ci_ha_matrix_740.test_no_job_is_advisory), so an advisory leg there is
not an option.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "tap-targets.yml"
_SCRIPT = _REPO / "tests" / "ui" / "tap_targets.cjs"
_ALLOW = _REPO / "tests" / "ui" / "tap_targets_allow.json"


def test_the_check_has_its_own_non_blocking_workflow() -> None:
    text = _WORKFLOW.read_text("utf-8")
    assert re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE)
    assert 'TAP_TARGETS_STRICT: "0"' in text, "warning first (decision 5)"
    assert "node tests/ui/tap_targets.cjs" in text
    assert "scripts/dev_server.py" in text
    assert "tap-targets" not in (_REPO / ".github" / "workflows" / "test.yml").read_text("utf-8")


def test_the_script_only_fails_when_strict() -> None:
    src = _SCRIPT.read_text("utf-8")
    assert "process.exit(STRICT && (findings.length || controls < 30) ? 1 : 0)" in src
    assert "::warning" in src
    assert "MIN_TARGET = 44" in src and "MIN_GAP = 8" in src


def test_every_exception_says_why() -> None:
    allow = json.loads(_ALLOW.read_text("utf-8"))
    for key, why in allow.items():
        if key.startswith("_"):
            continue
        reasons = why.values() if isinstance(why, dict) else [why]
        assert all(isinstance(r, str) and len(r) > 10 for r in reasons), key
        if isinstance(why, dict):
            assert set(why) <= {"target", "gap", "active", "focus"}, key
