"""#1204 — the design-system follow-up after #1178.

1. **Television type scale.** The TV keeps the 16 px root and gets steps of its
   own (``--tv-fs-1`` … ``--tv-fs-12`` in ``design/tokens.json``). The rule the
   issue set: at 1280 × 720 nothing is smaller than before, at 1920 × 1080
   every size is within 1 px of before. The table below is the before, measured
   from the ``clamp()`` each rule carried on ``origin/main`` (13b9aff0).
2. **One rank row.** The host's list and the television's board use the card
   row the phone already had (``.qz-rank-row``), declared once.
3. **Button variants.** Families that restated a look take ``.btn-primary``,
   ``-secondary``, ``-ghost``, ``-danger`` or ``-icon-only`` instead.
4. **Confirm sheets.** The red confirm button is ink on ``--color-error-fill``.
5. **Toast under modals.** Kept, by the z-index table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import dashboard_css

WWW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www"
SRC = WWW / "css" / "src"


def _px(expr: str, width: float, height: float) -> float:
    expr = expr.strip()
    if expr.startswith("clamp("):
        low, mid, high = (part.strip() for part in expr[6:-1].split(","))
        return max(_px(low, width, height), min(_px(mid, width, height), _px(high, width, height)))
    number, unit = re.fullmatch(r"([\d.]+)(px|rem|vw|vh)", expr).groups()
    return float(number) * {"px": 1, "rem": 16, "vw": width / 100, "vh": height / 100}[unit]


def _root(css: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", css[css.index(":root") :]))


def _font_size(selector: str) -> str:
    """The television's own size for ``selector``: its first top-level rule in
    10-tv.css that sets one (media-query rules are indented), token resolved."""
    tv = (SRC / "10-tv.css").read_text("utf-8")
    for match in re.finditer(r"\n" + re.escape(selector) + r" \{([^}]*)\}", tv):
        size = re.search(r"font-size:\s*([^;]+);", match.group(1))
        if size:
            break
    else:
        raise AssertionError(f"{selector} has no font-size in 10-tv.css")
    value = size.group(1).strip()
    tokens = _root((SRC / "00-tokens.css").read_text("utf-8"))
    token = re.fullmatch(r"var\((--[\w-]+)\)", value)
    return tokens[token.group(1)].strip() if token else value


# selector -> (before at 1280 wide, before at 1920 wide), from origin/main.
BEFORE = {
    ".dashboard-question": (53.76, 56.0),
    ".dashboard-answer": (22.0, 22.0),
    ".dashboard-answer .answer-label": (28.0, 28.0),
    ".dashboard-answer-percent": (20.48, 22.0),
    ".dashboard-answer-progress": (17.92, 18.4),
    ".dashboard-category": (15.36, 20.0),
    ".dashboard-round": (15.36, 20.0),
    ".dashboard-leaderboard-title": (16.0, 20.0),
    ".dashboard-leaderboard .leaderboard-row": (20.48, 24.0),
    ".dashboard-leaderboard .leaderboard-rank": (19.2, 22.0),
    ".dashboard-leaderboard .leaderboard-score": (21.76, 26.0),
    ".dashboard-leaderboard .leaderboard-streak": (11.0, 11.0),
    ".dashboard-funfact": (23.04, 26.0),
    ".dashboard-funfact .fun-fact-label": (16.0, 20.0),
    ".dashboard-join-url": (19.2, 21.6),
    ".dashboard-join-hint": (14.08, 16.0),
    ".dashboard-player-chip": (17.92, 20.0),
    ".dashboard-waiting-text": (20.48, 22.0),
    ".podium-title": (46.08, 48.0),
    ".podium-name": (23.04, 28.0),
    ".podium-score": (23.04, 26.0),
    ".award-winner": (20.48, 28.0),
    ".award-detail": (17.92, 22.0),
    ".dnl-lbl": (20.48, 24.0),
    ".dashboard-paused-title": (51.2, 56.0),
}


@pytest.mark.parametrize("selector", sorted(BEFORE))
def test_the_tv_is_not_smaller_at_1280_and_unchanged_at_1920(selector: str) -> None:
    before_720p, before_1080p = BEFORE[selector]
    value = _font_size(selector)
    now_720p, now_1080p = _px(value, 1280, 720), _px(value, 1920, 1080)
    assert now_720p >= before_720p - 0.01, (
        f"{selector} is {now_720p:.1f} px at 1280 px, was {before_720p} — the floor is today's size"
    )
    assert abs(now_1080p - before_1080p) <= 1.0, (
        f"{selector} is {now_1080p:.1f} px at 1920 px, was {before_1080p} — must match within 1 px"
    )


def test_the_tv_steps_are_tokens_from_the_one_source() -> None:
    tokens = (Path(__file__).resolve().parent.parent / "design" / "tokens.json").read_text("utf-8")
    sheet = _root(dashboard_css())
    for n in range(1, 13):
        assert f'"--tv-fs-{n}"' in tokens, f"--tv-fs-{n} is not in design/tokens.json"
        assert f"--tv-fs-{n}" in sheet, f"--tv-fs-{n} is not in tv.css"
    tv = (SRC / "10-tv.css").read_text("utf-8")
    assert tv.count("var(--tv-fs-") >= 50


def test_the_tv_keeps_the_16px_root() -> None:
    """A larger root would also grow every rem size the shared modules give the TV."""
    tv = re.sub(r"/\*.*?\*/", "", (SRC / "10-tv.css").read_text("utf-8"), flags=re.S)
    assert not re.search(r"(^|\})\s*html\s*\{[^}]*font-size", tv)


def test_host_and_tv_rows_are_the_shared_rank_row() -> None:
    finale = (SRC / "05-finale.css").read_text("utf-8")
    shared = finale[finale.index(".qz-rank-row,\n.leaderboard-row {") :]
    shared = shared[: shared.index("}")]
    for prop in ("background: var(--color-bg-surface)", "border: 1px solid var(--color-border-light)",
                 "border-radius: var(--radius-lg)"):
        assert prop in shared
    tv = (SRC / "10-tv.css").read_text("utf-8")
    row = tv[tv.index(".dashboard-leaderboard .leaderboard-row {") :]
    row = re.sub(r"/\*.*?\*/", "", row[: row.index("}")], flags=re.S)
    for prop in ("background", "border-radius", "border"):
        assert not re.search(r"(^|[\s;])" + prop + r"\s*:", row), f"the TV row restates {prop} again"
    assert ".qz-rank-row {" not in (SRC / "07-player.css").read_text("utf-8")


# id or class in the markup -> the variant it carries now
VARIANTS = [
    ("player.html", 'id="play-again-same-btn"', "btn-primary"),
    ("player.html", 'id="end-share-btn"', "btn-primary"),
    ("player.html", 'id="lightning-recap-continue-btn"', "btn-primary"),
    ("player.html", 'id="new-game-btn"', "btn-secondary"),
    ("player.html", 'id="next-round-btn"', "btn-primary"),
    ("player.html", 'id="end-game-from-reveal-btn"', "btn-ghost"),
    ("player.html", 'id="team-leave-btn"', "btn-ghost"),
    ("player.html", 'id="invite-copy-btn"', "btn-secondary"),
    ("admin.html", 'id="reset-game-btn"', "btn-ghost"),
    ("admin.html", 'id="pwa-install-btn"', "btn-ghost"),
    ("admin.html", 'id="lobby-back-btn"', "btn-secondary"),
    ("admin.html", 'id="admin-star-hint-dismiss"', "btn-icon-only"),
    ("admin.html", 'id="pwa-ios-hint-close"', "btn-icon-only"),
]


@pytest.mark.parametrize("page,anchor,variant", VARIANTS)
def test_button_families_carry_a_variant(page: str, anchor: str, variant: str) -> None:
    html = (WWW / page).read_text("utf-8")
    tag = re.search(r"<button[^>]*" + re.escape(anchor) + r"[^>]*>", html).group(0)
    assert variant in re.search(r'class="([^"]*)"', tag).group(1).split()


def test_the_scoring_buttons_are_secondary() -> None:
    js = (WWW / "js" / "render-shared.js").read_text("utf-8")
    assert js.count('class="scoring-btn btn-secondary"') == 2


FAMILY_RULES = {
    "07-player.css": [".resbtn {", ".resbtn--primary {", ".resbtn--secondary {", ".lobby-back-btn {",
                      ".invite-copy-btn {"],
    "08-responsive.css": [".pl-result-bar-btn {"],
    "05-finale.css": [".scoring-btn {"],
    "04-lobby.css": [".qz-team-leaveBtn {"],
    "01-base.css": [".admin-header-btn {", ".pwa-ios-hint-close {"],
    "06-admin.css": [".admin-star-hint-dismiss {"],
}


@pytest.mark.parametrize("module,selector", [(m, s) for m, sels in FAMILY_RULES.items() for s in sels])
def test_families_no_longer_restate_the_look(module: str, selector: str) -> None:
    css = (SRC / module).read_text("utf-8")
    body = css[css.index("\n" + selector) :]
    body = re.sub(r"/\*.*?\*/", "", body[: body.index("}")], flags=re.S)
    for prop in ("background", "color", "border", "font", "font-family"):
        assert not re.search(r"(^|[\s;])" + prop + r"\s*:", body), f"{selector} restates {prop}"


def test_the_variants_live_once_in_the_shared_module() -> None:
    shared = (SRC / "02-shared.css").read_text("utf-8")
    for variant in (".btn-primary {", ".btn-secondary {", ".btn-ghost {", ".btn-danger {", ".btn-icon-only {"):
        assert shared.count(variant) == 1, variant
    for module in SRC.glob("*.css"):
        if module.name != "02-shared.css":
            assert "\n.btn-ghost {" not in module.read_text("utf-8"), module.name


def test_confirm_sheets_put_ink_on_the_error_fill() -> None:
    shared = (SRC / "02-shared.css").read_text("utf-8")
    danger = shared[shared.index(".btn-danger {") :]
    danger = danger[: danger.index("}")]
    assert "background: var(--color-error-fill)" in danger
    assert "color: var(--on-accent)" in danger
    # No later rule gives a confirm button another colour.
    for module in SRC.glob("*.css"):
        text = module.read_text("utf-8")
        for match in re.finditer(r"([^{}]*\.btn-danger[^{}]*)\{([^}]*)\}", text):
            if module.name == "02-shared.css":
                continue
            assert "color" not in match.group(2), f"{module.name}: {match.group(1).strip()}"
    admin = (WWW / "admin.html").read_text("utf-8")
    for confirm in ("end-game-confirm-btn", "reset-game-confirm-btn", "kick-player-confirm-btn"):
        tag = re.search(r'<button[^>]*id="' + confirm + r'"[^>]*>', admin).group(0)
        assert "btn-danger" in tag


def test_the_toast_stays_under_modals_by_the_z_table() -> None:
    tokens = _root((SRC / "00-tokens.css").read_text("utf-8"))
    assert int(tokens["--z-toast"]) < int(tokens["--z-modal"]) < int(tokens["--z-overlay"])
    shared = (SRC / "02-shared.css").read_text("utf-8")
    toast = shared[shared.index(".qz-toast {") :]
    assert "z-index: var(--z-toast)" in toast[: toast.index("}")]
