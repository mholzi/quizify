"""The player-flagged-questions log, as an object rather than as view code.

A player who hits a wrong, ambiguous or broken question taps "report" and one
line lands in ``flagged.jsonl`` next to the rest of Quizify's state. The pack
maintainer reads them back on the analytics dashboard, or with ``jq``.

Until #790 this file had no name in the codebase: the append (with its
size-trim) was written inline in ``flag_question_view`` and the parse loop
inline in ``flag_list_view``, which meant the only way to test either was
through an HTTP request, and load-time validation was nobody's job. Both
halves live here now, on top of :class:`~custom_components.quizify.storage.JsonlFile`,
so the views call :meth:`add` and :meth:`list` and nothing else.

Two policies belong to the store rather than to the HTTP layer:

* **The file is capped.** An unauthenticated POST writes here, so the log
  trims its oldest half at :data:`MAX_BYTES` instead of growing forever.
* **The client IP never leaves the host.** It is recorded on disk for the
  operator's own forensics and stripped from everything :meth:`list` returns
  (#305) — the ``/api/quizify/*`` routes carry no HA auth, so a response is a
  publication.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ..storage import JsonlFile

if TYPE_CHECKING:
    from ..runtime import Runtime

#: Cap on the log. ~256 KB is thousands of reports — far more than a host will
#: ever read — while staying small enough that the trim rewrite is cheap.
MAX_BYTES = 256 * 1024

#: Bounds on the caller-supplied strings (#357). Without them an anonymous
#: POST could append a megabyte-long single line, and because the size-trim
#: runs *before* the append that one line would still land.
QUESTION_ID_MAX = 64
REASON_MAX = 200
PLAYER_NAME_MAX = 50

#: Recorded on disk, never returned. See the module docstring.
_PRIVATE_FIELDS = frozenset({"remote"})

FILENAME = "flagged.jsonl"


class FlagStore:
    """Append-only log of questions players reported as wrong or unclear."""

    def __init__(self, runtime: Runtime, filename: str = FILENAME) -> None:
        self._file = JsonlFile(
            runtime, runtime.data_dir / filename, label="Question flag log"
        )

    async def add(
        self,
        question_id: str,
        *,
        reason: str = "",
        player_name: str = "",
        remote: str = "",
    ) -> dict[str, Any]:
        """Record one flag and return the entry as stored.

        Every caller-supplied field is truncated here rather than at the call
        site, so a second entry point cannot forget to do it.
        """
        entry: dict[str, Any] = {
            "ts": int(time.time()),
            "question_id": str(question_id)[:QUESTION_ID_MAX],
            "reason": str(reason)[:REASON_MAX],
            "player_name": str(player_name)[:PLAYER_NAME_MAX],
            "remote": str(remote),
        }
        await self._file.append(entry, max_bytes=MAX_BYTES)
        return entry

    async def list(self) -> list[dict[str, Any]]:
        """Return every recorded flag, oldest first, without the client IP.

        A line that will not parse — a torn last entry from a process that
        died mid-append — is skipped rather than fatal, and so is a line that
        parsed into something other than an object.
        """
        return [
            {k: v for k, v in entry.items() if k not in _PRIVATE_FIELDS}
            for entry in await self._file.read_all()
            if isinstance(entry, dict)
        ]
