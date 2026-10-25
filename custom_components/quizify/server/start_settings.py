"""Start-game settings coercion for the socket-less start paths (#744, #1124).

Moved out of ``server/websocket.py`` (issue #1124) unchanged. These are pure
functions with no handler state: they turn a saved preset, the explicit
``quizify.start_game`` service fields, or a wire toggle into the keyword
arguments ``QuizifyGameState.start_game`` takes. ``websocket.py`` re-imports
every name, so ``from ...server.websocket import _preset_to_start_settings``
and ``_coerce_toggle`` keep working.
"""

from __future__ import annotations

from typing import Any


def _coerce_toggle(value: Any, *, default: bool) -> bool:
    """Coerce a wire toggle value to a bool (#285).

    Front-end checkboxes serialize as a JSON ``bool``, but be defensive about
    string forms ("false"/"0"/"off"/"no") and a missing key so a malformed
    payload can't accidentally arm/disarm a setting against the default.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "off", "no", "")
    return default


# --- socket-less start_game settings (#744) ----------------------------------
#
# The keyword arguments ``QuizifyGameState.start_game`` accepts and that a
# service call / preset / last-used snapshot may fill in. The two seed
# arguments are deliberately absent: they exist for tests to pin the random
# draws and must never be reachable from a host-facing surface.
_START_GAME_FIELDS = frozenset(
    {
        "category",
        "categories",
        "difficulty",
        "num_rounds",
        "language",
        "timer_duration",
        "lightning_enabled",
        "hot_seat_enabled",
        "powerups_enabled",
        "wager_enabled",
    }
)

# The admin UI's sentinel for "no pick" in the pack and difficulty choosers. It
# is stored verbatim in a saved preset and typed verbatim into a service call,
# and ``start_game`` spells the same thing ``None``.
_MIXED = "mixed"

#: Saved-preset field name -> ``start_game`` keyword. ``packs`` and ``category``
#: are handled separately because they resolve to two different keywords.
_PRESET_FIELD_MAP = {
    "rounds": "num_rounds",
    "timer": "timer_duration",
    "difficulty": "difficulty",
    "language": "language",
    "lightning": "lightning_enabled",
    "hot_seat": "hot_seat_enabled",
    "powerups": "powerups_enabled",
    "wager": "wager_enabled",
}


def _coerce_category(raw: Any) -> dict[str, Any]:
    """Resolve a pack selection to ``category`` / ``categories`` kwargs.

    Accepts the three shapes every Quizify surface already uses: a list of
    slugs (multi-pack), a single slug, or the ``"mixed"`` sentinel / empty value
    meaning "all packs". Always returns BOTH keys so a narrower pick from a
    later layer fully replaces a broader one instead of merging with it.
    """
    if isinstance(raw, (list, tuple)):
        slugs = [str(s).strip() for s in raw if str(s).strip()]
        if slugs:
            return {"category": None, "categories": slugs}
        return {"category": None, "categories": None}
    value = str(raw or "").strip()
    if not value or value == _MIXED:
        return {"category": None, "categories": None}
    return {"category": value, "categories": None}


def _preset_to_start_settings(preset: dict[str, Any]) -> dict[str, Any]:
    """Translate a saved preset record into ``start_game`` keywords (#744).

    Presets are stored in the admin UI's vocabulary (``rounds``, ``timer``,
    ``packs``, ``hot_seat``); this is the one place that knows the mapping.
    Fields the preset does not carry are left out entirely so the layer below
    keeps owning them.
    """
    settings: dict[str, Any] = {}
    if "packs" in preset:
        settings.update(_coerce_category(preset.get("packs")))
    elif "category" in preset:
        settings.update(_coerce_category(preset.get("category")))
    for field, keyword in _PRESET_FIELD_MAP.items():
        if preset.get(field) is None:
            continue
        value = preset[field]
        if keyword == "difficulty":
            settings["difficulty"] = (
                None if str(value).strip() in ("", _MIXED) else str(value).strip()
            )
        elif keyword == "language":
            # #889: a preset's packs are written in one language, so the
            # language rides along with them. Empty means "not stored" — a
            # preset saved before #889 — and the layers below keep owning it.
            text = str(value).strip()
            if text:
                settings["language"] = text
        elif keyword in ("num_rounds", "timer_duration"):
            try:
                settings[keyword] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            settings[keyword] = bool(value)
    return settings


def _overrides_to_start_settings(overrides: dict[str, Any]) -> dict[str, Any]:
    """Translate the explicit ``quizify.start_game`` fields into keywords.

    The service schema has already coerced and range-checked the numbers, so
    this only has to resolve the pack selection and the ``"mixed"`` difficulty
    sentinel. Absent fields stay absent.
    """
    settings: dict[str, Any] = {}
    if "category" in overrides:
        settings.update(_coerce_category(overrides["category"]))
    if "difficulty" in overrides:
        raw = str(overrides["difficulty"] or "").strip()
        settings["difficulty"] = None if raw in ("", _MIXED) else raw
    for key in ("num_rounds", "language", "timer_duration"):
        if overrides.get(key) is not None:
            settings[key] = overrides[key]
    return settings
