"""Guard the client-side question-image URL check (#540).

#536/#538 taught the *server* sanitizer (``_sanitize_image_url``) that a pack
may point at an image shipped inside the integration, i.e. a path under
``/quizify/static/``. The *clients* kept their own defence-in-depth copy of an
``^https?://``-only test, so every question in the picture packs from #537
arrived with a URL the browser then threw away: the dashboard and the player
both rendered the question text with no image, and nothing in the suite
noticed because the server-side tests were all green.

Two properties keep that from happening again:

1. The rule exists once, in ``QuizifyUtils.safeImageUrl``, and behaves like the
   Python sanitizer — asserted by running the real shipped JS under node
   against the same cases as ``test_local_image_urls_536`` style inputs.
2. No view re-implements it. A future call site that inlines its own regex
   would drift away from the server the same way this one did.

The node half follows ``test_worker_contract_256.py``: skip locally when node
is absent, hard-fail in CI where ``QUIZIFY_REQUIRE_NODE=1`` is set.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.game.questions import _sanitize_image_url

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
UTILS_JS = WWW / "js" / "utils.js"

# Every place that turns an ``image_url`` from the wire into an ``img.src``.
# utils.js is excluded — it is the one file allowed to spell out the rule.
RENDER_SITES = [
    WWW / "dashboard.html",
    WWW / "js" / "player-game.js",
    WWW / "js" / "player-lightning.js",
    WWW / "js" / "player.bundle.js",
]

# (url, expected) — expected "" means "render text-only". Kept deliberately in
# lock-step with the server cases so the two can be diffed by eye.
CASES: list[tuple[object, str]] = [
    ("https://example.com/a.jpg", "https://example.com/a.jpg"),
    ("http://example.com/a.jpg", "http://example.com/a.jpg"),
    # The form the picture packs actually ship (#537) — the regression.
    (
        "/quizify/static/img/packs/picture-round/great-wave.webp",
        "/quizify/static/img/packs/picture-round/great-wave.webp",
    ),
    # Traversal out of the static mount stays refused, in both spellings.
    ("/quizify/static/../../secrets.yaml", ""),
    ("/quizify/static/%2e%2e/%2e%2e/secrets.yaml", ""),
    # Other local paths are not a general "relative paths are fine" licence.
    ("/local/somefile.png", ""),
    ("img/packs/x.webp", ""),
    # Schemes that must never reach img.src.
    ("javascript:alert(1)", ""),
    ("data:image/png;base64,AAAA", ""),
    # Absent / malformed.
    ("", ""),
    ("   ", ""),
]

_NODE_HARNESS = """
const fs = require('fs');
global.window = {};
// argv[0] is node, argv[1] this harness — the utils path is argv[2].
eval(fs.readFileSync(process.argv[2], 'utf8'));
const cases = JSON.parse(process.argv[3]);
const out = cases.map((u) => global.window.QuizifyUtils.safeImageUrl(u));
process.stdout.write(JSON.stringify(out));
"""


def _require_node() -> None:
    """Skip locally when node is missing; fail in CI, which pins node 20."""
    if shutil.which("node") is not None:
        return
    msg = "node not available — the safeImageUrl behaviour check cannot run"
    if os.environ.get("QUIZIFY_REQUIRE_NODE") == "1":
        pytest.fail(msg)
    pytest.skip(msg)


def test_safe_image_url_behaviour_matches_server(tmp_path: Path) -> None:
    """The shipped helper accepts and rejects exactly what the server does."""
    _require_node()
    harness = tmp_path / "harness.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    urls = [u for u, _ in CASES]
    result = subprocess.run(
        ["node", str(harness), str(UTILS_JS), json.dumps(urls)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    got = json.loads(result.stdout)
    mismatches = [
        (url, expected, actual)
        for (url, expected), actual in zip(CASES, got, strict=True)
        if actual != expected
    ]
    assert not mismatches, f"safeImageUrl disagrees with the spec: {mismatches}"


def test_client_agrees_with_python_sanitizer(tmp_path: Path) -> None:
    """Client and server verdicts agree on every case.

    Asserted as "same verdict", not "same string": what matters is that an
    image the server hands out is one the browser will actually load, and that
    neither side alone decides an image is fine.
    """
    _require_node()
    harness = tmp_path / "harness.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    urls = [u for u, _ in CASES]
    result = subprocess.run(
        ["node", str(harness), str(UTILS_JS), json.dumps(urls)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    client = json.loads(result.stdout)
    server = [_sanitize_image_url(u, "test-q") for u in urls]
    disagreements = [
        (url, srv, cli)
        for url, srv, cli in zip(urls, server, client, strict=True)
        if bool(srv) != bool(cli)
    ]
    assert not disagreements, (
        "client and server disagree on whether these images may render "
        f"(url, server, client): {disagreements}"
    )


def test_render_sites_use_the_helper() -> None:
    """Every render site routes through the shared helper."""
    missing = [
        p.name for p in RENDER_SITES if "QuizifyUtils.safeImageUrl" not in p.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"{missing} do not call QuizifyUtils.safeImageUrl(). Route image URLs "
        "through it so the accepted forms stay in one place (utils.js)."
    )


def test_no_render_site_reimplements_the_scheme_test() -> None:
    """No view carries its own http(s)-only regex.

    This is the exact shape of the #540 bug: three copies of
    ``/^https?:\\/\\//i`` that the server-side change in #536 could not reach.
    """
    needle = "^https?:"
    offenders = [p.name for p in RENDER_SITES if needle in p.read_text(encoding="utf-8")]
    assert not offenders, (
        f"{offenders} test the URL scheme inline. A local copy drifts away "
        "from the server sanitizer the moment the allowed forms change "
        "(#540) — call QuizifyUtils.safeImageUrl() instead."
    )


def test_picture_packs_ship_urls_the_client_accepts() -> None:
    """End-to-end on the real content: every shipped image URL survives both.

    The packs from #537 are the reason this bug was visible at all, so assert
    against them directly rather than against a hand-written sample.
    """
    _require_node()
    questions_dir = REPO / "custom_components" / "quizify" / "questions"
    urls: list[str] = []
    for pack in ("bilderraetsel-de.json", "picture-round-en.json"):
        data = json.loads((questions_dir / pack).read_text(encoding="utf-8"))
        questions = data["questions"] if isinstance(data, dict) else data
        urls.extend(q["image_url"] for q in questions if q.get("image_url"))
    assert urls, "expected the picture packs to ship image_url values"

    harness = REPO / "tests" / "_tmp_harness.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    try:
        result = subprocess.run(
            ["node", str(harness), str(UTILS_JS), json.dumps(urls)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        harness.unlink(missing_ok=True)
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    dropped = [u for u, safe in zip(urls, json.loads(result.stdout), strict=True) if not safe]
    assert not dropped, (
        f"{len(dropped)} shipped picture-pack images would render text-only "
        f"in the browser, e.g. {dropped[:3]}"
    )
