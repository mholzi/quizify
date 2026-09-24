"""Per-game room code carried in the join link (#1016).

Quizify's game routes are registered without Home Assistant auth, because the
players are phones that scanned a QR code and have no HA login. Over the Nabu
Casa remote UI those routes are therefore reachable from the internet, and
before #1016 the join link was the bare ``/quizify/player`` path: anybody who
once saw it could walk into any later game.

The room code is the per-game secret that link now carries
(``/quizify/player?room=<code>``). It is minted server-side, rotated on every
``reset_game``, and required on a *fresh* player join. A player who already
holds a session token reconnects without it, and the admin token keeps working
wherever it did before.

Six characters from an alphabet without look-alikes (no ``0/O``, ``1/I/L``):
short enough to type off the television when a phone cannot scan, and
~2**29.7 values — at the loopback join budget (``MAX_PLAYERS * 5`` attempts a
minute) a blind guess takes years, and the code changes with the next reset.
"""

from __future__ import annotations

import hmac
import secrets

#: Characters a room code is drawn from. Upper case only; input is folded.
ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 6
#: Query parameter the join link carries the code in.
ROOM_CODE_PARAM = "room"
#: Path of the player page, without the code.
PLAYER_PATH = "/quizify/player"


def new_room_code() -> str:
    """Return a fresh, cryptographically random room code."""
    return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))


def normalize_room_code(value: object) -> str:
    """Fold user/URL input into the stored shape (trimmed, upper case)."""
    if not isinstance(value, str):
        return ""
    return value.strip().upper()[: ROOM_CODE_LENGTH * 4]


def room_code_matches(expected: str | None, supplied: object) -> bool:
    """Constant-time check of *supplied* against *expected*.

    ``None``/empty on either side never matches.
    """
    candidate = normalize_room_code(supplied)
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())


def join_path(code: str) -> str:
    """The relative join link for *code* (what the QR codes encode)."""
    return f"{PLAYER_PATH}?{ROOM_CODE_PARAM}={code}"
