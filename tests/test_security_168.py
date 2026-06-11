"""Security regression tests for issue #168 (code-review batch).

Covers the four hardening items:
  1. Admin-bootstrap race — two concurrent first-connections must NOT both be
     granted admin (only one token persists; the other admin would be silently
     locked out). ConnectionManager.try_bootstrap_admin() must be atomic.
  2. Player session tokens must expire passively — a token that is issued and
     never looked up again must not linger forever in _session_tokens.
  3. Out-of-range answer index from a malicious/buggy client must be rejected,
     not crash or misclassify (defended at the websocket layer AND in
     QuestionBank.validate_answer).
  4. A malformed (valid-JSON-but-not-an-object) pack file must degrade the
     featured-pack icon to the default instead of 500-ing the admin screen.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import (  # noqa: E402
    Answer,
    Question,
    QuestionBank,
)
from custom_components.quizify.server import views  # noqa: E402
from custom_components.quizify.server.connection import (  # noqa: E402
    _PLAYER_TOKEN_TTL,
    ConnectionManager,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: D401
        return func(*args)


@pytest.fixture
def conn(tmp_path: Path) -> ConnectionManager:
    runtime = _FakeRuntime(tmp_path)
    return ConnectionManager(runtime, lambda: None)


# ---------------------------------------------------------------------------
# 1. Admin-bootstrap race
# ---------------------------------------------------------------------------


class TestAdminBootstrapRace:
    @pytest.mark.asyncio
    async def test_concurrent_bootstrap_grants_exactly_one_admin(
        self, conn: ConnectionManager
    ) -> None:
        """Fire many bootstrap attempts concurrently on a fresh install.
        Exactly one must win; all must converge on the same persisted token."""
        results = await asyncio.gather(
            *(conn.try_bootstrap_admin() for _ in range(10))
        )
        assert sum(results) == 1, f"expected 1 winner, got {sum(results)}"
        # A token now exists and is stable.
        assert conn._admin_session_token is not None
        token = conn._admin_session_token

        # A later attempt (e.g. another tab) never re-grants bootstrap.
        assert await conn.try_bootstrap_admin() is False
        assert conn._admin_session_token == token

    @pytest.mark.asyncio
    async def test_bootstrap_persists_token_durably(
        self, conn: ConnectionManager, tmp_path: Path
    ) -> None:
        """The winning bootstrap must have persisted the token to disk before
        returning, so a second ConnectionManager (simulating a racing connect
        that loads from storage) sees it and does not bootstrap again."""
        assert await conn.try_bootstrap_admin() is True

        conn2 = ConnectionManager(_FakeRuntime(tmp_path), lambda: None)
        assert await conn2.try_bootstrap_admin() is False
        await conn2.async_load_admin_token()
        assert conn2._admin_session_token == conn._admin_session_token


# ---------------------------------------------------------------------------
# 1b. Constant-time admin-token validation (#259)
# ---------------------------------------------------------------------------


class TestValidateAdminToken:
    """validate_admin_token must validate the right token via the new
    constant-time (hmac.compare_digest) path and reject everything else."""

    @pytest.mark.asyncio
    async def test_correct_token_validates(self, conn: ConnectionManager) -> None:
        await conn.try_bootstrap_admin()
        token = conn._admin_session_token
        assert conn.validate_admin_token(token) is True

    @pytest.mark.asyncio
    async def test_wrong_token_rejected(self, conn: ConnectionManager) -> None:
        await conn.try_bootstrap_admin()
        assert conn.validate_admin_token("not-the-token") is False

    @pytest.mark.asyncio
    async def test_empty_token_rejected(self, conn: ConnectionManager) -> None:
        await conn.try_bootstrap_admin()
        assert conn.validate_admin_token("") is False

    def test_no_admin_token_set_rejects_everything(
        self, conn: ConnectionManager
    ) -> None:
        # No token has been created yet → even a plausible value must not pass,
        # and the guard must short-circuit before compare_digest (which would
        # raise on a None operand).
        assert conn._admin_session_token is None
        assert conn.validate_admin_token("anything") is False
        assert conn.validate_admin_token("") is False

    @pytest.mark.asyncio
    async def test_uses_constant_time_compare(
        self, conn: ConnectionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The compare must route through hmac.compare_digest, not `==`,
        so the validation has no early-exit timing oracle (#259)."""
        import custom_components.quizify.server.connection as conn_mod

        await conn.try_bootstrap_admin()
        token = conn._admin_session_token
        calls: list[tuple[str, str]] = []
        real = conn_mod.hmac.compare_digest

        def _spy(a, b):  # noqa: ANN001
            calls.append((a, b))
            return real(a, b)

        monkeypatch.setattr(conn_mod.hmac, "compare_digest", _spy)
        assert conn.validate_admin_token(token) is True
        assert calls, "validate_admin_token did not use hmac.compare_digest"


# ---------------------------------------------------------------------------
# 2. Passive session-token expiry
# ---------------------------------------------------------------------------


class TestSessionTokenSweep:
    def test_create_sweeps_expired_tokens(
        self, conn: ConnectionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An issued-but-never-looked-up token past its TTL must be dropped
        when the next token is created — not linger forever."""
        clock = {"t": 1000.0}
        monkeypatch.setattr(
            "custom_components.quizify.server.connection.time.monotonic",
            lambda: clock["t"],
        )
        stale = conn.create_session_token("Alice")
        assert stale in conn._session_tokens

        # Advance past the TTL and create another token.
        clock["t"] += _PLAYER_TOKEN_TTL + 1
        conn.create_session_token("Bob")

        # Alice's stale token was swept even though it was never validated.
        assert stale not in conn._session_tokens
        assert len(conn._session_tokens) == 1

    def test_sweep_keeps_live_tokens(
        self, conn: ConnectionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tokens within TTL survive a sweep."""
        clock = {"t": 1000.0}
        monkeypatch.setattr(
            "custom_components.quizify.server.connection.time.monotonic",
            lambda: clock["t"],
        )
        fresh = conn.create_session_token("Alice")
        clock["t"] += 60  # well within TTL
        conn.create_session_token("Bob")
        assert fresh in conn._session_tokens
        assert len(conn._session_tokens) == 2


# ---------------------------------------------------------------------------
# 3. Out-of-range answer index
# ---------------------------------------------------------------------------


def _bank_with_one_question() -> tuple[QuestionBank, Question]:
    bank = QuestionBank.__new__(QuestionBank)  # no disk load needed
    q = Question(
        id="q1",
        category="test",
        difficulty="easy",
        question="2+2?",
        answers=[Answer(text="4", correct=True), Answer(text="5", correct=False)],
    )
    return bank, q


class TestAnswerIndexBounds:
    @pytest.mark.parametrize("idx", [-1, 2, 99, 1000])
    def test_validate_answer_rejects_out_of_range(self, idx: int) -> None:
        """The scoring-layer guard returns False (wrong) for any index outside
        the answer list, so a crafted index can never index past the array."""
        bank, q = _bank_with_one_question()
        assert bank.validate_answer(q, idx) is False

    def test_validate_answer_accepts_in_range(self) -> None:
        bank, q = _bank_with_one_question()
        assert bank.validate_answer(q, 0) is True
        assert bank.validate_answer(q, 1) is False  # in range, just wrong


# ---------------------------------------------------------------------------
# 4. Malformed pack file → default icon, no 500
# ---------------------------------------------------------------------------


class TestFeaturedPackMalformed:
    @pytest.mark.asyncio
    async def test_non_object_pack_json_degrades_to_default_icon(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pack file containing valid JSON that is NOT an object (here a
        list) must not raise — the view degrades to the 🎲 default icon."""
        # Write a malformed pack: valid JSON, wrong shape.
        (tmp_path / "geographie.json").write_text(
            json.dumps([1, 2, 3]), encoding="utf-8"
        )

        bank = MagicMock()
        bank.get_pack_versions.return_value = {
            "geographie": {"language": "de", "question_count": 5, "name": "Geo"},
        }
        bank._questions_dir = tmp_path
        bank._categories = {}

        ctx = MagicMock()
        ctx.game._question_bank = bank
        ctx.analytics = None  # force the deterministic fallback path
        ctx.question_stats = None
        ctx.runtime = _FakeRuntime(tmp_path)

        monkeypatch.setattr(views, "_get_ctx", lambda _req: ctx)

        request = MagicMock()
        request.query.get.side_effect = lambda k, d=None: {"lang": "de"}.get(k, d)

        resp = await views.featured_pack_view(request)

        assert resp.status == 200
        body = json.loads(resp.text)
        # Fell back to the default icon rather than raising AttributeError.
        assert body["title"].startswith("🎲")
        assert body["value"] == "geographie"
