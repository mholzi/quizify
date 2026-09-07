"""The CI workflows must not drift out of support unwatched (#888).

Three `actions/*` steps sat three majors behind for long enough that GitHub's
own deprecation shim was carrying them: every v1.16.0 run printed "Node.js 20 is
deprecated. The following actions target Node.js 20 but are being forced to run
on Node.js 24". The worker-contract leg installed Node 20 itself, four months
after it went end-of-life. `ruff` was eleven minor versions behind.

None of that is a bug anybody could see. It is the absence of anything that
looks — so the fix is a `.github/dependabot.yml`, and these are the assertions
that keep the arrangement in place:

* nothing may go *back* below the majors this issue moved to,
* the version numbers dependabot needs to read must stay somewhere it can read
  them — a `pip install "ruff==…"` inside a shell command is invisible to it,
* the floor pin must stay on dependabot's ignore list, because moving it moves
  the minimum Home Assistant version this project advertises,
* and both workflows ask for a read-only token, since neither writes anything.

Text assertions rather than a YAML parse: PyYAML is not in
``requirements_test.txt``, and a guard that skips itself when a dependency is
missing is the failure mode #740 was about.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TEST_YML = WORKFLOWS / "test.yml"
VALIDATE_YML = WORKFLOWS / "validate.yml"
DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"
LINT_REQS = REPO_ROOT / "requirements_lint.txt"

# Where this issue left each action. A floor, not a pin: bumping past it is the
# point, dropping below it is the regression.
MIN_MAJOR = {
    "actions/checkout": 7,
    "actions/setup-python": 7,
    "actions/setup-node": 7,
}

# Node 20 reached end of life on 2026-04-30.
MIN_NODE = 22


def _workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def test_no_action_slips_back_below_a_supported_major() -> None:
    """Every pinned `actions/*` step, across every workflow file.

    Only the three this issue moved carry a floor. An action added later is
    dependabot's business now; this is here so a copy-pasted `@v4` from an old
    example cannot quietly land one of these back on the shim.
    """
    seen = set()
    for path in _workflows():
        for name, major in re.findall(
            r"uses:\s*(actions/[\w-]+)@v(\d+)", path.read_text("utf-8")
        ):
            if name not in MIN_MAJOR:
                continue
            assert int(major) >= MIN_MAJOR[name], (
                f"{path.name} pins {name}@v{major}; v{MIN_MAJOR[name]} is the "
                "floor #888 established. Older majors run on a Node runtime "
                "GitHub only shims."
            )
            seen.add(name)
    assert seen == set(MIN_MAJOR), (
        f"the scan did not find every guarded action, only {sorted(seen)}"
    )


def test_the_worker_contract_leg_runs_a_supported_node() -> None:
    """`node --check` on an end-of-life runtime proves less than it looks."""
    versions = re.findall(r'node-version:\s*"(\d+)"', TEST_YML.read_text("utf-8"))
    assert versions, "the contract check must pin a node version (#328)"
    for version in versions:
        assert int(version) >= MIN_NODE, (
            f"node {version} is end-of-life; {MIN_NODE} is the floor (#888)"
        )


def test_the_tool_pins_live_where_dependabot_can_read_them() -> None:
    """A version typed into a `run:` line is invisible to every updater there is.

    This is the mechanism, not a preference: dependabot's `pip` ecosystem reads
    requirements files and its `github-actions` ecosystem reads `uses:` lines.
    Neither reads shell.
    """
    workflow = TEST_YML.read_text("utf-8")
    assert not re.search(r"pip install[^\n]*ruff==", workflow), (
        "ruff's pin belongs in requirements_lint.txt, not inline in a run step"
    )
    assert not re.search(r"pip install[^\n]*mypy==", workflow), (
        "mypy's pin belongs in requirements_lint.txt, not inline in a run step"
    )
    assert "requirements_lint.txt" in workflow

    reqs = LINT_REQS.read_text("utf-8")
    for tool in ("ruff", "mypy"):
        assert re.search(rf"^{tool}==\d+\.\d+\.\d+$", reqs, re.MULTILINE), (
            f"{tool} must stay pinned exactly — an unpinned lint gate reds "
            "unrelated pull requests and then gets ignored"
        )


def test_dependabot_watches_both_ecosystems_weekly() -> None:
    assert DEPENDABOT.is_file(), (
        "without this file nothing proposes an update, which is how all of the "
        "above happened"
    )
    config = DEPENDABOT.read_text("utf-8")
    for ecosystem in ("github-actions", "pip"):
        assert f"package-ecosystem: {ecosystem}" in config
    assert config.count("interval: weekly") >= 2


def test_the_advertised_ha_floor_is_not_dependabot_maintained() -> None:
    """`requirements_test_ha_floor.txt` is frozen on purpose (#740).

    Its single pin is what holds the harness at Home Assistant 2024.12.5 — the
    minimum hacs.json and README:447 promise. A bump would move that promise
    with no issue, no release note and a still-green
    ``test_ci_ha_matrix_740.py``, which only checks that the pin is *exact*.
    """
    config = DEPENDABOT.read_text("utf-8")
    assert 'dependency-name: "pytest-homeassistant-custom-component"' in config
    ignore_at = config.find("ignore:")
    assert ignore_at != -1
    assert config.find("pytest-homeassistant-custom-component", ignore_at) > ignore_at


def test_both_workflows_ask_for_a_read_only_token() -> None:
    """Nothing here pushes, comments or publishes."""
    for path in (TEST_YML, VALIDATE_YML):
        text = path.read_text("utf-8")
        assert re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE), (
            f"{path.name} has no top-level read-only permissions block, so it "
            "inherits whatever the repository default grants"
        )
