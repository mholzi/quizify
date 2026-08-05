"""Guard the durable admin-token storage fix (admin lockout, 2026-08-05).

The admin session token is a *persistent* credential: the server writes its
copy to ``quizify/admin_token.json`` and — see
``ConnectionManager.try_bootstrap_admin`` — only ever mints a NEW one when no
token exists at all. The client kept its copy in ``sessionStorage``, which dies
with the tab. Closing the admin tab (or restarting HA while it was closed)
therefore orphaned the credential: no browser could present it, and no browser
could earn a replacement, so every fresh admin tab was refused with
"Admin only" — permanently, and silently.

The visible symptom was the setup panel: ``admin_connect`` is what triggers the
admin-connect frame, and that frame is what carries the TTS / media_player /
light / scene lists (#502, #494). Rejected connect → no frame → the dropdowns
never populate and show only their authored "Use default" option. Note they do
not even show the "None found" fallback, which is how this was told apart from
an empty-list-from-the-server bug.

Asserted over the shipped JS text because the behaviour lives in the browser
and there is no JS test runner in this repo (same approach as
``test_entity_picker_race_524.py``).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
UTILS_JS = WWW / "js" / "utils.js"

TOKEN_KEY = "quizify_admin_session_token"

# Every file that reads or writes the admin token. utils.js is excluded: it is
# the one place allowed to name the storage backend.
CONSUMERS = [
    WWW / "js" / "admin.js",
    WWW / "js" / "pack-submit.js",
    WWW / "js" / "player-core.js",
    WWW / "js" / "player.bundle.js",
    WWW / "analytics.html",
]


def test_token_key_named_only_in_utils() -> None:
    """No consumer may name the storage key — they go through the helpers.

    This is the property that keeps the fix from rotting: a future call site
    that reaches for ``sessionStorage`` directly re-creates the lockout for
    that one page, and nothing else in the suite would notice.
    """
    offenders = [p.name for p in CONSUMERS if TOKEN_KEY in p.read_text(encoding="utf-8")]
    assert not offenders, (
        f"{offenders} name the admin-token storage key directly. Read and write "
        "it through QuizifyUtils.readAdminToken()/writeAdminToken() so the "
        "storage choice stays in one place (utils.js)."
    )


def test_consumers_use_the_helper() -> None:
    """Each consumer actually calls the accessor (it didn't just drop the read)."""
    for path in CONSUMERS:
        text = path.read_text(encoding="utf-8")
        assert "readAdminToken()" in text, f"{path.name} never reads the admin token"


def test_utils_persists_to_local_storage() -> None:
    """The token is written to localStorage — the store that outlives the tab."""
    utils = UTILS_JS.read_text(encoding="utf-8")
    assert "localStorage.setItem(ADMIN_TOKEN_KEY" in utils
    assert "localStorage.getItem(ADMIN_TOKEN_KEY)" in utils


def test_utils_migrates_the_legacy_per_tab_token() -> None:
    """A tab still holding the old sessionStorage token keeps working.

    Without this, upgrading mid-session would drop a live admin's credential
    and hand the crown to whoever connected next.
    """
    utils = UTILS_JS.read_text(encoding="utf-8")
    assert "sessionStorage.getItem(ADMIN_TOKEN_KEY)" in utils, (
        "the legacy per-tab token must still be read once, so an already-open "
        "admin tab survives the upgrade"
    )
    read_fn = utils.split("function readAdminToken()", 1)[1].split("\n    }", 1)[0]
    assert "writeAdminToken(legacy)" in read_fn, (
        "a legacy token must be promoted to the durable store on first read"
    )


def test_storage_access_is_guarded() -> None:
    """Storage can throw (Safari private mode, disabled cookies).

    The helpers must degrade to "no token" rather than take the admin page
    down with them — a missing token only costs the bootstrap path, an
    exception costs the whole panel.
    """
    utils = UTILS_JS.read_text(encoding="utf-8")
    for fn in ("readAdminToken", "writeAdminToken"):
        body = utils.split(f"function {fn}(", 1)[1].split("\n    }", 1)[0]
        assert "try {" in body and "catch" in body, f"{fn} must guard storage access"
