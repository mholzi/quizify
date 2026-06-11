"""Worker ⇄ integration pack-schema contract tests (#256).

The submission worker (``cf-workers/quizify-api.js``) and the integration
(``server/pack_submission.py``) each carry their own copy of the #179 community
pack validator. They MUST agree: a pack the integration accepts must not be
rejected at the worker (the last hop), and vice versa. The original review found
the worker validating the *wrong* schema, which would have rejected every
legitimate submission — and there were no tests, so it went unnoticed.

These tests pin the contract with a single shared fixture
(``tests/fixtures/community_pack.json``):

  * the Python side asserts ``validate_pack`` accepts the fixture;
  * a small JS check (``cf-workers/contract-check.mjs``) asserts the worker's
    ``validatePack`` accepts the same fixture and rejects a malformed one;
  * ``node --check`` proves the worker source still parses.

If either schema drifts, one of these fails CI.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import (  # noqa: E402
    SUBMIT_ANSWERS_PER_QUESTION,
)
from custom_components.quizify.server.pack_submission import (  # noqa: E402
    validate_pack,
)

_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "community_pack.json"
_WORKER = _REPO_ROOT / "cf-workers" / "quizify-api.js"
_CONTRACT_CHECK = _REPO_ROOT / "cf-workers" / "contract-check.mjs"


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_fixture_exists() -> None:
    assert _FIXTURE.exists(), "shared community_pack.json fixture is missing"


def test_python_validator_accepts_canonical_fixture() -> None:
    """The integration's validate_pack must accept the shared fixture — this is
    the canonical shape both sides pin to."""
    pack = _load_fixture()
    ok, errors = validate_pack(pack)
    assert ok, f"Python validate_pack rejected the canonical fixture: {errors}"
    assert errors == []


def test_fixture_matches_expected_shape() -> None:
    """Guard the fixture itself so a careless edit can't quietly weaken the
    contract both validators are pinned to."""
    pack = _load_fixture()
    assert isinstance(pack.get("name"), str) and pack["name"].strip()
    assert isinstance(pack.get("language"), str) and pack["language"].strip()
    questions = pack.get("questions")
    assert isinstance(questions, list) and questions
    seen_ids: set[str] = set()
    for q in questions:
        assert isinstance(q.get("id"), str) and q["id"].strip()
        assert q["id"] not in seen_ids
        seen_ids.add(q["id"])
        assert isinstance(q.get("question"), str) and q["question"].strip()
        answers = q.get("answers")
        assert isinstance(answers, list)
        assert len(answers) == SUBMIT_ANSWERS_PER_QUESTION
        assert sum(1 for a in answers if a.get("correct") is True) == 1


def test_python_validator_rejects_malformed_fixture() -> None:
    """The same malformation the JS check uses (a question with too few answers)
    must be rejected on the Python side too, proving the two validators reject
    in lockstep, not just accept."""
    pack = _load_fixture()
    pack["questions"][0]["answers"] = pack["questions"][0]["answers"][:2]
    ok, errors = validate_pack(pack)
    assert not ok
    assert errors


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_worker_source_parses() -> None:
    """`node --check` proves the worker source still parses — a syntax error
    would otherwise only surface at deploy time."""
    result = subprocess.run(
        ["node", "--check", str(_WORKER)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_worker_validator_matches_contract() -> None:
    """Run the JS contract check: the worker's validatePack must accept the
    shared fixture and reject a malformed pack. This is the cross-language pin —
    if the worker schema drifts from the fixture, this fails CI."""
    result = subprocess.run(
        ["node", str(_CONTRACT_CHECK)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"contract-check.mjs failed:\n{result.stdout}\n{result.stderr}"
    )
