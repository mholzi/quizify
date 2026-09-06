"""One JSON-on-disk routine for every store in the integration (#790).

Quizify persists eight small files — the admin token, saved presets, pack
news, analytics, per-question stats, the question history, the flag log — and
until this module existed each of them hand-wrote the same three steps:
``read_text`` in an executor, ``json.loads``, and a ``tmp.write_text`` +
``os.replace`` back out. Seven copies, seven sets of ``except`` clauses, and
seven private opinions about what a corrupt file means.

Two shipped bugs came out of that spread rather than out of any one store:

``#700``
    A malformed community pack took setup down. Degrading a broken file to a
    default was a policy some of the copies had and others did not.

``#588``
    Question stats were lost on a restart, because the "save often enough"
    policy had been worked out once, in analytics, and never shared.

So the routine lives here once. The blocking primitives (:func:`read_json`,
:func:`write_json`, :func:`append_jsonl`, :func:`read_jsonl`) are the whole
implementation; :class:`JsonFile` and :class:`JsonlFile` are thin async
wrappers that offload them through the runtime's executor. Callers that are
*already* inside an executor thread — the question history in
``game/questions.py`` keeps a synchronous API for the standalone dev server —
use the primitives directly and get the same corrupt-file policy and the same
atomic write as everybody else.

Two invariants the whole integration now shares:

* **A readable default beats a crash.** A truncated, half-written or
  hand-mangled file logs a warning and yields the caller's default. Nothing
  here raises at the caller unless it explicitly asks for ``on_corrupt="raise"``.
* **A write is all-or-nothing.** Every write goes to a sibling ``.tmp`` and is
  moved into place with :func:`os.replace`, so a reader never sees a partial
  file and a crash mid-write leaves the previous version intact.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .runtime import Runtime

_LOGGER = logging.getLogger(__name__)

#: What to do when a file exists but cannot be parsed.
#:
#: ``"warn_and_default"``
#:     Log a warning naming the file and hand the caller its default. The
#:     policy every store in Quizify wants: a bookkeeping file that got
#:     truncated must never be the reason a game cannot start (#700).
#: ``"raise"``
#:     Re-raise. For a caller that genuinely cannot proceed without the data
#:     and would rather fail loudly than run on a default.
OnCorrupt = Literal["warn_and_default", "raise"]

#: Mode for directories created on demand. Matches what analytics and question
#: stats already asked for; the umask narrows it further on most hosts.
DIR_MODE = 0o755


def _describe(path: Path, label: str | None) -> str:
    """Human-readable name for log lines — the label if given, else the file."""
    return label or path.name


# ---------------------------------------------------------------------------
# Blocking primitives (MUST NOT run on the event loop)
# ---------------------------------------------------------------------------


def read_json(
    path: Path,
    default: Any = None,
    *,
    on_corrupt: OnCorrupt = "warn_and_default",
    label: str | None = None,
) -> Any:
    """Return the JSON decoded from *path*, or *default*.

    *default* is returned when the file is missing, unreadable or unparseable.
    The caller owns the object it passes: this function never copies it, so
    pass a fresh literal rather than a shared module-level constant.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as err:
        # An unreadable file is a corrupt file as far as the caller is
        # concerned — permissions, a vanished mount, a directory where a file
        # was expected. Same policy, so the same branch.
        if on_corrupt == "raise":
            raise
        _LOGGER.warning("%s unreadable, using default: %s", _describe(path, label), err)
        return default

    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as err:
        if on_corrupt == "raise":
            raise
        _LOGGER.warning("%s corrupt, using default: %s", _describe(path, label), err)
        return default


def write_json(path: Path, data: Any, *, label: str | None = None) -> bool:
    """Atomically write *data* to *path* as JSON. Returns success.

    Serialisation happens here rather than at the call site so it lands in the
    executor thread with the write (#304): a full evening of analytics is a
    megabyte or so, and ``json.dumps`` on the event loop stalls every
    connected phone for the length of the serialise.

    Compact separators, no ``indent``: these files are machine-read, never
    hand-edited, and pretty-printing roughly doubles both the payload and the
    serialise cost.
    """
    tmp = path.with_suffix(".tmp")
    try:
        content = json.dumps(data)
    except (TypeError, ValueError) as err:
        _LOGGER.error("Failed to serialise %s: %s", _describe(path, label), err)
        return False
    try:
        path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as err:
        _LOGGER.error("Failed to persist %s: %s", _describe(path, label), err)
        # Leave no half-written sibling behind to be mistaken for a real file.
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    return True


def remove_file(path: Path, *, label: str | None = None) -> bool:
    """Delete *path* if it is there. Idempotent; returns success."""
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError as err:
        _LOGGER.warning("Failed to delete %s: %s", _describe(path, label), err)
        return False
    return True


def append_jsonl(
    path: Path,
    entry: Any,
    *,
    max_bytes: int | None = None,
    label: str | None = None,
) -> bool:
    """Append one JSON line to *path*, trimming the file first if it is full.

    ``max_bytes`` is a soft cap on an append-only log: when the file has
    reached it, the oldest half of the lines are dropped before the new entry
    goes on. The trim rewrites through a ``.tmp`` like every other write here,
    so a crash during it cannot leave the log truncated mid-line.
    """
    try:
        serialised = json.dumps(entry, ensure_ascii=False)
    except (TypeError, ValueError) as err:
        _LOGGER.error(
            "Failed to serialise entry for %s: %s", _describe(path, label), err
        )
        return False

    try:
        path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        if max_bytes is not None and path.exists() and path.stat().st_size >= max_bytes:
            _trim_jsonl(path, label=label)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(serialised + "\n")
    except OSError as err:
        _LOGGER.error("Failed to append to %s: %s", _describe(path, label), err)
        return False
    return True


def _trim_jsonl(path: Path, *, label: str | None = None) -> None:
    """Drop the oldest half of *path*'s lines. Best-effort, never raises."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as err:
        _LOGGER.warning("Could not trim %s: %s", _describe(path, label), err)
        return
    kept = lines[len(lines) // 2 :]
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as err:
        _LOGGER.warning("Could not trim %s: %s", _describe(path, label), err)
        with contextlib.suppress(OSError):
            tmp.unlink()


def read_jsonl(path: Path, *, label: str | None = None) -> list[Any]:
    """Return every parseable line of *path* as decoded JSON.

    A single unparseable line is skipped, not fatal: a JSONL log's whole point
    is that a torn last line — the process died mid-append — costs one entry
    rather than the file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as err:
        _LOGGER.warning("%s unreadable: %s", _describe(path, label), err)
        return []

    entries: list[Any] = []
    skipped = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            skipped += 1
    if skipped:
        _LOGGER.warning(
            "%s: skipped %d unparseable line(s)", _describe(path, label), skipped
        )
    return entries


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------


class JsonFile:
    """One JSON file, read and written off the event loop.

    ``JsonFile(runtime, path)`` binds a location; the store above it keeps the
    schema, the locking and the meaning. Deliberately not a cache — every
    :meth:`load` reads the file — because the stores that want an in-memory
    copy (analytics, question stats) already hold one and the ones that do not
    (presets, pack news) read rarely and would rather be correct after an
    external edit.
    """

    def __init__(
        self, runtime: Runtime, path: Path, *, label: str | None = None
    ) -> None:
        self._runtime = runtime
        self._path = Path(path)
        self._label = label

    @property
    def path(self) -> Path:
        """The file this instance is bound to."""
        return self._path

    async def exists(self) -> bool:
        """Whether the file is there (checked in the executor)."""
        return bool(await self._runtime.run_in_executor(self._path.exists))

    async def load(
        self,
        default: Any = None,
        *,
        on_corrupt: OnCorrupt = "warn_and_default",
    ) -> Any:
        """Return the decoded contents, or *default* if unusable."""

        def _read() -> Any:
            return read_json(
                self._path, default, on_corrupt=on_corrupt, label=self._label
            )

        return await self._runtime.run_in_executor(_read)

    async def save(self, data: Any) -> bool:
        """Atomically persist *data*. Returns whether it landed."""

        def _write() -> bool:
            return write_json(self._path, data, label=self._label)

        return bool(await self._runtime.run_in_executor(_write))

    async def remove(self) -> bool:
        """Delete the file (idempotent). Returns success."""

        def _rm() -> bool:
            return remove_file(self._path, label=self._label)

        return bool(await self._runtime.run_in_executor(_rm))


class JsonlFile:
    """One append-only JSON-lines log, read and written off the event loop."""

    def __init__(
        self, runtime: Runtime, path: Path, *, label: str | None = None
    ) -> None:
        self._runtime = runtime
        self._path = Path(path)
        self._label = label

    @property
    def path(self) -> Path:
        """The file this instance is bound to."""
        return self._path

    async def append(self, entry: Any, max_bytes: int | None = None) -> bool:
        """Append one entry, trimming the oldest half at *max_bytes*."""

        def _append() -> bool:
            return append_jsonl(
                self._path, entry, max_bytes=max_bytes, label=self._label
            )

        return bool(await self._runtime.run_in_executor(_append))

    async def read_all(self) -> list[Any]:
        """Return every parseable entry, oldest first."""

        def _read() -> list[Any]:
            return read_jsonl(self._path, label=self._label)

        return await self._runtime.run_in_executor(_read)
