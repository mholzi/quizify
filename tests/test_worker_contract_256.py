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

import copy
import json
import os
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
_MALFORMATIONS = _REPO_ROOT / "tests" / "fixtures" / "community_pack_malformations.json"
_WORKER = _REPO_ROOT / "cf-workers" / "quizify-api.js"
_CONTRACT_CHECK = _REPO_ROOT / "cf-workers" / "contract-check.mjs"

# When ``QUIZIFY_REQUIRE_NODE=1`` is set (CI runs node), a missing ``node`` is a
# hard FAILURE instead of a silent skip (#313). Locally — where node may be
# absent — the tests still skip. Set it in CI to guarantee the cross-language
# contract is actually exercised and can't rot behind a perpetual skip.
_REQUIRE_NODE = os.environ.get("QUIZIFY_REQUIRE_NODE") == "1"


def _require_node() -> None:
    """Skip (local) or fail (CI, ``QUIZIFY_REQUIRE_NODE=1``) when node is absent.

    Turning the old silent ``skipif`` into a CI failure closes the #313 gap
    where a CI image without node would have quietly green-lit a drifted worker
    schema by never running the cross-language check at all.
    """
    if shutil.which("node") is not None:
        return
    msg = "node not available — the worker contract check cannot run"
    if _REQUIRE_NODE:
        pytest.fail(f"{msg} (QUIZIFY_REQUIRE_NODE=1 requires it)")
    pytest.skip(msg)


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _load_malformations() -> list[dict]:
    data = json.loads(_MALFORMATIONS.read_text(encoding="utf-8"))
    return data["malformations"]


def _apply_malformation(pack: dict, spec: dict) -> dict:
    """Apply one shared malformation to a *copy* of the canonical pack.

    Mirrors ``applyMalformation`` in ``cf-workers/contract-check.mjs`` exactly —
    the same ``kind`` codes must mutate the pack identically on both sides so a
    given malformation pins the same rejection in both validators.
    """
    p = copy.deepcopy(pack)
    kind = spec["kind"]
    if kind == "truncate_first_answers":
        p["questions"][0]["answers"] = p["questions"][0]["answers"][: spec["n"]]
    elif kind == "delete_field":
        p.pop(spec["field"], None)
    elif kind == "mark_second_answer_correct":
        p["questions"][0]["answers"][1]["correct"] = True
    elif kind == "duplicate_first_id":
        p["questions"][1]["id"] = p["questions"][0]["id"]
    elif kind == "blank_first_question_text":
        p["questions"][0]["question"] = "   "
    else:  # pragma: no cover - guards against an unhandled new kind
        raise ValueError(f"unknown malformation kind: {kind}")
    return p


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


@pytest.mark.parametrize(
    "spec", _load_malformations(), ids=lambda s: s["id"]
)
def test_python_validator_rejects_each_shared_malformation(spec: dict) -> None:
    """Every malformation in the shared catalog must be rejected by the Python
    validator. The same catalog is replayed against the worker's validatePack
    (see ``contract-check.mjs``), so a divergence on either side fails CI —
    broadening the contract beyond the single original malformation (#313)."""
    pack = _apply_malformation(_load_fixture(), spec)
    ok, errors = validate_pack(pack)
    assert not ok, f"Python validate_pack wrongly accepted '{spec['id']}'"
    assert errors


def test_malformation_catalog_is_non_trivial() -> None:
    """Guard the shared catalog itself: it must carry several malformations so
    a careless edit can't quietly shrink the contract back to one case."""
    specs = _load_malformations()
    assert len(specs) >= 3
    assert len({s["id"] for s in specs}) == len(specs)  # unique ids


def test_worker_source_parses() -> None:
    """`node --check` proves the worker source still parses — a syntax error
    would otherwise only surface at deploy time. Hard-fails (not skips) under
    ``QUIZIFY_REQUIRE_NODE=1`` so CI can't silently bypass it (#313)."""
    _require_node()
    result = subprocess.run(
        ["node", "--check", str(_WORKER)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


def test_worker_validator_matches_contract() -> None:
    """Run the JS contract check: the worker's validatePack must accept the
    shared fixture and reject *every* malformation in the shared catalog. This
    is the cross-language pin — if the worker schema drifts from the fixture or
    stops rejecting any catalogued malformation, this fails CI.

    Hard-fails (not skips) under ``QUIZIFY_REQUIRE_NODE=1`` (#313)."""
    _require_node()
    result = subprocess.run(
        ["node", str(_CONTRACT_CHECK)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"contract-check.mjs failed:\n{result.stdout}\n{result.stderr}"
    )
