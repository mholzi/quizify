"""Direct unit tests for the admin TokenStore (#260 coverage gap).

TokenStore is the HA-free on-disk JSON store that persists the admin session
token (replacing homeassistant.helpers.storage.Store). It was only exercised
indirectly via ConnectionManager; these tests drive it directly through the
StandaloneRuntime so the load / save / remove paths — including the corrupt-file
and missing-file degradations — are covered.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.runtime import StandaloneRuntime  # noqa: E402
from custom_components.quizify.server.token_store import TokenStore  # noqa: E402


def _store(tmp_path: Path) -> TokenStore:
    return TokenStore(StandaloneRuntime(tmp_path), filename="admin_token.json")


def test_load_missing_returns_none(tmp_path: Path) -> None:
    """A store whose file was never written returns None, not an error."""
    store = _store(tmp_path)
    assert asyncio.run(store.load()) is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    """Saved data reads back identically."""
    store = _store(tmp_path)

    async def _go() -> dict | None:
        await store.save({"token": "abc-123"})
        return await store.load()

    assert asyncio.run(_go()) == {"token": "abc-123"}


def test_save_is_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    """The atomic os.replace write leaves no .tmp residue on success."""
    store = _store(tmp_path)
    asyncio.run(store.save({"token": "xyz"}))
    assert (tmp_path / "admin_token.json").exists()
    assert not (tmp_path / "admin_token.tmp").exists()


def test_save_overwrites_previous_value(tmp_path: Path) -> None:
    """A second save replaces the first value."""
    store = _store(tmp_path)

    async def _go() -> dict | None:
        await store.save({"token": "first"})
        await store.save({"token": "second"})
        return await store.load()

    assert asyncio.run(_go()) == {"token": "second"}


def test_load_corrupt_file_returns_none(tmp_path: Path) -> None:
    """Invalid JSON on disk degrades to None rather than raising (so a
    corrupt token file forces a clean re-bootstrap instead of crashing setup)."""
    (tmp_path / "admin_token.json").write_text("{not valid json")
    store = _store(tmp_path)
    assert asyncio.run(store.load()) is None


def test_remove_deletes_file(tmp_path: Path) -> None:
    """remove() deletes the persisted file."""
    store = _store(tmp_path)

    async def _go() -> bool:
        await store.save({"token": "gone-soon"})
        await store.remove()
        return (tmp_path / "admin_token.json").exists()

    assert asyncio.run(_go()) is False


def test_remove_is_idempotent(tmp_path: Path) -> None:
    """remove() on an absent file is a no-op, not an error (FileNotFoundError
    is swallowed)."""
    store = _store(tmp_path)
    # Never saved → file doesn't exist; removing twice must not raise.
    asyncio.run(store.remove())
    asyncio.run(store.remove())
