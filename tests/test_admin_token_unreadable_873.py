"""Regression tests for issue #873 (security).

``TokenStore.load`` used to read the admin token with the integration's
house policy for a broken file, ``on_corrupt="warn_and_default"``: a
permissions error, a directory where the file should be, or a half-written
JSON blob logged a warning and returned ``None``. ``None`` is also what a
**fresh install** returns — so ``async_load_admin_token`` marked the store
loaded with no token, and ``try_bootstrap_admin`` then handed the next
``?role=admin`` handshake from any LAN address the host's seat and overwrote
the file. That is the takeover #725 closed, arriving through the storage layer
instead of the expiry path, and a restore with the wrong ownership or a full
disk was enough to trigger it.

The store now reads with ``on_corrupt="raise"``. An unreadable file leaves
``_admin_token_loaded`` false, the bootstrap refuses, and the log names
``quizify.reset_admin_session`` — an HA-authenticated service call — as the
deliberate way back in.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.runtime import StandaloneRuntime  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402

TOKEN_FILE = "admin_token.json"


def _conn(tmp_path: Path) -> ConnectionManager:
    return ConnectionManager(StandaloneRuntime(tmp_path), lambda: None)


def _write_corrupt(tmp_path: Path) -> None:
    """A half-written file — the crash-during-write shape."""
    (tmp_path / TOKEN_FILE).write_text('{"token": "abc', encoding="utf-8")


def _write_unreadable(tmp_path: Path) -> Path:
    """A file whose ownership/permissions came back wrong from a restore."""
    path = tmp_path / TOKEN_FILE
    path.write_text('{"token": "the-real-hosts-token"}', encoding="utf-8")
    path.chmod(0o000)
    return path


def _make_a_directory(tmp_path: Path) -> None:
    """A directory where the file should be — the other OSError shape."""
    (tmp_path / TOKEN_FILE).mkdir()


class TestCorruptTokenFileKeepsBootstrapShut:
    def test_a_corrupt_file_does_not_grant_admin(self, tmp_path: Path) -> None:
        """The heart of #873: a broken credential file is not a fresh install."""
        _write_corrupt(tmp_path)
        conn = _conn(tmp_path)
        assert asyncio.run(conn.try_bootstrap_admin()) is False

    def test_a_corrupt_file_does_not_mark_the_store_loaded(
        self, tmp_path: Path
    ) -> None:
        """A failed read must stay retryable, not cache "no token" for the run."""
        _write_corrupt(tmp_path)
        conn = _conn(tmp_path)
        asyncio.run(conn.async_load_admin_token())
        assert conn._admin_token_loaded is False
        assert conn._admin_session_token is None

    def test_a_corrupt_file_is_not_overwritten(self, tmp_path: Path) -> None:
        """The old code minted a token and clobbered the file it could not read."""
        _write_corrupt(tmp_path)
        conn = _conn(tmp_path)
        asyncio.run(conn.try_bootstrap_admin())
        assert (tmp_path / TOKEN_FILE).read_text(encoding="utf-8") == '{"token": "abc'

    def test_a_directory_in_the_files_place_does_not_grant_admin(
        self, tmp_path: Path
    ) -> None:
        _make_a_directory(tmp_path)
        conn = _conn(tmp_path)
        assert asyncio.run(conn.try_bootstrap_admin()) is False

    @pytest.mark.skipif(
        os.geteuid() == 0, reason="root reads a 0o000 file regardless of mode"
    )
    def test_an_unreadable_file_does_not_grant_admin(self, tmp_path: Path) -> None:
        """The restore-with-wrong-ownership shape the issue names."""
        path = _write_unreadable(tmp_path)
        try:
            conn = _conn(tmp_path)
            assert asyncio.run(conn.try_bootstrap_admin()) is False
            assert conn._admin_session_token is None
        finally:
            path.chmod(0o600)

    def test_the_error_names_the_recovery_service(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A locked-out host has to be told how to get back in."""
        _write_corrupt(tmp_path)
        conn = _conn(tmp_path)
        with caplog.at_level(logging.ERROR):
            asyncio.run(conn.try_bootstrap_admin())
        logged = caplog.text
        assert "quizify.reset_admin_session" in logged
        assert any(r.levelno >= logging.ERROR for r in caplog.records), (
            "an unreadable credential store must log at ERROR, not WARNING — "
            "the only trace of #873 was a warning nobody read"
        )


class TestTheNormalPathsAreUntouched:
    def test_a_missing_file_still_bootstraps(self, tmp_path: Path) -> None:
        """A genuinely fresh install must still hand the first admin the seat."""
        conn = _conn(tmp_path)
        assert asyncio.run(conn.try_bootstrap_admin()) is True
        assert conn._admin_session_token
        assert (tmp_path / TOKEN_FILE).exists()

    def test_a_healthy_file_is_loaded_and_blocks_a_second_bootstrap(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / TOKEN_FILE).write_text('{"token": "kept"}', encoding="utf-8")
        conn = _conn(tmp_path)
        assert asyncio.run(conn.try_bootstrap_admin()) is False
        assert conn.validate_admin_token("kept")

    def test_reset_admin_session_reopens_the_bootstrap_deliberately(
        self, tmp_path: Path
    ) -> None:
        """The documented way out of the lockout, exercised end to end.

        ``quizify.reset_admin_session`` calls ``async_clear_admin_token``; the
        file goes, the store counts as loaded, and the next connection may
        bootstrap again. That is a deliberate act by somebody already
        authenticated to Home Assistant — which is exactly the difference
        between this and the hole #873 describes.
        """
        _write_corrupt(tmp_path)
        conn = _conn(tmp_path)

        async def _go() -> bool:
            assert await conn.try_bootstrap_admin() is False
            await conn.async_clear_admin_token()
            return await conn.try_bootstrap_admin()

        assert asyncio.run(_go()) is True
