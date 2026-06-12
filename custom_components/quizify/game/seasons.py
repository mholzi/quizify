"""Seasonal-pack metadata: recurring month-day windows + active-window checks.

A pack may carry an optional ``season`` field describing a *recurring*
calendar window (it surfaces every year, independent of year):

    "season": {"start": "12-01", "end": "12-26", "label": "🎄 Weihnachten"}

``start``/``end`` are ``MM-DD`` strings, both bounds inclusive. Wrap-around
windows that span the new year are supported:

    "season": {"start": "12-20", "end": "01-06", "label": "🎄 Festtage"}

This module never touches the real clock — the "current date" is always passed
in by the caller (see ``is_in_season`` / ``active_season_label``) so the
behaviour is fully testable with an injected date. Packs without a ``season``
field are fully back-compatible: :func:`parse_season` returns ``None`` and every
``is_in_season(None, ...)`` is ``False``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

_LOGGER = logging.getLogger(__name__)

# Label cap so a hostile/malformed community pack can't flood the picker badge.
_MAX_LABEL_LEN = 40


@dataclass(frozen=True)
class Season:
    """A parsed, validated recurring seasonal window.

    ``start``/``end`` are ``(month, day)`` tuples, both inclusive. The window
    wraps around the new year when ``start > end`` (e.g. Dec 20 → Jan 6).
    """

    start: tuple[int, int]
    end: tuple[int, int]
    label: str


def _parse_md(raw: object) -> tuple[int, int] | None:
    """Parse a ``"MM-DD"`` string into a validated ``(month, day)`` tuple.

    Returns ``None`` for anything malformed (wrong type, wrong shape, or a
    month/day outside a sane calendar range). Day validity is checked against a
    leap year (2024) so Feb 29 is accepted as a legitimate window edge.
    """
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        month = int(parts[0])
        day = int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12):
        return None
    # Validate the day against a leap year so 02-29 is allowed.
    try:
        date(2024, month, day)
    except ValueError:
        return None
    return (month, day)


def parse_season(raw: object) -> Season | None:
    """Parse + validate a pack's ``season`` field into a :class:`Season`.

    Tolerant by design — any malformed shape (not a dict, missing/invalid
    ``start``/``end``, non-string ``label``) is logged and yields ``None`` so a
    bad pack simply behaves as a non-seasonal pack rather than breaking the
    loader. A pack with no ``season`` field passes ``raw is None`` and returns
    ``None`` silently.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _LOGGER.warning(
            "Ignoring 'season': expected an object, got %s", type(raw).__name__
        )
        return None

    start = _parse_md(raw.get("start"))
    end = _parse_md(raw.get("end"))
    if start is None or end is None:
        _LOGGER.warning(
            "Ignoring 'season': invalid start/end (%r → %r); expected 'MM-DD'",
            raw.get("start"),
            raw.get("end"),
        )
        return None

    label_raw = raw.get("label", "")
    label = label_raw.strip() if isinstance(label_raw, str) else ""
    if not label:
        _LOGGER.warning(
            "Season window %s–%s has no label; using a generic one", start, end
        )
        label = "Seasonal"
    label = label[:_MAX_LABEL_LEN]

    return Season(start=start, end=end, label=label)


def is_in_season(season: Season | None, today: date) -> bool:
    """Return whether ``today`` falls inside the recurring window (inclusive).

    Year-agnostic: only ``today``'s month/day matter. Handles the wrap-around
    case (window spanning the new year) by inverting the comparison. ``None``
    (no season / unparseable) is always out of season.
    """
    if season is None:
        return False

    cur = (today.month, today.day)
    start = season.start
    end = season.end

    if start <= end:
        # Normal window within a single calendar year, e.g. 10-25 → 11-01.
        return start <= cur <= end
    # Wrap-around window, e.g. 12-20 → 01-06: in-season if on/after start OR
    # on/before end.
    return cur >= start or cur <= end


def _days_until_end(season: Season, today: date) -> int:
    """Days from ``today`` to the window's end (inclusive), for tie-breaking.

    Used only when several seasons are active at once: the soonest-ending one
    wins so a short, specific window (e.g. a single holiday) takes priority over
    a long one. Year-agnostic — counts forward from today to the next occurrence
    of the end month/day.
    """
    end_month, end_day = season.end
    year = today.year
    try:
        end_date = date(year, end_month, end_day)
    except ValueError:
        # Feb 29 in a non-leap year: treat as Mar 1 for ordering purposes.
        end_date = date(year, 3, 1)
    if end_date < today:
        try:
            end_date = date(year + 1, end_month, end_day)
        except ValueError:
            end_date = date(year + 1, 3, 1)
    return (end_date - today).days


def pick_active_season(
    seasons: dict[str, Season | None], today: date
) -> str | None:
    """Pick the single seasonal pack to pin from ``{slug: Season}``.

    Returns the slug of the in-season pack to feature, or ``None`` if none are
    active. When multiple windows overlap, selection is deterministic:
    soonest-ending first (so a tight holiday window beats a broad one), then by
    slug as a final stable tie-breaker.
    """
    active = [
        (slug, season)
        for slug, season in seasons.items()
        if season is not None and is_in_season(season, today)
    ]
    if not active:
        return None
    active.sort(key=lambda item: (_days_until_end(item[1], today), item[0]))
    return active[0][0]
